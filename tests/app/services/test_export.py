"""Tests for ExportService.

Tests export orchestration and state management.
"""

import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest

from stream_of_worship.app.services.export import (
    ExportService,
    ExportState,
    ExportProgress,
    ExportJob,
)
from stream_of_worship.app.services.audio_engine import ExportResult
from stream_of_worship.app.db.models import Songset, SongsetItem


@pytest.fixture
def mock_asset_cache():
    """Mocked AssetCache."""
    return MagicMock()


@pytest.fixture
def mock_audio_engine():
    """Mocked AudioEngine."""
    engine = MagicMock()
    engine.generate_songset_audio = Mock(return_value=ExportResult(
        output_path=Path("/tmp/audio.mp3"),
        total_duration_seconds=300.0,
        segments=[],
    ))
    return engine


@pytest.fixture
def mock_video_engine():
    """Mocked VideoEngine."""
    engine = MagicMock()
    engine.generate_lyrics_video = Mock(return_value=Path("/tmp/video.mp4"))
    return engine


@pytest.fixture
def export_service(tmp_path, mock_asset_cache, mock_audio_engine, mock_video_engine):
    """ExportService with mocked dependencies."""
    return ExportService(
        asset_cache=mock_asset_cache,
        audio_engine=mock_audio_engine,
        video_engine=mock_video_engine,
        output_dir=tmp_path,
    )


@pytest.fixture
def sample_songset():
    """Sample songset for testing."""
    return Songset(
        id="songset_0001",
        name="Test Songset",
        description="A test songset",
        created_at="2024-01-01T00:00:00",
        updated_at="2024-01-01T00:00:00",
    )


@pytest.fixture
def sample_items():
    """Sample songset items for testing."""
    return [
        SongsetItem(
            id="item_0001",
            songset_id="songset_0001",
            song_id="song_0001",
            recording_hash_prefix="abc123def456",
            position=0,
        ),
    ]


class TestExportState:
    """Tests for ExportState enum."""

    def test_export_state_enum_values(self):
        """Verify ExportState members."""
        assert ExportState.IDLE is not None
        assert ExportState.PREPARING is not None
        assert ExportState.DOWNLOADING is not None
        assert ExportState.GENERATING_AUDIO is not None
        assert ExportState.GENERATING_VIDEO is not None
        assert ExportState.FINALIZING is not None
        assert ExportState.COMPLETED is not None
        assert ExportState.FAILED is not None
        assert ExportState.CANCELLED is not None


class TestExportProgress:
    """Tests for ExportProgress dataclass."""

    def test_export_progress_dataclass(self):
        """Verify ExportProgress creation."""
        progress = ExportProgress(
            state=ExportState.GENERATING_AUDIO,
            current_step=2,
            total_steps=3,
            step_description="Generating audio...",
            percent_complete=66.7,
        )

        assert progress.state == ExportState.GENERATING_AUDIO
        assert progress.current_step == 2
        assert progress.total_steps == 3
        assert progress.step_description == "Generating audio..."
        assert abs(progress.percent_complete - 66.7) < 0.1
        assert progress.error_message is None


class TestExportJob:
    """Tests for ExportJob dataclass."""

    def test_export_job_dataclass(self, sample_songset, sample_items):
        """Verify ExportJob creation."""
        from stream_of_worship.app.services.video_engine import VideoTemplate

        job = ExportJob(
            id="export_20240101_120000",
            songset=sample_songset,
            items=sample_items,
            output_audio_path=Path("/output/audio.mp3"),
            output_video_path=Path("/output/video.mp4"),
            include_video=True,
            video_template=VideoTemplate(
                name="dark",
                background_color=(0, 0, 0),
                text_color=(255, 255, 255),
                highlight_color=(255, 255, 0),
                font_size=48,
                resolution=(1920, 1080),
            ),
            created_at=datetime.now(),
        )

        assert job.id == "export_20240101_120000"
        assert job.songset == sample_songset
        assert job.items == sample_items
        assert job.output_audio_path == Path("/output/audio.mp3")
        assert job.output_video_path == Path("/output/video.mp4")
        assert job.include_video is True
        assert job.audio_result is None


class TestExportStateTransitions:
    """Tests for export state transitions."""

    def test_export_transitions_through_states(self, export_service, sample_songset, sample_items):
        """Verify IDLE -> EXPORTING -> COMPLETED."""
        states = []

        def on_progress(progress):
            states.append(progress.state)

        export_service.register_progress_callback(on_progress)

        export_service.export(sample_songset, sample_items)

        # Should have progressed through multiple states
        assert ExportState.PREPARING in states
        assert ExportState.GENERATING_AUDIO in states
        assert ExportState.COMPLETED in states

    def test_initial_state_is_idle(self, export_service):
        """Verify initial state is IDLE."""
        assert export_service.current_state == ExportState.IDLE
        assert export_service.is_exporting is False


class TestCallbackRegistration:
    """Tests for callback registration."""

    def test_progress_callback_invoked(self, export_service, sample_songset, sample_items):
        """Verify progress updates sent."""
        progress_calls = []

        def on_progress(progress):
            progress_calls.append(progress)

        export_service.register_progress_callback(on_progress)
        export_service.export(sample_songset, sample_items)

        assert len(progress_calls) > 0
        assert all(isinstance(p, ExportProgress) for p in progress_calls)

    def test_completion_callback_invoked(self, export_service, sample_songset, sample_items):
        """Verify completion notification."""
        completion_calls = []

        def on_completion(job, success):
            completion_calls.append((job, success))

        export_service.register_completion_callback(on_completion)
        export_service.export(sample_songset, sample_items)

        assert len(completion_calls) == 1
        assert completion_calls[0][1] is True  # success

    def test_multiple_progress_callbacks(self, export_service, sample_songset, sample_items):
        """Verify multiple callbacks can be registered."""
        calls1 = []
        calls2 = []

        export_service.register_progress_callback(lambda p: calls1.append(p))
        export_service.register_progress_callback(lambda p: calls2.append(p))

        export_service.export(sample_songset, sample_items)

        assert len(calls1) > 0
        assert len(calls2) > 0


class TestCancel:
    """Tests for cancellation."""

    def test_cancel_sets_cancelled_state(self, export_service):
        """Verify cancellation sets state."""
        export_service.cancel()

        assert export_service.current_state == ExportState.CANCELLED

    def test_cancel_stops_export_midway(self, export_service, sample_songset, sample_items, mock_audio_engine):
        """Verify early termination."""
        # Make audio generation take a while
        def slow_generate(*args, **kwargs):
            time.sleep(0.1)
            return ExportResult(
                output_path=Path("/tmp/audio.mp3"),
                total_duration_seconds=300.0,
                segments=[],
            )

        mock_audio_engine.generate_songset_audio.side_effect = slow_generate

        # Start export in a thread
        thread = export_service.export_async(sample_songset, sample_items)

        # Immediately cancel
        export_service.cancel()

        # Wait for thread to finish
        thread.join(timeout=1.0)

        assert export_service.current_state == ExportState.CANCELLED


class TestExportAsync:
    """Tests for async export."""

    def test_export_async_runs_in_thread(self, export_service, sample_songset, sample_items):
        """Verify threading used."""
        import threading

        thread = export_service.export_async(sample_songset, sample_items)

        assert isinstance(thread, threading.Thread)
        assert thread.is_alive() or thread.ident is not None

        # Wait for completion
        thread.join(timeout=5.0)


class TestExportResult:
    """Tests for export result handling."""

    def test_export_result_contains_paths(self, export_service, sample_songset, sample_items):
        """Verify audio/video paths in result."""
        job = export_service.export(sample_songset, sample_items)

        assert job.output_audio_path is not None
        assert job.output_video_path is not None
        assert "Test_Songset" in str(job.output_audio_path) or "Test" in str(job.output_audio_path)

    def test_export_result_audio_result_populated(self, export_service, sample_songset, sample_items, mock_audio_engine):
        """Verify audio_result populated after export."""
        audio_result = ExportResult(
            output_path=Path("/tmp/audio.mp3"),
            total_duration_seconds=300.0,
            segments=[],
        )
        mock_audio_engine.generate_songset_audio.return_value = audio_result

        job = export_service.export(sample_songset, sample_items)

        assert job.audio_result is not None
        assert job.audio_result == audio_result


class TestExportWithoutVideo:
    """Tests for audio-only export."""

    def test_export_audio_only_skips_video(self, export_service, sample_songset, sample_items, mock_video_engine):
        """Verify video generation skipped when include_video=False."""
        export_service.export(sample_songset, sample_items, include_video=False)

        mock_video_engine.generate_lyrics_video.assert_not_called()

    def test_export_audio_only_has_correct_steps(self, export_service, sample_songset, sample_items):
        """Verify only 2 steps for audio-only export."""
        states = []

        export_service.register_progress_callback(lambda p: states.append(p.state))
        export_service.export(sample_songset, sample_items, include_video=False)

        # Should not have GENERATING_VIDEO state
        assert ExportState.GENERATING_VIDEO not in states


class TestExportFailure:
    """Tests for export failure handling."""

    def test_export_failure_sets_failed_state(self, export_service, sample_songset, sample_items, mock_audio_engine):
        """Verify FAILED state on error."""
        mock_audio_engine.generate_songset_audio.side_effect = Exception("Audio generation failed")

        job = export_service.export(sample_songset, sample_items)

        assert export_service.current_state == ExportState.FAILED
        assert job.audio_result is None

    def test_export_failure_invokes_completion_callback(self, export_service, sample_songset, sample_items, mock_audio_engine):
        """Verify completion callback called with success=False."""
        mock_audio_engine.generate_songset_audio.side_effect = Exception("Audio generation failed")

        completion_calls = []
        export_service.register_completion_callback(lambda job, success: completion_calls.append((job, success)))

        export_service.export(sample_songset, sample_items)

        assert len(completion_calls) == 1
        assert completion_calls[0][1] is False


class TestFilenameSanitization:
    """Tests for output filename sanitization."""

    def test_special_characters_sanitized(self, export_service):
        """Verify special chars replaced with underscore."""
        songset = Songset(
            id="songset_0001",
            name="Test/Songset: With*Special?Chars",
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )

        job = export_service.export(songset, [])

        # Filename should not contain special characters
        filename = str(job.output_audio_path.name)
        assert "/" not in filename
        assert ":" not in filename
        assert "*" not in filename
        assert "?" not in filename
