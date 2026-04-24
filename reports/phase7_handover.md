 Phase 7 implements bidirectional Turso cloud sync for the local SQLite database using embedded replicas. The implementation
   is functionally complete but missing test coverage for the new SyncService and updated db commands.

  ---
  Completed

  1. pyproject.toml — Turso Dependency

  - Added new optional dependency group turso with libsql>=0.1.0
  - Located under [project.optional-dependencies]

  2. Database Client (src/stream_of_worship/admin/db/client.py)

  New/Modified:
  - SyncError exception class with optional cause attribute
  - Optional libsql import with LIBSQL_AVAILABLE flag
  - DatabaseClient.__init__() now accepts turso_url and turso_token parameters
  - is_turso_enabled property — returns True only when both URL configured AND libsql available
  - connection property — conditional backend (libsql when enabled, sqlite3 otherwise)
  - sync() method — calls libsql sync() and updates last_sync_at metadata
  - update_sync_metadata() method — generic metadata key/value storage
  - get_stats() — enhanced to return sync metadata (sync_version, local_device_id, last_sync_at, turso_configured)

  Key Pattern:
  if self.is_turso_enabled:
      self._connection = libsql.connect(
          str(self.db_path),
          sync_url=self.turso_url,
          auth_token=self.turso_token or "",
      )
  else:
      self._connection = sqlite3.connect(...)

  3. Data Models (src/stream_of_worship/admin/db/models.py)

  Extended DatabaseStats dataclass with:
  - last_sync_at: Optional[str] — ISO timestamp of last sync
  - sync_version: str — schema version (default "1")
  - local_device_id: str — unique device identifier
  - turso_configured: bool — whether Turso is enabled

  4. Sync Service (src/stream_of_worship/admin/services/sync.py) — NEW FILE

  Created new service module with:

  Dataclasses:
  - SyncStatus — current sync configuration and state
  - SyncResult — result of sync operation

  Exceptions:
  - SyncConfigError — configuration validation failures
  - SyncNetworkError — network/sync operation failures (with status_code)

  Class SyncService:
  - get_sync_status() — returns current sync status including libsql availability
  - validate_config() — checks prerequisites (libsql installed, db exists, URL format, token present)
  - execute_sync() — performs actual sync via DatabaseClient
  - _ensure_device_id() — retrieves device ID from database
  - _mask_url() — masks sensitive URL parts for display

  Helper:
  - get_sync_service_from_config(config) — factory function

  5. Database Commands (src/stream_of_worship/admin/commands/db.py)

  Modified get_db_client():
  - Now passes turso_url and turso_token to DatabaseClient

  Enhanced show_status command:
  - Added sync configuration table displaying:
    - Sync enabled/disabled status
    - libsql availability
    - Device ID (when configured)
    - Sync version
    - Turso URL (masked)

  New db sync command:
  - Validates configuration before attempting sync
  - Shows current sync status (last sync time, device ID)
  - Calls sync_service.execute_sync()
  - Handles errors: SyncConfigError, SyncNetworkError, SyncError
  - --force flag to bypass validation errors

  6. Tests (tests/admin/test_client.py)

  Added TestSyncFeatures class with 8 tests:
  - test_is_turso_enabled_without_config — Turso disabled when no URL
  - test_is_turso_enabled_with_config — Turso detection with config
  - test_sync_raises_error_when_not_configured — proper error on sync()
  - test_get_stats_without_sync_metadata — stats when metadata empty
  - test_get_stats_with_turso_disabled — stats with Turso explicitly disabled
  - test_update_sync_metadata — basic metadata insertion
  - test_update_sync_metadata_overwrites_existing — upsert behavior
  - test_turso_connection_mocked — mocked libsql connection

  ---
  In Progress

  None — core implementation is complete.

  ---
  TODOs (Remaining Work)

  1. Create tests/admin/services/test_sync.py (~20 tests)

  Test coverage needed for SyncService:

  SyncStatus dataclass:
  - Test field initialization
  - Test default values

  SyncResult dataclass:
  - Test success case
  - Test failure case with error message

  SyncConfigError:
  - Test exception inheritance
  - Test message propagation

  SyncNetworkError:
  - Test with status_code
  - Test without status_code

  SyncService.get_sync_status():
  - Test when libsql not installed
  - Test when database doesn't exist
  - Test when Turso fully configured
  - Test metadata retrieval from database

  SyncService.validate_config():
  - Test all valid configuration
  - Test libsql not installed
  - Test database not found
  - Test missing Turso URL
  - Test invalid URL format
  - Test missing token

  SyncService.execute_sync():
  - Test successful sync
  - Test config validation failure
  - Test sync operation failure (SyncError)
  - Test connection cleanup in finally block

  SyncService._mask_url():
  - Test libsql URL masking
  - Test empty URL
  - Test non-libsql URL

  get_sync_service_from_config():
  - Test factory creates correct instance

  2. Create tests/admin/commands/test_db_commands.py (~17 tests)

  Test coverage needed for db command updates:

  get_db_client():
  - Test creates client with correct Turso config from env

  show_status command:
  - Test status when config not found
  - Test status when database doesn't exist
  - Test status display with sync disabled
  - Test status display with sync enabled (mocked)

  sync command:
  - Test sync when config not found
  - Test sync when Turso not configured
  - Test sync when libsql not installed
  - Test sync with validation errors (no force)
  - Test sync with validation errors (with force)
  - Test successful sync
  - Test SyncConfigError handling
  - Test SyncNetworkError handling
  - Test generic Exception handling
  - Test sync status display before/after

  3. Verification

  Run full test suite:
  PYTHONPATH=src uv run --extra admin --extra test pytest tests/admin/ -v

  Expected: ~340 total tests (295 existing + ~45 new)

  ---
  Deviation from Implementation Plan
  ┌──────────────────────────────────┬─────────────┬────────────────────────────────┐
  │             Planned              │   Actual    │             Notes              │
  ├──────────────────────────────────┼─────────────┼────────────────────────────────┤
  │ libsql>=0.1.0 dependency         │ Same        │ No change                      │
  ├──────────────────────────────────┼─────────────┼────────────────────────────────┤
  │ 8 tests in test_client.py        │ 8 tests     │ Matches plan                   │
  ├──────────────────────────────────┼─────────────┼────────────────────────────────┤
  │ ~20 tests in test_sync.py        │ NOT CREATED │ Tool issues prevented creation │
  ├──────────────────────────────────┼─────────────┼────────────────────────────────┤
  │ ~17 tests in test_db_commands.py │ NOT CREATED │ Needs creation                 │
  └──────────────────────────────────┴─────────────┴────────────────────────────────┘
  Total test gap: ~37 tests remain unwritten.

  ---
  Gotchas & Important Notes

  1. libsql is Optional

  The implementation maintains zero breaking changes. If libsql is not installed:
  - LIBSQL_AVAILABLE = False
  - is_turso_enabled returns False
  - Falls back to standard sqlite3
  - All existing functionality works unchanged

  2. Turso Token from Environment

  The Turso auth token is read from SOW_TURSO_TOKEN environment variable, NOT from config file. This is intentional for
  security (tokens shouldn't be committed).

  3. Device ID Generation

  Device ID is generated automatically on first stats retrieval when Turso is enabled:
  if not local_device_id and self.is_turso_enabled:
      local_device_id = str(uuid.uuid4())[:8]
      self.update_sync_metadata("local_device_id", local_device_id)

  4. URL Masking

  Turso URLs are masked in status display to avoid leaking sensitive tokens:
  - libsql://database.turso.io?authToken=secret → libsql://database.turso.io

  5. Connection Management

  DatabaseClient now has explicit close() method. SyncService uses try/finally to ensure connections are closed after sync
  operations.

  6. Foreign Keys

  Foreign keys are only enabled for sqlite3 connections, not libsql. This matches sqlite3 default behavior.

  7. Schema Compatibility

  The sync_metadata table was already created in Phase 1 schema. No migrations needed.

  8. Test Mocking Pattern

  When mocking libsql in tests, patch at:
  @patch("stream_of_worship.admin.db.client.LIBSQL_AVAILABLE", True)
  @patch("stream_of_worship.admin.db.client.libsql")

  ---
  Configuration Reference

  Config File (TOML)

  [database]
  path = "/path/to/sow.db"

  [turso]
  database_url = "libsql://your-db.turso.io"

  Environment Variables

  export SOW_TURSO_TOKEN="your-turso-auth-token"

  Installation

  # For Turso support
  uv add --extra turso libsql

  # Or add to pyproject.toml dependencies

  ---
  Command Reference

  # Check status (includes sync configuration)
  sow-admin db status

  # Sync with Turso
  sow-admin db sync

  # Force sync despite validation warnings
  sow-admin db sync --force

  # Show database path
  sow-admin db path

  ---
  Files Modified/Created

  1. ✅ pyproject.toml — Added turso dependency group
  2. ✅ src/stream_of_worship/admin/db/client.py — Conditional backend, sync support
  3. ✅ src/stream_of_worship/admin/db/models.py — Extended DatabaseStats
  4. ✅ src/stream_of_worship/admin/services/sync.py — NEW: SyncService
  5. ✅ src/stream_of_worship/admin/commands/db.py — Enhanced status, new sync command
  6. ✅ tests/admin/test_client.py — Added sync feature tests
  7. ❌ tests/admin/services/test_sync.py — NOT CREATED (needs ~20 tests)
  8. ❌ tests/admin/commands/test_db_commands.py — NOT CREATED (needs ~17 tests)

  ---
  Next Steps to Complete

  1. Create test file: tests/admin/services/test_sync.py using the test list in TODOs section
  2. Create test file: tests/admin/commands/test_db_commands.py using the test list in TODOs section
  3. Run tests: PYTHONPATH=src uv run --extra admin --extra test pytest tests/admin/ -v
  4. Fix any failures — likely mocking issues or edge cases
  5. Update MEMORY.md with Phase 7 completion commit hash
  6. Update report/current_impl_status.md with Phase 7 status

  ---
  Architecture Decisions

  Why libsql over turso-client?

  libsql provides a drop-in sqlite3-compatible API with sync built-in. This allows minimal code changes — just swap
  sqlite3.connect() for libsql.connect() when configured.

  Why separate SyncService?

  Separation of concerns: DatabaseClient handles database operations, SyncService handles Turso-specific orchestration
  (validation, status display, error handling).

  Why env var for token?

  Security best practice — auth tokens shouldn't be stored in config files that may be committed.

  ---
  Troubleshooting

  Issue: ImportError: No module named 'libsql'
  Fix: uv add --extra turso libsql or pip install libsql

  Issue: Sync fails with "Turso sync is not configured"
  Fix: Check that turso.database_url is set in config AND SOW_TURSO_TOKEN env var is set

  Issue: Tests fail with libsql mocking errors
  Fix: Ensure patches are at correct path: stream_of_worship.admin.db.client.libsql