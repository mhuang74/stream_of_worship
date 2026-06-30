"""Write songset constructor artifacts."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

from poc.songset_constructor.config import RunConfig
from poc.songset_constructor.models import SongCandidate, SongsetProposal


def _json_default(value):
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def write_artifacts(
    *,
    config: RunConfig,
    proposals: list[SongsetProposal],
    pool: list[SongCandidate],
    trace: list[dict],
) -> dict[str, str]:
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "proposals": output_dir / "proposals.json",
        "report": output_dir / "proposal_report.md",
        "pool": output_dir / "candidate_pool.csv",
        "trace": output_dir / "graph_trace.jsonl",
    }
    write_proposals(paths["proposals"], config, proposals)
    write_report(paths["report"], proposals)
    write_pool_csv(paths["pool"], pool)
    paths["trace"].write_text(
        "\n".join(json.dumps(item, ensure_ascii=False, default=_json_default) for item in trace) + "\n",
        encoding="utf-8",
    )
    return {name: str(path) for name, path in paths.items()}


def write_proposals(path: Path, config: RunConfig, proposals: list[SongsetProposal]) -> None:
    payload = {
        "run_id": config.thread_id,
        "config": config.to_dict(),
        "generated_at": datetime.now(UTC).isoformat(),
        "proposals": [proposal.model_dump(mode="json") for proposal in proposals],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")


def write_report(path: Path, proposals: list[SongsetProposal]) -> None:
    lines = ["# Songset Proposals", ""]
    if not proposals:
        lines.extend(["No valid proposals generated.", ""])
    for proposal in proposals:
        lines.extend(
            [
                f"## Rank {proposal.rank} - Score {proposal.score.total:.4f}",
                "",
                "| # | Title | Phase | BPM | Key | Themes | Transition |",
                "|---|---|---:|---:|---|---|---|",
            ]
        )
        for item in proposal.items:
            key = " ".join(part for part in [item.key, item.mode] if part)
            transition = f"shift {item.key_shift_semitones}, gap {item.gap_beats:g} beats"
            lines.append(
                f"| {item.position} | {item.title} | {item.phase} | {item.bpm or ''} | {key} | {', '.join(item.themes)} | {transition} |"
            )
        lines.extend(
            [
                "",
                f"Rationale: {proposal.rationale or 'Deterministic beam ranking.'}",
                "",
                f"Score: theme {proposal.score.f_theme:.3f}, tempo {proposal.score.f_tempo:.3f}, harmony {proposal.score.f_harmony:.3f}, diversity {proposal.score.f_diversity:.3f}.",
                "",
            ]
        )
        if proposal.hard_constraint_warnings:
            lines.extend([f"Warnings: {', '.join(proposal.hard_constraint_warnings)}", ""])
        if proposal.judge_reason:
            lines.extend([f"Judge note: {proposal.judge_reason}", ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def write_pool_csv(path: Path, pool: list[SongCandidate]) -> None:
    headers = [
        "song_id",
        "title",
        "title_pinyin",
        "composer",
        "album_name",
        "album_series",
        "recording_hash_prefix",
        "tempo_bpm",
        "musical_key",
        "musical_mode",
        "key_confidence",
        "loudness_db",
        "phase",
        "top_themes",
        "fan_out",
        "is_dead_end",
        "is_hymn",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for candidate in pool:
            top = sorted(candidate.themes.items(), key=lambda item: (-item[1], item[0]))[:2]
            writer.writerow(
                {
                    "song_id": candidate.song_id,
                    "title": candidate.title,
                    "title_pinyin": candidate.title_pinyin,
                    "composer": candidate.composer,
                    "album_name": candidate.album_name,
                    "album_series": candidate.album_series,
                    "recording_hash_prefix": candidate.recording_hash_prefix,
                    "tempo_bpm": candidate.tempo_bpm,
                    "musical_key": candidate.musical_key,
                    "musical_mode": candidate.musical_mode,
                    "key_confidence": candidate.key_confidence,
                    "loudness_db": candidate.loudness_db,
                    "phase": candidate.phase,
                    "top_themes": ",".join(theme for theme, score in top if score > 0),
                    "fan_out": candidate.fan_out,
                    "is_dead_end": candidate.is_dead_end,
                    "is_hymn": candidate.is_hymn,
                }
            )
