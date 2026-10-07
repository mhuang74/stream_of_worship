#!/usr/bin/env python3
"""Phase 0a: snapshot LRC truth lists + review queue from the production DB.

Implements Phase 0a of ``specs/lrc-review-triage-cascade-design-v2.md``:

- positive truth list: every ``published`` recording song_id
  (equivalent to ``sow-admin audio list --visibility published --format ids``)
  → ``positive.txt``
- negative truth list: every song_id with OPEN ``sad`` lyrics feedback
  (equivalent to ``sow-admin lyrics feedback list --rating poor --format ids``)
  → ``negative.txt``
- review-queue snapshot: every non-deleted ``review`` recording with
  song_id, hash_prefix, lrc_source, lrc_status, youtube_url presence → JSON
- seed-subset assertions: seed positives ⊆ published snapshot, seed negatives ⊆
  feedback-poor snapshot. Divergences are recorded per-song in the JSON; the
  seed list stays authoritative for that song. Nothing is dropped silently.

Read-only: zero writes to canonical Lyrics, catalog status, provenance, or
visibility. The only artifacts are files under ``eval/lrc_truth/``.

Usage:
    uv run --project ops/admin-cli --extra admin \
        python lab/poc-scripts/snapshot_lrc_truth.py \
        [--output-dir eval/lrc_truth] [--config <path>]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.db.connection import ConnectionProvider

DEFAULT_OUTPUT_DIR = Path("eval/lrc_truth")

RATING_FILTER = {"good": "happy", "poor": "sad"}


def query_published_song_ids(conn) -> list[str]:
    """Song IDs of all non-deleted recordings with visibility_status='published'.

    Mirrors the CLI's recording→song dedup (one id per song, sorted for stable
    diffing; the CLI emits in feedback/import order, this snapshot sorts).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT s.id
            FROM recordings r
            JOIN songs s ON s.id = r.song_id
            WHERE r.visibility_status = 'published'
              AND r.deleted_at IS NULL
              AND s.id IS NOT NULL
            ORDER BY s.id
            """
        )
        return [row[0] for row in cur.fetchall()]


def query_feedback_poor_song_ids(conn) -> list[str]:
    """Song IDs with OPEN 'sad' lyrics feedback (the must-FAIL set).

    Mirrors ``lyrics feedback list --rating poor``: groups are built from open
    sad rows; a song qualifies while it has ≥1 unresolved sad row. Songless
    recordings cannot appear here (no song_id to list) — the CLI reports them
    on stderr; if that ever happens the review-queue snapshot is the fallback
    record.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT s.id
            FROM lyrics_feedback f
            JOIN recordings r ON r.content_hash = f.recording_content_hash
            JOIN songs s ON s.id = r.song_id
            WHERE f.resolved_at IS NULL
              AND f.rating = 'sad'
              AND r.deleted_at IS NULL
            ORDER BY s.id
            """
        )
        return [row[0] for row in cur.fetchall()]


def query_review_queue(conn) -> list[dict]:
    """All non-deleted review recordings with triage-relevant columns."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id, r.hash_prefix, r.lrc_source, r.lrc_status,
                   (r.youtube_url IS NOT NULL) AS has_youtube_url, r.imported_at
            FROM recordings r
            LEFT JOIN songs s ON s.id = r.song_id
            WHERE r.visibility_status = 'review'
              AND r.deleted_at IS NULL
            ORDER BY r.imported_at DESC, r.hash_prefix
            """
        )
        rows = []
        for (
            song_id,
            hash_prefix,
            lrc_source,
            lrc_status,
            has_youtube_url,
            imported_at,
        ) in cur.fetchall():
            rows.append(
                {
                    "song_id": song_id,
                    "hash_prefix": hash_prefix,
                    "lrc_source": lrc_source,
                    "lrc_status": lrc_status,
                    "has_youtube_url": has_youtube_url,
                    "imported_at": (
                        imported_at.isoformat()
                        if hasattr(imported_at, "isoformat")
                        else imported_at
                    ),
                }
            )
        return rows


def load_seed_ids(path: Path) -> list[str]:
    ids = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            ids.append(line)
    return ids


def assert_seed_subset(seeds: list[str], snapshot_ids: set[str], kind: str) -> list[dict]:
    """Seed ⊆ snapshot assertion; every seed song gets a per-song result.

    A missing seed is a divergence: recorded, never silently dropped — the
    seed list stays authoritative for that song.
    """
    results = []
    for song_id in seeds:
        present = song_id in snapshot_ids
        entry = {"song_id": song_id, "in_snapshot": present}
        if not present:
            entry["divergence"] = (
                f"seed {kind} missing from live {kind} snapshot; "
                f"seed list remains authoritative for this song"
            )
        results.append(entry)
    return results


def write_ids(paths: list[Path], ids: list[str]) -> None:
    for path in paths:
        path.write_text("\n".join(ids) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for positive.txt / negative.txt / snapshot JSON "
        "(default: eval/lrc_truth)",
    )
    parser.add_argument("--config", type=Path, default=None, help="Admin config path")
    args = parser.parse_args(argv)

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC)
    started_at = now.isoformat(timespec="seconds")

    config = AdminConfig.load(args.config)
    provider = ConnectionProvider(config.get_connection_url())
    conn = provider.get_connection()

    published_started = datetime.now(UTC)
    published_ids = query_published_song_ids(conn)
    published_finished = datetime.now(UTC).isoformat(timespec="seconds")
    published_started_iso = published_started.isoformat(timespec="seconds")

    negative_started = datetime.now(UTC)
    negative_ids = query_feedback_poor_song_ids(conn)
    negative_finished = datetime.now(UTC).isoformat(timespec="seconds")
    negative_started_iso = negative_started.isoformat(timespec="seconds")

    review_started = datetime.now(UTC)
    review_queue = query_review_queue(conn)
    review_finished = datetime.now(UTC).isoformat(timespec="seconds")
    review_started_iso = review_started.isoformat(timespec="seconds")
    provider.close()

    seed_pos = load_seed_ids(output_dir / "seed_positive.txt")
    seed_neg = load_seed_ids(output_dir / "seed_negative.txt")
    if not seed_pos or not seed_neg:
        print(
            "error: seed_positive.txt / seed_negative.txt missing or empty in "
            f"{output_dir}; seed lists are the regression anchor and must exist",
            file=sys.stderr,
        )
        return 2

    positive_assertions = assert_seed_subset(seed_pos, set(published_ids), "positive")
    negative_assertions = assert_seed_subset(seed_neg, set(negative_ids), "negative")
    positive_divergences = [a for a in positive_assertions if not a["in_snapshot"]]
    negative_divergences = [a for a in negative_assertions if not a["in_snapshot"]]

    lrc_source_counts: dict[str, int] = {}
    for row in review_queue:
        key = row["lrc_source"] or "null"
        lrc_source_counts[key] = lrc_source_counts.get(key, 0) + 1
    with_youtube_url = sum(1 for row in review_queue if row["has_youtube_url"])

    snapshot = {
        "schema_version": 1,
        "created_at": started_at,
        "spec": "specs/lrc-review-triage-cascade-design-v2.md",
        "phase": "0a",
        "queries": {
            "positive": {
                "command": "sow-admin audio list --visibility published --format ids",
                "sql_equivalent": (
                    "SELECT DISTINCT s.id FROM recordings r JOIN songs s ON s.id = r.song_id "
                    "WHERE r.visibility_status = 'published' AND r.deleted_at IS NULL"
                ),
                "started_at": published_started_iso,
                "finished_at": published_finished,
                "count": len(published_ids),
            },
            "negative": {
                "command": "sow-admin lyrics feedback list --rating poor --format ids",
                "sql_equivalent": (
                    "SELECT DISTINCT s.id FROM lyrics_feedback f "
                    "JOIN recordings r ON r.content_hash = f.recording_content_hash "
                    "JOIN songs s ON s.id = r.song_id "
                    "WHERE f.resolved_at IS NULL AND f.rating = 'sad' AND r.deleted_at IS NULL"
                ),
                "started_at": negative_started_iso,
                "finished_at": negative_finished,
                "count": len(negative_ids),
            },
            "review_queue": {
                "command": "review-visibility recordings snapshot (song_id, hash_prefix, lrc_source, lrc_status, youtube_url presence)",
                "started_at": review_started_iso,
                "finished_at": review_finished,
                "count": len(review_queue),
            },
        },
        "seed_subsets": {
            "source": "specs/lrc-review-triage-cascade-design-v2.md Appendix A",
            "authoritative": "seed lists stay authoritative per-song; divergences recorded here, never dropped",
            "positive": {
                "seed_count": len(seed_pos),
                "all_present": not positive_divergences,
                "per_song": positive_assertions,
            },
            "negative": {
                "seed_count": len(seed_neg),
                "all_present": not negative_divergences,
                "per_song": negative_assertions,
            },
        },
        "review_queue_summary": {
            "total": len(review_queue),
            "lrc_source_counts": lrc_source_counts,
            "with_youtube_url": with_youtube_url,
            "without_youtube_url": len(review_queue) - with_youtube_url,
        },
        "review_queue": review_queue,
        "notes": (
            "Spec measured 399 review songs on 2026-10-07; live count is "
            f"{len(review_queue)} at snapshot time. Live count is authoritative "
            "for later phases; this note records the divergence."
            if len(review_queue) != 399
            else "Review queue matches the spec's 399."
        ),
    }

    write_ids([output_dir / "positive.txt"], published_ids)
    write_ids([output_dir / "negative.txt"], negative_ids)

    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    snapshot_path = output_dir / f"snapshot-{stamp}.json"
    snapshot_path.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "latest.json").write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"positive.txt: {len(published_ids)} song ids")
    print(f"negative.txt: {len(negative_ids)} song ids")
    print(f"review queue: {len(review_queue)} recordings -> {snapshot_path.name}")
    print(
        f"seed positive assertions: {len(seed_pos)} checked, {len(positive_divergences)} divergence(s)"
    )
    print(
        f"seed negative assertions: {len(seed_neg)} checked, {len(negative_divergences)} divergence(s)"
    )
    if positive_divergences or negative_divergences:
        for entry in positive_divergences + negative_divergences:
            print(f"  divergence: {entry['song_id']}: {entry['divergence']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
