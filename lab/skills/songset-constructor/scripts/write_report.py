#!/usr/bin/env python3
"""Write the final proposal_report.md artifact from structured proposal data.

Usage:
    echo '{"proposals": [...], "pool": [...], "config": {...}, "transitions": [...]}' | \
        uv run --project ops/admin-cli --extra admin --extra constructor python write_report.py --output-dir output/songset_constructor/<timestamp>/

Input (stdin JSON):
    {
        "proposals": [...],     # list of SongsetProposal objects
        "pool": [...],          # enriched SongCandidate objects
        "config": {...},        # RunConfig as dict
        "transitions": [...],   # TransitionCandidate objects
        "summary": "..."        # optional agent-authored summary text
    }

Output: Writes proposal_report.md to the output directory. Prints file path to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from stream_of_worship.admin.constants import SONGSET_MAX_DURATION_SECONDS

if TYPE_CHECKING:
    from stream_of_worship.admin.songset_constructor.config import RunConfig
    from stream_of_worship.admin.songset_constructor.models import TransitionCandidate

PROJECT_ROOT = Path(__file__).resolve().parents[4]
ADMIN_CLI_SRC = PROJECT_ROOT / "ops" / "admin-cli" / "src"
if str(ADMIN_CLI_SRC) not in sys.path:
    sys.path.insert(0, str(ADMIN_CLI_SRC))

PHASE_NAMES = {1: "call", 2: "thanksgiving", 3: "worship", 4: "response", 5: "commitment"}
PC_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]


def _format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}m {secs:02d}s"


def main() -> None:
    parser = argparse.ArgumentParser(description="Write proposal_report.md from structured data")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: output/songset_constructor/<timestamp>/)",
    )
    args = parser.parse_args()

    data = json.load(sys.stdin)

    from stream_of_worship.admin.songset_constructor.config import RunConfig
    from stream_of_worship.admin.songset_constructor.models import (
        SongCandidate,
        SongsetProposal,
        TransitionCandidate,
    )

    proposals = [SongsetProposal.model_validate(p) for p in data.get("proposals", [])]
    pool = [SongCandidate.model_validate(c) for c in data.get("pool", [])]
    matrix: dict[tuple[str, str], object] = {}
    for t_data in data.get("transitions", []):
        t = TransitionCandidate.model_validate(t_data)
        matrix[(t.from_hash_prefix, t.to_hash_prefix)] = t
    config_dict = data.get("config", {})
    summary_text = data.get("summary", "")

    # Build RunConfig
    config_kwargs = {}
    valid_fields = set(RunConfig.__dataclass_fields__.keys())
    for key, value in config_dict.items():
        if key in valid_fields and value is not None:
            config_kwargs[key] = value
    config = RunConfig(**config_kwargs)

    # Determine output directory
    if args.output_dir:
        output_dir = args.output_dir
    else:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        output_dir = Path("output") / "songset_constructor" / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    report_path = output_dir / "proposal_report.md"
    report_text = _build_report(proposals, pool, config, summary_text, matrix)
    report_path.write_text(report_text, encoding="utf-8")

    print(str(report_path))


def _build_report(
    proposals: list,
    pool: list,
    config: RunConfig,
    summary_text: str,
    matrix: dict[tuple[str, str], TransitionCandidate] | None = None,
) -> str:
    """Build the full proposal_report.md content."""
    from stream_of_worship.admin.songset_constructor.artifacts.writer import (
        _diversity_summary,
    )

    lines: list[str] = ["# Songset Proposals", ""]

    # Run Summary
    lines.extend(_run_summary(config, pool))

    # Pool Overview
    lines.extend(_pool_overview(pool))

    # Per-proposal details
    if not proposals:
        lines.extend(["No valid proposals generated.", ""])
    for proposal in proposals:
        lines.extend(_proposal_section(proposal, config, pool, matrix))

    # Diversity Summary
    lines.extend(_diversity_summary(proposals, pool, config=config))

    # Agent Summary
    if summary_text:
        lines.extend(["## Agent Summary", "", summary_text, ""])

    return "\n".join(lines)


def _run_summary(config: RunConfig, pool: list) -> list[str]:
    """Generate the run summary section."""
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    flags = []
    if config.intimate:
        flags.append("intimate=true")
    if config.season:
        flags.append(f"season={config.season}")
    if config.relax_h1:
        flags.append("relax_h1=true")
    if config.relax_h4:
        flags.append("relax_h4=true")
    if config.relax_h5:
        flags.append("relax_h5=true")
    flags_str = ", ".join(flags) if flags else "none"

    lines = [
        "## Run Summary",
        "",
        f"- Run ID: {config.thread_id}",
        f"- Generated: {generated_at}",
        f"- Requested song count: {config.count}",
        f"- Top-k: {config.proposals}",
        f"- Pool size: {len(pool)}",
        f"- Flags: {flags_str}",
    ]

    leader_label = next((c.leader_range_label for c in pool if c.leader_range_label), None)
    leader_pcs = next((c.leader_range_pcs for c in pool if c.leader_range_pcs), None)
    if leader_label or leader_pcs:
        lines.append(f"- Leader range: {leader_label or 'custom'}")
        if leader_pcs:
            pc_names = ", ".join(PC_NAMES[pc] for pc in sorted(leader_pcs))
            lines.append(f"- Comfortable tonic PCs: {pc_names}")
    lines.append("")
    return lines


def _pool_overview(pool: list) -> list[str]:
    """Generate the pool overview section."""
    from collections import Counter

    from stream_of_worship.admin.songset_constructor.rules.themes import THEMES

    total = len(pool)
    phase_counts: Counter[int] = Counter(c.phase for c in pool)
    tempo_known = sum(1 for c in pool if c.tempo_bpm is not None)
    tempo_missing = total - tempo_known

    # Theme coverage
    dominant_themes: Counter[str] = Counter()
    for c in pool:
        if c.themes:
            positive = {t: s for t, s in c.themes.items() if s > 0}
            if positive:
                dominant = max(positive.items(), key=lambda item: (item[1], item[0]))[0]
                dominant_themes[dominant] += 1

    import math
    theme_entropy = 0.0
    counts = list(dominant_themes.values())
    if counts:
        total_counts = sum(counts)
        theme_entropy = -sum((c / total_counts) * math.log2(c / total_counts) for c in counts if c > 0)
    max_theme_entropy = math.log2(len(THEMES)) if THEMES else 0.0

    phase_dist = ", ".join(f"P{p}={phase_counts.get(p, 0)}" for p in range(1, 6))

    durations = [c.duration_seconds for c in pool if c.duration_seconds is not None]
    duration_known = len(durations)
    duration_missing = total - duration_known

    lines = [
        "## Pool Overview",
        "",
        f"- Total candidates: {total}",
        f"- Phase distribution: {phase_dist}",
        f"- Tempo coverage: {tempo_known} known BPM, {tempo_missing} missing",
        f"- Duration coverage: {duration_known} known, {duration_missing} missing",
    ]
    if durations:
        avg_dur = sum(durations) / len(durations)
        max_dur = max(durations)
        lines.append(f"- Duration stats: avg {_format_duration(avg_dur)}, max {_format_duration(max_dur)}")
    lines.append(f"- Theme entropy: {theme_entropy:.2f} bits (max {max_theme_entropy:.3f})")

    # Component coverage + theme source distribution + H8 transposable population
    with_components = [c for c in pool if c.has_components]
    component_count = len(with_components)
    component_pct = (100.0 * component_count / total) if total else 0.0
    lines.append(
        f"- Component coverage: {component_count}/{total} ({component_pct:.1f}%) have song_components data"
    )

    source_counts: Counter[str] = Counter(c.theme_source for c in pool)
    source_summary = ", ".join(
        f"{source}={source_counts[source]}" for source in ("component", "fusion") if source_counts[source]
    )
    if source_summary:
        lines.append(f"- Theme source: {source_summary}")

    song_level_transposable = sum(
        1 for c in pool if c.key_confidence is not None and c.key_confidence >= 0.6
    )
    boundary_transposable = sum(
        1
        for c in pool
        if (c.entry_key_confidence if c.entry_key is not None else c.key_confidence) is not None
        and (c.entry_key_confidence if c.entry_key is not None else c.key_confidence) >= 0.6
    )
    lines.append(
        f"- Transposable population (key confidence >= 0.6): song-level {song_level_transposable}, "
        f"boundary-gated {boundary_transposable} (H8 boundary-first regression)"
    )
    lines.append("")
    return lines


def _proposal_section(
    proposal,
    config: RunConfig,
    pool: list,
    matrix: dict[tuple[str, str], TransitionCandidate] | None = None,
) -> list[str]:
    """Generate a per-proposal section."""
    from stream_of_worship.admin.songset_constructor.artifacts.writer import brief_summary_block

    lines = [f"## Rank {proposal.rank} - Score {proposal.score.total:.4f}", ""]
    lines.extend(brief_summary_block(proposal, config=config, pool=pool))
    lines.extend([
        "",
        "### Details",
        "",
        "| # | Title | Album | Phase | BPM | Key | Entry/Exit BPM | Entry/Exit Key | Dur | Themes (source) | Transition |",
        "|---|---|---:|---:|---|---|---|---|---|---|---|",
    ])

    for item in proposal.items:
        key = " ".join(part for part in [item.key, item.mode] if part)
        transition = f"shift {item.key_shift_semitones}, gap {item.gap_beats:g} beats"
        phase_display = str(item.phase)
        if item.secondary_phases:
            phase_display += f" (+{','.join(str(p) for p in sorted(item.secondary_phases))})"
        themes = ", ".join(item.themes) if item.themes else "none"
        source = f" ({item.theme_source})" if item.theme_source else ""
        themes_display = f"{themes}{source}"
        bpm = f"{item.bpm:g}" if item.bpm is not None else ""
        dur = _format_duration(item.duration_seconds) if item.duration_seconds is not None else "?"
        boundary_bpm = (
            f"{item.entry_bpm:g}/{item.exit_bpm:g}"
            if item.entry_bpm is not None and item.exit_bpm is not None
            else (
                f"{item.entry_bpm:g}/—"
                if item.entry_bpm is not None
                else f"—/{item.exit_bpm:g}" if item.exit_bpm is not None else "—"
            )
        )
        boundary_key = (
            f"{item.entry_key}/{item.exit_key}"
            if item.entry_key is not None and item.exit_key is not None
            else (
                f"{item.entry_key}/—"
                if item.entry_key is not None
                else f"—/{item.exit_key}" if item.exit_key is not None else "—"
            )
        )
        lines.append(
            f"| {item.position} | {item.title} | {item.album_name or ''} | {phase_display} | {bpm} | {key} | {boundary_bpm} | {boundary_key} | {dur} | {themes_display} | {transition} |"
        )

    # Total duration line
    total_duration = sum(item.duration_seconds or 0.0 for item in proposal.items)
    has_unknown = any(item.duration_seconds is None for item in proposal.items)
    dur_status = "✓"
    if total_duration > SONGSET_MAX_DURATION_SECONDS:
        dur_status = "✗ H9 VIOLATED"
    elif has_unknown:
        dur_status = "⚠ unknown durations"
    lines.extend([
        "",
        f"**Total duration:** {_format_duration(total_duration)} ({total_duration:.0f}s / {SONGSET_MAX_DURATION_SECONDS}s limit) {dur_status}",
    ])

    # Singing range subsection
    leader_pcs = next((c.leader_range_pcs for c in pool if c.leader_range_pcs), None)
    leader_label = next((c.leader_range_label for c in pool if c.leader_range_label), None)
    if leader_pcs:
        pc_names = ", ".join(PC_NAMES[pc] for pc in sorted(leader_pcs))
        lines.extend([
            "",
            f"**Singing range:** {leader_label or 'custom'} (comfortable tonics: {pc_names})",
            "",
            "| # | Song | Key | Mode | In range | Applied shift | Range distance |",
            "|---|------|-----|------|----------|---------------|----------------|",
        ])
        for item in proposal.items:
            in_range = "✓" if item.in_leader_range else "✗"
            if item.in_leader_range and item.recommended_key_shift_for_range != 0 or item.in_leader_range and item.key_shift_semitones != 0:
                in_range = "✓ (shifted)"
            applied_shift = item.key_shift_semitones if item.key_shift_semitones != 0 else (
                item.recommended_key_shift_for_range if item.recommended_key_shift_for_range != 0 else 0
            )
            lines.append(
                f"| {item.position} | {item.title} | {item.key or '?'} | {item.mode or '?'} | {in_range} | {applied_shift} | {item.leader_range_distance} |"
            )

    # Component metadata: adjacency provenance + warnings, energy/posture arcs, coverage warnings
    if matrix is not None:
        lines.extend(["", "### Adjacency (component metadata)", "", "| From → To | Provenance | CFD | BPM Δ | Warnings |", "|---|---|---:|---:|---|"])
        for left, right in zip(proposal.items, proposal.items[1:]):
            transition = matrix.get((left.recording_hash_prefix, right.recording_hash_prefix))
            if transition is None:
                lines.append(f"| {left.title} → {right.title} | (no transition) | — | — | — |")
                continue
            source = "component" if transition.boundary_source == "component" else "song-level"
            warnings = ", ".join(transition.warnings) if transition.warnings else "—"
            lines.append(
                f"| {left.title} → {right.title} | {source} | {transition.cfd} | {transition.bpm_delta:g} | {warnings} |"
            )

    energy_trajectory: list[str] = []
    posture_sequence: list[str] = []
    for item in proposal.items:
        entry_pct = f"{item.entry_energy_pct:.2f}" if item.entry_energy_pct is not None else "—"
        exit_pct = f"{item.exit_energy_pct:.2f}" if item.exit_energy_pct is not None else "—"
        energy_trajectory.append(f"{item.position}:{entry_pct}→{exit_pct}")
        posture = item.component_posture or "—"
        fit = ""
        if item.component_posture is not None:
            from stream_of_worship.admin.songset_constructor.components import POSTURE_PHASE_FIT

            fit = f" (fit {POSTURE_PHASE_FIT[item.component_posture].get(item.phase, 0.5):.1f})"
        posture_sequence.append(f"{item.position}: {posture} @ P{item.phase}{fit}")
    lines.extend(["", f"**Energy arc (entry→exit pct):** {' | '.join(energy_trajectory)}"])
    lines.extend([f"**Posture sequence:** {' | '.join(posture_sequence)}", ""])

    fallback_songs = [item.title for item in proposal.items if not item.has_components]
    if fallback_songs:
        lines.extend([f"Fallback (song-level): {', '.join(fallback_songs)} — theme via fusion, no energy/posture data", ""])
    # Score breakdown with range_penalty + active weight mode (four/five/six-way)
    active = (proposal.score.f_energy is not None) + (proposal.score.f_posture is not None)
    mode = {
        0: "four-way (0.40/0.30/0.20/0.10)",
        1: "five-way (base × 0.95)",
        2: "six-way (base × 0.90 + 0.05 + 0.05)",
    }[active]
    energy_part = (
        f"f_energy {proposal.score.f_energy:.3f}" if proposal.score.f_energy is not None else "f_energy (n/a)"
    )
    posture_part = (
        f"f_posture {proposal.score.f_posture:.3f}" if proposal.score.f_posture is not None else "f_posture (n/a)"
    )
    score_parts = (
        f"f_theme {proposal.score.f_theme:.3f}, "
        f"f_tempo {proposal.score.f_tempo:.3f}, "
        f"f_harmony {proposal.score.f_harmony:.3f}, "
        f"f_diversity {proposal.score.f_diversity:.3f}, "
        f"{energy_part}, "
        f"{posture_part}"
    )
    if proposal.score.range_penalty > 0:
        score_parts += f", range_penalty -{proposal.score.range_penalty:.3f}"
    lines.extend(["", f"Score ({mode}): {score_parts}.", ""])

    if proposal.hard_constraint_warnings:
        lines.extend([f"Warnings: {', '.join(proposal.hard_constraint_warnings)}", ""])

    if proposal.judge_reason:
        lines.extend([f"Judge note: {proposal.judge_reason}", ""])

    return lines


if __name__ == "__main__":
    main()
