# Plan: Admin-CLI Test SQLite/Turso Cleanup & Migration

**Status:** Ready for implementation
**Scope:** `ops/admin-cli/` (tests, conftest, pyproject.toml, stale production comments)
**Goal:** Remove all SQLite/Turso test infrastructure. Migrate the 111 skipped tests to a hybrid Postgres-integration + mock-based pattern. Make the default `pytest` run green without Docker.

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

### Decisions (confirmed with user)

1. **DB-interaction tests → hybrid**: integration tests (require Docker) for
   SQL-heavy assertions; mock-based unit tests for command-output formatting
   that only inspects a fixed return value.
2. **Default `pytest` excludes integration tests** via
   `addopts = "-m not integration"` in `pyproject.toml`.
3. **Remove `aiosqlite`** from `[test]` extras.
4. **Clean up all stale Turso/SQLite references** in production code.

---

## Current state (read-only findings)

### Skipped tests (111 total)

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

### Errored tests (18 total, runtime — not collection)

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

### Collection errors (3 files)

| File | Failing import |
|---|---|
| `tests/admin/commands/test_db_commands.py:9` | `from tests.conftest import make_test_provider` |
| `tests/admin/test_client.py:8` | `from tests.conftest import make_test_provider` |
| `tests/db/test_postgres_clients.py:14` | `from tests.conftest import make_test_provider` |

Root cause: `pyproject.toml` sets `pythonpath = ["src"]` only; `tests` is not a
package on the import path. `make_test_provider` is a plain function (not a
fixture) defined at `tests/conftest.py:39`.

### Production Turso/SQLite references (5, all comments/docstrings)

| File:Line | Content |
|---|---|
| `src/.../admin/config.py:116` | `# Backward compatibility: silently ignore old [turso] section` |
| `src/.../admin/services/sync.py:4` | module docstring mentioning removed Turso sync |
| `src/.../admin/db/__init__.py:3` | `Provides SQLite database client, models, and schema definitions.` |
| `src/.../admin/commands/audio.py:1279` | `# ... because SQLite can't extract the series number` |
| `src/.../admin/README.md:274` | `# Check for running processes (if using local SQLite)` |

### Existing passing patterns (reference for migration)

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

### Key production signatures

- `get_db_client(config)` — `catalog.py:63`; returns `DatabaseClient(provider)`.
- `DatabaseClient.__init__(connection_provider: ConnectionProvider)` — `db/client.py:45`.
- `AdminConfig.get_connection_url()` — `config.py:49`; raises
  `ValueError("database_url is not configured")` when `[database].url` is absent.
- `ALL_SCHEMA_STATEMENTS` — `stream_of_worship/db/postgres_schema.py:51`; list
  of DDL statements used by integration tests to seed a fresh schema.

---

## Implementation

### Phase 1 — Pytest config & conftest (foundation)

**File: `ops/admin-cli/pyproject.toml`**

1. In `[tool.pytest.ini_options]` (lines 149–152), add:
   ```toml
   addopts = "-m 'not integration'"
   markers = [
       "integration: requires Docker (testcontainers Postgres container)",
   ]
   ```
   The `markers` declaration replaces the programmatic
   `config.addinivalue_line` in `conftest.py:pytest_configure` (see step 2).
   `addopts` ensures the default `pytest` invocation skips every
   `@pytest.mark.integration` test, so Docker is never required for a local
   run. Run the full suite with `pytest -m ""` or `pytest -m integration`.

2. In `[project.optional-dependencies].test` (line ~118), remove:
   ```toml
   "aiosqlite>=0.19.0",
   ```
   Verify no project code imports `aiosqlite` (confirmed: only the venv and
   transitive testcontainers deps reference it).

**File: `ops/admin-cli/tests/conftest.py`**

3. Convert `make_test_provider` from a plain function (imported by 3 files)
   into a pytest fixture so callers stop doing
   `from tests.conftest import make_test_provider`. Replace the function
   (lines 39–50) with:
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
   `provider = make_test_provider()` (function-scoped fixture, depends on
   `postgres_url` transitively).

4. Remove `pytest_configure`'s `addinivalue_line("markers", "integration: ...")`
   call (lines 24–26) since the marker is now declared in `pyproject.toml`.
   Keep `pytest_configure` only if other config is needed; otherwise delete
   the function.

5. Harden the `postgres_url` fixture (lines 32–52) so that when Docker is
   absent it **skips** rather than **errors**. Wrap the
   `PostgresContainer(...)` instantiation in a try/except that calls
   `pytest.skip("Docker not available; skipping integration test")` on
   `docker.errors.DockerException`. This prevents the 18 `ERROR` outcomes
   (errors count as failures; skips do not) even if an integration test is
   explicitly selected without Docker.

**Verification after Phase 1:**
```bash
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 \
  --extra admin --extra test pytest ops/admin-cli/tests -q
```
Expected: 0 errors, the 18 integration tests deselected (skipped via marker),
the 3 collection-error files now collect (but their tests are deselected as
integration). The 111 still-skipped tests remain skipped pending Phase 2–4.

### Phase 2 — Fix collection-error files

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
Expected: 0 collection errors; the 3 files now collect. Full count = 751 +
their tests (previously uncounted).

### Phase 3 — Migrate `test_audio_commands.py` (65 tests)

This is the largest file. Split the 65 tests into two cohorts by what they
assert.

**Cohort A — Error-path / config-validation tests (mock-based, stay unit)**

These assert exit code + printed message and never need a live DB. Migrate
them to the modern config format and the actual error messages the code
emits today.

9. Rewrite `_setup_db` and the obsolete `[database]\npath = "/nonexistent/db.sqlite"`
   config writes. For error-path tests that simulate a missing database,
   use a config with NO `[database]` section (or empty `url = ""`) and assert
   on `ValueError` propagation / exit code 1 — there is no longer a
   `"Database not found"` string. Update each assertion accordingly:
   - `test_download_without_config`, `test_list_without_config`,
     `test_show_without_config`, `test_analyze_without_config`,
     `test_status_without_config` — keep as-is (patch
     `get_config_path` to raise `FileNotFoundError`; assert
     `"Config file not found"`). Already correct; just remove the module
     skip.
   - `test_download_without_database`, `test_list_without_database`,
     `test_show_without_database`, `test_analyze_without_database`,
     `test_catalog_*_without_database` — write a config with an empty
     `[database]` section; assert `exit_code == 1`. The resulting
     `ValueError` from `get_connection_url()` surfaces as a non-zero exit.
     (If Typer wraps it as an unhandled exception, assert `exit_code != 0`
     and consider patching `get_db_client` to raise
     `typer.Exit(1)` if cleaner output is desired — but prefer not to
     change production behavior in this pass; see Open Question below.)

10. Remove the module-level `import sqlite3` (line 3) and the `pytestmark`
    skip (line 17).

**Cohort B — DB-interaction tests (integration)**

Tests that seed data and assert on query results, ordering, or CRUD side
effects become `@pytest.mark.integration` and use the `postgres_url` +
`make_test_provider` fixtures.

11. Replace the `_setup_db(tmp_path)` helper with a session/function-scoped
    fixture that:
    - Takes `postgres_url` and `make_test_provider`.
    - Creates a `DatabaseClient(make_test_provider())`.
    - Calls `client.initialize_schema()` (the admin client has its own
      schema init at `db/client.py:90`, which runs `ALL_SCHEMA_STATEMENTS`).
    - Seeds a `Song` via `client.insert_song(song)`.
    - Writes a real config TOML with `[database]\nurl = "{postgres_url}"\n`
      (note: testcontainers URL already has credentials inline, so
      `SOW_DATABASE_PASSWORD` is not needed; if `get_connection_url`
      mangles it, set `SOW_DATABASE_URL` env var to the testcontainers URL
      instead).
    - Yields `{config_path, song, db_client}`.
    - Tears down by dropping all tables (mirror the cleanup in
      `test_client.py:29-35`).

12. Mark each DB-interaction test class (or the whole module minus Cohort A)
    with `@pytest.mark.integration`. Concretely the classes that need the
    marker:
    - `TestAudioDownloadCommand` (the DB-touching subset: `test_download_song_not_found`,
      `test_download_existing_recording`, `test_download_dry_run_shows_metadata`,
      `test_download_success`, `test_download_youtube_failure`,
      `test_download_r2_credentials_missing`, `test_download_r2_upload_failure`)
    - `TestAudioListCommand` (all DB tests: `test_list_empty_database`,
      `test_list_all_recordings`, `test_list_with_status_filter`,
      `test_list_ids_format`, `test_list_with_limit`,
      `test_list_shows_song_titles`, `test_list_shows_album_column`,
      `test_list_invalid_sort`, `test_list_album_filter`,
      `test_list_sort_by_title`, `test_list_sort_by_imported`)
    - `TestAudioShowCommand` (DB tests: `test_show_no_recording_for_song`,
      `test_show_displays_basic_fields`, `test_show_displays_analysis_results`,
      `test_show_pending_recording_no_analysis_section`,
      `test_show_recording_without_linked_song`)
    - `TestAnalyzeCommand` (DB tests: the subset that loads a real recording)
    - `TestStatusCommand` (DB tests)
    - `TestDownloadCommandNewFeatures` (DB-touching subset)
    - `TestDeleteCommand` (all DB CRUD tests)

    Use class-level `@pytest.mark.integration` where the whole class is
    DB-bound; use method-level markers for the mixed classes.

**Cohort C — Mock-based command-output tests (no DB)**

For tests that only assert on Rich panel formatting given a known
`Recording`/`Song` (e.g. `test_show_displays_basic_fields` could be split
into a mock variant that patches `get_db_client` to return a MagicMock
yielding a canned `Recording`), convert to the pattern in
`test_audio_batch_unified.py`:

13. For any test where the assertion is purely about rendered output (strings
    in `result.output`) and the DB call is a single `get_song` /
    `get_recording_by_song_id`, prefer patching
    `stream_of_worship.admin.commands.catalog.get_db_client` (or the
    command-local `get_db_client`) to return a MagicMock configured with
    the canned return values. Keep these as plain unit tests (no
    `@pytest.mark.integration`). This minimizes Docker reliance.

    Apply judgement per test: if the test would lose meaningful coverage by
    mocking (e.g. it verifies SQL `WHERE`/`ORDER BY` behavior), keep it as
    integration. If it only checks `"Song Title:" in output`, mock it.

### Phase 4 — Migrate `test_catalog_commands.py` (22 tests)

Same hybrid approach as Phase 3.

14. Remove module skip (line 5).
15. Replace `_setup_db` with the same Postgres-backed fixture (factor the
    fixture into `tests/admin/conftest.py` — a NEW conftest under
    `tests/admin/` — so both `test_audio_commands.py` and
    `test_catalog_commands.py` share it; see Phase 6).
16. Error-path tests (without-config / without-database) → unit tests with
    modern config format.
17. DB-interaction tests (list/search/show with seeded songs) →
    `@pytest.mark.integration`.

### Phase 5 — Migrate `test_scraper.py` (24 tests)

The scraper tests are mostly about HTML parsing and ID normalization, with a
few that exercise `CatalogScraperWithDatabase`.

18. Remove module skip (line 5).
19. Pure parsing/validation tests (`TestCatalogScraperLyricsParsing`,
    `TestCatalogScraperHelpers`, `TestCatalogScraperValidation`) need NO DB —
    they should run as plain unit tests. Audit each; if any currently rely on
    the `_setup_db` helper, decouple them.
20. `TestCatalogScraper` tests that call `scraper.scrape(...)` against fixtures
    (no DB) → unit tests.
21. `TestCatalogScraperWithDatabase` (save_songs, incremental, force) →
    `@pytest.mark.integration` using the Postgres fixture + `DatabaseClient`.

### Phase 6 — Shared fixtures conftest

22. Create `ops/admin-cli/tests/admin/conftest.py` containing the shared
    Postgres-backed `setup_db` fixture extracted from Phases 3–5 (the
    function that yields `{config_path, song, db_client}`). This avoids
    duplicating the helper across `test_audio_commands.py`,
    `test_catalog_commands.py`, and `test_scraper.py`. Keep the root
    `conftest.py` for `postgres_url` and `make_test_provider` only.

### Phase 7 — Clean up `test_audio_batch_v4.py` config strings

23. Replace the ~13 occurrences of
    `'[database]\npath = "/nonexistent/db.sqlite"\n'` with
    `'[database]\nurl = "postgresql://invalid/invalid"\n'` (or a minimal
    valid-shape config). These tests only exercise argument validation and
    never connect, so the URL value is irrelevant — but the `path` key is
    obsolete and confusing. No behavioral change.

### Phase 8 — Clean up stale production comments

24. **`src/.../admin/db/__init__.py:3`** — update docstring from
    "Provides SQLite database client..." to "Provides PostgreSQL database
    client, models, and schema definitions."
25. **`src/.../admin/config.py:116`** — decide whether to keep the
    "silently ignore old [turso] section" backward-compat comment. If user
    configs no longer carry a `[turso]` section (migration is complete),
    delete the comment. If unsure, keep it but reword to
    `# Legacy: old configs may carry a [turso] section; it is ignored.`
26. **`src/.../admin/services/sync.py:4`** — update module docstring to
    remove the Turso reference; describe what the module actually does now.
27. **`src/.../admin/commands/audio.py:1279`** — reword the comment from
    "because SQLite can't extract the series number" to
    "because Postgres can't extract the series number from the title in a
    single ORDER BY" (or whatever the actual rationale is — verify the
    surrounding code).
28. **`src/.../admin/README.md:274`** — replace the "if using local SQLite"
    parenthetical with Postgres-appropriate guidance or remove it.

### Phase 9 — Clean up test docstrings mentioning SQLite

29. **`tests/admin/test_config.py:152-157`** — the `test_ignores_old_turso_section`
    test. This is a *valid* test (it verifies the backward-compat behavior in
    `config.py:116`). If Phase 8 step 25 keeps the backward-compat behavior,
    keep this test. If the behavior is removed, delete this test.
30. **`tests/db/test_model_coercion.py:45`** — update the docstring
    "legacy SQLite format" to "legacy string timestamp format" (the test
    logic is DB-agnostic; only the docstring is stale).
31. **`tests/admin/services/test_sync.py:3`** — sync the docstring with the
    production `sync.py` docstring update from step 26.

---

## Verification

After each phase, run:

```bash
# Default run (no Docker) — must be green, 0 errors, 0 unexpected skips
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
  only the intentional `test_ignores_old_turso_section` (if kept) and
  `aiosqlite`-free `pyproject.toml`.
- `aiosqlite` removed from `pyproject.toml`.

---

## Open questions / decisions to confirm before implementation

1. **`"Database not found"` error-path tests**: the current production code
   raises an unhandled `ValueError("database_url is not configured")` when
   `[database].url` is missing, which Typer surfaces as a traceback with
   `exit_code == 1`. The migrated error-path tests can assert `exit_code != 0`
   without inspecting the message. **Preferred?** Or should production be
   patched to catch the `ValueError` and print a friendly
   `"Database not configured. Run 'sow-admin db init'"` message (small
   behavior change, out of scope for a "test cleanup" pass but improves UX)?

2. **`[turso]` backward-compat in `config.py`**: keep the silent-ignore
   behavior (and its test) or remove it now that migration is complete?
   Removing it means old config files with a `[turso]` section load fine
   (unknown sections are ignored by `tomllib` anyway), so the comment+test
   are arguably dead. Recommend: **remove the comment AND the test**
   (`test_ignores_old_turso_section`), since `tomllib` ignores unknown
   sections regardless.

3. **Fixture sharing**: confirm the new `tests/admin/conftest.py` (Phase 6)
   is the right place for the shared `setup_db` fixture, vs. keeping it
   duplicated per-file. The shared approach is recommended.

---

## Out of scope

- No production behavior changes (except docstring/comment edits).
- No DB schema changes.
- No new dependencies (testcontainers is already present).
- The `lab/sow-app` and `ops/analysis-service` test suites are untouched.
