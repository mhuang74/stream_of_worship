"""Tests for forced alignment job processing."""

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sow_analysis.models import (
    ForcedAlignmentJobRequest,
    ForcedAlignmentOptions,
    JobStatus,
    JobType,
)
from sow_analysis.workers.forced_alignment import (
    assign_lines_to_chunks,
    extract_chunk,
    find_chunk_boundaries,
    format_timestamp,
    pick_quiet_cuts,
    validate_audio_duration,
)


def _write_tone_wav(path: Path, duration: float, silent_windows=(), sr: int = 16_000) -> Path:
    """Write a mono 16 kHz tone WAV with near-silent windows (start, end) in seconds."""
    import numpy as np
    import soundfile

    samples = np.arange(int(duration * sr)) / sr
    tone = 0.5 * np.sin(2 * np.pi * 440.0 * samples)
    for start, end in silent_windows:
        mask = (samples >= start) & (samples < end)
        tone[mask] = 0.0001
    soundfile.write(str(path), tone.astype("float32"), sr, subtype="PCM_16")
    return path


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
    def align_client(self, mocker):
        client = MagicMock()
        client.align = AsyncMock(
            return_value=[
                (0.0, 2.0, "hello"),
                (2.0, 4.0, "world"),
            ]
        )
        mocker.patch(
            "sow_analysis.workers.queue.get_modal_forced_aligner_client",
            return_value=client,
        )
        return client

    @pytest.fixture
    def mock_queue(self, align_client):
        from sow_analysis.workers.queue import JobQueue

        queue = JobQueue(max_concurrent_analyze=1, cache_dir=Path("/tmp/test_cache"))
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
    async def test_process_forced_alignment_job_language_mapping(
        self, mock_queue, fa_job, align_client
    ):
        fa_job.request.options.language = "en"
        with patch("sow_analysis.workers.queue.validate_audio_duration", return_value=120.0):
            await mock_queue._process_forced_alignment_job(fa_job)

        align_client.align.assert_called_once()
        call_args = align_client.align.call_args
        assert call_args[0][2] == "English"

    @pytest.mark.asyncio
    async def test_process_forced_alignment_job_logs_resolved_audio_input(
        self, mock_queue, fa_job, align_client, caplog
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
        align_client.align.assert_awaited_once()
        call_args = align_client.align.call_args
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
    async def test_process_forced_alignment_job_alignment_failure(
        self, mock_queue, fa_job, align_client
    ):
        align_client.align = AsyncMock(side_effect=RuntimeError("Model failed"))

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
    async def test_auto_detect_english(self, mock_queue, fa_job, align_client):
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
        call_args = align_client.align.call_args
        assert call_args[0][2] == "English"

    @pytest.mark.asyncio
    async def test_auto_detect_chinese(self, mock_queue, fa_job, align_client):
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
        call_args = align_client.align.call_args
        assert call_args[0][2] == "Chinese"

    @pytest.mark.asyncio
    async def test_explicit_zh_bypasses_auto(self, mock_queue, fa_job, align_client):
        """When language='zh' explicitly, auto-detection is skipped."""
        fa_job.request.options.language = "zh"

        with patch("sow_analysis.workers.queue.validate_audio_duration", return_value=120.0):
            await mock_queue._process_forced_alignment_job(fa_job)

        assert fa_job.status == JobStatus.COMPLETED
        call_args = align_client.align.call_args
        assert call_args[0][2] == "Chinese"

    @pytest.mark.asyncio
    async def test_explicit_en_bypasses_auto(self, mock_queue, fa_job, align_client):
        """When language='en' explicitly, auto-detection is skipped."""
        fa_job.request.options.language = "en"

        with patch("sow_analysis.workers.queue.validate_audio_duration", return_value=120.0):
            await mock_queue._process_forced_alignment_job(fa_job)

        assert fa_job.status == JobStatus.COMPLETED
        call_args = align_client.align.call_args
        assert call_args[0][2] == "English"

    @pytest.mark.asyncio
    async def test_auto_detect_no_resolver_fails(self, fa_job):
        """When language='auto' but resolver is not available, job fails."""
        from sow_analysis.workers.queue import JobQueue

        queue = JobQueue(max_concurrent_analyze=1, cache_dir=Path("/tmp/test_cache"))
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


class TestPickQuietCuts:
    """Test the pure silence-snapping helper."""

    def test_picks_quietest_frame_in_window(self):
        times = [float(value) for value in range(101)]
        rms = [1.0] * 101
        rms[48] = 0.001

        cuts = pick_quiet_cuts(times, rms, [50.0], window=5.0, min_gap=10.0)

        assert cuts == [48.0]

    def test_keeps_min_gap_from_audio_edges(self):
        times = [float(value) for value in range(101)]
        rms = [1.0] * 101
        rms[0] = 0.0  # quietest frame sits exactly on the edge

        cuts = pick_quiet_cuts(times, rms, [0.5], window=5.0, min_gap=60.0)

        assert cuts == [60.0]

    def test_keeps_min_gap_between_cuts(self):
        times = [float(value) for value in range(0, 200)]
        rms = [1.0] * len(times)
        rms[10] = 0.0
        rms[20] = 0.0

        cuts = pick_quiet_cuts(times, rms, [10.0, 20.0], window=2.0, min_gap=60.0)

        assert cuts[0] == 60.0
        assert cuts[1] == 120.0

    def test_degenerate_input_falls_back_to_ideal(self):
        assert pick_quiet_cuts([], [], [100.0, 200.0], window=5.0) == [100.0, 200.0]
        assert pick_quiet_cuts([1.0], [], [100.0], window=5.0) == [100.0]
        assert pick_quiet_cuts([1.0, 2.0], [0.1, 0.2], [], window=5.0) == []


class TestFindChunkBoundaries:
    """Test silence-aware boundary discovery."""

    def test_cuts_land_in_silent_gaps(self, tmp_path):
        audio = _write_tone_wav(
            tmp_path / "song.wav", 60.0, silent_windows=[(18.0, 22.0), (38.0, 42.0)]
        )

        boundaries = find_chunk_boundaries(audio, 60.0, chunk_seconds=30.0, cut_window=10.0)

        assert boundaries[0] == 0.0
        assert boundaries[-1] == 60.0
        assert len(boundaries) == 4  # 3 chunks of ~20 s
        assert 18.0 <= boundaries[1] <= 22.0
        assert 38.0 <= boundaries[2] <= 42.0

    def test_every_chunk_respects_the_model_limit(self, tmp_path):
        audio = _write_tone_wav(tmp_path / "long.wav", 40.0)

        for duration in (301.0, 610.0, 890.0, 900.0):
            boundaries = find_chunk_boundaries(audio, duration, chunk_seconds=300.0, cut_window=30.0)
            lengths = [
                boundaries[index + 1] - boundaries[index]
                for index in range(len(boundaries) - 1)
            ]
            assert max(lengths) <= 300.0, (duration, lengths)

    def test_unreadable_audio_falls_back_to_even_windows(self, tmp_path):
        boundaries = find_chunk_boundaries(
            tmp_path / "missing.wav", 60.0, chunk_seconds=30.0, cut_window=10.0
        )

        assert boundaries == [0.0, 20.0, 40.0, 60.0]

    def test_short_recording_is_one_chunk(self, tmp_path):
        boundaries = find_chunk_boundaries(
            tmp_path / "missing.wav", 120.0, chunk_seconds=300.0, cut_window=30.0
        )

        assert boundaries == [0.0, 120.0]


class TestAssignLinesToChunks:
    """Test lyric-line partitioning across chunks."""

    def test_assigns_by_line_timestamps(self):
        slices = assign_lines_to_chunks(
            ["a", "b", "c", "d"],
            [0.0, 305.0, 610.0],
            610.0,
            line_times=[10.0, 200.0, 400.0, 600.0],
        )

        assert slices == [["a", "b"], ["c", "d"]]

    def test_proportional_fallback_without_line_times(self):
        lines = ["aaaaa", "bbbbb", "ccccc", "ddddd"]

        slices = assign_lines_to_chunks(lines, [0.0, 305.0, 610.0], 610.0)

        # Equal-length lines: centroids 76.25 / 228.75 / 381.25 / 533.75 s.
        assert slices == [["aaaaa", "bbbbb"], ["ccccc", "ddddd"]]

    def test_blank_line_attaches_to_following_line_chunk(self):
        slices = assign_lines_to_chunks(["a", "b", "", "c", "d"], [0.0, 305.0, 610.0], 610.0)

        assert slices == [["a", "b"], ["", "c", "d"]]

    def test_trailing_blank_line_attaches_to_previous_chunk(self):
        # Proportional centroids: "a" -> 152.5 s (chunk 0), "b" -> 457.5 s (chunk 1);
        # the two trailing blanks inherit "b"'s chunk.
        slices = assign_lines_to_chunks(["a", "b", "", ""], [0.0, 305.0, 610.0], 610.0)

        assert slices == [["a"], ["b", "", ""]]

    def test_slices_concatenate_back_to_original_order(self):
        lines = ["a", "b", "", "c", "d", "e", "", "f"]

        slices = assign_lines_to_chunks(
            lines, [0.0, 200.0, 400.0, 610.0], 610.0, line_times=[10, 150, 0, 250, 300, 450, 0, 600]
        )

        assert [line for chunk in slices for line in chunk] == lines

    def test_out_of_order_timestamps_stay_monotonic(self):
        lines = ["a", "b", "c"]

        slices = assign_lines_to_chunks(lines, [0.0, 305.0, 610.0], 610.0, [500.0, 100.0, 550.0])

        assert [line for chunk in slices for line in chunk] == lines
        assert slices[0] == []


class TestExtractChunk:
    """Test audio chunk extraction."""

    def test_extracts_requested_span(self, tmp_path):
        source = _write_tone_wav(tmp_path / "full.wav", 10.0)

        chunk_path = extract_chunk(source, 2.0, 5.0, tmp_path / "chunks" / "chunk_0.wav")

        import soundfile

        info = soundfile.info(str(chunk_path))
        assert chunk_path.exists()
        assert info.samplerate == 16_000
        assert info.channels == 1
        assert info.duration == pytest.approx(3.0, abs=0.05)


class TestForcedAlignmentChunkedPath:
    """Chunked alignment path for recordings beyond the model limit."""

    @pytest.fixture
    def align_client(self, mocker):
        client = MagicMock()
        client.align = AsyncMock(
            side_effect=[
                [(0.0, 2.0, "hello")],
                [(0.0, 2.0, "world")],
            ]
        )
        mocker.patch(
            "sow_analysis.workers.queue.get_modal_forced_aligner_client",
            return_value=client,
        )
        return client

    @pytest.fixture
    def mock_queue(self, align_client):
        from sow_analysis.workers.queue import JobQueue

        queue = JobQueue(max_concurrent_analyze=1, cache_dir=Path("/tmp/test_cache"))
        queue.r2_client = AsyncMock()
        queue.r2_client.bucket = "test-bucket"
        queue.r2_client.download_audio = AsyncMock()
        queue.r2_client.download_lrc_text = AsyncMock(return_value=None)
        queue.r2_client.upload_official_lrc = AsyncMock(return_value="s3://bucket/test/lyrics.lrc")
        queue.r2_client.head_object = AsyncMock(return_value={"ETag": '"abc123"'})
        queue.r2_client.check_exists = AsyncMock(return_value=False)
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
            options=ForcedAlignmentOptions(language="en"),
        )
        return Job(
            id="job_chunked",
            type=JobType.FORCED_ALIGNMENT,
            status=JobStatus.QUEUED,
            request=request,
        )

    @pytest.mark.asyncio
    async def test_long_recording_is_chunked_and_offset_shifted(
        self, mock_queue, fa_job, align_client, mocker, tmp_path
    ):
        mocker.patch(
            "sow_analysis.workers.queue.find_chunk_boundaries",
            return_value=[0.0, 305.0, 610.0],
        )
        mocker.patch(
            "sow_analysis.workers.queue.assign_lines_to_chunks",
            return_value=[["hello"], ["world"]],
        )
        mocker.patch(
            "sow_analysis.workers.queue.extract_chunk",
            side_effect=lambda source, start, end, out_path: out_path,
        )

        captured = {}

        async def _capture_upload(hash_prefix, lrc_path, expected_etag=None):
            captured["content"] = Path(lrc_path).read_text(encoding="utf-8")
            return "s3://bucket/test/lyrics.lrc"

        mock_queue.r2_client.upload_official_lrc = AsyncMock(side_effect=_capture_upload)

        with patch("sow_analysis.workers.queue.validate_audio_duration", return_value=610.0):
            await mock_queue._process_forced_alignment_job(fa_job)

        assert fa_job.status == JobStatus.COMPLETED
        assert fa_job.result is not None
        assert fa_job.result.line_count == 2
        assert align_client.align.await_count == 2
        # Chunk 2's local 0.0 s becomes 305.0 s on the full-recording timeline.
        assert captured["content"].splitlines() == ["[00:00.00] hello", "[05:05.00] world"]

    @pytest.mark.asyncio
    async def test_chunk_failure_is_attributed_to_the_chunk(
        self, mock_queue, fa_job, align_client, mocker
    ):
        align_client.align = AsyncMock(side_effect=RuntimeError("Model failed"))
        mocker.patch(
            "sow_analysis.workers.queue.find_chunk_boundaries",
            return_value=[0.0, 305.0, 610.0],
        )
        mocker.patch(
            "sow_analysis.workers.queue.assign_lines_to_chunks",
            return_value=[["hello"], ["world"]],
        )
        mocker.patch(
            "sow_analysis.workers.queue.extract_chunk",
            side_effect=lambda source, start, end, out_path: out_path,
        )

        with patch("sow_analysis.workers.queue.validate_audio_duration", return_value=610.0):
            await mock_queue._process_forced_alignment_job(fa_job)

        assert fa_job.status == JobStatus.FAILED
        assert "chunk 1/2: Model failed" in fa_job.error_message

    @pytest.mark.asyncio
    async def test_existing_lrc_timings_drive_chunk_assignment(
        self, mock_queue, fa_job, align_client, mocker
    ):
        mock_queue.r2_client.download_lrc_text = AsyncMock(
            return_value="[00:00.10]hello\n[05:00.00]world\n"
        )
        find_mock = mocker.patch(
            "sow_analysis.workers.queue.find_chunk_boundaries",
            return_value=[0.0, 305.0, 610.0],
        )
        assign_mock = mocker.patch(
            "sow_analysis.workers.queue.assign_lines_to_chunks",
            return_value=[["hello"], ["world"]],
        )
        mocker.patch(
            "sow_analysis.workers.queue.extract_chunk",
            side_effect=lambda source, start, end, out_path: out_path,
        )

        with patch("sow_analysis.workers.queue.validate_audio_duration", return_value=610.0):
            await mock_queue._process_forced_alignment_job(fa_job)

        assert fa_job.status == JobStatus.COMPLETED
        find_mock.assert_called_once()
        assert assign_mock.call_args.args[3] == [0.1, 300.0]
