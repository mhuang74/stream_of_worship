"""Tests for forced alignment job processing."""

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, patch

import pytest

from sow_analysis.models import (
    ForcedAlignmentJobRequest,
    ForcedAlignmentOptions,
    JobResult,
    JobStatus,
    JobType,
)
from sow_analysis.workers.forced_alignment import (
    format_timestamp,
    map_segments_to_lines,
    normalize_text,
    validate_audio_duration,
)


class TestForcedAlignmentJobRequest:
    """Test ForcedAlignmentJobRequest model validation."""

    def test_default_options(self):
        req = ForcedAlignmentJobRequest(
            audio_url="s3://bucket/audio.mp3",
            content_hash="abc123",
            lyrics_text="test lyrics",
        )
        assert req.options.language == "auto"
        assert req.options.force is False
        assert req.options.use_vocals_stem is True

    def test_custom_options(self):
        req = ForcedAlignmentJobRequest(
            audio_url="s3://bucket/audio.mp3",
            content_hash="abc123",
            lyrics_text="test lyrics",
            options=ForcedAlignmentOptions(language="en", force=True, use_vocals_stem=False),
        )
        assert req.options.language == "en"
        assert req.options.force is True
        assert req.options.use_vocals_stem is False

    def test_invalid_language(self):
        with pytest.raises(Exception):
            ForcedAlignmentJobRequest(
                audio_url="s3://bucket/audio.mp3",
                content_hash="abc123",
                lyrics_text="test lyrics",
                options=ForcedAlignmentOptions(language="fr"),
            )


class TestFormatTimestamp:
    """Test format_timestamp function."""

    def test_zero(self):
        assert format_timestamp(0.0) == "[00:00.00]"

    def test_minutes_seconds(self):
        assert format_timestamp(65.5) == "[01:05.50]"

    def test_large_minutes(self):
        assert format_timestamp(185.37) == "[03:05.37]"


class TestValidateAudioDuration:
    """Test validate_audio_duration function."""

    def test_under_limit(self, tmp_path):
        audio_path = tmp_path / "test.wav"
        with patch("soundfile.info") as mock_info_fn:
            mock_info = MagicMock()
            mock_info.duration = 120.0
            mock_info_fn.return_value = mock_info
            result = validate_audio_duration(audio_path)
            assert result == 120.0

    def test_over_limit(self, tmp_path):
        audio_path = tmp_path / "test.wav"
        with patch("soundfile.info") as mock_info_fn:
            mock_info = MagicMock()
            mock_info.duration = 400.0
            mock_info_fn.return_value = mock_info
            with pytest.raises(ValueError, match="exceeds"):
                validate_audio_duration(audio_path)

    def test_librosa_fallback(self, tmp_path):
        audio_path = tmp_path / "test.mp3"
        with patch("soundfile.info", side_effect=Exception("soundfile failed")):
            with patch("librosa.get_duration", return_value=60.0):
                result = validate_audio_duration(audio_path)
                assert result == 60.0


class TestForcedAlignmentWorker:
    """Test forced alignment job processing with mocked dependencies."""

    @pytest.fixture
    def mock_queue(self):
        from sow_analysis.workers.queue import JobQueue

        queue = JobQueue(max_concurrent_local_model=1, cache_dir=Path("/tmp/test_cache"))
        queue._forced_aligner_wrapper = AsyncMock()
        queue._forced_aligner_wrapper.align = AsyncMock(
            return_value=[
                (0.0, 2.0, "hello"),
                (2.0, 4.0, "world"),
            ]
        )
        queue.r2_client = AsyncMock()
        queue.r2_client.bucket = "test-bucket"
        queue.r2_client.download_audio = AsyncMock()
        queue.r2_client.upload_official_lrc = AsyncMock(return_value="s3://bucket/test/lyrics.lrc")
        queue.r2_client.head_object = AsyncMock(return_value={"ETag": '"abc123"'})
        queue.r2_client.check_exists = AsyncMock(return_value=False)
        queue.r2_client.copy_object = AsyncMock()
        queue.job_store = AsyncMock()
        queue.job_store.update_job = AsyncMock()
        queue._resolve_transcription_audio = AsyncMock(
            return_value=MagicMock(
                path=Path("/tmp/test_audio.wav"),
                r2_url=None,
                stem_kind="full_mix",
                is_dry_or_clean_vocals=False,
            )
        )
        return queue

    @pytest.fixture
    def fa_job(self):
        from sow_analysis.models import Job

        request = ForcedAlignmentJobRequest(
            audio_url="s3://bucket/audio.mp3",
            content_hash="abc123def456",
            lyrics_text="hello\nworld",
            song_title="Test Song",
            options=ForcedAlignmentOptions(language="auto"),
        )
        return Job(
            id="job_test123",
            type=JobType.FORCED_ALIGNMENT,
            status=JobStatus.QUEUED,
            request=request,
        )

    @pytest.mark.asyncio
    async def test_process_forced_alignment_job_success(self, mock_queue, fa_job):
        with patch("sow_analysis.workers.queue.validate_audio_duration", return_value=120.0):
            await mock_queue._process_forced_alignment_job(fa_job)

        assert fa_job.status == JobStatus.COMPLETED
        assert fa_job.result is not None
        assert fa_job.result.lrc_source == "forced_alignment"
        assert fa_job.result.line_count == 2
        assert fa_job.result.lrc_url == "s3://bucket/test/lyrics.lrc"
        mock_queue.r2_client.upload_official_lrc.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_forced_alignment_job_language_mapping(self, mock_queue, fa_job):
        fa_job.request.options.language = "en"
        with patch("sow_analysis.workers.queue.validate_audio_duration", return_value=120.0):
            await mock_queue._process_forced_alignment_job(fa_job)

        mock_queue._forced_aligner_wrapper.align.assert_called_once()
        call_args = mock_queue._forced_aligner_wrapper.align.call_args
        assert call_args[0][2] == "English"

    @pytest.mark.asyncio
    async def test_process_forced_alignment_job_logs_resolved_audio_input(
        self, mock_queue, fa_job, caplog
    ):
        from sow_analysis.workers.queue import ResolvedTranscriptionAudio

        stem_path = Path("/tmp/vocals_dry.flac")
        stem_url = "s3://bucket/abc123def456/stems/vocals_dry.flac"
        mock_queue._resolve_transcription_audio.return_value = ResolvedTranscriptionAudio(
            path=stem_path,
            r2_url=stem_url,
            stem_kind="vocals_dry",
            is_dry_or_clean_vocals=True,
        )

        with caplog.at_level(logging.INFO, logger="sow_analysis.workers.queue"):
            with patch("sow_analysis.workers.queue.validate_audio_duration", return_value=120.0):
                await mock_queue._process_forced_alignment_job(fa_job)

        assert fa_job.status == JobStatus.COMPLETED
        mock_queue._forced_aligner_wrapper.align.assert_awaited_once()
        call_args = mock_queue._forced_aligner_wrapper.align.call_args
        assert call_args[0][0] == stem_path
        assert "Forced alignment audio input resolved" in caplog.text
        assert "stem_kind=vocals_dry" in caplog.text
        assert "is_dry_or_clean_vocals=True" in caplog.text
        assert "use_vocals_stem=True" in caplog.text
        assert f"source_url={stem_url}" in caplog.text
        assert f"local_path={stem_path}" in caplog.text

    @pytest.mark.asyncio
    async def test_process_forced_alignment_job_invalid_request(self, mock_queue):
        from sow_analysis.models import Job

        job = Job(
            id="job_invalid",
            type=JobType.FORCED_ALIGNMENT,
            status=JobStatus.QUEUED,
            request=MagicMock(),
        )
        await mock_queue._process_forced_alignment_job(job)
        assert job.status == JobStatus.FAILED

    @pytest.mark.asyncio
    async def test_process_forced_alignment_job_no_wrapper(self, fa_job):
        from sow_analysis.workers.queue import JobQueue

        queue = JobQueue(max_concurrent_local_model=1, cache_dir=Path("/tmp/test_cache"))
        queue.r2_client = AsyncMock()
        queue.r2_client.download_audio = AsyncMock()
        queue.job_store = AsyncMock()
        queue.job_store.update_job = AsyncMock()
        queue._forced_aligner_wrapper = None

        await queue._process_forced_alignment_job(fa_job)
        assert fa_job.status == JobStatus.FAILED
        assert "not available" in fa_job.error_message

    @pytest.mark.asyncio
    async def test_process_forced_alignment_job_alignment_failure(self, mock_queue, fa_job):
        mock_queue._forced_aligner_wrapper.align = AsyncMock(
            side_effect=RuntimeError("Model failed")
        )

        with patch("sow_analysis.workers.queue.validate_audio_duration", return_value=120.0):
            await mock_queue._process_forced_alignment_job(fa_job)
        assert fa_job.status == JobStatus.FAILED
        assert "Model failed" in fa_job.error_message

    @pytest.mark.asyncio
    async def test_service_level_backup(self, mock_queue, fa_job):
        mock_queue.r2_client.head_object = AsyncMock(return_value={"ETag": '"oldetag"'})
        mock_queue.r2_client.upload_official_lrc = AsyncMock(
            return_value="s3://bucket/test/lyrics.lrc"
        )

        with patch("sow_analysis.workers.queue.validate_audio_duration", return_value=120.0):
            await mock_queue._process_forced_alignment_job(fa_job)

        assert fa_job.status == JobStatus.COMPLETED
        mock_queue.r2_client.upload_official_lrc.assert_awaited_once()
        call_kwargs = mock_queue.r2_client.upload_official_lrc.call_args.kwargs
        assert call_kwargs.get("expected_etag") == "oldetag"

    @pytest.mark.asyncio
    async def test_deadlock_prevention(self, fa_job):
        """Verify forced alignment job + stem separation child job completes
        when SOW_MAX_CONCURRENT_LOCAL_MODEL_JOBS=1."""
        from sow_analysis.workers.queue import JobQueue

        queue = JobQueue(max_concurrent_local_model=1, cache_dir=Path("/tmp/test_cache"))
        queue._forced_aligner_wrapper = AsyncMock()
        queue._forced_aligner_wrapper.align = AsyncMock(return_value=[(0.0, 2.0, "test")])
        queue.r2_client = AsyncMock()
        queue.r2_client.bucket = "test-bucket"
        queue.r2_client.download_audio = AsyncMock()
        queue.r2_client.upload_official_lrc = AsyncMock(return_value="s3://bucket/test.lrc")
        queue.r2_client.head_object = AsyncMock(return_value={"ETag": '"etag"'})
        queue.r2_client.check_exists = AsyncMock(return_value=False)
        queue.r2_client.copy_object = AsyncMock()
        queue.job_store = AsyncMock()
        queue.job_store.update_job = AsyncMock()

        fa_job.request.options.use_vocals_stem = False

        with patch("sow_analysis.workers.queue.validate_audio_duration", return_value=120.0):
            await queue._process_forced_alignment_job(fa_job)
        assert fa_job.status == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_auto_detect_english(self, mock_queue, fa_job):
        """When language='auto' and lyrics are English, resolve to 'English'."""
        fa_job.request.options.language = "auto"
        fa_job.request.lyrics_text = "Hello world\nThis is English"
        fa_job.request.song_title = "English Song"

        with (
            patch("sow_analysis.workers.queue.resolve_lrc_language") as mock_resolve,
            patch("sow_analysis.workers.queue.warn_if_lrc_language_script_mismatch"),
            patch("sow_analysis.workers.queue.validate_audio_duration", return_value=120.0),
        ):
            from sow_analysis.workers.lrc import LrcLanguageResolution

            mock_resolve.return_value = LrcLanguageResolution(
                requested="auto", resolved="en", reason="title_latin"
            )
            await mock_queue._process_forced_alignment_job(fa_job)

        assert fa_job.status == JobStatus.COMPLETED
        call_args = mock_queue._forced_aligner_wrapper.align.call_args
        assert call_args[0][2] == "English"

    @pytest.mark.asyncio
    async def test_auto_detect_chinese(self, mock_queue, fa_job):
        """When language='auto' and lyrics are Chinese, resolve to 'Chinese'."""
        fa_job.request.options.language = "auto"
        fa_job.request.lyrics_text = "我要看見\n如同摩西看見祢的榮耀"
        fa_job.request.song_title = "中文歌曲"

        with (
            patch("sow_analysis.workers.queue.resolve_lrc_language") as mock_resolve,
            patch("sow_analysis.workers.queue.warn_if_lrc_language_script_mismatch"),
            patch("sow_analysis.workers.queue.validate_audio_duration", return_value=120.0),
        ):
            from sow_analysis.workers.lrc import LrcLanguageResolution

            mock_resolve.return_value = LrcLanguageResolution(
                requested="auto", resolved="zh", reason="title_cjk"
            )
            await mock_queue._process_forced_alignment_job(fa_job)

        assert fa_job.status == JobStatus.COMPLETED
        call_args = mock_queue._forced_aligner_wrapper.align.call_args
        assert call_args[0][2] == "Chinese"

    @pytest.mark.asyncio
    async def test_explicit_zh_bypasses_auto(self, mock_queue, fa_job):
        """When language='zh' explicitly, auto-detection is skipped."""
        fa_job.request.options.language = "zh"

        with patch("sow_analysis.workers.queue.validate_audio_duration", return_value=120.0):
            await mock_queue._process_forced_alignment_job(fa_job)

        assert fa_job.status == JobStatus.COMPLETED
        call_args = mock_queue._forced_aligner_wrapper.align.call_args
        assert call_args[0][2] == "Chinese"

    @pytest.mark.asyncio
    async def test_explicit_en_bypasses_auto(self, mock_queue, fa_job):
        """When language='en' explicitly, auto-detection is skipped."""
        fa_job.request.options.language = "en"

        with patch("sow_analysis.workers.queue.validate_audio_duration", return_value=120.0):
            await mock_queue._process_forced_alignment_job(fa_job)

        assert fa_job.status == JobStatus.COMPLETED
        call_args = mock_queue._forced_aligner_wrapper.align.call_args
        assert call_args[0][2] == "English"

    @pytest.mark.asyncio
    async def test_auto_detect_no_resolver_fails(self, fa_job):
        """When language='auto' but resolver is not available, job fails."""
        from sow_analysis.workers.queue import JobQueue

        queue = JobQueue(max_concurrent_local_model=1, cache_dir=Path("/tmp/test_cache"))
        queue._forced_aligner_wrapper = AsyncMock()
        queue.r2_client = AsyncMock()
        queue.job_store = AsyncMock()
        queue.job_store.update_job = AsyncMock()

        fa_job.request.options.language = "auto"

        with patch("sow_analysis.workers.queue.resolve_lrc_language", None):
            await queue._process_forced_alignment_job(fa_job)

        assert fa_job.status == JobStatus.FAILED
        assert (
            "auto-detection" in fa_job.error_message or "Language resolver" in fa_job.error_message
        )

    @pytest.mark.asyncio
    async def test_stale_object_fails_job(self, mock_queue, fa_job):
        """If lyrics.lrc ETag changes after job start, upload fails with stale_object."""
        from sow_analysis.storage.r2 import StaleObjectError

        mock_queue.r2_client.head_object = AsyncMock(return_value={"ETag": '"oldetag"'})
        mock_queue.r2_client.upload_official_lrc = AsyncMock(
            side_effect=StaleObjectError("lyrics.lrc was modified")
        )

        with patch("sow_analysis.workers.queue.validate_audio_duration", return_value=120.0):
            await mock_queue._process_forced_alignment_job(fa_job)

        assert fa_job.status == JobStatus.FAILED
        assert "stale_object" in fa_job.stage
        assert "modified" in fa_job.error_message

    @pytest.mark.asyncio
    async def test_backup_failure_fails_job(self, mock_queue, fa_job):
        """If backup fails and skip_backup=False, job fails with backup_failed."""
        from sow_analysis.storage.r2 import BackupFailedError

        mock_queue.r2_client.head_object = AsyncMock(return_value={"ETag": '"etag"'})
        mock_queue.r2_client.upload_official_lrc = AsyncMock(
            side_effect=BackupFailedError("copy failed")
        )

        with patch("sow_analysis.workers.queue.validate_audio_duration", return_value=120.0):
            await mock_queue._process_forced_alignment_job(fa_job)

        assert fa_job.status == JobStatus.FAILED
        assert "backup_failed" in fa_job.stage


class TestForcedAlignerWrapper:
    """Test ForcedAlignerWrapper double-check locking."""

    @pytest.mark.asyncio
    async def test_ensure_ready_raises_on_failure(self):
        from sow_analysis.workers.forced_aligner import ForcedAlignerWrapper

        wrapper = ForcedAlignerWrapper(model_path="/nonexistent", device="cpu")
        wrapper.initialize = AsyncMock()
        wrapper._ready = False

        with pytest.raises(RuntimeError, match="failed to load"):
            await wrapper._ensure_ready()

    @pytest.mark.asyncio
    async def test_ensure_ready_skips_if_ready(self):
        from sow_analysis.workers.forced_aligner import ForcedAlignerWrapper

        wrapper = ForcedAlignerWrapper(model_path="test", device="cpu")
        wrapper._ready = True

        await wrapper._ensure_ready()

    @pytest.mark.asyncio
    async def test_cleanup(self):
        from sow_analysis.workers.forced_aligner import ForcedAlignerWrapper

        wrapper = ForcedAlignerWrapper(model_path="test", device="cpu")
        wrapper._ready = True
        wrapper._model = MagicMock()

        await wrapper.cleanup()

        assert wrapper._ready is False
        assert wrapper._model is None
        assert wrapper.is_ready is False
