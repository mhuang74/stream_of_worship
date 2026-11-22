import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest

from sow_render_worker import glue_jobs
from sow_render_worker.db import GlueJob
from sow_render_worker.glue_descriptor import build_descriptor
from sow_render_worker.glue_excerpts import GlueExcerptTooShort, PreparedExcerpt

JOB_ID = "11111111-2222-3333-4444-555555555555"
USER_ID = 7
SONGSET_ID = "ss_001"
UPDATED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
A_HASH = "a" * 64
B_HASH = "b" * 64

_DB_HELPERS = (
    "claim_glue_job",
    "get_glue_job",
    "set_glue_phase",
    "heartbeat_glue_job",
    "record_provider_contact",
    "finish_glue_job",
    "read_songset_items_for_glue",
    "prepare_excerpt",
)


def _items() -> list[dict]:
    common = {
        "songset_id": SONGSET_ID,
        "gap_beats": 2,
        "crossfade_enabled": False,
        "crossfade_duration_seconds": None,
        "key_shift_semitones": 0,
        "tempo_ratio": 1.0,
        "arrangement_steps": None,
        "glue_enabled": True,
        "glue_beats": 4,
        "glue_active_take_id": None,
        "updated_at": UPDATED_AT,
        "sections": None,
        "beats": None,
        "duration_seconds": 180.0,
        "deleted_at": None,
    }
    return [
        {
            **common,
            "id": "item-a",
            "song_id": "song-a",
            "recording_hash_prefix": "rec-a",
            "position": 0,
            "tempo_bpm": 80.0,
            "recording_content_hash": A_HASH,
            "song_title": "Song A",
        },
        {
            **common,
            "id": "item-b",
            "song_id": "song-b",
            "recording_hash_prefix": "rec-b",
            "position": 1,
            "tempo_bpm": 96.0,
            "recording_content_hash": B_HASH,
            "song_title": "Song B",
        },
    ]


def _expected_descriptor() -> tuple[dict, str, str]:
    return build_descriptor(
        beats=4,
        to_tempo_bpm=96.0,
        from_side={"contentHash": A_HASH, "steps": None},
        to_side={"contentHash": B_HASH, "steps": None},
    )


def _make_job(**overrides) -> GlueJob:
    _, canonical, key = _expected_descriptor()
    values = {
        "id": JOB_ID,
        "user_id": USER_ID,
        "songset_id": SONGSET_ID,
        "from_item_id": "item-a",
        "to_item_id": "item-b",
        "client_request_id": "33333333-4444-5555-6666-777777777777",
        "take_mode": "initial",
        "descriptor_key": key,
        "descriptor_json": canonical,
        "phase": "preparing",
        "active": True,
        "dispatch_state": "dispatched",
        "attempt_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "pending_take_id": "ffffffff-0000-1111-2222-333333333333",
        "take_ordinal": 1,
        "seed": 123456789,
        "expected_from_updated_at": UPDATED_AT,
        "expected_to_updated_at": UPDATED_AT,
        "paid_reservation_id": "99999999-8888-7777-6666-555555555555",
        "paid_reservation_state": "reserved",
        "provider_contacted": False,
    }
    values.update(overrides)
    return GlueJob(**values)


def _prepared(**kwargs) -> PreparedExcerpt:
    return PreparedExcerpt(
        side=kwargs["side"],
        seconds=12.0,
        logical_key=f"key-{kwargs['side']}",
        content_hash=kwargs["content_hash"],
        canonical_steps_json=None,
        wav_path=Path(f"/tmp/excerpt-{kwargs['side']}.wav"),
        audio_sha256="f" * 64,
        elevenlabs_song_id=None,
    )


class _Context:
    def __init__(self, remaining_ms: int) -> None:
        self._remaining_ms = remaining_ms

    def get_remaining_time_in_millis(self) -> int:
        return self._remaining_ms


class _FakeProviderStage:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, job, conn, *, a_excerpt, b_excerpt, work_dir, log_event) -> None:
        self.calls.append(
            {
                "job": job,
                "conn": conn,
                "a_excerpt": a_excerpt,
                "b_excerpt": b_excerpt,
                "work_dir": work_dir,
                "log_event": log_event,
            }
        )


def _make_conn() -> tuple[MagicMock, MagicMock]:
    conn = MagicMock()
    conn.autocommit = True
    cursor = MagicMock()
    cursor.rowcount = 1
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cursor)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn, cursor


@pytest.fixture
def worker(monkeypatch):
    conn, cursor = _make_conn()
    mocks = {name: MagicMock(name=name) for name in _DB_HELPERS}
    for name, mock in mocks.items():
        monkeypatch.setattr(glue_jobs, name, mock)

    job = _make_job()
    mocks["claim_glue_job"].return_value = (job, True)
    mocks["get_glue_job"].return_value = job
    mocks["set_glue_phase"].return_value = job
    mocks["heartbeat_glue_job"].return_value = True
    mocks["record_provider_contact"].return_value = True
    mocks["read_songset_items_for_glue"].return_value = _items()
    mocks["prepare_excerpt"].side_effect = _prepared
    mocks["finish_glue_job"].return_value = job

    sleeps: list[float] = []
    monkeypatch.setattr(glue_jobs, "_sleep", sleeps.append)
    fetcher = MagicMock(name="asset_fetcher")
    return SimpleNamespace(
        conn=conn,
        cursor=cursor,
        mocks=mocks,
        job=job,
        sleeps=sleeps,
        fetcher=fetcher,
    )


def _run(worker, stage=None, *, context=None, mode="generate") -> None:
    glue_jobs.execute_glue_job(
        JOB_ID,
        USER_ID,
        worker.conn,
        lambda_context=context,
        mode=mode,
        provider_stage=stage,
        asset_fetcher=worker.fetcher,
    )


def _finish_kwargs(worker) -> dict:
    finish = worker.mocks["finish_glue_job"]
    assert finish.call_count == 1, finish.call_args_list
    return finish.call_args.kwargs


def _executed_sql(cursor) -> str:
    return " ".join(str(c.args[0]) for c in cursor.execute.call_args_list)


def test_unknown_job_returns_without_work(worker):
    worker.mocks["claim_glue_job"].return_value = (None, False)
    _run(worker, _FakeProviderStage())
    worker.mocks["finish_glue_job"].assert_not_called()
    worker.mocks["read_songset_items_for_glue"].assert_not_called()


def test_duplicate_delivery_after_terminal_does_nothing(worker):
    job = _make_job(phase="ready", active=False, provider_contacted=True)
    worker.mocks["claim_glue_job"].return_value = (job, False)
    worker.mocks["get_glue_job"].return_value = job
    stage = _FakeProviderStage()

    _run(worker, stage)

    worker.mocks["finish_glue_job"].assert_not_called()
    worker.mocks["set_glue_phase"].assert_not_called()
    worker.mocks["heartbeat_glue_job"].assert_not_called()
    worker.mocks["read_songset_items_for_glue"].assert_not_called()
    worker.mocks["prepare_excerpt"].assert_not_called()
    worker.mocks["record_provider_contact"].assert_not_called()
    assert stage.calls == []


def test_duplicate_delivery_during_composing_is_unknown_paid(worker):
    job = _make_job(
        phase="composing",
        provider_contacted=True,
        paid_reservation_state="consumed",
    )
    worker.mocks["claim_glue_job"].return_value = (job, False)
    worker.mocks["get_glue_job"].return_value = job
    stage = _FakeProviderStage()

    _run(worker, stage)

    kwargs = _finish_kwargs(worker)
    assert kwargs["phase"] == "failed"
    assert kwargs["error_code"] == glue_jobs.GLUE_PROVIDER_UNKNOWN
    assert kwargs["retry_kind"] == "paid"
    assert stage.calls == []
    worker.mocks["record_provider_contact"].assert_not_called()
    assert "glue_paid_attempts" not in _executed_sql(worker.cursor)


def test_stale_reclaim_of_uploading_phase_is_unknown_paid(worker):
    job = _make_job(phase="uploading", provider_contacted=True)
    worker.mocks["claim_glue_job"].return_value = (job, True)
    worker.mocks["get_glue_job"].return_value = job
    stage = _FakeProviderStage()

    _run(worker, stage)

    kwargs = _finish_kwargs(worker)
    assert kwargs["error_code"] == glue_jobs.GLUE_PROVIDER_UNKNOWN
    assert kwargs["retry_kind"] == "paid"
    assert stage.calls == []


def test_in_flight_phase_with_durable_response_is_left_for_staging(worker):
    job = _make_job(
        phase="composing",
        provider_contacted=True,
        paid_reservation_state="consumed",
        raw_response_r2_key="glues/7/jobs/x/response.pcm",
        raw_response_sha256="e" * 64,
        raw_response_bytes=1234,
    )
    worker.mocks["claim_glue_job"].return_value = (job, False)
    worker.mocks["get_glue_job"].return_value = job
    stage = _FakeProviderStage()

    _run(worker, stage)

    worker.mocks["finish_glue_job"].assert_not_called()
    assert stage.calls == []


def test_descriptor_mismatch_finishes_cancelled_without_provider(worker):
    job = _make_job(descriptor_key="0" * 64, descriptor_json="{}")
    worker.mocks["claim_glue_job"].return_value = (job, True)
    worker.mocks["get_glue_job"].return_value = job
    stage = _FakeProviderStage()

    _run(worker, stage)

    kwargs = _finish_kwargs(worker)
    assert kwargs["phase"] == "cancelled"
    assert kwargs["error_code"] == glue_jobs.GLUE_INPUTS_CHANGED
    assert kwargs["retry_kind"] == "none"
    assert stage.calls == []
    worker.mocks["prepare_excerpt"].assert_not_called()
    worker.mocks["record_provider_contact"].assert_not_called()
    assert "glue_paid_attempts" in _executed_sql(worker.cursor)


def test_item_timestamp_change_is_stale(worker):
    items = _items()
    items[1]["updated_at"] = datetime(2026, 5, 5, tzinfo=timezone.utc)
    worker.mocks["read_songset_items_for_glue"].return_value = items
    stage = _FakeProviderStage()

    _run(worker, stage)

    kwargs = _finish_kwargs(worker)
    assert kwargs["phase"] == "cancelled"
    assert kwargs["error_code"] == glue_jobs.GLUE_INPUTS_CHANGED
    assert stage.calls == []


def test_excerpt_too_short_fails_without_release_after_contact(worker):
    def fake_prepare(**kwargs):
        if kwargs["side"] == "A":
            raise GlueExcerptTooShort("under one second", side="A")
        return _prepared(**kwargs)

    worker.mocks["prepare_excerpt"].side_effect = fake_prepare
    stage = _FakeProviderStage()

    _run(worker, stage)

    kwargs = _finish_kwargs(worker)
    assert kwargs["phase"] == "failed"
    assert kwargs["error_code"] == "GLUE_EXCERPT_TOO_SHORT"
    assert kwargs["retry_kind"] == "none"
    assert stage.calls == []
    worker.mocks["record_provider_contact"].assert_not_called()
    assert "glue_paid_attempts" in _executed_sql(worker.cursor)


def test_cancellation_wins_pre_contact_cas(worker):
    worker.mocks["record_provider_contact"].return_value = False
    stage = _FakeProviderStage()

    _run(worker, stage)

    kwargs = _finish_kwargs(worker)
    assert kwargs["phase"] == "cancelled"
    assert kwargs["error_code"] is None
    assert kwargs["retry_kind"] is None
    assert stage.calls == []


def test_deadline_gate_refuses_to_start(worker):
    stage = _FakeProviderStage()

    _run(worker, stage, context=_Context(900_000))

    kwargs = _finish_kwargs(worker)
    assert kwargs["phase"] == "failed"
    assert kwargs["error_code"] == glue_jobs.GLUE_EXECUTION_DEADLINE
    assert kwargs["retry_kind"] == "free"
    assert stage.calls == []
    worker.mocks["record_provider_contact"].assert_not_called()
    assert "glue_paid_attempts" in _executed_sql(worker.cursor)


def test_deadline_gate_skipped_without_lambda_context(worker):
    stage = _FakeProviderStage()

    _run(worker, stage, context=None)

    assert len(stage.calls) == 1
    worker.mocks["finish_glue_job"].assert_not_called()


def test_happy_path_orders_contact_before_provider_and_heartbeats(worker):
    stage = _FakeProviderStage()

    _run(worker, stage, context=_Context(1_900_000))

    assert len(stage.calls) == 1
    call_args = stage.calls[0]
    assert call_args["job"].id == JOB_ID
    assert call_args["a_excerpt"].side == "A"
    assert call_args["b_excerpt"].side == "B"
    assert isinstance(call_args["work_dir"], Path)
    assert call_args["work_dir"].exists() is False  # cleaned up in finally
    assert callable(call_args["log_event"])
    worker.mocks["record_provider_contact"].assert_called_once_with(
        worker.conn, JOB_ID, USER_ID, "upload-a"
    )
    sides = [c.kwargs["side"] for c in worker.mocks["prepare_excerpt"].call_args_list]
    assert sides == ["A", "B"]
    assert worker.mocks["heartbeat_glue_job"].call_count >= 6
    worker.mocks["finish_glue_job"].assert_not_called()


def test_missing_provider_key_raises_and_stays_pre_contact(worker, monkeypatch):
    monkeypatch.delenv("SOW_ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr(glue_jobs, "_load_provider_stage", lambda: (lambda *a, **k: None))

    with pytest.raises(glue_jobs.GlueConfigurationError) as exc_info:
        _run(worker, None)

    assert exc_info.value.code == "GLUE_NOT_CONFIGURED"
    worker.mocks["finish_glue_job"].assert_not_called()
    worker.mocks["record_provider_contact"].assert_not_called()
    worker.mocks["prepare_excerpt"].assert_not_called()
    assert worker.job.provider_contacted is False


def test_missing_provider_module_raises_configuration_error(worker, monkeypatch):
    monkeypatch.setitem(sys.modules, "sow_render_worker.glue_provider", None)

    with pytest.raises(glue_jobs.GlueConfigurationError):
        _run(worker, None)

    worker.mocks["record_provider_contact"].assert_not_called()


def test_injected_provider_stage_needs_no_key(worker, monkeypatch):
    monkeypatch.delenv("SOW_ELEVENLABS_API_KEY", raising=False)
    stage = _FakeProviderStage()

    _run(worker, stage)

    assert len(stage.calls) == 1


def test_staging_mode_without_raw_metadata_fails_paid(worker):
    job = _make_job(phase="staging", provider_contacted=True)
    worker.mocks["claim_glue_job"].return_value = (job, True)
    worker.mocks["get_glue_job"].return_value = job
    stage = _FakeProviderStage()

    _run(worker, stage, mode="staging")

    kwargs = _finish_kwargs(worker)
    assert kwargs["phase"] == "failed"
    assert kwargs["error_code"] == glue_jobs.GLUE_PROVIDER_UNKNOWN
    assert kwargs["retry_kind"] == "paid"
    assert stage.calls == []
    worker.mocks["set_glue_phase"].assert_not_called()


def test_staging_mode_runs_staging_stage_and_never_provider(worker, monkeypatch):
    job = _make_job(
        phase="storing-response",
        provider_contacted=True,
        paid_reservation_state="consumed",
        raw_response_r2_key="glues/7/jobs/x/response.pcm",
        raw_response_sha256="e" * 64,
        raw_response_bytes=1234,
    )
    worker.mocks["claim_glue_job"].return_value = (job, True)
    worker.mocks["get_glue_job"].return_value = job
    staging_calls = []

    def fake_staging(job_arg, conn_arg, *, work_dir, log_event):
        staging_calls.append({"job": job_arg, "work_dir": work_dir})

    monkeypatch.setattr(glue_jobs, "_load_staging_stage", lambda: fake_staging)
    stage = _FakeProviderStage()

    _run(worker, stage, mode="staging")

    assert len(staging_calls) == 1
    assert staging_calls[0]["job"].id == JOB_ID
    assert stage.calls == []
    worker.mocks["record_provider_contact"].assert_not_called()
    worker.mocks["set_glue_phase"].assert_called_with(worker.conn, JOB_ID, USER_ID, "staging")
    worker.mocks["finish_glue_job"].assert_not_called()


def test_staging_mode_without_staging_stage_fails_free(worker, monkeypatch):
    job = _make_job(
        phase="staging",
        provider_contacted=True,
        raw_response_r2_key="glues/7/jobs/x/response.pcm",
        raw_response_sha256="e" * 64,
        raw_response_bytes=1234,
    )
    worker.mocks["claim_glue_job"].return_value = (job, True)
    worker.mocks["get_glue_job"].return_value = job

    def missing_staging():
        raise ImportError("no staging module")

    monkeypatch.setattr(glue_jobs, "_load_staging_stage", missing_staging)

    _run(worker, _FakeProviderStage(), mode="staging")

    kwargs = _finish_kwargs(worker)
    assert kwargs["phase"] == "failed"
    assert kwargs["error_code"] == glue_jobs.GLUE_WORKER_FAILED
    assert kwargs["retry_kind"] == "free"


def test_unknown_mode_is_rejected(worker):
    with pytest.raises(ValueError, match="mode"):
        _run(worker, _FakeProviderStage(), mode="resume")


def test_retry_logs_attempt_and_jitter_bounds(worker, monkeypatch, caplog):
    attempts = {"count": 0}

    def flaky_read(conn, songset_id):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ConnectionError("transient")
        return _items()

    worker.mocks["read_songset_items_for_glue"].side_effect = flaky_read
    jitter = MagicMock(return_value=0.25)
    monkeypatch.setattr(glue_jobs.random, "uniform", jitter)

    with caplog.at_level(logging.INFO, logger="sow_render_worker.glue_jobs"):
        _run(worker, _FakeProviderStage())

    assert attempts["count"] == 2
    jitter.assert_called_once_with(0, 1.0)
    assert worker.sleeps == [0.25]
    retry_lines = [r for r in caplog.records if "glue.retry" in r.getMessage()]
    assert len(retry_lines) == 1
    assert '"retryCount": 1' in retry_lines[0].getMessage()
    worker.mocks["finish_glue_job"].assert_not_called()


def test_free_retries_give_up_after_three_jittered_delays(worker, monkeypatch, caplog):
    worker.mocks["read_songset_items_for_glue"].side_effect = ConnectionError("down")
    jitter = MagicMock(return_value=0.5)
    monkeypatch.setattr(glue_jobs.random, "uniform", jitter)

    with caplog.at_level(logging.INFO, logger="sow_render_worker.glue_jobs"):
        _run(worker, _FakeProviderStage())

    assert worker.mocks["read_songset_items_for_glue"].call_count == 4
    assert jitter.call_args_list == [call(0, 1.0), call(0, 2.0), call(0, 4.0)]
    assert worker.sleeps == [0.5, 0.5, 0.5]
    kwargs = _finish_kwargs(worker)
    assert kwargs["error_code"] == glue_jobs.GLUE_WORKER_FAILED
    assert kwargs["retry_kind"] == "free"
    retry_messages = [r.getMessage() for r in caplog.records if "glue.retry" in r.getMessage()]
    assert len(retry_messages) == 3
    assert [f'"retryCount": {n}' in " ".join(retry_messages) for n in (1, 2, 3)] == [
        True,
        True,
        True,
    ]


def test_release_unconsumed_reservation_is_guarded(worker):
    job = _make_job()

    released = glue_jobs._release_unconsumed_reservation(worker.conn, job)

    assert released is True
    sql = _executed_sql(worker.cursor)
    assert "glue_paid_attempts" in sql
    assert "state = %s" in sql
    assert "paid_reservation_state = %s" in sql
    all_params = [c.args[1] for c in worker.cursor.execute.call_args_list]
    assert all("reserved" in params for params in all_params)
    assert all("released" in params for params in all_params)
    worker.conn.commit.assert_called_once()
    assert worker.conn.autocommit is True


def test_sanitized_detail_strips_paths_and_urls():
    detail = glue_jobs._sanitize_detail(
        "GET https://api.elevenlabs.io/v1/music failed at /tmp/sow/x.wav",
        fallback="Glue worker failed",
    )
    assert detail == "Glue worker failed"
    assert glue_jobs._sanitize_detail("short safe reason", fallback="x") == "short safe reason"


def test_provider_stage_returning_in_flight_phase_is_ambiguous(worker):
    def stage_left_composing(job, conn, *, a_excerpt, b_excerpt, work_dir, log_event):
        return None

    worker.mocks["get_glue_job"].return_value = _make_job(
        phase="composing", provider_contacted=True, paid_reservation_state="consumed"
    )

    _run(worker, stage_left_composing)

    kwargs = _finish_kwargs(worker)
    assert kwargs["phase"] == "failed"
    assert kwargs["error_code"] == glue_jobs.GLUE_PROVIDER_UNKNOWN
    assert kwargs["retry_kind"] == "paid"


def test_generate_mode_recovers_storing_response_without_provider(worker, monkeypatch):
    job = _make_job(
        phase="storing-response",
        provider_contacted=True,
        paid_reservation_state="consumed",
        raw_response_r2_key="glues/7/jobs/x/response.pcm",
        raw_response_sha256="e" * 64,
        raw_response_bytes=1234,
    )
    worker.mocks["claim_glue_job"].return_value = (job, True)
    worker.mocks["get_glue_job"].return_value = job
    staging_calls = []

    def fake_staging(job_arg, conn_arg, *, work_dir, log_event):
        staging_calls.append(job_arg.id)

    monkeypatch.setattr(glue_jobs, "_load_staging_stage", lambda: fake_staging)
    stage = _FakeProviderStage()

    _run(worker, stage, mode="generate")

    assert staging_calls == [JOB_ID]
    assert stage.calls == []
    worker.mocks["prepare_excerpt"].assert_not_called()
    worker.mocks["read_songset_items_for_glue"].assert_not_called()


def test_compose_deadline_uses_compose_reserve():
    with pytest.raises(glue_jobs.GlueJobError) as exc_info:
        glue_jobs.require_deadline(_Context(1_000_000), "compose")
    assert exc_info.value.code == glue_jobs.GLUE_EXECUTION_DEADLINE
    assert exc_info.value.retry_kind == "free"
    assert glue_jobs.require_deadline(_Context(1_900_000), "compose") == 1_900_000
    assert glue_jobs.require_deadline(None, "compose") is None
    with pytest.raises(ValueError, match="stage"):
        glue_jobs.require_deadline(_Context(1), "persist")
