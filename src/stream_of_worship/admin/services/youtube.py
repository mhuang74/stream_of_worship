"""YouTube audio download service using yt-dlp.

Provides search-based audio downloading from YouTube using song metadata
(title, composer, album) assembled into a search query.
"""

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yt_dlp

# Constants for search and duration filtering
OFFICIAL_LYRICS_SUFFIX = "官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美"
DURATION_WARNING_THRESHOLD = 420  # 7 minutes in seconds
ZH_LANG_CODES = ["zh-Hant", "zh-TW", "zh-Hans", "zh-CN", "zh-HK", "zh"]
EN_LANG_CODES = ["en-US", "en"]
DEFAULT_TRANSCRIPT_LANGUAGES = ZH_LANG_CODES + EN_LANG_CODES


@dataclass
class YouTubeVideoMetadata:
    """Admin-safe subset of `yt-dlp` video metadata."""

    video_id: str
    title: str
    webpage_url: str
    duration: int | None
    channel: str | None
    uploader: str | None
    creator: str | None
    upload_date: str | None
    description: str | None
    thumbnail: str | None
    raw: dict[str, Any]


@dataclass
class TranscriptDraft:
    """Draft transcript lines and their provenance."""

    source: str
    lines: list[str]


def _youtube_extractor_args() -> dict[str, Any]:
    return {
        "youtube": {
            "remote_components": "ejs:github",
        }
    }


def extract_video_id(url: str) -> str | None:
    """Extract a YouTube video ID from a supported URL."""
    if "youtu.be/" in url:
        match = re.search(r"youtu\.be/([^/?]+)", url)
        if match:
            return match.group(1)

    if "youtube.com/watch" in url:
        match = re.search(r"[?&]v=([^&]+)", url)
        if match:
            return match.group(1)

    return None


def extract_video_metadata(url: str) -> YouTubeVideoMetadata:
    """Extract video metadata for a direct YouTube URL."""
    ydl_opts = {
        "noplaylist": True,
        "quiet": True,
        "extractor_args": _youtube_extractor_args(),
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as e:
        raise RuntimeError(f"Failed to extract video metadata: {e}") from e

    if not info:
        raise RuntimeError("Failed to extract video metadata: no video information returned")

    video_id = info.get("id") or extract_video_id(url)
    if not video_id:
        raise RuntimeError(f"Failed to extract video metadata: could not determine video ID for {url}")

    canonical_url = info.get("webpage_url") or url
    return YouTubeVideoMetadata(
        video_id=video_id,
        title=info.get("title") or "",
        webpage_url=canonical_url,
        duration=info.get("duration"),
        channel=info.get("channel"),
        uploader=info.get("uploader"),
        creator=info.get("creator"),
        upload_date=info.get("upload_date"),
        description=info.get("description"),
        thumbnail=info.get("thumbnail"),
        raw=info,
    )


def derive_song_defaults(metadata: YouTubeVideoMetadata) -> dict[str, str | None]:
    """Heuristically derive editable song defaults from video metadata."""
    title_text = metadata.title.strip()
    title_part = title_text
    album_name = None

    for separator in (" | ", " ｜ ", " — ", " – "):
        if separator in title_part:
            left, right = title_part.split(separator, 1)
            title_part = left.strip()
            if album_name is None and right.strip():
                album_name = right.strip()
            break

    song_title = title_part
    composer = None
    if " - " in title_part:
        left, right = title_part.split(" - ", 1)
        if left.strip() and right.strip():
            song_title = left.strip()
            composer = right.strip()

    return {
        "title": song_title or metadata.title,
        "composer": composer,
        "lyricist": None,
        "album_name": album_name,
        "album_series": None,
        "musical_key": None,
        "source_url": metadata.webpage_url,
        "lyrics_raw": None,
    }


def _find_best_transcript(transcript_list: Any) -> Any | None:
    best_zh_manual = None
    best_zh_generated = None
    best_en_manual = None
    best_en_generated = None

    for transcript in transcript_list:
        code = transcript.language_code
        is_generated = transcript.is_generated

        if code in ZH_LANG_CODES or code.startswith("zh"):
            if not is_generated and best_zh_manual is None:
                best_zh_manual = transcript
            elif is_generated and best_zh_generated is None:
                best_zh_generated = transcript
        elif code in EN_LANG_CODES or code.startswith("en"):
            if not is_generated and best_en_manual is None:
                best_en_manual = transcript
            elif is_generated and best_en_generated is None:
                best_en_generated = transcript

    return best_zh_manual or best_zh_generated or best_en_manual or best_en_generated


def _cleanup_transcript_line(text: str) -> str | None:
    text = re.sub(r"\s+", " ", text.strip())
    if not text:
        return None
    if re.fullmatch(r"\[[^\]]+\]", text):
        return None
    return text


def _fetch_transcript_draft(
    url: str,
    languages: list[str] | None = None,
) -> TranscriptDraft:
    video_id = extract_video_id(url)
    if not video_id:
        raise RuntimeError(f"Could not extract video ID from URL: {url}")

    requested_languages = languages or DEFAULT_TRANSCRIPT_LANGUAGES

    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError as e:
        raise RuntimeError("youtube-transcript-api is not installed") from e

    api = YouTubeTranscriptApi()

    try:
        transcript = api.fetch(video_id, languages=requested_languages)
        source = f"YouTube transcript ({','.join(requested_languages)})"
    except Exception:
        try:
            transcript_list = api.list(video_id)
            best = _find_best_transcript(transcript_list)
            if best is None:
                raise RuntimeError("No suitable transcript available")
            transcript = best.fetch()
            source_kind = "auto-generated" if best.is_generated else "manual"
            source = f"YouTube {best.language} ({source_kind})"
        except Exception as e:
            raise RuntimeError(f"Failed to fetch YouTube transcript: {e}") from e

    lines = []
    for snippet in transcript:
        line = _cleanup_transcript_line(getattr(snippet, "text", ""))
        if line:
            lines.append(line)

    return TranscriptDraft(source=source, lines=lines)


def fetch_transcript_lines(url: str, languages: list[str] | None = None) -> list[str]:
    """Fetch cleaned transcript lines for a YouTube video."""
    return _fetch_transcript_draft(url, languages=languages).lines


class YouTubeDownloader:
    """Downloads audio from YouTube using yt-dlp.

    Attributes:
        output_dir: Directory where downloaded files are saved
    """

    def __init__(self, output_dir: Optional[Path] = None):
        """Initialize the downloader.

        Args:
            output_dir: Directory for downloads.  Defaults to a fresh
                system temp directory.
        """
        self.output_dir = output_dir or Path(tempfile.mkdtemp())
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def build_search_query(
        self,
        title: str,
        composer: Optional[str] = None,
        album: Optional[str] = None,
        suffix: str = "",
    ) -> str:
        """Build a YouTube search query from song metadata.

        Args:
            title: Song title (required)
            composer: Composer / artist name
            album: Album name
            suffix: Optional suffix to append (e.g., for official lyrics videos)

        Returns:
            Space-joined search query string
        """
        parts = [title]
        if composer:
            parts.append(composer)
        if album:
            parts.append(album)
        if suffix:
            parts.append(suffix)
        return " ".join(parts)

    def preview_video(self, query: str) -> Optional[dict[str, Any]]:
        """Preview a YouTube video without downloading.

        Uses yt-dlp to extract metadata for the top search result.

        Args:
            query: YouTube search query or direct URL

        Returns:
            Dict with video info (id, title, duration, webpage_url) or None if not found

        Raises:
            RuntimeError: If extraction fails
        """
        ydl_opts = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "extractor_args": _youtube_extractor_args(),
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Determine if this is a direct URL or search query
                if query.startswith(("http://", "https://", "www.", "youtube.com", "youtu.be")):
                    info = ydl.extract_info(query, download=False)
                else:
                    info = ydl.extract_info(f"ytsearch1:{query}", download=False)

                if info is None:
                    return None

                # Handle ytsearch response structure (results in entries[0])
                if "entries" in info and info["entries"]:
                    video_info = info["entries"][0]
                else:
                    video_info = info

                return {
                    "id": video_info.get("id"),
                    "title": video_info.get("title"),
                    "duration": video_info.get("duration"),
                    "webpage_url": video_info.get("webpage_url"),
                }
        except yt_dlp.utils.DownloadError as e:
            raise RuntimeError(f"Failed to preview video: {e}") from e

    def download_by_url(self, url: str) -> Path:
        """Download audio from YouTube by direct URL.

        Args:
            url: Direct YouTube URL

        Returns:
            Path to the downloaded audio file

        Raises:
            RuntimeError: If download fails
        """
        ydl_opts = {
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
            "outtmpl": str(self.output_dir / "%(title)s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "extractor_args": _youtube_extractor_args(),
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

                # After download, find the actual output file
                mp3_files = list(self.output_dir.glob("*.mp3"))
                if mp3_files:
                    return mp3_files[0]

                # Fallback: check for any audio file
                audio_exts = [".mp3", ".m4a", ".webm", ".opus", ".ogg"]
                for ext in audio_exts:
                    files = list(self.output_dir.glob(f"*{ext}"))
                    if files:
                        return files[0]

                # Debug: list all files in output directory
                existing_files = list(self.output_dir.iterdir())
                raise RuntimeError(
                    f"Downloaded file not found. "
                    f"Files in directory: {[f.name for f in existing_files]}"
                )
        except yt_dlp.utils.DownloadError as e:
            raise RuntimeError(f"Download failed: {e}") from e

    def download(self, query: str) -> Path:
        """Download audio from YouTube by search query.

        Searches YouTube for *query*, downloads the top result, and
        post-processes it to MP3 at 192 kbps.

        Args:
            query: YouTube search query string

        Returns:
            Path to the downloaded audio file

        Raises:
            RuntimeError: If no results are found or the download fails
        """
        ydl_opts = {
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
            "outtmpl": str(self.output_dir / "%(title)s.%(ext)s"),
            "noplaylist": True,
            "default_search": "ytsearch1",
            "quiet": True,
            "extractor_args": _youtube_extractor_args(),
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"ytsearch1:{query}", download=True)
                if info is None:
                    raise RuntimeError(f"No results found for query: {query}")

                # After download, find the actual output file
                # prepare_filename() may not match actual filename when using ytsearch
                mp3_files = list(self.output_dir.glob("*.mp3"))
                if mp3_files:
                    return mp3_files[0]

                # Fallback: check for any audio file
                audio_exts = [".mp3", ".m4a", ".webm", ".opus", ".ogg"]
                for ext in audio_exts:
                    files = list(self.output_dir.glob(f"*{ext}"))
                    if files:
                        return files[0]

                # Debug: list all files in output directory
                existing_files = list(self.output_dir.iterdir())
                raise RuntimeError(
                    f"Downloaded file not found. "
                    f"Files in directory: {[f.name for f in existing_files]}"
                )
        except yt_dlp.utils.DownloadError as e:
            raise RuntimeError(f"Download failed: {e}") from e

    def download_with_info(self, query: str) -> tuple[Path, Optional[str], Optional[str]]:
        """Download audio from YouTube by search query and return path + webpage_url + video_title.

        Same as download() but also returns the YouTube URL and video title for the downloaded video.

        Args:
            query: YouTube search query string

        Returns:
            Tuple of (Path to downloaded audio file, YouTube webpage_url or None, video title or None)

        Raises:
            RuntimeError: If no results are found or the download fails
        """
        ydl_opts = {
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
            "outtmpl": str(self.output_dir / "%(title)s.%(ext)s"),
            "noplaylist": True,
            "default_search": "ytsearch1",
            "quiet": True,
            "extractor_args": _youtube_extractor_args(),
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"ytsearch1:{query}", download=True)
                if info is None:
                    raise RuntimeError(f"No results found for query: {query}")

                webpage_url: Optional[str] = None
                video_title: Optional[str] = None
                if "entries" in info and info["entries"]:
                    video_info = info["entries"][0]
                    webpage_url = video_info.get("webpage_url")
                    video_title = video_info.get("title")
                else:
                    webpage_url = info.get("webpage_url")
                    video_title = info.get("title")

                mp3_files = list(self.output_dir.glob("*.mp3"))
                if mp3_files:
                    return mp3_files[0], webpage_url, video_title

                audio_exts = [".mp3", ".m4a", ".webm", ".opus", ".ogg"]
                for ext in audio_exts:
                    files = list(self.output_dir.glob(f"*{ext}"))
                    if files:
                        return files[0], webpage_url, video_title

                existing_files = list(self.output_dir.iterdir())
                raise RuntimeError(
                    f"Downloaded file not found. "
                    f"Files in directory: {[f.name for f in existing_files]}"
                )
        except yt_dlp.utils.DownloadError as e:
            raise RuntimeError(f"Download failed: {e}") from e
