  Phase 5: CLI ↔ Service Integration — Handover Document

  Date: 2026-02-06
  Status: ~90% Complete
  Last Commit: (pending final test commit)

  ---
  What's Completed

  1. AnalysisClient Service (src/stream_of_worship/admin/services/analysis.py) ✅

  Status: Complete with 28 passing tests

  - AnalysisServiceError exception class with status_code support
  - AnalysisResult dataclass with all analysis fields
  - JobInfo dataclass with status, progress, stage, result tracking
  - AnalysisClient class with methods:
    - __init__() — validates SOW_ANALYSIS_API_KEY env var
    - health_check() — GET /api/v1/health
    - submit_analysis() — POST /api/v1/jobs/analyze with Bearer auth
    - get_job() — GET /api/v1/jobs/{job_id} with Bearer auth
    - wait_for_completion() — polling with callback support and timeout
    - _parse_job_response() — static method for JSON parsing

  2. Services Export (src/stream_of_worship/admin/services/__init__.py) ✅

  - Added AnalysisClient import and __all__ export

  3. Database Client Update (src/stream_of_worship/admin/db/client.py) ✅

  - Added r2_stems_url: Optional[str] = None parameter to update_recording_analysis()
  - Uses COALESCE for backward compatibility with existing records

  4. Audio Commands (src/stream_of_worship/admin/commands/audio.py) ✅

  New imports added:
  import json
  from stream_of_worship.admin.services.analysis import (
      AnalysisClient, AnalysisServiceError, JobInfo,
  )

  New helper functions:
  - _format_duration(seconds) → MM:SS format
  - _colorize_status(status) → Rich color markup

  audio analyze command:
  - Resolves identifier as song_id or hash_prefix
  - Validates r2_audio_url exists
  - Handles already-completed (with/without --force)
  - Handles already-processing (with/without --wait)
  - Rich progress display in wait mode (spinner, bar, stage)
  - Stores results to DB on completion

  audio status command:
  - Mode A (with job_id): Query service, display Rich Panel
  - Mode B (no args): List pending recordings in Rich Table

  5. AnalysisClient Tests (tests/admin/test_analysis_client.py) ✅

  28 tests, all passing:
  - TestAnalysisClientInit (4 tests)
  - TestHealthCheck (2 tests)
  - TestSubmitAnalysis (5 tests)
  - TestGetJob (5 tests)
  - TestWaitForCompletion (5 tests)
  - TestParseJobResponse (3 tests)
  - TestAnalysisResult (2 tests)
  - TestJobInfo (2 tests)

  Import added to test_audio_commands.py:
  from stream_of_worship.admin.services.analysis import AnalysisServiceError, JobInfo

  ---
  What's In Progress

  1. Audio Commands Tests (tests/admin/test_audio_commands.py) ⏳

  Status: Imports added, but test classes not yet appended

  Needs to be added:

  TestAnalyzeCommand (~16 tests)

  1. test_analyze_without_config — Fails when no config file exists
  2. test_analyze_without_database — Fails when database path doesn't exist
  3. test_analyze_recording_not_found_by_hash — Error for nonexistent hash prefix
  4. test_analyze_recording_not_found_by_song_id — Error when song has no recording
  5. test_analyze_no_r2_audio_url — Error when recording lacks audio URL
  6. test_analyze_already_completed_no_force — Exit 0 with message when already done
  7. test_analyze_already_completed_with_force — Re-submits with --force
  8. test_analyze_already_processing_no_wait — Exit 0 with existing job info
  9. test_analyze_already_processing_with_wait — Polls existing job
  10. test_analyze_missing_api_key — Error when SOW_ANALYSIS_API_KEY not set
  11. test_analyze_service_unavailable — Error when service unreachable
  12. test_analyze_fire_and_forget_success — Submits, updates DB to "processing"
  13. test_analyze_by_hash_prefix — Resolves by hash prefix
  14. test_analyze_by_song_id — Resolves by song_id
  15. test_analyze_wait_mode_completed — Polls, stores results to DB
  16. test_analyze_wait_mode_failed — Updates DB to "failed" on failure
  17. test_analyze_wait_mode_timeout — Error on poll timeout
  18. test_analyze_no_stems_flag — Passes generate_stems=False

  TestStatusCommand (~8 tests)

  1. test_status_without_config — Fails when no config file exists
  2. test_status_without_database — Fails when database path doesn't exist
  3. test_status_with_job_id_success — Displays job in Rich Panel
  4. test_status_with_job_id_not_found — Error 404 handling
  5. test_status_with_job_id_missing_api_key — Error 401 handling
  6. test_status_no_args_all_completed — "All recordings processed" message
  7. test_status_no_args_pending — Shows pending recordings table
  8. test_status_empty_database — Empty DB handling

  ---
  Deviations from Implementation Plan

  Minor Deviations

  1. Test counts slightly adjusted — Plan specified ~44 new tests total; implementation has 28 (analysis_client) + ~24
  (planned for audio_commands) = ~52 tests for better coverage.
  2. Additional error handling — Added explicit handling for 401 Unauthorized in analyze command with specific error message.
  3. Status command enhancements — Added color-coded status display using _colorize_status() helper for better UX.

  Implementation Matches Plan

  - All data classes match specification exactly
  - API endpoints match plan (POST /api/v1/jobs/analyze, GET /api/v1/jobs/{job_id})
  - Error handling patterns match specification
  - Rich progress display matches plan (spinner + bar + stage)
  - Database updates follow exact specification

  ---
  Remaining Todos

  - Append TestAnalyzeCommand class to tests/admin/test_audio_commands.py
  - Append TestStatusCommand class to tests/admin/test_audio_commands.py
  - Run all tests: PYTHONPATH=src uv run --extra admin --extra test pytest tests/admin/ -v
  - Run new tests specifically: PYTHONPATH=src uv run --extra admin --extra test pytest tests/admin/test_audio_commands.py -v
   -k "analyze or status"
  - Smoke test CLI help: PYTHONPATH=src uv run --extra admin python -m stream_of_worship.admin.main audio analyze --help
  - Update MEMORY.md with completion commit hash
  - Commit changes with message: "Phase 5: CLI ↔ Service Integration complete"

  ---
  Test Commands

  # Run all admin tests
  PYTHONPATH=src uv run --extra admin --extra test pytest tests/admin/ -v

  # Run just the new analysis client tests
  PYTHONPATH=src uv run --extra admin --extra test pytest tests/admin/test_analysis_client.py -v

  # Run just the new command tests (once added)
  PYTHONPATH=src uv run --extra admin --extra test pytest tests/admin/test_audio_commands.py -v -k "analyze or status"

  # Smoke test CLI
  PYTHONPATH=src uv run --extra admin python -m stream_of_worship.admin.main audio analyze --help
  PYTHONPATH=src uv run --extra admin python -m stream_of_worship.admin.main audio status --help

  ---
  Key Files Summary
  ┌──────────────────────────────────────────────────┬──────────┬───────────────────────────────────────┐
  │                       File                       │  Status  │             Lines Changed             │
  ├──────────────────────────────────────────────────┼──────────┼───────────────────────────────────────┤
  │ src/stream_of_worship/admin/services/analysis.py │ Created  │ ~300 lines                            │
  ├──────────────────────────────────────────────────┼──────────┼───────────────────────────────────────┤
  │ src/stream_of_worship/admin/services/__init__.py │ Modified │ +2 lines                              │
  ├──────────────────────────────────────────────────┼──────────┼───────────────────────────────────────┤
  │ src/stream_of_worship/admin/db/client.py         │ Modified │ +2 lines (parameter)                  │
  ├──────────────────────────────────────────────────┼──────────┼───────────────────────────────────────┤
  │ src/stream_of_worship/admin/commands/audio.py    │ Modified │ +300 lines (analyze, status commands) │
  ├──────────────────────────────────────────────────┼──────────┼───────────────────────────────────────┤
  │ tests/admin/test_analysis_client.py              │ Created  │ ~480 lines (28 tests)                 │
  ├──────────────────────────────────────────────────┼──────────┼───────────────────────────────────────┤
  │ tests/admin/test_audio_commands.py               │ Modified │ Imports added, tests pending          │
  └──────────────────────────────────────────────────┴──────────┴───────────────────────────────────────┘
  ---
  Expected Final Test Counts
  ┌────────────────────────────┬──────────┬─────────────────────────┐
  │         Component          │  Tests   │         Status          │
  ├────────────────────────────┼──────────┼─────────────────────────┤
  │ Phase 1 (Foundation)       │ ~40      │ ✅ Passing              │
  ├────────────────────────────┼──────────┼─────────────────────────┤
  │ Phase 2 (Catalog)          │ 44       │ ✅ Passing              │
  ├────────────────────────────┼──────────┼─────────────────────────┤
  │ Phase 3 (Audio Download)   │ 57       │ ✅ Passing              │
  ├────────────────────────────┼──────────┼─────────────────────────┤
  │ Phase 4 (Analysis Service) │ 54       │ ✅ Passing              │
  ├────────────────────────────┼──────────┼─────────────────────────┤
  │ Phase 5 (CLI Integration)  │ 28 + ~24 │ 28 passing, ~24 pending │
  ├────────────────────────────┼──────────┼─────────────────────────┤
  │ Total                      │ ~247     │ ~90% complete           │
  └────────────────────────────┴──────────┴─────────────────────────┘