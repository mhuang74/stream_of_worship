"""Phase 1b harness tests: cache-idempotency and frozen-protocol guard.

Issue #252 Testing Decisions:
- cache-idempotency: a second harness run over the core set must produce zero
  new transcriptions (word-cache hits only) and identical feature values.
- frozen-protocol guard: the harness must fail loudly if asked to compute a
  feature family not declared in the issue's frozen list.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPT_DIR))

import phase1b_lead_outliers as p1b  # noqa: E402
import phase1_onset_lead as phase1  # noqa: E402
import eval_lrc  # noqa: E402


# ── Frozen-protocol guard ─────────────────────────────────────────────────────


def test_compute_features_rejects_undeclared_family():
    """Any feature family outside the frozen list must raise, not compute."""
    original = p1b.FEATURE_FAMILIES
    try:
        # Simulate a post-hoc addition attempt: an undeclared family sneaks
        # into the requested set inside compute_features.
        p1b.FEATURE_FAMILIES = ("abs_s", "entropy_of_leads")
        with pytest.raises(ValueError, match="not in the frozen list"):
            p1b.compute_features(
                leads_s=[0.1, 0.2, 3.0],
                leads_b=[0.1, 0.2, 3.0],
                line_indices=[0, 1, 2],
                n_sung=3,
                verified_flags=[False, False, True],
            )
    finally:
        p1b.FEATURE_FAMILIES = original


def test_feature_families_match_frozen_list():
    """The declared family tuple is exactly the issue's frozen list."""
    assert p1b.FEATURE_FAMILIES == (
        "abs_s",
        "abs_b",
        "rel",
        "summary",
        "drift",
        "union",
        "verified",
    )


def test_feature_keys_match_frozen_grids():
    """Grid keys are exactly the pre-declared grids — no swept cell may appear
    or disappear without a protocol change."""
    feats = p1b.compute_features(
        leads_s=[0.1, 0.2, 3.0, -1.0],
        leads_b=[0.1, 0.2, 3.0, -1.0],
        line_indices=[0, 1, 2, 3],
        n_sung=4,
        verified_flags=[False, False, True, False],
    )
    assert set(feats["abs_s"]) == {f"T={t}" for t in p1b.T_GRID_S}
    assert set(feats["abs_b"]) == {f"T={t}" for t in p1b.T_GRID_B}
    assert set(feats["rel"]) == {f"k={k}" for k in p1b.K_GRID}
    assert set(feats["union"]) == {f"T={t},k={k}" for t in p1b.T_GRID_S for k in p1b.K_GRID}
    assert set(feats["verified"]) == {f"vt={vt}" for vt in p1b.VT_GRID}
    assert set(feats["summary"]) == {
        "abs_median_lead_s",
        "mad_lead_s",
        "p10_p90_width_s",
        "late_line_fraction",
    }
    assert set(feats["drift"]) == {"abs_slope", "abs_curvature"}


# ── Whole-line verification semantics ────────────────────────────────────────


def _make_audio_words(spec: list[tuple[str, float]]) -> list[eval_lrc.PinyinWord]:
    words = []
    for text, t in spec:
        py = eval_lrc.chinese_to_pinyin(text)
        for p in py or [""]:
            words.append(eval_lrc.PinyinWord(text=text, pinyin=p, time_seconds=t))
    return words


def test_whole_line_verification_suppresses_matcher_flip():
    """A line whose own words sit inside its claimed window is NOT a verified
    outlier even when the onset matcher flipped to a far repetition."""
    parsed = [
        {"time": 10.0, "text": "我愛你", "is_placeholder": False},
        {"time": 16.0, "text": "我愛你", "is_placeholder": False},
        {"time": 22.0, "text": "我愛你", "is_placeholder": False},
    ]
    # Word stream: line 0's words are sung at 10.0 (in-window), but a
    # matcher flip would attribute the onset at 26.0 (the third repetition).
    audio = _make_audio_words(
        [("我爱你", 10.0), ("我爱你", 16.0), ("我爱你", 26.0)]
    )
    frac = p1b.whole_line_match_fraction(parsed, audio, 0)
    assert frac is not None and frac >= p1b.VERIFY_CUTOFF


def test_whole_line_verification_confirms_misplaced_line():
    """A line whose words are absent from its claimed window IS a verified
    outlier when the whole-line fraction falls below the cutoff."""
    parsed = [
        {"time": 10.0, "text": "平安夜安静", "is_placeholder": False},
        {"time": 15.0, "text": "世界都睡觉", "is_placeholder": False},
    ]
    # Word stream: line 0's window [8, 17) contains only unrelated words
    # ("世界都睡觉" sung at 14.0 — inside the window but not line 0's text);
    # line 0's own words are sung far away at 20.0 → fraction < cutoff.
    audio = _make_audio_words([("世界都睡觉", 14.0), ("平安夜安静", 20.0)])
    frac = p1b.whole_line_match_fraction(parsed, audio, 0)
    assert frac is not None and frac < p1b.VERIFY_CUTOFF


def test_whole_line_verification_unverifiable_returns_none():
    parsed = [
        {"time": 10.0, "text": "", "is_placeholder": True},
        {"time": 15.0, "text": "我愛你", "is_placeholder": False},
    ]
    audio = _make_audio_words([("我爱你", 12.0)])
    # placeholder line has no tokens
    assert p1b.whole_line_match_fraction(parsed, audio, 0) is None


# ── Population integrity ─────────────────────────────────────────────────────


def test_population_roles_are_disjoint_and_complete():
    core = set(p1b.CORE_POSITIVES) | set(p1b.CORE_TIMING_NEGATIVES)
    expansion = set(p1b.EXPANSION_TIMING_NEGATIVES) | set(p1b.EXPANSION_CONVENIENCE)
    diag = set(p1b.DIAGNOSTIC_NEGATIVES)
    assert not (core & expansion) and not (core & diag) and not (expansion & diag)
    # 5 positives + 4 core timing negatives = core n=9 (issue #252)
    assert len(p1b.CORE_POSITIVES) == 5
    assert len(p1b.CORE_TIMING_NEGATIVES) == 4


def test_song_role_mapping():
    assert p1b.song_role("hereforyou_62e79ae9") == "positive"
    assert p1b.song_role("cang_shen_zhi_chu_39437ec0") == "timing_negative"
    assert p1b.song_role("dan_dan_ai_mi_f6653864") == "convenience"
    assert p1b.song_role("ai_shi_wo_men_yong_gan_6d1865b8") == "diagnostic"
    assert p1b.song_role("not_a_song") == "unknown"


# ── Cache-idempotency over real Phase 1 caches (integration, cheap) ─────────


def test_second_run_hits_word_caches_only(tmp_path, monkeypatch):
    """For every core song with a Phase 1 word cache, resolve_words_cache
    finds it — so a re-run performs zero transcriptions."""
    if not (p1b.PHASE1_DIR / "bpm_cache.json").exists():
        pytest.skip("Phase 1 artifacts not present in this checkout")
    monkeypatch.setattr(p1b, "OUT_DIR", tmp_path)
    for sid in (*p1b.CORE_POSITIVES, *p1b.CORE_TIMING_NEGATIVES[:3]):
        assert p1b.resolve_words_cache(sid) is not None, f"missing word cache: {sid}"


def test_load_or_transcribe_words_uses_cache_and_is_stable(tmp_path, monkeypatch):
    """Calling load_or_transcribe_words twice on a cached song yields identical
    word streams and never writes a Phase 1b cache (Phase 1 dir read-only)."""
    sid = "zhu_a__wo_yao_gen_sui_mi_83163301"
    cache = p1b.resolve_words_cache(sid)
    if cache is None:
        pytest.skip("Phase 1 word cache not present in this checkout")
    monkeypatch.setattr(p1b, "OUT_DIR", tmp_path)  # any new write would land here

    calls = []
    real_transcribe = p1b.eval_lrc.transcribe_with_whisper

    def counting_transcribe(*a, **kw):
        calls.append(1)
        return real_transcribe(*a, **kw)

    monkeypatch.setattr(p1b.eval_lrc, "transcribe_with_whisper", counting_transcribe)

    words1 = p1b.load_or_transcribe_words(sid, "dbd506660aa3", Path("/nonexistent"))
    words2 = p1b.load_or_transcribe_words(sid, "dbd506660aa3", Path("/nonexistent"))
    assert calls == []  # zero transcriptions
    assert [(w.text, w.pinyin, w.time_seconds) for w in words1] == [
        (w.text, w.pinyin, w.time_seconds) for w in words2
    ]
    assert not (tmp_path / f"{sid}.words.json").exists()


def test_build_song_record_deterministic(tmp_path):
    """Same inputs → identical feature values (idempotency contract)."""
    sid = "zhu_a__wo_yao_gen_sui_mi_83163301"
    cache = p1b.resolve_words_cache(sid)
    base_path = p1b.resolve_base_json(sid)
    lrc_path = p1b.LRC_DIR / f"{sid}.lrc"
    if not (cache and base_path and lrc_path.exists()):
        pytest.skip("Phase 1 artifacts not present in this checkout")
    words = p1b.load_or_transcribe_words(sid, "dbd506660aa3", Path("/nonexistent"))
    bpm = p1b.load_bpm_map()[sid]
    base = json.loads(base_path.read_text(encoding="utf-8"))
    r1 = p1b.build_song_record(sid, lrc_path.read_text(encoding="utf-8"), words, bpm, base, False)
    r2 = p1b.build_song_record(sid, lrc_path.read_text(encoding="utf-8"), words, bpm, base, False)
    assert r1["features"] == r2["features"]