# LRC Pipeline: Short-Circuit Permanent YouTube Transcript Errors

## Context

Log evidence (2026-07-14, analysis-dev-1, 50 LRC jobs processing):

```
ERROR - [job_54029c3d20a7] Direct fetch failed for -DA1bhTnOz0, trying list fallback:
Could not retrieve a transcript for the video https://www.youtube.com/watch?v=-DA1bhTnOz0!
No transcripts were found for any of the requested language codes: ['zh-Hant', 'zh-TW', 'zh-Hans', 'zh-CN', 'zh-HK', 'zh', 'en-US', 'en']
For this video (-DA1bhTnOz0) transcripts are available in the following languages: ro ("Romanian (auto-generated)")

ERROR - [job_53150ab1e26f] Direct fetch failed for C6HSl4Nhgdg, trying list fallback:
Could not retrieve a transcript for the video https://www.youtube.com/watch?v=C6HSl4Nhgdg!
Subtitles are disabled for this video

ERROR - [job_c0ae0f6f410f] Direct fetch failed for Nxd2Z6-pDCQ, trying list fallback:
Subtitles are disabled for this video

ERROR - [job_095d76c215bc] Direct fetch failed for 86t6JIxlbEc, trying list fallback:
Subtitles are disabled for this video

ERROR - [job_fc70f0091d38] Direct fetch failed for _I4wvMSOiw8, trying list fallback:
Subtitles are disabled for this video

ERROR - [job_a77e836485bf] Direct fetch failed for BvqsBBSqKAo, trying list fallback:
Subtitles are disabled for this video
```

### Root cause

`fetch_youtube_transcript()` (`youtube_transcript.py:640`) implements a **two-phase
fetch strategy**:

1. **Phase 1 — Direct fetch**: Calls `YouTubeTranscriptApi.fetch(video_id, languages=...)`
   with the preferred BCP-47 language codes (e.g. `zh-Hant`, `zh-TW`, `en-US`).
2. **Phase 2 — List fallback**: On any exception from Phase 1, calls
   `YouTubeTranscriptApi.list(video_id)`, then `_find_best_transcript()` to pick the
   best available transcript by iterating the full transcript list, then fetches it.

The list fallback is **correct for `NoTranscriptFound`** — the direct fetch fails because
BCP-47 codes like `zh-Hant` differ from YouTube's `zh-TW`, and the list fallback can find
the correct transcript by its actual language code.

**However**, the list fallback is **wasteful for permanent errors**. When YouTube returns
`TranscriptsDisabled` ("Subtitles are disabled for this video"), a list call will also
fail because there are no transcripts to list — the disabled subtitles apply to the entire
transcript listing endpoint. The same applies to `VideoUnavailable`, `VideoUnplayable`,
`InvalidVideoId`, and `AgeRestricted`.

Each wasted fallback cycle costs:
- `asyncio.sleep(2.0)` — deliberate 2-second proxy IP rotation delay
- A second `_rate_limiter.call()` — includes semaphore acquire, min-interval throttle,
  and the `YouTubeTranscriptApi.list()` HTTP round-trip through the proxy (~5-10s)
- A second exception log line

With 50 concurrent LRC jobs and 6+ hitting permanent errors, this wastes ≥42 seconds
of per-job processing time, plus pollutes the logs with "List fallback also failed"
errors.

### Current exception flow

```
YouTubeTranscriptApi raises: TranscriptsDisabled (subclass of CouldNotRetrieveTranscript)
  → _rate_limiter.call():
      → _is_rate_limited_error() → False (no 429)
      → _is_transient_connection_error() → False (no SSL markers)
      → Non-retriable: raise immediately (line 372, no retry)
  → fetch_youtube_transcript() except block (line 701):
      → logger.error("Direct fetch failed ... trying list fallback")
      → asyncio.sleep(2.0)   ← WASTED
  → Phase 2: _rate_limiter.call(_fetch_via_list):
      → YouTubeTranscriptApi.list(video_id)
      → This also fails (subtitles disabled applies to listing)
  → fetch_youtube_transcript() outer except block (line 721):
      → logger.error("List fallback also failed ...")
      → raise YouTubeTranscriptError(...)
  → try_youtube_transcript_lrc() except Exception (lrc.py:904):
      → return None  (falls back to Whisper/Qwen3 ASR)
```

### youtube-transcript-api exception hierarchy (v1.2.4)

Source: `/home/mhuang/.cache/uv/archive-v0/yH7uCUQ4cmwkEJBJ1fAW6/youtube_transcript_api/_errors.py`

```
YouTubeTranscriptApiException
  └── CouldNotRetrieveTranscript
       ├── TranscriptsDisabled         CAUSE_MESSAGE = "Subtitles are disabled for this video"
       ├── NoTranscriptFound            CAUSE_MESSAGE = "No transcripts were found ..."
       ├── VideoUnavailable             CAUSE_MESSAGE = "The video is no longer available"
       ├── VideoUnplayable              CAUSE_MESSAGE = "The video is unplayable for the following reason: {reason}"
       ├── InvalidVideoId              CAUSE_MESSAGE = "You provided an invalid video id ..."
       ├── AgeRestricted               CAUSE_MESSAGE = "This video is age-restricted ..."
       ├── RequestBlocked               CAUSE_MESSAGE = "YouTube is blocking requests from your IP ..."
       │    └── IpBlocked               CAUSE_MESSAGE = "YouTube is blocking requests from your IP ..."
       ├── PoTokenRequired             CAUSE_MESSAGE = "The requested video cannot be retrieved without a PO Token ..."
       ├── NotTranslatable
       ├── TranslationLanguageNotAvailable
       ├── FailedToCreateConsentCookie
       ├── YouTubeDataUnparsable
       ├── YouTubeRequestFailed
       └── ...
```

**Permanently unavailable types** (the video will never have a usable transcript
for our purposes — the list fallback will also fail or return unavailable):

| Class | Why permanent |
|-------|---------------|
| `TranscriptsDisabled` | Subtitles turned off; listing also yields nothing |
| `VideoUnavailable` | Video removed; no data to fetch |
| `VideoUnplayable` | Video cannot be played; listing may also fail |
| `InvalidVideoId` | Malformed ID; both fetch and list will fail |
| `AgeRestricted` | Requires auth; listing not useful in our context |

**Errors where list fallback IS valuable (do NOT short-circuit):**

| Class | Why keep fallback |
|-------|-------------------|
| `NoTranscriptFound` | Direct fetch failed on BCP-47 codes, but list can find actual codes (e.g. `zh-TW`) — this is the fallback's primary purpose |
| `RequestBlocked` / `IpBlocked` | Transient IP ban; rotating proxy IP may succeed on second attempt |
| `FailedToCreateConsentCookie` | Consent flow issue; may succeed from a different IP |
| `PoTokenRequired` | Requires PO token; listing may still work if we eventually add token support |
| `YouTubeRequestFailed` | Generic request failure; retry via list may succeed |
| `YouTubeDataUnparsable` | Edge case; unlikely to succeed, but listing won't hurt |

---

## Change Set

### File 1: `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py`

#### Change 1a: Add `_is_permanently_unavailable_error` helper

Insert after `_is_transient_connection_error()` (after line 143, before
`class _YouTubeRateLimiter`):

```python
def _is_permanently_unavailable_error(e: Exception) -> bool:
    """Check if an exception indicates the video will never have a usable transcript.

    These errors are permanent at the video level — the list fallback
    (Phase 2 of fetch_youtube_transcript) will also fail, so we short-circuit
    to avoid:

    - The 2-second asyncio.sleep proxy rotation delay
    - A second rate-limiter-throttled HTTP round-trip to YouTube
    - A second exception log line

    Detects youtube-transcript-api exception types by class name (avoids
    importing the library at module level):

    - TranscriptsDisabled: subtitles are disabled for this video
    - VideoUnavailable: video has been removed
    - VideoUnplayable: video cannot be played
    - InvalidVideoId: the video ID is malformed
    - AgeRestricted: video is age-restricted (requires auth)
    """
    permanently_unavailable_types = {
        "TranscriptsDisabled",
        "VideoUnavailable",
        "VideoUnplayable",
        "InvalidVideoId",
        "AgeRestricted",
    }
    visited: set[int] = set()
    exc: Optional[Exception] = e
    while exc is not None and id(exc) not in visited:
        visited.add(id(exc))
        if type(exc).__name__ in permanently_unavailable_types:
            return True
        exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    return False
```

The class-name-based detection approach is chosen because:

1. It avoids importing `youtube_transcript_api._errors` at module level —
   the library import is currently deferred to inside
   `_fetch_direct()` / `_fetch_via_list()` (lazy import pattern already used).
   The library import has side effects (cookies, HTTP session setup).
2. The `id(exc) not in visited` traversal mirrors the pattern already used in
   `_is_rate_limited_error()` and `_is_transient_connection_error()`, ensuring
   consistency with existing code style.
3. It is robust against minor library version bumps — class names are part of
   the public exception API (documented in the library's `__init__` re-exports).

#### Change 1b: Short-circuit in `fetch_youtube_transcript`

Replace the Phase 1 except block (lines 693-704):

**Before:**
```python
    # Phase 1: Direct fetch with language codes
    try:
        transcript = await _rate_limiter.call(
            _fetch_direct, description=f"direct fetch for {video_id}"
        )
        if transcript:
            logger.info(f"Fetched {len(transcript)} transcript segments from YouTube")
            return transcript
    except Exception as e:
        logger.error(f"Direct fetch failed for {video_id}, trying list fallback: {e}")
        # Brief delay to allow proxy IP rotation before list fallback
        await asyncio.sleep(2.0)
```

**After:**
```python
    # Phase 1: Direct fetch with language codes
    try:
        transcript = await _rate_limiter.call(
            _fetch_direct, description=f"direct fetch for {video_id}"
        )
        if transcript:
            logger.info(f"Fetched {len(transcript)} transcript segments from YouTube")
            return transcript
    except Exception as e:
        if _is_permanently_unavailable_error(e):
            logger.warning(
                f"Direct fetch failed for {video_id} with permanent error "
                f"(transcript will never be available), skipping list fallback: {e}"
            )
            raise YouTubeTranscriptError(
                f"Transcript permanently unavailable for video {video_id}: {e}"
            ) from e
        logger.error(f"Direct fetch failed for {video_id}, trying list fallback: {e}")
        # Brief delay to allow proxy IP rotation before list fallback
        await asyncio.sleep(2.0)
```

**Rationale for log level `warning` (not `error`):**
Permanent unavailability is an **expected** outcome — many videos legitimately
have subtitles disabled. Logging at `error` level pollutes error monitors.
`warning` is the right level for "expected problem that the system handled."

**Rationale for the propagated `YouTubeTranscriptError`:**
This maintains the existing flow — `try_youtube_transcript_lrc()` (lrc.py:904)
catches `Exception` (which includes `YouTubeTranscriptError`) and returns
`None`, which signals the LRC pipeline to fall back to Whisper/Qwen3 ASR.
No behavioral change downstream — just faster.

### File 2: `ops/analysis-service/tests/test_youtube_transcript.py`

Add `_is_permanently_unavailable_error` to the import block at the top of
the test file:

```python
from sow_analysis.workers.youtube_transcript import (
    DEFAULT_LANGUAGES,
    EN_LANG_CODES,
    ZH_LANG_CODES,
    _build_proxy_config,
    _find_best_transcript,
    _is_rate_limited_error,
    _is_transient_connection_error,
    _is_permanently_unavailable_error,  # NEW
    _rate_limiter,
    _YouTubeRateLimiter,
    build_correction_prompt,
    extract_video_id,
    fetch_youtube_transcript,
    language_preference_codes,
    parse_lrc_response,
    RotatingProxyConfig,
    YouTubeRateLimitedError,
    YouTubeTranscriptError,
)
```

Add a new test class `TestFetchYoutubeTranscriptPermanentErrors` with the
following cases, following the structure of existing tests in
`TestFetchYoutubeTranscript`:

#### Test 1: `test_subtitles_disabled_short_circuits_list_fallback`

```python
@pytest.mark.asyncio
async def test_subtitles_disabled_short_circuits_list_fallback(self):
    """TranscriptsDisabled → YouTubeTranscriptError raised, list NOT called."""
    from unittest.mock import patch

    from sow_analysis.workers.youtube_transcript import YouTubeTranscriptError

    class TranscriptsDisabled(Exception):
        pass  # Class name matches the real library exception type

    with patch("youtube_transcript_api.YouTubeTranscriptApi") as MockApi:
        mock_api = MockApi.return_value
        mock_api.fetch.side_effect = TranscriptsDisabled("Subtitles are disabled")

        with pytest.raises(YouTubeTranscriptError, match="permanently unavailable"):
            await fetch_youtube_transcript("testVideoId")

        # CRITICAL: list() must not be called (the short-circuit)
        mock_api.list.assert_not_called()
```

#### Test 2: `test_video_unavailable_short_circuits_list_fallback`

```python
@pytest.mark.asyncio
async def test_video_unavailable_short_circuits_list_fallback(self):
    """VideoUnavailable → YouTubeTranscriptError raised, list NOT called."""
    from unittest.mock import patch

    from sow_analysis.workers.youtube_transcript import YouTubeTranscriptError

    class VideoUnavailable(Exception):
        pass

    with patch("youtube_transcript_api.YouTubeTranscriptApi") as MockApi:
        mock_api = MockApi.return_value
        mock_api.fetch.side_effect = VideoUnavailable("The video is no longer available")

        with pytest.raises(YouTubeTranscriptError, match="permanently unavailable"):
            await fetch_youtube_transcript("testVideoId")

        mock_api.list.assert_not_called()
```

#### Test 3: `test_invalid_video_id_short_circuits_list_fallback`

```python
@pytest.mark.asyncio
async def test_invalid_video_id_short_circuits_list_fallback(self):
    """InvalidVideoId → YouTubeTranscriptError raised, list NOT called."""
    from unittest.mock import patch

    from sow_analysis.workers.youtube_transcript import YouTubeTranscriptError

    class InvalidVideoId(Exception):
        pass

    with patch("youtube_transcript_api.YouTubeTranscriptApi") as MockApi:
        mock_api = MockApi.return_value
        mock_api.fetch.side_effect = InvalidVideoId("invalid id")

        with pytest.raises(YouTubeTranscriptError, match="permanently unavailable"):
            await fetch_youtube_transcript("testVideoId")

        mock_api.list.assert_not_called()
```

#### Test 4: `test_age_restricted_short_circuits_list_fallback`

```python
@pytest.mark.asyncio
async def test_age_restricted_short_circuits_list_fallback(self):
    """AgeRestricted → YouTubeTranscriptError raised, list NOT called."""
    from unittest.mock import patch

    from sow_analysis.workers.youtube_transcript import YouTubeTranscriptError

    class AgeRestricted(Exception):
        pass

    with patch("youtube_transcript_api.YouTubeTranscriptApi") as MockApi:
        mock_api = MockApi.return_value
        mock_api.fetch.side_effect = AgeRestricted("age-restricted")

        with pytest.raises(YouTubeTranscriptError, match="permanently unavailable"):
            await fetch_youtube_transcript("testVideoId")

        mock_api.list.assert_not_called()
```

#### Test 5: `test_video_unplayable_short_circuits_list_fallback`

```python
@pytest.mark.asyncio
async def test_video_unplayable_short_circuits_list_fallback(self):
    """VideoUnplayable → YouTubeTranscriptError raised, list NOT called."""
    from unittest.mock import patch

    from sow_analysis.workers.youtube_transcript import YouTubeTranscriptError

    class VideoUnplayable(Exception):
        pass

    with patch("youtube_transcript_api.YouTubeTranscriptApi") as MockApi:
        mock_api = MockApi.return_value
        mock_api.fetch.side_effect = VideoUnplayable("unplayable")

        with pytest.raises(YouTubeTranscriptError, match="permanently unavailable"):
            await fetch_youtube_transcript("testVideoId")

        mock_api.list.assert_not_called()
```

#### Test 6: `test_no_transcript_found_still_tries_list_fallback`

This is the **critical regression test** — `NoTranscriptFound` must NOT be
short-circuited, because the list fallback is specifically designed to find
transcripts under non-BCP-47 language codes:

```python
@pytest.mark.asyncio
async def test_no_transcript_found_still_tries_list_fallback(self):
    """NoTranscriptFound must NOT short-circuit — it's the fallback's purpose."""
    from unittest.mock import patch

    mock_snippet = type("Snippet", (), {"text": "我要看見", "start": 15.0})()
    mock_fetched = [mock_snippet]

    mock_transcript_obj = type(
        "Transcript",
        (),
        {
            "language_code": "zh-TW",
            "language": "Chinese (Taiwan)",
            "is_generated": False,
            "fetch": lambda s: mock_fetched,
        },
    )()

    class NoTranscriptFound(Exception):
        pass

    with patch("youtube_transcript_api.YouTubeTranscriptApi") as MockApi:
        mock_api = MockApi.return_value
        mock_api.fetch.side_effect = NoTranscriptFound("No transcripts found")
        mock_api.list.return_value = [mock_transcript_obj]

        result = await fetch_youtube_transcript("testVideoId")

    assert len(result) == 1
    mock_api.list.assert_called_once_with("testVideoId")
```

#### Test 7 (unit test class): `TestIsPermanentlyUnavailableError`

Direct unit tests for the helper against the real exception class names:

```python
class TestIsPermanentlyUnavailableError:
    """Tests for _is_permanently_unavailable_error()."""

    @pytest.mark.parametrize(
        "exception_class_name,should_match",
        [
            ("TranscriptsDisabled", True),
            ("VideoUnavailable", True),
            ("VideoUnplayable", True),
            ("InvalidVideoId", True),
            ("AgeRestricted", True),
            ("NoTranscriptFound", False),
            ("RequestBlocked", False),
            ("IpBlocked", False),
            ("PoTokenRequired", False),
            ("FailedToCreateConsentCookie", False),
            ("YouTubeRequestFailed", False),
            ("YouTubeDataUnparsable", False),
            ("NotTranslatable", False),
            ("TranslationLanguageNotAvailable", False),
            ("GenericException", False),
        ],
    )
    def test_exception_class_name_detection(self, exception_class_name, should_match):
        # Dynamically create an exception class with the given name
        exc_type = type(exception_class_name, (Exception,), {})
        exc = exc_type("test message")
        assert _is_permanently_unavailable_error(exc) is should_match

    def test_detects_wrapped_exception_in_cause_chain(self):
        """Permanent error wrapped in another exception is still detected."""
        class TranscriptsDisabled(Exception):
            pass

        primary = RuntimeError("wrapper")
        primary.__cause__ = TranscriptsDisabled("subtitles disabled")
        assert _is_permanently_unavailable_error(primary) is True

    def test_detects_wrapped_exception_in_context_chain(self):
        """Permanent error in __context__ chain is detected."""
        class VideoUnavailable(Exception):
            pass

        primary = ValueError("wrapper")
        primary.__context__ = VideoUnavailable("video removed")
        assert _is_permanently_unavailable_error(primary) is True

    def test_handles_circular_exception_chain(self):
        """Circular __cause__/__context__ chain doesn't infinite-loop."""
        exc_a = Exception("a")
        exc_b = Exception("b")
        exc_a.__cause__ = exc_b
        exc_b.__cause__ = exc_a
        # Should return False without hanging
        assert _is_permanently_unavailable_error(exc_a) is False
```

---

## What Does NOT Change

- **`_is_rate_limited_error` / `_is_transient_connection_error`** — these are
  layered checks inside `_YouTubeRateLimiter.call()`. The short-circuit happens
  after that method raises its exception — we only skip Phase 2.
- **`_YouTubeRateLimiter`** — no change. Changes are isolated to
  `fetch_youtube_transcript()`.
- **`try_youtube_transcript_lrc` (lrc.py:851)** — no change. It already
  catches `Exception` (incl. `YouTubeTranscriptError`) and returns `None`.
- **Queue retry loop** (`queue.py:_process_lrc_job()`) — no change. The short-
  circuit raises `YouTubeTranscriptError`, which is NOT `YouTubeRateLimitedError`,
  so the queue loop will not treat it as retryable. `try_youtube_transcript_lrc`
  will catch it and return `None`, ending the YouTube attempt and falling
  through to Whisper.
- **`_find_best_transcript`** — no change to the fallback logic itself. Only the
  decision of whether to enter the fallback is modified.
- **`SOW_FREE_ONLY_MODE` bypass behavior** — for permanent errors, free mode AND
  non-free mode behave identically. A permanently unavailable video will never
  have a transcript regardless of mode.

---

## Sequence-of-Events After Change (expected behavior)

### Before (e.g. `Subtitles are disabled`)

1. Phase 1 direct fetch fails with `TranscriptsDisabled`
2. `_rate_limiter.call()` raises immediately (not 429, not transient)
3. `fetch_youtube_transcript` logs "Direct fetch failed ... trying list fallback"
4. `asyncio.sleep(2.0)` waits 2 seconds
5. Phase 2 list call via `_rate_limiter.call()` → `YouTubeTranscriptApi.list()`
6. List call also fails with `TranscriptsDisabled`
7. `fetch_youtube_transcript` logs "List fallback also failed ..."
8. Raises `YouTubeTranscriptError`
9. `try_youtube_transcript_lrc` catches → returns `None`
10. Queue falls through to Whisper/Qwen3 ASR

**Total wasted time: 2s + list HTTP round-trip (~5-10s) ≈ 7-12s per job**

### After (e.g. `Subtitles are disabled`)

1. Phase 1 direct fetch fails with `TranscriptsDisabled`
2. `_rate_limiter.call()` raises immediately (not 429, not transient)
3. `fetch_youtube_transcript` calls `_is_permanently_unavailable_error(e)` → `True`
4. Logs warning: "Direct fetch failed ... with permanent error ... skipping list fallback"
5. Raises `YouTubeTranscriptError` immediately
6. `try_youtube_transcript_lrc` catches → returns `None`
7. Queue falls through to Whisper/Qwen3 ASR

**Savings: ~7-12s per job, cleaner logs, one exception log line instead of two**

### After (`NoTranscriptFound` — unchanged path)

1. Phase 1 direct fetch fails with `NoTranscriptFound`
2. `_rate_limiter.call()` raises immediately (not 429, not transient)
3. `fetch_youtube_transcript` calls `_is_permanently_unavailable_error(e)` → `False`
4. Logs error: "Direct fetch failed ... trying list fallback"
5. `asyncio.sleep(2.0)`
6. Phase 2 list call succeeds (e.g. finds `zh-TW`)
7. Returns fetched transcript
8. LRC generation proceeds via YouTube transcript path

---

## Acceptance Criteria

- `_is_permanently_unavailable_error()` correctly identifies the 5 short-circuit
  types (`TranscriptsDisabled`, `VideoUnavailable`, `VideoUnplayable`,
  `InvalidVideoId`, `AgeRestricted`) by class name.
- `_is_permanently_unavailable_error()` returns `False` for `NoTranscriptFound`
  and all other youtube-transcript-api exception types.
- `fetch_youtube_transcript()` does not call `YouTubeTranscriptApi.list()` when
  a permanent error is encountered.
- `fetch_youtube_transcript()` continues to call `YouTubeTranscriptApi.list()`
  when `NoTranscriptFound` is raised (primary use case of the fallback).
- New tests pass:
  ```bash
  cd ops/analysis-service && uv run --extra dev pytest tests/test_youtube_transcript.py -v
  ```
- Full suite passes:
  ```bash
  cd ops/analysis-service && uv run --extra dev pytest tests/ -v
  ```
- No regressions: existing `TestFetchYoutubeTranscript` tests still pass unchanged.
- Wire log: short-circuited jobs log at `WARNING` level with message "permanently unavailable" and do **not** emit the secondary "List fallback also failed" error.

---

## Risks & Mitigations

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Class-name detection misses a new exception subtype added in a future library version | Low | `youtube_transcript_api` exception names are stable (v1.0 → v1.2 unchanged). Adding the unknown type to the set is a one-line change. The helper returns `False` for unknowns → conservative fallback to current behavior. |
| `YouTubeTranscriptApi.list()` would have succeeded for a "permanent" error (false positive) | Very Low | For `TranscriptsDisabled`/`VideoUnavailable`/`InvalidVideoId`/`AgeRestricted`/`VideoUnplayable`, the video state is server-side and cannot succeed via a different endpoint. Library confirms these exceptions are raised uniformly by both `.fetch()` and `.list()`. |
| `NoTranscriptFound` cases (Romanian-only videos in the logs) are not short-circuited | Accepted | `NoTranscriptFound` for a Romanian-only video will still fail at the list phase, but the fallback is correctly attempted — it may find a usable transcript in the list. This is the intended use case of the fallback. The savings from short-circuiting would require language-aware filtering, which is out of scope and error-prone. |
| Test mocks use class-name-only exceptions that don't inherit from the real youtube-transcript-api `CouldNotRetrieveTranscript` | Low | The class-name detection in `_is_permanently_unavailable_error` is intentionally type-agnostic. The mock exceptions only need the right class name. |
| In free-only mode, a job that previously would retry via list phase now skips straight to ASR | Low (savings, no regression) | The list phase never succeeded for these videos. The queue's retry loop only catches `YouTubeRateLimitedError` (429), not `YouTubeTranscriptError`, so permanent errors already triggered ASR fallback before this change. |

---

## Alternative Considered and Rejected

### Alternative: Import the real exception classes at module level

```python
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    VideoUnavailable,
    ...
)

_PERMANENTLY_UNAVAILABLE = (TranscriptsDisabled, VideoUnavailable, ...)

def _is_permanently_unavailable_error(e):
    return isinstance(e, _PERMANENTLY_UNAVAILABLE)
```

**Rejected because:**
1. `youtube_transcript_api._errors` is a "private" (underscore-prefixed) module.
   Importing from it creates a hidden coupling that may break on library
   reorganization.
2. Module-level import would introduce a library initialization side-effect
   at import time of `youtube_transcript.py`, breaking the existing lazy-import
   pattern used throughout this file (import inside `_fetch_direct` /
   `_fetch_via_list` functions).
3. Class-name detection is a well-established Python pattern for third-party
   exception handling across library versions (e.g., sqlalchemy uses similar
   patterns in its dialect shims).
4. Tests with class-name mocks are simpler: no need for real library imports.

### Alternative: String-based detection on `CAUSE_MESSAGE`

```python
PERMANENT_MARKERS = ("Subtitles are disabled", "The video is no longer available", ...)

def _is_permanently_unavailable_error(e):
    return any(m in str(exc) for m in PERMANENT_MARKERS for exc in _chain(e))
```

**Rejected because:**
- The library may localize or revise messages; class names are more stable.
- String matching causes false positives (e.g., a generic request error whose
  message happens to contain "disabled").
- Class-name check is structurally stronger.

---

## Validation Steps (Manual, post-merge)

1. Re-run an LRC batch job against the same video IDs that hit
   `Subtitles are disabled` earlier (e.g. `-DA1bhTnOz0`, `C6HSl4Nhgdg`,
   `Nxd2Z6-pDCQ`, `86t6JIxlbEc`, `_I4wvMSOiw8`, `BvqsBBSqKAo`).
2. Confirm the "trying list fallback" log line is absent for those jobs.
3. Confirm the "skipping list fallback" warning appears instead.
4. Confirm the total `LRC processing=` queue wait time decreases next batch.
