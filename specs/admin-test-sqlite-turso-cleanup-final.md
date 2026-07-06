# Plan: Admin-CLI Test SQLite/Turso Cleanup & Migration (Final)

**Status:** Ready for implementation
**Scope:** `ops/admin-cli/` (tests, conftest, pyproject.toml, stale production comments)
**Goal:** Remove all SQLite/Turso test infrastructure. Migrate the 111 skipped tests to a hybrid Postgres-integration + mock-based pattern. Make the default `pytest` run green without Docker.

---

## Resolved decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Error-path test assertions | Assert `exit_code != 0` only; no production change |
| 2 | `[turso]` backward-compat comment + test | Remove both (tomllib ignores unknown sections) |
| 3 | Shared `setup_db` fixture location | `tests/admin/conftest.py` (new file) |
| 4 | Integration marker style | Class-level when all methods DB-bound; method-level otherwise |
| 5 | Config file for testcontainers | Write `[database].url = "<testcontainers_url>"` inline in TOML |
| 6 | Audio test migration sequencing | All 65 in one PR |
| 7 | Catalog + scraper sequencing | Combined into one PR |
| 8 | `postgres_url` Docker-absent behavior | Try/except `docker.errors.DockerException` → `pytest.skip` |
| 9 | `aiosqlite` removal | Remove from `[test]` extras + `uv lock` |
| 10 | `audio.py:1279` stale comment | Investigate surrounding code; reword contextually (or delete if rationale no longer applies) |

---

## Context

The admin CLI migrated its production DB layer from SQLite/Turso to PostgreSQL
(`ConnectionProvider` + `psycopg`). The test suite was not fully migrated:

- **111 tests** across 3 files carry `pytestmark = pytest.mark.skip(reason="pre-migration
  SQLite/Turso test; not compatible with Postgres")`.
- **18 tests** in `tests/db/` error at runtime because the session-scoped
  `postgres_url` fixture starts a `PostgresContainer` and Docker is not running
  locally. These tests ARE already Postgres-compatible; they just need to be
  excluded from the default run.
- **3 files** fail at collection because they do
  `from tests.conftest import make_test_provider` and `tests` is not on
  `pythonpath` (only `src` is).
- **`aiosqlite>=0.19.0`** is still listed under the `[test]` extra but is
  unused by any project code.
- **5 stale Turso/SQLite comments/docstrings** remain in production code.

### Current state (read-only findings)

#### Skipped tests (111 total)

All share the identical module-level marker in 3 files:

| File | Tests | Marker line |
|---|---|---|
| `tests/admin/test_audio_commands.py` | 65 | `:17` |
| `tests/admin/test_catalog_commands.py` | 22 | `:5` |
| `tests/admin/test_scraper.py` | 24 | `:5` |

These tests were written against the old SQLite `DatabaseClient(db_path)`
constructor (now removed — `DatabaseClient.__init__` takes a
`ConnectionProvider`). They use a private `_setup_db(tmp_path)` helper that
creates a temp `.db` file via `DatabaseClient(db_path).initialize_schema()` and
writes configs in the obsolete format `[database]\npath = "..."` (config now
reads `[database].url`). Many also assert on `"Database not found"` — a message
that no longer exists in the codebase (Postgres has no file-not-found concept;
an unconfigured `database_url` raises `ValueError("database_url is not
configured")` from `AdminConfig.get_connection_url()`).

#### Errored tests (18 total, runtime — not collection)

All originate from the session-scoped `postgres_url` fixture at
`tests/conftest.py:32`:

```python
with PostgresContainer("postgres:16-alpine") as postgres:
```

`pytest.importorskip("testcontainers", ...)` passes (the package is installed)
but Docker is not running, so `docker.from_env()` raises
`DockerException`. Affected files (all already marked
`@pytest.mark.integration` at class level):

- `tests/db/test_full_schema_init.py` (2 tests, marker `:29`)
- `tests/db/test_role_permissions.py` (7 tests, marker `:75`)
- `tests/db/test_user_client.py` (9 tests, marker `:42`)

#### Collection errors (3 files)

| File | Failing import |
|---|---|
| `tests/admin/commands/test_db_commands.py:9` | `from tests.conftest import make_test_provider` |
| `tests/admin/test_client.py:8` | `from tests.conftest import make_test_provider` |
| `tests/db/test_postgres_clients.py:14` | `from tests.conftest import make_test_provider` |

Root cause: `pyproject.toml` sets `pythonpath = ["src"]` only; `tests` is not a
package on the import path. `make_test_provider` is a plain function (not a
fixture) defined at `tests/conftest.py:39`.

#### Production Turso/SQLite references (5, all comments/docstrings)

| File:Line | Content |
|---|---|
| `src/.../admin/config.py:116` | `# Backward compatibility: silently ignore old [turso] section` |
| `src/.../admin/services/sync.py:4` | module docstring mentioning removed Turso sync |
| `src/.../admin/db/__init__.py:3` | `Provides SQLite database client, models, and schema definitions.` |
| `src/.../admin/commands/audio.py:1279` | `# ... because SQLite can't extract the series number` |
| `src/.../admin/README.md:274` | `# Check for running processes (if using local SQLite)` |

#### Existing passing patterns (reference for migration)

- **Mock-based unit tests** (no DB): `tests/admin/test_audio_batch_unified.py`,
  `tests/admin/test_audio_batch_v4.py`, `tests/admin/test_audio_lrc_visibility.py`.
  These build `Song`/`Recording` dataclass instances and `MagicMock` the
  `DatabaseClient`. `test_audio_batch_v4.py` writes `[database]\npath = ...`
  config strings but only to satisfy the config loader for argument-validation
  tests that exit before any DB access — these need their config strings
  updated to the `[database].url` form but are otherwise fine.
- **Postgres integration tests** (require Docker): `tests/admin/test_client.py`,
  `tests/db/test_postgres_clients.py`, `tests/db/test_user_client.py`. These use
  the `postgres_url` fixture + `make_test_provider(postgres_url)` and are marked
  `@pytest.mark.integration`.

#### Key production signatures

- `get_db_client(config)` — `catalog.py:63`; returns `DatabaseClient(provider)`.
- `DatabaseClient.__init__(connection_provider: ConnectionProvider)` — `db/client.py:45`.
- `AdminConfig.get_connection_url()` — `config.py:49`; raises
  `ValueError("database_url is not configured")` when `[database].url` is absent.
- `ALL_SCHEMA_STATEMENTS` — `stream_of_worship/db/postgres_schema.py:51`; list
  of DDL statements used by integration tests to seed a fresh schema.

---

## Implementation phases

### Phase 1 — Pytest config & conftest foundation

**Commit A: `pyproject.toml` + `conftest.py` rewrite**

1. `pyproject.toml` `[tool.pytest.ini_options]` (lines 149–152) — add:
   ```toml
   addopts = "-m 'not integration'"
   markers = ["integration: requires Docker (testcontainers Postgres container)"]
   ```
   The `markers` declaration replaces the programmatic
   `config.addinivalue_line` in `conftest.py:pytest_configure`. `addopts`
   ensures the default `pytest` invocation skips every
   `@pytest.mark.integration` test, so Docker is never required for a local
   run. Run the full suite with `pytest -m ""` or `pytest -m integration`.

2. `pyproject.toml` `[project.optional-dependencies].test` (line ~118) — remove:
   ```toml
   "aiosqlite>=0.19.0",
   ```
   Run `uv lock` to update the lockfile. No project code imports `aiosqlite`
   (confirmed: only the venv and transitive testcontainers deps reference it).

3. `tests/conftest.py` — convert `make_test_provider` (lines 39–50) from a
   plain function into a function-scoped pytest fixture so callers stop doing
   `from tests.conftest import make_test_provider`:
   ```python
   @pytest.fixture
   def make_test_provider(postgres_url):
       """Return a factory that builds a ConnectionProvider for the test DB."""
       from stream_of_worship.db.connection import ConnectionProvider
       def _make():
           return ConnectionProvider(postgres_url, sslmode="disable")
       return _make
   ```
   Callers change from `provider = make_test_provider(postgres_url)` to
   `provider = make_test_provider()`.

4. `tests/conftest.py` — harden `postgres_url` (lines 32–52): wrap the
   `PostgresContainer("postgres:16-alpine")` instantiation in a try/except
   that calls `pytest.skip("Docker not available; skipping integration test")`
   on `docker.errors.DockerException`. This prevents the 18 `ERROR` outcomes
   (errors count as failures; skips do not) even if an integration test is
   explicitly selected without Docker.

5. `tests/conftest.py` — remove `pytest_configure`'s
   `addinivalue_line("markers", "integration: ...")` call (lines 24–26) since
   the marker is now declared in `pyproject.toml`. Keep the `src_dir` path
   injection at the top of the file.

**Verification after Phase 1:**
```bash
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 \
  --extra admin --extra test pytest ops/admin-cli/tests -q
```
Expected: 0 errors, the 18 integration tests deselected (skipped via marker),
the 3 collection-error files now collect (but their tests are deselected as
integration). The 111 still-skipped tests remain skipped pending Phases 4–5.

### Phase 2 — Fix collection-error files (3 files)

Apply the `make_test_provider` fixture migration to the 3 files that
previously failed to import. They are already marked `@pytest.mark.integration`,
so after Phase 1 they will be deselected by default but must at least
**collect** cleanly.

6. **`tests/admin/commands/test_db_commands.py`** — delete line 9
   (`from tests.conftest import make_test_provider`); change call sites
   (lines 23, 36) from `make_test_provider(postgres_url)` to
   `make_test_provider()` and add `make_test_provider` to the test function
   signature.

7. **`tests/admin/test_client.py`** — same edit: delete line 8, change
   lines 14 and 27, add fixture param to the `admin_client` fixture and any
   test that calls it directly.

8. **`tests/db/test_postgres_clients.py`** — delete line 14, change lines 27
   and 46, add fixture param.

**Verification after Phase 2:**
```bash
PYTHONPATH=... pytest ops/admin-cli/tests --collect-only -q
```
Expected: 0 collection errors; the 3 files now collect.

### Phase 3 — Shared `setup_db` fixture conftest

9. Create `ops/admin-cli/tests/admin/conftest.py` containing a Postgres-backed
   `setup_db` fixture that:
   - Takes `postgres_url` and `make_test_provider`.
   - Creates a `DatabaseClient(make_test_provider())`.
   - Calls `client.initialize_schema()` (the admin client has its own schema
     init at `db/client.py:90`, which runs `ALL_SCHEMA_STATEMENTS`).
   - Seeds one `Song` via `client.insert_song(song)`.
   - Writes config TOML with `[database]\nurl = "{postgres_url}"\n` (note:
     testcontainers URLs already have credentials inline, so
     `SOW_DATABASE_PASSWORD` is not needed).
   - Yields `{"config_path", "song", "db_client"}`.
   - Tears down by dropping all tables (mirror the cleanup in
     `test_client.py:29-35`).

   This avoids duplicating the helper across `test_audio_commands.py`,
   `test_catalog_commands.py`, and `test_scraper.py`. Keep the root
   `conftest.py` for `postgres_url` and `make_test_provider` only.

### Phase 4 — Migrate `test_audio_commands.py` (all 65 tests, one PR)

**4a. Remove SQLite infrastructure**
- Delete `import sqlite3` (line 3) and the `pytestmark` skip (line 17).
- Delete the old `_setup_db(tmp_path)` helper (lines 23–43).

**4b. Error-path tests (unit tests, no marker)**

For tests that assert config/validation errors (`test_*_without_config`,
`test_*_without_database`):
- `test_*_without_config`: keep as-is (patch `get_config_path` to raise
  `FileNotFoundError`; assert `exit_code == 1` + `"Config file not found"`).
  Already correct; just remove the module skip.
- `test_*_without_database`: write a config with an empty `[database]` section
  (no `url`); assert `exit_code != 0`. Do NOT assert on a "Database not found"
  message — production raises `ValueError("database_url is not configured")`,
  surfaced as a traceback with `exit_code == 1`. Asserting on exit code only
  avoids coupling to the exact error message.

**4c. DB-interaction tests (`@pytest.mark.integration`)**

For each class that's entirely DB-bound, apply class-level
`@pytest.mark.integration`:
- `TestAudioListCommand` — all 12 methods touch seeded DB.
- `TestDeleteCommand` — all CRUD tests (likely class-level).
- `TestDownloadCommandNewFeatures` — audit per method; mark class if all DB,
  else mark methods.

For mixed classes, apply method-level `@pytest.mark.integration` to
DB-touching tests:
- `TestAudioDownloadCommand` — mark `test_download_song_not_found`,
  `test_download_existing_recording`, `test_download_dry_run_shows_metadata`,
  `test_download_success`, `test_download_youtube_failure`,
  `test_download_r2_credentials_missing`, `test_download_r2_upload_failure`.
- `TestAudioShowCommand` — mark `test_show_no_recording_for_song`,
  `test_show_displays_basic_fields`, `test_show_displays_analysis_results`,
  `test_show_pending_recording_no_analysis_section`,
  `test_show_recording_without_linked_song`.
- `TestAnalyzeCommand` — mark the subset that loads a real recording.
- `TestStatusCommand` — mark DB tests.

All DB-interaction tests use the shared `setup_db` fixture from
`tests/admin/conftest.py`.

**4d. Mock-based command-output tests (no marker)**

For tests where the assertion is purely about rendered output given a known
`Recording`/`Song`:
- Patch `stream_of_worship.admin.commands.catalog.get_db_client` (or the
  command-local `get_db_client`) to return a `MagicMock` configured with
  canned return values.
- Follow the pattern in `test_audio_batch_unified.py`.
- Apply judgement per test: if a test would lose meaningful coverage by
  mocking (e.g. it verifies SQL `WHERE`/`ORDER BY` behavior), keep it as
  integration. If it only checks `"Song Title:" in output`, mock it.

### Phase 5 — Migrate `test_catalog_commands.py` + `test_scraper.py` (combined, one PR)

**5a. `test_catalog_commands.py` (22 tests)**

10. Remove module skip (line 5).
11. Error-path tests (`test_*_without_config`, `test_*_without_database`) →
    unit tests with modern config format (`[database].url`).
12. DB-interaction tests (`test_list_*`, `test_search_*`, `test_show_*`) →
    `@pytest.mark.integration` using shared `setup_db`. Class-level marker
    where all methods DB-bound; method-level for mixed classes.

**5b. `test_scraper.py` (24 tests)**

13. Remove module skip (line 5).
14. Pure parsing/validation tests (`TestCatalogScraperLyricsParsing`,
    `TestCatalogScraperHelpers`, `TestCatalogScraperValidation`) → plain unit
    tests, no DB, no marker. Audit each; if any currently rely on the
    `_setup_db` helper, decouple them.
15. `TestCatalogScraper` tests that call `scraper.scrape(...)` against fixtures
    (no DB) → unit tests.
16. `TestCatalogScraperWithDatabase` (save_songs, incremental, force) →
    `@pytest.mark.integration` using the Postgres fixture + `DatabaseClient`.

### Phase 6 — Clean up `test_audio_batch_v4.py` config strings

17. Replace ~13 occurrences of
    `'[database]\npath = "/nonexistent/db.sqlite"\n'` with
    `'[database]\nurl = "postgresql://invalid/invalid"\n'`. These tests only
    exercise argument validation and never connect, so the URL value is
    irrelevant — but the `path` key is obsolete and confusing. No behavioral
    change.

### Phase 7 — Clean up stale production comments

18. **`src/.../admin/db/__init__.py:3`** — update docstring from
    "Provides SQLite database client..." to "Provides PostgreSQL database
    client, models, and schema definitions."
19. **`src/.../admin/config.py:116`** — delete the `[turso]` backward-compat
    comment (decision #2: remove both comment and test). `tomllib` ignores
    unknown sections regardless, so the comment is dead.
20. **`src/.../admin/services/sync.py:4`** — update module docstring to remove
    the Turso reference; describe what the module actually does now.
21. **`src/.../admin/commands/audio.py:1279`** — read ~10 lines of
    surrounding context; if the re-sort rationale still applies to Postgres,
    reword the comment to describe the actual Postgres rationale. If it was
    SQLite-specific and no longer applies, delete it.
22. **`src/.../admin/README.md:274`** — replace the "if using local SQLite"
    parenthetical with Postgres-appropriate guidance or remove it.

### Phase 8 — Clean up test docstrings mentioning SQLite

23. **`tests/admin/test_config.py:152-168`** — delete
    `test_ignores_old_turso_section` (decision #2: remove the test). The
    backward-compat behavior it verifies is a no-op (tomllib ignores unknown
    sections).
24. **`tests/db/test_model_coercion.py:45`** — update the docstring
    "legacy SQLite format" to "legacy string timestamp format" (the test
    logic is DB-agnostic; only the docstring is stale).
25. **`tests/admin/services/test_sync.py:3`** — sync the docstring with the
    production `sync.py` docstring update from step 20.

---

## Verification

After each phase, run:

```bash
# Default run (no Docker) — must be green, 0 errors, 0 SQLite skips
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 \
  --extra admin --extra test pytest ops/admin-cli/tests -q

# Full run (with Docker) — integration tests execute
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 \
  --extra admin --extra test pytest ops/admin-cli/tests -q -m ""

# Collection check (no import errors)
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 \
  --extra admin --extra test pytest ops/admin-cli/tests --collect-only -q
```

**Acceptance criteria:**

- Default `pytest` (no Docker): **0 passed-skipped-as-SQLite, 0 errors**.
  Integration tests are deselected via `-m "not integration"`. The only
  skips should be `pytest.importorskip` guards for optional deps.
- `pytest -m ""` (with Docker): the migrated integration tests pass against
  the testcontainers Postgres.
- `pytest --collect-only`: 0 collection errors across the whole tree.
- `grep -ri "sqlite\|turso" ops/admin-cli/src ops/admin-cli/tests` returns
  only intentional references (none, after cleanup).
- `aiosqlite` absent from `pyproject.toml` and `uv.lock`.

---

## Commit structure

| Commit | Content | Phase |
|---|---|---|
| 1 | pyproject.toml + conftest.py + aiosqlite removal + 3 collection-error fixes | 1, 2 |
| 2 | tests/admin/conftest.py shared setup_db fixture | 3 |
| 3 | test_audio_commands.py full migration (65 tests) + test_audio_batch_v4.py config cleanup | 4, 6 |
| 4 | test_catalog_commands.py + test_scraper.py migration (46 tests) | 5 |
| 5 | Stale comment/docstring cleanup (production + tests) | 7, 8 |

---

## Out of scope

- No production behavior changes (except docstring/comment edits + the
  `[turso]` comment removal).
- No DB schema changes.
- No new dependencies (testcontainers is already present).
- The `lab/sow-app` and `ops/analysis-service` test suites are untouched.
