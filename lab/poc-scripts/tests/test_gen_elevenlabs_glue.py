"""Unit tests for the ElevenLabs Glue POC — no network, no database, no R2."""

from __future__ import annotations

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


def _music(seconds: float, seed: int, amplitude: float = 0.1) -> np.ndarray:
    """Band-limited signal: a 16-sample shift keeps high correlation, unlike white noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(round(seconds * SR), dtype=np.float64) / SR
    signal = np.zeros_like(t)
    for frequency in rng.uniform(90.0, 330.0, size=6):
        signal += np.sin(2.0 * np.pi * frequency * t + rng.uniform(0, 2 * np.pi))
    envelope = 0.6 + 0.4 * np.sin(2.0 * np.pi * 0.37 * t)
    mono = (amplitude * signal / 3.0 * envelope).astype(np.float32)
    return np.ascontiguousarray(mono[:, None].repeat(2, 1))


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
    a = _music(4.0, seed=7)
    b = _music(4.0, seed=8)
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
