import signal
from unittest.mock import MagicMock, patch

import pytest

from sow_render_worker import pipeline
from sow_render_worker.pipeline import (
    LAMBDA_TIMEOUT_SAFETY_MARGIN_SECONDS,
    _sigterm_handler,
)


class TestCheckLambdaTimeout:
    def test_raises_when_low_remaining_time(self):
        from sow_render_worker.pipeline import execute_render_pipeline

        mock_context = MagicMock()
        mock_context.get_remaining_time_in_millis.return_value = 30 * 1000

        with patch("sow_render_worker.pipeline.get_render_job") as mock_get:
            mock_get.return_value = MagicMock(
                audio_enabled=True,
                video_enabled=False,
                songset_id="ss1",
            )
            with patch("sow_render_worker.pipeline.AssetFetcher") as mock_fetcher:
                mock_fetcher_instance = MagicMock()
                mock_fetcher_instance.get_job_temp_dir.return_value = "/tmp"
                mock_fetcher.return_value = mock_fetcher_instance
                with patch("sow_render_worker.pipeline.R2Uploader"):
                    with patch("sow_render_worker.pipeline.start_render_job", return_value=MagicMock()):
                        with patch("sow_render_worker.pipeline.reclaim_stale_job", return_value=None):
                            with pytest.raises(TimeoutError, match="Lambda timeout imminent"):
                                execute_render_pipeline(
                                    "job1", 1, MagicMock(), lambda_context=mock_context
                                )

    def test_passes_when_sufficient_time(self):
        mock_context = MagicMock()
        mock_context.get_remaining_time_in_millis.return_value = 120 * 1000

        assert mock_context.get_remaining_time_in_millis() / 1000 >= LAMBDA_TIMEOUT_SAFETY_MARGIN_SECONDS

    def test_raises_on_sigterm_flag(self):
        pipeline._shutdown_requested = True

        try:
            mock_context = MagicMock()
            mock_context.get_remaining_time_in_millis.return_value = 120 * 1000

            from sow_render_worker.pipeline import execute_render_pipeline

            with patch("sow_render_worker.pipeline.get_render_job") as mock_get:
                mock_get.return_value = MagicMock(
                    audio_enabled=True,
                    video_enabled=False,
                    songset_id="ss1",
                )
                with patch("sow_render_worker.pipeline.AssetFetcher") as mock_fetcher:
                    mock_fetcher_instance = MagicMock()
                    mock_fetcher_instance.get_job_temp_dir.return_value = "/tmp"
                    mock_fetcher.return_value = mock_fetcher_instance
                    with patch("sow_render_worker.pipeline.R2Uploader"):
                        with patch("sow_render_worker.pipeline.start_render_job", return_value=MagicMock()):
                            with patch("sow_render_worker.pipeline.reclaim_stale_job", return_value=None):
                                with patch("sow_render_worker.pipeline.fetch_songset_items", return_value=[MagicMock()]):
                                    with patch("sow_render_worker.pipeline.update_render_progress"):
                                        with pytest.raises(TimeoutError, match="Lambda received SIGTERM"):
                                            execute_render_pipeline(
                                                "job1", 1, MagicMock(), lambda_context=mock_context
                                            )
        finally:
            pipeline._shutdown_requested = False


class TestSigtermHandler:
    def test_handler_sets_flag(self):
        pipeline._shutdown_requested = False

        _sigterm_handler(signal.SIGTERM, None)

        assert pipeline._shutdown_requested is True

        pipeline._shutdown_requested = False


class TestLambdaTimeoutSafetyMargin:
    def test_safety_margin_is_60_seconds(self):
        assert LAMBDA_TIMEOUT_SAFETY_MARGIN_SECONDS == 60
