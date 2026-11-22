from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sow_render_worker.asset_fetcher import AssetFetcher, DEFAULT_CACHE_DIR, DEFAULT_TEMP_DIR
from sow_render_worker.r2_client import R2Client


def _make_mock_r2_client() -> MagicMock:
    client = MagicMock(spec=R2Client)
    client.get_audio_signed_url.return_value = "https://signed-url.example.com/audio.mp3"
    client.get_lrc_signed_url.return_value = "https://signed-url.example.com/lyrics.lrc"
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

    def test_get_job_temp_dir_creates_job_subdirectory(self, tmp_path):
        temp_dir = str(tmp_path / "temp")
        Path(temp_dir).mkdir(parents=True)
        fetcher = _make_fetcher(temp_dir=temp_dir)

        result = fetcher.get_job_temp_dir("job_123")

        assert result == Path(temp_dir) / "job_123"
        assert result.exists()


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

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.stream.return_value = [b"downloaded audio data"]
        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        fetcher._http = mock_http

        result = fetcher.download_audio("abc123")

        assert result is not None
        assert Path(result).exists()
        assert Path(result).read_bytes() == b"downloaded audio data"
        assert Path(result).name == "abc123.mp3"
        mock_r2.get_audio_signed_url.assert_called_once_with(
            "abc123", expires_in_seconds=3600
        )
        mock_response.release_conn.assert_called()

    def test_raises_on_download_failure(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        Path(cache_dir).mkdir(parents=True)

        mock_r2 = _make_mock_r2_client()
        fetcher = _make_fetcher(cache_dir=cache_dir, r2_client=mock_r2)

        mock_response = MagicMock()
        mock_response.status = 403
        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        fetcher._http = mock_http

        with pytest.raises(RuntimeError, match="Failed to download audio"):
            fetcher.download_audio("abc123")
        mock_response.release_conn.assert_called()

    def test_raises_on_exception(self, tmp_path):
        cache_dir = str(tmp_path / "cache")
        Path(cache_dir).mkdir(parents=True)

        mock_r2 = _make_mock_r2_client()
        fetcher = _make_fetcher(cache_dir=cache_dir, r2_client=mock_r2)

        mock_http = MagicMock()
        mock_http.request.side_effect = Exception("network error")

        fetcher._http = mock_http

        with pytest.raises(RuntimeError, match="Failed to download audio"):
            fetcher.download_audio("abc123")

    def test_creates_cache_dir_if_missing(self, tmp_path):
        cache_dir = str(tmp_path / "new_cache")

        mock_r2 = _make_mock_r2_client()
        fetcher = _make_fetcher(cache_dir=cache_dir, r2_client=mock_r2)

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.stream.return_value = [b"audio data"]
        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        fetcher._http = mock_http

        result = fetcher.download_audio("abc123")

        assert result is not None
        assert Path(cache_dir).exists()


class TestDownloadLrc:
    def test_downloads_lrc_content(self, tmp_path):
        mock_r2 = _make_mock_r2_client()
        fetcher = _make_fetcher(r2_client=mock_r2)

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.stream.return_value = ["[00:01.00]第一行\n[00:05.00]第二行".encode("utf-8")]
        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        fetcher._http = mock_http

        result = fetcher.download_lrc("abc123")

        assert result == "[00:01.00]第一行\n[00:05.00]第二行"
        mock_r2.get_lrc_signed_url.assert_called_once_with(
            "abc123", expires_in_seconds=3600
        )
        mock_response.release_conn.assert_called()

    def test_raises_on_download_failure(self, tmp_path):
        mock_r2 = _make_mock_r2_client()
        fetcher = _make_fetcher(r2_client=mock_r2)

        mock_response = MagicMock()
        mock_response.status = 404
        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        fetcher._http = mock_http

        with pytest.raises(RuntimeError, match="Failed to download LRC"):
            fetcher.download_lrc("abc123")
        mock_response.release_conn.assert_called()

    def test_raises_on_exception(self, tmp_path):
        mock_r2 = _make_mock_r2_client()
        fetcher = _make_fetcher(r2_client=mock_r2)

        mock_http = MagicMock()
        mock_http.request.side_effect = Exception("network error")

        fetcher._http = mock_http

        with pytest.raises(RuntimeError, match="Failed to download LRC"):
            fetcher.download_lrc("abc123")

    def test_caches_lrc_content(self, tmp_path):
        mock_r2 = _make_mock_r2_client()
        fetcher = _make_fetcher(r2_client=mock_r2)

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.stream.return_value = [b"cached lrc"]
        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

        fetcher._http = mock_http

        result1 = fetcher.download_lrc("abc123")
        result2 = fetcher.download_lrc("abc123")

        assert result1 == "cached lrc"
        assert result2 == "cached lrc"
        mock_r2.get_lrc_signed_url.assert_called_once()


class TestCleanupTemp:
    def test_removes_job_temp_dir(self, tmp_path):
        temp_dir = str(tmp_path / "temp")
        Path(temp_dir).mkdir(parents=True)
        job_dir = Path(temp_dir) / "job_123"
        job_dir.mkdir()
        (job_dir / "output.mp3").write_bytes(b"data1")
        (job_dir / "output.mp4").write_bytes(b"data2")

        fetcher = _make_fetcher(temp_dir=temp_dir)
        fetcher.get_job_temp_dir("job_123")
        fetcher.cleanup_temp()

        assert not job_dir.exists()
        assert Path(temp_dir).exists()

    def test_no_op_when_no_job_temp_dir(self, tmp_path):
        temp_dir = str(tmp_path / "temp")
        Path(temp_dir).mkdir(parents=True)
        (Path(temp_dir) / "output.mp3").write_bytes(b"data1")

        fetcher = _make_fetcher(temp_dir=temp_dir)
        fetcher.cleanup_temp()

        assert Path(temp_dir).exists()

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

        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.stream.return_value = [b"audio data"]
        mock_http = MagicMock()
        mock_http.request.return_value = mock_response

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

        mock_response1 = MagicMock()
        mock_response1.status = 200
        mock_response1.stream.return_value = [b"audio1"]

        mock_response2 = MagicMock()
        mock_response2.status = 200
        mock_response2.stream.return_value = [b"audio2"]

        mock_http = MagicMock()
        mock_http.request.side_effect = [mock_response1, mock_response2]

        fetcher._http = mock_http

        result1 = fetcher.download_audio("song1")
        result2 = fetcher.download_audio("song2")

        assert result1 is not None
        assert result2 is not None
        assert Path(result1).read_bytes() == b"audio1"
        assert Path(result2).read_bytes() == b"audio2"
        assert mock_r2.get_audio_signed_url.call_count == 2


class TestDownloadGlue:
    """Glue take downloads validate immutable length/SHA before atomic rename."""

    DATA = b"RIFF" + b"\x00" * 120 + b"GLUE-STAGED-WAV"
    R2_KEY = "glues/1/descriptor/take-1111.wav"

    def _response(self, data: bytes = DATA, status: int = 200, content_length=None):
        response = MagicMock()
        response.status = status
        response.headers = {
            "Content-Length": str(len(data) if content_length is None else content_length)
        }
        response.stream.return_value = [data[:17], data[17:]]
        return response

    def _fetcher(self, tmp_path, r2_client=None):
        cache_dir = str(tmp_path / "cache")
        temp_dir = str(tmp_path / "temp")
        fetcher = _make_fetcher(
            cache_dir=cache_dir,
            temp_dir=temp_dir,
            r2_client=r2_client or _make_mock_r2_client(),
        )
        mock_http = MagicMock()
        fetcher._http = mock_http
        return fetcher, mock_http, Path(cache_dir), Path(temp_dir)

    def _expected_sha(self):
        import hashlib

        return hashlib.sha256(self.DATA).hexdigest()

    def _download(self, fetcher, **overrides):
        kwargs = dict(
            expected_bytes=len(self.DATA),
            expected_sha256=self._expected_sha(),
            cache_key="glue-take-1",
        )
        kwargs.update(overrides)
        return fetcher.download_glue(self.R2_KEY, **kwargs)

    def test_success_validates_length_and_sha_then_atomically_replaces(self, tmp_path):
        import os

        from unittest.mock import patch

        fetcher, mock_http, cache_dir, temp_dir = self._fetcher(tmp_path)
        mock_http.request.return_value = self._response()
        fetcher._r2_client.generate_signed_url.return_value = "https://signed/x.wav"

        replace_calls = []
        real_replace = os.replace

        def _recording_replace(src, dst):
            replace_calls.append((str(src), str(dst)))
            assert Path(src).read_bytes() == self.DATA
            return real_replace(src, dst)

        with patch("sow_render_worker.asset_fetcher.os.replace", side_effect=_recording_replace):
            result = self._download(fetcher)

        assert Path(result) == cache_dir / "glue-take-1.wav"
        assert Path(result).read_bytes() == self.DATA
        assert len(replace_calls) == 1
        assert list(temp_dir.glob("*.part")) == []
        fetcher._r2_client.generate_signed_url.assert_called_once()
        assert fetcher._r2_client.generate_signed_url.call_args[0][0] == self.R2_KEY
        mock_http.request.assert_called_once()

    def test_truncated_cached_file_is_never_a_cache_hit(self, tmp_path):
        fetcher, mock_http, cache_dir, _ = self._fetcher(tmp_path)
        cache_dir.mkdir(parents=True)
        (cache_dir / "glue-take-1.wav").write_bytes(self.DATA[:10])
        mock_http.request.return_value = self._response()

        result = self._download(fetcher)

        assert Path(result).read_bytes() == self.DATA
        mock_http.request.assert_called_once()

    def test_corrupt_cached_file_with_right_size_is_never_a_cache_hit(self, tmp_path):
        fetcher, mock_http, cache_dir, _ = self._fetcher(tmp_path)
        cache_dir.mkdir(parents=True)
        (cache_dir / "glue-take-1.wav").write_bytes(b"x" * len(self.DATA))
        mock_http.request.return_value = self._response()

        result = self._download(fetcher)

        assert Path(result).read_bytes() == self.DATA
        mock_http.request.assert_called_once()

    def test_valid_cached_file_is_a_hit_without_a_request(self, tmp_path):
        fetcher, mock_http, cache_dir, _ = self._fetcher(tmp_path)
        cache_dir.mkdir(parents=True)
        (cache_dir / "glue-take-1.wav").write_bytes(self.DATA)

        result = self._download(fetcher)

        assert Path(result).read_bytes() == self.DATA
        mock_http.request.assert_not_called()

    def test_checksum_mismatch_raises_and_leaves_no_cache_file(self, tmp_path):
        from sow_render_worker.asset_fetcher import GlueDownloadError

        fetcher, mock_http, cache_dir, temp_dir = self._fetcher(tmp_path)
        mock_http.request.return_value = self._response(data=b"X" * len(self.DATA))

        with pytest.raises(GlueDownloadError) as excinfo:
            self._download(fetcher)

        assert excinfo.value.code == "GLUE_AUDIO_UNAVAILABLE"
        assert "checksum" in str(excinfo.value).lower()
        assert list(cache_dir.glob("*.wav")) == []
        assert list(temp_dir.glob("*.part")) == []
        assert mock_http.request.call_count == 1

    def test_length_mismatch_raises_and_leaves_no_cache_file(self, tmp_path):
        from sow_render_worker.asset_fetcher import GlueDownloadError

        fetcher, mock_http, cache_dir, temp_dir = self._fetcher(tmp_path)
        mock_http.request.return_value = self._response()

        with pytest.raises(GlueDownloadError, match="length"):
            self._download(fetcher, expected_bytes=len(self.DATA) + 1)

        assert list(cache_dir.glob("*.wav")) == []
        assert list(temp_dir.glob("*.part")) == []

    def test_missing_object_is_not_retried(self, tmp_path):
        from sow_render_worker.asset_fetcher import GlueDownloadError

        fetcher, mock_http, _, _ = self._fetcher(tmp_path)
        mock_http.request.return_value = self._response(status=404)

        with pytest.raises(GlueDownloadError) as excinfo:
            self._download(fetcher)

        assert excinfo.value.code == "GLUE_AUDIO_UNAVAILABLE"
        assert mock_http.request.call_count == 1

    def test_failure_log_names_job_and_take(self, tmp_path, caplog):
        import logging

        from sow_render_worker.asset_fetcher import GlueDownloadError

        fetcher, mock_http, _, _ = self._fetcher(tmp_path)
        mock_http.request.return_value = self._response(status=404)

        with caplog.at_level(
            logging.ERROR, logger="sow_render_worker.asset_fetcher"
        ):
            with pytest.raises(GlueDownloadError):
                self._download(fetcher, job_id="job-1", take_id="take-1")

        assert "glue-take-1.wav" in caplog.text
        assert "job=job-1" in caplog.text
        assert "take=take-1" in caplog.text

    def test_retries_transient_failure_with_full_jitter_and_succeeds(
        self, tmp_path, monkeypatch, caplog
    ):
        import logging

        from unittest.mock import patch

        fetcher, mock_http, _, _ = self._fetcher(tmp_path)
        mock_http.request.side_effect = [OSError("connection reset"), self._response()]

        delays: list[tuple[float, float]] = []
        sleeps: list[float] = []

        def fake_uniform(low, high):
            delays.append((low, high))
            return high

        monkeypatch.setattr("sow_render_worker.asset_fetcher.random.uniform", fake_uniform)
        monkeypatch.setattr("sow_render_worker.asset_fetcher.time.sleep", sleeps.append)

        with caplog.at_level(logging.WARNING, logger="sow_render_worker.asset_fetcher"):
            result = self._download(fetcher)

        assert Path(result).read_bytes() == self.DATA
        assert delays == [(0, 1.0)]
        assert sleeps == [1.0]
        assert "retry attempt 1" in caplog.text
        assert mock_http.request.call_count == 2

    def test_jitter_caps_are_one_two_and_four_seconds(self, tmp_path, monkeypatch):
        from sow_render_worker.asset_fetcher import GlueDownloadError

        fetcher, mock_http, _, _ = self._fetcher(tmp_path)
        mock_http.request.side_effect = OSError("down")

        delays: list[tuple[float, float]] = []
        sleeps: list[float] = []

        def fake_uniform(low, high):
            delays.append((low, high))
            return 0.0

        monkeypatch.setattr("sow_render_worker.asset_fetcher.random.uniform", fake_uniform)
        monkeypatch.setattr("sow_render_worker.asset_fetcher.time.sleep", sleeps.append)

        with pytest.raises(GlueDownloadError):
            self._download(fetcher)

        assert delays == [(0, 1.0), (0, 2.0), (0, 4.0)]
        assert sleeps == [0.0, 0.0, 0.0]
        assert mock_http.request.call_count == 4

    def test_exhausted_retries_raise_distinct_error(self, tmp_path, monkeypatch):
        from sow_render_worker.asset_fetcher import GlueDownloadError

        fetcher, mock_http, cache_dir, _ = self._fetcher(tmp_path)
        mock_http.request.side_effect = OSError("down")
        monkeypatch.setattr("sow_render_worker.asset_fetcher.time.sleep", lambda _: None)

        with pytest.raises(GlueDownloadError) as excinfo:
            self._download(fetcher)

        assert excinfo.value.code == "GLUE_AUDIO_UNAVAILABLE"
        assert "exhausted" in str(excinfo.value).lower()
        assert list(cache_dir.glob("*.wav")) == []
        assert mock_http.request.call_count == 4

    def test_connect_and_read_timeouts_are_configured_separately(self, tmp_path):
        fetcher, mock_http, _, _ = self._fetcher(tmp_path)
        mock_http.request.return_value = self._response()

        self._download(fetcher)

        timeout = mock_http.request.call_args.kwargs["timeout"]
        assert timeout.connect_timeout == 30
        assert timeout.read_timeout == 300

    def test_error_message_does_not_leak_signed_url_or_secrets(self, tmp_path, monkeypatch):
        from sow_render_worker.asset_fetcher import GlueDownloadError

        fetcher, mock_http, _, _ = self._fetcher(tmp_path)
        mock_http.request.side_effect = OSError(
            "https://signed-url.example.com/x.wav?X-Amz-Signature=super-secret failed"
        )
        monkeypatch.setattr("sow_render_worker.asset_fetcher.time.sleep", lambda _: None)

        with pytest.raises(GlueDownloadError) as excinfo:
            self._download(fetcher)

        message = str(excinfo.value)
        assert "https://" not in message
        assert "X-Amz" not in message
        assert "super-secret" not in message
        assert "signed-url.example.com" not in message
