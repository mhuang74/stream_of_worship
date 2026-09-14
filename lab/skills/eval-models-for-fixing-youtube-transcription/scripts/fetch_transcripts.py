#!/usr/bin/env python3
"""Fetch YouTube transcripts for fixture entries into a language-safe cache.

Analysis env:
    uv run --project ops/analysis-service python \
        lab/skills/eval-models-for-fixing-youtube-transcription/scripts/fetch_transcripts.py \
        --fixtures <path> [--out-dir DIR] [--refresh]

Cache filename includes the REQUESTED language (<video_id>__<language>.json)
and the record stores fetched_language_code, because the production fallback
chain can return a different actual language than requested.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (
    OUTPUT_ROOT,
    bootstrap_analysis_src,
    load_fixture,
    load_skill_env,
    save_transcript,
    transcript_cache_path,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch YouTube transcripts for fixture entries")
    parser.add_argument("--fixtures", required=True, type=Path, help="Fixture JSON path")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=OUTPUT_ROOT / "transcripts",
        help="Transcript cache directory",
    )
    parser.add_argument("--refresh", action="store_true", help="Refetch even when cached")
    args = parser.parse_args()

    load_skill_env()
    bootstrap_analysis_src()
    from sow_analysis.workers.youtube_transcript import extract_video_id, fetch_youtube_transcript

    entries = load_fixture(args.fixtures)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    fetched = cached = failed = 0

    async def run() -> None:
        nonlocal fetched, cached, failed
        for entry in entries:
            video_id = extract_video_id(entry["youtube_url"])
            if not video_id:
                print(
                    f"ERROR {entry['song_id']}: could not extract video ID from {entry['youtube_url']}"
                )
                failed += 1
                continue
            cache = transcript_cache_path(args.out_dir, video_id, entry["language"])
            if cache.is_file() and not args.refresh:
                print(f"CACHED {entry['song_id']} ({video_id}, lang={entry['language']})")
                cached += 1
                continue
            try:
                transcript = await fetch_youtube_transcript(video_id, language=entry["language"])
                fetched_code = getattr(transcript, "language_code", "") or "unknown"
                save_transcript(
                    cache, video_id, entry["language"], fetched_code, transcript.snippets
                )
                print(
                    f"FETCHED {entry['song_id']} ({video_id}): "
                    f"requested {entry['language']} → fetched <{fetched_code}>"
                )
                fetched += 1
            except Exception as e:  # noqa: BLE001 — per-song errors never fatal
                print(f"ERROR {entry['song_id']} ({video_id}): {type(e).__name__}: {e}")
                failed += 1

    asyncio.run(run())

    print(f"\nSummary: fetched={fetched} cached={cached} failed={failed} (total {len(entries)})")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
