#!/usr/bin/env python3
"""Load data from SQLite source into PostgreSQL target (v4 runbook).

Usage:
    python 02_load_data.py --source-db /path/to/sow_source.db --target-dsn "$DATABASE_URL"
    python 02_load_data.py --source-db /path/to/sow_source.db --target-dsn "$DATABASE_URL" --dry-run
    python 02_load_data.py --source-db /path/to/sow_source.db --validate-json

Behavior:
    - Loads songs then recordings (FK order)
    - Uses INSERT ... ON CONFLICT DO NOTHING for idempotency
    - Re-running on already-loaded target is a no-op
    - Validates JSON columns before loading (abort on invalid)
    - Reports row counts before and after
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from typing import Any

import psycopg


def validate_json_columns(conn: sqlite3.Connection) -> bool:
    """Validate all JSON-like text columns parse as valid JSON."""
    errors: list[tuple[str, str, str]] = []

    # songs json columns
    for sid, lyrics_lines, sections in conn.execute(
        "SELECT id, lyrics_lines, sections FROM songs"
    ):
        for col, val in [("lyrics_lines", lyrics_lines), ("sections", sections)]:
            if val:
                try:
                    json.loads(val)
                except json.JSONDecodeError:
                    errors.append(("songs", sid, col))

    # recordings json columns
    for ch, beats, downbeats, sections, embeddings_shape in conn.execute(
        "SELECT content_hash, beats, downbeats, sections, embeddings_shape FROM recordings"
    ):
        for col, val in [
            ("beats", beats),
            ("downbeats", downbeats),
            ("sections", sections),
            ("embeddings_shape", embeddings_shape),
        ]:
            if val:
                try:
                    json.loads(val)
                except json.JSONDecodeError:
                    errors.append(("recordings", ch, col))

    if errors:
        print("JSON validation FAILED:", file=sys.stderr)
        for table, pk, col in errors:
            print(f"  {table}:{pk}:{col}", file=sys.stderr)
        return False

    total_songs = conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    total_recordings = conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]
    print(f"JSON validation PASSED ({total_songs} songs, {total_recordings} recordings)")
    return True


def pg_upsert(cur: psycopg.Cursor, sql: str, params: tuple[Any, ...]) -> None:
    """Execute INSERT with ON CONFLICT DO NOTHING."""
    cur.execute(sql, params)


def load_songs(sqlite_conn: sqlite3.Connection, pg_conn: psycopg.Connection, dry_run: bool) -> int:
    """Load songs from SQLite into PostgreSQL."""
    cursor = sqlite_conn.execute(
        """
        SELECT
            id, title, title_pinyin, composer, lyricist,
            album_name, album_series, musical_key, lyrics_raw,
            lyrics_lines, sections, source_url, table_row_number,
            scraped_at, created_at, updated_at, deleted_at
        FROM songs
        ORDER BY id
        """
    )

    insert_sql = """
        INSERT INTO songs (
            id, title, title_pinyin, composer, lyricist,
            album_name, album_series, musical_key, lyrics_raw,
            lyrics_lines, sections, source_url, table_row_number,
            scraped_at, created_at, updated_at, deleted_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
    """

    count = 0
    pg_cursor = pg_conn.cursor()
    for row in cursor:
        if not dry_run:
            pg_upsert(pg_cursor, insert_sql, row)
            count += 1
            if count % 100 == 0:
                pg_conn.commit()
                print(f"  songs: {count} loaded...")
        else:
            count += 1

    if not dry_run:
        pg_conn.commit()
    return count


def load_recordings(
    sqlite_conn: sqlite3.Connection, pg_conn: psycopg.Connection, dry_run: bool
) -> int:
    """Load recordings from SQLite into PostgreSQL."""
    cursor = sqlite_conn.execute(
        """
        SELECT
            content_hash, hash_prefix, song_id, original_filename,
            file_size_bytes, imported_at, r2_audio_url, r2_stems_url,
            r2_lrc_url, duration_seconds, tempo_bpm, musical_key,
            musical_mode, key_confidence, loudness_db, beats,
            downbeats, sections, embeddings_shape, analysis_status,
            analysis_job_id, lrc_status, lrc_job_id,
            created_at, updated_at, youtube_url, visibility_status,
            deleted_at, download_status
        FROM recordings
        ORDER BY content_hash
        """
    )

    insert_sql = """
        INSERT INTO recordings (
            content_hash, hash_prefix, song_id, original_filename,
            file_size_bytes, imported_at, r2_audio_url, r2_stems_url,
            r2_lrc_url, duration_seconds, tempo_bpm, musical_key,
            musical_mode, key_confidence, loudness_db, beats,
            downbeats, sections, embeddings_shape, analysis_status,
            analysis_job_id, lrc_status, lrc_job_id,
            created_at, updated_at, youtube_url, visibility_status,
            deleted_at, download_status
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (hash_prefix) DO NOTHING
    """

    count = 0
    pg_cursor = pg_conn.cursor()
    for row in cursor:
        if not dry_run:
            pg_upsert(pg_cursor, insert_sql, row)
            count += 1
            if count % 50 == 0:
                pg_conn.commit()
                print(f"  recordings: {count} loaded...")
        else:
            count += 1

    if not dry_run:
        pg_conn.commit()
    return count


def get_counts(pg_conn: psycopg.Connection) -> dict[str, int]:
    """Get row counts from PostgreSQL."""
    cur = pg_conn.cursor()
    cur.execute(
        """
        SELECT 'songs', COUNT(*) FROM songs
        UNION ALL SELECT 'recordings', COUNT(*) FROM recordings
        """
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Load SQLite data into PostgreSQL")
    parser.add_argument("--source-db", required=True, help="Path to SQLite source database")
    parser.add_argument("--target-dsn", default="", help="PostgreSQL connection string")
    parser.add_argument("--dry-run", action="store_true", help="Count rows but do not write")
    parser.add_argument("--validate-json", action="store_true", help="Run JSON validation only")
    args = parser.parse_args()

    print(f"Source: {args.source_db}")
    print(f"Target: {'(dry-run)' if args.dry_run else args.target_dsn}")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print()

    # Connect to SQLite
    sqlite_conn = sqlite3.connect(args.source_db)
    sqlite_conn.execute("PRAGMA foreign_keys = ON")

    # JSON validation
    if args.validate_json:
        ok = validate_json_columns(sqlite_conn)
        sqlite_conn.close()
        return 0 if ok else 1

    # Always run JSON validation before load
    print("Running JSON validation...")
    if not validate_json_columns(sqlite_conn):
        sqlite_conn.close()
        return 1

    if args.dry_run:
        print("\nDry-run mode: counting rows that would be loaded")
        songs_count = sqlite_conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
        recordings_count = sqlite_conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]
        print(f"  songs: {songs_count}")
        print(f"  recordings: {recordings_count}")
        sqlite_conn.close()
        return 0

    if not args.target_dsn:
        print("ERROR: --target-dsn is required (unless --dry-run or --validate-json)", file=sys.stderr)
        return 1

    # Connect to PostgreSQL
    pg_conn = psycopg.connect(args.target_dsn, connect_timeout=10)

    # Source counts
    src_songs = sqlite_conn.execute("SELECT COUNT(*) FROM songs").fetchone()[0]
    src_recordings = sqlite_conn.execute("SELECT COUNT(*) FROM recordings").fetchone()[0]
    print(f"\nSource counts: {src_songs} songs, {src_recordings} recordings")

    # Target counts before
    before = get_counts(pg_conn)
    print(f"Target counts before: {before.get('songs', 0)} songs, {before.get('recordings', 0)} recordings")

    # Load data
    print("\nLoading songs...")
    songs_loaded = load_songs(sqlite_conn, pg_conn, dry_run=False)
    print(f"Loaded {songs_loaded} songs")

    print("\nLoading recordings...")
    recordings_loaded = load_recordings(sqlite_conn, pg_conn, dry_run=False)
    print(f"Loaded {recordings_loaded} recordings")

    # Target counts after
    after = get_counts(pg_conn)
    print(f"\nTarget counts after: {after.get('songs', 0)} songs, {after.get('recordings', 0)} recordings")

    # Verify
    print("\nVerification:")
    ok = True
    if after.get("songs", 0) != src_songs:
        print(f"  MISMATCH: songs target={after.get('songs',0)} != source={src_songs}")
        ok = False
    else:
        print(f"  PASS: songs count matches")

    if after.get("recordings", 0) != src_recordings:
        print(f"  MISMATCH: recordings target={after.get('recordings',0)} != source={src_recordings}")
        ok = False
    else:
        print(f"  PASS: recordings count matches")

    sqlite_conn.close()
    pg_conn.close()

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
