# Fix: `theme_anchors` Table Not Created by `sow-admin db init`

## 1. Problem Statement

Running `sow-admin theme-anchors sync` against a freshly initialized database fails with `UndefinedTable: relation "theme_anchors" does not exist`, even though `sow-admin db init` reported success immediately before.

### Error signature

```
❯ sow_admin db init
Postgres schema initialized successfully!
Schema Version │ 3

❯ sow_admin theme-anchors sync
UndefinedTable: relation "theme_anchors" does not exist
LINE 1: SELECT COUNT(*) FROM theme_anchors WHERE model_version = 'te...
```

Traceback points to `ops/admin-cli/src/stream_of_worship/admin/commands/theme_anchors.py:62`.

### Impact

- `theme-anchors sync` is unusable against any DB initialized by the current `db init`.
- Downstream, `sow-admin songset construct` cannot run because it calls `check_theme_anchors` (`songset_constructor/db.py:121`), which raises `ThemeAnchorsMissingError` when the table is absent (`commands/songset.py:470`).
- The same gap silently affects the two song embedding tables (`song_embedding`, `song_line_embedding`) — they are also never created by `db init`, so any semantic-search or embedding-write path is broken on a fresh DB.

## 2. Root Cause Analysis

There are **two** `ALL_SCHEMA_STATEMENTS` lists, and `db init` uses the wrong one:

1. **Local list** — `stream_of_worship.admin.db.schema.ALL_SCHEMA_STATEMENTS` (`ops/admin-cli/src/stream_of_worship/admin/db/schema.py:213`). This list **does** include the pgvector-backed tables:
   - `CREATE_SONG_EMBEDDING_TABLE`
   - `CREATE_SONG_LINE_EMBEDDING_TABLE`
   - `*CREATE_EMBEDDING_INDEXES`
   - `CREATE_THEME_ANCHORS_TABLE`
   - `CREATE_THEME_ANCHORS_INDEX`

2. **Unified list** — `stream_of_worship.db.postgres_schema.ALL_SCHEMA_STATEMENTS` (`ops/admin-cli/src/stream_of_worship/db/postgres_schema.py:51`). This is the list that `db init` actually executes (via `DatabaseClient.initialize_schema` at `ops/admin-cli/src/stream_of_worship/admin/db/client.py:106`). It is hand-assembled and re-exports only a **subset** of the names from `admin.db.schema` (lines 12–27):

   ```python
   from stream_of_worship.admin.db.schema import (
       ACTIVE_RECORDINGS_QUERY,
       ACTIVE_SONGS_QUERY,
       CREATE_INDEXES,
       CREATE_RECORDINGS_TABLE,
       CREATE_RECORDINGS_UPDATE_TRIGGER,
       CREATE_SONGS_TABLE,
       CREATE_SONGS_UPDATE_TRIGGER,
       CREATE_UPDATE_TIMESTAMP_FUNCTION,
       RECORDING_COLUMN_COUNT,
       RECORDING_COLUMNS_FOR_JOIN,
       ROW_COUNT_QUERY,
       SONG_COLUMN_COUNT,
       SONG_COLUMNS_FOR_JOIN,
       TABLE_STATS_QUERY,
   )
   ```

   The five pgvector constants above are **deliberately omitted** from this import and from `ALL_SCHEMA_STATEMENTS` (lines 51–68). So `db init` never creates `theme_anchors` (nor `song_embedding` / `song_line_embedding`).

`theme-anchors sync` (`commands/theme_anchors.py:62`) then blindly runs `SELECT COUNT(*) FROM theme_anchors ...` assuming the table exists, and crashes.

### Secondary gap: `pgvector` extension never enabled

Every pgvector-backed table uses `embedding vector(1536)`. The `vector` type is provided by the `pgvector` extension, which Neon ships but does **not** auto-enable per database. There is no `CREATE EXTENSION IF NOT EXISTS vector;` anywhere in the schema statements (confirmed: grep for `CREATE EXTENSION` across `ops/admin-cli` returns zero matches). So even if the table DDL were added to the unified list, the `CREATE TABLE ... vector(1536)` would fail with `type "vector" does not exist` on a fresh Neon DB until the extension is enabled.

## 3. Fix Strategy

Two layers — primary (make `db init` create the tables) and defensive (make `theme-anchors sync` self-sufficient).

### Fix A: Wire missing DDL into the unified schema list (primary)

Add the five omitted pgvector constants **plus** a new `CREATE_EXTENSION_VECTOR` constant to `postgres_schema.ALL_SCHEMA_STATEMENTS`, in dependency-safe order:

1. `CREATE_EXTENSION_VECTOR` runs **first** (before any `vector(...)` column is referenced).
2. The three table DDLs and their indexes run after `*CREATE_INDEXES`, mirroring the order already used in `admin/db/schema.py:213-225`.

### Fix B: Make `theme-anchors sync` self-sufficient (defensive)

Before the `SELECT COUNT(*)` check at `theme_anchors.py:62`, run the extension + table + index DDL (all idempotent `IF NOT EXISTS`). This guards against anyone running `theme-anchors sync` against a DB that predates the schema change without re-running `db init`, and matches the "create-if-missing" pattern the command already implies.

## 4. Implementation Steps

### Step 1: Add `CREATE_EXTENSION_VECTOR` constant

**File:** `ops/admin-cli/src/stream_of_worship/admin/db/schema.py`

Add near the existing pgvector table definitions (e.g. just before `CREATE_SONG_EMBEDDING_TABLE`, around line 128):

```python
CREATE_EXTENSION_VECTOR = "CREATE EXTENSION IF NOT EXISTS vector;"
```

Also add `CREATE_EXTENSION_VECTOR` to the local `ALL_SCHEMA_STATEMENTS` list (line 213) as the **first** entry, so the local list stays consistent with the unified list.

### Step 2: Wire missing DDL into the unified schema list

**File:** `ops/admin-cli/src/stream_of_worship/db/postgres_schema.py`

Extend the import block from `stream_of_worship.admin.db.schema` (lines 12–27) to also import:

- `CREATE_EXTENSION_VECTOR`
- `CREATE_SONG_EMBEDDING_TABLE`
- `CREATE_SONG_LINE_EMBEDDING_TABLE`
- `CREATE_EMBEDDING_INDEXES`
- `CREATE_THEME_ANCHORS_TABLE`
- `CREATE_THEME_ANCHORS_INDEX`

Update `ALL_SCHEMA_STATEMENTS` (lines 51–68) to:

```python
ALL_SCHEMA_STATEMENTS = [
    # --- 0. extensions (must precede any vector() column) ---
    CREATE_EXTENSION_VECTOR,
    # --- 1. admin / catalog ---
    CREATE_SONGS_TABLE,
    CREATE_RECORDINGS_TABLE,
    *CREATE_INDEXES,
    CREATE_SONG_EMBEDDING_TABLE,
    CREATE_SONG_LINE_EMBEDDING_TABLE,
    *CREATE_EMBEDDING_INDEXES,
    CREATE_THEME_ANCHORS_TABLE,
    CREATE_THEME_ANCHORS_INDEX,
    CREATE_UPDATE_TIMESTAMP_FUNCTION,
    CREATE_SONGS_UPDATE_TRIGGER,
    CREATE_RECORDINGS_UPDATE_TRIGGER,
    # --- 2. auth (Better Auth core) ---
    *ALL_AUTH_SCHEMA_STATEMENTS,
    # --- 3. app / songsets ---
    CREATE_SONGSETS_TABLE,
    CREATE_SONGSET_ITEMS_TABLE,
    *CREATE_APP_INDEXES,
    CREATE_SONGSETS_UPDATE_TRIGGER,
    # --- 4. per-user app tables ---
    *ALL_USER_DATA_SCHEMA_STATEMENTS,
]
```

Add the six new names to the `__all__` list (lines 70–98) for consistency.

### Step 3: Make `theme-anchors sync` self-sufficient

**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/theme_anchors.py`

Add import at top:

```python
from stream_of_worship.admin.db.schema import (
    CREATE_EXTENSION_VECTOR,
    CREATE_THEME_ANCHORS_INDEX,
    CREATE_THEME_ANCHORS_TABLE,
)
```

After acquiring `cursor` (line 59) and before the `if not force:` count check (line 62), insert:

```python
cursor.execute(CREATE_EXTENSION_VECTOR)
cursor.execute(CREATE_THEME_ANCHORS_TABLE)
cursor.execute(CREATE_THEME_ANCHORS_INDEX)
conn.commit()
```

All three statements are idempotent (`CREATE EXTENSION IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`), so re-running is safe.

### Step 4: Update integration test contract

**File:** `ops/admin-cli/tests/db/test_full_schema_init.py`

Add the three new tables to `EXPECTED_TABLES` (line 9):

```python
EXPECTED_TABLES = {
    # catalog
    "songs",
    "recordings",
    # pgvector-backed
    "song_embedding",
    "song_line_embedding",
    "theme_anchors",
    # auth (Better Auth core)
    "user",
    "account",
    "session",
    "verification",
    # app
    "songsets",
    "songset_items",
    # per-user app
    "user_settings",
    "user_lrc_override",
    "lyric_mark",
    "songset_share",
}
```

Update the cleanup `DROP TABLE` lists in both `test_all_tables_created` (line 55) and `test_critical_foreign_keys` (line 113) to also drop the new tables, and add `DROP EXTENSION IF EXISTS vector CASCADE;`:

```sql
DROP TABLE IF EXISTS songset_share, lyric_mark,
    user_lrc_override, user_settings,
    songset_items, songsets,
    theme_anchors, song_line_embedding, song_embedding,
    recordings, songs,
    "session", "account", "verification", "user" CASCADE;
DROP FUNCTION IF EXISTS update_updated_at_column CASCADE;
DROP FUNCTION IF EXISTS update_updatedat_column CASCADE;
DROP EXTENSION IF EXISTS vector CASCADE;
```

## 5. Files Changed

| File | Change |
|------|--------|
| `ops/admin-cli/src/stream_of_worship/admin/db/schema.py` | Add `CREATE_EXTENSION_VECTOR` constant; prepend it to local `ALL_SCHEMA_STATEMENTS` list |
| `ops/admin-cli/src/stream_of_worship/db/postgres_schema.py` | Import the six missing constants; insert them into unified `ALL_SCHEMA_STATEMENTS` in dependency-safe order; add to `__all__` |
| `ops/admin-cli/src/stream_of_worship/admin/commands/theme_anchors.py` | Import the three DDL constants; run them idempotently before the count check |
| `ops/admin-cli/tests/db/test_full_schema_init.py` | Add three tables to `EXPECTED_TABLES`; update cleanup DDL in both test cases |

## 6. Verification Checklist

- [ ] `sow-admin db init --force` creates `theme_anchors`, `song_embedding`, `song_line_embedding` tables
- [ ] `sow-admin theme-anchors sync` succeeds against a fresh DB (no `UndefinedTable`)
- [ ] `sow-admin theme-anchors sync` outputs "Synced 12 theme anchors to database."
- [ ] `sow-admin theme-anchors sync` (re-run without `--force`) reports "already has 12 rows"
- [ ] `CREATE EXTENSION vector` runs before any `vector(...)` column DDL (ordering verified)
- [ ] `tests/db/test_full_schema_init.py::TestFullSchemaInit::test_all_tables_created` passes with updated `EXPECTED_TABLES`
- [ ] `tests/db/test_full_schema_init.py::TestFullSchemaInit::test_critical_foreign_keys` passes with updated cleanup
- [ ] `tests/songset_construct/test_theme_anchors_sync.py` unit tests still pass
- [ ] `sow-admin songset construct` no longer fails at `check_theme_anchors` step

## 7. Test Commands

```bash
# Integration tests (require Docker for testcontainers)
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest tests/db/test_full_schema_init.py -v

# Unit tests (no Docker)
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest tests/songset_construct/test_theme_anchors_sync.py -v

# Manual smoke
sow-admin db init --force
sow-admin theme-anchors sync
sow-admin theme-anchors sync  # second run, should skip
```

## 8. Notes

- **Neon pgvector:** Neon ships pgvector but the extension must be enabled per-DB with `CREATE EXTENSION IF NOT EXISTS vector;`. The new `CREATE_EXTENSION_VECTOR` statement handles this idempotently.
- **No data migration needed:** All new tables use `CREATE ... IF NOT EXISTS`. Existing DBs that already have the tables (e.g. via manual DDL) are unaffected. Existing DBs missing the tables will get them on the next `db init` (or `db init --force`).
- **Scope:** This fix addresses all three pgvector-backed tables (`theme_anchors`, `song_embedding`, `song_line_embedding`) in a single change, since they share the same root cause and the same `vector` extension dependency.
- **No admin-cli ML dependency introduced:** The DDL constants are plain SQL strings. No PyTorch/Demucs/allin1 imports are added. The admin-cli "no heavy ML" boundary is preserved.
