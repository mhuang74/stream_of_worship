"""LRC source-chain behaviour after SOW_FREE_ONLY_MODE was removed.

Free-only mode used to choose between "wait out the cooldown" and "fall back to a
local model". Both local models are gone (Whisper and stem separation run on Modal),
so the chain is now unconditional:

- a rate-limited YouTube fetch falls through to ASR in the same pass (no cooldown
  wait loop), and
- Qwen3 ASR quota exhaustion always waits for the UTC reset once, then retries.

These tests drive ``JobQueue._process_lrc_job`` with a stub queue and stop it at the
stage under test, so they fail if a settings-gated branch comes back.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from sow_analysis import config as config_module
from sow_analysis.config import Settings, settings
from sow_analysis.models import Job, JobStatus, JobType, LrcJobRequest
from sow_analysis.services.qwen3_asr_client import Qwen3AsrQuotaExhaustedError
from sow_analysis.workers import queue as queue_module
from sow_analysis.workers.queue import JobQueue, ResolvedTranscriptionAudio
from sow_analysis.workers.youtube_transcript import YouTubeRateLimitedError


class _Stop(Exception):
    """Sentinel raised to end the pipeline at the stage under test."""


def _job(**overrides) -> Job:
    request = LrcJobRequest(
        audio_url="s3://bucket/abc123/audio.mp3",
        content_hash="abc123" + "0" * 26,
        lyrics_text="主是我力量\n是我盾牌",
        song_title="測試詩歌",
        youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        **overrides,
    )
    return Job(id="job_test", type=JobType.LRC, status=JobStatus.QUEUED, request=request)


def _stub_queue(**attrs) -> JobQueue:
    """JobQueue with only the collaborators the LRC chain touches."""
    q = JobQueue.__new__(JobQueue)
    q.job_store = AsyncMock()
    q.cache_manager = MagicMock()
    q.cache_manager.get_lrc_result.return_value = None
    q.cache_manager.get_qwen3_asr_transcription.return_value = None
    q.cache_manager.get_whisper_transcription.return_value = None
    q.r2_client = None
    q._dashscope_asr_semaphore = None
    q._qwen3_client = None
    q._qwen3_quota_waiter = None
    q._mvsep_quota_waiter = None
    for key, value in attrs.items():
        setattr(q, key, value)
    return q


def _stages(job_store: AsyncMock) -> list[str]:
    return [c.kwargs.get("stage") for c in job_store.update_job.await_args_list]


@pytest.fixture(autouse=True)
def _no_r2(monkeypatch):
    # Keep the pipeline offline: no R2 init, no ETag capture, no audio download.
    monkeypatch.setattr(settings, "SOW_R2_ENDPOINT_URL", "")


async def test_youtube_rate_limit_falls_through_to_asr_in_one_pass(monkeypatch):
    """A rate-limited YouTube fetch goes straight to the ASR stages."""
    youtube = AsyncMock(side_effect=YouTubeRateLimitedError("429 from YouTube"))
    monkeypatch.setattr("sow_analysis.workers.lrc.try_youtube_transcript_lrc", youtube)

    q = _stub_queue()
    resolver = AsyncMock(side_effect=_Stop("reached-asr"))
    q._resolve_lrc_transcription_audio = resolver

    job = _job()
    await q._process_lrc_job(job)

    # One fetch attempt: the old free-mode loop re-fetched until the breaker budget ran out.
    assert youtube.await_count == 1
    assert resolver.await_count == 1
    stages = _stages(q.job_store)
    assert "trying_youtube_transcript" in stages
    assert "downloading" in stages
    assert job.status == JobStatus.FAILED
    assert "reached-asr" in (job.error_message or "")


async def test_qwen3_quota_exhaustion_waits_then_retries(monkeypatch):
    """Quota exhaustion always waits (no flag), retries once, then falls back to Whisper."""
    youtube = AsyncMock(side_effect=YouTubeRateLimitedError("429 from YouTube"))
    monkeypatch.setattr("sow_analysis.workers.lrc.try_youtube_transcript_lrc", youtube)

    qwen = AsyncMock(side_effect=Qwen3AsrQuotaExhaustedError("daily quota exhausted"))
    monkeypatch.setattr(queue_module, "generate_lrc_from_qwen3_asr", qwen)
    whisper = AsyncMock(side_effect=_Stop("whisper-fallback"))
    monkeypatch.setattr(queue_module, "generate_lrc", whisper)

    waiter = MagicMock()
    waiter.mark_exhausted = AsyncMock()
    waiter.wait = AsyncMock(return_value=True)

    q = _stub_queue(_qwen3_quota_waiter=waiter)
    q._resolve_lrc_transcription_audio = AsyncMock(
        return_value=ResolvedTranscriptionAudio(
            path=Path("/tmp/vocals_dry.flac"),
            r2_url="s3://bucket/abc123/stems/vocals_dry.flac",
            stem_kind="vocals_dry",
            is_dry_or_clean_vocals=True,
        )
    )

    job = _job()
    await q._process_lrc_job(job)

    waiter.mark_exhausted.assert_awaited_once()
    waiter.wait.assert_awaited_once()
    # First call hit the quota, the post-wait retry hit it again.
    assert qwen.await_count == 2
    stages = _stages(q.job_store)
    assert stages.count("waiting_for_qwen3_asr_quota_reset") == 1
    assert "falling_back_to_whisper" in stages
    # Exactly one wait cycle, then the ASR fallback — no unbounded waiting.
    assert whisper.await_count == 1
    assert "whisper-fallback" in (job.error_message or "")


def test_free_only_mode_settings_are_gone():
    """The flag and its *_FREE override family are not part of the settings contract.

    pydantic-settings drops unknown keys, so leaving SOW_FREE_ONLY_MODE in an
    operator .env is harmless — which is exactly why the setting must not reappear
    and silently imply the service still honours it.
    """
    fields = set(Settings.model_fields)
    assert "SOW_FREE_ONLY_MODE" not in fields
    assert {name for name in fields if name.endswith("_FREE")} == set()
    assert not hasattr(settings, "SOW_FREE_ONLY_MODE")
    assert config_module.Settings.model_config.get("extra") != "forbid"
