"""Tests for YouTube download service."""

from unittest.mock import MagicMock, patch

import pytest
import yt_dlp

from stream_of_worship.admin.services.youtube import YouTubeDownloader


class TestYouTubeDownloaderInit:
    """Tests for YouTubeDownloader initialization."""

    def test_custom_output_dir(self, tmp_path):
        """Uses the provided output directory."""
        downloader = YouTubeDownloader(output_dir=tmp_path)
        assert downloader.output_dir == tmp_path

    def test_creates_output_dir(self, tmp_path):
        """Creates the output directory if it does not exist."""
        output_dir = tmp_path / "sub" / "downloads"
        downloader = YouTubeDownloader(output_dir=output_dir)
        assert downloader.output_dir == output_dir
        assert output_dir.exists()

    def test_default_output_dir_is_set(self):
        """When no output_dir is given, a temp directory is created."""
        downloader = YouTubeDownloader()
        assert downloader.output_dir.exists()


class TestBuildSearchQuery:
    """Tests for build_search_query."""

    @pytest.fixture
    def downloader(self, tmp_path):
        return YouTubeDownloader(output_dir=tmp_path)

    def test_title_only(self, downloader):
        assert downloader.build_search_query("Song Title") == "Song Title"

    def test_title_and_composer(self, downloader):
        result = downloader.build_search_query("Song Title", composer="Artist")
        assert result == "Song Title Artist"

    def test_title_and_album(self, downloader):
        result = downloader.build_search_query("Song Title", album="Album Name")
        assert result == "Song Title Album Name"

    def test_all_fields(self, downloader):
        result = downloader.build_search_query(
            "Song Title", composer="Artist", album="Album"
        )
        assert result == "Song Title Artist Album"

    def test_none_fields_are_skipped(self, downloader):
        result = downloader.build_search_query("Title", composer=None, album=None)
        assert result == "Title"

    def test_chinese_metadata(self, downloader):
        result = downloader.build_search_query(
            "將天敞開", composer="游智婷", album="敬拜讚美15"
        )
        assert result == "將天敞開 游智婷 敬拜讚美15"


class TestDownload:
    """Tests for the download method."""

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_download_returns_mp3_path(self, mock_ydl_class, tmp_path):
        """Returns the .mp3 path when FFmpeg post-processor creates it."""
        mp3_file = tmp_path / "Test Song.mp3"
        mp3_file.write_bytes(b"fake mp3 data")

        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {"title": "Test Song", "ext": "webm"}
        mock_ydl.prepare_filename.return_value = str(tmp_path / "Test Song.webm")
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        downloader = YouTubeDownloader(output_dir=tmp_path)
        result = downloader.download("Test Song query")

        assert result == mp3_file
        mock_ydl.extract_info.assert_called_once_with(
            "ytsearch1:Test Song query", download=True
        )

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_download_falls_back_to_original_extension(self, mock_ydl_class, tmp_path):
        """Falls back to the original filename when .mp3 does not exist."""
        original_file = tmp_path / "Test Song.webm"
        original_file.write_bytes(b"fake webm data")

        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {"title": "Test Song", "ext": "webm"}
        mock_ydl.prepare_filename.return_value = str(original_file)
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        downloader = YouTubeDownloader(output_dir=tmp_path)
        result = downloader.download("query")

        assert result == original_file

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_download_no_results(self, mock_ydl_class, tmp_path):
        """Raises RuntimeError when extract_info returns None."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = None
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        downloader = YouTubeDownloader(output_dir=tmp_path)
        with pytest.raises(RuntimeError, match="No results found"):
            downloader.download("nonexistent query")

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_download_error_wraps_as_runtime_error(self, mock_ydl_class, tmp_path):
        """yt_dlp DownloadError is wrapped as RuntimeError."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError("network error")
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        downloader = YouTubeDownloader(output_dir=tmp_path)
        with pytest.raises(RuntimeError, match="Download failed"):
            downloader.download("bad query")

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_download_file_not_found_after_extract(self, mock_ydl_class, tmp_path):
        """Raises RuntimeError when neither file exists after extraction."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {"title": "Missing", "ext": "webm"}
        mock_ydl.prepare_filename.return_value = str(tmp_path / "Missing.webm")
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        downloader = YouTubeDownloader(output_dir=tmp_path)
        with pytest.raises(RuntimeError, match="Downloaded file not found"):
            downloader.download("query")

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_download_passes_correct_ydl_options(self, mock_ydl_class, tmp_path):
        """YoutubeDL is constructed with the expected option keys."""
        mp3_file = tmp_path / "Song.mp3"
        mp3_file.write_bytes(b"data")

        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {"title": "Song", "ext": "webm"}
        mock_ydl.prepare_filename.return_value = str(tmp_path / "Song.webm")
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        downloader = YouTubeDownloader(output_dir=tmp_path)
        downloader.download("query")

        opts = mock_ydl_class.call_args[0][0]
        assert opts["format"] == "bestaudio/best"
        assert opts["noplaylist"] is True
        assert opts["quiet"] is True
        assert opts["postprocessors"][0]["key"] == "FFmpegExtractAudio"
        assert opts["postprocessors"][0]["preferredcodec"] == "mp3"


class TestBuildSearchQueryWithSuffix:
    """Tests for build_search_query with suffix parameter."""

    @pytest.fixture
    def downloader(self, tmp_path):
        return YouTubeDownloader(output_dir=tmp_path)

    def test_build_query_with_suffix(self, downloader):
        """Appends suffix to query when provided."""
        result = downloader.build_search_query(
            "Song Title", composer="Artist", suffix="Official Lyrics MV"
        )
        assert result == "Song Title Artist Official Lyrics MV"

    def test_build_query_without_suffix(self, downloader):
        """Works without suffix parameter (backward compatibility)."""
        result = downloader.build_search_query("Song Title", composer="Artist")
        assert result == "Song Title Artist"

    def test_build_query_empty_suffix(self, downloader):
        """Empty suffix is handled correctly."""
        result = downloader.build_search_query("Song Title", suffix="")
        assert result == "Song Title"


class TestPreviewVideo:
    """Tests for the preview_video method."""

    @pytest.fixture
    def downloader(self, tmp_path):
        return YouTubeDownloader(output_dir=tmp_path)

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_preview_video_success(self, mock_ydl_class, downloader):
        """Returns correct video info dict."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "entries": [{
                "id": "abc123",
                "title": "Test Video",
                "duration": 245,
                "webpage_url": "https://youtube.com/watch?v=abc123",
            }]
        }
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        result = downloader.preview_video("Test Song")

        assert result is not None
        assert result["id"] == "abc123"
        assert result["title"] == "Test Video"
        assert result["duration"] == 245
        assert result["webpage_url"] == "https://youtube.com/watch?v=abc123"

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_preview_video_no_results(self, mock_ydl_class, downloader):
        """Returns None for no results."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = None
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        result = downloader.preview_video("nonexistent query")

        assert result is None

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_preview_video_handles_direct_url(self, mock_ydl_class, downloader):
        """Handles direct URL without ytsearch prefix."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "id": "xyz789",
            "title": "Direct Video",
            "duration": 180,
            "webpage_url": "https://youtube.com/watch?v=xyz789",
        }
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        result = downloader.preview_video("https://youtube.com/watch?v=xyz789")

        assert result is not None
        assert result["id"] == "xyz789"
        # Verify URL was passed directly (not with ytsearch prefix)
        mock_ydl.extract_info.assert_called_once_with(
            "https://youtube.com/watch?v=xyz789", download=False
        )

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_preview_video_wraps_download_error(self, mock_ydl_class, downloader):
        """DownloadError is wrapped as RuntimeError."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError("network error")
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        with pytest.raises(RuntimeError, match="Failed to preview video"):
            downloader.preview_video("query")


class TestDownloadByUrl:
    """Tests for the download_by_url method."""

    @pytest.fixture
    def downloader(self, tmp_path):
        return YouTubeDownloader(output_dir=tmp_path)

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_download_by_url_success(self, mock_ydl_class, tmp_path, downloader):
        """Downloads from direct URL."""
        mp3_file = tmp_path / "Test Song.mp3"
        mp3_file.write_bytes(b"fake mp3 data")

        mock_ydl = MagicMock()
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        result = downloader.download_by_url("https://youtube.com/watch?v=abc123")

        assert result == mp3_file
        # Verify URL was passed directly to download
        mock_ydl.download.assert_called_once_with(["https://youtube.com/watch?v=abc123"])

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_download_by_url_error(self, mock_ydl_class, downloader):
        """DownloadError is wrapped as RuntimeError."""
        mock_ydl = MagicMock()
        mock_ydl.download.side_effect = yt_dlp.utils.DownloadError("network error")
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        with pytest.raises(RuntimeError, match="Download failed"):
            downloader.download_by_url("https://youtube.com/watch?v=bad")
