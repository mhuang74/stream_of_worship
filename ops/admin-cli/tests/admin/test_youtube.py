"""Tests for YouTube download service."""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest
import yt_dlp

from stream_of_worship.admin.services.youtube import (
    YouTubeDownloader,
    _extract_chinese_title_from_youtube,
    _select_best_candidate,
    derive_song_defaults,
    extract_video_id,
    extract_video_metadata,
    fetch_transcript_lines,
)


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


class TestDownloadWithInfo:
    """Tests for the download_with_info method."""

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_download_with_info_returns_path_and_url(self, mock_ydl_class, tmp_path):
        """Returns tuple of (Path, webpage_url, video_title) when download succeeds."""
        mp3_file = tmp_path / "Test Song.mp3"
        mp3_file.write_bytes(b"fake mp3 data")

        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "entries": [{
                "id": "abc123",
                "title": "Test Song",
                "webpage_url": "https://youtube.com/watch?v=abc123",
            }]
        }
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        downloader = YouTubeDownloader(output_dir=tmp_path)
        path, url, video_title = downloader.download_with_info("Test Song query")

        assert path == mp3_file
        assert url == "https://youtube.com/watch?v=abc123"
        assert video_title == "Test Song"
        mock_ydl.extract_info.assert_called_once_with(
            "ytsearch1:Test Song query", download=True
        )

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_download_with_info_handles_direct_video_info(self, mock_ydl_class, tmp_path):
        """Handles direct video info (not in entries list)."""
        mp3_file = tmp_path / "Direct Video.mp3"
        mp3_file.write_bytes(b"fake mp3 data")

        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "id": "xyz789",
            "title": "Direct Video",
            "webpage_url": "https://youtube.com/watch?v=xyz789",
        }
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        downloader = YouTubeDownloader(output_dir=tmp_path)
        path, url, video_title = downloader.download_with_info("query")

        assert path == mp3_file
        assert url == "https://youtube.com/watch?v=xyz789"
        assert video_title == "Direct Video"

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_download_with_info_no_results(self, mock_ydl_class, tmp_path):
        """Raises RuntimeError when extract_info returns None."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = None
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        downloader = YouTubeDownloader(output_dir=tmp_path)
        with pytest.raises(RuntimeError, match="No results found"):
            downloader.download_with_info("nonexistent query")

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_download_with_info_missing_webpage_url(self, mock_ydl_class, tmp_path):
        """Returns None for webpage_url and video_title when not in video info."""
        mp3_file = tmp_path / "Song.mp3"
        mp3_file.write_bytes(b"fake mp3 data")

        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "entries": [{
                "id": "abc123",
                "title": "Song",
            }]
        }
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        downloader = YouTubeDownloader(output_dir=tmp_path)
        path, url, video_title = downloader.download_with_info("query")

        assert path == mp3_file
        assert url is None
        assert video_title == "Song"

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_download_with_info_error(self, mock_ydl_class, tmp_path):
        """yt_dlp DownloadError is wrapped as RuntimeError."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError("network error")
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        downloader = YouTubeDownloader(output_dir=tmp_path)
        with pytest.raises(RuntimeError, match="Download failed"):
            downloader.download_with_info("bad query")


class TestMetadataHelpers:
    def test_extract_video_id_supports_watch_and_short_urls(self):
        assert extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert extract_video_id("https://youtu.be/dQw4w9WgXcQ?t=12") == "dQw4w9WgXcQ"

    def test_derive_song_defaults_parses_sample_title(self):
        metadata = types.SimpleNamespace(
            title="Here I Bow - Brian & Jenn Johnson | After All These Years",
            webpage_url="https://youtube.com/watch?v=test123",
        )
        defaults = derive_song_defaults(metadata)
        assert defaults["title"] == "Here I Bow"
        assert defaults["composer"] == "Brian & Jenn Johnson"
        assert defaults["album_name"] == "After All These Years"
        assert defaults["source_url"] == "https://youtube.com/watch?v=test123"

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_extract_video_metadata_wraps_download_error(self, mock_ydl_class):
        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError("network error")
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        with pytest.raises(RuntimeError, match="Failed to extract video metadata"):
            extract_video_metadata("https://youtube.com/watch?v=bad")


class TestTranscriptDrafts:
    def test_fetch_transcript_lines_cleans_cues_and_whitespace(self, monkeypatch):
        class FakeApi:
            @staticmethod
            def get_transcript(video_id, languages):
                return [
                    {"text": "[Music]"},
                    {"text": "  Here   I  bow "},
                    {"text": "[Laughter]"},
                    {"text": "Here I bow"},
                ]

        fake_module = types.SimpleNamespace(YouTubeTranscriptApi=FakeApi)
        monkeypatch.setitem(sys.modules, "youtube_transcript_api", fake_module)

        lines = fetch_transcript_lines("https://www.youtube.com/watch?v=test123")

        assert lines == ["Here I bow", "Here I bow"]

    def test_fetch_transcript_lines_falls_back_to_best_available_transcript(self, monkeypatch):
        class FakeTranscript:
            def __init__(self, language_code, language, is_generated, snippets):
                self.language_code = language_code
                self.language = language
                self.is_generated = is_generated
                self._snippets = snippets

            def fetch(self):
                return self._snippets

        fallback_transcript = FakeTranscript(
            "en",
            "English",
            True,
            [{"text": " Grace upon grace "}],
        )

        class FakeApi:
            @staticmethod
            def get_transcript(video_id, languages):
                raise RuntimeError("preferred transcript unavailable")

            @staticmethod
            def list_transcripts(video_id):
                return [fallback_transcript]

        fake_module = types.SimpleNamespace(YouTubeTranscriptApi=FakeApi)
        monkeypatch.setitem(sys.modules, "youtube_transcript_api", fake_module)

        lines = fetch_transcript_lines("https://www.youtube.com/watch?v=test123")

        assert lines == ["Grace upon grace"]


class TestExtractChineseTitle:
    """Tests for _extract_chinese_title_from_youtube in its new location."""

    def test_extracts_chinese_title_from_brackets(self):
        title = "【一生敬拜祢 All the Days of My Life】官方歌詞版MV"
        assert _extract_chinese_title_from_youtube(title) == "一生敬拜祢"

    def test_stops_at_whitespace_in_bracket(self):
        title = "【一生敬拜祢 All the Days】MV"
        assert _extract_chinese_title_from_youtube(title) == "一生敬拜祢"

    def test_returns_none_when_no_brackets(self):
        assert _extract_chinese_title_from_youtube("Some Video Title") is None

    def test_returns_none_for_empty_string(self):
        assert _extract_chinese_title_from_youtube("") is None

    def test_returns_none_for_none(self):
        assert _extract_chinese_title_from_youtube(None) is None

    def test_extracts_single_character_title(self):
        assert _extract_chinese_title_from_youtube("【愛】MV") == "愛"


class TestSelectBestCandidate:
    """Tests for the _select_best_candidate helper."""

    def test_returns_first_matching_candidate(self):
        entries = [
            {"title": "【另一首】Wrong Song MV", "webpage_url": "url1"},
            {"title": "【目標歌曲】Target Song MV", "webpage_url": "url2"},
            {"title": "【目標歌曲】Target Song Cover", "webpage_url": "url3"},
        ]
        result = _select_best_candidate(entries, "目標歌曲")
        assert result is not None
        assert result["webpage_url"] == "url2"

    def test_returns_none_when_no_match(self):
        entries = [
            {"title": "【第一首】Wrong MV", "webpage_url": "url1"},
            {"title": "【第二首】Wrong MV", "webpage_url": "url2"},
        ]
        assert _select_best_candidate(entries, "目標歌曲") is None

    def test_skips_entries_without_brackets(self):
        entries = [
            {"title": "No brackets here", "webpage_url": "url1"},
            {"title": "【目標歌曲】Target MV", "webpage_url": "url2"},
        ]
        result = _select_best_candidate(entries, "目標歌曲")
        assert result is not None
        assert result["webpage_url"] == "url2"

    def test_skips_none_entries(self):
        entries = [None, {"title": "【目標歌曲】MV", "webpage_url": "url1"}]
        result = _select_best_candidate(entries, "目標歌曲")
        assert result is not None
        assert result["webpage_url"] == "url1"

    def test_returns_none_for_empty_entries(self):
        assert _select_best_candidate([], "目標歌曲") is None

    def test_exact_match_only(self):
        entries = [{"title": "【目標歌曲相似】MV", "webpage_url": "url1"}]
        assert _select_best_candidate(entries, "目標歌曲") is None


class TestPreviewVideoMultiCandidate:
    """Tests for preview_video with multi-candidate scanning."""

    @pytest.fixture
    def downloader(self, tmp_path):
        return YouTubeDownloader(output_dir=tmp_path)

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_multi_candidate_returns_matched_entry(self, mock_ydl_class, downloader):
        """Returns the first entry whose Chinese title matches song_title."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "entries": [
                {"id": "1", "title": "【另一首】Wrong MV", "duration": 200, "webpage_url": "url1"},
                {"id": "2", "title": "【目標歌曲】Target MV", "duration": 250, "webpage_url": "url2"},
                {"id": "3", "title": "【目標歌曲】Cover", "duration": 180, "webpage_url": "url3"},
            ]
        }
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        result = downloader.preview_video("query", max_results=5, song_title="目標歌曲")

        assert result is not None
        assert result["id"] == "2"
        assert result["title"] == "【目標歌曲】Target MV"
        assert result["webpage_url"] == "url2"
        mock_ydl.extract_info.assert_called_once_with("ytsearch5:query", download=False)

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_multi_candidate_returns_none_when_no_match(self, mock_ydl_class, downloader):
        """Returns None when no candidate's title matches."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "entries": [
                {"id": "1", "title": "【第一首】MV", "duration": 200, "webpage_url": "url1"},
                {"id": "2", "title": "【第二首】MV", "duration": 250, "webpage_url": "url2"},
            ]
        }
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        result = downloader.preview_video("query", max_results=5, song_title="目標歌曲")

        assert result is None

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_multi_candidate_direct_url_ignores_song_title(self, mock_ydl_class, downloader):
        """Direct URL path ignores max_results and song_title."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "id": "xyz789",
            "title": "Direct Video",
            "duration": 180,
            "webpage_url": "https://youtube.com/watch?v=xyz789",
        }
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        result = downloader.preview_video(
            "https://youtube.com/watch?v=xyz789",
            max_results=5,
            song_title="目標歌曲",
        )

        assert result is not None
        assert result["id"] == "xyz789"
        mock_ydl.extract_info.assert_called_once_with(
            "https://youtube.com/watch?v=xyz789", download=False
        )

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_multi_candidate_handles_empty_entries(self, mock_ydl_class, downloader):
        """Returns None when entries list is empty."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {"entries": []}
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        result = downloader.preview_video("query", max_results=5, song_title="目標歌曲")

        assert result is None

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_multi_candidate_handles_none_entries(self, mock_ydl_class, downloader):
        """Returns None when entries is None."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {"entries": None}
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        result = downloader.preview_video("query", max_results=5, song_title="目標歌曲")

        assert result is None

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_backward_compatible_default_max_results(self, mock_ydl_class, downloader):
        """When max_results=1 (default), uses ytsearch1: (backward compat)."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "entries": [{"id": "1", "title": "Test", "duration": 200, "webpage_url": "url1"}]
        }
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        result = downloader.preview_video("query")

        assert result is not None
        assert result["id"] == "1"
        mock_ydl.extract_info.assert_called_once_with("ytsearch1:query", download=False)


class TestDownloadMultiCandidate:
    """Tests for download() with multi-candidate scanning."""

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_download_with_match_delegates_to_download_by_url(self, mock_ydl_class, tmp_path):
        """Two-phase: scans candidates, then downloads the matched URL."""
        mp3_file = tmp_path / "Target Song.mp3"
        mp3_file.write_bytes(b"fake mp3 data")

        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "entries": [
                {"id": "1", "title": "【另一首】Wrong MV", "webpage_url": "url1"},
                {"id": "2", "title": "【目標歌曲】Target MV", "webpage_url": "https://youtube.com/watch?v=2"},
            ]
        }
        mock_ydl.download.return_value = None
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        downloader = YouTubeDownloader(output_dir=tmp_path)
        result = downloader.download("query", max_results=5, song_title="目標歌曲")

        assert result == mp3_file
        # Phase 1: extract_info with download=False
        mock_ydl.extract_info.assert_called_once_with("ytsearch5:query", download=False)
        # Phase 2: download_by_url called the matched URL
        mock_ydl.download.assert_called_once_with(["https://youtube.com/watch?v=2"])

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_download_no_match_raises_runtime_error(self, mock_ydl_class, tmp_path):
        """Raises RuntimeError when no candidate matches."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "entries": [
                {"id": "1", "title": "【另一首】Wrong MV", "webpage_url": "url1"},
            ]
        }
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        downloader = YouTubeDownloader(output_dir=tmp_path)
        with pytest.raises(RuntimeError, match="No matching title found"):
            downloader.download("query", max_results=5, song_title="目標歌曲")

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_download_no_match_when_info_is_none(self, mock_ydl_class, tmp_path):
        """Raises RuntimeError when extract_info returns None."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = None
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        downloader = YouTubeDownloader(output_dir=tmp_path)
        with pytest.raises(RuntimeError, match="No matching title found"):
            downloader.download("query", max_results=5, song_title="目標歌曲")


class TestDownloadWithInfoMultiCandidate:
    """Tests for download_with_info() with multi-candidate scanning."""

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_download_with_info_match_returns_correct_tuple(self, mock_ydl_class, tmp_path):
        """Two-phase: returns (Path, url, title) from the matched candidate."""
        mp3_file = tmp_path / "Target Song.mp3"
        mp3_file.write_bytes(b"fake mp3 data")

        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "entries": [
                {"id": "1", "title": "【另一首】Wrong MV", "webpage_url": "url1"},
                {"id": "2", "title": "【目標歌曲】Target MV", "webpage_url": "https://youtube.com/watch?v=2"},
            ]
        }
        mock_ydl.download.return_value = None
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        downloader = YouTubeDownloader(output_dir=tmp_path)
        path, url, title = downloader.download_with_info(
            "query", max_results=5, song_title="目標歌曲"
        )

        assert path == mp3_file
        assert url == "https://youtube.com/watch?v=2"
        assert title == "【目標歌曲】Target MV"
        mock_ydl.extract_info.assert_called_once_with("ytsearch5:query", download=False)
        mock_ydl.download.assert_called_once_with(["https://youtube.com/watch?v=2"])

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_download_with_info_no_match_raises(self, mock_ydl_class, tmp_path):
        """Raises RuntimeError when no candidate matches."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {
            "entries": [{"id": "1", "title": "【另一首】MV", "webpage_url": "url1"}]
        }
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        downloader = YouTubeDownloader(output_dir=tmp_path)
        with pytest.raises(RuntimeError, match="No matching title found"):
            downloader.download_with_info("query", max_results=5, song_title="目標歌曲")

    @patch("stream_of_worship.admin.services.youtube.yt_dlp.YoutubeDL")
    def test_download_with_info_empty_entries_raises(self, mock_ydl_class, tmp_path):
        """Raises RuntimeError when entries list is empty."""
        mock_ydl = MagicMock()
        mock_ydl.extract_info.return_value = {"entries": []}
        mock_ydl_class.return_value.__enter__ = MagicMock(return_value=mock_ydl)
        mock_ydl_class.return_value.__exit__ = MagicMock(return_value=False)

        downloader = YouTubeDownloader(output_dir=tmp_path)
        with pytest.raises(RuntimeError, match="No matching title found"):
            downloader.download_with_info("query", max_results=5, song_title="目標歌曲")
