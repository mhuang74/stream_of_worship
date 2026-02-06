"""Export service for sow-app.

Orchestrates audio and video export operations with progress tracking.
Manages the export pipeline from songset to final output files.
"""

import threading
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Callable, Optional

from stream_of_worship.app.db.models import Songset, SongsetItem
from stream_of_worship.app.services.asset_cache import AssetCache
from stream_of_worship.app.services.audio_engine import AudioEngine, ExportResult
from stream_of_worship.app.services.video_engine import VideoEngine, VideoTemplate


class ExportState(Enum):
    """Current export state."""

    IDLE = auto()
    PREPARING = auto()
    DOWNLOADING = auto()
    GENERATING_AUDIO = auto()
    GENERATING_VIDEO = auto()
    FINALIZING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass
class ExportProgress:
    """Export progress information.

    Attributes:
        state: Current export state
        current_step: Current step number
        total_steps: Total number of steps
        step_description: Description of current step
        percent_complete: Overall percentage complete
        error_message: Error message if failed
    """

    state: ExportState
    current_step: int
    total_steps: int
    step_description: str
    percent_complete: float
    error_message: Optional[str] = None


@dataclass
class ExportJob:
    """An export job with configuration and results.

    Attributes:
        id: Unique job ID
        songset: Songset being exported
        items: Items in the songset
        output_audio_path: Path for audio output
        output_video_path: Path for video output
        include_video: Whether to generate video
        video_template: Video template to use
        created_at: When the job was created
        audio_result: Audio export result (populated after export)
    """

    id: str
    songset: Songset
    items: list[SongsetItem]
    output_audio_path: Path
    output_video_path: Path
    include_video: bool
    video_template: VideoTemplate
    created_at: datetime
    audio_result: Optional[ExportResult] = None


class ExportService:
    """Service for exporting songsets to audio/video files.

    Manages the export pipeline with progress tracking and cancellation support.

    Attributes:
        asset_cache: Asset cache for downloading files
        audio_engine: Audio engine for generating audio
        video_engine: Video engine for generating video
        output_dir: Directory for output files
    """

    def __init__(
        self,
        asset_cache: AssetCache,
        audio_engine: AudioEngine,
        video_engine: VideoEngine,
        output_dir: Path,
    ):
        """Initialize the export service.

        Args:
            asset_cache: Asset cache for downloading files
            audio_engine: Audio engine for generating audio
            video_engine: Video engine for generating video
            output_dir: Directory for output files
        """
        self.asset_cache = asset_cache
        self.audio_engine = audio_engine
        self.video_engine = video_engine
        self.output_dir = output_dir

        self._current_job: Optional[ExportJob] = None
        self._state = ExportState.IDLE
        self._cancel_event = threading.Event()
        self._progress_callbacks: list[Callable[[ExportProgress], None]] = []
        self._completion_callbacks: list[Callable[[ExportJob, bool], None]] = []

        self._lock = threading.Lock()

    def register_progress_callback(self, callback: Callable[[ExportProgress], None]) -> None:
        """Register a callback for progress updates.

        Args:
            callback: Function called with progress updates
        """
        self._progress_callbacks.append(callback)

    def register_completion_callback(self, callback: Callable[[ExportJob, bool], None]) -> None:
        """Register a callback for export completion.

        Args:
            callback: Function called when export completes (job, success)
        """
        self._completion_callbacks.append(callback)

    def _notify_progress(self, progress: ExportProgress) -> None:
        """Notify all progress callbacks."""
        for callback in self._progress_callbacks:
            try:
                callback(progress)
            except Exception:
                pass

    def _notify_completion(self, job: ExportJob, success: bool) -> None:
        """Notify all completion callbacks."""
        for callback in self._completion_callbacks:
            try:
                callback(job, success)
            except Exception:
                pass

    def _update_state(
        self,
        state: ExportState,
        step: int,
        total_steps: int,
        description: str,
        error: Optional[str] = None,
    ) -> None:
        """Update export state and notify listeners."""
        percent = (step / total_steps * 100) if total_steps > 0 else 0
        progress = ExportProgress(
            state=state,
            current_step=step,
            total_steps=total_steps,
            step_description=description,
            percent_complete=percent,
            error_message=error,
        )
        self._state = state
        self._notify_progress(progress)

    @property
    def is_exporting(self) -> bool:
        """Check if an export is currently in progress."""
        with self._lock:
            return self._state in (
                ExportState.PREPARING,
                ExportState.DOWNLOADING,
                ExportState.GENERATING_AUDIO,
                ExportState.GENERATING_VIDEO,
                ExportState.FINALIZING,
            )

    @property
    def current_state(self) -> ExportState:
        """Get current export state."""
        with self._lock:
            return self._state

    def cancel(self) -> None:
        """Cancel the current export operation."""
        self._cancel_event.set()
        self._update_state(
            ExportState.CANCELLED,
            0,
            0,
            "Export cancelled",
        )

    def _check_cancelled(self) -> bool:
        """Check if export has been cancelled.

        Returns:
            True if cancelled
        """
        return self._cancel_event.is_set()

    def export(
        self,
        songset: Songset,
        items: list[SongsetItem],
        include_video: bool = True,
        video_template: Optional[VideoTemplate] = None,
    ) -> ExportJob:
        """Export a songset to audio/video files.

        This method runs synchronously. For async operation, run in a thread.

        Args:
            songset: Songset to export
            items: Items in the songset
            include_video: Whether to generate video
            video_template: Video template (defaults to dark)

        Returns:
            ExportJob with results
        """
        job_id = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Sanitize songset name for filename
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in songset.name)

        job = ExportJob(
            id=job_id,
            songset=songset,
            items=items,
            output_audio_path=self.output_dir / f"{safe_name}.mp3",
            output_video_path=self.output_dir / f"{safe_name}.mp4",
            include_video=include_video,
            video_template=video_template or VideoEngine.get_template("dark"),
            created_at=datetime.now(),
        )

        self._current_job = job
        self._cancel_event.clear()

        total_steps = 3 if include_video else 2  # Prepare, Audio, [Video]

        try:
            # Step 1: Preparing
            self._update_state(ExportState.PREPARING, 1, total_steps, "Preparing export...")

            if self._check_cancelled():
                return job

            # Step 2: Download assets and generate audio
            self._update_state(
                ExportState.GENERATING_AUDIO,
                2,
                total_steps,
                "Generating audio...",
            )

            def audio_progress(current: int, total: int) -> None:
                """Audio generation progress callback."""
                sub_progress = current / total if total > 0 else 0
                self._notify_progress(ExportProgress(
                    state=ExportState.GENERATING_AUDIO,
                    current_step=2,
                    total_steps=total_steps,
                    step_description=f"Generating audio... ({current}/{total})",
                    percent_complete=(1 + sub_progress) / total_steps * 100,
                ))

            job.audio_result = self.audio_engine.generate_songset_audio(
                items=items,
                output_path=job.output_audio_path,
                progress_callback=audio_progress,
            )

            if self._check_cancelled():
                return job

            # Step 3: Generate video (if requested)
            if include_video:
                self._update_state(
                    ExportState.GENERATING_VIDEO,
                    3,
                    total_steps,
                    "Generating lyrics video...",
                )

                def video_progress(current: int, total: int) -> None:
                    """Video generation progress callback."""
                    sub_progress = current / total if total > 0 else 0
                    self._notify_progress(ExportProgress(
                        state=ExportState.GENERATING_VIDEO,
                        current_step=3,
                        total_steps=total_steps,
                        step_description=f"Generating video... ({current}/{total} frames)",
                        percent_complete=(2 + sub_progress) / total_steps * 100,
                    ))

                self.video_engine.generate_lyrics_video(
                    audio_result=job.audio_result,
                    items=items,
                    output_path=job.output_video_path,
                    progress_callback=video_progress,
                )

            # Complete
            self._update_state(
                ExportState.COMPLETED,
                total_steps,
                total_steps,
                "Export complete!",
            )
            self._notify_completion(job, True)

        except Exception as e:
            error_msg = str(e)
            self._update_state(
                ExportState.FAILED,
                0,
                total_steps,
                f"Export failed: {error_msg}",
                error=error_msg,
            )
            self._notify_completion(job, False)

        return job

    def export_async(
        self,
        songset: Songset,
        items: list[SongsetItem],
        include_video: bool = True,
        video_template: Optional[VideoTemplate] = None,
    ) -> threading.Thread:
        """Start an export operation in a background thread.

        Args:
            songset: Songset to export
            items: Items in the songset
            include_video: Whether to generate video
            video_template: Video template (defaults to dark)

        Returns:
            Thread running the export
        """
        def run_export():
            self.export(songset, items, include_video, video_template)

        thread = threading.Thread(target=run_export, daemon=True)
        thread.start()
        return thread
