"""Write songset constructor artifacts."""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
        "review": output_dir / "songset_review.md",
    }
    write_proposals(paths["proposals"], config, proposals)
    write_report(paths["report"], proposals)
    write_pool_csv(paths["pool"], pool)
    paths["trace"].write_text(
        "\n".join(json.dumps(item, ensure_ascii=False, default=_json_default) for item in trace) + "\n",
        encoding="utf-8",
    )
    paths["review"].write_text(
        build_review_report(config=config, proposals=proposals, pool=pool, trace=trace),
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


def build_review_report(
    *,
    config: RunConfig,
    proposals: list[SongsetProposal],
    pool: list[SongCandidate],
    trace: list[dict],
    chat: Any | None = None,
    fallback_note: str | None = None,
) -> str:
    generated_at = datetime.now(UTC).isoformat()
    if config.no_llm:
        return _fallback_review_report(config, proposals, pool, trace, generated_at, fallback_note)

    try:
        chat_model = chat
        if chat_model is None:
            from poc.songset_constructor.graph.llm import build_chat_model

            chat_model = build_chat_model(config)
        if chat_model is None:
            return _fallback_review_report(
                config, proposals, pool, trace, generated_at, fallback_note
            )
        payload = _review_payload(config, proposals, pool, trace, generated_at)
        response = chat_model.invoke(_review_prompt(payload))
        content = getattr(response, "content", response)
        if isinstance(content, list):
            content = " ".join(str(part) for part in content)
        report = _strip_markdown_fence(str(content).strip())
        if not report:
            return _fallback_review_report(
                config, proposals, pool, trace, generated_at, fallback_note
            )
        if not report.startswith("# Songset Constructor Review"):
            prefix = _fallback_title_and_findings(config, proposals, pool, trace)
            report = prefix + "\n\n" + report.lstrip()
        return report + ("\n" if not report.endswith("\n") else "")
    except Exception:
        return _fallback_review_report(
            config,
            proposals,
            pool,
            trace,
            generated_at,
            "LLM report generation failed; fallback report used.",
        )


def _review_payload(
    config: RunConfig,
    proposals: list[SongsetProposal],
    pool: list[SongCandidate],
    trace: list[dict],
    generated_at: str,
) -> dict[str, Any]:
    config_data = config.to_dict()
    config_data.pop("env_file", None)
    return {
        "config": config_data,
        "run_summary": {
            "run_id": config.thread_id,
            "generated_at": generated_at,
            "requested_song_count": config.songs,
            "top_k": config.top_k,
            "pool_size": len(pool),
            "flags": _config_flags(config),
        },
        "candidate_pool": _candidate_pool_summary(pool, trace),
        "trace_summaries": _trace_stage_summaries(trace),
        "validation_events": _validation_events(trace),
        "proposals": [proposal.model_dump(mode="json") for proposal in proposals],
    }


def _review_prompt(payload: dict[str, Any]) -> str:
    return (
        "Write Markdown only for a human reviewer. Use exactly this section structure:\n"
        "# Songset Constructor Review\n"
        "## Key Findings\n"
        "## Run Summary\n"
        "## What Was Done\n"
        "## How Filters Were Applied\n"
        "## Proposal N for each ranked proposal\n\n"
        "Guardrails:\n"
        "- Use only facts in the payload.\n"
        "- Do not invent songs, scores, filters, validation errors, or conclusions.\n"
        "- Keep proposal tables complete and faithful.\n"
        "- Put 3-6 key-finding bullets immediately after the title.\n"
        "- Mention relaxation warnings plainly.\n"
        "- State whether proposals came from deterministic beam ranking or LLM-origin proposals.\n"
        "- Do not include raw JSON.\n\n"
        "Factual payload:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default)}"
    )


def _fallback_review_report(
    config: RunConfig,
    proposals: list[SongsetProposal],
    pool: list[SongCandidate],
    trace: list[dict],
    generated_at: str,
    fallback_note: str | None = None,
) -> str:
    lines = _fallback_title_and_findings(config, proposals, pool, trace).splitlines()
    if fallback_note:
        lines.extend(["", f"> {fallback_note}"])
    lines.extend(
        [
            "",
            "## Run Summary",
            "",
            f"- Run ID: {config.thread_id}",
            f"- Generated: {generated_at}",
            f"- Requested song count: {config.songs}",
            f"- Top-k: {config.top_k}",
            f"- Pool size: {len(pool)}",
            f"- Relevant flags: {_format_flags(_config_flags(config))}",
            "",
            "## What Was Done",
            "",
            "- Loaded the catalog pool from the configured read-only source.",
            "- Enriched candidates with phase and theme metadata, dropping songs without enough "
            "tempo/key data.",
            "- Built compatible transition candidates and seeded ranked sequences.",
            "- Finalized ranked proposals from beam candidates and validation state.",
            "- Wrote proposal, report, pool, trace, and review artifacts.",
            "",
            "## How Filters Were Applied",
            "",
            _filters_summary(config, proposals, pool, trace),
            "",
        ]
    )
    for proposal in proposals:
        lines.extend(_proposal_section(proposal))
    if not proposals:
        lines.extend(["## Proposal 0", "", "No valid proposals were generated.", ""])
    return "\n".join(lines).rstrip() + "\n"


def _fallback_title_and_findings(
    config: RunConfig,
    proposals: list[SongsetProposal],
    pool: list[SongCandidate],
    trace: list[dict],
) -> str:
    origin = _proposal_origin(proposals)
    warnings = _proposal_warnings(proposals)
    tempos = [candidate.tempo_bpm for candidate in pool if candidate.tempo_bpm is not None]
    phase_counts = Counter(candidate.phase for candidate in pool)
    findings = [
        (
            f"{len(proposals)} proposal{'s' if len(proposals) != 1 else ''} were generated."
            if proposals
            else "No valid proposals were generated."
        ),
        f"Final proposals came from {origin}.",
        "Relaxation or constraint warnings: "
        f"{', '.join(warnings) if warnings else 'none reported'}.",
        f"Phase flow available in the pool: {_format_phase_counts(phase_counts)}.",
        f"Tempo coverage: {len(tempos)} known BPM values and {len(pool) - len(tempos)} missing.",
    ]
    dropped = _latest_trace_data(trace, "enrich_pool").get("dropped")
    if dropped:
        findings.append(f"Enrichment dropped {dropped} songs before transition ranking.")
    findings = findings[:6]
    return "# Songset Constructor Review\n\n## Key Findings\n\n" + "\n".join(
        f"- {finding}" for finding in findings
    )


def _candidate_pool_summary(pool: list[SongCandidate], trace: list[dict]) -> dict[str, Any]:
    bpm_values = [
        round(candidate.tempo_bpm) for candidate in pool if candidate.tempo_bpm is not None
    ]
    return {
        "total": len(pool),
        "phase_counts": dict(sorted(Counter(candidate.phase for candidate in pool).items())),
        "known_bpm_count": len(bpm_values),
        "missing_bpm_count": len(pool) - len(bpm_values),
        "common_bpm_values": Counter(bpm_values).most_common(8),
        "dropped_counts": _latest_trace_data(trace, "enrich_pool"),
    }


def _trace_stage_summaries(trace: list[dict]) -> list[dict[str, Any]]:
    summaries = []
    for entry in trace:
        if not isinstance(entry, dict):
            continue
        data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
        summaries.append(
            {
                "node": entry.get("node"),
                "event": entry.get("event"),
                "iteration": entry.get("iteration"),
                "data": {key: value for key, value in data.items() if key != "prompt"},
            }
        )
    return summaries


def _validation_events(trace: list[dict]) -> list[dict[str, Any]]:
    return [
        entry.get("data", {})
        for entry in trace
        if entry.get("node") == "validate_score" and entry.get("event") == "validation"
    ]


def _latest_trace_data(trace: list[dict], node: str) -> dict[str, Any]:
    for entry in reversed(trace):
        if entry.get("node") == node and isinstance(entry.get("data"), dict):
            return entry["data"]
    return {}


def _config_flags(config: RunConfig) -> dict[str, Any]:
    return {
        "no_llm": config.no_llm,
        "llm_judge": config.llm_judge,
        "interactive_review": config.interactive_review,
        "auto_relax": config.auto_relax,
        "relax_h1": config.relax_h1,
        "relax_h2_bpm": config.relax_h2_bpm,
        "relax_h3_bpm": config.relax_h3_bpm,
        "relax_h4": config.relax_h4,
        "relax_h4_bpm": config.relax_h4_bpm,
        "relax_h5": config.relax_h5,
        "relax_h5_cfd": config.relax_h5_cfd,
        "intimate": config.intimate,
        "hymnal_mode": config.hymnal_mode,
        "season": config.season,
    }


def _format_flags(flags: dict[str, Any]) -> str:
    enabled = [
        f"{key}={value}"
        for key, value in flags.items()
        if value not in (False, None, [], {})
    ]
    return ", ".join(enabled) if enabled else "none"


def _filters_summary(
    config: RunConfig,
    proposals: list[SongsetProposal],
    pool: list[SongCandidate],
    trace: list[dict],
) -> str:
    enrich = _latest_trace_data(trace, "enrich_pool")
    validation = _validation_events(trace)
    warnings = _proposal_warnings(proposals)
    parts = [
        f"Enrichment output contains {len(pool)} candidates; dropped={enrich.get('dropped', 0)}.",
        f"Validation events recorded: {len(validation)}.",
        f"Final relaxed warning flags: {_format_flags(_config_flags(config))}.",
    ]
    failures = []
    for event_data in validation:
        failures.extend(str(error) for error in event_data.get("errors", []) or [])
        failures.extend(str(code) for code in event_data.get("violated", []) or [])
    if failures:
        parts.append("Validation failures: " + "; ".join(failures) + ".")
    if warnings:
        parts.append("Proposal warnings: " + ", ".join(warnings) + ".")
    return " ".join(parts)


def _proposal_section(proposal: SongsetProposal) -> list[str]:
    warnings = (
        ", ".join(proposal.hard_constraint_warnings)
        if proposal.hard_constraint_warnings
        else "none"
    )
    lines = [
        f"## Proposal {proposal.rank}",
        "",
        f"Score: {proposal.score.total:.4f}",
        (
            "Score components: "
            f"theme {proposal.score.f_theme:.3f}, tempo {proposal.score.f_tempo:.3f}, "
            f"harmony {proposal.score.f_harmony:.3f}, diversity {proposal.score.f_diversity:.3f}."
        ),
        "Origin: "
        f"{'LLM-origin proposal' if proposal.llm_origin else 'deterministic beam ranking'}.",
        f"Warnings: {warnings}.",
        f"Rationale: {proposal.rationale or 'Deterministic beam ranking.'}",
    ]
    if proposal.judge_reason:
        lines.append(f"Judge note: {proposal.judge_reason}")
    lines.extend(
        [
            "",
            "| # | Title | Phase | BPM | Key | Themes | Transition |",
            "|---|---|---:|---:|---|---|---|",
        ]
    )
    for item in proposal.items:
        key = " ".join(part for part in [item.key, item.mode] if part)
        transition = f"shift {item.key_shift_semitones}, gap {item.gap_beats:g} beats"
        lines.append(
            "| "
            + " | ".join(
                [
                    str(item.position),
                    _md_cell(item.title),
                    str(item.phase),
                    "" if item.bpm is None else f"{item.bpm:g}",
                    _md_cell(key),
                    _md_cell(", ".join(item.themes)),
                    _md_cell(transition),
                ]
            )
            + " |"
        )
    lines.append("")
    return lines


def _proposal_origin(proposals: list[SongsetProposal]) -> str:
    if not proposals:
        return "no final proposal source"
    if all(proposal.llm_origin for proposal in proposals):
        return "LLM-origin proposals"
    if any(proposal.llm_origin for proposal in proposals):
        return "a mix of LLM-origin proposals and deterministic beam ranking"
    return "deterministic beam ranking"


def _proposal_warnings(proposals: list[SongsetProposal]) -> list[str]:
    return sorted(
        {warning for proposal in proposals for warning in proposal.hard_constraint_warnings}
    )


def _format_phase_counts(phase_counts: Counter) -> str:
    if not phase_counts:
        return "none"
    return ", ".join(f"phase {phase}: {count}" for phase, count in sorted(phase_counts.items()))


def _strip_markdown_fence(content: str) -> str:
    if not content.startswith("```"):
        return content
    lines = content.splitlines()
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return content


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
