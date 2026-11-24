"""Unit tests for the Treblo Glue POC — no network, no database, no R2."""

from __future__ import annotations

import json

import numpy as np
import pytest

import gen_glue_treblo as glue_mod
from gen_glue_treblo import (
    CHANNELS,
    CREDITS_PER_REQUEST,
    ENV_FILE,
    SR,
    TAG_FILE,
    GlueSpec,
    Montage,
    build_montage,
    evaluate_status,
    glue_specs,
    inpaint_body,
    load_tag_vocabulary,
    report_html,
    resolve_section,
    response_key,
    splice,
    treblo_generate_cached,
    validate_tags,
)

TEMPO = 86.13281


def _samples(seconds: float, offset: float = 0.0) -> np.ndarray:
    """A deterministic, distinguishable stereo side: a ramp, so slicing is provable."""
    frames = round(seconds * SR)
    ramp = np.linspace(offset, offset + 0.5, frames, dtype=np.float32)
    return np.ascontiguousarray(np.stack([ramp, ramp], axis=1))


def _montage(upload_seconds: float = 12.0, beats: int = 4) -> tuple[Montage, GlueSpec]:
    spec = glue_specs([beats], TEMPO)[0]
    a = _samples(upload_seconds + 8.0, 0.0)
    b = _samples(upload_seconds + 8.0, 0.5)
    return build_montage(a, b, spec, upload_seconds), spec


def _body(montage: Montage, **overrides) -> dict:
    kwargs = {
        "tags": ("worship", "praise & worship"),
        "instrumental": True,
        "lyrics": None,
        "seed": 7,
        "prompt_strength": 2.0,
        "balance_strength": 0.7,
    }
    kwargs.update(overrides)
    return inpaint_body("UPLOAD_ID", montage, **kwargs)


def _good_metrics(**overrides) -> dict:
    metrics = {
        "glue_rms_db": -18.0,
        "section_delta_ms": 0.0,
        "padded_ms": 0.0,
        "trimmed_ms": 0.0,
        "a_residual_db": 0.1,
        "b_residual_db": -0.2,
        "joint": {
            "a": {"step_db": 0.4, "jump_ratio": 1.2},
            "b": {"step_db": -0.3, "jump_ratio": 0.9},
        },
    }
    metrics.update(overrides)
    return metrics


def test_montage_geometry() -> None:
    montage, spec = _montage()
    frames = round(12.0 * SR)

    assert spec.target_seconds == pytest.approx(4 * 60.0 / TEMPO, abs=1e-9)
    assert montage.hole_start == frames
    assert montage.hole_end - montage.hole_start == glue_mod.seconds_to_samples(spec.target_seconds)
    assert montage.hole_end - montage.hole_start == 122_880, "4 beats at 86.13281 BPM"
    assert np.all(montage.samples[montage.hole_start : montage.hole_end] == 0.0)
    assert montage.side_seconds == 12.0
    assert montage.seconds == pytest.approx(2 * 12.0 + spec.target_seconds, abs=1e-6)
    assert montage.seconds == pytest.approx(26.786, abs=2e-3)
    # A's last 12 s and B's first 12 s sit either side of the hole, sample-identical
    a_side = montage.samples[: montage.hole_start]
    b_side = montage.samples[montage.hole_end :]
    assert a_side.shape[0] == b_side.shape[0] == frames
    # A is its last 12 s of a 20 s ramp from 0.0 (so it resumes at 0.2), B starts its 0.5 ramp
    assert float(a_side[0, 0]) == pytest.approx(0.2, abs=1e-3)
    assert float(b_side[0, 0]) == pytest.approx(0.5, abs=1e-3)
    assert float(a_side[-1, 0]) == pytest.approx(0.5, abs=1e-3), "A's side ends where A ends"
    assert montage.wav_bytes == montage.samples.size * glue_mod.SAMPLE_WIDTH + 44


def test_montage_rejects_short_side() -> None:
    spec = glue_specs([4], TEMPO)[0]
    side = _samples(5.0)
    with pytest.raises(ValueError) as excinfo:
        build_montage(side, side, spec, 12.0)
    message = str(excinfo.value)
    assert "exceeds an arranged side" in message
    assert "5.00 s / 5.00 s" in message


def test_montage_rejects_oversize(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(glue_mod, "MAX_UPLOAD_BYTES", 1_000_000)
    spec = glue_specs([4], TEMPO)[0]
    side = _samples(20.0)
    with pytest.raises(ValueError) as excinfo:
        build_montage(side, side, spec, 12.0)
    message = str(excinfo.value)
    assert "40 MB upload limit" in message
    assert "exceeds" in message


def test_glue_specs_exact() -> None:
    spec = glue_specs([4], TEMPO)[0]
    assert spec.target_seconds == pytest.approx(4 * 60.0 / TEMPO, abs=1e-9)
    assert spec.target_samples == round(spec.target_seconds * SR)
    assert spec.target_samples == glue_mod.seconds_to_samples(spec.target_seconds)
    assert glue_specs([1], TEMPO)[0].target_seconds == pytest.approx(60.0 / TEMPO)

    with pytest.raises(ValueError):
        glue_specs([0], TEMPO)
    with pytest.raises(ValueError):
        glue_specs([4], 0.0)
    with pytest.raises(ValueError) as excinfo:
        glue_specs([1], 900.0)
    assert "0.1 s section floor" in str(excinfo.value)


def test_inpaint_body_keys() -> None:
    montage, spec = _montage()
    body = _body(montage)

    assert set(body) == {
        "audio_upload_id",
        "tags",
        "sections",
        "selection_crop",
        "num_songs",
        "seed",
        "prompt_strength",
        "balance_strength",
        "output_format",
        "instrumental",
    }
    assert body["audio_upload_id"] == "UPLOAD_ID"
    assert body["sections"] == [[12.0, pytest.approx(12.0 + spec.target_seconds, abs=1e-6)]]
    assert body["selection_crop"] is True
    assert body["num_songs"] == 1
    assert body["tags"] == ["worship", "praise & worship"], "tag order is preserved"
    assert body["instrumental"] is True
    assert body["seed"] == 7
    assert body["prompt_strength"] == 2.0
    assert body["balance_strength"] == 0.7
    assert body["output_format"] == "flac"
    assert "audio_url" not in body and "audio_base64" not in body


def test_inpaint_body_lyrics() -> None:
    montage, _spec = _montage()
    body = _body(montage, instrumental=False, lyrics="[Glue]\nOoh")
    assert body["lyrics"] == "[Glue]\nOoh"
    assert "instrumental" not in body

    with pytest.raises(ValueError):
        _body(montage, instrumental=False, lyrics="   ")


def test_validate_tags() -> None:
    vocabulary = load_tag_vocabulary()
    assert "worship" in vocabulary and "praise & worship" in vocabulary
    validate_tags(("worship", "praise & worship"), vocabulary)

    with pytest.raises(SystemExit) as excinfo:
        validate_tags(("worship band",), vocabulary)
    message = str(excinfo.value)
    assert "worship band" in message
    assert TAG_FILE.name in message

    with pytest.raises(SystemExit):
        validate_tags(("worship",) * 65, vocabulary)
    with pytest.raises(SystemExit):
        validate_tags((), vocabulary)


def test_resolve_section_crop() -> None:
    montage, spec = _montage()
    returned = _samples(spec.target_seconds)
    glue, info = resolve_section(returned, montage, spec)

    assert info["geometry"] == "crop"
    assert glue.shape[0] == spec.target_samples
    assert info["section_delta_ms"] == pytest.approx(0.0, abs=1.0)
    assert info["padded_ms"] == 0.0 and info["trimmed_ms"] == 0.0
    assert np.array_equal(glue, returned)


def test_resolve_section_montage_fallback() -> None:
    spec = glue_specs([4], TEMPO)[0]
    samples = _samples(2 * 12.0 + spec.target_seconds)
    montage = Montage(
        samples=samples,
        hole_start=round(12.0 * SR),
        hole_end=round(12.0 * SR) + spec.target_samples,
        side_seconds=12.0,
    )
    glue, info = resolve_section(samples.copy(), montage, spec)

    assert info["geometry"] == "montage"
    assert glue.shape[0] == spec.target_samples
    assert np.array_equal(glue, samples[montage.hole_start : montage.hole_end])
    assert info["section_delta_ms"] == 0.0, "the sliced hole is exact by construction"


def test_resolve_section_short_pads() -> None:
    montage, spec = _montage()
    short = spec.target_samples - round(0.04 * SR)
    returned = np.zeros((short, CHANNELS), dtype=np.float32)
    glue, info = resolve_section(returned, montage, spec)

    assert info["geometry"] == "crop"
    assert glue.shape[0] == spec.target_samples
    assert info["padded_ms"] == pytest.approx(40.0, abs=0.1)
    assert info["section_delta_ms"] == pytest.approx(-40.0, abs=0.1)

    status, reasons = evaluate_status({**info, "glue_rms_db": -20.0}, splice_ok=True)
    assert status == "check"
    assert any("padding" in reason for reason in reasons)


def test_resolve_section_rejects_unknown_length() -> None:
    montage, spec = _montage()
    returned = _samples(spec.target_seconds / 2.0)
    with pytest.raises(RuntimeError) as excinfo:
        resolve_section(returned, montage, spec)
    message = str(excinfo.value)
    assert "1.393" in message, "the returned length is named"
    assert "2.786" in message, "the requested length is named"


def test_response_key_varies() -> None:
    montage, _spec = _montage()
    body = _body(montage)
    key = response_key(body, "beats-4")

    assert key == response_key(json.loads(json.dumps(body)), "beats-4")
    assert key != response_key({**body, "seed": 8}, "beats-4")
    assert key != response_key(body, "beats-4-t2")


def test_generate_cached_reuses_bytes(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    montage, _spec = _montage()
    body = _body(montage)
    key = response_key(body, "beats-4")
    (tmp_path / f"{key}.bin").write_bytes(b"PAID-SECTION")

    monkeypatch.setattr(glue_mod, "treblo_submit", lambda _body: pytest.fail("resubmitted"))
    monkeypatch.setattr(glue_mod, "treblo_wait", lambda *_a, **_k: pytest.fail("polled"))

    raw, _meta, reused = treblo_generate_cached(body, "beats-4", tmp_path, False)
    assert raw == b"PAID-SECTION"
    assert reused is True


def test_generate_cached_resumes_saved_task(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    montage, _spec = _montage()
    body = _body(montage)
    key = response_key(body, "beats-4")
    (tmp_path / f"{key}.request.json").write_text(json.dumps({"task_id": "task-9"}))

    waited: list[str] = []

    def fake_wait(task_id: str, **_kwargs) -> dict:
        waited.append(task_id)
        return {"song_paths": ["https://cdn.example/section.flac"], "model_version": "v2"}

    monkeypatch.setattr(glue_mod, "treblo_wait", fake_wait)
    monkeypatch.setattr(glue_mod, "treblo_submit", lambda _body: pytest.fail("resubmitted"))

    def fake_download(url: str, dest) -> bytes:
        assert url == "https://cdn.example/section.flac"
        dest.write_bytes(b"SECTION")
        return b"SECTION"

    monkeypatch.setattr(glue_mod, "treblo_download", fake_download)

    raw, meta, reused = treblo_generate_cached(body, "beats-4", tmp_path, False)
    assert waited == ["task-9"], "the saved task is polled, never resubmitted"
    assert raw == b"SECTION"
    assert reused is False
    assert meta["task_id"] == "task-9"
    assert meta["model_version"] == "v2"
    assert (tmp_path / f"{key}.bin").is_file()
    assert (tmp_path / f"{key}.meta.json").is_file()


def test_status_parses_json_string_and_object(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    monkeypatch.setenv("TREBLO_API_KEY", "test-key")

    class FakeResponse:
        status_code = 200

        def __init__(self, body: str) -> None:
            self.text = body

    bodies = iter(['"GENERATING"', '{"status": "SUCCESS"}', "TRANSCRIBE_STAGE_1", ""])

    def fake_get(*_args, **_kwargs):
        return FakeResponse(next(bodies))

    monkeypatch.setattr(requests, "get", fake_get)
    # the documented shapes: a JSON string, then an object with include_alignment
    assert glue_mod.treblo_status("task-1") == "GENERATING"
    assert glue_mod.treblo_status("task-1") == "SUCCESS"
    # a bare word and an empty body both mean "not finished", never a failed take
    assert glue_mod.treblo_status("task-1") == "TRANSCRIBE_STAGE_1"
    assert glue_mod.treblo_status("task-1") == "PENDING"


def test_fatal_from_response() -> None:
    credits = glue_mod._fatal_from_response(402, "no credits", "inpaint")
    assert isinstance(credits, glue_mod.ApiFatalError)
    assert "credits" in str(credits)

    toobig = glue_mod._fatal_from_response(413, "too large", "upload")
    assert isinstance(toobig, glue_mod.ApiFatalError)
    assert "40 MB" in str(toobig)

    rejected = glue_mod._fatal_from_response(422, '{"detail":"bad tag"}', "inpaint")
    assert isinstance(rejected, glue_mod.ApiFatalError)
    assert "bad tag" in str(rejected)

    key = glue_mod._fatal_from_response(401, "unauthorized", "inpaint")
    assert isinstance(key, glue_mod.ApiFatalError)
    assert "TREBLO_API_KEY" in str(key)
    assert str(ENV_FILE) in str(key)

    other = glue_mod._fatal_from_response(500, "boom", "inpaint")
    assert isinstance(other, glue_mod.ApiFatalError)
    assert "500" in str(other)


def test_evaluate_status_rules() -> None:
    assert evaluate_status({"glue_rms_db": -80.0}, splice_ok=True)[0] == "failed"
    assert evaluate_status(_good_metrics(), splice_ok=False)[0] == "failed"
    assert evaluate_status(_good_metrics(), splice_ok=True) == ("ok", [])

    stepped = _good_metrics()
    stepped["joint"] = {"a": {"step_db": 4.0, "jump_ratio": 1.0}, "b": {"step_db": 0.0}}
    status, reasons = evaluate_status(stepped, splice_ok=True)
    assert status == "check"
    assert any("A joint level step" in reason for reason in reasons)

    delta = evaluate_status(_good_metrics(section_delta_ms=150.0), splice_ok=True)
    assert delta[0] == "check"
    assert any("section length off by" in reason for reason in delta[1])


def _minimal_index() -> dict:
    take = {
        "id": "j1-beats-4",
        "take": "beats-4",
        "label": "beats-4 — 4 beats",
        "beats": 4,
        "seed": 0,
        "status": "ok",
        "target_seconds": 2.7865,
        "glue_seconds": 2.7865,
        "summary": "seed 0 · section crop",
        "montage": {
            "file": "junctions/j1/montages/beats-4.flac",
            "seconds": 26.7865,
            "side_seconds": 12.0,
            "hole_seconds": 2.7865,
            "sha256": "ab" * 32,
            "audio_upload_id": "upload-1",
        },
        "metrics": {
            "returned_seconds": 2.7865,
            "section_delta_ms": 0.0,
            "padded_ms": 0.0,
            "trimmed_ms": 0.0,
            "geometry": "crop",
            "glue_seconds": 2.7865,
            "glue_rms_db": -18.0,
            "nonfinite": False,
            "joint": {"a": {"step_db": 0.2, "jump_ratio": 1.1}, "b": {"step_db": -0.1}},
            "edge_gain_db": [0.5, -0.5],
            "edge_gain_requested_db": [0.5, -0.5],
            "edge_gain_clamped": [False, False],
            "edge_limit_db": 18.0,
            "a_residual_db": 0.1,
            "b_residual_db": -0.1,
            "peak": 0.8,
            "peak_scaled": False,
            "task_id": "0f1e2d3c4b5a",
            "model_version": "treblo-v2-test",
            "credits": CREDITS_PER_REQUEST,
        },
        "files": {
            "glue": "junctions/j1/glues/beats-4.flac",
            "generation": "junctions/j1/glues/beats-4-section.flac",
            "seam": "junctions/j1/seams/beats-4.flac",
            "full": "junctions/j1/full/beats-4.flac",
        },
        "reasons": [],
        "error": None,
    }
    return {
        "version": 1,
        "run": "run1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "engine": "treblo",
        "endpoint": glue_mod.ENDPOINT,
        "songset_id": "hbWfCccGxN_JLQGBEqmdu",
        "model_version": "treblo-v2-test",
        "tags": ["worship", "praise & worship"],
        "instrumental": True,
        "prompt_strength": 2.0,
        "balance_strength": 0.7,
        "selection_crop": True,
        "output_format": "flac",
        "upload_seconds": 12.0,
        "beats": [4],
        "credits_estimate": CREDITS_PER_REQUEST,
        "credits_spent": CREDITS_PER_REQUEST,
        "items": [
            {
                "position": 1,
                "title": "A song",
                "song_id": "song-a",
                "hash_prefix": "aaaaaaaa",
                "arranged_seconds": 146.51,
                "tempo_bpm": 68.0,
                "key": "D major",
                "steps": 5,
                "file": "context/song-01-aaaaaaaa.flac",
            }
        ],
        "junctions": [
            {
                "id": "j1",
                "index": 1,
                "from": {"title": "A song"},
                "to": {"title": "B song"},
                "tempo_bpm": TEMPO,
                "beat_seconds": 0.69664,
                "montage": take["montage"],
                "baseline": {
                    "id": "j1-baseline",
                    "mode": "gap",
                    "gap_beats": 2.0,
                    "gap_seconds": 1.39328,
                    "crossfade_seconds": 0.0,
                    "status": "ok",
                    "seconds": 325.21,
                    "files": {
                        "seam": "junctions/j1/seams/baseline.flac",
                        "full": "junctions/j1/full/baseline.flac",
                    },
                },
                "takes": [take],
            }
        ],
        "full_set": {
            "baseline": {
                "id": "full-set-baseline",
                "label": "full set — baseline",
                "status": "ok",
                "seconds": 326.6,
                "files": {"full": "full-set/baseline.flac"},
            },
            "takes": [],
            "skipped": "junction subset selected (--junctions)",
        },
    }


def test_report_html_contains_take() -> None:
    page = report_html(_minimal_index())

    assert "j1-beats-4" in page
    assert "engine treblo" in page
    assert "tags worship, praise &amp; worship" in page
    assert "junctions/j1/glues/beats-4.flac" in page
    assert "junctions/j1/full/beats-4.flac" in page
    assert "100/100 credits" in page
    assert "task 0f1e2d3c" in page


def test_splice_preserves_songs() -> None:
    rng = np.random.default_rng(0)
    a = rng.standard_normal((997, CHANNELS)).astype(np.float32)
    glue = rng.standard_normal((122_884, CHANNELS)).astype(np.float32)
    b = rng.standard_normal((1503, CHANNELS)).astype(np.float32)

    stitched = splice(a, glue, b)
    assert stitched.shape[0] == a.shape[0] + glue.shape[0] + b.shape[0]
    assert np.array_equal(stitched[: a.shape[0]], a)
    assert np.array_equal(stitched[a.shape[0] : a.shape[0] + glue.shape[0]], glue)
    assert np.array_equal(stitched[-b.shape[0] :], b)


def test_dry_run_writes_nothing(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import requests

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(glue_mod, "DRY_RUN_ITEMS", ((146510, 68.0), (177310, TEMPO)))

    def boom(*_args, **_kwargs):
        raise AssertionError("the dry run must not touch the network")

    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr(requests, "get", boom)

    assert glue_mod.main(["--dry-run", "--dry-run-items", "2", "--tags", "worship"]) == 0
    printed = capsys.readouterr().out
    assert f"est. credits: 1 × {CREDITS_PER_REQUEST} = {CREDITS_PER_REQUEST}" in printed
    assert '"sections"' in printed
    assert '"selection_crop": true' in printed
    assert not list(tmp_path.iterdir()), "the dry run must not write anything"


def test_upload_is_fresh_respects_the_api_lifetime() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    fresh = {"uploaded_at": (now - timedelta(hours=1)).isoformat(timespec="seconds")}
    stale = {"uploaded_at": (now - timedelta(hours=23)).isoformat(timespec="seconds")}
    assert glue_mod._upload_is_fresh(fresh, now=now)
    # the id is only good for "about 24 hours"; re-upload well before that
    assert not glue_mod._upload_is_fresh(stale, now=now)
    # an entry we cannot date is not reusable: a dead id would fail the paid submit
    assert not glue_mod._upload_is_fresh({}, now=now)
    assert not glue_mod._upload_is_fresh({"uploaded_at": "not a date"}, now=now)
    naive = {"uploaded_at": (now - timedelta(hours=1)).replace(tzinfo=None).isoformat()}
    assert glue_mod._upload_is_fresh(naive, now=now)


def test_upload_reuploads_a_stale_id(tmp_path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from datetime import UTC, datetime, timedelta

    _, sha = glue_mod.pcm16_and_sha(np.zeros((glue_mod.SR, 2), dtype=np.float32))
    stale = (datetime.now(UTC) - timedelta(hours=30)).isoformat(timespec="seconds")
    glue_mod.save_uploads(
        tmp_path,
        {sha: {"audio_upload_id": "old", "seconds": 1.0, "format": "wav", "uploaded_at": stale}},
    )
    monkeypatch.setattr(glue_mod, "treblo_post_upload", lambda *_a: ("new", "wav"))

    entry = glue_mod.treblo_upload(
        np.zeros((glue_mod.SR, 2), dtype=np.float32), tmp_path, False, "probe"
    )
    assert entry["audio_upload_id"] == "new"
    assert "past the upload lifetime" in capsys.readouterr().out
    assert glue_mod.load_uploads(tmp_path)[sha]["audio_upload_id"] == "new"


def test_submit_survives_a_renamed_task_id(monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    monkeypatch.setenv("TREBLO_API_KEY", "test-key")

    class FakeResponse:
        status_code = 200

        def __init__(self, body: str) -> None:
            self.text = body

        def json(self):
            return json.loads(self.text)

    for body, expected in (
        ('{"task_id": "abc"}', "abc"),
        ('{"id": "def"}', "def"),
        ('{"job_id": "ghi"}', "ghi"),
    ):
        monkeypatch.setattr(requests, "post", lambda *_a, _b=body, **_k: FakeResponse(_b))
        assert glue_mod.treblo_submit({}) == expected

    # a charged submit with no id anywhere must still name the body, never raise KeyError
    monkeypatch.setattr(requests, "post", lambda *_a, **_k: FakeResponse('{"status": "queued"}'))
    with pytest.raises(glue_mod.ApiFatalError) as excinfo:
        glue_mod.treblo_submit({})
    assert "task id" in str(excinfo.value)
    assert "queued" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Upload retries, the content guard, and the rotating proxy pool
# ---------------------------------------------------------------------------

# The recorded 524 body (Cloudflare edge gave up on the origin) is no longer on disk — the later
# 422 run overwrote output/treblo-glue/test_arrangement/failure.json. This is the same shape: an
# HTML error page, not JSON, which is what makes a 524 unparseable as an upload response.
CLOUDFLARE_524 = (
    "<!DOCTYPE html>\n<html>\n<head><title>api.treblo.com | 524: A timeout occurred</title></head>\n"
    "<body><h1>Error 524</h1><p>A timeout occurred</p></body>\n</html>"
)
CONTENT_GUARD_422 = '{"detail":"This upload matches existing audio and cannot be used."}'


class _Resp:
    def __init__(self, status_code: int, text: str, content: bytes = b"") -> None:
        self.status_code, self.text = status_code, text
        self.content = content

    def json(self):
        return json.loads(self.text)


def test_upload_retries_a_cloudflare_524(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOW_YOUTUBE_PROXY", raising=False)
    answers = [_Resp(524, CLOUDFLARE_524), _Resp(200, '{"audio_upload_id": "up-1"}')]
    calls: list[tuple] = []

    def post(path, mime, proxies):
        calls.append((path, mime, proxies))
        return answers[len(calls) - 1]

    sleeps: list[float] = []
    result = glue_mod.treblo_post_upload(
        tmp_path / "montage.wav", tmp_path / "montage.flac", post=post, sleep=sleeps.append
    )
    assert result == ("up-1", "wav")
    assert len(calls) == 2
    assert sleeps == [glue_mod.UPLOAD_RETRY_SLEEPS[0]]


def test_upload_retries_a_connection_error(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import requests

    monkeypatch.delenv("SOW_YOUTUBE_PROXY", raising=False)
    calls: list[tuple] = []

    def post(path, mime, proxies):
        calls.append((path, mime, proxies))
        if len(calls) == 1:
            raise requests.exceptions.ConnectionError("boom")
        return _Resp(200, '{"audio_upload_id": "up-2"}')

    sleeps: list[float] = []
    result = glue_mod.treblo_post_upload(
        tmp_path / "montage.wav", tmp_path / "montage.flac", post=post, sleep=sleeps.append
    )
    assert result == ("up-2", "wav")
    assert len(calls) == 2
    assert sleeps == [glue_mod.UPLOAD_RETRY_SLEEPS[0]]


def test_upload_gives_up_after_the_retry_budget(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOW_YOUTUBE_PROXY", raising=False)
    calls: list[tuple] = []

    def post(path, mime, proxies):
        calls.append((path, mime, proxies))
        return _Resp(524, CLOUDFLARE_524)

    sleeps: list[float] = []
    with pytest.raises(glue_mod.ApiFatalError) as excinfo:
        glue_mod.treblo_post_upload(
            tmp_path / "montage.wav", tmp_path / "montage.flac", post=post, sleep=sleeps.append
        )
    message = str(excinfo.value)
    assert "after 4 attempts" in message
    assert "524" in message
    assert len(sleeps) == 3
    assert len(calls) == 4


def test_content_guard_raises_upload_blocked_without_retrying(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SOW_YOUTUBE_PROXY", raising=False)
    calls: list[tuple] = []

    def post(path, mime, proxies):
        calls.append((path, mime, proxies))
        return _Resp(422, CONTENT_GUARD_422)

    sleeps: list[float] = []
    with pytest.raises(glue_mod.UploadBlocked) as excinfo:
        glue_mod.treblo_post_upload(
            tmp_path / "montage.wav", tmp_path / "montage.flac", post=post, sleep=sleeps.append
        )
    message = str(excinfo.value)
    assert len(calls) == 1
    assert sleeps == []
    assert "audio_upload_id" in message
    # the "use a different song" verdict belongs to other 422s, not the content guard
    assert "a different song" not in message


def test_upload_does_not_retry_a_bad_key(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOW_YOUTUBE_PROXY", raising=False)
    calls: list[tuple] = []

    def post(path, mime, proxies):
        calls.append((path, mime, proxies))
        return _Resp(401, "unauthorized")

    sleeps: list[float] = []
    with pytest.raises(glue_mod.ApiFatalError) as excinfo:
        glue_mod.treblo_post_upload(
            tmp_path / "montage.wav", tmp_path / "montage.flac", post=post, sleep=sleeps.append
        )
    assert not isinstance(excinfo.value, glue_mod.UploadBlocked)
    assert "TREBLO_API_KEY" in str(excinfo.value)
    assert len(calls) == 1
    assert sleeps == []


def test_upload_re_encodes_once_on_413_then_retries(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SOW_YOUTUBE_PROXY", raising=False)
    wav, flac = tmp_path / "montage.wav", tmp_path / "montage.flac"
    wav.write_bytes(b"wav")
    answers = [
        _Resp(413, "too large"),
        _Resp(524, CLOUDFLARE_524),
        _Resp(200, '{"audio_upload_id": "up-3"}'),
    ]
    calls: list[tuple] = []

    def post(path, mime, proxies):
        calls.append((path, mime, proxies))
        return answers[len(calls) - 1]

    runs: list[list[str]] = []

    def fake_run(cmd):
        runs.append(cmd)
        flac.write_bytes(b"flac")

    monkeypatch.setattr(glue_mod, "_run", fake_run)
    sleeps: list[float] = []
    result = glue_mod.treblo_post_upload(wav, flac, post=post, sleep=sleeps.append)
    assert result == ("up-3", "flac")
    assert len(runs) == 1
    assert len(calls) == 3
    assert calls[0][0] == wav
    assert calls[1][0] == flac and calls[2][0] == flac
    assert calls[1][1] == "audio/flac"


def test_upload_attempts_rotate_proxy_ports(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import itertools

    monkeypatch.setenv("SOW_YOUTUBE_PROXY", "http://u:p@pool:10001-10002")
    ports = itertools.cycle(["http://u:p@pool:10001", "http://u:p@pool:10002"])
    monkeypatch.setattr(glue_mod, "choose_proxy", lambda _pool: next(ports))
    answers = [_Resp(524, CLOUDFLARE_524), _Resp(200, '{"audio_upload_id": "up-1"}')]
    seen: list[dict[str, str] | None] = []

    def post(path, mime, proxies):
        seen.append(proxies)
        return answers[len(seen) - 1]

    result = glue_mod.treblo_post_upload(
        tmp_path / "montage.wav", tmp_path / "montage.flac", post=post, sleep=lambda _s: None
    )
    assert result == ("up-1", "wav")
    assert seen[0] == {"http": "http://u:p@pool:10001", "https": "http://u:p@pool:10001"}
    assert seen[1] == {"http": "http://u:p@pool:10002", "https": "http://u:p@pool:10002"}


def test_proxy_is_opt_in_and_never_read_from_the_env_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SOW_YOUTUBE_PROXY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("SOW_YOUTUBE_PROXY=http://u:p@host:10001-10003\n")
    glue_mod.load_env_file(env_file)
    # the variable is not in ENV_KEYS, so a file-only value must never enable the proxy
    assert "SOW_YOUTUBE_PROXY" not in glue_mod.os.environ
    assert glue_mod.load_proxy_pool() is None
    assert glue_mod.request_proxies() is None

    monkeypatch.setenv("SOW_YOUTUBE_PROXY", "http://u:p@host:10001-10003")
    assert glue_mod.load_proxy_pool() == [
        "http://u:p@host:10001",
        "http://u:p@host:10002",
        "http://u:p@host:10003",
    ]


def test_invalid_proxy_range_aborts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOW_YOUTUBE_PROXY", "http://u:secret@host:10010-10001")
    with pytest.raises(SystemExit) as excinfo:
        glue_mod.load_proxy_pool()
    message = str(excinfo.value)
    assert "SOW_YOUTUBE_PROXY" in message
    assert "secret" not in message


def test_every_api_call_uses_the_pool_but_the_cdn_does_not(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import requests

    monkeypatch.setenv("SOW_YOUTUBE_PROXY", "http://u:p@pool:10001-10003")
    monkeypatch.setenv("TREBLO_API_KEY", "test-key")
    monkeypatch.setattr(glue_mod, "choose_proxy", lambda pool: pool[0])
    expected = {"http": "http://u:p@pool:10001", "https": "http://u:p@pool:10001"}
    posted: list[dict] = []
    fetched: list[dict] = []

    def fake_post(*_args, **kwargs):
        posted.append(kwargs)
        return _Resp(200, '{"task_id": "t-1"}')

    def fake_get(*args, **kwargs):
        fetched.append({"url": args[0], **kwargs})
        if "generations/status" in str(args[0]):
            return _Resp(200, '"PENDING"')
        if "generations/t-" in str(args[0]):
            return _Resp(200, '{"song_paths": ["http://cdn.treblo.com/x.flac"]}')
        return _Resp(200, '{"ok": true}')

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)

    assert glue_mod.treblo_submit({}) == "t-1"
    assert glue_mod.treblo_status("t-1") == "PENDING"
    assert glue_mod.treblo_task("t-1")["song_paths"] == ["http://cdn.treblo.com/x.flac"]
    glue_mod.treblo_download("http://cdn.treblo.com/x.flac", tmp_path / "x.bin")

    assert posted[0]["proxies"] == expected
    assert fetched[0]["proxies"] == expected
    assert fetched[1]["proxies"] == expected
    assert "proxies" not in fetched[2], "the CDN download is direct"


def test_report_renders_a_blocked_junction() -> None:
    baseline = {
        "id": "j1-baseline",
        "label": "baseline",
        "summary": "production gap",
        "status": "ok",
        "gap_seconds": 3.43839,
        "crossfade_seconds": 0.0,
        "seconds": 27.4,
        "files": {"seam": "junctions/j1/seams/baseline.flac"},
    }
    provided_items = [{"title": "A"}, {"title": "B"}]
    index = {
        "run": "probe",
        "songset_id": "ss",
        "items": provided_items,
        "beats": [4],
        "tags": ["worship"],
        "upload_seconds": 12.0,
        "credits_spent": 0,
        "credits_estimate": 100,
        "junctions": [
            {
                "id": "j1",
                "index": 1,
                "from": {"title": "A"},
                "to": {"title": "B"},
                "tempo_bpm": TEMPO,
                "beat_seconds": 0.7,
                "status": "blocked",
                "blocked_reason": glue_mod.CONTENT_GUARD_MESSAGE.format(body=CONTENT_GUARD_422),
                "montage": {"seconds": 27.4, "side_seconds": 12.0, "hole_seconds": 3.4},
                "baseline": baseline,
                "takes": [],
            },
            {
                "id": "j2",
                "index": 2,
                "from": {"title": "B"},
                "to": {"title": "C"},
                "tempo_bpm": TEMPO,
                "beat_seconds": 0.7,
                "status": "ok",
                "blocked_reason": None,
                "montage": {"seconds": 27.4, "side_seconds": 12.0, "hole_seconds": 3.4},
                "baseline": {**baseline, "id": "j2-baseline"},
                "takes": [
                    {
                        "id": "j2-take1",
                        "label": "beats-4 take1",
                        "status": "ok",
                        "seconds": 3.4,
                        "files": {"seam": "junctions/j2/glues/beats-4-take1.flac"},
                    }
                ],
            },
        ],
    }
    rendered = report_html(index)
    assert "blocked:" in rendered
    assert "content guard" in rendered
    assert 'data-take-id="j2-take1"' in rendered, "one refused junction must not hide the rest"


def test_full_set_is_skipped_when_a_junction_is_blocked() -> None:
    """A blocked junction has no Glue, and assemble_set needs one per junction."""
    junctions = [
        {"index": 1, "status": "blocked"},
        {"index": 2, "status": "ok"},
    ]
    reason = glue_mod.full_set_skip_reason(junctions, [1, 2], 3)
    assert reason is not None
    assert "j1" in reason
    assert "content guard" in reason
    assert "j2" not in reason

    # both junctions blocked: name both
    both = glue_mod.full_set_skip_reason(
        [{"index": 1, "status": "blocked"}, {"index": 2, "status": "blocked"}], [1, 2], 3
    )
    assert both is not None and "j1" in both and "j2" in both

    # a user-chosen subset is reported as such, not as a guard refusal
    subset = glue_mod.full_set_skip_reason([{"index": 1, "status": "ok"}], [1], 3)
    assert subset is not None and "subset" in subset

    # every junction has a Glue: the full set is built
    assert glue_mod.full_set_skip_reason([{"index": 1, "status": "ok"}], [1], 2) is None
