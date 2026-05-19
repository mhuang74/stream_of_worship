from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sow_render_worker.asset_fetcher import AssetFetcher, DEFAULT_CACHE_DIR, DEFAULT_TEMP_DIR
from sow_render_worker.r2_client import R2Client, SignedUrlResult


def _make_mock_r2_client() -> MagicMock:
    client = MagicMock(spec=R2Client)
    client.get_audio_signed_url.return_value = SignedUrlResult(
        url="https://signed-url.example.com/audio.mp3",
        expires_at=None,
        cache_control="public, max-age=3600",
    )
    client.get_lrc_signed_url.return_value = SignedUrlResult(
        url="https://signed-url.example.com/lyrics.lrc",
        expires_at=None,
        cache_control="public, max-age=86400",
    )
    return client


def _make_fetcher(
    cache_dir: str | None = None,
    temp_dir: str | None = None,
    r2_client: MagicMock | None = None,
) -> AssetFetcher:
    if r2_client is None:
        r2_client = _make_mock_r2_client()
    return AssetFetcher(
        cache_dir=cache_dir,
        temp_dir=temp_dir,
        r2_client=r2_client,
    )


class TestAssetFetcherInit:
    def test_default_dirs(self):
        fetcher = _make_fetcher()
        assert fetcher._cache_dir == Path(DEFAULT_CACHE_DIR)
        assert fetcher._temp_dir == Path(DEFAULT_TEMP_DIR)

    def test_custom_dirs(self):
        fetcher = _make_fetcher(cache_dir="/custom/cache", temp_dir="/custom/temp")
        assert fetcher._cache_dir == Path("/custom/cache")
        assert fetcher._temp_dir == Path("/custom/temp")

    def test_uses_provided_r2_client(self):
        mock_client = _make_mock_r2_client()
        fetcher = _make_fetcher(r2_client=mock_client)
        assert fetcher._r2_client is mock_client


class TestInitialize:
    def test_creates_directories(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        temp_dir = str(tmp_path / "temp")
        fetcher = _make_fetcher(cache_dir=cache_dir, temp_dir=temp_dir)

        fetcher.initialize()

        assert Path(cache_dir).exists()
        assert Path(temp_dir).exists()

    def test_idempotent(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        temp_dir = str(tmp_path / "temp")
        fetcher = _make_fetcher(cache_dir=cache_dir, temp_dir=temp_dir)

        fetcher.initialize()
        fetcher.initialize()

        assert Path(cache_dir).exists()
        assert Path(temp_dir).exists()


class TestGetTempDir:
    def test_returns_temp_dir_path(self, tmp_path):
        temp_dir = str(tmp_path / "temp")
        fetcher = _make_fetcher(temp_dir=temp_dir)

        result = fetcher.get_temp_dir()

        assert result == Path(temp_dir)
        assert Path(temp_dir).exists()


class TestGetCacheDir:
    def test_returns_cache_dir_path(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        fetcher = _make_fetcher(cache_dir=cache_dir)

        result = fetcher.get_cache_dir()

        assert result == Path(cache_dir)


class TestDownloadAudio:
    def test_returns_cached_file_if_exists(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        Path(cache_dir).mkdir(parents=True)
        cached_file = Path(cache_dir) / "abc123.mp3"
        cached_file.write_bytes(b"cached audio data")

        fetcher = _make_fetcher(cache_dir=cache_dir)

        result = fetcher.download_audio("abc123")

        assert result == str(cached_file)
        fetcher._r2_client.get_audio_signed_url.assert_not_called()

    def test_downloads_and_caches_file(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        Path(cache_dir).mkdir(parents=True)

        mock_r2 = _make_mock_r2_client()
        fetcher = _make_fetcher(cache_dir=cache_dir, r2_client=mock_r2)

        with patch("sow_render_worker.asset_fetcher.urllib3") as mock_urllib3:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.data = b"downloaded audio data"
            mock_http = MagicMock()
            mock_http.request.return_value = mock_response
            mock_urllib3.PoolManager.return_value = mock_http

            fetcher._http = mock_http

            result = fetcher.download_audio("abc123")

        assert result is not None
        assert Path(result).exists()
        assert Path(result).read_bytes() == b"downloaded audio data"
        assert Path(result).name == "abc123.mp3"
        mock_r2.get_audio_signed_url.assert_called_once_with(
            "abc123", expires_in_seconds=3600
        )

    def test_returns_none_on_download_failure(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        Path(cache_dir).mkdir(parents=True)

        mock_r2 = _make_mock_r2_client()
        fetcher = _make_fetcher(cache_dir=cache_dir, r2_client=mock_r2)

        with patch("sow_render_worker.asset_fetcher.urllib3") as mock_urllib3:
            mock_response = MagicMock()
            mock_response.status = 403
            mock_http = MagicMock()
            mock_http.request.return_value = mock_response
            mock_urllib3.PoolManager.return_value = mock_http

            fetcher._http = mock_http

            result = fetcher.download_audio("abc123")

        assert result is None

    def test_returns_none_on_exception(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        Path(cache_dir).mkdir(parents=True)

        mock_r2 = _make_mock_r2_client()
        fetcher = _make_fetcher(cache_dir=cache_dir, r2_client=mock_r2)

        with patch("sow_render_worker.asset_fetcher.urllib3") as mock_urllib3:
            mock_http = MagicMock()
            mock_http.request.side_effect = Exception("network error")
            mock_urllib3.PoolManager.return_value = mock_http

            fetcher._http = mock_http

            result = fetcher.download_audio("abc123")

        assert result is None

    def test_creates_cache_dir_if_missing(self, tmp_path):
        cache_dir = str(tmp_path / "new_cache")

        mock_r2 = _make_mock_r2_client()
        fetcher = _make_fetcher(cache_dir=cache_dir, r2_client=mock_r2)

        with patch("sow_render_worker.asset_fetcher.urllib3") as mock_urllib3:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.data = b"audio data"
            mock_http = MagicMock()
            mock_http.request.return_value = mock_response
            mock_urllib3.PoolManager.return_value = mock_http

            fetcher._http = mock_http

            result = fetcher.download_audio("abc123")

        assert result is not None
        assert Path(cache_dir).exists()


class TestDownloadLrc:
    def test_downloads_lrc_content(self, tmp_path):
        mock_r2 = _make_mock_r2_client()
        fetcher = _make_fetcher(r2_client=mock_r2)

        with patch("sow_render_worker.asset_fetcher.urllib3") as mock_urllib3:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.data = "[00:01.00]第一行\n[00:05.00]第二行".encode("utf-8")
            mock_http = MagicMock()
            mock_http.request.return_value = mock_response
            mock_urllib3.PoolManager.return_value = mock_http

            fetcher._http = mock_http

            result = fetcher.download_lrc("abc123")

        assert result == "[00:01.00]第一行\n[00:05.00]第二行"
        mock_r2.get_lrc_signed_url.assert_called_once_with(
            "abc123", expires_in_seconds=3600
        )

    def test_returns_none_on_download_failure(self, tmp_path):
        mock_r2 = _make_mock_r2_client()
        fetcher = _make_fetcher(r2_client=mock_r2)

        with patch("sow_render_worker.asset_fetcher.urllib3") as mock_urllib3:
            mock_response = MagicMock()
            mock_response.status = 404
            mock_http = MagicMock()
            mock_http.request.return_value = mock_response
            mock_urllib3.PoolManager.return_value = mock_http

            fetcher._http = mock_http

            result = fetcher.download_lrc("abc123")

        assert result is None

    def test_returns_none_on_exception(self, tmp_path):
        mock_r2 = _make_mock_r2_client()
        fetcher = _make_fetcher(r2_client=mock_r2)

        with patch("sow_render_worker.asset_fetcher.urllib3") as mock_urllib3:
            mock_http = MagicMock()
            mock_http.request.side_effect = Exception("network error")
            mock_urllib3.PoolManager.return_value = mock_http

            fetcher._http = mock_http

            result = fetcher.download_lrc("abc123")

        assert result is None


class TestIsCached:
    def test_returns_true_when_cached(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        Path(cache_dir).mkdir(parents=True)
        (Path(cache_dir) / "abc123.mp3").write_bytes(b"data")

        fetcher = _make_fetcher(cache_dir=cache_dir)

        assert fetcher.is_cached("abc123") is True

    def test_returns_false_when_not_cached(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        Path(cache_dir).mkdir(parents=True)

        fetcher = _make_fetcher(cache_dir=cache_dir)

        assert fetcher.is_cached("abc123") is False


class TestClearFileCache:
    def test_removes_cached_files(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        Path(cache_dir).mkdir(parents=True)
        (Path(cache_dir) / "abc123.mp3").write_bytes(b"data1")
        (Path(cache_dir) / "def456.mp3").write_bytes(b"data2")

        fetcher = _make_fetcher(cache_dir=cache_dir)
        fetcher.clear_file_cache()

        assert list(Path(cache_dir).iterdir()) == []

    def test_handles_empty_cache(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        Path(cache_dir).mkdir(parents=True)

        fetcher = _make_fetcher(cache_dir=cache_dir)
        fetcher.clear_file_cache()

        assert list(Path(cache_dir).iterdir()) == []

    def test_handles_nonexistent_dir(self, tmp_path):
        cache_dir = str(tmp_path / "nonexistent")

        fetcher = _make_fetcher(cache_dir=cache_dir)
        fetcher.clear_file_cache()


class TestGetCacheStats:
    def test_returns_stats_for_cached_files(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        Path(cache_dir).mkdir(parents=True)
        (Path(cache_dir) / "abc123.mp3").write_bytes(b"1234")
        (Path(cache_dir) / "def456.mp3").write_bytes(b"567890")

        fetcher = _make_fetcher(cache_dir=cache_dir)
        stats = fetcher.get_cache_stats()

        assert stats["file_count"] == 2
        assert stats["total_size_bytes"] == 10

    def test_returns_zero_for_empty_cache(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        Path(cache_dir).mkdir(parents=True)

        fetcher = _make_fetcher(cache_dir=cache_dir)
        stats = fetcher.get_cache_stats()

        assert stats["file_count"] == 0
        assert stats["total_size_bytes"] == 0

    def test_returns_zero_for_nonexistent_dir(self, tmp_path):
        cache_dir = str(tmp_path / "nonexistent")

        fetcher = _make_fetcher(cache_dir=cache_dir)
        stats = fetcher.get_cache_stats()

        assert stats["file_count"] == 0
        assert stats["total_size_bytes"] == 0


class TestCleanupTemp:
    def test_removes_temp_files(self, tmp_path):
        temp_dir = str(tmp_path / "temp")
        Path(temp_dir).mkdir(parents=True)
        (Path(temp_dir) / "output.mp3").write_bytes(b"data1")
        (Path(temp_dir) / "output.mp4").write_bytes(b"data2")

        fetcher = _make_fetcher(temp_dir=temp_dir)
        fetcher.cleanup_temp()

        assert list(Path(temp_dir).iterdir()) == []

    def test_handles_empty_dir(self, tmp_path):
        temp_dir = str(tmp_path / "temp")
        Path(temp_dir).mkdir(parents=True)

        fetcher = _make_fetcher(temp_dir=temp_dir)
        fetcher.cleanup_temp()

        assert list(Path(temp_dir).iterdir()) == []

    def test_handles_nonexistent_dir(self, tmp_path):
        temp_dir = str(tmp_path / "nonexistent")

        fetcher = _make_fetcher(temp_dir=temp_dir)
        fetcher.cleanup_temp()


class TestCachingWorkflow:
    def test_download_then_cache_hit(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        Path(cache_dir).mkdir(parents=True)

        mock_r2 = _make_mock_r2_client()
        fetcher = _make_fetcher(cache_dir=cache_dir, r2_client=mock_r2)

        with patch("sow_render_worker.asset_fetcher.urllib3") as mock_urllib3:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.data = b"audio data"
            mock_http = MagicMock()
            mock_http.request.return_value = mock_response
            mock_urllib3.PoolManager.return_value = mock_http

            fetcher._http = mock_http

            result1 = fetcher.download_audio("abc123")

        assert result1 is not None
        assert mock_r2.get_audio_signed_url.call_count == 1

        result2 = fetcher.download_audio("abc123")
        assert result2 is not None
        assert mock_r2.get_audio_signed_url.call_count == 1
        assert result1 == result2

    def test_multiple_downloads_different_files(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        Path(cache_dir).mkdir(parents=True)

        mock_r2 = _make_mock_r2_client()
        fetcher = _make_fetcher(cache_dir=cache_dir, r2_client=mock_r2)

        with patch("sow_render_worker.asset_fetcher.urllib3") as mock_urllib3:
            mock_http = MagicMock()
            mock_urllib3.PoolManager.return_value = mock_http

            fetcher._http = mock_http

            mock_response1 = MagicMock()
            mock_response1.status = 200
            mock_response1.data = b"audio1"

            mock_response2 = MagicMock()
            mock_response2.status = 200
            mock_response2.data = b"audio2"

            mock_http.request.side_effect = [mock_response1, mock_response2]

            result1 = fetcher.download_audio("song1")
            result2 = fetcher.download_audio("song2")

        assert result1 is not None
        assert result2 is not None
        assert Path(result1).read_bytes() == b"audio1"
        assert Path(result2).read_bytes() == b"audio2"
        assert mock_r2.get_audio_signed_url.call_count == 2
