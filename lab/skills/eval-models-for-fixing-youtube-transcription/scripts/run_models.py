#!/usr/bin/env python3
"""Run every candidate model over the fixtures under both prompt variants.

Analysis env:
    uv run --project ops/analysis-service python \
        lab/skills/eval-models-for-fixing-youtube-transcription/scripts/run_models.py \
        --fixtures <path> --models "<comma list or @file>" \
        [--variants prod,strict] [--run-dir DIR]

The production file stays untouched: the `strict` variant injects an
"Additional Requirements" block into the prompt at run time, immediately
before the `## Output Format` heading.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (
    OUTPUT_ROOT,
    EvalError,
    bootstrap_analysis_src,
    load_fixture,
    load_skill_env,
    load_transcript,
    lrc_text,
    now_run_id,
    resolve_lyrics_lines,
    resolve_model_ids,
    slugify,
    snippets_to_namespace,
    transcript_cache_path,
    write_jsonl,
)

STRICT_BLOCK = """\
## Additional Requirements

1. Transcript lines are frequently fragments of one sung phrase. Whenever a run of
   consecutive transcribed lines together forms ONE lyric line from the Official
   Lyrics, merge them into a SINGLE output line using the FIRST merged line's timestamp.
2. If several transcribed lines share the exact same timestamp, merge them into one
   output line at that timestamp.
3. Never emit a partial phrase: each output line's text must be exactly one full
   lyric line from the Official Lyrics (repeated phrases allowed).
4. Lines in the Official Lyrics consisting entirely of a [bracketed] label are
   section tags (metadata), not sung phrases — never emit them as lyric lines."""

VALID_VARIANTS = ("prod", "strict")


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 — git metadata is best-effort
        return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run candidate models over fixtures")
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--transcripts-dir", type=Path, default=OUTPUT_ROOT / "transcripts")
    parser.add_argument(
        "--models", default=None, help="Comma-separated model IDs or path to a file (leading @ ok)"
    )
    parser.add_argument(
        "--variants", default="prod,strict", help="Comma-separated subset of: prod,strict"
    )
    parser.add_argument("--run-dir", type=Path, default=None)
    args = parser.parse_args()

    try:
        models = resolve_model_ids(args.models)
    except EvalError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    if not variants or any(v not in VALID_VARIANTS for v in variants):
        print(
            f"ERROR: --variants must be a comma-separated subset of {VALID_VARIANTS}",
            file=sys.stderr,
        )
        sys.exit(1)

    load_skill_env()
    bootstrap_analysis_src()
    from sow_analysis.config import settings
    from sow_analysis.workers.youtube_transcript import (
        LLMConfigError,
        YouTubeTranscriptError,
        _format_transcript_text,
        _llm_correct,
        build_correction_prompt,
        extract_video_id,
        parse_lrc_response,
    )

    entries = load_fixture(args.fixtures)
    run_dir = args.run_dir or (OUTPUT_ROOT / f"{now_run_id()}-run")
    for variant in variants:
        (run_dir / "raw" / variant).mkdir(parents=True, exist_ok=True)
        (run_dir / "parsed" / variant).mkdir(parents=True, exist_ok=True)

    base_url_host = (
        settings.SOW_LLM_BASE_URL.split("//")[-1].split("/")[0] if settings.SOW_LLM_BASE_URL else ""
    )
    meta = {
        "models": models,
        "variants": variants,
        "judge_model": None,
        "base_url_host": base_url_host,
        "fixture_path": str(args.fixtures),
        "transcripts_dir": str(args.transcripts_dir),
        "utc_timestamp": now_run_id(),
        "git_head": _git_head(),
    }
    (run_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    results_path = run_dir / "results.jsonl"
    write_jsonl(results_path, [])  # truncate at phase start

    n_items = len(entries) * len(models) * len(variants)
    done = 0
    for entry in entries:
        for model in models:
            slug = slugify(model)
            for variant in variants:
                done += 1
                print(f"[{done}/{n_items}] {entry['song_id']} × {model} × {variant} ...")
                row = {
                    "song_id": entry["song_id"],
                    "model": model,
                    "variant": variant,
                    "language": entry["language"],
                    "status": "error",
                    "n_lines": 0,
                    "raw_path": None,
                    "parsed_path": None,
                    "error": None,
                }
                try:
                    video_id = extract_video_id(entry["youtube_url"])
                    if not video_id:
                        raise ValueError(f"Could not extract video ID from {entry['youtube_url']}")
                    cache = transcript_cache_path(args.transcripts_dir, video_id, entry["language"])
                    if not cache.is_file():
                        raise FileNotFoundError(
                            f"No transcript cache for ({video_id}, {entry['language']}): {cache} — run Step 3"
                        )
                    snippets = load_transcript(cache)["snippets"]
                    transcript_text = _format_transcript_text(snippets_to_namespace(snippets))
                    prompt = build_correction_prompt(
                        transcript_text,
                        resolve_lyrics_lines(entry),
                        language=entry["language"],
                    )
                    if variant == "strict":
                        prompt = prompt.replace(
                            "## Output Format", STRICT_BLOCK + "\n\n## Output Format", 1
                        )

                    response = await_(_llm_correct(prompt, model))

                    raw_path = run_dir / "raw" / variant / f"{entry['song_id']}__{slug}.txt"
                    raw_path.write_text(response, encoding="utf-8")
                    lrc_lines = parse_lrc_response(response)
                    parsed_path = run_dir / "parsed" / variant / f"{entry['song_id']}__{slug}.lrc"
                    parsed_path.write_text(lrc_text(lrc_lines) + "\n", encoding="utf-8")

                    row.update(
                        status="ok",
                        n_lines=len(lrc_lines),
                        raw_path=str(raw_path.relative_to(run_dir)),
                        parsed_path=str(parsed_path.relative_to(run_dir)),
                    )
                    print(f"  ok ({len(lrc_lines)} lines)")
                except (YouTubeTranscriptError, LLMConfigError, ValueError) as e:
                    row["error"] = f"{type(e).__name__}: {e}"
                    print(f"  ERROR: {row['error']}")
                except Exception as e:  # noqa: BLE001 — one bad item must never kill the phase
                    row["error"] = f"{type(e).__name__}: {e}"
                    print(f"  ERROR: {row['error']}")
                write_jsonl(results_path, [row], append=True)


def await_(coro):
    """Run a coroutine to completion from sync code."""
    import asyncio

    return asyncio.run(coro)


if __name__ == "__main__":
    main()
