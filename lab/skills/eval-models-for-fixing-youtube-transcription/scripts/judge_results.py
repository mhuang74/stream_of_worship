#!/usr/bin/env python3
"""Score parsed LRC outputs: mechanical checks always, judge LLM per item.

Analysis env:
    uv run --project ops/analysis-service python \
        lab/skills/eval-models-for-fixing-youtube-transcription/scripts/judge_results.py \
        --run-dir <dir> [--judge-model ID] [--mechanical-only] [--allow-self-judge]

Self-judge guard: if the resolved judge model appears in meta.json["models"]
and --allow-self-judge is absent, exit 1. Mechanical checks use official
lines EXCLUDING [bracketed] tag lines; unmatched lines (neither exact-equal
nor a proper substring of any official line) fail complete_phrases.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (
    JudgeParseError,
    bootstrap_analysis_src,
    judge_official_lines,
    load_fixture,
    load_skill_env,
    parse_judge_json,
    read_jsonl,
    slugify,
)

JUDGE_SCHEMA_HINT = """\
Output ONLY a JSON object with exactly this schema (no markdown, no commentary):
{
  "criteria": {
    "complete_phrases": {"pass": true, "issues": [{"index": 3, "timestamp": "01:23.45", "text": "...", "reason": "..."}]},
    "unique_timestamps": {"pass": true, "issues": [{"detail": "..."}]},
    "ending_window": {"pass": true, "gap_seconds": 12.4, "detail": "..."}
  },
  "overall_pass": true,
  "notes": "one short line"
}
Issues arrays are empty when a criterion passes."""

ENDING_WINDOW_SECONDS = 60

JUDGE_CRITERIA = f"""\
Judge the candidate LRC against the official lyrics by exactly these criteria:
1. Each timestamp must carry a complete lyrics phrase — its text must be exactly one
   full line from the official lyrics (repeated phrases allowed); a partial/fragment
   phrase is a failure.
2. Each timestamp must be unique — no two lines may share the same timestamp, and one
   lyric phrase must never be split across multiple lines with identical timestamps
   whose texts together form one official phrase.
3. The last timestamp must be within {ENDING_WINDOW_SECONDS} seconds of the total song duration:
   0 <= duration_seconds - max(timestamp) <= {ENDING_WINDOW_SECONDS}."""


def build_judge_prompt(official: list[str], lrc_lines, duration_seconds: float) -> str:
    official_block = "\n".join(f"{i + 1}. {ln}" for i, ln in enumerate(official))
    candidate_block = "\n".join(f"{i + 1}. {ln.format()}" for i, ln in enumerate(lrc_lines))
    return f"""{JUDGE_CRITERIA}

Total song duration: {duration_seconds:.2f} seconds

## Official Lyrics (numbered)
{official_block}

## Candidate LRC (numbered)
{candidate_block}

{JUDGE_SCHEMA_HINT}"""


def mechanical_checks(official: list[str], lrc_lines, duration_seconds: float) -> dict:
    """Mechanical checks on tag-excluded official lines."""
    official_stripped = [ln.strip() for ln in official]
    official_set = set(official_stripped)

    # Duplicate timestamps: exact [mm:ss.xx] string collisions.
    seen: dict[str, int] = {}
    for line in lrc_lines:
        ts = line.format().split("]")[0] + "]"
        seen[ts] = seen.get(ts, 0) + 1
    duplicate_pairs = sorted(ts for ts, n in seen.items() if n > 1)

    # Order: non-decreasing time (reported, non-gating).
    order_violations = 0
    prev = float("-inf")
    for line in lrc_lines:
        if line.time_seconds < prev:
            order_violations += 1
        else:
            prev = line.time_seconds

    # Ending gap from MAX timestamp (order violations must not corrupt it).
    # Negative = beyond song end.
    times = [line.time_seconds for line in lrc_lines]
    ending_gap: float | None = duration_seconds - max(times) if times else None

    exact_idx: list[int] = []
    partial_idx: list[int] = []
    unmatched_idx: list[int] = []
    for i, line in enumerate(lrc_lines):
        text = line.text.strip()
        if text in official_set:
            exact_idx.append(i)
        elif any(text in off and text != off for off in official_stripped):
            partial_idx.append(i)
        else:
            unmatched_idx.append(i)

    coverage = len(exact_idx) / len(lrc_lines) if lrc_lines else 0.0
    return {
        "duplicate_pairs": duplicate_pairs,
        "order_violations": order_violations,
        "ending_gap_seconds": ending_gap,
        "exact_match_coverage": round(coverage, 4),
        "partial_phrase_line_indexes": partial_idx,
        "unmatched_line_indexes": unmatched_idx,
    }


def derive_mechanical_criteria(mech: dict) -> dict:
    """Strengthened mechanical verdict — matches judge criterion 1."""
    complete_fail = bool(mech["unmatched_line_indexes"]) or bool(
        mech["partial_phrase_line_indexes"]
    )
    unique_fail = bool(mech["duplicate_pairs"])
    gap = mech["ending_gap_seconds"]
    ending_fail = gap is None or gap < 0 or gap > ENDING_WINDOW_SECONDS
    return {
        "complete_phrases": not complete_fail,
        "unique_timestamps": not unique_fail,
        "ending_window": not ending_fail,
        "overall_pass": not (complete_fail or unique_fail or ending_fail),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Judge run outputs")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--judge-model", default=None)
    parser.add_argument("--mechanical-only", action="store_true")
    parser.add_argument("--allow-self-judge", action="store_true")
    args = parser.parse_args()

    load_skill_env()
    run_dir = args.run_dir
    meta_path = run_dir / "meta.json"
    if not meta_path.is_file():
        print(f"ERROR: {meta_path} not found — run Step 4 first.", file=sys.stderr)
        sys.exit(1)
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    judge_model = args.judge_model or os.environ.get("SOW_LLM_MODEL", "")
    if not args.mechanical_only and not judge_model:
        print(
            "ERROR: no judge model — pass --judge-model or export SOW_LLM_MODEL.", file=sys.stderr
        )
        sys.exit(1)

    # Self-judge guard (independent of the Step 0 interview).
    if (
        not args.mechanical_only
        and judge_model in meta.get("models", [])
        and not args.allow_self_judge
    ):
        print(
            f"ERROR: judge model {judge_model!r} is also a candidate. "
            "Pick a different judge, or pass --allow-self-judge to opt in explicitly "
            "(rows will be badged self-judged in the report).",
            file=sys.stderr,
        )
        sys.exit(1)

    bootstrap_analysis_src()
    from sow_analysis.workers.youtube_transcript import parse_lrc_response

    fixture_path = meta.get("fixture_path")
    if not fixture_path or not Path(fixture_path).is_file():
        print(f"ERROR: fixture from meta.json not found: {fixture_path}", file=sys.stderr)
        sys.exit(1)
    entries = {e["song_id"]: e for e in load_fixture(fixture_path)}

    results = read_jsonl(run_dir / "results.jsonl")
    ok_rows = [r for r in results if r.get("status") == "ok"]
    if not ok_rows:
        print("No ok rows in results.jsonl — nothing to judge.")
        return

    if not args.mechanical_only:
        from openai import OpenAI
        from sow_analysis.workers.llm_rate_limit import call_llm_with_retry

        client = OpenAI(
            api_key=os.environ["SOW_LLM_API_KEY"],
            base_url=os.environ["SOW_LLM_BASE_URL"],
            max_retries=0,
        )

        def judge_call(prompt: str) -> str:
            """One judge LLM call via the production retry util (sync wrapper)."""
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(
                    call_llm_with_retry(
                        lambda: _chat(client, judge_model, prompt),
                        description=f"judge ({judge_model})",
                        loop=loop,
                    )
                )
            finally:
                loop.close()

        def judge_with_parse_retry(prompt: str) -> dict:
            """Parse with one retry appending 'Output ONLY the JSON object.'"""
            try:
                return parse_judge_json(judge_call(prompt))
            except JudgeParseError:
                retry = judge_call(prompt + "\n\nOutput ONLY the JSON object.")
                return parse_judge_json(retry)  # JudgeParseError propagates -> judge_error

    verdicts_dir = run_dir / "verdicts"
    n = len(ok_rows)
    judge_errors = 0
    for i, row in enumerate(ok_rows, 1):
        song_id, model, variant = row["song_id"], row["model"], row["variant"]
        slug = slugify(model)
        print(f"[{i}/{n}] judging {song_id} × {model} × {variant} ...")

        verdict = {
            "song_id": song_id,
            "model": model,
            "variant": variant,
            "judge_model": None if args.mechanical_only else judge_model,
            "criteria_source": "mechanical",
            "mechanical": None,
            "judge": None,
            "criteria": None,
        }
        try:
            entry = entries[song_id]
            official = judge_official_lines(entry)
            parsed_path = run_dir / row["parsed_path"]
            lrc_lines = parse_lrc_response(parsed_path.read_text(encoding="utf-8"))
            mech = mechanical_checks(official, lrc_lines, entry["duration_seconds"])
            verdict["mechanical"] = mech

            if args.mechanical_only:
                verdict["criteria"] = derive_mechanical_criteria(mech)
            else:
                try:
                    judge_verdict = judge_with_parse_retry(
                        build_judge_prompt(official, lrc_lines, entry["duration_seconds"])
                    )
                except Exception as e:  # noqa: BLE001 — judge failure is per-item
                    judge_errors += 1
                    # Judge failed: keep mechanical verdict, conservative overall fail.
                    criteria = derive_mechanical_criteria(mech)
                    criteria["overall_pass"] = False
                    verdict["criteria"] = criteria
                    verdict["judge_error"] = f"{type(e).__name__}: {e}"
                    print(f"  judge_error: {verdict['judge_error']}")
                else:
                    verdict["criteria_source"] = "judge"
                    verdict["judge"] = judge_verdict
                    verdict["criteria"] = {
                        "complete_phrases": bool(
                            judge_verdict["criteria"]["complete_phrases"]["pass"]
                        ),
                        "unique_timestamps": bool(
                            judge_verdict["criteria"]["unique_timestamps"]["pass"]
                        ),
                        "ending_window": bool(judge_verdict["criteria"]["ending_window"]["pass"]),
                        "overall_pass": bool(judge_verdict.get("overall_pass", False)),
                    }
        except Exception as e:  # noqa: BLE001 — per-item catch-all, continue
            judge_errors += 1
            verdict["criteria"] = {
                "complete_phrases": False,
                "unique_timestamps": False,
                "ending_window": False,
                "overall_pass": False,
            }
            verdict["judge_error"] = f"{type(e).__name__}: {e}"
            print(f"  judge_error: {verdict['judge_error']}")

        out_dir = verdicts_dir / variant
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{song_id}__{slug}.json").write_text(
            json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    meta["judge_model"] = None if args.mechanical_only else judge_model
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDone: {n} items judged ({judge_errors} judge errors). Verdicts in {verdicts_dir}")


def _chat(client, model: str, prompt: str) -> str:
    """Sync OpenAI chat call (passed into the production retry util)."""
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    return response.choices[0].message.content


if __name__ == "__main__":
    main()
