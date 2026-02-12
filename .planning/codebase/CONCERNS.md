# Codebase Concerns

**Analysis Date:** 2026-02-13

## Tech Debt

**Bare Exception Handlers:**
- Issue: Multiple locations silently swallow all exceptions with `except Exception: pass`
- Files:
  - `src/stream_of_worship/app/services/playback.py` (lines 302, 415, 425)
  - `src/stream_of_worship/app/services/export.py` (lines 151, 159)
  - `src/stream_of_worship/app/state.py` (line 100)
  - `src/stream_of_worship/app/config.py` (lines 204-206)
  - `src/stream_of_worship/admin/db/client.py` (lines 201-203, 722-724)
  - `src/stream_of_worship/admin/db/models.py` (lines 117-118, 301-302)
  - `src/stream_of_worship/tui/utils/logger.py` (lines 65-67, 335-336)
  - `src/stream_of_worship/tui/screens/playlist.py` (line 388)
  - `src/stream_of_worship/admin/config.py` (lines 245-247)
- Impact: Errors are hidden, making bugs harder to diagnose. Configuration corruption, state update failures, and resource cleanup issues go unreported.
- Fix approach: Replace with specific exception types, add logging to understand failure modes, or let errors bubble up with context.

**Incomplete Section Preview Implementation:**
- Issue: `PlaybackService.preview_section()` has TODO marker and just calls regular play()
- Files: `src/stream_of_worship/app/services/playback.py` (line 538)
- Impact: Section preview should auto-stop at section end, but doesn't. Users must manually stop preview.
- Fix approach: Implement section boundary detection and automatic stop after section_end_seconds.

**TODO: Analysis Command Not Implemented:**
- Issue: CLI analysis command stub exists with TODO
- Files: `src/stream_of_worship/cli/main.py` (line 292)
- Impact: Users must use POC scripts directly instead of integrated CLI
- Fix approach: Integrate with admin/services/analysis.py (AnalysisClient)

## Known Bugs

**Screen Caching Bug (FIXED - See Note):**
- Symptoms: On second navigation to a screen, internal state changes but display freezes showing previous screen
- Files: Navigation testing documents at `tests/app/README_NAVIGATION_TESTS.md` and `tests/app/test_navigation.py`
- Trigger: Navigate List → Editor, go back, navigate List → Editor again
- Status: Bug documented and test suite created to detect it. Implementation note: Textual may cache screen instances. Each navigation must create fresh screen instances.
- Test evidence: `test_visual_freeze_detection` and `test_songset_list_to_editor_and_back` tests catch this by comparing SVG screenshot content before/after navigation

**Resume from Pause Not Implemented:**
- Issue: PlaybackService pause state exists but `play()` doesn't resume from paused position
- Files: `src/stream_of_worship/app/services/playback.py` (line 302)
- Impact: Calling play() while paused doesn't resume; it likely restarts from beginning
- Fix approach: Track pause position and restore it in play() when resuming

## Performance Bottlenecks

**Position Tracking Thread Sleep:**
- Problem: Playback position thread sleeps 0.1s between updates (10Hz update rate)
- Files: `src/stream_of_worship/app/services/playback.py` (line 178)
- Cause: Polling-based position tracking from miniaudio decoder
- Impact: Position UI updates lag by up to 100ms; playback feeling may be slightly unresponsive
- Improvement path: Use callback-based position updates from miniaudio if available, or reduce sleep to 0.05s (20Hz) if CPU permits

**Blocking HTTP Requests with Timeout:**
- Problem: Analysis service polling uses blocking requests with 600s timeout
- Files: `src/stream_of_worship/admin/services/analysis.py` (lines 95-106, 344-359)
- Impact: Long analysis jobs block CLI for up to 600 seconds. Multiple concurrent analyses cause cascading waits.
- Improvement path: Use asyncio-based requests (aiohttp) or implement job queue with background polling

**Bare Frame Processing in Video Engine:**
- Problem: Video generation processes all frames sequentially
- Files: `src/stream_of_worship/app/services/video_engine.py` (line 995 - large file)
- Impact: Video generation may be slow for long songsets (10+ minutes)
- Improvement path: Implement frame batch processing or parallel encoding stages

## Fragile Areas

**Configuration Deserialization:**
- Files: `src/stream_of_worship/app/config.py` (lines 203-209) and `src/stream_of_worship/admin/config.py` (lines 244-250)
- Why fragile: When config file is corrupted, app silently creates new default config without notifying user of data loss
- Safe modification: Add warning logs, backup original corrupt file, or prompt user before silently recreating
- Test coverage: No tests for corrupted config recovery path

**Lyrics Line Parsing:**
- Files: `src/stream_of_worship/admin/db/models.py` (lines 115-121, 299-303)
- Why fragile: Falls back from JSON to raw string with silent exception swallow. Double fallback without clear contract.
- Safe modification: Define expected format explicitly, validate before storing, log parse failures
- Test coverage: No tests for edge cases like partially valid JSON or mixed format

**Export State Machine:**
- Files: `src/stream_of_worship/app/services/export.py` (entire file)
- Why fragile: Thread-based export with state transitions may have race conditions. Callbacks silently fail.
- Safe modification: Add state machine validation, lock around state transitions, validate callback execution
- Test coverage: Tests exist but async callback handling needs review for race conditions

**Miniaudio Stream Generator:**
- Files: `src/stream_of_worship/app/services/playback.py` (lines 221-260)
- Why fragile: Complex generator with frame boundary calculations. Off-by-one errors could cause audio dropout.
- Safe modification: Add extensive unit tests for generator frame math, add assertions for invariants
- Test coverage: Needs unit tests separate from integration tests

## Test Coverage Gaps

**Playback State Transitions:**
- What's not tested: State machine transitions (PLAYING→PAUSED→PLAYING, etc.)
- Files: `src/stream_of_worship/app/services/playback.py`
- Risk: Resume-from-pause bug exists and goes unnoticed
- Priority: High - affects core playback functionality

**Exception Handlers in Critical Paths:**
- What's not tested: Graceful handling when callbacks fail in state change listeners
- Files: `src/stream_of_worship/app/state.py` (line 100), `src/stream_of_worship/app/services/export.py` (lines 151, 159)
- Risk: One failing callback can break all state updates. Silent failures hide notification breakage.
- Priority: High - impacts UI responsiveness and error visibility

**Configuration Loading with Corrupt Files:**
- What's not tested: Recovery when config file is corrupted/unparseable
- Files: `src/stream_of_worship/app/config.py`, `src/stream_of_worship/admin/config.py`
- Risk: User data loss without warning or backup
- Priority: Medium - occurs only on config corruption but impact is data loss

**Lyrics JSON Parsing Edge Cases:**
- What's not tested: Mixed format lyrics, partial JSON, empty strings, special characters
- Files: `src/stream_of_worship/admin/db/models.py` (lines 115-121, 299-303)
- Risk: Silent fallback may hide data corruption. Line parsing accuracy affects syncing.
- Priority: Medium - LRC sync quality depends on correct parsing

**R2 Download Failure Recovery:**
- What's not tested: Partial downloads, corrupted cache, retry logic
- Files: `src/stream_of_worship/app/services/asset_cache.py`
- Risk: Export fails without clear recovery path
- Priority: Medium - affects reliability of export feature

**Navigation Screen Instance Caching:**
- What's not tested: All code paths that create/cache screen instances
- Files: `src/stream_of_worship/app/app.py` (navigation logic)
- Risk: Screen caching bug documented in `tests/app/README_NAVIGATION_TESTS.md` suggests infrastructure may not be preventing re-use
- Priority: High - fixes may be preventing the caching behavior, but needs validation across all navigation paths

## Scaling Limits

**In-Memory Job Queue:**
- Current capacity: Designed for small batches (documented as "optional Redis planned")
- Files: `services/analysis/src/sow_analysis/workers/queue.py`
- Limit: If analysis service receives sustained high load, in-memory queue can OOM
- Scaling path: Implement Redis-based queue, add queue size limits with backpressure, implement job persistence

**Concurrent Playback Position Tracking:**
- Current capacity: Single playback instance + single position thread
- Files: `src/stream_of_worship/app/services/playback.py`
- Limit: Architecture supports only one active playback at a time
- Scaling path: Design multi-instance playback orchestrator if simultaneous playback needed

**Local Asset Cache Disk Usage:**
- Current capacity: Unbounded cache directory growth
- Files: `src/stream_of_worship/app/services/asset_cache.py`
- Limit: No cache eviction policy; disk usage grows indefinitely
- Scaling path: Implement LRU eviction, add cache size limits, add cleanup commands

## Dependencies at Risk

**PyAudio (Optional Audio Input):**
- Risk: Deprecated, barely maintained, platform-specific compilation issues
- Files: `pyproject.toml` line 41 (tui extras)
- Impact: Audio input features may fail on new Python versions or systems without gcc/headers
- Migration plan: Consider `sounddevice` (more active) or use miniaudio for input as well

**PyTorch Version Pinning:**
- Risk: Pinned to 2.8.x with specific torchaudio compatibility (`torchaudio 2.9.0+ removed AudioMetaData`)
- Files: `pyproject.toml` lines 60-62 (transcription extras)
- Impact: Cannot upgrade PyTorch past 2.8 without breaking transcription. Security fixes in 2.9+ are unavailable.
- Migration plan: Review pyannote.audio dependency - may have released version compatible with newer PyTorch

**NATTEN Compilation:**
- Risk: Compiled from source in Docker. Tight coupling to specific PyTorch version.
- Files: `services/analysis/Dockerfile` (conditional NATTEN compilation)
- Impact: Any PyTorch upgrade breaks NATTEN compilation. Adds significant build time.
- Migration plan: Check if pre-built wheels available for supported PyTorch versions, or evaluate alternative attention implementations

**Whisper Model Caching:**
- Risk: Model files downloaded on first use to `WHISPER_CACHE_DIR`, no size limit
- Files: `services/analysis/src/sow_analysis/config.py`
- Impact: First LRC generation downloads ~2GB model without user confirmation
- Migration plan: Pre-download in container, or implement explicit model download step with progress

## Missing Critical Features

**Bidirectional Sync:**
- Problem: Turso sync is present but only sync-to-local, not local-to-cloud
- Files: `src/stream_of_worship/admin/services/sync.py`
- Blocks: Multi-device workflow; changes on desktop don't sync to mobile
- Impact: Admin changes on one device don't propagate to app on another device

**Robust Error Recovery in Export:**
- Problem: Export can fail midway with incomplete output files
- Files: `src/stream_of_worship/app/services/export.py`
- Blocks: Users cannot resume failed exports; must restart from scratch
- Impact: Long exports (20+ minutes) that fail near the end must be restarted entirely

**Audio Quality Settings:**
- Problem: Export uses fixed audio parameters with no quality/bitrate options
- Files: `src/stream_of_worship/app/services/audio_engine.py`
- Blocks: Users cannot optimize for storage vs quality tradeoff
- Impact: Export size cannot be controlled

## Security Considerations

**R2 Credentials in Docker Compose:**
- Risk: Sample `docker-compose.yml` may encourage putting secrets in environment variable list without .env file
- Files: `services/analysis/docker-compose.yml`
- Current mitigation: Comments suggest using .env, but no hard requirement
- Recommendations: Make .env.example with placeholders, validate that env vars are sourced from .env file on startup

**Unvalidated Cache Downloads:**
- Risk: Asset cache downloads from R2 without hash/signature validation
- Files: `src/stream_of_worship/app/services/asset_cache.py`
- Current mitigation: Uses HTTPS
- Recommendations: Store SHA256 hash in database, validate on download, reject corrupted files

**Whisper Model Trust:**
- Risk: Model files downloaded from Hugging Face without signature verification
- Files: Whisper library (external)
- Current mitigation: Standard Hugging Face infrastructure
- Recommendations: Pre-download models in CI, commit hashes to version control

**LLM API Key Exposure:**
- Risk: `SOW_LLM_API_KEY` in environment, could leak in logs or error messages
- Files: `services/analysis/src/sow_analysis/config.py`
- Current mitigation: Not logged in debug output
- Recommendations: Add explicit .env var masking helper, audit all logging for API key references

## Architecture Issues

**Screen Instance Lifecycle Ambiguity:**
- Problem: Navigation system unclear whether screens are cached or recreated
- Files: `src/stream_of_worship/app/app.py` (screen stack/navigation), `src/stream_of_worship/app/screens/`
- Impact: Screen caching bug documented but root cause in architecture unclarified
- Fix approach: Document screen lifecycle explicitly; implement fresh instance creation for each navigation; add state reset on navigation

**Separate CLI and App Configurations:**
- Problem: `AdminConfig` (used by CLI) and `AppConfig` (extends AdminConfig) have overlapping responsibility
- Files: `src/stream_of_worship/admin/config.py` and `src/stream_of_worship/app/config.py`
- Impact: Configuration changes require updates in two places. Inheritance makes update surface larger.
- Fix approach: Single config with optional app-specific keys, or composition instead of inheritance

**Callback-Based State Notification:**
- Problem: State changes notify listeners via callbacks, but exceptions in callbacks are silently caught
- Files: `src/stream_of_worship/app/state.py` (lines 95-100)
- Impact: UI state listeners can fail silently, leaving UI out-of-sync with application state
- Fix approach: Log callback failures, collect errors, consider fatal vs recoverable distinctions

---

*Concerns audit: 2026-02-13*
