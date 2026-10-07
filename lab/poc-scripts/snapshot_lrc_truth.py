#!/usr/bin/env python3
"""Phase 0a: snapshot LRC truth lists + review queue from the production DB.

Implements Phase 0a of ``specs/lrc-review-triage-cascade-design-v2.md``:

- positive truth list: the live CLI query
  ``sow-admin audio list --visibility published --format ids`` is executed
  (via the same Typer entry point) and its stdout is written verbatim to
  ``positive.txt`` — no reimplementation of the CLI's SQL.
- negative truth list: the live CLI query
  ``sow-admin lyrics feedback list --rating poor --format ids`` likewise →
  ``negative.txt``. The CLI may emit songless-recording notices on stderr;
  those are captured and recorded in the snapshot JSON but never pollute the
  id list.
- review-queue snapshot: every non-deleted ``review`` recording with
  song_id, hash_prefix, lrc_source, lrc_status, youtube_url presence → JSON.
  The review queue is not available via ``--format ids`` with those columns,
  so this part uses a direct read-only SELECT.
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

import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.db.connection import ConnectionProvider

DEFAULT_OUTPUT_DIR = Path("eval/lrc_truth")


def run_cli_ids_query(sow_admin_bin: Path, argv: list[str]) -> tuple[list[str], str]:
    """Run the real ``sow-admin`` command as a subprocess, capture stdout.

    This is the live query exactly as the spec names it — the CLI's own SQL
    (LEFT JOIN songs, hash_prefix fallback for songless recordings,
    soft-deleted-song exclusion) applies unmodified. ``--format ids`` writes
    one id per line to stdout; the lyrics feedback command may print
    songless-recording notices to stderr.

    Returns:
        (ids, stderr_text)
    """
    cmd = [str(sow_admin_bin), *argv]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"{' '.join(argv)} exited {result.returncode}: {result.stderr.strip()}")
    ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return ids, result.stderr


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


def extract_seed_lists_from_spec(
    spec_path: Path,
) -> tuple[list[str], list[str], dict[str, str]]:
    """Parse the 46 seed positives and 8 seed negatives out of Appendix A.

    Mechanically derived from the spec so the anchor can never be corrupted by
    a transcription typo. Returns (positives, negatives, negative_lrc_sources).
    """
    text = spec_path.read_text(encoding="utf-8")
    pos_block = _appendix_block(text, "Seed positives")
    neg_block = _appendix_block(text, "Seed negatives")

    positives = [w for w in pos_block.split() if _looks_like_song_id(w)]

    negatives = []
    negative_sources: dict[str, str] = {}
    for line in neg_block.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        song_id = parts[0]
        if not _looks_like_song_id(song_id):
            continue
        negatives.append(song_id)
        for source in ("manual_upload", "youtube_transcript", "qwen3_asr", "whisper_asr"):
            if f"({source}" in line or f"— {source}" in line:
                negative_sources[song_id] = source
                break
    return positives, negatives, negative_sources


def _looks_like_song_id(token: str) -> bool:
    return len(token) > 8 and token[-8:].isalnum() and "_" in token


def _appendix_block(text: str, heading: str) -> str:
    idx = text.index(heading)
    fence_start = text.index("```", idx)
    fence_end = text.index("```", fence_start + 3)
    return text[fence_start + 3 : fence_end]


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
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for positive.txt / negative.txt / snapshot JSON "
        "(default: eval/lrc_truth)",
    )
    parser.add_argument("--config", type=Path, default=None, help="Admin config path")
    parser.add_argument(
        "--spec",
        type=Path,
        default=Path("specs/lrc-review-triage-cascade-design-v2.md"),
        help="Spec path to mechanically extract seed lists from",
    )
    args = parser.parse_args(argv)

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(UTC)
    started_at = now.isoformat(timespec="seconds")

    config_path = str(args.config) if args.config else None

    # Locate the real sow-admin binary from the same venv this script runs in
    # (sys.executable's directory), falling back to PATH.
    sow_admin = Path(sys.executable).parent / "sow-admin"
    if not sow_admin.exists():
        sow_admin = shutil.which("sow-admin")
        if not sow_admin:
            raise SystemExit("sow-admin binary not found in venv or PATH")
        sow_admin = Path(sow_admin)

    positive_argv = ["audio", "list", "--visibility", "published", "--format", "ids"]
    negative_argv = [
        "lyrics",
        "feedback",
        "list",
        "--rating",
        "poor",
        "--format",
        "ids",
    ]
    if config_path:
        positive_argv += ["--config", config_path]
        negative_argv += ["--config", config_path]

    positive_started_iso = datetime.now(UTC).isoformat(timespec="seconds")
    positive_ids, pos_stderr = run_cli_ids_query(sow_admin, positive_argv)
    positive_finished = datetime.now(UTC).isoformat(timespec="seconds")
    negative_started_iso = datetime.now(UTC).isoformat(timespec="seconds")
    negative_ids, neg_stderr = run_cli_ids_query(sow_admin, negative_argv)
    negative_finished = datetime.now(UTC).isoformat(timespec="seconds")
    config = AdminConfig.load(args.config)
    provider = ConnectionProvider(config.get_connection_url())
    conn = provider.get_connection()
    review_started_iso = datetime.now(UTC).isoformat(timespec="seconds")
    review_queue = query_review_queue(conn)
    review_finished = datetime.now(UTC).isoformat(timespec="seconds")
    provider.close()

    spec_pos, spec_neg, neg_sources = extract_seed_lists_from_spec(args.spec)

    # Seed files are written from the spec parse so the anchor can never be a
    # hand-typed transcription of Appendix A.
    write_ids([output_dir / "seed_positive.txt"], spec_pos)
    write_ids([output_dir / "seed_negative.txt"], spec_neg)

    positive_assertions = assert_seed_subset(spec_pos, set(positive_ids), "positive")
    negative_assertions = assert_seed_subset(spec_neg, set(negative_ids), "negative")
    positive_divergences = [a for a in positive_assertions if not a["in_snapshot"]]
    negative_divergences = [a for a in negative_assertions if not a["in_snapshot"]]

    lrc_source_counts: dict[str, int] = {}
    for row in review_queue:
        key = row["lrc_source"] or "null"
        lrc_source_counts[key] = lrc_source_counts.get(key, 0) + 1
    with_youtube_url = sum(1 for row in review_queue if row["has_youtube_url"])

    snapshot = {
        "schema_version": 2,
        "created_at": started_at,
        "spec": "specs/lrc-review-triage-cascade-design-v2.md",
        "phase": "0a",
        "queries": {
            "positive": {
                "command": "sow-admin audio list --visibility published --format ids",
                "argv": positive_argv,
                "invocation": "real sow-admin subprocess; stdout captured verbatim as positive.txt",
                "started_at": positive_started_iso,
                "finished_at": positive_finished,
                "count": len(positive_ids),
                "stderr_notices": pos_stderr.strip() or None,
            },
            "negative": {
                "command": "sow-admin lyrics feedback list --rating poor --format ids",
                "argv": negative_argv,
                "invocation": "real sow-admin subprocess; stdout captured verbatim as negative.txt",
                "started_at": negative_started_iso,
                "finished_at": negative_finished,
                "count": len(negative_ids),
                "stderr_notices": neg_stderr.strip() or None,
            },
            "review_queue": {
                "command": "review-visibility recordings snapshot (song_id, hash_prefix, lrc_source, lrc_status, youtube_url presence)",
                "started_at": review_started_iso,
                "finished_at": review_finished,
                "count": len(review_queue),
            },
        },
        "seed_subsets": {
            "source": f"mechanically parsed from {args.spec} Appendix A",
            "authoritative": "seed lists stay authoritative per-song; divergences recorded here, never dropped",
            "positive": {
                "seed_count": len(spec_pos),
                "all_present": not positive_divergences,
                "per_song": positive_assertions,
            },
            "negative": {
                "seed_count": len(spec_neg),
                "all_present": not negative_divergences,
                "per_song": negative_assertions,
                "lrc_source_provenance": neg_sources,
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

    write_ids([output_dir / "positive.txt"], positive_ids)
    write_ids([output_dir / "negative.txt"], negative_ids)

    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    snapshot_path = output_dir / f"snapshot-{stamp}.json"
    snapshot_path.write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "latest.json").write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"positive.txt: {len(positive_ids)} song ids")
    print(f"negative.txt: {len(negative_ids)} song ids")
    print(f"review queue: {len(review_queue)} recordings -> {snapshot_path.name}")
    print(
        f"seed positive assertions: {len(spec_pos)} checked, {len(positive_divergences)} divergence(s)"
    )
    print(
        f"seed negative assertions: {len(spec_neg)} checked, {len(negative_divergences)} divergence(s)"
    )
    if positive_divergences or negative_divergences:
        for entry in positive_divergences + negative_divergences:
            print(f"  divergence: {entry['song_id']}: {entry['divergence']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
