#!/usr/bin/env python3
"""Write the final proposal_report.md artifact from structured proposal data.

Usage:
    echo '{"proposals": [...], "pool": [...], "config": {...}, "transitions": [...]}' | \
        python write_report.py --output-dir output/songset_constructor/<timestamp>/

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

PROJECT_ROOT = Path(__file__).resolve().parents[4]
ADMIN_CLI_SRC = PROJECT_ROOT / "ops" / "admin-cli" / "src"
if str(ADMIN_CLI_SRC) not in sys.path:
    sys.path.insert(0, str(ADMIN_CLI_SRC))

PHASE_NAMES = {1: "call", 2: "thanksgiving", 3: "worship", 4: "response", 5: "commitment"}


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
    from stream_of_worship.admin.songset_constructor.models import SongCandidate, SongsetProposal
    from stream_of_worship.admin.songset_constructor.artifacts.writer import (
        brief_summary_block,
        _deterministic_arc_narrative,
        _diversity_summary,
    )

    proposals = [SongsetProposal.model_validate(p) for p in data.get("proposals", [])]
    pool = [SongCandidate.model_validate(c) for c in data.get("pool", [])]
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
    report_text = _build_report(proposals, pool, config, summary_text)
    report_path.write_text(report_text, encoding="utf-8")

    print(str(report_path))


def _build_report(
    proposals: list,
    pool: list,
    config: "RunConfig",
    summary_text: str,
) -> str:
    """Build the full proposal_report.md content."""
    from stream_of_worship.admin.songset_constructor.artifacts.writer import (
        brief_summary_block,
        _deterministic_arc_narrative,
        _diversity_summary,
    )
    from stream_of_worship.admin.songset_constructor.rules.themes import THEMES

    lines: list[str] = ["# Songset Proposals", ""]

    # Run Summary
    lines.extend(_run_summary(config, pool))

    # Pool Overview
    lines.extend(_pool_overview(pool))

    # Per-proposal details
    if not proposals:
        lines.extend(["No valid proposals generated.", ""])
    for proposal in proposals:
        lines.extend(_proposal_section(proposal, config, pool))

    # Diversity Summary
    lines.extend(_diversity_summary(proposals, pool, config=config))

    # Agent Summary
    if summary_text:
        lines.extend(["## Agent Summary", "", summary_text, ""])

    return "\n".join(lines)


def _run_summary(config: "RunConfig", pool: list) -> list[str]:
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

    return [
        "## Run Summary",
        "",
        f"- Run ID: {config.thread_id}",
        f"- Generated: {generated_at}",
        f"- Requested song count: {config.count}",
        f"- Top-k: {config.proposals}",
        f"- Pool size: {len(pool)}",
        f"- Flags: {flags_str}",
        "",
    ]


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

    return [
        "## Pool Overview",
        "",
        f"- Total candidates: {total}",
        f"- Phase distribution: {phase_dist}",
        f"- Tempo coverage: {tempo_known} known BPM, {tempo_missing} missing",
        f"- Theme entropy: {theme_entropy:.2f} bits (max {max_theme_entropy:.3f})",
        "",
    ]


def _proposal_section(proposal, config: "RunConfig", pool: list) -> list[str]:
    """Generate a per-proposal section."""
    from stream_of_worship.admin.songset_constructor.artifacts.writer import brief_summary_block

    lines = [f"## Rank {proposal.rank} - Score {proposal.score.total:.4f}", ""]
    lines.extend(brief_summary_block(proposal, config=config, pool=pool))
    lines.extend(["", "### Details", "", "| # | Title | Album | Phase | BPM | Key | Themes | Transition |", "|---|---|---:|---:|---|---|---|"])

    for item in proposal.items:
        key = " ".join(part for part in [item.key, item.mode] if part)
        transition = f"shift {item.key_shift_semitones}, gap {item.gap_beats:g} beats"
        phase_display = str(item.phase)
        if item.secondary_phases:
            phase_display += f" (+{','.join(str(p) for p in sorted(item.secondary_phases))})"
        themes = ", ".join(item.themes) if item.themes else "none"
        bpm = f"{item.bpm:g}" if item.bpm is not None else ""
        lines.append(f"| {item.position} | {item.title} | {item.album_name or ''} | {phase_display} | {bpm} | {key} | {themes} | {transition} |")

    lines.extend([
        "",
        f"Score: theme {proposal.score.f_theme:.3f}, tempo {proposal.score.f_tempo:.3f}, "
        f"harmony {proposal.score.f_harmony:.3f}, diversity {proposal.score.f_diversity:.3f}.",
        "",
    ])

    if proposal.hard_constraint_warnings:
        lines.extend([f"Warnings: {', '.join(proposal.hard_constraint_warnings)}", ""])

    if proposal.judge_reason:
        lines.extend([f"Judge note: {proposal.judge_reason}", ""])

    return lines


if __name__ == "__main__":
    main()
