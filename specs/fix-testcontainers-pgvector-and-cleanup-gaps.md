# Fix: Testcontainers Image Lacks pgvector + Incomplete Test Cleanup

## 1. Problem Statement

The predecessor fix (`fix-theme-anchors-table-missing-from-db-init.md`) added `CREATE_EXTENSION_VECTOR` ("CREATE EXTENSION IF NOT EXISTS vector;") and three pgvector-backed table DDLs (`song_embedding`, `song_line_embedding`, `theme_anchors`) to `ALL_SCHEMA_STATEMENTS`. This correctly fixes production `db init`, but breaks **every integration test** that runs `ALL_SCHEMA_STATEMENTS` against a testcontainers Postgres instance.

### Error signature

```
psycopg.errors.UndefinedFile: extension "vector" is not available
CONTEXT:  SQL function "vector" inlined
```

The trace points to the first `CREATE TABLE ... vector(1536)` statement executed after `CREATE_EXTENSION_VECTOR` silently fails (testcontainers default image `postgres:16-alpine` does not ship pgvector).

### Scope of breakage

Eight integration test files + one shared conftest fixture execute `ALL_SCHEMA_STATEMENTS` against the session-scoped testcontainers container:

1. `tests/db/test_full_schema_init.py` — updated by predecessor spec, but uses a non-pgvector image
2. `tests/db/test_user_client.py` — cleanup missing new tables
3. `tests/db/test_postgres_clients.py` — cleanup missing new tables
4. `tests/db/test_role_permissions.py` — no cleanup block (relies on DROP OWNED BY)
5. `tests/admin/conftest.py` (`setup_db` fixture) — cleanup missing new tables
6. `tests/admin/commands/test_db_commands.py` — cleanup missing new tables, auth tables, extension
7. `tests/admin/test_audio_commands.py` (`_drop_all_tables`) — cleanup missing new tables
8. `tests/admin/test_client.py` — cleanup missing new tables, auth tables, extension

All eight will fail because the Postgres container image does not include the pgvector extension.

## 2. Root Cause Analysis

### Gap A: Wrong testcontainers image

`tests/conftest.py:30` creates a container from `postgres:16-alpine`:

```python
container = PostgresContainer("postgres:16-alpine")
```

The official `postgres:16-alpine` image does not include pgvector. When `CREATE_EXTENSION_VECTOR` runs, Postgres reports `extension "vector" is not available` and all subsequent `vector(...)` column DDL fails.

The `pgvector/pgvector:pg16` Docker image (maintained by the pgvector project) bundles pgvector compiled against PostgreSQL 16, making `CREATE EXTENSION vector` available without any additional build step.

### Gap B: Incomplete cleanup in test fixtures

Although `song_embedding` and `song_line_embedding` FK into `songs(id) ON DELETE CASCADE` (so they are dropped when `songs` is dropped), `theme_anchors` has **no foreign keys** — it is a standalone lookup table. If a test inserts theme anchor rows (e.g. `theme-anchors sync` unit tests) and a subsequent test in the same session-scoped container re-initializes the schema with `CREATE TABLE IF NOT EXISTS`, the leftover rows from the prior test persist undetected.

Most cleanup blocks wrap in `except Exception: pass`, so they won't cause hard failures, but they leave orphaned `theme_anchors` rows that can cause false positives/negatives in later tests.

Six test files have cleanup blocks that need updating (see section 4 below).

## 3. Fix Strategy

Two layers.

### Fix A: Switch testcontainers image to pgvector-enabled image

Replace `postgres:16-alpine` with `pgvector/pgvector:pg16` in the session-scoped `postgres_url` fixture. This image is a drop-in replacement — it exposes the same pg binary and same connection mechanics, it simply has pgvector compiled in.

### Fix B: Update all incomplete cleanup blocks

Add `theme_anchors`, `song_line_embedding`, `song_embedding`, and `DROP EXTENSION IF EXISTS vector CASCADE;` to every cleanup block. For the two test files that also drop auth tables (`test_db_commands.py`, `test_client.py`), also add the missing `"session"`, `"account"`, `"verification"`, `"user"` drops and `user_settings`, `user_lrc_override`, `lyric_mark`, `songset_share` drops for consistency.

## 4. Implementation Steps

### Step 1: Switch testcontainers image

**File:** `ops/admin-cli/tests/conftest.py`

Change line 30:

```python
# Before:
container = PostgresContainer("postgres:16-alpine")
# After:
container = PostgresContainer("pgvector/pgvector:pg16")
```

### Step 2: Update test_user_client.py cleanup

**File:** `ops/admin-cli/tests/db/test_user_client.py`

Update the cleanup block at line 28:

```python
            cur.execute(
                """
                DROP TABLE IF EXISTS songset_share, lyric_mark,
                    user_lrc_override, user_settings,
                    songset_items, songsets,
                    theme_anchors, song_line_embedding, song_embedding,
                    recordings, songs,
                    "session", "account", "verification", "user" CASCADE;
                DROP FUNCTION IF EXISTS update_updated_at_column CASCADE;
                DROP FUNCTION IF EXISTS update_updatedat_column CASCADE;
                DROP EXTENSION IF EXISTS vector CASCADE;
                """
            )
```

### Step 3: Update test_postgres_clients.py cleanup

**File:** `ops/admin-cli/tests/db/test_postgres_clients.py`

Update the cleanup block starting at line 47 — add `theme_anchors`, `song_line_embedding`, `song_embedding` to the DROP TABLE list and add a final `DROP EXTENSION IF EXISTS vector CASCADE;`:

```python
            cur.execute("""
                DROP TABLE IF EXISTS songset_share CASCADE;
                DROP TABLE IF EXISTS lyric_mark CASCADE;
                DROP TABLE IF EXISTS user_lrc_override CASCADE;
                DROP TABLE IF EXISTS user_settings CASCADE;
                DROP TABLE IF EXISTS songset_items CASCADE;
                DROP TABLE IF EXISTS songsets CASCADE;
                DROP TABLE IF EXISTS theme_anchors CASCADE;
                DROP TABLE IF EXISTS song_line_embedding CASCADE;
                DROP TABLE IF EXISTS song_embedding CASCADE;
                DROP TABLE IF EXISTS recordings CASCADE;
                DROP TABLE IF EXISTS songs CASCADE;
                DROP TABLE IF EXISTS "session" CASCADE;
                DROP TABLE IF EXISTS "account" CASCADE;
                DROP TABLE IF EXISTS "verification" CASCADE;
                DROP TABLE IF EXISTS "user" CASCADE;
                DROP FUNCTION IF EXISTS update_updated_at_column CASCADE;
                DROP FUNCTION IF EXISTS update_updatedat_column CASCADE;
                DROP EXTENSION IF EXISTS vector CASCADE;
            """)
```

### Step 4: Update tests/admin/conftest.py cleanup

**File:** `ops/admin-cli/tests/admin/conftest.py`

Update the `setup_db` teardown at line 52 — add `theme_anchors`, `song_line_embedding`, `song_embedding` to the DROP TABLE list and add `DROP EXTENSION IF EXISTS vector CASCADE;`:

```python
            cur.execute("""
                DROP TABLE IF EXISTS songset_share CASCADE;
                DROP TABLE IF EXISTS lyric_mark CASCADE;
                DROP TABLE IF EXISTS user_lrc_override CASCADE;
                DROP TABLE IF EXISTS user_settings CASCADE;
                DROP TABLE IF EXISTS songset_items CASCADE;
                DROP TABLE IF EXISTS songsets CASCADE;
                DROP TABLE IF EXISTS theme_anchors CASCADE;
                DROP TABLE IF EXISTS song_line_embedding CASCADE;
                DROP TABLE IF EXISTS song_embedding CASCADE;
                DROP TABLE IF EXISTS recordings CASCADE;
                DROP TABLE IF EXISTS songs CASCADE;
                DROP TABLE IF EXISTS "session" CASCADE;
                DROP TABLE IF EXISTS "account" CASCADE;
                DROP TABLE IF EXISTS "verification" CASCADE;
                DROP TABLE IF EXISTS "user" CASCADE;
                DROP FUNCTION IF EXISTS update_updated_at_column CASCADE;
                DROP FUNCTION IF EXISTS update_updatedat_column CASCADE;
                DROP EXTENSION IF EXISTS vector CASCADE;
            """)
```

### Step 5: Update test_db_commands.py cleanup

**File:** `ops/admin-cli/tests/admin/commands/test_db_commands.py`

The cleanup at line 37 only drops `songset_items`, `songsets`, `recordings`, `songs`. Replace with the full list:

```python
            cur.execute("""
                DROP TABLE IF EXISTS songset_share CASCADE;
                DROP TABLE IF EXISTS lyric_mark CASCADE;
                DROP TABLE IF EXISTS user_lrc_override CASCADE;
                DROP TABLE IF EXISTS user_settings CASCADE;
                DROP TABLE IF EXISTS songset_items CASCADE;
                DROP TABLE IF EXISTS songsets CASCADE;
                DROP TABLE IF EXISTS theme_anchors CASCADE;
                DROP TABLE IF EXISTS song_line_embedding CASCADE;
                DROP TABLE IF EXISTS song_embedding CASCADE;
                DROP TABLE IF EXISTS recordings CASCADE;
                DROP TABLE IF EXISTS songs CASCADE;
                DROP TABLE IF EXISTS "session" CASCADE;
                DROP TABLE IF EXISTS "account" CASCADE;
                DROP TABLE IF EXISTS "verification" CASCADE;
                DROP TABLE IF EXISTS "user" CASCADE;
                DROP FUNCTION IF EXISTS update_updated_at_column CASCADE;
                DROP FUNCTION IF EXISTS update_updatedat_column CASCADE;
                DROP EXTENSION IF EXISTS vector CASCADE;
            """)
```

### Step 6: Update test_audio_commands.py _drop_all_tables

**File:** `ops/admin-cli/tests/admin/test_audio_commands.py`

Update `_drop_all_tables` at line 35 — add `theme_anchors`, `song_line_embedding`, `song_embedding` and the extension drop:

```python
    try:
        cleanup_provider = make_test_provider()
        with cleanup_provider.get_connection().cursor() as cur:
            cur.execute("""
                DROP TABLE IF EXISTS songset_share CASCADE;
                DROP TABLE IF EXISTS lyric_mark CASCADE;
                DROP TABLE IF EXISTS user_lrc_override CASCADE;
                DROP TABLE IF EXISTS user_settings CASCADE;
                DROP TABLE IF EXISTS songset_items CASCADE;
                DROP TABLE IF EXISTS songsets CASCADE;
                DROP TABLE IF EXISTS theme_anchors CASCADE;
                DROP TABLE IF EXISTS song_line_embedding CASCADE;
                DROP TABLE IF EXISTS song_embedding CASCADE;
                DROP TABLE IF EXISTS recordings CASCADE;
                DROP TABLE IF EXISTS songs CASCADE;
                DROP TABLE IF EXISTS "session" CASCADE;
                DROP TABLE IF EXISTS "account" CASCADE;
                DROP TABLE IF EXISTS "verification" CASCADE;
                DROP TABLE IF EXISTS "user" CASCADE;
                DROP FUNCTION IF EXISTS update_updated_at_column CASCADE;
                DROP FUNCTION IF EXISTS update_updatedat_column CASCADE;
                DROP EXTENSION IF EXISTS vector CASCADE;
            """)
        cleanup_provider.close()
    except Exception:
        pass
```

### Step 7: Update test_client.py cleanup

**File:** `ops/admin-cli/tests/admin/test_client.py`

The cleanup at line 28 only drops `songset_items`, `songsets`, `recordings`, `songs`. Replace with the full list (same as Step 5):

```python
            cur.execute("""
                DROP TABLE IF EXISTS songset_share CASCADE;
                DROP TABLE IF EXISTS lyric_mark CASCADE;
                DROP TABLE IF EXISTS user_lrc_override CASCADE;
                DROP TABLE IF EXISTS user_settings CASCADE;
                DROP TABLE IF EXISTS songset_items CASCADE;
                DROP TABLE IF EXISTS songsets CASCADE;
                DROP TABLE IF EXISTS theme_anchors CASCADE;
                DROP TABLE IF EXISTS song_line_embedding CASCADE;
                DROP TABLE IF EXISTS song_embedding CASCADE;
                DROP TABLE IF EXISTS recordings CASCADE;
                DROP TABLE IF EXISTS songs CASCADE;
                DROP TABLE IF EXISTS "session" CASCADE;
                DROP TABLE IF EXISTS "account" CASCADE;
                DROP TABLE IF EXISTS "verification" CASCADE;
                DROP TABLE IF EXISTS "user" CASCADE;
                DROP FUNCTION IF EXISTS update_updated_at_column CASCADE;
                DROP FUNCTION IF EXISTS update_updatedat_column CASCADE;
                DROP EXTENSION IF EXISTS vector CASCADE;
            """)
```

## 5. Files Changed

| File | Change |
|------|--------|
| `ops/admin-cli/tests/conftest.py` | Switch container image from `postgres:16-alpine` to `pgvector/pgvector:pg16` |
| `ops/admin-cli/tests/db/test_user_client.py` | Add `theme_anchors`, `song_line_embedding`, `song_embedding`, `DROP EXTENSION vector` to cleanup |
| `ops/admin-cli/tests/db/test_postgres_clients.py` | Add `theme_anchors`, `song_line_embedding`, `song_embedding`, `DROP EXTENSION vector` to cleanup |
| `ops/admin-cli/tests/admin/conftest.py` | Add `theme_anchors`, `song_line_embedding`, `song_embedding`, `DROP EXTENSION vector` to setup_db teardown |
| `ops/admin-cli/tests/admin/commands/test_db_commands.py` | Replace partial cleanup with full drop list including auth tables + extension |
| `ops/admin-cli/tests/admin/test_audio_commands.py` | Add `theme_anchors`, `song_line_embedding`, `song_embedding`, `DROP EXTENSION vector` to `_drop_all_tables` |
| `ops/admin-cli/tests/admin/test_client.py` | Replace partial cleanup with full drop list including auth tables + extension |

## 6. Verification Checklist

- [ ] `pgvector/pgvector:pg16` image pulls and starts successfully via testcontainers
- [ ] `CREATE EXTENSION IF NOT EXISTS vector` succeeds in the test container
- [ ] `CREATE TABLE ... vector(1536)` DDL succeeds in the test container
- [ ] `tests/db/test_full_schema_init.py::test_all_tables_created` passes
- [ ] `tests/db/test_full_schema_init.py::test_critical_foreign_keys` passes
- [ ] `tests/db/test_user_client.py` passes
- [ ] `tests/db/test_postgres_clients.py` passes
- [ ] `tests/db/test_role_permissions.py` passes
- [ ] `tests/admin/commands/test_db_commands.py` passes
- [ ] `tests/admin/test_audio_commands.py` passes
- [ ] `tests/admin/test_client.py` passes
- [ ] `tests/admin/conftest.py::setup_db` fixture does not leave orphaned `theme_anchors` rows
- [ ] No "extension vector is not available" errors in any integration test
- [ ] No "relation song_embedding does not exist" errors during cleanup

## 7. Test Commands

```bash
# Core integration tests (require Docker for testcontainers)
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest tests/db/test_full_schema_init.py -v

# All other integration tests that use ALL_SCHEMA_STATEMENTS
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest tests/db/test_user_client.py -v
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest tests/db/test_postgres_clients.py -v
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest tests/db/test_role_permissions.py -v
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest tests/admin/commands/test_db_commands.py -v
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest tests/admin/test_audio_commands.py -v
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest tests/admin/test_client.py -v

# Unit tests (unaffected, should still pass)
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest tests/songset_construct/test_theme_anchors_sync.py -v

# Full integration suite
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest -v
```

## 8. Notes

- **Image size:** `pgvector/pgvector:pg16` is approximately 150 MB, slightly larger than `postgres:16-alpine` (~80 MB). The first pull takes a few seconds longer but subsequent runs use the Docker layer cache.
- **Drop-in replacement:** `pgvector/pgvector:pg16` is built FROM `postgres:16` and adds only the pgvector extension package. All testcontainers connection mechanics, environment variables, and authentication work identically.
- **`DROP EXTENSION` ordering:** The `DROP EXTENSION IF EXISTS vector CASCADE` is placed last in each cleanup block. Because `song_embedding`, `song_line_embedding`, and `theme_anchors` all use the `vector` type, dropping them first (via `DROP TABLE ... CASCADE`) removes the dependency so the extension can be dropped cleanly.
- **`test_role_permissions.py` exception:** This file uses `DROP OWNED BY sow_app_test CASCADE` followed by `DROP ROLE` — it does not have an explicit table-drop cleanup. The roles are re-created per test function (`DROP ROLE IF EXISTS` at line 35). No changes are needed here because the session-scoped container is cleaned by other tests' teardown blocks that run in the same session. However, if this file is run in isolation without other tests' cleanups, leftover `theme_anchors` could persist. Consider adding a cleanup if isolation is desired.
- **Scope:** This fix only addresses test infrastructure. No production source code changes are needed — the predecessor fix is correct for production Neon databases (which ship pgvector).
