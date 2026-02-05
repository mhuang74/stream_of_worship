"""YouTube audio download service using yt-dlp.

Provides search-based audio downloading from YouTube using song metadata
(title, composer, album) assembled into a search query.
"""

import tempfile
from pathlib import Path
from typing import Optional

import yt_dlp


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
    ) -> str:
        """Build a YouTube search query from song metadata.

        Args:
            title: Song title (required)
            composer: Composer / artist name
            album: Album name

        Returns:
            Space-joined search query string
        """
        parts = [title]
        if composer:
            parts.append(composer)
        if album:
            parts.append(album)
        return " ".join(parts)

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
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(f"ytsearch1:{query}", download=True)
                if info is None:
                    raise RuntimeError(f"No results found for query: {query}")

                filename = ydl.prepare_filename(info)
                # FFmpeg post-processor rewrites the extension to .mp3
                mp3_path = Path(filename).with_suffix(".mp3")
                if mp3_path.exists():
                    return mp3_path
                if Path(filename).exists():
                    return Path(filename)
                raise RuntimeError(f"Downloaded file not found: {filename}")
        except yt_dlp.utils.DownloadError as e:
            raise RuntimeError(f"Download failed: {e}") from e
