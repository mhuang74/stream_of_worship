"""YouTube audio download service using yt-dlp.

Provides search-based audio downloading from YouTube using song metadata
(title, composer, album) assembled into a search query.
"""

import os
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


def _youtube_proxy_opts() -> dict[str, Any]:
    """Read SOW_YOUTUBE_PROXY / SOW_YOUTUBE_PROXY_RETRIES env vars.

    Returns ydl_opts keys: ``proxy`` (only if set, non-empty) and optionally
    ``retries``. Returns an empty dict if neither is set, letting yt-dlp
    fall back to its own defaults (or system ``HTTPS_PROXY`` env var).
    """
    opts: dict[str, Any] = {}
    proxy = os.environ.get("SOW_YOUTUBE_PROXY", "").strip()
    if proxy:
        opts["proxy"] = proxy
    retries_env = os.environ.get("SOW_YOUTUBE_PROXY_RETRIES", "").strip()
    if retries_env.isdigit():
        opts["retries"] = int(retries_env)
    return opts


def _extract_chinese_title_from_youtube(video_title: Optional[str]) -> Optional[str]:
    """Extract the Chinese title from YouTube video title format.

    YouTube MV titles are typically formatted as:
    "【一生敬拜祢 All the Days of My Life】官方歌詞版MV ..."

    This function extracts the Chinese portion from the first bracketed segment.

    Args:
        video_title: YouTube video title

    Returns:
        Chinese title or None if not found
    """
    if not video_title:
        return None

    match = re.match(r"【([^】\s]+)", video_title)
    if match:
        return match.group(1)

    return None


def _select_best_candidate(
    entries: list[dict[str, Any]],
    song_title: str,
) -> Optional[dict[str, Any]]:
    """Select the first entry whose Chinese title exactly matches ``song_title``.

    Iterates through YouTube search result entries in relevance order and
    returns the first whose extracted Chinese title (from the ``【...```
    bracket) equals ``song_title`` exactly.

    Args:
        entries: List of yt-dlp entry dicts (may contain ``None`` items).
        song_title: Expected song title to match against.

    Returns:
        The matched entry dict, or ``None`` if no candidate matches.
    """
    for entry in entries:
        if entry is None:
            continue
        video_title = entry.get("title")
        chinese_title = _extract_chinese_title_from_youtube(video_title)
        if chinese_title is not None and chinese_title == song_title:
            return entry
    return None


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
        "no_warnings": True,
        "remote_components": ["ejs:github"],
        **_youtube_proxy_opts(),
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

    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=requested_languages)
        source = f"YouTube transcript ({','.join(requested_languages)})"
    except Exception:
        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
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
        line = _cleanup_transcript_line(snippet.get("text", ""))
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

    def preview_video(
        self,
        query: str,
        max_results: int = 1,
        song_title: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """Preview a YouTube video without downloading.

        Uses yt-dlp to extract metadata for the top search result.

        Args:
            query: YouTube search query or direct URL
            max_results: Maximum number of search results to scan. When
                greater than 1 and ``song_title`` is provided, the top N
                results are scanned for a title match. Defaults to 1 for
                backward compatibility.
            song_title: Expected song title. When provided alongside
                ``max_results > 1``, candidates are filtered by exact title
                match and the first match is returned.

        Returns:
            Dict with video info (id, title, duration, webpage_url) or None if not found

        Raises:
            RuntimeError: If extraction fails
        """
        ydl_opts = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "remote_components": ["ejs:github"],
            **_youtube_proxy_opts(),
        }

        is_url = query.startswith(("http://", "https://", "www.", "youtube.com", "youtu.be"))
        use_multi_candidate = (
            song_title is not None and max_results > 1 and not is_url
        )

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                if is_url:
                    info = ydl.extract_info(query, download=False)
                elif use_multi_candidate:
                    info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
                else:
                    info = ydl.extract_info(f"ytsearch1:{query}", download=False)

                if info is None:
                    return None

                if use_multi_candidate:
                    entries = info.get("entries") or []
                    matched = _select_best_candidate(entries, song_title)
                    if matched is None:
                        return None
                    return {
                        "id": matched.get("id"),
                        "title": matched.get("title"),
                        "duration": matched.get("duration"),
                        "webpage_url": matched.get("webpage_url"),
                    }

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
            "no_warnings": True,
            "remote_components": ["ejs:github"],
            **_youtube_proxy_opts(),
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

    def download(
        self,
        query: str,
        max_results: int = 1,
        song_title: Optional[str] = None,
    ) -> Path:
        """Download audio from YouTube by search query.

        Searches YouTube for *query*, downloads the top result, and
        post-processes it to MP3 at 192 kbps.

        When ``song_title`` is provided alongside ``max_results > 1``, a
        two-phase approach is used: first metadata for the top N results is
        extracted (without downloading), the first candidate whose Chinese
        title matches ``song_title`` is selected, and that video is
        downloaded by its URL. If no candidate matches, a ``RuntimeError`` is
        raised.

        Args:
            query: YouTube search query string
            max_results: Maximum number of search results to scan when
                ``song_title`` is provided. Defaults to 1.
            song_title: Expected song title for candidate matching.

        Returns:
            Path to the downloaded audio file

        Raises:
            RuntimeError: If no results are found, no candidate matches, or
                the download fails
        """
        if song_title is not None and max_results > 1:
            return self._download_with_match(query, max_results, song_title)

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
            "no_warnings": True,
            "remote_components": ["ejs:github"],
            **_youtube_proxy_opts(),
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

    def download_with_info(
        self,
        query: str,
        max_results: int = 1,
        song_title: Optional[str] = None,
    ) -> tuple[Path, Optional[str], Optional[str]]:
        """Download audio from YouTube by search query and return path + webpage_url + video_title.

        Same as download() but also returns the YouTube URL and video title for the downloaded video.

        When ``song_title`` is provided alongside ``max_results > 1``, a
        two-phase approach is used: first metadata for the top N results is
        extracted (without downloading), the first candidate whose Chinese
        title matches ``song_title`` is selected, and that video is
        downloaded by its URL. If no candidate matches, a ``RuntimeError`` is
        raised.

        Args:
            query: YouTube search query string
            max_results: Maximum number of search results to scan when
                ``song_title`` is provided. Defaults to 1.
            song_title: Expected song title for candidate matching.

        Returns:
            Tuple of (Path to downloaded audio file, YouTube webpage_url or None, video title or None)

        Raises:
            RuntimeError: If no results are found, no candidate matches, or
                the download fails
        """
        if song_title is not None and max_results > 1:
            return self._download_with_match_info(query, max_results, song_title)

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
            "no_warnings": True,
            "remote_components": ["ejs:github"],
            **_youtube_proxy_opts(),
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

    def _scan_for_match(
        self,
        query: str,
        max_results: int,
        song_title: str,
    ) -> dict[str, Any]:
        """Extract metadata for the top N search results and pick the best match.

        Phase 1 of the two-phase download: fetches candidate metadata
        without downloading audio, then selects the first entry whose
        Chinese title exactly matches ``song_title``.

        Args:
            query: YouTube search query string
            max_results: Maximum number of results to scan
            song_title: Expected song title

        Returns:
            The matched entry dict.

        Raises:
            RuntimeError: If extraction fails or no candidate matches.
        """
        ydl_opts = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "remote_components": ["ejs:github"],
            **_youtube_proxy_opts(),
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(
                    f"ytsearch{max_results}:{query}", download=False
                )
        except yt_dlp.utils.DownloadError as e:
            raise RuntimeError(f"Failed to scan search results: {e}") from e

        if info is None:
            raise RuntimeError(
                f"No matching title found in top {max_results} results for query: {query}"
            )

        entries = info.get("entries") or []
        matched = _select_best_candidate(entries, song_title)
        if matched is None:
            raise RuntimeError(
                f"No matching title found in top {max_results} results for query: {query}"
            )
        return matched

    def _download_with_match(
        self,
        query: str,
        max_results: int,
        song_title: str,
    ) -> Path:
        """Two-phase download: scan candidates, then download the matched URL.

        Args:
            query: YouTube search query string
            max_results: Maximum number of results to scan
            song_title: Expected song title

        Returns:
            Path to the downloaded audio file

        Raises:
            RuntimeError: If no candidate matches or the download fails
        """
        matched = self._scan_for_match(query, max_results, song_title)
        url = matched.get("webpage_url")
        if not url:
            raise RuntimeError(
                f"Matched candidate has no webpage_url for query: {query}"
            )
        return self.download_by_url(url)

    def _download_with_match_info(
        self,
        query: str,
        max_results: int,
        song_title: str,
    ) -> tuple[Path, Optional[str], Optional[str]]:
        """Two-phase download with info: scan, then download the matched URL.

        Args:
            query: YouTube search query string
            max_results: Maximum number of results to scan
            song_title: Expected song title

        Returns:
            Tuple of (Path, webpage_url, video_title) from the matched candidate

        Raises:
            RuntimeError: If no candidate matches or the download fails
        """
        matched = self._scan_for_match(query, max_results, song_title)
        url = matched.get("webpage_url")
        if not url:
            raise RuntimeError(
                f"Matched candidate has no webpage_url for query: {query}"
            )
        path = self.download_by_url(url)
        return path, url, matched.get("title")
