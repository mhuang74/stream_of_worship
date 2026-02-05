│ Phase 5: CLI ↔ Service Integration — Implementation Plan                                                                  │
│                                                                                                                           │
│ Overview                                                                                                                  │
│                                                                                                                           │
│ Add audio analyze and audio status commands to the CLI that communicate with the FastAPI analysis service over HTTP.      │
│ Create a new AnalysisClient service and update the DB client.                                                             │
│                                                                                                                           │
│ Files to Create/Modify                                                                                                    │
│ ┌──────────────────────────────────────────────────┬────────┬─────────────────────────────────────────────────────┐       │
│ │                       File                       │ Action │                     Description                     │       │
│ ├──────────────────────────────────────────────────┼────────┼─────────────────────────────────────────────────────┤       │
│ │ src/stream_of_worship/admin/services/analysis.py │ CREATE │ HTTP client for analysis service API                │       │
│ ├──────────────────────────────────────────────────┼────────┼─────────────────────────────────────────────────────┤       │
│ │ src/stream_of_worship/admin/commands/audio.py    │ MODIFY │ Add analyze and status commands                     │       │
│ ├──────────────────────────────────────────────────┼────────┼─────────────────────────────────────────────────────┤       │
│ │ src/stream_of_worship/admin/db/client.py         │ MODIFY │ Add r2_stems_url param to update_recording_analysis │       │
│ ├──────────────────────────────────────────────────┼────────┼─────────────────────────────────────────────────────┤       │
│ │ src/stream_of_worship/admin/services/__init__.py │ MODIFY │ Export AnalysisClient                               │       │
│ ├──────────────────────────────────────────────────┼────────┼─────────────────────────────────────────────────────┤       │
│ │ tests/admin/test_analysis_client.py              │ CREATE │ Tests for AnalysisClient service                    │       │
│ ├──────────────────────────────────────────────────┼────────┼─────────────────────────────────────────────────────┤       │
│ │ tests/admin/test_audio_commands.py               │ MODIFY │ Add tests for analyze + status commands             │       │
│ └──────────────────────────────────────────────────┴────────┴─────────────────────────────────────────────────────┘       │
│ ---                                                                                                                       │
│ 1. services/analysis.py (NEW)                                                                                             │
│                                                                                                                           │
│ HTTP client wrapping the analysis service REST API. Follows R2Client pattern.                                             │
│                                                                                                                           │
│ Data classes                                                                                                              │
│                                                                                                                           │
│ @dataclass                                                                                                                │
│ class AnalysisResult:                                                                                                     │
│     duration_seconds: Optional[float] = None                                                                              │
│     tempo_bpm: Optional[float] = None                                                                                     │
│     musical_key: Optional[str] = None                                                                                     │
│     musical_mode: Optional[str] = None                                                                                    │
│     key_confidence: Optional[float] = None                                                                                │
│     loudness_db: Optional[float] = None                                                                                   │
│     beats: Optional[List[float]] = None                                                                                   │
│     downbeats: Optional[List[float]] = None                                                                               │
│     sections: Optional[List[Dict[str, Any]]] = None                                                                       │
│     embeddings_shape: Optional[List[int]] = None                                                                          │
│     stems_url: Optional[str] = None                                                                                       │
│                                                                                                                           │
│ @dataclass                                                                                                                │
│ class JobInfo:                                                                                                            │
│     job_id: str                                                                                                           │
│     status: str           # queued | processing | completed | failed                                                      │
│     job_type: str                                                                                                         │
│     progress: float = 0.0                                                                                                 │
│     stage: str = ""                                                                                                       │
│     error_message: Optional[str] = None                                                                                   │
│     result: Optional[AnalysisResult] = None                                                                               │
│     created_at: Optional[str] = None                                                                                      │
│     updated_at: Optional[str] = None                                                                                      │
│                                                                                                                           │
│ class AnalysisServiceError(Exception): ...                                                                                │
│                                                                                                                           │
│ AnalysisClient class                                                                                                      │
│                                                                                                                           │
│ - Constructor: __init__(self, base_url: str, timeout: int = 30) — reads SOW_ANALYSIS_API_KEY from env, raises ValueError  │
│ if missing                                                                                                                │
│ - health_check() → dict — GET /api/v1/health (no auth needed)                                                             │
│ - submit_analysis(audio_url, content_hash, generate_stems=True, force=False) → JobInfo — POST /api/v1/jobs/analyze with   │
│ Bearer auth                                                                                                               │
│ - get_job(job_id) → JobInfo — GET /api/v1/jobs/{job_id} with Bearer auth                                                  │
│ - wait_for_completion(job_id, poll_interval=3.0, timeout=600.0, callback=None) → JobInfo — polls get_job in a loop; calls │
│  callback(JobInfo) each iteration for progress updates; raises AnalysisServiceError on timeout                            │
│ - _parse_job_response(data) → JobInfo — static method, parses JSON dict                                                   │
│                                                                                                                           │
│ All requests exceptions wrapped into AnalysisServiceError with descriptive messages. Specific handling for 401 (invalid   │
│ API key) and 404 (job not found).                                                                                         │
│                                                                                                                           │
│ ---                                                                                                                       │
│ 2. commands/audio.py — New Commands                                                                                       │
│                                                                                                                           │
│ 2a. audio analyze                                                                                                         │
│                                                                                                                           │
│ analyze IDENTIFIER [--force] [--no-stems] [--wait] [--config]                                                             │
│                                                                                                                           │
│ Identifier resolution — accepts both song_id and hash_prefix:                                                             │
│ 1. Try db_client.get_song(identifier) → if found, get recording via get_recording_by_song_id                              │
│ 2. Otherwise try db_client.get_recording_by_hash(identifier)                                                              │
│                                                                                                                           │
│ Logic flow:                                                                                                               │
│ 1. Standard config/db boilerplate                                                                                         │
│ 2. Resolve identifier → recording (exit 1 if not found)                                                                   │
│ 3. Validate recording.r2_audio_url exists                                                                                 │
│ 4. If analysis_status == "completed" and not --force → print "already analyzed", exit 0                                   │
│ 5. If analysis_status == "processing" and analysis_job_id set → without --wait: print "already in progress", exit 0; with │
│  --wait: skip to polling existing job                                                                                     │
│ 6. Create AnalysisClient(config.analysis_url) — catch ValueError                                                          │
│ 7. Submit via client.submit_analysis(...) — catch AnalysisServiceError                                                    │
│ 8. Update DB: analysis_status="processing", analysis_job_id=job.job_id                                                    │
│ 9. Print job ID                                                                                                           │
│ 10. If --wait: poll with Rich Progress spinner, on completion store results via update_recording_analysis +               │
│ update_recording_status, on failure update to "failed"                                                                    │
│                                                                                                                           │
│ Rich progress display (wait mode):                                                                                        │
│ - SpinnerColumn + TextColumn (description) + BarColumn + TextColumn (stage)                                               │
│ - Updated via callback from wait_for_completion                                                                           │
│                                                                                                                           │
│ 2b. audio status                                                                                                          │
│                                                                                                                           │
│ status [JOB_ID] [--config]                                                                                                │
│                                                                                                                           │
│ Mode A (with job_id): Query service via client.get_job(job_id), display in a Rich Panel (job ID, status, progress, stage, │
│  results if completed, error if failed).                                                                                  │
│                                                                                                                           │
│ Mode B (no job_id): List all recordings where analysis_status != "completed" or lrc_status != "completed". Display Rich   │
│ Table with columns: Hash Prefix, Song, Analysis Status, Job ID, LRC Status, LRC Job. If none pending, print "All          │
│ recordings are fully processed."                                                                                          │
│                                                                                                                           │
│ Helper functions (module-level in audio.py)                                                                               │
│                                                                                                                           │
│ - _format_duration(seconds) → str (MM:SS)                                                                                 │
│ - _colorize_status(status) → Rich-markup str (green/yellow/red/dim)                                                       │
│                                                                                                                           │
│ New imports in audio.py                                                                                                   │
│                                                                                                                           │
│ import json                                                                                                               │
│ from stream_of_worship.admin.services.analysis import (                                                                   │
│     AnalysisClient, AnalysisServiceError,                                                                                 │
│ )                                                                                                                         │
│                                                                                                                           │
│ ---                                                                                                                       │
│ 3. db/client.py — Minor Update                                                                                            │
│                                                                                                                           │
│ Add r2_stems_url: Optional[str] = None parameter to update_recording_analysis (line 483). Add to SQL:                     │
│ r2_stems_url = COALESCE(?, r2_stems_url),                                                                                 │
│ Backward-compatible (defaults to None, preserves existing value via COALESCE).                                            │
│                                                                                                                           │
│ ---                                                                                                                       │
│ 4. services/__init__.py — Minor Update                                                                                    │
│                                                                                                                           │
│ Add AnalysisClient to imports and __all__.                                                                                │
│                                                                                                                           │
│ ---                                                                                                                       │
│ 5. Error Handling                                                                                                         │
│ Error: Missing API key                                                                                                    │
│ Exception: ValueError from constructor                                                                                    │
│ Command Response: "[red]Analysis service not configured: ..." exit 1                                                      │
│ ────────────────────────────────────────                                                                                  │
│ Error: Service unreachable                                                                                                │
│ Exception: AnalysisServiceError (wraps ConnectionError)                                                                   │
│ Command Response: "[red]Cannot connect to analysis service..." exit 1                                                     │
│ ────────────────────────────────────────                                                                                  │
│ Error: 401 Unauthorized                                                                                                   │
│ Exception: AnalysisServiceError                                                                                           │
│ Command Response: "[red]Authentication failed..." exit 1                                                                  │
│ ────────────────────────────────────────                                                                                  │
│ Error: 404 Job not found                                                                                                  │
│ Exception: AnalysisServiceError                                                                                           │
│ Command Response: "[red]Job not found..." exit 1                                                                          │
│ ────────────────────────────────────────                                                                                  │
│ Error: Poll timeout                                                                                                       │
│ Exception: AnalysisServiceError                                                                                           │
│ Command Response: "[red]Timed out..." exit 1                                                                              │
│ ────────────────────────────────────────                                                                                  │
│ Error: Job failed                                                                                                         │
│ Exception: job.status == "failed" (not exception)                                                                         │
│ Command Response: Print error_message, update DB to "failed", exit 1                                                      │
│ ---                                                                                                                       │
│ 6. Test Plan                                                                                                              │
│                                                                                                                           │
│ tests/admin/test_analysis_client.py (NEW, ~20 tests)                                                                      │
│                                                                                                                           │
│ Mock at: stream_of_worship.admin.services.analysis.requests.get/post                                                      │
│ Credentials: monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-key")                                                       │
│ Class: TestAnalysisClientInit                                                                                             │
│ Tests: creates with API key, stores base_url/timeout, raises ValueError without key, strips trailing slash                │
│ ────────────────────────────────────────                                                                                  │
│ Class: TestHealthCheck                                                                                                    │
│ Tests: success, connection error                                                                                          │
│ ────────────────────────────────────────                                                                                  │
│ Class: TestSubmitAnalysis                                                                                                 │
│ Tests: success, options passed correctly, connection error, 401 unauthorized, 500 server error                            │
│ ────────────────────────────────────────                                                                                  │
│ Class: TestGetJob                                                                                                         │
│ Tests: queued, completed with result, failed, 404 not found, 401 unauthorized                                             │
│ ────────────────────────────────────────                                                                                  │
│ Class: TestWaitForCompletion                                                                                              │
│ Tests: completes immediately, polls until complete, returns on failure, timeout, callback invoked                         │
│ ────────────────────────────────────────                                                                                  │
│ Class: TestParseJobResponse                                                                                               │
│ Tests: minimal, full with result, null result                                                                             │
│ tests/admin/test_audio_commands.py additions (~24 tests)                                                                  │
│                                                                                                                           │
│ Mock at: stream_of_worship.admin.commands.audio.AnalysisClient                                                            │
│                                                                                                                           │
│ TestAnalyzeCommand (~16 tests):                                                                                           │
│ - No config / no DB (standard boilerplate tests)                                                                          │
│ - Recording not found (by song_id, by hash)                                                                               │
│ - Song without recording                                                                                                  │
│ - No r2_audio_url                                                                                                         │
│ - Already completed (without/with --force)                                                                                │
│ - Already processing (without/with --wait)                                                                                │
│ - Missing API key                                                                                                         │
│ - Service unavailable                                                                                                     │
│ - Fire-and-forget success (verify DB updated to "processing")                                                             │
│ - By hash_prefix / by song_id                                                                                             │
│ - Wait mode: completed (verify DB updated with results)                                                                   │
│ - Wait mode: failed (verify DB updated to "failed")                                                                       │
│ - Wait mode: timeout                                                                                                      │
│ - --no-stems flag passed correctly                                                                                        │
│                                                                                                                           │
│ TestStatusCommand (~8 tests):                                                                                             │
│ - No config / no DB                                                                                                       │
│ - With job_id: success, not found, missing API key                                                                        │
│ - No args: all completed, shows pending, empty DB                                                                         │
│                                                                                                                           │
│ Expected totals                                                                                                           │
│                                                                                                                           │
│ - New tests: ~44                                                                                                          │
│ - Running total: 210 + 44 = ~254                                                                                          │
│                                                                                                                           │
│ ---                                                                                                                       │
│ 7. Dependencies                                                                                                           │
│                                                                                                                           │
│ No new dependencies. Uses requests (already in admin extra) and json/time (stdlib).                                       │
│                                                                                                                           │
│ ---                                                                                                                       │
│ 8. Implementation Order                                                                                                   │
│                                                                                                                           │
│ 1. services/analysis.py + services/__init__.py                                                                            │
│ 2. tests/admin/test_analysis_client.py — run and verify                                                                   │
│ 3. db/client.py — add r2_stems_url param                                                                                  │
│ 4. commands/audio.py — add analyze and status commands                                                                    │
│ 5. tests/admin/test_audio_commands.py — add test classes                                                                  │
│ 6. Run all tests: PYTHONPATH=src uv run --extra admin --extra test pytest tests/admin/ -v                                 │
│                                                                                                                           │
│ ---                                                                                                                       │
│ 9. Verification                                                                                                           │
│                                                                                                                           │
│ # Run all admin tests                                                                                                     │
│ PYTHONPATH=src uv run --extra admin --extra test pytest tests/admin/ -v                                                   │
│                                                                                                                           │
│ # Run just the new tests                                                                                                  │
│ PYTHONPATH=src uv run --extra admin --extra test pytest tests/admin/test_analysis_client.py -v                            │
│ PYTHONPATH=src uv run --extra admin --extra test pytest tests/admin/test_audio_commands.py -v -k "analyze or status"      │
│                                                                                                                           │
│ # Smoke test CLI help                                                                                                     │
│ PYTHONPATH=src uv run --extra admin python -m stream_of_worship.admin.main audio analyze --help                           │
│ PYTHONPATH=src uv run --extra admin python -m stream_of_worship.admin.main audio status --help