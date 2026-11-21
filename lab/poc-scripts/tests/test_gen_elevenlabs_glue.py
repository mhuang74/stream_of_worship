"""Unit tests for the ElevenLabs Glue POC — no network, no database, no R2."""

from __future__ import annotations

import json
import sys

import numpy as np
import pytest

import gen_elevenlabs_glue as glue_mod
from gen_elevenlabs_glue import (
    EDGE_SECONDS,
    SR,
    PlanOptions,
    apply_fades,
    build_plan,
    evaluate_status,
    joint_metrics,
    locate_glue,
    plan_total_seconds,
    report_html,
    splice,
    stage_edges,
    take_specs,
    take_tail,
)

TEMPO = 86.13281


def _sine(seconds: float, amplitude: float, frequency: float = 440.0) -> np.ndarray:
    t = np.arange(round(seconds * SR), dtype=np.float64) / SR
    return np.ascontiguousarray(
        (amplitude * np.sin(2.0 * np.pi * frequency * t)).astype(np.float32)[:, None].repeat(2, 1)
    )


def _rms_db(samples: np.ndarray) -> float:
    return glue_mod.rms_db(samples)


def test_beat_grid_uses_incoming_tempo() -> None:
    specs = take_specs([2, 4, 8, 16], TEMPO)
    targets = [spec.target_seconds for spec in specs]
    assert targets == pytest.approx([1.3932, 2.7865, 5.5729, 11.1459], abs=2e-3)
    # 4 beats is 2.7864 s, under the API's 3 s floor, so 2 and 4 both ask for 3000 ms.
    assert [spec.duration_ms for spec in specs] == [3000, 3000, 5573, 11146]
    assert [spec.trim for spec in specs] == ["tail", "tail", "none", "none"]
    assert specs[0].generation_seconds == pytest.approx(3.0)
    assert specs[2].generation_seconds == pytest.approx(5.573)
    assert specs[3].generation_seconds == pytest.approx(11.146)
    # The glue lengths are whole beats of the incoming tempo, not of A's.
    assert specs[2].target_seconds == pytest.approx(8 * 60.0 / TEMPO)


def test_plan_shape_vocal_negatives_and_conditioning() -> None:
    spec = take_specs([2], TEMPO)[0]
    opts = PlanOptions()
    plan = build_plan("AID", "BID", 0, 146510, 177310, spec, opts)
    chunks = plan["chunks"]
    assert len(chunks) == 3
    assert chunks[0] == {"song_id": "AID", "range": {"start_ms": 0, "end_ms": 146510}}
    assert chunks[2] == {"song_id": "BID", "range": {"start_ms": 0, "end_ms": 177310}}

    middle = chunks[1]
    assert middle["duration_ms"] == 3000
    assert middle["conditioning_ref"] == {
        "song_id": "BID",
        "range": {"start_ms": 0, "end_ms": 8000},
    }
    assert middle["condition_strength"] == "high"
    assert "vocals" in middle["negative_styles"]
    assert middle["context_adherence"] == "high"

    lines = [line for line in middle["text"].splitlines() if line.strip()]
    assert lines[0] == "[Glue]"
    assert len(lines) == 2, "the glue must carry a direction, never a lyric line"
    assert lines[1].startswith("{") and lines[1].endswith("}")
    assert "no vocals" in middle["text"]

    assert plan_total_seconds(spec, 0, 146510, 177310) == pytest.approx(326.82, abs=0.01)

    uncond = build_plan("AID", "BID", 0, 146510, 177310, spec, PlanOptions(condition="none"))
    assert "conditioning_ref" not in uncond["chunks"][1]
    assert "condition_strength" not in uncond["chunks"][1]

    windowed = build_plan(
        "AID", "BID", 116510, 146510, 30000, spec, PlanOptions(condition_seconds=20.0)
    )
    assert windowed["chunks"][0]["range"] == {"start_ms": 116510, "end_ms": 146510}
    assert windowed["chunks"][1]["conditioning_ref"]["range"] == {"start_ms": 0, "end_ms": 20000}
    assert windowed["chunks"][2]["range"] == {"start_ms": 0, "end_ms": 30000}


def test_arrangement_windows_picks_the_excerpt_per_side() -> None:
    a = np.arange(20 * SR, dtype=np.float32)[:, None].repeat(2, 1)
    b = np.arange(40 * SR, dtype=np.float32)[:, None].repeat(2, 1) + 1e6

    a_up, b_up, a_start, a_ref, b_ref = glue_mod.arrangement_windows(a, b, 0.0, 12.0)
    assert a_up.shape[0] == 12 * SR and b_up.shape[0] == 12 * SR
    assert np.array_equal(a_up, a[-12 * SR :]), "A must be its last 12 s, the seam side"
    assert np.array_equal(b_up, b[: 12 * SR]), "B must be its first 12 s, the seam side"
    assert (a_start, a_ref, b_ref) == (0, 12000, 12000)
    # the 3 s needle the alignment searches for still fits inside each uploaded window
    assert a_up.shape[0] >= 3 * SR and b_up.shape[0] >= 3 * SR

    a_full, b_full, a_start, a_ref, b_ref = glue_mod.arrangement_windows(a, b, 0.0, 0.0)
    assert a_full is a and b_full is b
    assert (a_start, a_ref, b_ref) == (0, 20000, 40000)

    _a, _b, a_start, a_ref, b_ref = glue_mod.arrangement_windows(a, b, 30.0, 0.0)
    assert (a_start, a_ref, b_ref) == (0, 20000, 30000), "windowed refs shift inside the upload"

    _a, _b, a_start, a_ref, b_ref = glue_mod.arrangement_windows(
        np.zeros((40 * SR, 2), np.float32), b, 30.0, 0.0
    )
    assert (a_start, a_ref, b_ref) == (10000, 40000, 30000), "A's window is its tail"

    with pytest.raises(ValueError):
        glue_mod.arrangement_windows(a, b, 0.0, 25.0)


def test_extract_glue_keeps_tail() -> None:
    frames = 3 * SR
    ramp = np.linspace(-0.5, 0.5, frames, dtype=np.float32)[:, None].repeat(2, 1)
    spec = take_specs([2], TEMPO)[0]
    target = spec.target_samples
    assert target == round(spec.target_seconds * SR)
    kept = take_tail(ramp, target)
    assert kept.shape == (target, 2)
    assert np.array_equal(kept, ramp[-target:])
    assert kept[0, 0] == pytest.approx(ramp[-target, 0])

    with pytest.raises(RuntimeError):
        take_tail(ramp, frames + 1)


def test_locate_refs_recovers_padding() -> None:
    rng = np.random.default_rng(7)
    a = (rng.standard_normal((SR * 4, 2)) * 0.1).astype(np.float32)
    b = (rng.standard_normal((SR * 4, 2)) * 0.1).astype(np.float32)
    generated = _sine(3.0, 0.2, frequency=220.0)
    pad = round(0.007 * SR)  # 7 ms of codec padding at each end
    zero_pad = np.zeros((pad, 2), dtype=np.float32)
    seam = np.concatenate([zero_pad, a, generated, b, zero_pad])

    alignment = locate_glue(seam, a[-3 * SR :], b[: 3 * SR], a.shape[0], generated.shape[0])
    assert alignment.r_a > 0.99
    assert alignment.r_b > 0.99
    assert alignment.glue_start == pad + a.shape[0]
    assert alignment.glue_end == pad + a.shape[0] + generated.shape[0]
    assert alignment.lag_a == pad
    assert alignment.lag_b == pad
    assert np.array_equal(seam[alignment.glue_start : alignment.glue_end], generated)
    assert alignment.widened is False


def _rerendered(source: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Same performance envelope, unrelated waveform — what the API returns for a ref chunk."""
    size = round(0.05 * SR)
    mono = np.sqrt(np.mean(np.square(source, dtype=np.float64), axis=1))
    blocks = mono[: mono.shape[0] // size * size].reshape(-1, size).mean(axis=1)
    envelope = np.repeat(blocks, size)[: source.shape[0]][:, None]
    return (rng.standard_normal(source.shape) * envelope).astype(np.float32)


def _modulated(seconds: float, rng: np.random.Generator) -> np.ndarray:
    """White noise under a slowly varying envelope, so the 50 ms energy carries identity."""
    t = np.arange(round(seconds * SR), dtype=np.float64) / SR
    env = 0.02 + 0.03 * np.abs(np.sin(2 * np.pi * 0.7 * t))
    return (rng.standard_normal((env.shape[0], 2)) * env[:, None]).astype(np.float32)


def test_locate_glue_uses_plan_geometry_when_refs_are_rerendered() -> None:
    rng = np.random.default_rng(11)
    a = _modulated(12.0, rng)
    b = _modulated(12.0, rng)
    generated = _sine(3.0, 0.2, frequency=220.0)
    seam = np.concatenate([_rerendered(a, rng), generated, _rerendered(b, rng)])

    alignment = locate_glue(seam, a[-3 * SR :], b[: 3 * SR], a.shape[0], generated.shape[0])
    assert alignment.mode == "plan", "a re-rendered reference must fall back to plan geometry"
    assert alignment.r_a < 0.95 and alignment.r_b < 0.95
    assert alignment.glue_start == a.shape[0]
    assert alignment.glue_end == a.shape[0] + generated.shape[0]
    assert np.array_equal(seam[alignment.glue_start : alignment.glue_end], generated)
    assert alignment.ref_envelope_r_a > 0.9 and alignment.ref_envelope_r_b > 0.9


def test_status_in_plan_mode_judges_the_envelope_not_the_waveform() -> None:
    base = {
        "glue_rms_db": -20.0,
        "geometry": "plan",
        "r_a": 0.1,
        "r_b": 0.2,
        "ref_envelope_r_a": 0.99,
        "ref_envelope_r_b": 0.98,
        "joint": {},
    }
    assert evaluate_status(base, splice_ok=True) == ("ok", []), "waveform r is noise, not evidence"

    status, reasons = evaluate_status({**base, "ref_envelope_r_b": 0.4}, splice_ok=True)
    assert status == "check"
    assert reasons[0].startswith("ref_envelope_r_b 0.4 <")

    status, reasons = evaluate_status(
        {**base, "geometry": "correlation", "r_a": 0.5}, splice_ok=True
    )
    assert status == "check" and "reference not located" in reasons[0]


def test_best_offset_is_exact_off_grid() -> None:
    """Broadband audio decorrelates within a few samples, so the search must not quantise lags."""
    rng = np.random.default_rng(3)
    needle = (rng.standard_normal((2 * SR, 2)) * 0.1).astype(np.float32)
    for pad in (309, 9128 - 16 * 570 + 309):  # 309 and 317 samples: neither is a coarse-grid point
        haystack = np.concatenate(
            [
                np.zeros((pad, 2), dtype=np.float32),
                needle,
                np.zeros((1000, 2), dtype=np.float32),
            ]
        )
        r, start = glue_mod.best_offset(needle, haystack, pad + 40, max_lag=8820)
        assert start == pad, f"expected exact start {pad}, got {start}"
        assert r > 0.999


def test_stage_edges_matches_joints_and_caps() -> None:
    glue = _sine(2.0, 0.30)
    edge_n = round(EDGE_SECONDS * SR)
    a_edge = _sine(EDGE_SECONDS, 0.1 * np.sqrt(2.0))  # -20 dBFS RMS
    b_edge = _sine(EDGE_SECONDS, 10 ** (-26.0 / 20.0) * np.sqrt(2.0))

    staged, info = stage_edges(glue, a_edge, b_edge)
    # A linear-in-dB ramp leaves the edge windows ~0.3 dB under their target: the 200 ms head
    # window averages the first tenth of the ramp. That residual is measured, not hidden.
    assert _rms_db(staged[:edge_n]) == pytest.approx(_rms_db(a_edge), abs=0.5)
    assert _rms_db(staged[-edge_n:]) == pytest.approx(_rms_db(b_edge), abs=0.5)
    assert info["a_residual_db"] == pytest.approx(0.0, abs=0.5)
    assert info["b_residual_db"] == pytest.approx(0.0, abs=0.5)
    assert info["edge_gain_clamped"] == [False, False]

    quiet_a = _sine(EDGE_SECONDS, 0.001)  # ~ -63 dBFS RMS, 40+ dB below B
    capped, capped_info = stage_edges(glue, quiet_a, b_edge)
    assert capped_info["edge_gain_db"][0] == pytest.approx(-18.0)
    assert capped_info["edge_gain_requested_db"][0] < -30.0
    assert capped_info["edge_gain_clamped"][0] is True
    assert capped_info["a_residual_db"] < -30.0
    assert np.all(np.isfinite(capped))


def test_splice_keeps_sources_sample_identical() -> None:
    rng = np.random.default_rng(11)
    a = (rng.standard_normal((SR, 2)) * 0.05).astype(np.float32)
    b = (rng.standard_normal((SR * 2, 2)) * 0.05).astype(np.float32)
    glue = _sine(1.4, 0.2)
    before = glue.copy()

    faded = apply_fades(glue)
    assert np.array_equal(glue, before), "fading must not mutate the input glue"
    fade_n = round(0.02 * SR)
    assert np.all(faded[0] == 0.0) and np.all(faded[-1] == 0.0)
    assert np.all(np.abs(faded[:fade_n]) <= np.abs(glue[:fade_n]) + 1e-7)
    assert np.all(np.abs(faded[-fade_n:]) <= np.abs(glue[-fade_n:]) + 1e-7)
    assert np.array_equal(faded[faded.shape[0] // 2], glue[glue.shape[0] // 2])

    final = splice(a, faded, b)
    assert final.shape == (a.shape[0] + faded.shape[0] + b.shape[0], 2)
    assert np.array_equal(final[: a.shape[0]], a)
    assert np.array_equal(final[a.shape[0] : a.shape[0] + faded.shape[0]], faded)
    assert np.array_equal(final[-b.shape[0] :], b)


def test_joint_metrics_flags_step_and_click() -> None:
    stepped = _sine(2.0, 0.2)
    stepped[SR:] *= 0.25  # -12 dB level drop at the joint
    step = joint_metrics(stepped, SR)
    assert step["step_db"] < -6.0

    clicked = _sine(2.0, 0.2)
    clicked[SR] += 0.7
    click = joint_metrics(clicked, SR)
    assert click["jump"] > 0.5
    assert click["jump_ratio"] is not None and click["jump_ratio"] > 2.0

    clean = joint_metrics(_sine(2.0, 0.2), SR)
    assert abs(clean["step_db"]) < 0.5
    assert clean["jump_ratio"] is None or clean["jump_ratio"] < 2.0


def _synthetic_index() -> dict:
    files = {
        "glue": "glues/beats-2.flac",
        "generation": "glues/beats-2-generation.flac",
        "seam": "seams/beats-2.flac",
        "full": "full/beats-2.flac",
    }
    return {
        "version": 1,
        "run": "run1",
        "songset_id": "hbWfCccGxN_JLQGBEqmdu",
        "model_id": "music_v2_5",
        "output_format": "pcm_44100",
        "input": "full",
        "credits_estimate": 5198,
        "items": [
            {
                "position": 0,
                "title": "Here I Bow",
                "arranged_seconds": 146.51,
                "tempo_bpm": 68.0,
                "key": "D major",
                "steps": 5,
            },
            {
                "position": 1,
                "title": "Here for You",
                "arranged_seconds": 177.31,
                "tempo_bpm": TEMPO,
                "key": "C major",
                "steps": 6,
            },
        ],
        "seam": {"tempo_bpm": TEMPO, "beat_seconds": 0.6966, "baseline_gap_seconds": 1.3932},
        "condition": {"mode": "b", "strength": "high", "seconds": 8.0},
        "takes": [
            {
                "id": "beats-2",
                "label": "beats-2 — 2 beats",
                "beats": 2,
                "status": "ok",
                "target_seconds": 1.3932,
                "generation_seconds": 3.0,
                "generated_seconds": 2.998,
                "trim": "tail",
                "glue_seconds": 1.3932,
                "metrics": {"r_a": 0.999, "r_b": 0.998, "lag_a": 3, "lag_b": -2},
                "files": files,
                "error": None,
            },
            {
                "id": "beats-8",
                "label": "beats-8 — 8 beats",
                "beats": 8,
                "status": "failed",
                "target_seconds": 5.5729,
                "generation_seconds": 5.573,
                "trim": "none",
                "glue_seconds": None,
                "metrics": {},
                "files": {},
                "error": "compose failed HTTP 500",
            },
        ],
        "baseline": {
            "id": "baseline",
            "label": "baseline — 2 beats (1.393 s) of silence",
            "summary": "current render — 2-beat silence gap",
            "status": "ok",
            "beats": 2,
            "glue_seconds": 1.3932,
            "files": {"seam": "seams/baseline.flac", "full": "full/baseline.flac"},
        },
    }


def test_report_contains_players_scores_and_localstorage() -> None:
    index = _synthetic_index()
    page = report_html(index)

    for src in index["takes"][0]["files"].values():
        assert f'src="{src}"' in page
    take_card = page.split('data-take-id="beats-2"', 1)[1].split("</article>", 1)[0]
    assert take_card.count("<audio") == 4
    assert 'src="glues/beats-2-generation.flac"' in page
    assert page.count('preload="none"') == 2  # full pair per take + baseline full

    # one score UI per card: the ok take, the failed take and the baseline
    assert page.count('<input class="score-input" type="number" min="0" max="10" step="0.5">') == 3
    assert page.count('class="vocals-input"') == 3
    assert page.count('class="note-input"') == 3

    assert 'id="last-played"' in page
    assert "Last played: —" in page
    assert "sow.elevenlabs-glue:" in page and ":scores'" in page and ":lastPlayed'" in page
    assert 'data-run="run1"' in page
    for control in ("export-scores", "import-scores", "reset-scores", "import-file"):
        assert f'id="{control}"' in page

    assert 'class="card"' in page and 'data-take-id="beats-2"' in page
    assert "current render — 2-beat silence gap" in page
    assert "compose failed HTTP 500" in page  # failed takes are shown, never dropped
    assert "<th>score</th>" in page
    # A minimal but valid HTML document with no external resources.
    assert page.startswith("<!doctype html>") and page.rstrip().endswith("</html>")
    assert "http://" not in page and "https://" not in page


def test_fatal_api_response_exits_2(tmp_path, monkeypatch, capsys) -> None:
    import types

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def item(position):
        return glue_mod.SongsetItem(
            position=position,
            song_id=f"song{position}",
            title=f"Title {position}",
            hash_prefix=f"hash{position}",
            gap_beats=2,
            crossfade_enabled=False,
            steps=[{"startSeconds": 0.0, "endSeconds": 10.0}],
        )

    recording = glue_mod.Recording(
        hash_prefix="hash0",
        content_hash=None,
        duration_seconds=200.0,
        tempo_bpm=TEMPO,
        musical_key="C",
        musical_mode="major",
        loudness_db=-14.0,
    )
    monkeypatch.setattr(
        glue_mod,
        "fetch_songset",
        lambda conn, songset_id: (
            [item(0), item(1)],
            {"hash0": recording, "hash1": recording},
        ),
    )
    monkeypatch.setattr(
        glue_mod, "ensure_source_audio", lambda prefix, root, connect: _sine(10.0, 0.1)
    )
    monkeypatch.setattr(glue_mod, "arranged_audio", lambda audio, steps, label: _sine(12.0, 0.1))

    def forbidden(*_args, **_kwargs):
        raise glue_mod.ApiFatalError(
            "upload returned HTTP 402 (payment required): paid_plan_required"
        )

    monkeypatch.setattr(glue_mod, "ensure_upload", forbidden)
    monkeypatch.setattr(glue_mod, "compose", forbidden)
    for name in glue_mod.ENV_KEYS:
        monkeypatch.setenv(name, "test-value")
    monkeypatch.setitem(
        sys.modules, "psycopg", types.SimpleNamespace(connect=lambda url: FakeConnection())
    )
    monkeypatch.setitem(
        sys.modules,
        "stream_of_worship.admin.services.r2",
        types.SimpleNamespace(R2Client=lambda **_kwargs: object()),
    )

    out_root = tmp_path / "out"
    assert glue_mod.main(["--run-name", "gate", "--out-root", str(out_root)]) == 2
    failure = json.loads((out_root / "gate" / "failure.json").read_text())
    assert "402" in failure["error"] and failure["run"] == "gate"
    assert "error:" in capsys.readouterr().out


def test_dry_run_does_not_call_network(tmp_path, monkeypatch, capsys) -> None:
    def explode(*args, **kwargs):
        raise AssertionError("dry run must not touch the network, Postgres or R2")

    monkeypatch.setattr(glue_mod, "compose", explode)
    monkeypatch.setattr(glue_mod, "upload_song", explode)
    monkeypatch.setattr(glue_mod, "ensure_upload", explode)
    monkeypatch.setattr(glue_mod, "fetch_songset", explode)
    monkeypatch.setattr(glue_mod, "ensure_source_audio", explode)
    monkeypatch.setitem(sys.modules, "psycopg", None)
    monkeypatch.setitem(sys.modules, "stream_of_worship.admin.services.r2", None)

    out_root = tmp_path / "out"
    assert glue_mod.main(["--dry-run", "--out-root", str(out_root)]) == 0
    assert not out_root.exists()

    printed = capsys.readouterr().out
    for expected in ("1.3932", "2.7864", "5.5728", "11.1456", "326.82", "329.39", "334.97"):
        assert expected in printed
    assert "5198" in printed  # 4,857 upload + 341 generation at 15 credits/s
    assert "<A_ID>" in printed and "<B_ID>" in printed
    assert printed.count("plan beats-") == 4
    assert printed.count('"chunks"') == 4
    assert '"duration_ms": 5573' in printed
    assert '"conditioning_ref"' in printed
