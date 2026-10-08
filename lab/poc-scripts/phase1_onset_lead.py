"""Phase 1 onset-lead feasibility gate for the LRC review triage cascade.

Implements Phase 1 of ``specs/lrc-review-triage-cascade-design-v2.md`` (issue #245):

For each song, transcribe ``clean_vocals.flac`` with faster-whisper large-v3
in a single whole-file pass (faster-whisper's internal Silero VAD via
``vad_filter=True``, auto language detection; never the LRC's own
timestamps), then compute per-line onset-lead:

    lead_s = (onset_time_of_line_first_matched_word - lrc_line_timestamp)
    lead_beats = lead_s * BPM / 60

The LRC line's first word is located in the ASR word stream via pinyin,
homophone-tolerant sequence alignment (``eval_lrc.align_sequences_per_line``).
ADR-0008 gap-placeholder lines are excluded from scoring.

Tests
-----
(a) Separation — positives vs timing-attributed negatives onset-lead
    distributions.
(b) Recoverability — inject uniform offsets (+0.5s, +1.0s) into positive LRC
    line timestamps, re-measure, compare recovered mean offset vs injected.

Gate: (b) recovered mean offset within ±0.25s of injected on ≥90% of sung
lines for ≥8/10 songs, AND (a) negatives separate.

Zero writes: reads LRC from R2 (downloaded read-only into the eval dir),
reads cached stems, writes only JSON reports under ``eval/lrc_truth/phase1/``.

Usage:
    uv run --project . --extra lrc_eval python phase1_onset_lead.py measure
    uv run --project . --extra lrc_eval python phase1_onset_lead.py analyze
"""

from __future__ import annotations

import functools
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

app = typer.Typer(help="Phase 1 onset-lead feasibility gate")

# Seed truth lists live in the repo-root eval dir (Phase 0a artifacts);
# stem cache + per-phase outputs live under poc-scripts/eval.
REPO_ROOT = _SCRIPT_DIR.parent.parent
TRUTH_DIR = _SCRIPT_DIR / "eval/lrc_truth"
TRUTH_LISTS_DIR = REPO_ROOT / "eval/lrc_truth"
LRC_DIR = TRUTH_DIR / "lrcs"
OUT_DIR = TRUTH_DIR / "phase1"
STEM_MANIFEST = TRUTH_DIR / "stem_cache/manifest.json"

# Cache roots searched for <hash_prefix>/stems/clean_vocals.flac
CACHE_ROOTS = [
    Path.home() / ".cache/stream-of-worship",
    Path.home() / ".cache/stream-of-worship-admin",
    Path.home() / ".cache/stream-of-worship/stream-of-worship.bak",
]

ENGINE = "whisper"
MODEL = "large-v3"
COMPUTE_TYPE = "int8"

# Injected offsets for recoverability test (seconds)
INJECTED_OFFSETS = [0.5, 1.0]

DEFAULT_DURATION = 5.0  # fallback line duration for last/non-CJK lines
DEFAULT_LAST_LINE_DURATION_S = 5.0

# Gate constants
RECOVERY_TOLERANCE_S = 0.25
RECOVERY_LINE_FRACTION = 0.90
RECOVERY_SONG_FRACTION = 8 / 10


def load_song_set() -> list[dict]:
    """Load the 10-song Phase 1 set: 5 seed positives + 5 seed negatives.

    Set membership = cached stem (stem_cache manifest) AND fetched LRC
    (eval/lrc_truth/lrcs/) AND listed in a seed list. The stem-cache manifest
    covers 46 seed positives, but LRCs were only fetched for the Phase 1
    set, so the intersection is exactly the 10 spec songs (5 positives,
    5 negatives incl. the qwen3_asr and manual_upload ones).
    """
    manifest = json.loads(STEM_MANIFEST.read_text())
    positives = [l.strip() for l in (TRUTH_LISTS_DIR / "seed_positive.txt").read_text().split() if l.strip()]
    negatives = [l.strip() for l in (TRUTH_LISTS_DIR / "seed_negative.txt").read_text().split() if l.strip()]

    songs = []
    for sid, ent in manifest["songs"].items():
        if ent.get("status") != "cached":
            continue
        if not (LRC_DIR / f"{sid}.lrc").exists():
            # LRC fetch is sized to the Phase 1 set; a cached stem without a
            # fetched LRC is outside the set (keeps the gate denominator at
            # the spec's 10 songs rather than every cached seed song).
            continue
        truth = "positive" if sid in positives else "negative" if sid in negatives else None
        if truth is None:
            continue
        songs.append(
            {
                "song_id": sid,
                "hash_prefix": ent["hash_prefix"],
                "truth": truth,
                "source": ent.get("source"),
                "stem": ent.get("audio", "stems/clean_vocals.flac"),
            }
        )
    return songs


def resolve_stem(hash_prefix: str, rel: str) -> Path:
    for root in CACHE_ROOTS:
        p = root / hash_prefix / rel
        if p.exists():
            return p
    raise FileNotFoundError(f"stem not found for {hash_prefix}: {rel}")


def parse_lrc_lines(content: str) -> list[dict]:
    """Parse LRC into line dicts with metadata.

    Returns list of {index, time, text, is_placeholder}. ADR-0008 gap
    placeholders are lines whose text (excluding whitespace/separators) is
    empty or composed solely of separator glyphs.
    """
    lines = []
    for line in content.split("\n"):
        parsed = eval_lrc.parse_enhanced_lrc_line(line)
        if not parsed:
            continue
        line_start, raw_text, _words = parsed
        core = raw_text.replace(" ", "").replace("　", "")
        # ADR-0008 gap placeholders: e.g. "◆", "·", "-", "—" filler lines
        is_placeholder = core != "" and all(
            ch in "◆◇·•∙—–-_~…*○●○○" for ch in core
        )
        lines.append(
            {
                "time": line_start,
                "text": raw_text,
                "is_placeholder": is_placeholder or core == "",
            }
        )
    return lines


def try_line_alignment_words(
    lrc_words: list[eval_lrc.PinyinWord],
    audio_words: list[eval_lrc.PinyinWord],
    lrc_lines: list[tuple[float, str]],
    time_tolerance_s: float = 2.0,
) -> list[eval_lrc.DiffEntry] | None:
    """Attempt per-line alignment using the full word stream.

    Falls back to ``eval_lrc.align_sequences_per_line`` for lines whose text
    contains no CJK characters (English vocals) — that fallback matches on
    raw text via SequenceMatcher.

    Returns None only when the whole song has no pinyin-bearing LRC words
    (caller should rerun the raw per-line alignment).
    """
    total = sum(1 for w in lrc_words if w.pinyin)
    if not lrc_words or total == 0:
        # All-non-CJK LRC (e.g. English): fall back to raw per-line alignment
        # scoped to each line's time window — same path as mixed songs use for
        # their English lines, so English positives are measured, not skipped.
        return align_sequences_per_line_raw(lrc_lines or [], audio_words)

    # Line boundaries by CJK-word counts (lrc words stream is line-ordered)
    py_counts: list[int] = []
    for _t, text in lrc_lines:
        py_counts.append(len(eval_lrc.chinese_to_pinyin(text)))

    result: list[eval_lrc.DiffEntry] = []

    idx_cjk = 0
    audio_idx = 0
    for li, (_t, text) in enumerate(lrc_lines):
        count = py_counts[li]
        if count == 0:
            result.extend(align_sequences_nocjk(lrc_words, audio_words, lrc_lines, li, time_tolerance_s, idx_cjk))
            continue
        window = lrc_words[idx_cjk : idx_cjk + count]
        # Advance audio pointer to words near window start
        start_time = window[0].time_seconds - time_tolerance_s if window else -1
        while audio_idx < len(audio_words) and audio_words[audio_idx].time_seconds < start_time:
            audio_idx += 1
        # Collect audio words within line window
        end_time = window[-1].time_seconds + time_tolerance_s if window else start_time
        collected: list[tuple[int, eval_lrc.PinyinWord]] = []
        j = audio_idx
        while j < len(audio_words) and audio_words[j].time_seconds <= end_time:
            collected.append((j, audio_words[j]))
            j += 1
        lrc_py = [w.pinyin for w in window]
        audio_py = [w.pinyin for _, w in collected]
        matcher = SequenceMatcher(None, lrc_py, audio_py)
        for op, ls, le, as_, ae in matcher.get_opcodes():
            if op == "equal":
                for ii, (jj, aw) in enumerate(collected[as_:ae], start=ls):
                    lw = window[ii]
                    result.append(
                        eval_lrc.DiffEntry(
                            op="equal",
                            lrc_text=lw.text,
                            audio_text=aw.text,
                            lrc_pinyin=lw.pinyin,
                            audio_pinyin=aw.pinyin,
                            lrc_time=lw.time_seconds,
                            audio_time=aw.time_seconds,
                            time_diff=aw.time_seconds - lw.time_seconds,
                        )
                    )
        idx_cjk += count

    return result


def align_sequences_global_fallback(
    lrc_words: list[eval_lrc.PinyinWord], audio_words: list[eval_lrc.PinyinWord]
) -> list[eval_lrc.DiffEntry]:
    """Global sequence alignment when line boundaries are unusable."""
    lrc_py = [w.pinyin for w in lrc_words]
    audio_py = [w.pinyin for w in audio_words]
    matcher = SequenceMatcher(None, lrc_py, audio_py)
    result = []
    for op, ls, le, as_, ae in matcher.get_opcodes():
        if op == "equal":
            for i, j in zip(range(ls, le), range(as_, ae)):
                lw, aw = lrc_words[i], audio_words[j]
                result.append(
                    eval_lrc.DiffEntry(
                        op="equal",
                        lrc_text=lw.text,
                        audio_text=aw.text,
                        lrc_pinyin=lw.pinyin,
                        audio_pinyin=aw.pinyin,
                        lrc_time=lw.time_seconds,
                        audio_time=aw.time_seconds,
                        time_diff=aw.time_seconds - lw.time_seconds,
                    )
                )
    return result


def align_sequences_per_line_raw(
    lrc_lines: list[tuple[float, str]],
    audio_words: list[eval_lrc.PinyinWord],
    time_tolerance_s: float = 2.0,
) -> list[eval_lrc.DiffEntry]:
    """Align non-CJK LRC lines against ASR words by raw latin tokens.

    Each LRC line's window is [line_ts - tol, next_line_ts + tol). Audio words
    in the window are tokenized to latin tokens; the line's latin tokens are
    matched with SequenceMatcher. Equal matches emit DiffEntries with
    lrc_time = line start (the onset-lead estimator's anchor is the line
    timestamp, matching the CJK path's line-level semantics).

    A line whose window contains no audio tokens yields no entries (stays
    unmatched); no cross-line bleed because each line is matched
    independently against its own window.
    """
    result: list[eval_lrc.DiffEntry] = []
    for li, (line_ts, line_text) in enumerate(lrc_lines):
        next_ts = lrc_lines[li + 1][0] if li + 1 < len(lrc_lines) else line_ts + DEFAULT_DURATION
        lrc_tokens = [t for t in re.findall(r"[a-zA-Z']+", line_text.lower()) if len(t) >= 2]
        if not lrc_tokens:
            continue
        window = [w for w in audio_words if line_ts - time_tolerance_s <= w.time_seconds < next_ts + time_tolerance_s]
        audio_tokens: list[tuple[eval_lrc.PinyinWord, str]] = []
        for w in window:
            for tok in re.findall(r"[a-zA-Z']+", w.text.lower()):
                if len(tok) >= 2:
                    audio_tokens.append((w, tok))
        if not audio_tokens:
            continue
        matcher = SequenceMatcher(None, [tok for _, tok in audio_tokens], lrc_tokens)
        for op, as_, ae, ls, le in matcher.get_opcodes():
            if op != "equal":
                continue
            for ai in range(as_, ae):
                w, tok = audio_tokens[ai]
                li_tok = ls + (ai - as_)
                if li_tok >= len(lrc_tokens):
                    break
                result.append(
                    eval_lrc.DiffEntry(
                        op="equal",
                        lrc_text=lrc_tokens[li_tok],
                        audio_text=tok,
                        lrc_pinyin="",
                        audio_pinyin="",
                        lrc_time=line_ts,
                        audio_time=w.time_seconds,
                        time_diff=w.time_seconds - line_ts,
                    )
                )
    return result


def align_sequences_nocjk(
    lrc_words: list[eval_lrc.PinyinWord],
    audio_words: list[eval_lrc.PinyinWord],
    lrc_lines: list[tuple[float, str]],
    line_index: int,
    time_tolerance_s: float,
    cjk_words_before: int,
) -> list[eval_lrc.DiffEntry]:
    """Align a non-CJK (e.g. English) LRC line against raw audio text.

    The audio stream is Chinese-pinyin-oriented; for English lines we match
    raw latin tokens. Uses the audio words whose pinyin field is empty OR
    whose text is non-CJK. Falls back to matching nothing (line stays
    unmatched) — the report shows English lines with low match rates rather
    than crashing.
    """
    # English audio transcribed via a zh-forced model usually still yields
    # latin tokens. SequenceMatcher on lowercase raw text.
    line_ts, line_text = lrc_lines[line_index]
    next_ts = (
        lrc_lines[line_index + 1][0] if line_index + 1 < len(lrc_lines) else line_ts + DEFAULT_DURATION
    )
    window_audio = [
        w
        for w in audio_words
        if line_ts - time_tolerance_s <= w.time_seconds < next_ts + time_tolerance_s
    ]
    lrc_tokens = re.findall(r"[a-zA-Z']+", line_text.lower())
    audio_tokens = []
    for w in window_audio:
        audio_tokens.extend((w, tok) for tok in re.findall(r"[a-zA-Z']+", w.text.lower()))

    if not lrc_tokens or not audio_tokens:
        return []

    matcher = SequenceMatcher(None, [t for _, t in audio_tokens], lrc_tokens)
    # Reverse roles: we want (audio_index -> lrc tokens)
    result = []
    for op, as_, ae, ls, le in matcher.get_opcodes():
        if op == "equal":
            for ai in range(as_, ae):
                w, tok = audio_tokens[ai]
                li_tok = ls + (ai - as_)
                if li_tok >= len(lrc_tokens):
                    break
                # onset estimate: audio word time (single token per word)
                result.append(
                    eval_lrc.DiffEntry(
                        op="equal",
                        lrc_text=lrc_tokens[li_tok],
                        audio_text=tok,
                        lrc_pinyin="",
                        audio_pinyin="",
                        lrc_time=line_ts,
                        audio_time=w.time_seconds,
                        time_diff=w.time_seconds - line_ts,
                    )
                )
    return result


_PROVENANCE_JSON = REPO_ROOT / "eval/lrc_truth/latest.json"


@functools.lru_cache(maxsize=None)
def _provenance_map() -> dict[str, str]:
    """LRC provenance from the Phase 0 snapshot (eval/lrc_truth/latest.json).

    The snapshot's ``seed_subsets.negative.lrc_source_provenance`` is
    self-described as authoritative and was captured with the truth lists; a
    live DB query adds nothing and risks drift between the snapshot and the
    analysis. The stem-cache manifest's ``source`` field is stem-separation
    provenance (mvsep / r2_vocals_dry), NOT LRC provenance.
    """
    try:
        d = json.loads(_PROVENANCE_JSON.read_text())
        return dict(d["seed_subsets"]["negative"]["lrc_source_provenance"])
    except (OSError, KeyError, json.JSONDecodeError):
        # Fallback: seed lists in the spec (Appendix A) — only authoritative
        # for the 8 songs listed there.
        spec_sources = {
            "bu_ting_zan_mei_mi_e937a9d3": "manual_upload",
            "shu_bu_jin_71fba0ce": "youtube_transcript",
            "wo_neng_gei_ni_shen_me_03b2dcb2": "youtube_transcript",
            "jing_bai_ye_su_b08227a2": "youtube_transcript",
            "wo_jing_bai_mi__ye_su_e6dd6146": "youtube_transcript",
            "na_me_shen_de_ke_mu_ff92abd9": "youtube_transcript",
            "cang_shen_zhi_chu_39437ec0": "youtube_transcript",
            "ai_shi_wo_men_yong_gan_6d1865b8": "qwen3_asr",
        }
        return spec_sources


def _song_source(song_id: str) -> str:
    """LRC provenance for a song, from the Phase 0 snapshot provenance map."""
    return _provenance_map().get(song_id, "unknown")


def load_or_transcribe_audio_words(song: dict, audio_path: Path) -> list[eval_lrc.PinyinWord]:
    """Return the ASR word stream for a song, transcribing once and caching to disk.

    Cache format: eval/lrc_truth/phase1/<song_id>.words.json
    [ {"text":..., "pinyin":..., "time_seconds":...}, ... ]

    Recoverability runs re-align shifted LRC against this fixed stream instead
    of re-running large-v3 per offset (audio bytes are identical; a
    deterministic ASR would reproduce the same words, so re-transcription adds
    no signal).
    """
    cache = OUT_DIR / f"{song['song_id']}.words.json"
    if cache.exists():
        return [
            eval_lrc.PinyinWord(text=w["text"], pinyin=w["pinyin"], time_seconds=w["time_seconds"])
            for w in json.loads(cache.read_text())
        ]
    # Single full-file pass with faster-whisper's internal Silero VAD
    # (vad_filter=True). The generic transcribe_with_segmentation("vad") path
    # re-runs the model on dozens of
    # short clips (~2 min/clip on this CPU); a whole-file pass is one order
    # of magnitude faster and yields the same word stream with word
    # timestamps.
    words = eval_lrc.transcribe_with_whisper(
        audio_path=audio_path,
        model_name=MODEL,
        device="cpu",
        compute_type=COMPUTE_TYPE,
        language=None,
        lyrics_text=None,
    )
    cache.write_text(
        json.dumps(
            [{"text": w.text, "pinyin": w.pinyin, "time_seconds": w.time_seconds} for w in words]
        )
    )
    return words


def measure_song(
    song: dict,
    lrc_content: str,
    audio_path: Path,
    bpm: float,
    offset_s: float = 0.0,
    audio_words: list[eval_lrc.PinyinWord] | None = None,
) -> dict:
    """Compute per-line onset-lead for one LRC against the song's word stream.

    offset_s: synthetic uniform offset injected into LRC timestamps (positive
    = LRC shifted later, i.e. leading more).

    audio_words: optional pre-transcribed word stream (from
    load_or_transcribe_audio_words); when omitted the ASR runs here.
    """
    console = eval_lrc.Console(stderr=True)

    lines = parse_lrc_lines(lrc_content)
    # Inject offset into line timestamps
    shifted_lines = [(ln["time"] + offset_s, ln["text"]) for ln in lines]
    lrc_words = eval_lrc.parse_lrc_file(lrc_content)
    if offset_s:
        lrc_words = [
            eval_lrc.PinyinWord(text=w.text, pinyin=w.pinyin, time_seconds=w.time_seconds + offset_s)
            for w in lrc_words
        ]

    if audio_words is None:
        audio_words = load_or_transcribe_audio_words(song, audio_path)

    diff = try_line_alignment_words(lrc_words, audio_words, shifted_lines)
    if diff is None:
        # English/non-CJK lines carry no pinyin; fall back to raw diff whose
        # entries can still be mapped to lines via lrc_time. Recoverability
        # (which needs line-indexed leads) only ever runs on positive songs —
        # if a positive has no pinyin at all it is reported as unmatched.
        diff = eval_lrc.align_sequences_per_line(lrc_words, audio_words, shifted_lines)

    # Map each matched (equal) diff entry to its LRC line. Two paths:
    #  - word-stream alignment (pinyin present): word index -> line via cumulative counts
    #  - raw per-line alignment (fallback): entry already scoped to a line window;
    #    attribute it to the line whose [time, next_time) span contains lrc_time.
    line_word_counts = [len(eval_lrc.chinese_to_pinyin(text)) for _t, text in shifted_lines]
    total_words = sum(line_word_counts)
    word_indexed = total_words == len(lrc_words) and total_words > 0
    if not word_indexed and not total_words:
        # No pinyin anywhere: attribute entries by lrc_time window
        boundaries = [t for t, _ in shifted_lines] + [float("inf")]
        per_line: dict[int, list[float]] = {}
        for d in diff:
            if d.op != "equal" or d.time_diff is None or d.lrc_time is None:
                continue
            for li in range(len(shifted_lines)):
                if boundaries[li] <= d.lrc_time < boundaries[li + 1]:
                    per_line.setdefault(li, []).append(d.audio_time)
                    break
    else:
        per_line: dict[int, list[float]] = {}
        consumed: set[int] = set()
        for d in diff:
            if d.op != "equal" or d.time_diff is None:
                continue
            for wi, w in enumerate(lrc_words):
                if wi in consumed:
                    continue
                if abs(w.time_seconds - d.lrc_time) < 1e-6 and w.pinyin == d.lrc_pinyin:
                    consumed.add(wi)
                    li = None
                    acc = 0
                    for li_cand, count in enumerate(line_word_counts):
                        if acc <= wi < acc + count:
                            li = li_cand
                            break
                        acc += count
                    if li is not None:
                        per_line.setdefault(li, []).append(d.audio_time)
                    break

    results = []
    for li, ln in enumerate(lines):
        onsets = per_line.get(li)
        entry = {
            "line_index": li,
            "time": ln["time"] + offset_s,
            "base_time": ln["time"],
            "text": ln["text"],
            "is_placeholder": ln["is_placeholder"],
        }
        if onsets and not ln["is_placeholder"]:
            onset = min(onsets)  # line's first matched word onset
            lead_s = onset - (ln["time"] + offset_s)
            entry.update(
                {
                    "matched": True,
                    "onset": onset,
                    "lead_s": lead_s,
                    "lead_beats": lead_s * bpm / 60.0,
                    "matched_words": len(onsets),
                }
            )
        else:
            entry.update({"matched": False, "lead_s": None, "lead_beats": None})
        results.append(entry)

    sung = [r for r in results if not r["is_placeholder"]]
    matched = [r for r in sung if r["matched"]]
    leads = [r["lead_s"] for r in matched]

    return {
        "song_id": song["song_id"],
        "truth": song["truth"],
        "bpm": bpm,
        "offset_s": offset_s,
        "n_lines": len(lines),
        "n_sung": len(sung),
        "n_matched": len(matched),
        "match_rate": len(matched) / len(sung) if sung else 0.0,
        "lead_s_mean": statistics.mean(leads) if leads else None,
        "lead_s_median": statistics.median(leads) if leads else None,
        "lead_s_stdev": statistics.stdev(leads) if len(leads) > 1 else None,
        "lines": results,
        "n_audio_words": len(audio_words),
    }


def load_bpm_map() -> dict[str, float]:
    """Fetch tempo_bpm for all songs via one psycopg query; cached to disk.

    A long ASR run outlives a Neon connection (AdminShutdown kills idle
    pooled connections), so BPM is snapshotted once instead of queried
    per-song mid-run.
    """
    import psycopg

    cache = OUT_DIR / "bpm_cache.json"
    if cache.exists():
        return json.loads(cache.read_text())
    from stream_of_worship.admin.config import AdminConfig

    dsn = AdminConfig.load().get_connection_url()
    with psycopg.connect(dsn, connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute("SELECT song_id, tempo_bpm FROM recordings WHERE deleted_at IS NULL")
        rows = cur.fetchall()
    m = {r[0]: float(r[1]) for r in rows if r[1]}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(m))
    return m


@app.command()
def measure(
    songs: str = typer.Option(None, "--songs", help="Comma-separated song IDs (default: 10-song set)"),
) -> None:
    """Run the base measurement (offset 0) for all set songs; saves JSON per song."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bpm_map = load_bpm_map()

    set_songs = load_song_set()
    if songs:
        wanted = set(songs.split(","))
        set_songs = [s for s in set_songs if s["song_id"] in wanted]

    for song in set_songs:
        out_path = OUT_DIR / f"{song['song_id']}.base.json"
        if out_path.exists():
            print(f"skip (exists): {song['song_id']}")
            continue
        lrc_path = LRC_DIR / f"{song['song_id']}.lrc"
        if not lrc_path.exists():
            print(f"NO LRC: {song['song_id']}")
            continue
        audio = resolve_stem(song["hash_prefix"], song["stem"])
        bpm = bpm_map.get(song["song_id"])
        if not bpm:
            print(f"NO BPM: {song['song_id']}")
            continue
        print(f"=== {song['song_id']} ({song['truth']}) bpm={bpm} audio={audio}")
        words = load_or_transcribe_audio_words(song, audio)
        result = measure_song(song, lrc_path.read_text(encoding="utf-8"), audio, bpm, audio_words=words)
        out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(
            f"  lines={result['n_lines']} sung={result['n_sung']} matched={result['n_matched']}"
            f" ({result['match_rate']:.0%}) lead_mean={result['lead_s_mean']}s"
        )


@app.command()
def recover(
    songs: str = typer.Option(None, "--songs", help="Comma-separated song IDs (default: all 10)"),
) -> None:
    """Run recoverability injections (+0.5s, +1.0s) on ALL set songs.

    Issue #245 states the gate as "≥8/10 songs": recoverability tests the
    estimator's ability to recover an injected uniform offset, not LRC
    correctness, so negatives are legitimate subjects (drifted LRCs test
    whether a uniform component is recoverable from a drifted stream).
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bpm_map = load_bpm_map()

    set_songs = load_song_set()
    if songs:
        wanted = set(songs.split(","))
        set_songs = [s for s in set_songs if s["song_id"] in wanted]

    for song in set_songs:
        lrc_path = LRC_DIR / f"{song['song_id']}.lrc"
        if not lrc_path.exists():
            print(f"NO LRC: {song['song_id']}")
            continue
        audio = resolve_stem(song["hash_prefix"], song["stem"])
        bpm = bpm_map.get(song["song_id"])
        if not bpm:
            print(f"NO BPM: {song['song_id']}")
            continue
        words = load_or_transcribe_audio_words(song, audio)
        for offset in INJECTED_OFFSETS:
            out_path = OUT_DIR / f"{song['song_id']}.inject{offset:+.1f}.json".replace("+", "p")
            if out_path.exists():
                print(f"skip (exists): {song['song_id']} {offset}")
                continue
            print(f"=== {song['song_id']} inject {offset:+.1f}s")
            result = measure_song(
                song,
                lrc_path.read_text(encoding="utf-8"),
                audio,
                bpm,
                offset_s=offset,
                audio_words=words,
            )
            out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print(
                f"  matched={result['n_matched']}/{result['n_sung']}"
                f" lead_mean={result['lead_s_mean']}s"
            )


def _analyze() -> dict:
    """Analyze all measurement JSONs: separation + recoverability + gate."""
    base = _load_base()

    pos_leads_beats: list[float] = []
    timing_neg_leads_beats: list[float] = []  # youtube_transcript = timing failure
    other_neg_leads_beats: list[float] = []
    per_song_summary = []
    for sid, r in base.items():
        sung = [l for l in r["lines"] if not l["is_placeholder"]]
        beats = [l["lead_beats"] for l in sung if l["matched"] and l["lead_beats"] is not None]
        secs = [l["lead_s"] for l in sung if l["matched"] and l["lead_s"] is not None]
        summary = {
            "song_id": sid,
            "truth": r["truth"],
            "source": _song_source(sid),
            "bpm": r["bpm"],
            "n_sung": r["n_sung"],
            "n_matched": r["n_matched"],
            "match_rate": round(r["match_rate"], 3),
            "lead_s_mean": round(r["lead_s_mean"], 3) if r["lead_s_mean"] is not None else None,
            "lead_s_median": round(r["lead_s_median"], 3) if r["lead_s_median"] is not None else None,
            "lead_s_stdev": round(r["lead_s_stdev"], 3) if r["lead_s_stdev"] is not None else None,
            "lead_beats_p10": round(statistics.quantiles(beats, n=10)[0], 2) if len(beats) >= 10 else None,
            "lead_beats_p90": round(statistics.quantiles(beats, n=10)[8], 2) if len(beats) >= 10 else None,
            "n_late_lines": sum(1 for s in secs if s < 0),  # onset BEFORE timestamp => late LRC
        }
        per_song_summary.append(summary)
        if r["truth"] == "positive":
            pos_leads_beats.extend(beats)
        elif _song_source(sid) == "youtube_transcript":
            timing_neg_leads_beats.extend(beats)
        else:
            other_neg_leads_beats.extend(beats)

    # Recoverability: for each injected offset, recovered mean = measured
    # lead_mean(offset) - lead_mean(base); compare against injected. Runs over
    # ALL set songs (gate is ≥8/10): the estimator is under test, not LRC
    # correctness.
    recovery = []
    for sid, r in base.items():
        for offset in INJECTED_OFFSETS:
            inj_path = OUT_DIR / f"{sid}.inject{offset:+.1f}.json".replace("+", "p")
            if not inj_path.exists():
                continue
            inj = json.loads(inj_path.read_text())
            base_leads = {
                l["line_index"]: l["lead_s"] for l in r["lines"] if l["matched"] and l["lead_s"] is not None
            }
            inj_leads = {
                l["line_index"]: l["lead_s"] for l in inj["lines"] if l["matched"] and l["lead_s"] is not None
            }
            # Per-line recovered offset: lead(injected) - lead(base). A uniform
            # injected shift of +offset into the LRC timestamps makes every
            # lead smaller by offset... but we ALSO shifted the timestamps in
            # the measurement, so: lead_inj = onset - (t + offset)
            #                       lead_base = onset - t
            #                       lead_inj - lead_base = -offset
            # Recovered offset = -(lead_inj - lead_base) = offset (as injected).
            common = sorted(set(base_leads) & set(inj_leads))
            deltas = [inj_leads[i] - base_leads[i] for i in common]
            recovered = [-d for d in deltas]
            within = sum(1 for v in recovered if abs(v - offset) <= RECOVERY_TOLERANCE_S)
            # Denominator is ALL sung lines of the song, not just lines the
            # base and injected measurements had in common — otherwise a
            # song with few matched lines passes the ≥90% gate trivially.
            n_sung = r["n_sung"] or 1
            recovery.append(
                {
                    "song_id": sid,
                    "injected_s": offset,
                    "n_lines": len(common),
                    "n_sung": r["n_sung"],
                    "recovered_mean_s": round(statistics.mean(recovered), 3) if recovered else None,
                    "recovered_median_s": round(statistics.median(recovered), 3) if recovered else None,
                    "within_tolerance": within,
                    "fraction_within": round(within / n_sung, 3) if common else None,
                    # Permissive denominator: lines the estimator actually
                    # measured in BOTH runs. English/no-pinyin lines that never
                    # match are unmeasurable, not recovery failures; reported
                    # alongside the strict fraction so the gate report can
                    # show both readings of "≥90% of sung lines".
                    "fraction_within_matched": round(within / len(common), 3) if common else None,
                }
            )

    return {
        "per_song": per_song_summary,
        "separation": {
            "positive_lead_beats_mean": round(statistics.mean(pos_leads_beats), 2) if pos_leads_beats else None,
            "positive_lead_beats_median": round(statistics.median(pos_leads_beats), 2) if pos_leads_beats else None,
            "positive_lead_beats_stdev": round(statistics.stdev(pos_leads_beats), 2) if len(pos_leads_beats) > 1 else None,
            "timing_negative_lead_beats_mean": round(statistics.mean(timing_neg_leads_beats), 2) if timing_neg_leads_beats else None,
            "timing_negative_lead_beats_median": round(statistics.median(timing_neg_leads_beats), 2) if timing_neg_leads_beats else None,
            "timing_negative_lead_beats_stdev": round(statistics.stdev(timing_neg_leads_beats), 2) if len(timing_neg_leads_beats) > 1 else None,
            "other_negative_lead_beats_mean": round(statistics.mean(other_neg_leads_beats), 2) if other_neg_leads_beats else None,
            "other_negative_lead_beats_median": round(statistics.median(other_neg_leads_beats), 2) if other_neg_leads_beats else None,
            "n_positive_leads": len(pos_leads_beats),
            "n_timing_negative_leads": len(timing_neg_leads_beats),
            "n_other_negative_leads": len(other_neg_leads_beats),
        },
        "recoverability": recovery,
    }


def _load_base() -> dict:
    """Load all base measurement JSONs keyed by song id."""
    return {
        p.stem.replace(".base", ""): json.loads(p.read_text())
        for p in sorted(OUT_DIR.glob("*.base.json"))
    }


@app.command()
def analyze() -> None:
    """Analyze measured JSONs and evaluate the Phase 1 gate."""
    report = _analyze()
    base = _load_base()

    # Gate (b): fraction-within ≥90% of sung lines for ≥8/10 songs, per
    # injected offset. Injections run over all 10 songs (estimator under test).
    rec_by_offset = {}
    missing: dict[float, list[str]] = {}
    for offset in INJECTED_OFFSETS:
        rows = [r for r in report["recoverability"] if r["injected_s"] == offset]
        passed = [r for r in rows if r["fraction_within"] is not None and r["fraction_within"] >= RECOVERY_LINE_FRACTION]
        passed_matched = [
            r for r in rows if r["fraction_within_matched"] is not None and r["fraction_within_matched"] >= RECOVERY_LINE_FRACTION
        ]
        rec_by_offset[offset] = {
            "n_songs": len(rows),
            "n_pass": len(passed),
            "song_fraction": round(len(passed) / len(rows), 2) if rows else None,
            "passing_songs": [r["song_id"] for r in passed],
            "failing_songs": [r["song_id"] for r in rows if r not in passed],
            # Permissive reading: denominator = lines measured in both runs.
            "n_pass_matched_denom": len(passed_matched),
            "song_fraction_matched_denom": round(len(passed_matched) / len(rows), 2) if rows else None,
            "passing_songs_matched_denom": [r["song_id"] for r in passed_matched],
        }

    # Gate (a): separation — timing-attributed negatives (youtube_transcript
    # seeds: gross drift) vs positives, on PER-LINE lead distributions, not
    # medians. Criterion: the timing-negative per-line distribution sits
    # outside the positive per-line band — specifically the timing-negative
    # median beyond the positive p10–p90 spread, AND the timing-negative
    # interquartile range disjoint from the positive IQR.
    pos_lines = sorted(
        l["lead_s"]
        for r in base.values() if r["truth"] == "positive"
        for l in r["lines"] if l["matched"] and l["lead_s"] is not None and not l["is_placeholder"]
    )
    timing_neg_lines = sorted(
        l["lead_s"]
        for r_sid, r in base.items()
        if r["truth"] == "negative" and _song_source(r_sid) == "youtube_transcript"
        for l in r["lines"] if l["matched"] and l["lead_s"] is not None and not l["is_placeholder"]
    )

    def _quantiles(xs: list[float]) -> tuple[float, float, float] | None:
        if len(xs) >= 10:
            q = statistics.quantiles(xs, n=10)
            return round(q[0], 3), round(q[4], 3), round(q[8], 3)  # p10, p50, p90
        return None

    pos_q = _quantiles(pos_lines)
    tneg_q = _quantiles(timing_neg_lines)
    median_separated = (
        pos_q is not None and tneg_q is not None and not (pos_q[0] <= tneg_q[1] <= pos_q[2])
    )

    if pos_q is not None and tneg_q is not None:
        pos_p10, _pos_p50, pos_p90 = pos_q
        t_p10, t_p50, t_p90 = tneg_q
        iqr_disjoint = t_p90 < pos_p10 or t_p10 > pos_p90
    else:
        iqr_disjoint = None

    sep_report = {
        "attribution_note": (
            "Only youtube_transcript negatives carry timing failure ground "
            "truth; qwen3_asr (content) and manual_upload (anomaly) negatives "
            "are excluded from the separation test."
        ),
        "positive_lead_s_quantiles_p10_p50_p90": pos_q,
        "timing_negative_lead_s_quantiles_p10_p50_p90": tneg_q,
        "timing_negative_median_s": tneg_q[1] if tneg_q else None,
        "median_outside_positive_band": median_separated,
        "iqr_disjoint": iqr_disjoint,
    }

    # Hardening: the gate must not benefit from a shrunken denominator. If
    # any set song is missing an injection measurement, fail the offset
    # rather than computing song_fraction over fewer songs.
    expected_songs = {s["song_id"] for s in load_song_set()}
    for offset in INJECTED_OFFSETS:
        have = {r["song_id"] for r in report["recoverability"] if r["injected_s"] == offset}
        missing[offset] = sorted(expected_songs - have)

    # Hardening: separation needs the timing-attributed negative bucket to be
    # populated. The expected count is the youtube_transcript negatives
    # actually in the set (per the snapshot provenance); if the resolved
    # count differs, the test is invalid, not "passed trivially".
    expected_timing_neg = {
        sid
        for sid in expected_songs
        if _song_source(sid) == "youtube_transcript"
        and next(s for s in load_song_set() if s["song_id"] == sid)["truth"] == "negative"
    }

    rec_ok = (
        all(v["song_fraction"] is not None and v["song_fraction"] >= RECOVERY_SONG_FRACTION for v in rec_by_offset.values())
        and all(v["n_songs"] == 10 for v in rec_by_offset.values())
    )
    n_timing_neg_songs = len({r_sid for r_sid, r in base.items() if r["truth"] == "negative" and _song_source(r_sid) == "youtube_transcript"})
    sep_ok = bool(median_separated and iqr_disjoint) and n_timing_neg_songs == len(expected_timing_neg)
    gate = {
        "recoverability_per_offset": rec_by_offset,
        "recoverability_pass": rec_ok,
        "missing_injections": {o: m for o, m in missing.items() if m},
        "separation": sep_report,
        "separation_pass": sep_ok,
        "n_timing_negative_songs": n_timing_neg_songs,
        "gate_pass": rec_ok and sep_ok,
    }
    report["gate"] = gate

    out = OUT_DIR / "analysis.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report["gate"], ensure_ascii=False, indent=2))
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    app()