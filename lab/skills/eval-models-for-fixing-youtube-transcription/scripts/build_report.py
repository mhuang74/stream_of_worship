#!/usr/bin/env python3
"""Build the ranked comparison report from a judged run (no LLM calls).

Analysis env:
    uv run --project ops/analysis-service python \
        lab/skills/eval-models-for-fixing-youtube-transcription/scripts/build_report.py \
        --run-dir <dir>

Writes scores.json + report.md; prints top-3 ranking per variant to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import read_jsonl, slugify


def load_verdicts(run_dir: Path, variants: list[str]) -> list[dict]:
    verdicts = []
    for variant in variants:
        vdir = run_dir / "verdicts" / variant
        if vdir.is_dir():
            for p in sorted(vdir.glob("*.json")):
                verdicts.append(json.loads(p.read_text(encoding="utf-8")))
    return verdicts


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the eval comparison report")
    parser.add_argument("--run-dir", required=True, type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir

    meta_path = run_dir / "meta.json"
    if not meta_path.is_file():
        print(f"ERROR: {meta_path} not found — run Step 4 first.", file=sys.stderr)
        sys.exit(1)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    variants = meta.get("variants", ["prod", "strict"])
    models = meta.get("models", [])
    judge_model = meta.get("judge_model")
    results = read_jsonl(run_dir / "results.jsonl")
    verdicts = load_verdicts(run_dir, variants)

    fixture_path = meta.get("fixture_path")
    entries: dict[str, dict] = {}
    if fixture_path and Path(fixture_path).is_file():
        entries = {
            e["song_id"]: e for e in json.loads(Path(fixture_path).read_text(encoding="utf-8"))
        }

    self_judged = bool(judge_model and judge_model in models)
    song_ids = sorted({r["song_id"] for r in results})

    # ------------------------------------------------------------------
    # duration_suspect: every ok item (across models+variants) for a song
    # fails ending_window.
    ok_by_song: dict[str, list[dict]] = defaultdict(list)
    vmap = {(v["song_id"], v["model"], v["variant"]): v for v in verdicts}
    for r in results:
        if r.get("status") == "ok":
            v = vmap.get((r["song_id"], r["model"], r["variant"]))
            if v:
                ok_by_song[r["song_id"]].append(v)
    duration_suspect = {}
    for song_id in song_ids:
        items = ok_by_song.get(song_id, [])
        duration_suspect[song_id] = bool(items) and all(
            not v["criteria"]["ending_window"] for v in items
        )

    # ------------------------------------------------------------------
    # Per model×variant ranking rows.
    def agg_row(model: str, variant: str) -> dict:
        items = [v for v in verdicts if v["model"] == model and v["variant"] == variant]
        ok_items = items
        errors = sum(
            1
            for r in results
            if r["model"] == model and r["variant"] == variant and r.get("status") != "ok"
        )
        row = {
            "model": model,
            "variant": variant,
            "songs": len(ok_items),
            "pass_rate": None,
            "complete_phrases_fails": 0,
            "unique_timestamps_fails": 0,
            "ending_window_fails": 0,
            "partial_phrase_lines": 0,
            "unmatched_lines": 0,
            "duplicate_timestamps": 0,
            "order_violations": 0,
            "avg_exact_match_coverage": None,
            "judge_errors": 0,
            "errors": errors,
            "criteria_source_counts": {"judge": 0, "mechanical": 0},
        }
        if not ok_items:
            return row
        passes = 0
        coverage = 0.0
        for v in ok_items:
            c = v["criteria"]
            if c["overall_pass"]:
                passes += 1
            if not c["complete_phrases"]:
                row["complete_phrases_fails"] += 1
            if not c["unique_timestamps"]:
                row["unique_timestamps_fails"] += 1
            if not c["ending_window"]:
                row["ending_window_fails"] += 1
            m = v.get("mechanical") or {}
            row["partial_phrase_lines"] += len(m.get("partial_phrase_line_indexes", []))
            row["unmatched_lines"] += len(m.get("unmatched_line_indexes", []))
            row["duplicate_timestamps"] += len(m.get("duplicate_pairs", []))
            row["order_violations"] += m.get("order_violations", 0)
            coverage += m.get("exact_match_coverage", 0.0)
            if v.get("judge_error"):
                row["judge_errors"] += 1
            src = v.get("criteria_source", "mechanical")
            row["criteria_source_counts"][src] = row["criteria_source_counts"].get(src, 0) + 1
        row["pass_rate"] = passes / len(ok_items)
        row["avg_exact_match_coverage"] = round(coverage / len(ok_items), 4)
        return row

    ranking: dict[str, list[dict]] = {}
    for variant in variants:
        rows = [agg_row(m, variant) for m in models]

        def sort_key(r: dict):
            # pass_rate desc (N/A last) → partial asc → unmatched asc → coverage desc
            return (
                r["pass_rate"] is None,  # N/A last
                -(r["pass_rate"] if r["pass_rate"] is not None else 0.0),
                r["partial_phrase_lines"],
                r["unmatched_lines"],
                -(
                    r["avg_exact_match_coverage"]
                    if r["avg_exact_match_coverage"] is not None
                    else 0.0
                ),
            )

        ranking[variant] = sorted(rows, key=sort_key)

    # ------------------------------------------------------------------
    # strict vs prod delta view.
    delta = []
    for m in models:
        prod = next((r for r in ranking.get("prod", []) if r["model"] == m), None)
        strict = next((r for r in ranking.get("strict", []) if r["model"] == m), None)
        if prod and strict:
            delta.append(
                {
                    "model": m,
                    "pass_rate_prod": prod["pass_rate"],
                    "pass_rate_strict": strict["pass_rate"],
                    "pass_rate_delta": (
                        None
                        if prod["pass_rate"] is None or strict["pass_rate"] is None
                        else round(strict["pass_rate"] - prod["pass_rate"], 4)
                    ),
                    "failure_delta": {
                        k: strict[k] - prod[k]
                        for k in (
                            "complete_phrases_fails",
                            "unique_timestamps_fails",
                            "ending_window_fails",
                            "partial_phrase_lines",
                            "unmatched_lines",
                            "duplicate_timestamps",
                        )
                    },
                }
            )

    scores = {
        "judge_model": judge_model,
        "self_judged": self_judged,
        "variants": variants,
        "ranking": ranking,
        "delta_strict_vs_prod": delta,
        "duration_suspect": duration_suspect,
    }
    (run_dir / "scores.json").write_text(
        json.dumps(scores, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    report = _render_report(
        run_dir, meta, entries, results, verdicts, ranking, delta, duration_suspect, self_judged
    )
    (run_dir / "report.md").write_text(report, encoding="utf-8")

    for variant in variants:
        print(f"\nTop-3 ranking ({variant}):")
        for i, r in enumerate(ranking[variant][:3], 1):
            pr = f"{r['pass_rate']:.0%}" if r["pass_rate"] is not None else "N/A"
            print(f"  {i}. {r['model']} — pass_rate {pr} ({r['songs']} items)")
    print(f"\nReport: {run_dir / 'report.md'}")


def _render_report(
    run_dir: Path,
    meta: dict,
    entries: dict[str, dict],
    results: list[dict],
    verdicts: list[dict],
    ranking: dict[str, list[dict]],
    delta: list[dict],
    duration_suspect: dict[str, bool],
    self_judged: bool,
) -> str:
    variants = meta.get("variants", [])
    lines: list[str] = []
    lines.append("# Model bake-off: YouTube transcript → timed LRC correction")
    lines.append("")
    judge = meta.get("judge_model")
    lines.append(f"- **Judge model:** {judge or '— (mechanical-only run)'}")
    if self_judged:
        lines.append(
            f"- ⚠️ **SELF-JUDGED RUN**: the judge model ({judge}) is also a candidate — discount its rows."
        )
    lines.append(f"- **Variants:** {', '.join(variants)}")
    lines.append(f"- **Provider host:** {meta.get('base_url_host') or 'unknown'}")
    lines.append(f"- **Run:** {meta.get('utc_timestamp')} (git {meta.get('git_head')})")
    lines.append("")
    lines.append(
        "> Documented divergences: (1) the correction prompt keeps `[bracketed]` section-tag lines "
        "(production-faithful) while judge/mechanical official lines exclude them — a model echoing a "
        "tag line fails mechanically. (2) `lyrics_source: raw` songs have paragraph-boundary official "
        "lines, so exact-match coverage is structurally weaker for those songs."
    )
    lines.append("")

    for variant in variants:
        lines.append(f"## Ranking — {variant}")
        lines.append("")
        lines.append(
            "| model | songs | pass_rate | complete fails | unique fails | ending fails | partial lines | unmatched lines | dup timestamps | order violations | avg coverage | judge errors | errors | criteria source |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for r in ranking.get(variant, []):
            pr = f"{r['pass_rate']:.0%}" if r["pass_rate"] is not None else "N/A (ranked last)"
            cov = (
                f"{r['avg_exact_match_coverage']:.0%}"
                if r["avg_exact_match_coverage"] is not None
                else "—"
            )
            src = r["criteria_source_counts"]
            lines.append(
                f"| {r['model']} | {r['songs']} | {pr} | {r['complete_phrases_fails']} | "
                f"{r['unique_timestamps_fails']} | {r['ending_window_fails']} | {r['partial_phrase_lines']} | "
                f"{r['unmatched_lines']} | {r['duplicate_timestamps']} | {r['order_violations']} | {cov} | "
                f"{r['judge_errors']} | {r['errors']} | judge {src.get('judge', 0)} / mech {src.get('mechanical', 0)} |"
            )
        lines.append("")

    if delta:
        lines.append("## Delta — strict vs prod")
        lines.append("")
        lines.append(
            "> This is the prompt-vs-capability read: positive pass_rate_delta means the strict "
            "(merge/dedup rules) prompt helped that model."
        )
        lines.append("")
        lines.append(
            "| model | pass_rate prod | pass_rate strict | delta | failure-count deltas (strict − prod) |"
        )
        lines.append("|---|---|---|---|---|")
        for d in delta:
            pp = f"{d['pass_rate_prod']:.0%}" if d["pass_rate_prod"] is not None else "N/A"
            ps = f"{d['pass_rate_strict']:.0%}" if d["pass_rate_strict"] is not None else "N/A"
            pd = "—" if d["pass_rate_delta"] is None else f"{d['pass_rate_delta']:+.0%}"
            fd = ", ".join(
                f"{k.split('_fails')[0].split('_lines')[0].replace('_', ' ')} {v:+d}"
                for k, v in d["failure_delta"].items()
            )
            lines.append(f"| {d['model']} | {pp} | {ps} | {pd} | {fd} |")
        lines.append("")

    suspect = [s for s, flag in duration_suspect.items() if flag]
    if suspect:
        lines.append("## ⚠️ Duration anomalies (`duration_suspect`)")
        lines.append("")
        lines.append(
            "Every ok item (across models and variants) failed the ending window for these songs — "
            "this points at the fixture duration, not model quality:"
        )
        for s in suspect:
            title = entries.get(s, {}).get("title", "?")
            dur = entries.get(s, {}).get("duration_seconds", "?")
            lines.append(f"- {s} ({title}): duration_seconds = {dur}")
        lines.append("")

    lines.append("## Per-item verdicts")
    lines.append("")
    vmap = {(v["song_id"], v["model"], v["variant"]): v for v in verdicts}
    for r in sorted(results, key=lambda x: (x["song_id"], x["model"], x["variant"])):
        song_id, model, variant = r["song_id"], r["model"], r["variant"]
        entry = entries.get(song_id, {})
        title = entry.get("title", "?")
        lyrics_source = entry.get("lyrics_source", "absent")
        v = vmap.get((song_id, model, variant))
        slug = slugify(model)
        lines.append(f"### {song_id} ({title}) × {model} × {variant}")
        lines.append("")
        if r.get("status") != "ok":
            lines.append(f"- **status:** error — {r.get('error')}")
        elif v is None:
            lines.append("- **status:** ok (not judged — run Step 5)")
        else:
            c = v["criteria"]
            badge = ""
            if v.get("criteria_source") == "judge" and self_judged:
                badge = " 🔶 self-judged"
            if v.get("judge_error"):
                badge += " ⚠️ judge_error"
            overall = "**PASS**" if c["overall_pass"] else "**FAIL**"
            lines.append(
                f"- **verdict:** {overall}{badge} (criteria_source: {v.get('criteria_source')}, "
                f"lyrics_source: {lyrics_source})"
            )
            mech = v.get("mechanical") or {}
            lines.append(f"- mechanical: {json.dumps(mech, ensure_ascii=False)}")
            j = v.get("judge")
            if j:
                if j.get("notes"):
                    lines.append(f"- judge notes: {j['notes']}")
                crit = j.get("criteria", {})
                for name in ("complete_phrases", "unique_timestamps", "ending_window"):
                    cd = crit.get(name) or {}
                    issues = cd.get("issues") or []
                    mark = "✅" if cd.get("pass") else "❌"
                    lines.append(f"- judge/{name}: {mark}")
                    for issue in issues:
                        lines.append(f"  - {json.dumps(issue, ensure_ascii=False)}")
        raw_link = f"raw/{variant}/{song_id}__{slug}.txt"
        parsed_link = f"parsed/{variant}/{song_id}__{slug}.lrc"
        lines.append(f"- artifacts: [`{raw_link}`]({raw_link}) · [`{parsed_link}`]({parsed_link})")
        lines.append("")

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
