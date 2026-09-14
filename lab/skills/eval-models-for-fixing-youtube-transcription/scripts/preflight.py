#!/usr/bin/env python3
"""Preflight: verify env vars and ping each candidate + judge model.

Analysis env:
    uv run --project ops/analysis-service python \
        lab/skills/eval-models-for-fixing-youtube-transcription/scripts/preflight.py \
        --models "<comma list or @file>" [--skip-ping] [--strict-ping]

Any ping FAIL is a warning (exit 0) unless --strict-ping, in which case any
FAIL exits 1. Rationale: max_tokens=1 false-FAILs reasoning/thinking models;
a ping failure is a signal, not a verdict.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (
    EvalError,
    bootstrap_analysis_src,
    load_skill_env,
    now_run_id,
    require_env,
    resolve_model_ids,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Preflight checks for the LLM eval bake-off")
    parser.add_argument(
        "--models", default=None, help="Comma-separated model IDs or path to a file (leading @ ok)"
    )
    parser.add_argument("--skip-ping", action="store_true", help="Skip the chat ping calls")
    parser.add_argument(
        "--strict-ping",
        action="store_true",
        help="Any ping FAIL exits 1 (default: warning, exit 0)",
    )
    args = parser.parse_args()

    load_skill_env()
    require_env(["SOW_LLM_API_KEY", "SOW_LLM_BASE_URL"])
    try:
        models = resolve_model_ids(args.models)
    except EvalError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Run ID: {now_run_id()}")
    print(f"Candidates ({len(models)}):")
    for m in models:
        print(f"  - {m}")

    judge_model = os.environ.get("SOW_LLM_MODEL", "")
    if not judge_model:
        print(
            "WARNING: SOW_LLM_MODEL unset — judge model unresolved (required at Step 5).",
            file=sys.stderr,
        )
        judge_row = None
    else:
        judge_row = judge_model

    if args.skip_ping:
        print("Ping skipped (--skip-ping).")
        return

    bootstrap_analysis_src()
    from openai import OpenAI

    client = OpenAI(
        api_key=os.environ["SOW_LLM_API_KEY"],
        base_url=os.environ["SOW_LLM_BASE_URL"],
        max_retries=1,
        timeout=15,
    )

    rows: list[tuple[str, bool, float, str]] = []
    for model in [*models, judge_row] if judge_row else list(models):
        label = f"{model} (judge)" if model == judge_row and model in models else model
        start = time.monotonic()
        ok, err = True, ""
        try:
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=8,  # reasoning models reject max_tokens=1
                temperature=0,
            )
        except Exception as e:  # noqa: BLE001 — ping must never crash the phase
            ok, err = False, f"{type(e).__name__}: {e}"
        rows.append((label, ok, time.monotonic() - start, err))
        time.sleep(0.5)  # avoid provider burst 429

    _print_table(rows)
    failures = [r for r in rows if not r[1]]
    if failures:
        if args.strict_ping:
            print(f"\n{len(failures)} ping FAIL(s) — strict mode: exiting 1.")
            sys.exit(1)
        print(
            f"\nWARNING: {len(failures)} ping FAIL(s) — treat as a signal, not a verdict. Continuing (exit 0)."
        )
    else:
        print("\nAll pings OK.")


def _print_table(rows: list[tuple[str, bool, float, str]]) -> None:
    try:
        from rich.console import Console
        from rich.table import Table

        table = Table(title="Preflight pings")
        table.add_column("model")
        table.add_column("OK")
        table.add_column("latency (s)", justify="right")
        table.add_column("error")
        for model, ok, latency, err in rows:
            table.add_row(
                model, "[green]OK[/green]" if ok else "[red]FAIL[/red]", f"{latency:.1f}", err
            )
        Console().print(table)
    except ImportError:
        print("model | OK | latency | error")
        for model, ok, latency, err in rows:
            print(f"{model} | {'OK' if ok else 'FAIL'} | {latency:.1f} | {err}")


if __name__ == "__main__":
    main()
