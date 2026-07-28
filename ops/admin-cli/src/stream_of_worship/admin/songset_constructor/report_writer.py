"""Write diagnose_report.md if --report flag is set."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from stream_of_worship.admin.songset_constructor.config import RunConfig
from stream_of_worship.admin.songset_constructor.diagnose import assemble_report_sections


def write_report(config: RunConfig, result: dict, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "diagnose_report.md"

    lines = ["# Songset Constructor Diagnose Report", ""]
    lines.append(f"**Generated:** {datetime.now(UTC).isoformat()}")
    lines.append(f"**Run ID:** {config.thread_id}")
    lines.append("")
    lines.append("## RunConfig")
    for key, value in sorted(config.to_dict().items()):
        lines.append(f"\n- **{key}:** {value}")
    lines.append("")

    sections = assemble_report_sections(config, result)
    lines.extend(sections)

    trace = result.get("trace", [])
    if trace:
        lines.append("## Condensed Graph Trace")
        for entry in trace:
            node = entry.get("node", "?")
            event_name = entry.get("event", "?")
            data = entry.get("data", {})
            if data and isinstance(data, dict) and "prompt" not in data:
                data_str = " ".join(f"{k}={v}" for k, v in data.items())
                lines.append(f"\n- **{node}/{event_name}:** {data_str}")
            else:
                lines.append(f"\n- **{node}/{event_name}**")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
