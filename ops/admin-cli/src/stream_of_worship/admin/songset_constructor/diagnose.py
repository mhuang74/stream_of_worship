"""Diagnostic lines for the songset constructor result."""

from __future__ import annotations

from stream_of_worship.admin.songset_constructor.artifacts.writer import (
    _bottleneck_lines,
    _diversity_metrics,
    _song_frequency_table,
    _song_overlap_matrix,
    _theme_coverage_lines,
)
from stream_of_worship.admin.songset_constructor.config import RunConfig
from stream_of_worship.admin.songset_constructor.rules.diagnostics import (
    diagnostic_lines as _diagnostic_lines,
)
from stream_of_worship.admin.songset_constructor.rules.themes import THEMES


def assemble_report_sections(config: RunConfig, result: dict) -> list[str]:
    trace = result.get("trace", [])
    lines: list[str] = []
    load_data = _latest_trace_data(trace, "load_catalog")
    enrich_data = _latest_trace_data(trace, "enrich_pool")
    beam_data = _latest_trace_data(trace, "beam_seed_candidates")

    lines.append("## Pool Enrichment Metrics")
    if enrich_data:
        lines.append(f"\n> Pool: {load_data.get('pool_size', '?')} loaded → {enrich_data.get('pool_size', '?')} enriched ({enrich_data.get('dropped', 0)} dropped)")
        drop_reasons = enrich_data.get("drop_reasons", {})
        if drop_reasons:
            for reason, count in sorted(drop_reasons.items(), key=lambda x: (-x[1], x[0])):
                lines.append(f">   - {reason}: {count}")
    lines.append("")

    lines.append("## Pool Overview")
    pool = result.get("pool", [])
    lines.append(f"\nTotal candidates: {len(pool)}")
    if pool:
        phases = {}
        for c in pool:
            phases.setdefault(c.phase, 0)
            phases[c.phase] += 1
        lines.append(f"Phase distribution: {', '.join(f'P{p}={c}' for p, c in sorted(phases.items()))}")
    lines.append("")

    lines.append("## Phase Distribution & Role-Eligibility")
    role = beam_data.get("role_eligibility", {})
    if role:
        for key, value in sorted(role.items()):
            lines.append(f"\n- {key}: {value}")
    lines.append("")

    diag = _diagnostic_lines(config, result)
    if diag:
        lines.append("## Rule-Drop Diagnostics")
        for d in diag:
            lines.append(f"\n- {d}")
        lines.append("")

    proposals = result.get("final_proposals", [])

    if len(proposals) > 1:
        pool = result.get("pool", [])
        metrics = _diversity_metrics(proposals, pool)
        lines.append("## Diversity Matrix")
        lines.append(f"\nAcross {len(proposals)} proposals ({metrics['total_slots']} song slots total):\n")
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        unique_pct = round(len(metrics["unique_songs"]) / metrics["total_slots"] * 100) if metrics["total_slots"] else 0
        lines.append(f"| Unique songs | {len(metrics['unique_songs'])} / {metrics['total_slots']} ({unique_pct}%) |")
        lines.append(f"| Unique themes | {len(metrics['unique_themes'])} / {len(THEMES)} |")
        lines.append(f"| Unique composers | {len(metrics['unique_composers'])} |")
        lines.append(f"| Unique phases | {len(metrics['unique_phases'])} / 5 |")
        lines.append(f"| Middle-song reuse | {metrics['middle_reuse_count']} (across {metrics['total_middle_slots']} middle slots) |")
        lines.append("")
        lines.append("### Song Overlap Matrix")
        lines.append("")
        lines.extend(_song_overlap_matrix(proposals))
        lines.append("")
        lines.append("### Song Frequency")
        lines.append("")
        lines.extend(_song_frequency_table(proposals))
        lines.append("")
        lines.append("### Theme Coverage")
        lines.append("")
        lines.extend(_theme_coverage_lines(proposals))
        bottlenecks = _bottleneck_lines(metrics, proposals, pool)
        if bottlenecks:
            lines.extend(["", "### Bottlenecks", ""])
            lines.extend(f"- {line}" for line in bottlenecks)
        lines.append("")

    if proposals:
        lines.append(f"## Proposals ({len(proposals)} total)")
        for p in proposals:
            items_str = " → ".join(f"{i.title}(P{i.phase})" for i in p.items)
            lines.append(f"\n### Rank {p.rank} — Score: {p.score.total:.4f}")
            lines.append(f"\n- {items_str}")
            lines.append(f"\n- Score: theme={p.score.f_theme:.3f} tempo={p.score.f_tempo:.3f} harmony={p.score.f_harmony:.3f} diversity={p.score.f_diversity:.3f}")
            if p.hard_constraint_warnings:
                lines.append(f"\n- Warnings: {', '.join(p.hard_constraint_warnings)}")
    else:
        lines.append("## No Results")
        lines.append("\nNo valid proposals could be generated with the current constraints.")

    return lines


def _latest_trace_data(trace: list[dict], node: str) -> dict:
    for entry in reversed(trace):
        if entry.get("node") == node and isinstance(entry.get("data"), dict):
            return entry["data"]
    return {}
