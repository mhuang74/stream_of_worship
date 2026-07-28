"""Pool enrichment distribution report generation."""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable

from stream_of_worship.admin.songset_constructor.config import RunConfig
from stream_of_worship.admin.songset_constructor.models import SongCandidate
from stream_of_worship.admin.songset_constructor.rules.themes import THEMES, classify_lyrics_themes, classify_title_themes

PHASE_NAMES: dict[int, str] = {1: "讚美", 2: "感恩", 3: "敬拜", 4: "奉獻", 5: "差遣"}
UNDERREPRESENTED_PHASE_PCT = 15.0
UNDERREPRESENTED_THEME_PCT = 2.0
BAR_SCALE = 0.04


def _shannon_entropy(counts: Iterable[int]) -> float:
    values = [count for count in counts if count > 0]
    total = sum(values)
    if total <= 0:
        return 0.0
    return -sum((count / total) * math.log2(count / total) for count in values)


def _dominant_theme(candidate: SongCandidate) -> str | None:
    if not candidate.themes:
        return None
    positive = {theme: score for theme, score in candidate.themes.items() if score > 0}
    if not positive:
        return None
    return max(positive.items(), key=lambda item: (item[1], item[0]))[0]


def _bar(pct: float) -> str:
    blocks = max(0, int(round(pct * BAR_SCALE)))
    return "▓" * blocks


def _phase_label(phase: int) -> str:
    return f"Phase {phase} ({PHASE_NAMES.get(phase, '?')})"


def build_enrichment_report(
    *,
    pool: list[SongCandidate],
    config: RunConfig,
    load_trace: dict[str, Any] | None = None,
    enrich_trace: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    load_trace = load_trace or {}
    enrich_trace = enrich_trace or {}
    loaded_size = int(load_trace.get("pool_size", len(pool)))
    enriched_size = len(pool)
    dropped = int(enrich_trace.get("dropped", max(0, loaded_size - enriched_size)))
    drop_reasons = enrich_trace.get("drop_reasons") or {}
    dropped_samples = enrich_trace.get("dropped_samples") or []

    phase_counts: Counter[int] = Counter(candidate.phase for candidate in pool)
    secondary_phase_counts: Counter[int] = Counter()
    for c in pool:
        for p in c.secondary_phases:
            secondary_phase_counts[p] += 1
    theme_dominance: Counter[str] = Counter()
    zero_theme_count = 0
    for candidate in pool:
        dominant = _dominant_theme(candidate)
        if dominant is None:
            zero_theme_count += 1
        else:
            theme_dominance[dominant] += 1

    theme_inferred = sum(1 for candidate in pool if candidate.themes and max(candidate.themes.values(), default=0.0) > 0)
    tempo_fallback = enriched_size - theme_inferred
    tempo_fallback_no_tempo = sum(
        1 for candidate in pool
        if (not candidate.themes or max(candidate.themes.values(), default=0.0) == 0.0) and candidate.tempo_bpm is None
    )

    title_hits = sum(1 for candidate in pool if any(v > 0 for v in classify_title_themes(candidate.title, candidate.title_pinyin).values()))
    lyrics_hits = sum(1 for candidate in pool if any(v > 0 for v in classify_lyrics_themes(candidate.lyrics_raw).values()))
    song_emb_hits = sum(1 for candidate in pool if candidate.song_theme_scores_raw)
    line_emb_hits = sum(1 for candidate in pool if candidate.line_theme_scores_raw)

    tempo_known = sum(1 for candidate in pool if candidate.tempo_bpm is not None)
    tempo_missing = enriched_size - tempo_known
    bpms = sorted(candidate.tempo_bpm for candidate in pool if candidate.tempo_bpm is not None)
    bpm_min = bpms[0] if bpms else None
    bpm_max = bpms[-1] if bpms else None
    bpm_median: float | None = None
    if bpms:
        mid = len(bpms) // 2
        bpm_median = bpms[mid] if len(bpms) % 2 == 1 else (bpms[mid - 1] + bpms[mid]) / 2

    key_known = sum(1 for candidate in pool if candidate.musical_key)
    key_missing = enriched_size - key_known
    low_confidence_key = sum(1 for candidate in pool if candidate.key_confidence is not None and candidate.key_confidence < 0.6)

    album_series_counts: Counter[str | None] = Counter(candidate.album_series for candidate in pool)
    unique_themes_covered = len({theme for theme in theme_dominance if theme})
    theme_entropy = _shannon_entropy(theme_dominance.values())
    phase_entropy = _shannon_entropy(phase_counts.values())
    max_theme_entropy = math.log2(len(THEMES)) if THEMES else 0.0
    max_phase_entropy = math.log2(len(PHASE_NAMES)) if PHASE_NAMES else 0.0

    phase_distribution: list[dict[str, Any]] = []
    for phase in sorted(PHASE_NAMES):
        count = phase_counts.get(phase, 0)
        pct = (count / enriched_size * 100) if enriched_size else 0.0
        secondary_count = secondary_phase_counts.get(phase, 0)
        phase_distribution.append({
            "phase": phase, "label": _phase_label(phase),
            "count": count, "secondary_count": secondary_count,
            "pct": pct, "underrepresented": pct < UNDERREPRESENTED_PHASE_PCT if enriched_size else False,
        })

    theme_distribution: list[dict[str, Any]] = []
    for theme in THEMES:
        count = theme_dominance.get(theme, 0)
        pct = (count / enriched_size * 100) if enriched_size else 0.0
        theme_distribution.append({
            "theme": theme, "count": count, "pct": pct,
            "underrepresented": pct < UNDERREPRESENTED_THEME_PCT if enriched_size else False,
        })

    metrics: dict[str, Any] = {
        "loaded_size": loaded_size, "enriched_size": enriched_size, "dropped": dropped,
        "drop_reasons": dict(drop_reasons), "dropped_samples": list(dropped_samples),
        "phase_distribution": phase_distribution, "theme_distribution": theme_distribution,
        "zero_theme_count": zero_theme_count, "theme_inferred": theme_inferred,
        "tempo_fallback": tempo_fallback, "tempo_fallback_no_tempo": tempo_fallback_no_tempo,
        "signal_coverage": {"title_hits": title_hits, "lyrics_hits": lyrics_hits, "song_embedding": song_emb_hits, "line_embeddings": line_emb_hits},
        "tempo_key_coverage": {"tempo_known": tempo_known, "tempo_missing": tempo_missing, "bpm_min": bpm_min, "bpm_max": bpm_max, "bpm_median": bpm_median, "key_known": key_known, "key_missing": key_missing, "low_confidence_key": low_confidence_key},
        "album_series_distribution": [{"series": series, "count": count} for series, count in album_series_counts.most_common()],
        "diversity": {"unique_themes_covered": unique_themes_covered, "theme_entropy": theme_entropy, "max_theme_entropy": max_theme_entropy, "phase_entropy": phase_entropy, "max_phase_entropy": max_phase_entropy, "secondary_phase_counts": dict(sorted(secondary_phase_counts.items()))},
    }

    report_text = _render_markdown(metrics, config)
    return metrics, report_text


def _render_markdown(metrics: dict[str, Any], config: RunConfig) -> str:
    lines = ["# Enrichment Report", ""]
    lines.append(f"- Thread ID: `{config.thread_id}`")
    lines.append(f"- Pool limit: {config.pool}")
    lines.append(f"- Album series filter: {config.album_series or '(none)'}")
    lines.append(f"- Season: {config.season or '(none)'}")
    lines.append("")
    lines.append("## Pool Overview")
    lines.append("")
    lines.append(f"- Loaded from catalog: **{metrics['loaded_size']}**")
    lines.append(f"- Enriched (after drops): **{metrics['enriched_size']}**")
    lines.append(f"- Dropped: **{metrics['dropped']}**")
    if metrics["drop_reasons"]:
        for reason, count in sorted(metrics["drop_reasons"].items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"  - `{reason}`: {count}")
    if metrics["dropped_samples"]:
        lines.append("")
        lines.append("### Dropped samples")
        for sample in metrics["dropped_samples"]:
            lines.append(f"- {sample.get('title', '?')} (`{sample.get('recording_hash_prefix', '?')}`): {sample.get('reason', '?')}")
    lines.append("")
    lines.append("## Phase Distribution")
    lines.append("")
    lines.append("| Phase | Count | Secondary | % | Balance | Bar |")
    lines.append("|---|---:|---:|---:|---|---|")
    for entry in metrics["phase_distribution"]:
        balance = "underrepresented" if entry["underrepresented"] else "ok"
        lines.append(f"| {entry['label']} | {entry['count']} | {entry.get('secondary_count', 0)} | {entry['pct']:.1f}% | {balance} | {_bar(entry['pct'])} |")
    lines.append("")
    lines.append("## Theme Dominance")
    lines.append("")
    lines.append("| Theme | Count | % | Status |")
    lines.append("|---|---:|---:|---|")
    for entry in metrics["theme_distribution"]:
        status = "underrepresented" if entry["underrepresented"] else "ok"
        lines.append(f"| {entry['theme']} | {entry['count']} | {entry['pct']:.1f}% | {status} |")
    lines.append(f"- Zero-theme songs: **{metrics['zero_theme_count']}**")
    lines.append("")
    lines.append("## Phase Inference Source")
    lines.append("")
    lines.append(f"- From themes: **{metrics['theme_inferred']}**")
    lines.append(f"- Tempo-only fallback: **{metrics['tempo_fallback']}**")
    lines.append(f"- Tempo fallback with no tempo (default phase 3): **{metrics['tempo_fallback_no_tempo']}**")
    lines.append("")
    lines.append("## Theme Signal Coverage")
    lines.append("")
    signal = metrics["signal_coverage"]
    total = metrics["enriched_size"]
    lines.append(f"- Title hits: {signal['title_hits']}/{total}")
    lines.append(f"- Lyrics hits: {signal['lyrics_hits']}/{total}")
    lines.append(f"- Song embedding present: {signal['song_embedding']}/{total}")
    lines.append(f"- Line embeddings present: {signal['line_embeddings']}/{total}")
    lines.append("")
    lines.append("## Tempo & Key Coverage")
    lines.append("")
    tk = metrics["tempo_key_coverage"]
    lines.append(f"- Tempo known: {tk['tempo_known']}/{total} (missing: {tk['tempo_missing']})")
    bpm_summary = "n/a"
    if tk["bpm_min"] is not None:
        bpm_summary = f"min={tk['bpm_min']:.1f}, max={tk['bpm_max']:.1f}, median={tk['bpm_median']:.1f}"
    lines.append(f"- BPM range: {bpm_summary}")
    lines.append(f"- Key known: {tk['key_known']}/{total} (missing: {tk['key_missing']})")
    lines.append(f"- Low-confidence keys (<0.6): {tk['low_confidence_key']}")
    lines.append("")
    lines.append("## Album Series Distribution")
    lines.append("")
    if metrics["album_series_distribution"]:
        lines.append("| Series | Count |")
        lines.append("|---|---:|")
        for entry in metrics["album_series_distribution"]:
            series = entry["series"] if entry["series"] is not None else "(none)"
            lines.append(f"| {series} | {entry['count']} |")
    else:
        lines.append("_No album series data._")
    lines.append("")
    lines.append("## Diversity Assessment")
    lines.append("")
    div = metrics["diversity"]
    lines.append(f"- Unique themes covered: {div['unique_themes_covered']}/{len(THEMES)}")
    lines.append(f"- Theme entropy: {div['theme_entropy']:.3f} bits (max {div['max_theme_entropy']:.3f})")
    lines.append(f"- Phase entropy: {div['phase_entropy']:.3f} bits (max {div['max_phase_entropy']:.3f})")
    lines.append("")
    return "\n".join(lines)


def render_console_summary(metrics: dict[str, Any], report_path: str) -> str:
    lines = ["Enrichment Report", "================="]
    drop_reason = ""
    if metrics["drop_reasons"]:
        drop_reason = ": " + ", ".join(f"{reason} ({count})" for reason, count in sorted(metrics["drop_reasons"].items(), key=lambda item: (-item[1], item[0])))
    lines.append(f"Pool: {metrics['loaded_size']} loaded → {metrics['enriched_size']} enriched ({metrics['dropped']} dropped{drop_reason})")
    lines.append("")
    lines.append("Phase Distribution:")
    for entry in metrics["phase_distribution"]:
        marker = " ← underrepresented" if entry["underrepresented"] else ""
        sec = entry.get("secondary_count", 0)
        sec_str = f" (+{sec} sec)" if sec else ""
        lines.append(f"  {entry['label']:<22} {entry['count']:>4} songs{sec_str} ({entry['pct']:>5.1f}%)  {_bar(entry['pct'])}{marker}")
    lines.append("")
    lines.append("Theme Dominance:")
    for entry in sorted(metrics["theme_distribution"], key=lambda e: (-e["count"], e["theme"])):
        marker = " ← underrepresented" if entry["underrepresented"] else ""
        lines.append(f"  {entry['theme']:<6} {entry['count']:>4} songs ({entry['pct']:>5.1f}%){marker}")
    lines.append("")
    lines.append(f"Phase Inference: {metrics['theme_inferred']} from themes, {metrics['tempo_fallback']} tempo-only fallback ({metrics['tempo_fallback_no_tempo']} with no tempo)")
    signal = metrics["signal_coverage"]
    total = metrics["enriched_size"]
    lines.append(f"Signal Coverage: title {signal['title_hits']}/{total}, lyrics {signal['lyrics_hits']}/{total}, embeddings {signal['song_embedding']}/{total}")
    div = metrics["diversity"]
    lines.append(f"Theme Entropy: {div['theme_entropy']:.2f} bits (max {div['max_theme_entropy']:.2f})")
    lines.append(f"Phase Entropy: {div['phase_entropy']:.2f} bits (max {div['max_phase_entropy']:.2f})")
    lines.append("")
    lines.append(f"Report written to: {report_path}")
    return "\n".join(lines)
