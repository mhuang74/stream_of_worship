"""Free staging: verify stored bytes, measure QA, stage once, select safely."""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from sow_render_worker import glue_jobs as spine
from sow_render_worker import glue_staging
from sow_render_worker.db import GlueJob
from sow_render_worker.glue_staging import (
    RawResponseUnverified,
    ReferenceAudio,
    finalize_staging,
    prepare_staged_take,
)

SR = 44_100
A_SECONDS = 2.0
B_SECONDS = 2.0
BEATS = 4
TEMPO = 120.0
GENERATION_SECONDS = 3.0  # max(3, beats * 60 / tempo)


def _tone(seconds: float, frequency: float, amplitude: float = 0.3) -> np.ndarray:
    frames = int(round(seconds * SR))
    t = np.arange(frames) / SR
    # A varying envelope keeps the 50 ms RMS blocks informative (a stationary
    # sine has numerically flat blocks and a meaningless Pearson correlation).
    envelope = 0.6 + 0.4 * np.sin(2 * np.pi * 0.7 * t)
    return np.sin(2 * np.pi * frequency * t) * amplitude * envelope


def _pcm16_stereo(mono: np.ndarray) -> np.ndarray:
    pcm = np.rint(np.clip(mono, -1.0, 1.0) * 32767).astype("<i2")
    return np.repeat(pcm[:, None], 2, axis=1)


def _response_bytes(
    *,
    a_seconds: float = A_SECONDS,
    b_seconds: float = B_SECONDS,
    generation_seconds: float = GENERATION_SECONDS,
    riff: bool = False,
) -> bytes:
    a = _tone(a_seconds, 220.0)
    sweep_t = np.arange(int(round(generation_seconds * SR))) / SR
    generation = (
        np.sin(
            2
            * np.pi
            * (220.0 + (330.0 - 220.0) * sweep_t / max(generation_seconds, 1e-9))
            * sweep_t
        )
        * 0.3
        * (0.6 + 0.4 * np.sin(2 * np.pi * 0.7 * sweep_t))
    )
    b = _tone(b_seconds, 330.0)
    stereo = _pcm16_stereo(np.concatenate([a, generation, b]))
    if not riff:
        return stereo.tobytes()
    buffer = io.BytesIO()
    sf.write(buffer, stereo, SR, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def _descriptor() -> dict:
    return {
        "v": 2,
        "model": "music_v2_5",
        "promptVersion": 2,
        "excerptSeconds": 12,
        "beats": BEATS,
        "toTempoBpm": TEMPO,
        "from": {"contentHash": "a" * 64, "steps": [[0, A_SECONDS]]},
        "to": {"contentHash": "b" * 64, "steps": [[0, B_SECONDS]]},
    }


NOW = datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)


def _job(**overrides) -> GlueJob:
    descriptor = _descriptor()
    defaults = dict(
        id="11111111-2222-3333-4444-555555555555",
        user_id=42,
        songset_id="ss-1",
        from_item_id="item-a",
        to_item_id="item-b",
        client_request_id="req-1",
        take_mode="initial",
        descriptor_key="d" * 64,
        descriptor_json=json.dumps(descriptor, separators=(",", ":")),
        expected_from_updated_at=NOW,
        expected_to_updated_at=NOW,
        phase="staging",
        active=True,
        dispatch_state="local",
        attempt_id="attempt-1",
        pending_take_id="99999999-8888-4777-8666-555555555555",
        take_ordinal=1,
        seed=123456,
        raw_response_r2_key="glues/42/jobs/job/response.pcm",
        raw_response_sha256="f" * 64,
        raw_response_bytes=1,
        provider_contacted=True,
        paid_reservation_state="consumed",
    )
    defaults.update(overrides)
    return GlueJob(**defaults)


def _items(*, updated_at: datetime = NOW) -> list[dict]:
    return [
        {
            "id": "item-a",
            "duration_seconds": 200.0,
            "updated_at": updated_at,
            "recording_hash_prefix": "a" * 16,
        },
        {
            "id": "item-b",
            "duration_seconds": 180.0,
            "updated_at": updated_at,
            "recording_hash_prefix": "b" * 16,
        },
    ]


def _reference_from_response(raw: bytes) -> ReferenceAudio:
    seam = glue_staging.decode_stereo_pcm16(raw)
    mono = seam.astype(np.float64).mean(axis=1) / 32768.0
    a_frames = int(round(A_SECONDS * SR))
    b_frames = int(round(B_SECONDS * SR))
    return ReferenceAudio(
        a=mono[:a_frames],
        b=mono[-b_frames:],
        a_seconds=A_SECONDS,
        b_seconds=B_SECONDS,
    )


class FakeStore:
    def __init__(self, raw: bytes, *, read_errors: int = 0):
        self.raw = raw
        self.read_errors = read_errors
        self.reads = 0
        self.puts: list[tuple[str, bytes]] = []

    def read_raw(self, job):
        self.reads += 1
        if self.read_errors > 0:
            self.read_errors -= 1
            raise OSError("transient R2 read")
        return self.raw

    def put_take(self, key, wav_bytes):
        self.puts.append((key, wav_bytes))


class FakeConn:
    autocommit = True

    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class ProviderFake:
    """Any provider contact during staging is a paid-call leak."""

    def __init__(self):
        self.calls: list[str] = []

    def upload(self, *args, **kwargs):  # pragma: no cover - must never run
        self.calls.append("upload")
        raise AssertionError("staging must never upload")

    def compose(self, *args, **kwargs):  # pragma: no cover - must never run
        self.calls.append("compose")
        raise AssertionError("staging must never compose")


@pytest.fixture
def harness(monkeypatch):
    state: dict = {}

    def install(job, items, store):
        conn = FakeConn()
        inserted: list[dict] = []
        selected: list[dict] = []
        finished: list[dict] = []

        def current(_conn, job_id, user_id):
            return state.get("current")

        def read_items(_conn, _songset_id):
            return state["items"]

        def insert(_conn, **kwargs):
            inserted.append(kwargs)
            existing = state.get("existing_take")
            if existing is not None:
                return existing, False
            return SimpleNamespace(id=kwargs["take_id"]), True

        def select(_conn, **kwargs):
            selected.append(kwargs)
            return state.get("select_result", True)

        def finish(_conn, job_id, user_id, *, resulting_take_id, active_take_id):
            finished.append(
                {
                    "job_id": job_id,
                    "resulting_take_id": resulting_take_id,
                    "active_take_id": active_take_id,
                }
            )
            return SimpleNamespace(id=job_id)

        state.update(
            current=job,
            items=items,
            store=store,
            conn=conn,
            inserted=inserted,
            selected=selected,
            finished=finished,
            events=[],
        )
        monkeypatch.setattr(glue_staging, "get_glue_job", current)
        monkeypatch.setattr(glue_staging, "read_songset_items_for_glue", read_items)
        monkeypatch.setattr(glue_staging, "insert_glue_take_if_absent", insert)
        monkeypatch.setattr(glue_staging, "select_glue_take_for_junction", select)
        monkeypatch.setattr(glue_staging, "finish_glue_job_ready", finish)
        monkeypatch.setattr(spine, "heartbeat_glue_job", lambda *a, **k: True)
        monkeypatch.setattr(spine, "_sleep", lambda _seconds: None)
        return state

    return install


def _log(state):
    def log(event, *, level="info", **fields):
        state["events"].append((event, fields))

    return log


class TestPrepareStagedTake:
    def test_measures_actual_seconds_and_stages_a_canonical_wav(self):
        raw = _response_bytes()
        job = _job()
        prepared = prepare_staged_take(
            raw,
            job=job,
            descriptor=_descriptor(),
            items={"item-a": _items()[0], "item-b": _items()[1]},
            reference=_reference_from_response(raw),
        )
        assert prepared.status == "ok"
        assert prepared.reasons == ()
        assert prepared.seconds == pytest.approx(GENERATION_SECONDS, abs=1e-6)
        assert prepared.wav_bytes[:4] == b"RIFF"
        assert prepared.wav_bytes[8:12] == b"WAVE"
        samples, rate = sf.read(io.BytesIO(prepared.wav_bytes), dtype="int16", always_2d=True)
        assert rate == SR
        assert samples.shape == (int(round(GENERATION_SECONDS * SR)), 2)
        assert prepared.metrics["refEnvelopeRA"] is not None
        assert prepared.metrics["refEnvelopeRB"] is not None

    def test_accepts_a_riff_response_payload(self):
        prepared = prepare_staged_take(
            _response_bytes(riff=True),
            job=_job(),
            descriptor=_descriptor(),
            items={"item-a": _items()[0], "item-b": _items()[1]},
            reference=_reference_from_response(_response_bytes()),
        )
        assert prepared.status == "ok"
        assert prepared.seconds == pytest.approx(GENERATION_SECONDS, abs=1e-6)

    def test_low_reference_envelope_is_check_with_measured_reason(self):
        raw = _response_bytes()
        seam = glue_staging.decode_stereo_pcm16(raw)
        mono = seam.astype(np.float64).mean(axis=1) / 32768.0
        silence = np.zeros_like(mono[: int(A_SECONDS * SR)])
        prepared = prepare_staged_take(
            raw,
            job=_job(),
            descriptor=_descriptor(),
            items={"item-a": _items()[0], "item-b": _items()[1]},
            reference=ReferenceAudio(
                a=silence, b=silence, a_seconds=A_SECONDS, b_seconds=B_SECONDS
            ),
        )
        assert prepared.status == "check"
        assert any("reference-envelope correlation" in reason for reason in prepared.reasons)

    def test_geometry_mismatch_is_a_free_failure(self):
        raw = _response_bytes()
        truncated = raw[: int(-0.5 * SR) * 4]
        with pytest.raises(spine.GlueJobError) as exc_info:
            prepare_staged_take(
                truncated,
                job=_job(),
                descriptor=_descriptor(),
                items={"item-a": _items()[0], "item-b": _items()[1]},
            )
        assert exc_info.value.code == "GLUE_WORKER_FAILED"
        assert exc_info.value.retry_kind == "free"

    def test_generated_floor_rejects_silence(self):
        raw = _response_bytes()
        seam = glue_staging.decode_stereo_pcm16(raw).copy()
        start = int(A_SECONDS * SR)
        end = start + int(GENERATION_SECONDS * SR)
        seam[start:end] = 0
        with pytest.raises(spine.GlueJobError) as exc_info:
            prepare_staged_take(
                seam.tobytes(),
                job=_job(),
                descriptor=_descriptor(),
                items={"item-a": _items()[0], "item-b": _items()[1]},
            )
        assert "minimum audio level" in str(exc_info.value.detail)


class TestFinalizeStaging:
    def test_compose_success_stages_a_ready_take_and_selects_it(self, harness):
        raw = _response_bytes()
        store = FakeStore(raw)
        state = harness(_job(), _items(), store)
        provider = ProviderFake()

        finalize_staging(state["conn"], _job(), _log(state), r2_store=store)

        assert provider.calls == []
        assert len(state["inserted"]) == 1
        insert = state["inserted"][0]
        assert insert["take_id"] == "99999999-8888-4777-8666-555555555555"
        assert insert["status"] == "ready"
        assert insert["seconds"] == pytest.approx(GENERATION_SECONDS, abs=1e-6)
        assert insert["r2_key"] == (
            "glues/42/" + "d" * 64 + "/take-99999999-8888-4777-8666-555555555555.wav"
        )
        metrics = json.loads(insert["metrics_json"])
        assert metrics["status"] == "ok"
        assert len(metrics["audioSha256"]) == 64
        assert len(store.puts) == 1
        assert store.puts[0][0] == insert["r2_key"]
        assert hashlib.sha256(store.puts[0][1]).hexdigest() == metrics["audioSha256"]
        assert len(state["selected"]) == 1
        assert state["selected"][0]["descriptor_key"] == "d" * 64
        assert state["finished"] == [
            {
                "job_id": state["current"].id,
                "resulting_take_id": insert["take_id"],
                "active_take_id": insert["take_id"],
            }
        ]
        events = [event for event, _fields in state["events"]]
        assert "glue.staging.recovered" in events
        assert "glue.job.ready" in events

    def test_crash_recovery_uses_verified_raw_bytes_with_zero_provider_calls(self, harness):
        raw = _response_bytes()
        store = FakeStore(raw)
        recovered = _job(phase="storing-response")
        state = harness(recovered, _items(), store)
        provider = ProviderFake()

        finalize_staging(state["conn"], recovered, _log(state), r2_store=store)

        assert provider.calls == []
        assert store.reads == 1
        assert len(state["inserted"]) == 1
        assert len(state["finished"]) == 1
        assert state["finished"][0]["active_take_id"] == state["inserted"][0]["take_id"]
        assert state["conn"].commits == 1

    def test_transient_r2_read_error_is_retried_within_the_free_policy(self, harness):
        raw = _response_bytes()
        store = FakeStore(raw, read_errors=2)
        state = harness(_job(), _items(), store)

        finalize_staging(state["conn"], _job(), _log(state), r2_store=store)

        assert store.reads == 3
        assert len(state["inserted"]) == 1

    def test_unverifiable_raw_response_is_terminal_without_retry(self, monkeypatch):
        raw = _response_bytes()
        conn = FakeConn()
        monkeypatch.setattr(spine, "heartbeat_glue_job", lambda *a, **k: True)

        class BadStore(FakeStore):
            def read_raw(self, job):
                self.reads += 1
                raise RawResponseUnverified("checksum mismatch")

        store = BadStore(raw)

        with pytest.raises(spine.GlueJobError) as exc_info:
            finalize_staging(conn, _job(), lambda *a, **k: None, r2_store=store)

        assert store.reads == 1
        assert exc_info.value.code == "GLUE_WORKER_FAILED"
        assert exc_info.value.retry_kind == "none"
        assert conn.commits == 0

    def test_local_storage_fault_maps_to_retry_kind_free(self, harness, monkeypatch):
        raw = _response_bytes()

        class FailingStore(FakeStore):
            def put_take(self, key, wav_bytes):
                raise OSError("R2 write failed")

        store = FailingStore(raw)
        state = harness(_job(), _items(), store)
        with pytest.raises(OSError):
            finalize_staging(state["conn"], _job(), _log(state), r2_store=store)
        # Nothing was selected or finished: the prior take stays effective.
        assert state["selected"] == []
        assert state["finished"] == []

        failure_log: list[tuple[str, dict]] = []
        finished: dict = {}

        def finish(_conn, job_id, user_id, **kwargs):
            finished.update(kwargs)
            return SimpleNamespace(id=job_id)

        monkeypatch.setattr(spine, "finish_glue_job", finish)
        monkeypatch.setattr(spine, "get_glue_job", lambda *a, **k: None)
        monkeypatch.setattr(spine, "_release_unconsumed_reservation", lambda *a, **k: False)
        spine._handle_failure(
            FakeConn(),
            state["current"],
            OSError("R2 write failed"),
            contacted=False,
            log=lambda event, **fields: failure_log.append((event, fields)),
        )
        assert finished["phase"] == "failed"
        assert finished["error_code"] == "GLUE_WORKER_FAILED"
        assert finished["retry_kind"] == "free"
        assert failure_log[-1][1]["errorCode"] == "GLUE_WORKER_FAILED"

    def test_redelivery_of_a_ready_job_is_a_noop(self, harness, monkeypatch):
        raw = _response_bytes()
        store = FakeStore(raw)
        job = _job(phase="ready", active=False)
        take = SimpleNamespace(id=job.pending_take_id)
        state = harness(job, _items(), store)
        state["existing_take"] = take

        recorded = glue_staging.record_staged_take(
            state["conn"],
            job,
            prepared=SimpleNamespace(
                seconds=GENERATION_SECONDS,
                status="ok",
                reasons=(),
                metrics={"status": "ok"},
                wav_bytes=raw,
            ),
            wav_sha256="e" * 64,
            log=_log(state),
        )

        assert recorded is False
        assert state["selected"] == []
        assert state["finished"] == []
        events = [event for event, _fields in state["events"]]
        assert "glue.staging.replay" in events

    def test_descriptor_drift_retains_take_but_leaves_it_unselected(self, harness):
        raw = _response_bytes()
        store = FakeStore(raw)
        drifted = datetime(2026, 11, 22, 11, 0, tzinfo=timezone.utc)
        state = harness(_job(), _items(updated_at=drifted), store)

        finalize_staging(state["conn"], _job(), _log(state), r2_store=store)

        assert len(state["inserted"]) == 1
        assert state["selected"] == []
        assert state["finished"][0]["resulting_take_id"] == state["inserted"][0]["take_id"]
        assert state["finished"][0]["active_take_id"] is None
        events = [event for event, _fields in state["events"]]
        assert "glue.staging.stale" in events
