"""Artifact writers for songset constructor runs."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .models import ConstructorState, SongCandidate, SongsetProposal


def write_artifacts(state: ConstructorState, output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "proposals": output_dir / "proposals.json",
        "report": output_dir / "proposal_report.md",
        "pool": output_dir / "candidate_pool.csv",
        "trace": output_dir / "graph_trace.jsonl",
    }
    paths["proposals"].write_text(
        json.dumps(
            [_proposal_contract(p) for p in state.final_proposals],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    paths["report"].write_text(_render_report(state.final_proposals), encoding="utf-8")
    _write_pool(paths["pool"], state.pool)
    with paths["trace"].open("w", encoding="utf-8") as handle:
        for event in state.trace:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    return paths


def _proposal_contract(proposal: SongsetProposal) -> dict[str, Any]:
    return {
        "items": [
            {
                "song_id": item.song_id,
                "recording_hash_prefix": item.recording_hash_prefix,
                "position": item.position,
                "key_shift_semitones": item.key_shift_semitones,
                "tempo_ratio": item.tempo_ratio,
                "gap_beats": item.gap_beats,
                "crossfade_enabled": item.crossfade_enabled,
                "crossfade_duration_seconds": item.crossfade_duration_seconds,
            }
            for item in proposal.items
        ],
        "score_breakdown": proposal.score_breakdown.model_dump(),
        "llm_rationale": proposal.llm_rationale,
        "warnings": proposal.validation_warnings,
    }


def _render_report(proposals: list[SongsetProposal]) -> str:
    lines = ["# Agentic Songset Proposals", ""]
    if not proposals:
        return "# Agentic Songset Proposals\n\nNo valid proposals generated.\n"
    for index, proposal in enumerate(proposals, start=1):
        lines.extend(
            [
                f"## Proposal {index} - score {proposal.score_breakdown.total:.3f}",
                "",
                proposal.llm_rationale or "Deterministic candidate selected by validator.",
                "",
            ]
        )
        for item in proposal.items:
            title = item.title or item.song_id
            transition = ""
            if item.position > 1:
                transition = (
                    f" shift={item.key_shift_semitones:+d}, tempo={item.tempo_ratio:.3f}, "
                    f"gap={item.gap_beats:g}, crossfade={item.crossfade_enabled}"
                )
            lines.append(
                f"{item.position}. {title} ({item.phase or 'Unknown'}) "
                f"`{item.recording_hash_prefix}`{transition}"
            )
        if proposal.validation_warnings:
            lines.extend(["", "Warnings: " + ", ".join(proposal.validation_warnings)])
        lines.append("")
    return "\n".join(lines)


def _write_pool(path: Path, pool: list[SongCandidate]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "song_id",
                "title",
                "recording_hash_prefix",
                "bpm",
                "musical_key",
                "musical_mode",
                "key_confidence",
                "album_name",
                "album_series",
                "composer",
                "inferred_themes",
                "phase",
                "source_warnings",
            ],
        )
        writer.writeheader()
        for song in pool:
            row = song.model_dump()
            row["inferred_themes"] = "|".join(song.inferred_themes)
            row["source_warnings"] = "|".join(song.source_warnings)
            writer.writerow({key: row.get(key) for key in writer.fieldnames})
