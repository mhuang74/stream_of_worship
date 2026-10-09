"""Phase 1b lead-time separation extraction for the LRC triage cascade.

Implements the pre-registered protocol of issue #252 (frozen in the issue
body): a line-level outlier study that empirically extracts lead-time
conditions distinguishing seed positives from timing-attributed seed
negatives, with matcher-flip false positives controlled by whole-line
verification. Zero re-transcription for the 10 Phase 1 songs — their word
caches (``phase1/*.words.json``) and per-line measurements
(``phase1/*.base.json``) are consumed read-only.

FROZEN PROTOCOL (issue #252 — the harness fails loudly on any deviation):

Feature families (exactly these, in exactly this order):
  1. absolute per-line: fraction of sung lines with |lead| > T,
     T swept over T_GRID_S (seconds) and T_GRID_B (beats).
  2. song-relative per-line: fraction of sung lines with robust (MAD-based)
     z-score > k, k swept over K_GRID.
  3. song-level summary: |median lead|, MAD, p10–p90 width, late-line fraction.
  4. drift: lead-vs-line-index regression |slope| and |curvature|
     (quadratic coefficient after orthogonalizing (i-μi)² against (i-μi)).
  5. union: max of feature-1 and feature-2 fractions over the T_S × k grid.
  6. verified-outlier fraction: lines with |lead| > 3 s whose whole-line
     verification also fails, divided by sung lines — swept over VT_GRID.

Directions (declared before measurement, per family):
  - 1, 2, 5, 6: timing-negative values strictly ABOVE all positive values.
  - 3, 4: |value| of timing negatives strictly ABOVE all positive values.

Separation criterion: complete separation, non-zero gap —
min(negative values) > max(positive values), gap = min(neg) − max(pos) > 0.
The verdict cites the whole swept grid, never a single best cell.

Null action (pre-committed): if no candidate on the frozen list achieves
complete separation, signal 1 stays dropped; no feature family may be added,
no criterion softened, no further search.

Whole-line verification (feature 6 discriminator): for any line flagged as
an outlier, match the ENTIRE line's tokens (pinyin for CJK lines, raw latin
tokens otherwise) against the word stream inside the line's claimed window
[line_time − VERIFY_TOL_S, next_line_time + VERIFY_TOL_S). The line is a
*verified* outlier only when the matched-token fraction falls below
VERIFY_CUTOFF. A first-word matcher flip to the wrong repetition does not
survive this check when the line's own words sit in its claimed window.
Window shape and cutoff are frozen before measurement (this file's header).

Study population (issue #252):
  - core (n=9): 5 seed positives + 4 timing negatives — 3 youtube_transcript
    negatives measured in Phase 1 plus cang_shen_zhi_chu (stem cached; LRC
    fetched into the truth LRC cache).
  - conditional expansion (up to n=12): wo_jing_bai_mi__ye_su and
    na_me_shen_de_ke_mu (2 remaining youtube_transcript seed negatives) plus
    dan_dan_ai_mi (April-report known-bad, convenience label, NEVER in the
    primary fit). Degradation ladder per song, stopping at the first usable
    state: (1) retry stem separation; (2) transcribe the mixed audio and
    mark quality-flagged — excluded from the primary fit, reported
    separately; (3) drop the song.
  - excluded from fit, reported as diagnostics: ai_shi_wo_men_yong_gan
    (qwen3_asr content negative) and bu_ting_zan_mei_mi (manual_upload
    anomaly negative).

Measurement method (unchanged from Phase 1): whole-file faster-whisper
large-v3, CPU, internal Silero VAD, auto language detection; per-line
onset-lead via the homophone-tolerant pinyin per-line aligner with the
raw-token fallback for non-CJK lines; beats normalization via the
production recordings BPM. New word caches land under ``phase1b/`` — the
``phase1/`` directory is read-only for this harness.

Usage:
    uv run --project . --extra lrc_eval python phase1b_lead_outliers.py measure
    uv run --project . --extra lrc_eval python phase1b_lead_outliers.py analyze
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from difflib import SequenceMatcher
from pathlib import Path

import typer

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

import eval_lrc  # noqa: E402
import phase1_onset_lead as phase1  # noqa: E402

app = typer.Typer(help="Phase 1b lead-time separation extraction (issue #252)")

# Phase 1 artifacts are read-only for this study.
PHASE1_DIR = phase1.OUT_DIR
# Phase 1b artifacts live in their own run directory beside the Phase 1 outputs.
OUT_DIR = phase1.TRUTH_DIR / "phase1b"
LRC_DIR = phase1.LRC_DIR
STEM_MANIFEST = phase1.STEM_MANIFEST
REPO_ROOT = phase1.REPO_ROOT

ENGINE = "whisper"
MODEL = "large-v3"
COMPUTE_TYPE = "int8"

# ── Frozen decision constants (pre-registered before any measurement) ────────

# Feature 1 grid: seconds, then beats.
T_GRID_S: tuple[float, ...] = (1.0, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0)
T_GRID_B: tuple[float, ...] = (0.5, 1.0, 2.0, 3.0, 4.0, 8.0)
# Feature 2 grid: MAD-based robust z-score thresholds.
K_GRID: tuple[float, ...] = (3.0, 4.0, 5.0, 6.0, 8.0, 10.0)
# Feature 5 union grid: cartesian product of the absolute seconds grid and k.
# Feature 6: absolute-outlier anchor and verification sweep.
ABS_OUTLIER_T_S = 3.0
VT_GRID: tuple[float, ...] = (0.3, 0.4, 0.5)
# Whole-line verification window and cutoff (frozen before measurement).
VERIFY_TOL_S = 2.0
VERIFY_CUTOFF = 0.5

# Population (issue #252, exact ids).
CORE_POSITIVES: tuple[str, ...] = (
    "zhu_a__wo_yao_gen_sui_mi_83163301",
    "hereforyou_62e79ae9",
    "xin_kao_mei_yi_ju_ying_xu_df457941",
    "cong_zao_chen_dao_ye_wan_b035044f",
    "mei_hao_de_chuang_zao_3d42d76e",
)
CORE_TIMING_NEGATIVES: tuple[str, ...] = (  # 3 measured in Phase 1 + cang_shen
    "shu_bu_jin_71fba0ce",
    "wo_neng_gei_ni_shen_me_03b2dcb2",
    "jing_bai_ye_su_b08227a2",
    "cang_shen_zhi_chu_39437ec0",
)
EXPANSION_TIMING_NEGATIVES: tuple[str, ...] = (
    "wo_jing_bai_mi__ye_su_e6dd6146",
    "na_me_shen_de_ke_mu_ff92abd9",
)
EXPANSION_CONVENIENCE: tuple[str, ...] = ("dan_dan_ai_mi_f6653864",)
DIAGNOSTIC_NEGATIVES: tuple[str, ...] = (  # excluded from fit, reported anyway
    "ai_shi_wo_men_yong_gan_6d1865b8",  # qwen3_asr content negative
    "bu_ting_zan_mei_mi_e937a9d3",  # manual_upload anomaly negative
)

PRIMARY_FIT_FLOOR = 4  # core timing negatives; ceiling 6 DB-truth + 1 convenience
DB_TRUTH_TIMING_NEGATIVE_CEILING = 6

# Frozen-protocol guard: the only feature families this harness may compute.
FEATURE_FAMILIES = ("abs_s", "abs_b", "rel", "summary", "drift", "union", "verified")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_default(o):
    """Coerce ASR/alignment scalars (e.g. numpy.bool_, numpy float64) to plain
    JSON types instead of crashing after a completed transcription."""
    import numpy as np

    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")


def song_role(song_id: str) -> str:
    """Population role for a song id (issue #252 population lists)."""
    if song_id in CORE_POSITIVES:
        return "positive"
    if song_id in CORE_TIMING_NEGATIVES:
        return "timing_negative"
    if song_id in EXPANSION_TIMING_NEGATIVES:
        return "expansion_timing_negative"
    if song_id in EXPANSION_CONVENIENCE:
        return "convenience"
    if song_id in DIAGNOSTIC_NEGATIVES:
        return "diagnostic"
    return "unknown"


def resolve_words_cache(song_id: str) -> Path | None:
    """Locate a song's word cache: Phase 1 dir first, then Phase 1b."""
    for d in (PHASE1_DIR, OUT_DIR):
        p = d / f"{song_id}.words.json"
        if p.exists():
            return p
    return None


def resolve_base_json(song_id: str) -> Path | None:
    """Locate a Phase 1 per-song measurement JSON (read-only reuse)."""
    p = PHASE1_DIR / f"{song_id}.base.json"
    return p if p.exists() else None


def load_or_transcribe_words(song_id: str, hash_prefix: str, audio_path: Path) -> list[eval_lrc.PinyinWord]:
    """Word stream for a song; Phase 1 caches first, then Phase 1b cache.

    Cache-idempotency contract: one transcription per song, reused by every
    feature computation; a second harness run must produce zero new
    transcriptions.
    """
    cached = resolve_words_cache(song_id)
    if cached is not None:
        return [
            eval_lrc.PinyinWord(text=w["text"], pinyin=w["pinyin"], time_seconds=w["time_seconds"])
            for w in _load_json(cached)
        ]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    words = eval_lrc.transcribe_with_whisper(
        audio_path=audio_path,
        model_name=MODEL,
        device="cpu",
        compute_type=COMPUTE_TYPE,
        language=None,
        lyrics_text=None,
    )
    cache = OUT_DIR / f"{song_id}.words.json"
    cache.write_text(
        json.dumps([{"text": w.text, "pinyin": w.pinyin, "time_seconds": w.time_seconds} for w in words])
    )
    return words


def line_tokens(text: str) -> list[str]:
    """Pinyin tokens for CJK lines, raw latin tokens otherwise (≥2 chars)."""
    toks = eval_lrc.chinese_to_pinyin(text)
    if toks:
        return toks
    return [t for t in re.findall(r"[a-zA-Z']+", text.lower()) if len(t) >= 2]


def whole_line_match_fraction(
    parsed_lines: list[dict],
    audio_words: list[eval_lrc.PinyinWord],
    line_index: int,
    tol_s: float = VERIFY_TOL_S,
) -> float | None:
    """Fraction of a line's tokens matched inside its claimed time window.

    The claimed window is [line_time − tol, next_line_time + tol). CJK lines
    match on pinyin; non-CJK lines on raw latin tokens. Returns None when the
    line has no tokens or the window has no audio words (unverifiable).
    """
    ln = parsed_lines[line_index]
    next_t = (
        parsed_lines[line_index + 1]["time"]
        if line_index + 1 < len(parsed_lines)
        else ln["time"] + phase1.DEFAULT_DURATION
    )
    window = [w for w in audio_words if ln["time"] - tol_s <= w.time_seconds < next_t + tol_s]
    lrc_tokens = line_tokens(ln["text"])
    if not lrc_tokens or not window:
        return None
    audio_tokens = [w.pinyin if w.pinyin else w.text.lower() for w in window]
    matcher = SequenceMatcher(None, lrc_tokens, audio_tokens)
    matched = sum(i2 - i1 for op, i1, i2, _j1, _j2 in matcher.get_opcodes() if op == "equal")
    return matched / len(lrc_tokens)


def compute_features(
    leads_s: list[float],
    leads_b: list[float],
    line_indices: list[int],
    n_sung: int,
    verified_flags: list[bool],
) -> dict:
    """Compute the frozen feature list (and only it) for one song.

    Frozen-protocol guard: raises on any feature family not declared in
    ``FEATURE_FAMILIES`` so post-hoc additions are visible in code review.
    """
    requested = ("abs_s", "abs_b", "rel", "summary", "drift", "union", "verified")
    for fam in requested:
        if fam not in FEATURE_FAMILIES:
            raise ValueError(f"feature family {fam!r} is not in the frozen list {FEATURE_FAMILIES}")

    n = len(leads_s)
    if n == 0:
        # Whole-file ASR found no CJK vocal content matching any LRC line
        # (e.g. dan_dan mixed audio with 2 syllables vs 17 repetitive lines).
        # Not a feature failure: the song is unmeasurable on this audio.
        return {
            "measurable": False,
            "abs_s": {}, "abs_b": {}, "rel": {}, "union": {}, "verified": {},
            "summary": {k: None for k in (
                "abs_median_lead_s", "mad_lead_s", "p10_p90_width_s", "late_line_fraction"
            )},
            "drift": {"abs_slope": None, "abs_curvature": None},
        }

    med = statistics.median(leads_s)
    mad = statistics.median([abs(x - med) for x in leads_s])

    def frac_above(values: list[float], t: float) -> float:
        return sum(1 for x in values if abs(x) > t) / n

    def frac_z_above(t: float) -> float:
        if mad == 0:
            return 0.0
        return sum(1 for x in leads_s if abs(x - med) / mad > t) / n

    abs_s = {f"T={t}": round(frac_above(leads_s, t), 6) for t in T_GRID_S}
    abs_b = {f"T={t}": round(frac_above(leads_b, t), 6) for t in T_GRID_B}
    rel = {f"k={k}": round(frac_z_above(k), 6) for k in K_GRID}

    p10, p90 = statistics.quantiles(leads_s, n=10)[0], statistics.quantiles(leads_s, n=10)[8]
    summary = {
        "abs_median_lead_s": round(abs(med), 6),
        "mad_lead_s": round(mad, 6),
        "p10_p90_width_s": round(p90 - p10, 6),
        "late_line_fraction": round(sum(1 for x in leads_s if x < 0) / n, 6),
    }

    # Drift: ordinary least squares on lead ~ line_index; curvature is the
    # quadratic coefficient after orthogonalizing (i-μi)² against (i-μi).
    mi = statistics.mean(line_indices)
    ml = statistics.mean(leads_s)
    us = [i - mi for i in line_indices]
    suu = sum(u * u for u in us)
    slope = (sum(u * (x - ml) for u, x in zip(us, leads_s)) / suu) if suu else 0.0
    resid = [x - ml - slope * u for u, x in zip(us, leads_s)]
    qs = [u * u for u in us]
    squ = sum(q * u for q, u in zip(qs, us))
    qo = [q - (squ / suu) * u for q, u in zip(qs, us)] if suu else qs
    mo = statistics.mean(qo)
    mx = statistics.mean(resid)
    sqq = sum((q - mo) ** 2 for q in qo)
    curv = (sum((q - mo) * (x - mx) for q, x in zip(qo, resid)) / sqq) if sqq else 0.0
    drift = {"abs_slope": round(abs(slope), 6), "abs_curvature": round(abs(curv), 6)}

    union = {}
    for t in T_GRID_S:
        for k in K_GRID:
            key = f"T={t},k={k}"
            fa = frac_above(leads_s, t)
            fr = frac_z_above(k)
            union[key] = round(max(fa, fr), 6)

    verified = {
        f"vt={vt}": round(sum(1 for v in verified_flags if v) / n_sung, 6) if n_sung else 0.0
        for vt in VT_GRID
    }
    # verified_flags are anchored at ABS_OUTLIER_T_S; the per-vt sweep only
    # varies the verification cutoff, not the anchor (frozen).

    return {
        "abs_s": abs_s,
        "abs_b": abs_b,
        "rel": rel,
        "summary": summary,
        "drift": drift,
        "union": union,
        "verified": verified,
    }


def build_song_record(
    song_id: str,
    lrc_content: str,
    audio_words: list[eval_lrc.PinyinWord],
    bpm: float,
    base: dict | None,
    quality_flagged: bool,
) -> dict:
    """Per-song Phase 1b artifact with per-line attribution.

    Reuses the Phase 1 per-line measurement when available (``base``) — zero
    re-alignment — and otherwise re-measures with the Phase 1 code path.
    Every sung matched line carries its whole-line verification fraction; the
    ``verified_outlier`` flag is frozen at |lead| > ABS_OUTLIER_T_S and
    fraction < VERIFY_CUTOFF.
    """
    if base is not None:
        lines = base["lines"]
    else:
        raise RuntimeError("base measurement JSON missing; measure path must supply it")

    parsed = phase1.parse_lrc_lines(lrc_content)
    if len(parsed) != len(lines):
        raise ValueError(
            f"{song_id}: LRC re-parse yields {len(parsed)} lines but cached measurement has {len(lines)}"
        )

    sung_leads, sung_leads_b, sung_idx, verified_flags = [], [], [], []
    per_line = []
    for l in lines:
        entry = {
            "line_index": l["line_index"],
            "time": l["time"],
            "text": l["text"],
            "is_placeholder": l["is_placeholder"],
            "matched": l["matched"],
            "lead_s": l.get("lead_s"),
            "lead_beats": l.get("lead_beats"),
        }
        matched_line = l["matched"] and not l["is_placeholder"] and l.get("lead_s") is not None
        if matched_line:
            frac = whole_line_match_fraction(parsed, audio_words, l["line_index"])
            entry["whole_line_fraction"] = None if frac is None else round(frac, 4)
            outlier = abs(l["lead_s"]) > ABS_OUTLIER_T_S
            verified = bool(
                outlier and frac is not None and frac < VERIFY_CUTOFF
            )
            entry["abs_outlier"] = outlier
            entry["verified_outlier"] = verified
            verified_flags.append(verified)
            sung_leads.append(l["lead_s"])
            sung_leads_b.append(l["lead_beats"])
            sung_idx.append(l["line_index"])
        else:
            entry["whole_line_fraction"] = None
            entry["abs_outlier"] = None
            entry["verified_outlier"] = None
        per_line.append(entry)

    features = compute_features(
        sung_leads, sung_leads_b, sung_idx, base["n_sung"], verified_flags
    )

    return {
        "song_id": song_id,
        "role": song_role(song_id),
        "quality_flagged": quality_flagged,
        "bpm": bpm,
        "n_lines": base["n_lines"],
        "n_sung": base["n_sung"],
        "n_matched": base["n_matched"],
        "match_rate": base["match_rate"],
        "audio_engine": ENGINE,
        "asr_model": MODEL,
        "features": features,
        "lines": per_line,
    }


def measure_from_base(
    song_id: str,
    hash_prefix: str,
    audio_path: Path,
    bpm: float,
    quality_flagged: bool,
) -> dict | None:
    """Build a Phase 1b song record, reusing Phase 1 caches where possible.

    Phase 1 songs: reuse phase1/*.base.json per-line leads + words cache
    (zero re-transcription, zero re-alignment). New songs: transcribe once
    into the Phase 1b cache, then run the Phase 1 measure path.
    """
    lrc_path = LRC_DIR / f"{song_id}.lrc"
    if not lrc_path.exists():
        print(f"NO LRC: {song_id}")
        return None
    lrc_content = lrc_path.read_text(encoding="utf-8")

    base_path = resolve_base_json(song_id)
    words = load_or_transcribe_words(song_id, hash_prefix, audio_path)

    if base_path is not None:
        base = _load_json(base_path)
    else:
        song = {"song_id": song_id, "truth": song_role(song_id)}
        base = phase1.measure_song(song, lrc_content, audio_path, bpm, audio_words=words)

    return build_song_record(song_id, lrc_content, words, bpm, base, quality_flagged)


def expansion_audio_state(song_id: str, hash_prefix: str) -> tuple[Path, bool] | None:
    """Degradation ladder for an expansion song; returns (audio, quality_flag).

    (1) separated stem (cached clean_vocals) → usable, not flagged;
    (2) mixed audio → usable but quality-flagged (excluded from the primary
        fit, reported separately);
    (3) neither → None (drop the song).
    """
    manifest = _load_json(STEM_MANIFEST)
    ent = manifest["songs"].get(song_id, {})
    if ent.get("status") == "cached":
        try:
            stem = phase1.resolve_stem(hash_prefix, ent.get("audio", "stems/clean_vocals.flac"))
            return stem, False
        except FileNotFoundError:
            pass
    for root in phase1.CACHE_ROOTS:
        mixed = root / hash_prefix / "audio" / "audio.mp3"
        if mixed.exists() and mixed.stat().st_size > 0:
            return mixed, True
    return None


def load_bpm_map() -> dict[str, float]:
    """Phase 1 BPM snapshot covers every catalog song; reuse read-only."""
    cache = PHASE1_DIR / "bpm_cache.json"
    if not cache.exists():
        raise FileNotFoundError(
            f"{cache} missing — run phase1_onset_lead.py measure once first"
        )
    return {k: float(v) for k, v in _load_json(cache).items()}


@app.command()
def measure(songs: str = typer.Option(None, "--songs", help="Comma-separated song ids")) -> None:
    """Measure every study-population song; write per-song JSONs to phase1b/."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bpm_map = load_bpm_map()
    manifest = _load_json(STEM_MANIFEST)

    wanted: set[str] | None = set(songs.split(",")) if songs else None
    planned: list[tuple[str, str, bool]] = []  # (song_id, hash_prefix, quality_flag)
    for song_id in (
        *CORE_POSITIVES,
        *CORE_TIMING_NEGATIVES,
        *EXPANSION_TIMING_NEGATIVES,
        *EXPANSION_CONVENIENCE,
        *DIAGNOSTIC_NEGATIVES,
    ):
        if wanted is not None and song_id not in wanted:
            continue
        ent = manifest["songs"].get(song_id, {})
        hp = ent.get("hash_prefix")
        if not hp:
            print(f"NO HASH PREFIX: {song_id}")
            continue
        role = song_role(song_id)
        if role in ("positive", "timing_negative", "diagnostic"):
            if ent.get("status") != "cached":
                print(f"NO STEM: {song_id}")
                continue
            planned.append((song_id, hp, False))
        else:  # expansion songs: degradation ladder
            state = expansion_audio_state(song_id, hp)
            if state is None:
                print(f"DROP (unusable audio): {song_id}")
                continue
            audio, flagged = state
            print(f"EXPANSION {song_id}: audio={audio.name} quality_flagged={flagged}")
            planned.append((song_id, hp, flagged))

    for song_id, hp, flagged in planned:
        out_path = OUT_DIR / f"{song_id}.json"
        if out_path.exists():
            print(f"skip (exists): {song_id}")
            continue
        ent = manifest["songs"][song_id]
        if flagged:
            audio = next(
                root / hp / "audio" / "audio.mp3"
                for root in phase1.CACHE_ROOTS
                if (root / hp / "audio" / "audio.mp3").exists()
            )
        else:
            audio = phase1.resolve_stem(hp, ent.get("audio", "stems/clean_vocals.flac"))
        bpm = bpm_map.get(song_id)
        if not bpm:
            print(f"NO BPM: {song_id}")
            continue
        print(f"=== {song_id} ({song_role(song_id)}) bpm={bpm} flagged={flagged}")
        record = measure_from_base(song_id, hp, audio, bpm, flagged)
        if record is None:
            continue
        out_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=_json_default)
        )
        f = record["features"]["summary"]
        print(
            f"  sung={record['n_sung']} matched={record['n_matched']} "
            f"abs_med={f['abs_median_lead_s']} mad={f['mad_lead_s']}"
        )


def load_song_records() -> dict[str, dict]:
    return {
        p.name.removesuffix(".json"): _load_json(p)
        for p in sorted(OUT_DIR.glob("*.json"))
        if not p.name.startswith("analysis") and not p.name.endswith(".words.json")
    }


def separation_report(records: dict[str, dict]) -> dict:
    """Complete-separation sweep over the frozen grid (issue #252 criterion).

    Fit set: positives + timing negatives (core + expansion) that are not
    quality-flagged. Diagnostics, convenience, and quality-flagged songs are
    tabulated but never move thresholds.
    """
    pos_ids = [s for s, r in records.items() if r["role"] == "positive"]
    fit_neg_ids = [
        s
        for s, r in records.items()
        if r["role"] in ("timing_negative", "expansion_timing_negative") and not r["quality_flagged"]
    ]
    side_ids = [
        s
        for s, r in records.items()
        if s not in pos_ids and s not in fit_neg_ids
    ]

    def extract(rec: dict, family: str, key: str) -> float:
        if family in ("summary", "drift"):
            return rec["features"][family][key]
        return rec["features"][family][key]

    candidates: list[dict] = []
    for family, keys in (
        ("abs_s", [f"T={t}" for t in T_GRID_S]),
        ("abs_b", [f"T={t}" for t in T_GRID_B]),
        ("rel", [f"k={k}" for k in K_GRID]),
        ("summary", list(records[pos_ids[0]]["features"]["summary"].keys()) if pos_ids else []),
        ("drift", list(records[pos_ids[0]]["features"]["drift"].keys()) if pos_ids else []),
        ("union", list(records[pos_ids[0]]["features"]["union"].keys()) if pos_ids else []),
        ("verified", [f"vt={vt}" for vt in VT_GRID]),
    ):
        for key in keys:
            pos_vals = [extract(records[s], family, key) for s in pos_ids]
            neg_vals = [extract(records[s], family, key) for s in fit_neg_ids]
            if not neg_vals or not pos_vals:
                continue
            gap = min(neg_vals) - max(pos_vals)
            candidates.append(
                {
                    "family": family,
                    "key": key,
                    "direction": "timing_negative_above_positives",
                    "positive_values": {s: round(extract(records[s], family, key), 4) for s in pos_ids},
                    "fit_negative_values": {s: round(extract(records[s], family, key), 4) for s in fit_neg_ids},
                    "max_positive": round(max(pos_vals), 4),
                    "min_fit_negative": round(min(neg_vals), 4),
                    "gap": round(gap, 4),
                    "complete_separation": gap > 0,
                }
            )

    surviving = [c for c in candidates if c["complete_separation"]]
    return {
        "criterion": "complete separation, non-zero gap: min(neg) > max(pos)",
        "n_positives": len(pos_ids),
        "n_fit_negatives": len(fit_neg_ids),
        "fit_floor": PRIMARY_FIT_FLOOR,
        "fit_negatives": fit_neg_ids,
        "n_candidates": len(candidates),
        "surviving": surviving,
        "candidates": candidates,
        "side_songs": side_ids,
    }


@app.command()
def analyze() -> None:
    """Sweep the frozen grid, apply the pre-registered criterion, write verdict."""
    records = load_song_records()
    rep = separation_report(records)

    fit_negs = rep["n_fit_negatives"]
    rep["population_note"] = (
        "Primary fit floor is 4 timing negatives (core); ceiling 6 DB-truth "
        "+ 1 convenience. Diagnostics (qwen3_asr, manual_upload), the "
        "convenience song, and quality-flagged expansion songs are tabulated "
        "but never move thresholds."
    )

    surviving = rep["surviving"]
    if surviving:
        verdict = "CANDIDATE_SURVIVES"
        rep["entry_bar"] = (
            "Surviving candidate(s) must be re-derived on the #248 bake-off's "
            "second engine word streams before any checker entry; Phase 3 "
            "spot-check by ear on flagged NULL-provenance review songs "
            "estimates real-queue precision. Integration is a separate ticket."
        )
    else:
        verdict = "NULL_NO_SEPARATION"
        rep["null_action"] = (
            "Signal 1 stays dropped; the Phase 1 fallback (pinyin CER + VAD "
            "pre-filter, no auto-PASS) and the recommended v2.1 spec amendment "
            "proceed unchanged. No further feature search."
        )
    rep["verdict"] = verdict

    out = OUT_DIR / "analysis.json"
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=2))
    print(f"verdict: {verdict}")
    print(f"fit negatives: {fit_negs} (floor {PRIMARY_FIT_FLOOR})")
    print(f"candidates swept: {rep['n_candidates']}, surviving: {len(surviving)}")
    for c in surviving:
        print(f"  SURVIVOR {c['family']} {c['key']} gap={c['gap']}")
    print(f"written: {out}")


if __name__ == "__main__":
    app()