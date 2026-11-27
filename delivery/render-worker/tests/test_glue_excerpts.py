"""Canonical Glue excerpt construction and cache-state tests.

All audio fixtures are generated locally with numpy/soundfile; the fake asset
fetcher never touches R2 and no provider code is imported or contacted.
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from sow_render_worker import glue_excerpts
from sow_render_worker.glue_excerpts import (
    GLUE_EXCERPT_TOO_SHORT,
    SAMPLE_RATE,
    TARGET_EXCERPT_SECONDS,
    GlueAudioUnavailable,
    GlueExcerptTooShort,
    PreparedExcerpt,
    audio_unavailable_detail,
    build_excerpt,
    build_excerpt_key,
    canonical_steps_from_arrangement,
    get_excerpt,
    prepare_excerpt,
    record_constructed,
    record_failed,
    record_rejection,
    record_uploaded,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "webapp"
    / "src"
    / "test"
    / "fixtures"
    / "glue-canonical.cross_language.json"
)
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
EXCERPT_CASES: list[dict] = FIXTURE["excerpts"]
EXCERPT_IDS = [case["name"] for case in EXCERPT_CASES]

HASH_A = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"
HASH_B = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"

THREE_STEPS = [[0, 5], [10, 15], [20, 30]]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeCursor:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn
        self._row: dict | None = None

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def execute(self, sql: str, params: tuple | None = None) -> None:
        self._conn.executed.append((sql, params))
        if sql.startswith("SELECT * FROM glue_excerpts"):
            assert params is not None
            self._row = self._conn.rows.get((params[0], params[1]))
            return
        assert "INSERT INTO glue_excerpts" in sql, sql
        assert params is not None
        (
            user_id,
            logical_key,
            version,
            side,
            seconds,
            content_hash,
            steps_json,
            audio_sha256,
            song_id,
            phase,
            rejection_code,
            safe_failure_detail,
        ) = params
        row = dict(self._conn.rows.get((user_id, logical_key), {}))
        row.update(
            {
                "user_id": user_id,
                "logical_key": logical_key,
                "version": version,
                "side": side,
                "seconds": seconds,
                "content_hash": content_hash,
                "canonical_steps_json": steps_json
                or row.get("canonical_steps_json"),
                "audio_sha256": audio_sha256 or row.get("audio_sha256"),
                "elevenlabs_song_id": song_id or row.get("elevenlabs_song_id"),
                "phase": phase,
                "rejection_code": rejection_code,
                "safe_failure_detail": safe_failure_detail,
            }
        )
        self._conn.rows[(user_id, logical_key)] = row
        self._row = row

    def fetchone(self) -> dict | None:
        return self._row


class _FakeConn:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = {(row["user_id"], row["logical_key"]): dict(row) for row in rows or []}
        self.executed: list[tuple[str, tuple | None]] = []

    def cursor(self, cursor_factory=None) -> _FakeCursor:
        return _FakeCursor(self)


class _FakeAssetFetcher:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.calls: list[str] = []

    def download_audio(self, hash_prefix: str) -> str | None:
        self.calls.append(hash_prefix)
        return None if self.path is None else str(self.path)


def _excerpt_row(
    *,
    logical_key: str = "k" * 64,
    phase: str,
    side: str = "A",
    seconds: float = 12.0,
    content_hash: str = HASH_A,
    steps_json: str | None = "null",
    audio_sha256: str | None = "a" * 64,
    song_id: str | None = None,
    rejection_code: str | None = None,
) -> dict:
    return {
        "user_id": 42,
        "logical_key": logical_key,
        "version": 2,
        "side": side,
        "seconds": seconds,
        "content_hash": content_hash,
        "canonical_steps_json": steps_json,
        "audio_sha256": audio_sha256,
        "elevenlabs_song_id": song_id,
        "phase": phase,
        "rejection_code": rejection_code,
        "safe_failure_detail": None,
    }


def _write_source(
    path: Path,
    *,
    seconds: int = 30,
    rate: int = SAMPLE_RATE,
    mono: bool = False,
) -> Path:
    channels = 1 if mono else 2
    frames = np.zeros((seconds * rate, channels), dtype=np.int16)
    for second in range(seconds):
        block = slice(second * rate, (second + 1) * rate)
        frames[block, 0] = second + 1
        if not mono:
            frames[block, 1] = -(second + 1)
    sf.write(path, frames, rate, format="WAV", subtype="PCM_16")
    return path


def _second_values(path: Path) -> list[int]:
    samples, rate = sf.read(path, dtype="int16")
    assert rate == SAMPLE_RATE
    return [int(samples[index * rate, 0]) for index in range(samples.shape[0] // rate)]


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def test_side_a_extracts_arranged_tail_across_steps(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "source.wav")
    path, seconds, _ = build_excerpt(
        source_path=source, steps=THREE_STEPS, side="A", work_dir=tmp_path
    )

    assert seconds == 12.0
    samples, rate = sf.read(path, dtype="int16")
    assert rate == SAMPLE_RATE
    assert samples.shape == (12 * SAMPLE_RATE, 2)
    # Arranged playback is 20 s; the last 12 s are step 2's final 2 s
    # (source seconds 13-14) followed by all of step 3 (source 20-29).
    assert _second_values(path) == [14, 15, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30]


def test_side_b_extracts_arranged_head_across_steps(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "source.wav")
    path, seconds, _ = build_excerpt(
        source_path=source, steps=THREE_STEPS, side="B", work_dir=tmp_path
    )

    assert seconds == 12.0
    assert _second_values(path) == [1, 2, 3, 4, 5, 11, 12, 13, 14, 15, 21, 22]


def test_null_steps_uses_the_whole_recording(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "source.wav")

    a_path, a_seconds, _ = build_excerpt(
        source_path=source, steps=None, side="A", work_dir=tmp_path
    )
    b_path, b_seconds, _ = build_excerpt(
        source_path=source, steps=None, side="B", work_dir=tmp_path
    )

    assert a_seconds == 12.0
    assert b_seconds == 12.0
    assert _second_values(a_path) == list(range(19, 31))
    assert _second_values(b_path) == list(range(1, 13))


def test_short_side_uses_all_audio_and_records_actual_seconds(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "source.wav")
    steps = [[0, 2], [3, 4.5]]
    conn = _FakeConn()

    prepared = prepare_excerpt(
        conn=conn,
        user_id=42,
        side="B",
        recording_hash_prefix="rec-1",
        content_hash=HASH_B,
        steps=steps,
        asset_fetcher=_FakeAssetFetcher(source),
        work_dir=tmp_path,
    )

    assert prepared.seconds == 3.5
    assert prepared.wav_path is not None
    samples, _ = sf.read(prepared.wav_path, dtype="int16")
    assert samples.shape[0] == 3.5 * SAMPLE_RATE
    _, expected_key = build_excerpt_key(
        side="B", seconds=3.5, content_hash=HASH_B, steps=steps
    )
    assert prepared.logical_key == expected_key
    row = get_excerpt(conn, 42, expected_key)
    assert row is not None
    assert row.phase == "constructed"
    assert row.seconds == 3.5


def test_under_one_second_is_rejected_before_download(tmp_path: Path) -> None:
    fetcher = _FakeAssetFetcher(None)
    conn = _FakeConn()

    with pytest.raises(GlueExcerptTooShort) as excinfo:
        prepare_excerpt(
            conn=conn,
            user_id=42,
            side="A",
            recording_hash_prefix="rec-1",
            content_hash=HASH_A,
            steps=[[0, 0.4]],
            asset_fetcher=fetcher,
            work_dir=tmp_path,
        )

    assert excinfo.value.code == GLUE_EXCERPT_TOO_SHORT
    assert excinfo.value.side == "A"
    assert fetcher.calls == []
    assert conn.executed == []


def test_wav_is_a_canonical_44_byte_riff(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "source.wav")
    path, seconds, sha256 = build_excerpt(
        source_path=source, steps=THREE_STEPS, side="A", work_dir=tmp_path
    )
    data = path.read_bytes()

    riff, riff_size, wave = struct.unpack("<4sI4s", data[:12])
    fmt, fmt_size, audio_format, channels, rate, byte_rate, block_align, bits = (
        struct.unpack("<4sIHHIIHH", data[12:36])
    )
    data_tag, data_size = struct.unpack("<4sI", data[36:44])
    frames = seconds * SAMPLE_RATE

    assert riff == b"RIFF"
    assert riff_size == len(data) - 8
    assert wave == b"WAVE"
    assert fmt == b"fmt "
    assert fmt_size == 16
    assert audio_format == 1
    assert channels == 2
    assert rate == SAMPLE_RATE
    assert block_align == 4
    assert bits == 16
    assert byte_rate == SAMPLE_RATE * 4
    assert data_tag == b"data"
    assert data_size == frames * 4
    assert len(data) == 44 + frames * 4
    assert hashlib.sha256(data).hexdigest() == sha256


def test_identical_source_produces_identical_bytes(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "source.wav")

    first_path, _, first_sha = build_excerpt(
        source_path=source, steps=THREE_STEPS, side="B", work_dir=tmp_path
    )
    second_path, _, second_sha = build_excerpt(
        source_path=source, steps=THREE_STEPS, side="B", work_dir=tmp_path
    )

    assert first_sha == second_sha
    assert first_path.read_bytes() == second_path.read_bytes()


def test_mono_source_is_duplicated_to_stereo(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "mono.wav", seconds=20, rate=22_050, mono=True)
    path, seconds, _ = build_excerpt(
        source_path=source, steps=None, side="B", work_dir=tmp_path
    )

    samples, rate = sf.read(path, dtype="int16")
    assert seconds == 12.0
    assert rate == SAMPLE_RATE
    assert samples.shape == (int(TARGET_EXCERPT_SECONDS * SAMPLE_RATE), 2)
    np.testing.assert_array_equal(samples[:, 0], samples[:, 1])


# ---------------------------------------------------------------------------
# Logical identity
# ---------------------------------------------------------------------------


def test_fixture_shape_is_shared_with_the_webapp() -> None:
    assert EXCERPT_CASES, "cross-language fixture has no excerpt cases"
    assert FIXTURE["descriptors"], "#314 must not remove descriptor cases"


@pytest.mark.parametrize("case", EXCERPT_CASES, ids=EXCERPT_IDS)
def test_excerpt_key_matches_shared_fixture(case: dict) -> None:
    payload = case["input"]
    canonical, key = build_excerpt_key(
        side=payload["side"],
        seconds=payload["seconds"],
        content_hash=payload["contentHash"],
        steps=payload["steps"],
    )

    assert canonical == case["canonicalJson"]
    assert key == case["key"]
    assert len(key) == 64 and key == key.lower()


def test_excerpt_key_rejects_invalid_side_and_empty_hash() -> None:
    with pytest.raises(ValueError):
        build_excerpt_key(side="C", seconds=12, content_hash=HASH_A, steps=None)
    with pytest.raises(ValueError):
        build_excerpt_key(side="A", seconds=12, content_hash="", steps=None)


def test_null_steps_differs_from_a_full_range() -> None:
    _, null_key = build_excerpt_key(
        side="A", seconds=12, content_hash=HASH_A, steps=None
    )
    _, range_key = build_excerpt_key(
        side="A", seconds=12, content_hash=HASH_A, steps=[[0, 12]]
    )
    assert null_key != range_key


def test_canonical_steps_from_arrangement_preserves_playback_order() -> None:
    raw = json.dumps(
        {
            "version": 1,
            "steps": [
                {
                    "sectionIndex": 1,
                    "label": "chorus",
                    "startSeconds": 10,
                    "endSeconds": 20,
                    "manualBounds": True,
                },
                {
                    "sectionIndex": 0,
                    "label": "verse",
                    "startSeconds": 0,
                    "endSeconds": 10,
                    "manualBounds": True,
                },
            ],
        }
    )
    sections = json.dumps(
        [
            {"label": "verse", "start": 0, "end": 10},
            {"label": "chorus", "start": 10, "end": 20},
        ]
    )
    beats = json.dumps([0, 10, 20])

    steps = canonical_steps_from_arrangement(raw, sections, beats, 30)

    assert steps == [[10, 20], [0, 10]]


def test_canonical_steps_from_null_arrangement_stays_null() -> None:
    assert canonical_steps_from_arrangement(None, None, None, 30) is None


def test_stale_arrangement_raises_the_repair_message() -> None:
    raw = json.dumps(
        {
            "version": 1,
            "steps": [
                {
                    "sectionIndex": 0,
                    "label": "verse",
                    "startSeconds": 0,
                    "endSeconds": 9,
                }
            ],
        }
    )
    sections = json.dumps([{"label": "verse", "start": 0, "end": 10}])
    beats = json.dumps([0, 5, 10])

    with pytest.raises(ValueError):
        canonical_steps_from_arrangement(raw, sections, beats, 10)


# ---------------------------------------------------------------------------
# Cache states
# ---------------------------------------------------------------------------


def test_uploaded_cache_skips_download_and_construction(tmp_path: Path) -> None:
    steps = [[0, 20], [20, 24]]
    _, logical_key = build_excerpt_key(
        side="A", seconds=12, content_hash=HASH_A, steps=steps
    )
    conn = _FakeConn(
        [_excerpt_row(logical_key=logical_key, phase="uploaded", song_id="song-123")]
    )
    fetcher = _FakeAssetFetcher(None)

    prepared = prepare_excerpt(
        conn=conn,
        user_id=42,
        side="A",
        recording_hash_prefix="rec-1",
        content_hash=HASH_A,
        steps=steps,
        asset_fetcher=fetcher,
        work_dir=tmp_path,
    )

    assert isinstance(prepared, PreparedExcerpt)
    assert prepared.cached is True
    assert prepared.wav_path is None
    assert prepared.elevenlabs_song_id == "song-123"
    assert fetcher.calls == []
    assert not any("INSERT INTO glue_excerpts" in sql for sql, _ in conn.executed)


def test_rejected_cache_is_a_negative_entry(tmp_path: Path) -> None:
    steps = [[0, 20], [20, 24]]
    _, logical_key = build_excerpt_key(
        side="A", seconds=12, content_hash=HASH_A, steps=steps
    )
    conn = _FakeConn(
        [
            _excerpt_row(
                logical_key=logical_key,
                phase="rejected",
                rejection_code="GLUE_COPYRIGHT_REJECTED",
            )
        ]
    )
    fetcher = _FakeAssetFetcher(None)

    prepared = prepare_excerpt(
        conn=conn,
        user_id=42,
        side="A",
        recording_hash_prefix="rec-1",
        content_hash=HASH_A,
        steps=steps,
        asset_fetcher=fetcher,
        work_dir=tmp_path,
    )

    assert prepared.cached is True
    assert prepared.rejection_code == "GLUE_COPYRIGHT_REJECTED"
    assert prepared.elevenlabs_song_id is None
    assert fetcher.calls == []


def test_failed_cache_is_not_a_negative_entry(tmp_path: Path) -> None:
    steps = [[0, 20], [20, 24]]
    _, logical_key = build_excerpt_key(
        side="A", seconds=12, content_hash=HASH_A, steps=steps
    )
    conn = _FakeConn([_excerpt_row(logical_key=logical_key, phase="failed")])
    source = _write_source(tmp_path / "source.wav")
    fetcher = _FakeAssetFetcher(source)

    prepared = prepare_excerpt(
        conn=conn,
        user_id=42,
        side="A",
        recording_hash_prefix="rec-1",
        content_hash=HASH_A,
        steps=steps,
        asset_fetcher=fetcher,
        work_dir=tmp_path,
    )

    assert prepared.cached is False
    assert prepared.wav_path is not None
    assert fetcher.calls == ["rec-1"]
    row = get_excerpt(conn, 42, logical_key)
    assert row is not None and row.phase == "constructed"


def test_cache_state_transitions_keep_audit_fields() -> None:
    conn = _FakeConn()
    key = "k" * 64

    constructed = record_constructed(
        conn,
        42,
        logical_key=key,
        side="B",
        seconds=7.5,
        content_hash=HASH_B,
        canonical_steps_json="[[0,7.5]]",
        audio_sha256="a" * 64,
    )
    assert constructed.phase == "constructed"
    assert constructed.audio_sha256 == "a" * 64

    uploaded = record_uploaded(
        conn,
        42,
        logical_key=key,
        side="B",
        seconds=7.5,
        content_hash=HASH_B,
        canonical_steps_json="[[0,7.5]]",
        audio_sha256="a" * 64,
        elevenlabs_song_id="song-9",
    )
    assert uploaded.phase == "uploaded"
    assert uploaded.elevenlabs_song_id == "song-9"
    assert uploaded.audio_sha256 == "a" * 64

    failed = record_failed(
        conn,
        42,
        logical_key=key,
        side="B",
        seconds=7.5,
        content_hash=HASH_B,
        canonical_steps_json="[[0,7.5]]",
        audio_sha256="a" * 64,
        safe_failure_detail="staging failed",
    )
    assert failed.phase == "failed"
    assert failed.elevenlabs_song_id == "song-9"
    assert failed.rejection_code is None

    rejected = record_rejection(
        conn,
        42,
        logical_key=key,
        side="B",
        seconds=7.5,
        content_hash=HASH_B,
        canonical_steps_json="[[0,7.5]]",
        audio_sha256="a" * 64,
        rejection_code="GLUE_COPYRIGHT_REJECTED",
        safe_failure_detail="copyright",
    )
    assert rejected.phase == "rejected"
    assert rejected.rejection_code == "GLUE_COPYRIGHT_REJECTED"


def test_record_rejection_rejects_other_codes() -> None:
    with pytest.raises(ValueError):
        record_rejection(
            _FakeConn(),
            42,
            logical_key="k" * 64,
            side="A",
            seconds=2,
            content_hash=HASH_A,
            canonical_steps_json=None,
            audio_sha256="a" * 64,
            rejection_code="GLUE_PROVIDER_UNKNOWN",
        )


def test_prepare_records_constructed_identity_and_checksum(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "source.wav")
    steps = [[0, 5], [10, 15]]
    conn = _FakeConn()

    prepared = prepare_excerpt(
        conn=conn,
        user_id=42,
        side="A",
        recording_hash_prefix="rec-1",
        content_hash=HASH_A,
        steps=steps,
        asset_fetcher=_FakeAssetFetcher(source),
        work_dir=tmp_path,
    )

    assert prepared.seconds == 10.0
    assert prepared.canonical_steps_json == "[[0,5],[10,15]]"
    assert prepared.wav_path is not None
    data = prepared.wav_path.read_bytes()
    assert prepared.audio_sha256 == hashlib.sha256(data).hexdigest()
    _, expected_key = build_excerpt_key(
        side="A", seconds=10, content_hash=HASH_A, steps=steps
    )
    assert prepared.logical_key == expected_key
    row = get_excerpt(conn, 42, expected_key)
    assert row is not None
    assert row.phase == "constructed"
    assert row.audio_sha256 == prepared.audio_sha256
    assert row.canonical_steps_json == "[[0,5],[10,15]]"


def test_null_steps_cache_check_skips_construction_after_decode(tmp_path: Path) -> None:
    source = _write_source(tmp_path / "source.wav")
    _, logical_key = build_excerpt_key(
        side="B", seconds=12, content_hash=HASH_B, steps=None
    )
    conn = _FakeConn(
        [_excerpt_row(logical_key=logical_key, phase="uploaded", song_id="song-null")]
    )

    prepared = prepare_excerpt(
        conn=conn,
        user_id=42,
        side="B",
        recording_hash_prefix="rec-1",
        content_hash=HASH_B,
        steps=None,
        asset_fetcher=_FakeAssetFetcher(source),
        work_dir=tmp_path,
    )

    assert prepared.cached is True
    assert prepared.elevenlabs_song_id == "song-null"
    assert prepared.wav_path is None
    assert not any("INSERT INTO glue_excerpts" in sql for sql, _ in conn.executed)


def test_decode_failure_names_the_side_in_its_detail(tmp_path, monkeypatch):
    source = tmp_path / "recording.flac"
    source.write_bytes(b"not audio")

    def unavailable(*args, **kwargs):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(glue_excerpts.subprocess, "run", unavailable)
    with pytest.raises(GlueAudioUnavailable) as caught:
        build_excerpt(
            source_path=source, steps=None, side="B", work_dir=tmp_path
        )
    assert (caught.value.cause, caught.value.side) == ("decode-failed", "B")
    assert audio_unavailable_detail(caught.value) == "decode-failed side=B"
