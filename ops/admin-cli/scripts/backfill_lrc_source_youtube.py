#!/usr/bin/env python3
"""One-time backfill: set recordings.lrc_source = 'youtube_transcript'.

Targets recordings with ``lrc_source IS NULL`` and ``lrc_status='completed'``
(legacy rows predating provenance tracking). For each, probe YouTube for
transcript availability (zh/en languages only, matching what the LRC pipeline
can consume). Recordings with an available transcript get
``lrc_source='youtube_transcript'``; everything else stays NULL so failed
probes can be retried by re-running this script.

Dry-run by default; pass ``--apply`` to write.

Usage (direct, no proxy — SOW_YOUTUBE_PROXY is not read):
    export SOW_DATABASE_URL=...
    cd ops/admin-cli && uv run --extra admin python scripts/backfill_lrc_source_youtube.py [--apply] [--limit N] [--delay S]
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import TYPE_CHECKING

from youtube_transcript_api import YouTubeTranscriptApi

from stream_of_worship.admin.services.youtube import extract_video_id

if TYPE_CHECKING:
    from stream_of_worship.admin.db.client import DbClient

# Languages the LRC pipeline accepts (services/youtube.py ZH/EN_LANG_CODES).
LANG_PREFIXES = ("zh", "en")

PERMANENT_EXCEPTIONS = {
    "TranscriptsDisabled",
    "VideoUnavailable",
    "VideoUnplayable",
    "InvalidVideoId",
    "AgeRestricted",
}
RETRYABLE_EXCEPTIONS = {"IpBlocked", "RequestBlocked", "RetryError"}
RETRY_ATTEMPTS = 3
RETRY_SLEEP_SECONDS = 5.0

AVAILABLE = "available"
UNAVAILABLE = "unavailable"
ERROR = "error"
SKIP = "skip"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--apply", action="store_true", help="Write updates (default: dry-run)")
    parser.add_argument("--limit", type=int, default=None, help="Cap number of recordings probed")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds between probes")
    return parser.parse_args()


def probe_video(api: YouTubeTranscriptApi, video_id: str) -> tuple[str, str]:
    """Return (verdict, detail). Verdict: available | unavailable | error."""
    last_exc: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            transcripts = api.list(video_id)
        except Exception as e:  # noqa: BLE001 - classify by exception name
            name = type(e).__name__
            if name in PERMANENT_EXCEPTIONS:
                return UNAVAILABLE, name
            if name not in RETRYABLE_EXCEPTIONS:
                return ERROR, name
            last_exc = e
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_SLEEP_SECONDS)
            continue
        for t in transcripts:
            if t.language_code.startswith(LANG_PREFIXES):
                return AVAILABLE, f"{t.language_code}/{'gen' if t.is_generated else 'manual'}"
        return UNAVAILABLE, "NoZhOrEnTranscript"
    return ERROR, type(last_exc).__name__ if last_exc else "Unknown"


def apply_updates(db_client: "DbClient", hash_prefixes: list[str]) -> int:
    """Update lrc_source for the given recordings; returns rowcount."""
    conn = db_client.connection
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE recordings
            SET lrc_source = 'youtube_transcript', updated_at = NOW()
            WHERE hash_prefix = ANY(%s)
              AND lrc_source IS NULL
              AND lrc_status = 'completed'
              AND deleted_at IS NULL
            """,
            (hash_prefixes,),
        )
        rowcount = cur.rowcount
    conn.commit()
    return rowcount


def main() -> int:
    args = parse_args()

    import os

    if not os.environ.get("SOW_DATABASE_URL"):
        print("ERROR: SOW_DATABASE_URL is not set", file=sys.stderr)
        return 2

    from stream_of_worship.admin.config import AdminConfig
    from stream_of_worship.admin.commands.catalog import get_db_client

    db_client = get_db_client(AdminConfig())

    # lrc_source="none" maps to AND lrc_source IS NULL (excludes soft-deleted).
    candidates = [r for r in db_client.list_recordings(lrc_source="none") if r.lrc_status == "completed"]
    failed_excluded = 0
    if args.limit is not None:
        failed_excluded = sum(
            1 for r in db_client.list_recordings(lrc_source="none") if r.lrc_status != "completed"
        )
        candidates = candidates[: args.limit]
    else:
        failed_excluded = len(db_client.list_recordings(lrc_source="none")) - len(candidates)

    print(
        f"candidates: {len(candidates)} completed-missing (excluding {failed_excluded} failed); "
        f"mode: {'APPLY' if args.apply else 'DRY-RUN'}"
    )

    api = YouTubeTranscriptApi()  # direct, no proxy
    counts = {AVAILABLE: 0, UNAVAILABLE: 0, ERROR: 0, SKIP: 0}
    available_prefixes: list[str] = []

    for i, r in enumerate(candidates, 1):
        video_id = extract_video_id(r.youtube_url or "")
        if not video_id:
            verdict, detail = SKIP, "NoVideoId"
        else:
            verdict, detail = probe_video(api, video_id)
        counts[verdict] += 1
        print(f"[{i}/{len(candidates)}] {r.hash_prefix[:8]} {video_id or '-'} {verdict} {detail}")
        if verdict == AVAILABLE:
            available_prefixes.append(r.hash_prefix)
        if i < len(candidates):
            time.sleep(args.delay)

    if args.apply and available_prefixes:
        updated = apply_updates(db_client, available_prefixes)
        print(f"updated {updated} rows")
    else:
        print(f"updated 0 rows (dry-run: would update {len(available_prefixes)})")

    print(
        f"probed {len(candidates)}: available {counts[AVAILABLE]}, unavailable {counts[UNAVAILABLE]}, "
        f"error {counts[ERROR]}, skip {counts[SKIP]}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
