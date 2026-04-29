# Turso Bootstrap Batch Seeding Instructions

## Purpose

This document provides step-by-step instructions for manually seeding data to Turso when the standard `turso-bootstrap --seed` command times out or fails due to large data transfers.

## Background

The standard `sow-admin db turso-bootstrap --seed` command may timeout when seeding large datasets (600+ songs) because it attempts to insert all records in a single batch without intermediate syncs. This causes the libsql embedded replica to hang during the network sync to Turso.

## Prerequisites

1. Valid Turso database URL configured in `~/.config/sow-admin/config.json`
2. `SOW_TURSO_TOKEN` environment variable set (read-write token)
3. Source SQLite database with data to migrate (e.g., backup from `sow.db.bak-YYYYMMDDTHHMMSS/`)

## Quick Steps

### Step 1: Verify Environment

```bash
# Check Turso token is set
source ~/.zshrc  # or wherever SOW_TURSO_TOKEN is defined
echo "Token set: ${SOW_TURSO_TOKEN:+YES}"

# Check Turso remote is accessible
turso db shell <database-name> "SELECT COUNT(*) FROM songs;"
```

### Step 2: Identify Source Database

After a failed bootstrap attempt, look for the backup directory:

```bash
ls -la ~/.config/sow-admin/db/
```

Look for: `sow.db.bak-YYYYMMDDTHHMMSS/` directories

Verify the source has data:

```bash
sqlite3 ~/.config/sow-admin/db/sow.db.bak-<TIMESTAMP>/sow.db "SELECT COUNT(*) FROM songs;"
```

### Step 3: Run Batched Seeding Script

Create and run this Python script:

```python
#!/usr/bin/env python3
"""Batched Turso seeding script."""

import os
import sys
import libsql
import sqlite3
from pathlib import Path

sys.path.insert(0, "src")
from stream_of_worship.admin.config import ensure_config_exists

config = ensure_config_exists()
turso_token = os.environ.get("SOW_TURSO_TOKEN")

if not turso_token:
    print("Error: SOW_TURSO_TOKEN not set")
    sys.exit(1)

# UPDATE THIS PATH to your backup directory
backup_dir = Path("~/.config/sow-admin/db/sow.db.bak-<TIMESTAMP>").expanduser()
source_db = backup_dir / "sow.db"

print(f"Source DB: {source_db}")
print(f"Turso URL: {config.turso_database_url}")

conn = libsql.connect(
    str(config.db_path),
    sync_url=config.turso_database_url,
    auth_token=turso_token,
)
cursor = conn.cursor()

local_conn = sqlite3.connect(source_db)
local_conn.row_factory = sqlite3.Row
local_cursor = local_conn.cursor()

try:
    # Check current state
    cursor.execute("SELECT COUNT(*) FROM songs")
    current_songs = cursor.fetchone()[0]
    print(f"Current songs in Turso: {current_songs}")

    # Seed songs in batches
    local_cursor.execute("SELECT * FROM songs")
    songs = local_cursor.fetchall()
    print(f"Source songs: {len(songs)}")

    if current_songs < len(songs):
        print(f"Seeding {len(songs) - current_songs} remaining songs...")
        columns = ", ".join(songs[0].keys())
        placeholders = ", ".join(["?" for _ in songs[0].keys()])
        sql = f"INSERT OR REPLACE INTO songs ({columns}) VALUES ({placeholders})"

        batch_size = 25  # Small batches for network stability
        for i in range(0, len(songs), batch_size):
            batch = songs[i:i + batch_size]
            cursor.executemany(sql, [tuple(song) for song in batch])
            conn.commit()
            conn.sync()  # Critical: sync after each batch
            print(f"  Synced {min(i + batch_size, len(songs))}/{len(songs)}")

    # Seed recordings
    local_cursor.execute("SELECT * FROM recordings")
    recordings = local_cursor.fetchall()
    if recordings:
        print(f"Seeding {len(recordings)} recordings...")
        columns = ", ".join(recordings[0].keys())
        placeholders = ", ".join(["?" for _ in recordings[0].keys()])
        sql = f"INSERT OR REPLACE INTO recordings ({columns}) VALUES ({placeholders})"

        batch_size = 25
        for i in range(0, len(recordings), batch_size):
            batch = recordings[i:i + batch_size]
            cursor.executemany(sql, [tuple(r) for r in batch])
            conn.commit()
            conn.sync()
            print(f"  Synced {min(i + batch_size, len(recordings))}/{len(recordings)}")

    # Seed sync_metadata
    local_cursor.execute("SELECT * FROM sync_metadata")
    metadata = local_cursor.fetchall()
    if metadata:
        print(f"Seeding {len(metadata)} sync_metadata entries...")
        columns = ", ".join(metadata[0].keys())
        placeholders = ", ".join(["?" for _ in metadata[0].keys()])
        sql = f"INSERT OR REPLACE INTO sync_metadata ({columns}) VALUES ({placeholders})"
        cursor.executemany(sql, [tuple(m) for m in metadata])
        conn.commit()
        conn.sync()

    print("\n✅ Seeding completed!")

    # Verify
    cursor.execute("SELECT COUNT(*) FROM songs")
    final_songs = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM recordings")
    final_recordings = cursor.fetchone()[0]
    print(f"Final: {final_songs} songs, {final_recordings} recordings")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
finally:
    local_conn.close()
    conn.close()
```

Run with:

```bash
source ~/.zshrc
PYTHONPATH=src uv run --extra admin python seed_script.py
```

### Step 4: Verify Migration

```bash
# Check Turso remote
turso db shell <database-name> "SELECT COUNT(*) FROM songs;"
turso db shell <database-name> "SELECT COUNT(*) FROM recordings;"

# Test sync
PYTHONPATH=src uv run --extra admin python -m stream_of_worship.admin.main db sync
```

## Key Implementation Details

### Why Batching is Necessary

The libsql library's `executemany()` with large datasets (>500 rows) can hang during the `conn.sync()` call because:

1. All data accumulates in WAL (Write-Ahead Log) before sync
2. Network timeout during large HTTP POST to Turso
3. Transaction remains open, blocking subsequent operations

### Optimal Batch Size

- **Songs**: 25 records per batch (each song has large text fields for lyrics)
- **Recordings**: 25 records per batch
- **Sync metadata**: Can be done in single batch (small table)

### Resuming Partial Migrations

If migration is interrupted, the script can be safely re-run. It will:

1. Check current count in Turso
2. Compare with source
3. Only seed missing records

To resume with existing data in Turso, modify the script to:

1. Query existing IDs: `SELECT id FROM songs`
2. Skip already-seeded records by ID
3. Continue from where it left off

## Troubleshooting

### Error: "database disk image is malformed"

The embedded replica is corrupted. Clean up and retry:

```bash
rm -f ~/.config/sow-admin/db/sow.db ~/.config/sow-admin/db/sow.db-*
# Restore from backup and re-run seeding
cp ~/.config/sow-admin/db/sow_backup.db ~/.config/sow-admin/db/sow.db
```

### Error: "Sync failed" or timeout

Reduce batch size to 10 and try again. If still failing:

1. Check network connection
2. Verify Turso token permissions (needs write access)
3. Check Turso database isn't locked

### Error: "No such table"

The schema wasn't created. Run bootstrap without seed first:

```bash
PYTHONPATH=src uv run --extra admin python -m stream_of_worship.admin.main db turso-bootstrap --force
```

Then manually seed using the batched script above.

## Alternative: Direct SQLite Dump

For extremely large datasets, use Turso CLI's import feature:

```bash
# Dump from source
sqlite3 ~/.config/sow-admin/db/sow.db.bak-<TIMESTAMP>/sow.db ".dump songs" > /tmp/songs_dump.sql

# Import to Turso (requires turso CLI with db shell support)
turso db shell <database-name> < /tmp/songs_dump.sql
```

Note: This requires handling schema differences manually and doesn't support embedded replica sync metadata.

## References

- Original spec: `specs/turso_bootstrap_fixes_v2_opus.md`
- Source code: `src/stream_of_worship/admin/commands/db.py`
- Related migration: `reports/turso_v2_implementation_summary.md`
