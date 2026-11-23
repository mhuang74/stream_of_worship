"""Build one listening page for every audio-inpaint run under a directory.

`gen_seam_inpaint_acestep.py` writes a `report.html` per run; this writes the page above
them, so a review is one tab: the same bridge variant on both engines side by side,
then the production gap per take. Everything is read from each run's `index.json`
and every player points at the run's own `clips/` and `bridges/` files, so serve the
output directory over HTTP (relative paths do not work from `file://`).

Usage:
    uv run --project lab/poc-scripts --extra audio_inpaint \
      python lab/poc-scripts/build_inpaint_review_index.py \
      --root lab/poc-scripts/output/audio-inpaint
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

CORRECTION_ORDER = {"raw": 0, "trim": 1, "grid": 2}
ENGINE_LABELS = {"base": "ACE-Step base", "turbo": "ACE-Step turbo", "sa3": "SA3"}


def read_run(path: Path) -> dict[str, Any] | None:
    """One run: its index metadata, its records, and the label its columns carry."""
    index = path / "index.json"
    if not index.exists():
        return None
    data = json.loads(index.read_text())
    records = data.get("variants") or data.get("records") or []
    engine = data.get("engine")
    model = data.get("model")
    label = ENGINE_LABELS.get(engine or "", engine or data.get("app") or "unknown")
    if model:
        label = f"{label} ({model})"
    return {
        "dir": path,
        "name": path.name,
        "data": data,
        "records": records,
        "mode": data.get("mode"),
        "engine": engine,
        "label": label,
        "has_report": (path / "report.html").exists(),
    }


def model_calls(records: list[dict[str, Any]]) -> tuple[float, int]:
    """GPU seconds over model calls: a gap take copies one call's meta onto its 3 rows."""
    calls = [r for r in records if r.get("correction") in (None, "raw") and r.get("meta")]
    return sum(float((r["meta"] or {}).get("elapsed_seconds") or 0.0) for r in calls), len(calls)


def runs_table(runs: list[dict[str, Any]]) -> str:
    rows = []
    for run in runs:
        if run["mode"]:
            ok = sum(1 for r in run["records"] if r.get("status") == "ok")
            gpu, calls = model_calls(run["records"])
            cells = [
                run["name"],
                run["mode"],
                run["engine"] or "",
                str(run["data"].get("model") or ""),
                f"{ok} / {len(run['records'])}",
                f"{float(run['data'].get('wall_seconds') or 0.0):.0f} s",
                f"{gpu:.0f} s ({calls} calls)",
            ]
        else:
            cells = [
                run["name"],
                "(index schema v1)",
                "",
                str(run["data"].get("model") or ""),
                str(len(run["records"])),
                "",
                "",
            ]
        link = f'<a href="{run["name"]}/report.html">report</a>' if run["has_report"] else "—"
        rows.append(
            "<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + f"<td>{link}</td></tr>"
        )
    head = (
        "<tr><th>run</th><th>mode</th><th>engine</th><th>model</th><th>ok / records</th>"
        "<th>wall</th><th>GPU</th><th>run report</th></tr>"
    )
    return f'<table class="runs"><thead>{head}</thead><tbody>{"".join(rows)}</tbody></table>'


def record_cell(run: dict[str, Any], record: dict[str, Any]) -> str:
    """A player for one take plus the numbers that describe it, or why it was refused."""
    if record.get("status") != "ok" or not record.get("clip"):
        return (
            f'<p class="error">{html.escape(str(record.get("error") or record.get("status")))}</p>'
        )
    metrics = record.get("metrics") or {}
    timing = record.get("timing") or {}
    parts: list[str] = []

    def add(text: str) -> None:
        parts.append(text)

    joins = metrics.get("join_rms_jump_db") or {}
    if "left" in joins:
        add(f"join L {joins['left']:+.1f} / R {joins['right']:+.1f} dB")
    raw_joins = metrics.get("raw_join_rms_jump_db") or {}
    if "right" in raw_joins:
        add(f"raw R {raw_joins['right']:+.1f} dB")
    if metrics.get("chroma_corr_a") is not None:
        add(f"chroma A {metrics['chroma_corr_a']:.2f} / B {metrics['chroma_corr_b']:.2f}")
    if metrics.get("stereo_corr_hole") is not None:
        add(f"stereo {metrics['stereo_corr_hole']:.2f}")
    if metrics.get("hole_head_pad_seconds"):
        add(f"head pad {metrics['hole_head_pad_seconds']:.2f} s")
    if metrics.get("hole_overlap_seconds"):
        add(f"handoff {metrics['hole_overlap_seconds']:.2f} s")
    if timing.get("first_onset_ms") is not None:
        add(f"first onset {timing['first_onset_ms']:.0f} ms")
    if timing.get("beat_two_peak_ms") is not None:
        add(f"beat-two peak {timing['beat_two_peak_ms']:.0f} ms")
    if timing.get("trailing_silence_ms") is not None:
        add(f"dead air {timing['trailing_silence_ms']:.0f} ms")
    staged = metrics.get("staged_edge_error_db") or {}
    if staged.get("start") is not None and staged.get("end") is not None:
        add(f"staged residual {staged['start']:+.1f} / {staged['end']:+.1f} dB")

    clip = html.escape(f"{run['name']}/{record['clip']}")
    players = f'<audio controls preload="none" src="{clip}"></audio>'
    if record.get("bridge"):
        bridge = html.escape(f"{run['name']}/{record['bridge']}")
        players += (
            "<details><summary>generated region only</summary>"
            f'<audio controls preload="none" src="{bridge}"></audio></details>'
        )
    metric_line = f'<p class="metrics">{html.escape(" · ".join(parts))}</p>' if parts else ""
    return players + metric_line


def compare_section(
    title: str,
    blurb: str,
    runs: list[dict[str, Any]],
    key: Any,
    sort_key: Any,
    heading: Any,
    subheading: Any,
) -> str:
    """One card per group, one column per run, one row per record in that run."""
    groups: dict[Any, dict[str, list[dict[str, Any]]]] = {}
    for run in runs:
        for record in run["records"]:
            groups.setdefault(key(record), {}).setdefault(run["name"], []).append(record)
    by_name = {run["name"]: run for run in runs}
    cards = []
    for group in sorted(groups, key=sort_key):
        columns = []
        for name, run in by_name.items():
            rows = sorted(groups[group].get(name, []), key=subheading)
            if not rows:
                continue
            body = "".join(
                f'<div class="take"><p class="meta">{html.escape(heading(record))}</p>'
                f"{record_cell(run, record)}</div>"
                for record in rows
            )
            columns.append(f'<div class="col"><h4>{html.escape(run["label"])}</h4>{body}</div>')
        if not columns:
            continue
        label = group if isinstance(group, str) else " · ".join(str(part) for part in group)
        cards.append(
            f'<article class="card"><h3>{html.escape(label)}</h3>'
            f'<div class="cols">{"".join(columns)}</div></article>'
        )
    if not cards:
        return ""
    return f'<section><h2>{html.escape(title)}</h2><p class="sub">{blurb}</p><div class="grid">{"".join(cards)}</div></section>'


def build(root: Path) -> Path:
    runs = [
        run for run in (read_run(path) for path in sorted(root.iterdir()) if path.is_dir()) if run
    ]
    runs.sort(key=lambda run: (run["mode"] is None, run["mode"] or "", run["engine"] or ""))
    bridge = [run for run in runs if run["mode"] == "bridge"]
    gap = [run for run in runs if run["mode"] == "gap"]

    bridge_section = compare_section(
        "Bridges — the same variant on both engines",
        "Each player is the listenable splice: the outgoing recording's real audio, the generated "
        "bridge, the incoming recording's real audio. The collapsible player is the generated "
        "region alone.",
        bridge,
        key=lambda record: record.get("variant_id") or record["id"],
        sort_key=lambda group: group,
        heading=lambda record: f"seed {record.get('seed')} · {record.get('length_seconds')} s hole",
        subheading=lambda record: record.get("seed") or 0,
    )
    gap_section = compare_section(
        "Gap — the 1.393 s production gap (two beats at 86.13 BPM)",
        "31.4 s splice: real audio, the generated fill, the incoming downbeat. <code>raw</code> is "
        "the model output, <code>trim</code> removes its lead-in, <code>grid</code> also "
        "time-stretches hit two onto the beat. Refused takes show the measured reason.",
        gap,
        key=lambda record: (record.get("style"), record.get("seed")),
        sort_key=lambda group: (str(group[0]), group[1] or 0),
        heading=lambda record: f"{record.get('engine')} · {record.get('correction')}",
        subheading=lambda record: CORRECTION_ORDER.get(record.get("correction"), 9),
    )

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Audio-inpaint review</title>
<style>{STYLE}</style>
</head>
<body>
<h1>Audio-inpaint review</h1>
<p class="sub">Every seam under <code>{html.escape(root.name)}/</code> on one page. Players are
<code>preload="none"</code> — press play to load. Serve the directory over HTTP (relative audio
paths do not work from <code>file://</code>):
<code>cd {html.escape(str(root))} &amp;&amp; python -m http.server 8090</code>, then
<code>http://localhost:8090/index.html</code>.</p>
<h2>Runs</h2>
{runs_table(runs)}
{bridge_section}
{gap_section}
<h2>Reading the numbers</h2>
<ul class="legend">
<li><code>join L/R</code> — RMS step across each hole edge, measured outside the de-click fades;
the right figure is measured after the handoff to the incoming recording.</li>
<li><code>raw R</code> — the right splice before that handoff, i.e. how far the model's tail sat
from the incoming audio.</li>
<li><code>chroma A/B</code> — pitch-class correlation of the generated region with the 4 s of real
audio on each side.</li>
<li><code>stereo</code> — L/R coherence of the generated region (real music: 0.5–0.9).</li>
<li><code>head pad</code> / <code>handoff</code> — seconds of the outgoing recording's own audio
spliced over a dead model head, and of the incoming audio crossfaded over the model's tail.</li>
<li><code>first onset</code>, <code>beat-two peak</code>, <code>dead air</code> — gap timing: where
the fill's first hit lands, where its accent lands against the 696.6 ms beat, and how much
silence precedes the incoming downbeat.</li>
<li><code>staged residual</code> — dB the level staging still misses at the joints after the 18 dB
edge ramp; above 6 dB the take is refused.</li>
</ul>
</body>
</html>
"""
    out = root / "index.html"
    out.write_text(page)
    return out


STYLE = """
:root { color-scheme: light dark; }
body { font: 14px/1.5 system-ui, sans-serif; margin: 0 auto; padding: 24px; max-width: 1400px; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 16px; border-bottom: 1px solid #ddd; padding-bottom: 4px; margin-top: 36px; }
h3 { font-size: 13px; margin: 0 0 8px; overflow-wrap: anywhere; }
h4 { font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: #777; margin: 0 0 4px; }
.sub { color: #666; margin: 4px 0 16px; }
.legend { color: #666; font-size: 12px; margin: 4px 0 16px; }
.grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(560px, 1fr)); }
.card { border: 1px solid #ddd; border-radius: 8px; padding: 12px 14px; }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.col { min-width: 0; }
.take { border-top: 1px dashed #ddd; padding-top: 8px; margin-top: 8px; }
.take:first-of-type { border-top: none; }
.meta, .metrics { color: #555; font-size: 11px; margin: 4px 0; }
audio { width: 100%; height: 32px; }
.error { color: #b00; font-size: 11px; margin: 4px 0; }
details { margin-top: 4px; font-size: 11px; }
summary { cursor: pointer; color: #666; }
table.runs { border-collapse: collapse; font-size: 12px; }
table.runs th, table.runs td { border: 1px solid #ddd; padding: 4px 8px; text-align: left; }
code { font-size: 12px; }
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default="lab/poc-scripts/output/audio-inpaint",
        help="directory holding the run directories (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        raise SystemExit(f"no such directory: {root}")
    out = build(root)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
