"""Provider stage tests (issue #317): no network, fake transport only."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import urllib3
from botocore.exceptions import ClientError

from sow_render_worker import glue_jobs
from sow_render_worker import glue_provider
from sow_render_worker.db import GlueJob
from sow_render_worker.glue_excerpts import PreparedExcerpt
from sow_render_worker.glue_provider import (
    COMPOSE_READ_TIMEOUT_SECONDS,
    CONNECT_TIMEOUT_SECONDS,
    GLUE_TEXT,
    NEGATIVE_STYLES,
    POSITIVE_STYLES,
    UPLOAD_READ_TIMEOUT_SECONDS,
    ElevenLabsHttpTransport,
    ProviderAmbiguous,
    ProviderCopyright,
    ProviderEntitlement,
    ProviderNotSent,
    ProviderPlan,
    ProviderResponseTooLarge,
    build_compose_request,
    create_provider_stage,
    generation_duration_ms,
)

JOB_ID = "11111111-2222-3333-4444-555555555555"
USER_ID = 7
A_SECONDS = 12.0
B_SECONDS = 5.0
A_SHA = "a" * 64
B_SHA = "b" * 64

PINNED_REQUEST = (
    '{"model_id":"music_v2_5","seed":123456789,"composition_plan":{"chunks":['
    '{"song_id":"song-a","range":{"start_ms":0,"end_ms":12000}},'
    '{"text":"[Glue]\\n{instrumental only — no vocals, no words; seamless musical '
    "transition from the outgoing verse into the incoming chorus: warm piano, "
    'ambient pads, soft cymbal swell, natural build, no hard cut}",'
    '"duration_ms":3000,'
    '"positive_styles":["contemporary worship","warm piano","ambient pads","soft build",'
    '"seamless transition","great production quality"],'
    '"negative_styles":["vocals","singing","vocal","choir","spoken word","lyrics",'
    '"abrupt ending","silence"],'
    '"context_adherence":"high",'
    '"conditioning_ref":{"song_id":"song-b","range":{"start_ms":0,"end_ms":5000}},'
    '"condition_strength":"high"},'
    '{"song_id":"song-b","range":{"start_ms":0,"end_ms":5000}}]}}'
)


def _descriptor_json(*, beats: int = 4, tempo: float = 86.13281) -> str:
    return json.dumps(
        {
            "v": 2,
            "model": "music_v2_5",
            "promptVersion": 2,
            "excerptSeconds": 12,
            "beats": beats,
            "toTempoBpm": tempo,
            "from": {"contentHash": "a" * 64, "steps": None},
            "to": {"contentHash": "b" * 64, "steps": None},
        },
        separators=(",", ":"),
    )


def _make_job(**overrides) -> GlueJob:
    values = {
        "id": JOB_ID,
        "user_id": USER_ID,
        "songset_id": "ss_001",
        "from_item_id": "item-a",
        "to_item_id": "item-b",
        "client_request_id": "33333333-4444-5555-6666-777777777777",
        "take_mode": "initial",
        "descriptor_key": "d" * 64,
        "descriptor_json": _descriptor_json(),
        "phase": "uploading",
        "active": True,
        "dispatch_state": "dispatched",
        "attempt_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "pending_take_id": "ffffffff-0000-1111-2222-333333333333",
        "take_ordinal": 1,
        "seed": 123456789,
        "expected_from_updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "expected_to_updated_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
        "paid_reservation_id": "99999999-8888-7777-6666-555555555555",
        "paid_reservation_state": "consumed",
        "provider_contacted": True,
    }
    values.update(overrides)
    return GlueJob(**values)


def _excerpt(
    side: str,
    *,
    seconds: float,
    song_id: str | None = None,
    wav_path: Path | None = None,
    **overrides,
):
    values = {
        "side": side,
        "seconds": seconds,
        "logical_key": f"key-{side}",
        "content_hash": A_SHA if side == "A" else B_SHA,
        "canonical_steps_json": None,
        "wav_path": wav_path or Path(f"/tmp/excerpt-{side}.wav"),
        "audio_sha256": A_SHA if side == "A" else B_SHA,
        "elevenlabs_song_id": song_id,
    }
    values.update(overrides)
    return PreparedExcerpt(**values)


def _default_excerpts(root: Path):
    sources = Path(root) / "sources"
    sources.mkdir(parents=True, exist_ok=True)

    def make(side: str, **overrides):
        wav = sources / f"excerpt-{side}-{len(list(sources.iterdir()))}.wav"
        wav.write_bytes(b"RIFF" + side.encode() * 60)
        return _excerpt(side, wav_path=wav, **overrides)

    return make


class _Context:
    def __init__(self, remaining_ms: int) -> None:
        self._remaining_ms = remaining_ms

    def get_remaining_time_in_millis(self) -> int:
        return self._remaining_ms


class FakeTransport:
    """In-memory provider; no network and no API key."""

    def __init__(
        self,
        *,
        upload_ids=("song-a", "song-b"),
        chunks=(b"\x00\x01" * 64,),
        upload_errors=None,
        compose_error=None,
        compose_chunks=None,
        order=None,
    ) -> None:
        self._upload_ids = list(upload_ids)
        self._chunks = list(chunks)
        self._upload_errors = list(upload_errors or [])
        self._compose_error = compose_error
        self._compose_chunks = compose_chunks
        self.upload_calls: list[str] = []
        self.compose_calls: list[dict] = []
        self._order = order if order is not None else []

    def upload(self, wav_path) -> str:
        self.upload_calls.append(str(wav_path))
        self._order.append(("upload", str(wav_path)))
        if self._upload_errors:
            raise self._upload_errors.pop(0)
        return self._upload_ids.pop(0)

    def compose(self, request):
        self.compose_calls.append(json.loads(json.dumps(request)))
        self._order.append(("compose", None))
        if self._compose_error is not None:
            raise self._compose_error
        source = self._compose_chunks if self._compose_chunks is not None else self._chunks
        return iter(list(source))


class FakeRawStore:
    """Immutable store double: ``existing`` selects the collision outcome."""

    def __init__(self, *, existing: str | None = None, fail: Exception | None = None) -> None:
        self.calls: list[tuple[str, bytes]] = []
        self._existing = existing
        self._fail = fail

    def put_if_absent(self, key: str, source: Path) -> bool:
        data = Path(source).read_bytes()
        self.calls.append((key, data))
        if self._fail is not None:
            raise self._fail
        if self._existing == "different":
            return False
        return True


@pytest.fixture
def excerpts(tmp_path):
    """Build real excerpt WAVs so the stage can read their bytes."""

    def make(side: str, **overrides):
        wav = tmp_path / f"excerpt-{side}-{len(list(tmp_path.iterdir()))}.wav"
        wav.write_bytes(b"RIFF" + side.encode() * 60)
        return _excerpt(side, wav_path=wav, **overrides)

    return make


@pytest.fixture
def recording(monkeypatch, tmp_path):
    """Record DB/cache writes and their order; patch the stage's helpers."""
    order: list[tuple[str, object]] = []
    calls = {name: [] for name in ("set_phase", "recorded", "rejected", "heartbeats")}

    state = {"job": _make_job()}

    def fake_get_glue_job(conn, job_id, user_id):
        return state.get("current")

    def fake_set_phase(conn, job_id, user_id, phase, **fields):
        calls["set_phase"].append((phase, fields))
        order.append(("set_phase", phase))
        return state["job"]

    def fake_record_raw(conn, job_id, user_id, **kwargs):
        calls["recorded"].append(kwargs)
        order.append(("record_raw", kwargs))
        return state["job"]

    def fake_record_uploaded(conn, user_id, **kwargs):
        calls["uploads"] = calls.get("uploads", [])
        calls["uploads"].append(kwargs)
        order.append(("record_uploaded", kwargs["side"]))
        return SimpleNamespace(**kwargs)

    def fake_record_rejection(conn, user_id, **kwargs):
        calls["rejected"].append(kwargs)
        order.append(("record_rejection", kwargs["side"]))
        return SimpleNamespace(**kwargs)

    def fake_heartbeat(conn, job, log, *, stage):
        calls["heartbeats"].append(stage)
        order.append(("heartbeat", stage))

    monkeypatch.setattr(glue_provider, "get_glue_job", fake_get_glue_job)
    monkeypatch.setattr(glue_provider, "set_glue_phase", fake_set_phase)
    monkeypatch.setattr(glue_provider, "record_glue_raw_response", fake_record_raw)
    monkeypatch.setattr(glue_provider, "record_uploaded", fake_record_uploaded)
    monkeypatch.setattr(glue_provider, "record_rejection", fake_record_rejection)
    monkeypatch.setattr(glue_jobs, "_heartbeat", fake_heartbeat)
    monkeypatch.setattr(glue_jobs, "_sleep", lambda _seconds: None)
    state["current"] = None
    return SimpleNamespace(
        order=order, calls=calls, state=state, set_phase=fake_set_phase, tmp_path=tmp_path
    )


def _run_stage(
    recording,
    *,
    transport=None,
    raw_store=None,
    context=None,
    a_excerpt=None,
    b_excerpt=None,
    work_dir: Path | None = None,
    max_response_bytes: int = glue_provider.MAX_RESPONSE_BYTES,
    sleep=None,
    excerpts=None,
    tmp_path: Path | None = None,
):
    transport = transport or FakeTransport()
    raw_store = raw_store or FakeRawStore()
    stage = create_provider_stage(
        lambda_context=context,
        transport=transport,
        raw_store=raw_store,
        sleep=sleep or (lambda _seconds: None),
        max_response_bytes=max_response_bytes,
    )
    job = recording.state["job"]
    root = Path(tmp_path) if tmp_path is not None else recording.tmp_path
    if work_dir is None:
        work_dir = root / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    make = excerpts or _default_excerpts(root)
    stage(
        job,
        object(),
        a_excerpt=a_excerpt or make("A", seconds=A_SECONDS),
        b_excerpt=b_excerpt or make("B", seconds=B_SECONDS),
        work_dir=work_dir,
        log_event=lambda *a, **k: None,
    )
    return transport, raw_store


# ---------------------------------------------------------------------------
# Request builder
# ---------------------------------------------------------------------------


class TestComposeRequest:
    def test_exact_pinned_payload(self):
        request = build_compose_request(
            seed=123456789,
            beats=4,
            to_tempo_bpm=86.13281,
            a_song_id="song-a",
            a_ref_ms=12000,
            b_song_id="song-b",
            b_ref_ms=5000,
        )
        serialized = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
        assert serialized == PINNED_REQUEST

    def test_conditioning_reference_is_capped_at_eight_seconds(self):
        request = build_compose_request(
            seed=1,
            beats=4,
            to_tempo_bpm=86.13281,
            a_song_id="A_ID",
            a_ref_ms=12000,
            b_song_id="B_ID",
            b_ref_ms=12000,
        )
        chunk = request["composition_plan"]["chunks"][1]
        assert chunk["conditioning_ref"]["range"] == {"start_ms": 0, "end_ms": 8000}
        assert request["composition_plan"]["chunks"][2]["range"]["end_ms"] == 12000

    def test_uses_actual_short_side_ranges(self):
        request = build_compose_request(
            seed=1,
            beats=4,
            to_tempo_bpm=86.13281,
            a_song_id="A_ID",
            a_ref_ms=3210,
            b_song_id="B_ID",
            b_ref_ms=1000,
        )
        chunks = request["composition_plan"]["chunks"]
        assert chunks[0]["range"]["end_ms"] == 3210
        assert chunks[1]["conditioning_ref"]["range"]["end_ms"] == 1000
        assert chunks[2]["range"]["end_ms"] == 1000

    @pytest.mark.parametrize(
        ("beats", "tempo", "expected"),
        [
            (4, 86.13281, 3000),
            (4, 60.0, 4000),
            (8, 60.0, 8000),
            (16, 60.0, 16000),
            (2, 120.0, 3000),
            (16, 20.0, 48000),
        ],
    )
    def test_duration_ms_formula(self, beats, tempo, expected):
        assert generation_duration_ms(beats, tempo) == expected

    def test_rejects_generation_over_120_seconds(self):
        with pytest.raises(ValueError, match="120000"):
            build_compose_request(
                seed=1,
                beats=16,
                to_tempo_bpm=7.0,
                a_song_id="A_ID",
                a_ref_ms=12000,
                b_song_id="B_ID",
                b_ref_ms=12000,
            )

    def test_contains_no_content_or_locale_text(self):
        request = build_compose_request(
            seed=1,
            beats=4,
            to_tempo_bpm=86.13281,
            a_song_id="A_ID",
            a_ref_ms=12000,
            b_song_id="B_ID",
            b_ref_ms=5000,
        )
        serialized = json.dumps(request, ensure_ascii=False)
        # The only lowercase "lyrics" allowed is the fixed negative style.
        assert serialized.count('"lyrics"') == 1
        for forbidden in (
            "title",
            "Title",
            "song_title",
            "Lyrics",
            "lyric_text",
            "zh-Hant",
            "zh_TW",
            "locale",
            "Song A",
            "Song B",
            "敬拜",
            "詩歌",
        ):
            assert forbidden not in serialized, forbidden
        # No CJK code point: the prompt is fixed English (the em dash is not CJK).
        assert not any("\u3400" <= char <= "\u9fff" for char in serialized)

    def test_prompt_and_styles_are_the_fixed_english_lists(self):
        request = build_compose_request(
            seed=1,
            beats=4,
            to_tempo_bpm=86.13281,
            a_song_id="A_ID",
            a_ref_ms=12000,
            b_song_id="B_ID",
            b_ref_ms=12000,
        )
        chunk = request["composition_plan"]["chunks"][1]
        assert chunk["text"] == GLUE_TEXT
        assert chunk["positive_styles"] == list(POSITIVE_STYLES)
        assert chunk["negative_styles"] == list(NEGATIVE_STYLES)
        assert chunk["context_adherence"] == "high"
        assert chunk["condition_strength"] == "high"


# ---------------------------------------------------------------------------
# HTTP transport
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status=200, *, data=b"", chunks=(), read_error=None) -> None:
        self.status = status
        self.data = data
        self.headers = {}
        self._chunks = list(chunks)
        self._read_error = read_error
        self.released = False

    def read(self, size=None):
        if self._read_error is not None:
            raise self._read_error
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    def release_conn(self):
        self.released = True


class _FakePool:
    def __init__(self, response) -> None:
        self.calls: list[dict] = []
        self._response = response

    def urlopen(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._response


class TestElevenLabsHttpTransport:
    def test_timeout_configuration_is_30_900_1800(self):
        pool = _FakePool(_FakeResponse(data=b'{"song_id":"s1"}'))
        transport = ElevenLabsHttpTransport("fake-key", pool=pool)
        assert transport._timeout(UPLOAD_READ_TIMEOUT_SECONDS).connect_timeout == 30
        assert transport._timeout(UPLOAD_READ_TIMEOUT_SECONDS).read_timeout == 900
        assert transport._timeout(COMPOSE_READ_TIMEOUT_SECONDS).read_timeout == 1800
        assert COMPOSE_READ_TIMEOUT_SECONDS == 1800
        assert CONNECT_TIMEOUT_SECONDS == 30

    def test_caps_timeouts_to_remaining_lambda_budget(self):
        transport = ElevenLabsHttpTransport("fake-key", lambda_context=_Context(500_000))
        timeout = transport._timeout(COMPOSE_READ_TIMEOUT_SECONDS)
        assert timeout.connect_timeout <= 30
        assert timeout.read_timeout <= 470

    def test_upload_sends_wav_and_returns_song_id(self, tmp_path):
        wav = tmp_path / "excerpt-A.wav"
        wav.write_bytes(b"RIFF....")
        pool = _FakePool(_FakeResponse(data=b'{"song_id":"song-a"}'))
        transport = ElevenLabsHttpTransport("fake-key", pool=pool)

        assert transport.upload(wav) == "song-a"
        call = pool.calls[0]
        assert call["url"] == "https://api.elevenlabs.io/v1/music/upload"
        assert call["headers"]["xi-api-key"] == "fake-key"
        assert call["retries"] is False
        assert b"RIFF" in call["body"]
        assert call["preload_content"] is True

    def test_compose_streams_with_output_format(self):
        pool = _FakePool(_FakeResponse(chunks=[b"abc", b"def", b""]))
        transport = ElevenLabsHttpTransport("fake-key", pool=pool)
        chunks = list(transport.compose({"model_id": "music_v2_5"}))
        assert chunks == [b"abc", b"def"]
        call = pool.calls[0]
        assert call["url"].endswith("?output_format=pcm_44100")
        assert call["preload_content"] is False

    @pytest.mark.parametrize(
        ("status", "body", "expected"),
        [
            (401, b"unauthorized", ProviderEntitlement),
            (402, b"payment required", ProviderEntitlement),
            (403, b"missing_permissions", ProviderEntitlement),
            (422, b"bad plan", ProviderPlan),
            (413, b"too large", ProviderPlan),
            (415, b"unsupported media", ProviderPlan),
            (400, b"copyright claim", ProviderCopyright),
            (429, b"rate limited", ProviderAmbiguous),
            (500, b"boom", ProviderAmbiguous),
            (503, b"unavailable", ProviderAmbiguous),
        ],
    )
    def test_status_mapping(self, tmp_path, status, body, expected):
        wav = tmp_path / "x.wav"
        wav.write_bytes(b"RIFF")
        pool = _FakePool(_FakeResponse(status=status, data=body))
        transport = ElevenLabsHttpTransport("fake-key", pool=pool)
        with pytest.raises(expected) as exc_info:
            transport.upload(wav)
        assert exc_info.value.provider_status == status

    def test_connection_setup_failure_is_not_sent(self, tmp_path):
        wav = tmp_path / "x.wav"
        wav.write_bytes(b"RIFF")

        class FailingPool:
            def urlopen(self, *args, **kwargs):
                raise urllib3.exceptions.NewConnectionError(None, "refused")

        transport = ElevenLabsHttpTransport("fake-key", pool=FailingPool())
        with pytest.raises(ProviderNotSent):
            transport.upload(wav)

    def test_read_timeout_is_ambiguous_not_retryable(self):
        read_error = urllib3.exceptions.ReadTimeoutError(None, "", "")
        pool = _FakePool(_FakeResponse(read_error=read_error))
        transport = ElevenLabsHttpTransport("fake-key", pool=pool)
        stream = transport.compose({"seed": 1})
        with pytest.raises(ProviderAmbiguous):
            list(stream)

    def test_upload_rejection_has_no_mp3_fallback(self, tmp_path):
        wav = tmp_path / "x.wav"
        wav.write_bytes(b"RIFF")
        pool = _FakePool(_FakeResponse(status=415, data=b"unsupported"))
        transport = ElevenLabsHttpTransport("fake-key", pool=pool)
        with pytest.raises(ProviderPlan):
            transport.upload(wav)
        assert len(pool.calls) == 1


# ---------------------------------------------------------------------------
# Spine integration: the real stage through execute_glue_job
# ---------------------------------------------------------------------------


def test_execute_glue_job_drives_stage_to_storing_response(monkeypatch, tmp_path):
    """The spine + the real provider stage, with fakes at both seams."""
    from sow_render_worker.glue_descriptor import build_descriptor
    from sow_render_worker.glue_excerpts import PreparedExcerpt

    updated_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    a_hash, b_hash = "a" * 64, "b" * 64
    _, canonical, key = build_descriptor(
        beats=4,
        to_tempo_bpm=96.0,
        from_side={"contentHash": a_hash, "steps": None},
        to_side={"contentHash": b_hash, "steps": None},
    )
    job = _make_job(
        descriptor_json=canonical,
        descriptor_key=key,
        phase="preparing",
        seed=123456789,
        expected_from_updated_at=updated_at,
        expected_to_updated_at=updated_at,
        provider_contacted=False,
    )

    def items():
        common = {
            "songset_id": "ss_001",
            "gap_beats": 2,
            "crossfade_enabled": False,
            "crossfade_duration_seconds": None,
            "key_shift_semitones": 0,
            "tempo_ratio": 1.0,
            "arrangement_steps": None,
            "glue_enabled": True,
            "glue_beats": 4,
            "glue_active_take_id": None,
            "updated_at": updated_at,
            "sections": None,
            "beats": None,
            "duration_seconds": 180.0,
            "deleted_at": None,
        }
        return [
            {**common, "id": "item-a", "song_id": "song-a", "recording_hash_prefix": "rec-a",
             "position": 0, "tempo_bpm": 80.0, "recording_content_hash": a_hash},
            {**common, "id": "item-b", "song_id": "song-b", "recording_hash_prefix": "rec-b",
             "position": 1, "tempo_bpm": 96.0, "recording_content_hash": b_hash},
        ]

    phases: list[str] = []
    recorded: list[dict] = []
    uploads: list[dict] = []
    finished: list[dict] = []

    monkeypatch.setattr(glue_jobs, "claim_glue_job", lambda *a, **k: (job, True))
    monkeypatch.setattr(glue_jobs, "read_songset_items_for_glue", lambda *a, **k: items())
    monkeypatch.setattr(glue_jobs, "record_provider_contact", lambda *a, **k: True)
    monkeypatch.setattr(glue_jobs, "heartbeat_glue_job", lambda *a, **k: True)
    monkeypatch.setattr(glue_jobs, "_sleep", lambda _s: None)
    # The spine re-reads the row after the stage; return the post-stage facts
    # so the real staging handoff (absent module) is exercised, not a crash.
    post_stage = type(job)(
        **{
            **job.__dict__,
            "phase": "storing-response",
            "provider_contacted": True,
            "raw_response_r2_key": f"glues/7/jobs/{JOB_ID}/response.pcm",
            "raw_response_sha256": "e" * 64,
            "raw_response_bytes": 128,
        }
    )
    spine_reads: list[str] = []
    monkeypatch.setattr(
        glue_jobs,
        "get_glue_job",
        lambda conn, job_id, user_id: spine_reads.append(job_id) or post_stage,
    )
    monkeypatch.setattr(
        glue_jobs,
        "finish_glue_job",
        lambda conn, job_id, user_id, **kwargs: finished.append(kwargs) or job,
    )
    monkeypatch.setattr(glue_provider, "get_glue_job", lambda *a, **k: job)
    monkeypatch.setattr(
        glue_provider,
        "set_glue_phase",
        lambda conn, job_id, user_id, phase, **fields: phases.append(phase) or job,
    )
    monkeypatch.setattr(
        glue_provider,
        "record_glue_raw_response",
        lambda conn, job_id, user_id, **kwargs: recorded.append(kwargs) or job,
    )
    monkeypatch.setattr(
        glue_provider,
        "record_uploaded",
        lambda conn, user_id, **kwargs: uploads.append(kwargs) or SimpleNamespace(**kwargs),
    )

    def prepare_excerpt(**kwargs):
        side = kwargs["side"]
        wav = tmp_path / f"excerpt-{side}.wav"
        wav.write_bytes(b"RIFF" + side.encode() * 60)
        return PreparedExcerpt(
            side=side,
            seconds=A_SECONDS if side == "A" else B_SECONDS,
            logical_key=f"key-{side}",
            content_hash=a_hash if side == "A" else b_hash,
            canonical_steps_json=None,
            wav_path=wav,
            audio_sha256=a_hash if side == "A" else b_hash,
            elevenlabs_song_id=None,
        )

    monkeypatch.setattr(glue_jobs, "prepare_excerpt", prepare_excerpt)
    monkeypatch.setattr(
        glue_jobs, "_load_staging_stage", lambda: (_ for _ in ()).throw(ImportError("absent"))
    )

    transport = FakeTransport()
    store = FakeRawStore()
    stage = create_provider_stage(transport=transport, raw_store=store)

    glue_jobs.execute_glue_job(
        JOB_ID,
        USER_ID,
        object(),
        lambda_context=_Context(1_900_000),
        provider_stage=stage,
        asset_fetcher=object(),
    )

    assert transport.compose_calls == [json.loads(PINNED_REQUEST)]
    assert [u["side"] for u in uploads] == ["A", "B"]
    assert [u["elevenlabs_song_id"] for u in uploads] == ["song-a", "song-b"]
    assert recorded[0]["byte_count"] == 128
    assert recorded[0]["r2_key"] == f"glues/7/jobs/{JOB_ID}/response.pcm"
    assert phases[-1] == "storing-response"
    assert "storing-response" in phases
    # The spine re-read the persisted row, found the durable response, and
    # stopped at storing-response because staging is absent: never "failed".
    assert spine_reads, "the spine must re-read the job after the provider stage"
    assert finished == []


# ---------------------------------------------------------------------------
# Raw response store
# ---------------------------------------------------------------------------


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, size=-1):
        data, self._data = self._data, b""
        return data

    def close(self):
        pass


class _FakeS3:
    """Minimal conditional-put S3 double: immutable keys, no network."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts = 0
        self.gets = 0

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self.puts += 1
        data = Body.read() if hasattr(Body, "read") else bytes(Body)
        if kwargs.get("IfNoneMatch") == "*" and Key in self.objects:
            raise ClientError(
                {"Error": {"Code": "PreconditionFailed", "Message": "exists"}}, "PutObject"
            )
        self.objects[Key] = data

    def head_object(self, *, Bucket, Key):
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key])}

    def get_object(self, *, Bucket, Key):
        self.gets += 1
        return {"Body": _FakeBody(self.objects[Key])}


class TestR2RawResponseStore:
    def test_writes_once_with_conditional_put(self, tmp_path):
        s3 = _FakeS3()
        store = glue_provider.R2RawResponseStore(
            SimpleNamespace(client=s3, bucket_name="test-bucket")
        )
        source = tmp_path / "response.pcm"
        source.write_bytes(b"raw-bytes")
        assert store.put_if_absent("glues/7/jobs/j1/response.pcm", source) is True
        assert s3.objects["glues/7/jobs/j1/response.pcm"] == b"raw-bytes"
        assert s3.puts == 1

    def test_identical_existing_object_is_not_rewritten(self, tmp_path):
        s3 = _FakeS3()
        s3.objects["k"] = b"same"
        store = glue_provider.R2RawResponseStore(
            SimpleNamespace(client=s3, bucket_name="bucket")
        )
        source = tmp_path / "response.pcm"
        source.write_bytes(b"same")
        assert store.put_if_absent("k", source) is True
        assert s3.objects["k"] == b"same"

    def test_different_existing_object_refuses(self, tmp_path):
        s3 = _FakeS3()
        s3.objects["k"] = b"theirs"
        store = glue_provider.R2RawResponseStore(
            SimpleNamespace(client=s3, bucket_name="bucket")
        )
        source = tmp_path / "response.pcm"
        source.write_bytes(b"mine")
        assert store.put_if_absent("k", source) is False
        assert s3.objects["k"] == b"theirs"

    def test_same_length_different_bytes_refuses(self, tmp_path):
        s3 = _FakeS3()
        s3.objects["k"] = b"aaaa"
        store = glue_provider.R2RawResponseStore(
            SimpleNamespace(client=s3, bucket_name="bucket")
        )
        source = tmp_path / "response.pcm"
        source.write_bytes(b"bbbb")
        assert store.put_if_absent("k", source) is False

    def test_non_precondition_error_propagates(self, tmp_path):
        class Denied(_FakeS3):
            def put_object(self, **kwargs):
                raise ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")

        store = glue_provider.R2RawResponseStore(
            SimpleNamespace(client=Denied(), bucket_name="bucket")
        )
        source = tmp_path / "response.pcm"
        source.write_bytes(b"x")
        with pytest.raises(ClientError):
            store.put_if_absent("k", source)


# ---------------------------------------------------------------------------
# Stage behaviour
# ---------------------------------------------------------------------------


class TestProviderFactory:
    def test_missing_api_key_raises_configuration_error(self, monkeypatch):
        monkeypatch.delenv("SOW_ELEVENLABS_API_KEY", raising=False)
        with pytest.raises(glue_jobs.GlueConfigurationError) as exc_info:
            create_provider_stage()
        assert exc_info.value.code == "GLUE_NOT_CONFIGURED"

    def test_injected_transport_needs_no_api_key(self, monkeypatch):
        monkeypatch.delenv("SOW_ELEVENLABS_API_KEY", raising=False)
        stage = create_provider_stage(transport=FakeTransport(), raw_store=FakeRawStore())
        assert callable(stage)

    def test_default_transport_uses_env_key_and_no_network(self, monkeypatch):
        monkeypatch.setenv("SOW_ELEVENLABS_API_KEY", "fake-test-key")
        stage = create_provider_stage(raw_store=FakeRawStore())
        assert callable(stage)

    def test_create_provider_from_env_returns_http_transport(self, monkeypatch):
        monkeypatch.setenv("SOW_ELEVENLABS_API_KEY", "fake-test-key")
        transport = glue_provider.create_provider_from_env()
        assert isinstance(transport, ElevenLabsHttpTransport)

    def test_create_provider_from_env_requires_a_key(self, monkeypatch):
        monkeypatch.delenv("SOW_ELEVENLABS_API_KEY", raising=False)
        with pytest.raises(glue_jobs.GlueConfigurationError):
            glue_provider.create_provider_from_env()


class TestTransportTimeoutsUnderContext:
    def test_upload_read_timeout_is_capped_not_extended(self):
        transport = ElevenLabsHttpTransport("fake-key", lambda_context=_Context(200_000))
        timeout = transport._timeout(UPLOAD_READ_TIMEOUT_SECONDS)
        # 200 s remaining: connect 30 s, read at most 170 s, never the full 900.
        assert timeout.connect_timeout == 30
        assert 0 < timeout.read_timeout <= 170

    def test_no_context_keeps_the_spec_timeouts(self):
        transport = ElevenLabsHttpTransport("fake-key")
        upload = transport._timeout(UPLOAD_READ_TIMEOUT_SECONDS)
        compose = transport._timeout(COMPOSE_READ_TIMEOUT_SECONDS)
        assert (upload.connect_timeout, upload.read_timeout) == (30, 900)
        assert (compose.connect_timeout, compose.read_timeout) == (30, 1800)

    def test_upload_and_compose_pass_their_own_timeout_to_the_pool(self, tmp_path):
        wav = tmp_path / "x.wav"
        wav.write_bytes(b"RIFF")
        pool = _FakePool(_FakeResponse(data=b'{"song_id":"s"}'))
        transport = ElevenLabsHttpTransport("fake-key", pool=pool)
        transport.upload(wav)
        assert pool.calls[-1]["timeout"].read_timeout == 900

        stream_pool = _FakePool(_FakeResponse(chunks=[b"a", b""]))
        composer = ElevenLabsHttpTransport("fake-key", pool=stream_pool)
        list(composer.compose({"seed": 1}))
        assert stream_pool.calls[-1]["timeout"].read_timeout == 1800


class TestProviderStage:
    def test_cached_excerpts_skip_upload(self, recording):
        transport, store = _run_stage(
            recording,
            a_excerpt=_excerpt("A", seconds=A_SECONDS, song_id="cached-a"),
            b_excerpt=_excerpt("B", seconds=B_SECONDS, song_id="cached-b"),
        )
        assert transport.upload_calls == []
        assert len(transport.compose_calls) == 1
        request = transport.compose_calls[0]
        assert request["composition_plan"]["chunks"][0]["song_id"] == "cached-a"
        assert request["composition_plan"]["chunks"][2]["song_id"] == "cached-b"
        assert store.calls[0][0] == f"glues/7/jobs/{JOB_ID}/response.pcm"

    def test_partial_cache_uploads_only_the_uncached_side(self, recording, excerpts):
        transport, _ = _run_stage(
            recording,
            a_excerpt=excerpts("A", seconds=A_SECONDS, song_id="cached-a"),
        )
        assert len(transport.upload_calls) == 1
        assert transport.upload_calls[0].endswith(".wav")
        request = transport.compose_calls[0]
        assert request["composition_plan"]["chunks"][0]["song_id"] == "cached-a"
        assert request["composition_plan"]["chunks"][2]["song_id"] == "song-a"

    def test_upload_song_id_persisted_before_compose(self, recording):
        transport, _ = _run_stage(recording, transport=FakeTransport(order=recording.order))
        assert len(transport.upload_calls) == 2
        upload_records = [
            index for index, (name, _) in enumerate(recording.order) if name == "record_uploaded"
        ]
        compose_index = recording.order.index(("compose", None))
        assert len(upload_records) == 2
        assert all(index < compose_index for index in upload_records)

    def test_compose_called_exactly_once(self, recording):
        transport, _ = _run_stage(recording)
        assert len(transport.compose_calls) == 1
        assert transport.compose_calls[0] == json.loads(PINNED_REQUEST)

    def test_raw_metadata_recorded_before_storing_response(self, recording):
        _run_stage(recording)
        record_index = next(
            index for index, (name, _) in enumerate(recording.order) if name == "record_raw"
        )
        storing_index = recording.order.index(("set_phase", "storing-response"))
        assert record_index < storing_index
        recorded = recording.calls["recorded"][0]
        assert recorded["byte_count"] == 128
        assert recorded["sha256"] == hashlib.sha256(b"\x00\x01" * 64).hexdigest()
        assert recorded["r2_key"] == f"glues/7/jobs/{JOB_ID}/response.pcm"
        assert ("set_phase", "composing") in recording.order
        assert recording.calls["heartbeats"].count("composing") == 1

    def test_existing_raw_response_is_reused_without_provider(self, monkeypatch, tmp_path):
        job = _make_job(
            phase="storing-response",
            raw_response_r2_key="glues/7/jobs/x/response.pcm",
            raw_response_sha256="e" * 64,
            raw_response_bytes=10,
        )
        monkeypatch.setattr(glue_provider, "get_glue_job", lambda *a, **k: job)
        phases: list[str] = []
        monkeypatch.setattr(
            glue_provider,
            "set_glue_phase",
            lambda conn, job_id, user_id, phase, **fields: phases.append(phase) or job,
        )
        transport = FakeTransport()
        store = FakeRawStore()
        stage = create_provider_stage(transport=transport, raw_store=store)
        stage(
            job,
            object(),
            a_excerpt=_excerpt("A", seconds=A_SECONDS),
            b_excerpt=_excerpt("B", seconds=B_SECONDS),
            work_dir=tmp_path,
            log_event=lambda *a, **k: None,
        )
        assert transport.upload_calls == []
        assert transport.compose_calls == []
        assert store.calls == []
        assert phases == ["storing-response"]

    def test_resumed_job_with_persisted_song_ids_uploads_nothing(self, recording, tmp_path):
        # Free resume of a deadline-refused post-contact job: both excerpt ids
        # are already durable, so this delivery must go straight to compose.
        transport = FakeTransport()
        store = FakeRawStore()
        _run_stage(
            recording,
            transport=transport,
            raw_store=store,
            a_excerpt=_excerpt("A", seconds=A_SECONDS, song_id="persisted-a"),
            b_excerpt=_excerpt("B", seconds=B_SECONDS, song_id="persisted-b"),
        )
        assert transport.upload_calls == []
        chunks = transport.compose_calls[0]["composition_plan"]["chunks"]
        assert chunks[0]["song_id"] == "persisted-a"
        assert chunks[1]["conditioning_ref"]["song_id"] == "persisted-b"
        assert chunks[2]["song_id"] == "persisted-b"
        assert recording.calls.get("uploads", []) == []

    def test_never_overwrites_existing_raw_object(self, recording, tmp_path, monkeypatch):
        job = _make_job(
            raw_response_r2_key="glues/7/jobs/x/response.pcm",
            raw_response_sha256="e" * 64,
            raw_response_bytes=10,
        )
        monkeypatch.setattr(glue_provider, "get_glue_job", lambda *a, **k: job)
        transport = FakeTransport()
        store = FakeRawStore(existing="identical")
        stage = create_provider_stage(transport=transport, raw_store=store)
        stage(
            job,
            object(),
            a_excerpt=_excerpt("A", seconds=A_SECONDS),
            b_excerpt=_excerpt("B", seconds=B_SECONDS),
            work_dir=tmp_path,
            log_event=lambda *a, **k: None,
        )
        # The already-stored response wins; nothing new is composed or recorded.
        assert transport.compose_calls == []
        assert recording.calls["recorded"] == []

    def test_different_existing_object_is_not_overwritten(self, recording, tmp_path):
        store = FakeRawStore(existing="different")
        with pytest.raises(glue_jobs.GlueJobError) as exc_info:
            _run_stage(recording, raw_store=store, work_dir=tmp_path)
        # Exactly one conditional put: the immutable object is never rewritten
        # and unverifiable bytes are never recorded as this job's response.
        assert len(store.calls) == 1
        assert exc_info.value.code == glue_provider.GLUE_PROVIDER_UNKNOWN
        assert exc_info.value.retry_kind == "paid"
        assert recording.calls["recorded"] == []
        assert ("set_phase", "storing-response") not in recording.order

    def test_crash_between_r2_and_db_recovers_from_identical_bytes(self, recording, tmp_path):
        # The deterministic key already holds exactly this response (an earlier
        # delivery crashed after the put): metadata is recorded and no
        # different object is ever overwritten.
        store = FakeRawStore(existing="identical")
        _run_stage(recording, raw_store=store, work_dir=tmp_path / "recover")
        assert len(store.calls) == 1
        assert len(recording.calls["recorded"]) == 1
        assert ("set_phase", "storing-response") in recording.order

    def test_raw_store_failure_after_compose_is_paid_unknown(self, recording, tmp_path):
        store = FakeRawStore(fail=RuntimeError("r2 unavailable"))
        with pytest.raises(RuntimeError):
            _run_stage(recording, raw_store=store, work_dir=tmp_path / "store-fail")
        # Retried as free infrastructure work, then surfaced: with no durable
        # raw bytes the provider outcome cannot be proven, so the spine's
        # failure mapper classifies it paid and never composes again.
        assert len(store.calls) == 4
        assert recording.calls["recorded"] == []
        assert ("set_phase", "storing-response") not in recording.order

    def test_response_cap_aborts_before_overflow(self, recording, tmp_path):
        transport = FakeTransport(compose_chunks=[b"x" * 1024] * 5)
        store = FakeRawStore()
        work_dir = tmp_path / "cap-work"
        with pytest.raises(glue_jobs.GlueJobError) as exc_info:
            _run_stage(
                recording,
                transport=transport,
                raw_store=store,
                work_dir=work_dir,
                max_response_bytes=4096,
            )
        assert exc_info.value.code == glue_provider.GLUE_PROVIDER_UNKNOWN
        assert exc_info.value.retry_kind == "paid"
        assert store.calls == []
        assert recording.calls["recorded"] == []
        # No partial temp file survives an aborted stream.
        assert [p.name for p in work_dir.iterdir()] == []
        assert ("set_phase", "storing-response") not in recording.order

    def test_provider_response_too_large_type_is_paid(self):
        assert ProviderResponseTooLarge("too big").retry_kind == "paid"

    def test_response_cap_is_32_mib(self):
        assert glue_provider.MAX_RESPONSE_BYTES == 32 * 1024 * 1024

    def test_compose_connect_failure_is_free_and_not_repeated(self, recording):
        transport = FakeTransport(compose_error=ProviderNotSent("connect refused"))
        store = FakeRawStore()
        with pytest.raises(glue_jobs.GlueJobError) as exc_info:
            _run_stage(recording, transport=transport, raw_store=store)
        # A definitely-unsent compose is free to resume, but this attempt still
        # composed exactly once: no in-place repeat.
        assert exc_info.value.retry_kind == "free"
        assert len(transport.compose_calls) == 1
        assert store.calls == []

    def test_duration_over_limit_rejected_before_upload(self, recording, monkeypatch):
        job = _make_job(descriptor_json=_descriptor_json(beats=16, tempo=7.0))
        recording.state["job"] = job
        transport = FakeTransport()
        with pytest.raises(glue_jobs.GlueJobError) as exc_info:
            _run_stage(recording, transport=transport)
        assert exc_info.value.code == "GLUE_DURATION_TOO_LONG"
        assert exc_info.value.retry_kind == "none"
        assert transport.upload_calls == []
        assert transport.compose_calls == []

    def test_compose_deadline_requires_full_reserve(self, recording):
        transport = FakeTransport()
        with pytest.raises(glue_jobs.GlueJobError) as exc_info:
            _run_stage(recording, transport=transport, context=_Context(1_000_000))
        assert exc_info.value.code == glue_jobs.GLUE_EXECUTION_DEADLINE
        assert exc_info.value.retry_kind == "free"
        assert transport.compose_calls == []
        # Refused while still definitely unsent: the job never entered the
        # in-flight composing phase, so a redelivery is not "ambiguous".
        assert ("set_phase", "composing") not in recording.order
        assert recording.calls["heartbeats"][-1] == "composing"

    def test_upload_deadline_refuses_without_budget(self, recording):
        transport = FakeTransport()
        with pytest.raises(glue_jobs.GlueJobError) as exc_info:
            _run_stage(recording, transport=transport, context=_Context(500_000))
        assert exc_info.value.code == glue_jobs.GLUE_EXECUTION_DEADLINE
        assert exc_info.value.retry_kind == "free"
        assert transport.upload_calls == []
        # No in-flight provider phase was persisted for an unsent request.
        assert not any(phase == "uploading" for phase, _ in recording.calls["set_phase"])

    def test_not_sent_retries_then_succeeds(self, recording, excerpts):
        transport = FakeTransport(
            upload_errors=[ProviderNotSent("refused"), ProviderNotSent("refused")]
        )
        sleeps: list[float] = []
        events: list[tuple[str, dict]] = []
        stage = create_provider_stage(
            transport=transport,
            raw_store=FakeRawStore(),
            sleep=sleeps.append,
        )
        job = recording.state["job"]
        stage(
            job,
            object(),
            a_excerpt=excerpts("A", seconds=A_SECONDS),
            b_excerpt=excerpts("B", seconds=B_SECONDS),
            work_dir=recording.tmp_path / "retry-work",
            log_event=lambda event, **fields: events.append((event, fields)),
        )
        assert len(transport.upload_calls) == 4  # 1 A + 2 retried A + 1 B
        assert len(sleeps) == 2
        assert sleeps[0] <= 1.0
        assert sleeps[1] <= 2.0
        assert [fields["retryCount"] for name, fields in events if name == "glue.retry"] == [1, 2]

    def test_not_sent_exhausts_bounded_retries(self, recording):
        transport = FakeTransport(
            upload_errors=[ProviderNotSent("refused") for _ in range(4)]
        )
        with pytest.raises(glue_jobs.GlueJobError) as exc_info:
            _run_stage(recording, transport=transport)
        assert exc_info.value.code == glue_provider.GLUE_WORKER_FAILED
        assert exc_info.value.retry_kind == "free"
        assert len(transport.upload_calls) == 4
        assert transport.compose_calls == []

    def test_cancellation_before_upload_sends_nothing(self, recording, monkeypatch):
        real_set_phase = recording.set_phase

        def cancel_after_composing(conn, job_id, user_id, phase, **fields):
            if phase == "uploading":
                return None  # the row went inactive (cancel won)
            return real_set_phase(conn, job_id, user_id, phase, **fields)

        monkeypatch.setattr(glue_provider, "set_glue_phase", cancel_after_composing)
        transport = FakeTransport()
        # No exception: a cancelled job is a clean stop, not a failure to map.
        _run_stage(recording, transport=transport)
        assert transport.upload_calls == []
        assert transport.compose_calls == []

    def test_cancellation_before_compose_sends_nothing(self, recording, monkeypatch):
        real_set_phase = recording.set_phase

        def cancel_on_composing(conn, job_id, user_id, phase, **fields):
            if phase == "composing":
                return None
            return real_set_phase(conn, job_id, user_id, phase, **fields)

        monkeypatch.setattr(glue_provider, "set_glue_phase", cancel_on_composing)
        transport = FakeTransport()
        _run_stage(recording, transport=transport)
        # Both uploads may have happened, but no compose was ever sent.
        assert transport.compose_calls == []
        assert recording.calls["recorded"] == []

    def test_pre_request_heartbeat_failure_is_free_not_paid(self, recording, monkeypatch):
        def failing_heartbeat(conn, job, log, *, stage):
            raise RuntimeError("db down")

        monkeypatch.setattr(glue_jobs, "_heartbeat", failing_heartbeat)
        transport = FakeTransport()
        with pytest.raises(glue_jobs.GlueJobError) as exc_info:
            _run_stage(recording, transport=transport)
        # No request bytes left the process, so this is resume-free, not paid.
        assert exc_info.value.retry_kind == "free"
        assert transport.upload_calls == []
        assert transport.compose_calls == []

    @pytest.mark.parametrize(
        "error",
        [
            ProviderAmbiguous("HTTP 429", provider_status=429),
            ProviderAmbiguous("HTTP 500", provider_status=500),
            ProviderAmbiguous("read timeout"),
            ProviderAmbiguous("connection reset"),
        ],
    )
    def test_ambiguous_upload_is_paid_and_not_retried(self, recording, error):
        transport = FakeTransport(upload_errors=[error])
        with pytest.raises(glue_jobs.GlueJobError) as exc_info:
            _run_stage(recording, transport=transport)
        assert exc_info.value.code == glue_provider.GLUE_PROVIDER_UNKNOWN
        assert exc_info.value.retry_kind == "paid"
        assert len(transport.upload_calls) == 1
        assert transport.compose_calls == []

    def test_ambiguous_compose_is_paid_and_not_retried(self, recording):
        transport = FakeTransport(compose_error=ProviderAmbiguous("HTTP 503", provider_status=503))
        store = FakeRawStore()
        with pytest.raises(glue_jobs.GlueJobError) as exc_info:
            _run_stage(recording, transport=transport, raw_store=store)
        assert exc_info.value.code == glue_provider.GLUE_PROVIDER_UNKNOWN
        assert exc_info.value.retry_kind == "paid"
        assert len(transport.compose_calls) == 1
        assert store.calls == []

    def test_mid_stream_ambiguity_is_paid_and_persists_nothing(self, recording, tmp_path):
        class MidStreamTransport(FakeTransport):
            def compose(self, request):
                self.compose_calls.append(request)

                def chunks():
                    yield b"x" * 512
                    raise ProviderAmbiguous("connection reset mid-stream")

                return chunks()

        store = FakeRawStore()
        work_dir = tmp_path / "mid-stream"
        with pytest.raises(glue_jobs.GlueJobError) as exc_info:
            _run_stage(
                recording,
                transport=MidStreamTransport(),
                raw_store=store,
                work_dir=work_dir,
            )
        assert exc_info.value.code == glue_provider.GLUE_PROVIDER_UNKNOWN
        assert exc_info.value.retry_kind == "paid"
        assert store.calls == []
        assert recording.calls["recorded"] == []
        assert [p.name for p in work_dir.iterdir()] == []

    def test_empty_compose_response_is_ambiguous_not_success(self, recording, tmp_path):
        store = FakeRawStore()
        with pytest.raises(glue_jobs.GlueJobError) as exc_info:
            _run_stage(
                recording,
                transport=FakeTransport(compose_chunks=[]),
                raw_store=store,
                work_dir=tmp_path / "empty",
            )
        assert exc_info.value.retry_kind == "paid"
        assert store.calls == []
        assert recording.calls["recorded"] == []

    @pytest.mark.parametrize(
        ("error", "code"),
        [
            (ProviderEntitlement("HTTP 401", provider_status=401), "GLUE_PROVIDER_ENTITLEMENT"),
            (ProviderPlan("HTTP 422", provider_status=422), "GLUE_PROVIDER_PLAN"),
            (ProviderCopyright("copyright", provider_status=400), "GLUE_COPYRIGHT_REJECTED"),
        ],
    )
    def test_non_retryable_provider_errors(self, recording, error, code):
        transport = FakeTransport(upload_errors=[error])
        with pytest.raises(glue_jobs.GlueJobError) as exc_info:
            _run_stage(recording, transport=transport)
        assert exc_info.value.code == code
        assert exc_info.value.retry_kind == "none"
        assert len(transport.upload_calls) == 1
        assert transport.compose_calls == []

    def test_copyright_writes_negative_excerpt_cache_entry(self, recording):
        transport = FakeTransport(
            upload_errors=[ProviderCopyright("copyright", provider_status=400)]
        )
        with pytest.raises(glue_jobs.GlueJobError):
            _run_stage(recording, transport=transport)
        assert len(recording.calls["rejected"]) == 1
        rejected = recording.calls["rejected"][0]
        assert rejected["logical_key"] == "key-A"
        assert rejected["side"] == "A"
        assert rejected["rejection_code"] == "GLUE_COPYRIGHT_REJECTED"
        assert rejected["audio_sha256"] == A_SHA

    def test_cached_copyright_rejection_is_non_retryable(self, recording):
        transport = FakeTransport()
        with pytest.raises(glue_jobs.GlueJobError) as exc_info:
            _run_stage(
                recording,
                transport=transport,
                a_excerpt=_excerpt(
                    "A",
                    seconds=A_SECONDS,
                    rejection_code="GLUE_COPYRIGHT_REJECTED",
                ),
            )
        assert exc_info.value.code == "GLUE_COPYRIGHT_REJECTED"
        assert exc_info.value.retry_kind == "none"
        assert transport.upload_calls == []
        assert transport.compose_calls == []
