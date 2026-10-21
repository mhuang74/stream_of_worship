"""YouTube transcript-based LRC generation.

Downloads YouTube captions via youtube-transcript-api, corrects them
against official lyrics using an LLM, and produces timestamped LRC lines.

This is the primary (preferred) path for LRC generation — YouTube often
has human-curated subtitles with accurate timing. The Whisper+LLM pipeline
serves as fallback when YouTube transcripts are unavailable.
"""

import asyncio
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from ..config import settings
from ..workers.exceptions import LLMConfigError
from .llm_trace import LLMTrace
from .lrc import LRCLine, LRCWorkerError

logger = logging.getLogger(__name__)

_TIMESTAMP_MATCH_TOLERANCE = 0.011  # LRC timestamps are 2-decimal truncated


class RotatingProxyConfig:
    """Proxy config backed by a pool of proxy URLs with rotation.

    Accepts a list of proxy URLs (e.g. one per port of a port-range
    residential proxy). Each call to :meth:`to_requests_dict` picks a random
    URL from the pool, so every constructed YouTubeTranscriptApi instance /
    app-level retry may use a different proxy port — useful when individual
    ports of the pool have different success rates.

    Also sets:
    - prevent_keeping_connections_alive=True (triggers IP rotation per request)
    - retries_when_blocked=N (retries on HTTP 429, also triggers rotation)

    This is essential for rotating residential proxy services where each retry
    gets a new IP from the pool.
    """

    def __init__(self, urls: list[str], retries_when_blocked: int = 3):
        if not urls:
            raise ValueError("RotatingProxyConfig requires at least one proxy URL")
        self._urls = list(urls)
        self._retries_when_blocked = retries_when_blocked

    def to_requests_dict(self) -> dict:
        url = random.choice(self._urls)
        return {"http": url, "https": url}

    @property
    def prevent_keeping_connections_alive(self) -> bool:
        return True

    @property
    def retries_when_blocked(self) -> int:
        return self._retries_when_blocked


_PROXY_PORT_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")
_MAX_PROXY_POOL_SIZE = 64


def _parse_proxy_pool(proxy_url: str) -> list[str]:
    """Expand a proxy URL with a port range into a list of proxy URLs.

    Supports ``http://user:pass@host:10001-10010`` — expands to one URL per
    port (10001..10010 inclusive). A URL without a port range is returned as
    a single-element pool unchanged.

    Parsing deliberately avoids :mod:`urllib.parse` URL splitting: proxy
    passwords commonly contain characters (e.g. ``:``) that would break
    generic URL parsing. Instead only the trailing ``host:port`` segment is
    inspected.

    Raises:
        ValueError: if the range is inverted (low > high) or the resulting
            pool exceeds 64 URLs. The redacted URL is included in the message.
    """
    redacted = _redact_proxy_url(proxy_url)
    rest, sep, hostport = proxy_url.rpartition("@")
    prefix = rest + sep  # "" when no "@": the whole string is hostport
    host, _, port_segment = hostport.rpartition(":")
    match = _PROXY_PORT_RANGE_RE.match(port_segment)
    if not match:
        return [proxy_url]
    low, high = int(match.group(1)), int(match.group(2))
    if low > high:
        raise ValueError(f"Invalid proxy port range (low > high): {redacted}")
    urls = [f"{prefix}{host}:{port}" for port in range(low, high + 1)]
    if len(urls) > _MAX_PROXY_POOL_SIZE:
        raise ValueError(
            f"Proxy port range too large ({len(urls)} ports, max {_MAX_PROXY_POOL_SIZE}): {redacted}"
        )
    return urls


def _redact_proxy_url(proxy_url: str) -> str | None:
    """Redact userinfo credentials from a proxy URL for logging.

    Keeps scheme + host:port, replaces the userinfo part with ``***``.
    Returns None when the URL shape is unexpected (no ``@`` separator) so
    callers can render "(not set)"-style fallbacks.
    """
    rest, sep, hostport = proxy_url.rpartition("@")
    if not sep or ":" not in hostport:
        return "***"
    scheme, _, _rest = rest.partition("://")
    if not _rest:  # no "://" — unexpected shape; redact everything
        return "***"
    return f"{scheme}://***@{hostport}"


class YouTubeTranscriptError(LRCWorkerError):
    """Raised when YouTube transcript fetch or processing fails."""

    pass


class YouTubeRateLimitedError(YouTubeTranscriptError):
    """Raised when YouTube transcript fetch fails due to rate limiting (429),
    an open circuit breaker, or transient proxy/connection errors exhausting
    their retries. Retryable — caller may wait and retry."""

    pass


class LLMCorrectionError(YouTubeTranscriptError):
    """Raised when the LLM correction call fails (retries exhausted or a
    non-retryable LLM API error). Callers must NOT fall back to ASR: the ASR
    path also requires LLM correction, so fallback cannot succeed while the
    LLM API is failing. The job should fail instead."""

    pass


def _is_rate_limited_error(e: Exception) -> bool:
    """Check if an exception is caused by YouTube rate limiting (HTTP 429).

    Strategy 0: Class-name match on youtube-transcript-api block errors. The
    library raises IpBlocked(video_id) on HTTP 429 with no status_code/code
    attribute and no "429" in the message, so the numeric checks below never
    match it. Class-name matching (not isinstance) because we avoid importing
    the library at module level.

    Then tries three detection strategies in order:
    1. Check for HTTP 429 status_code on urllib3/requests exceptions.
    2. Check the exception string and its __cause__ chain for "429".
    3. Fall back to False (non-429 error — do not retry).

    The youtube-transcript-api library (via urllib3) raises errors like:
        ResponseError('too many 429 error responses')

    Strategy 0 is most robust; strategy 1 is more robust than the
    string-based fallback in strategy 2.
    """
    # Strategy 0: youtube-transcript-api block errors (IpBlocked / RequestBlocked)
    blocked_type_names = {"IpBlocked", "RequestBlocked"}
    visited: set[int] = set()
    exc: Exception = e
    while exc is not None and id(exc) not in visited:
        visited.add(id(exc))
        if any(t.__name__ in blocked_type_names for t in type(exc).__mro__):
            return True
        exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)

    # Strategy 1: Check for status_code attribute (urllib3 / requests)
    visited = set()
    exc = e
    while exc is not None and exc not in visited:
        visited.add(exc)
        if hasattr(exc, "status_code") and exc.status_code == 429:
            return True
        if hasattr(exc, "code") and exc.code == 429:
            return True
        exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)

    # Strategy 2: String-based fallback
    error_str = str(e)
    if "429" in error_str:
        return True
    visited = set()
    cause = e.__cause__ or e.__context__
    while cause is not None and cause not in visited:
        visited.add(cause)
        if "429" in str(cause):
            return True
        cause = cause.__cause__ or cause.__context__
    return False


def _is_transient_connection_error(e: Exception) -> bool:
    """Check if an exception is a transient connection-level error (SSL, network).

    These errors are common with rotating residential proxies where individual
    exit IPs may have TLS handshake issues. They are transient — the next
    request through a different IP typically succeeds.

    Detects:
    - SSLError from urllib3/requests (via type name and message)
    - ConnectionError variants
    - ProxyError from requests (proxy tunnel failures, e.g. dead proxy port)
    - "Max retries exceeded" caused by SSL/connection failures
    - Proxy tunnel/connection failure messages ("Tunnel connection failed",
      "Unable to connect to proxy", "Proxy error", "Bad Gateway")
    """
    visited: set[int] = set()
    exc: Optional[Exception] = e
    ssl_markers = (
        "ssl",
        "bad_extension",
        "wrong_version_number",
        "record layer failure",
        "tls",
        "tunnel connection failed",
        "unable to connect to proxy",
        "proxy error",
        "bad gateway",
    )
    conn_markers = (
        "connection reset",
        "connection aborted",
        "broken pipe",
        "read timed out",
    )
    while exc is not None and id(exc) not in visited:
        visited.add(id(exc))
        type_name = type(exc).__name__.lower()
        if "ssl" in type_name or "connectionerror" in type_name or "proxyerror" in type_name:
            return True
        error_str = str(exc).lower()
        if any(m in error_str for m in ssl_markers):
            return True
        if any(m in error_str for m in conn_markers):
            return True
        exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    return False


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
        if any(t.__name__ in permanently_unavailable_types for t in type(exc).__mro__):
            return True
        exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    return False


class _YouTubeRateLimiter:
    """Module-level rate limiter for YouTube transcript API calls.

    Provides four layers of protection against YouTube API rate limiting:
    1. Concurrency semaphore (limits simultaneous API calls)
    2. Min-interval throttle (ensures spacing between requests)
    3. Retry with exponential backoff + jitter on HTTP 429
    4. Circuit breaker with auto-recovery after cooldown

    Lazily initializes asyncio primitives on first use (within the event loop).
    """

    def __init__(self):
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._interval_lock: Optional[asyncio.Lock] = None
        self._state_lock: Optional[asyncio.Lock] = None
        self._last_request_time: float = 0.0
        self._consecutive_429_count: int = 0
        self._circuit_open_until: float = 0.0  # monotonic time

    def _ensure_initialized(self) -> None:
        """Lazily initialize asyncio primitives on first use."""
        if self._interval_lock is None:
            max_concurrent = settings.SOW_YOUTUBE_TRANSCRIPT_MAX_CONCURRENT
            # MAX_CONCURRENT=0 disables the semaphore entirely
            if max_concurrent > 0:
                self._semaphore = asyncio.Semaphore(max_concurrent)
            self._interval_lock = asyncio.Lock()
            self._state_lock = asyncio.Lock()

    async def _is_circuit_open(self) -> bool:
        """Check if the circuit breaker is currently open.

        If the cooldown has expired, reset the 429 count so the breaker
        can recover (the next fetch probes YouTube and resets fully on success).
        """
        if self._circuit_open_until == 0.0:
            return False
        if time.monotonic() >= self._circuit_open_until:
            # Cooldown expired — reset so the next 429 doesn't instantly re-trip
            async with self._state_lock:
                # Double-check after acquiring lock
                if time.monotonic() >= self._circuit_open_until:
                    if self._consecutive_429_count > 0:
                        logger.info(
                            "YouTube transcript circuit breaker cooldown expired — "
                            "resetting 429 count (was %d), next fetch will probe YouTube",
                            self._consecutive_429_count,
                        )
                    self._consecutive_429_count = 0
                    self._circuit_open_until = 0.0
            return False
        return True

    async def _open_circuit(self) -> None:
        """Open the circuit breaker for the configured cooldown period."""
        cooldown = (
            settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN_FREE
            if settings.SOW_FREE_ONLY_MODE
            else settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN
        )
        async with self._state_lock:
            self._circuit_open_until = time.monotonic() + cooldown
            logger.warning(
                "YouTube transcript circuit breaker OPENED after %d consecutive 429s, "
                "cooldown %ds — all YouTube transcript fetches will skip to fallback",
                self._consecutive_429_count,
                cooldown,
            )

    async def _reset_circuit(self) -> None:
        """Reset the circuit breaker on a successful request."""
        async with self._state_lock:
            if self._consecutive_429_count > 0:
                self._consecutive_429_count = 0
                self._circuit_open_until = 0.0
                logger.info("YouTube transcript circuit breaker RESET after successful request")

    async def _enforce_min_interval(self) -> None:
        """Sleep if the last request was too recent (min-interval throttle).

        Uses an asyncio.Lock to serialize the timestamp check so that
        concurrent callers are spaced out correctly.
        """
        async with self._interval_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            min_interval = (
                settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS_FREE
                if settings.SOW_FREE_ONLY_MODE
                else settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS
            )
            # Add jitter to prevent thundering herd when multiple jobs wake from cooldown
            if settings.SOW_FREE_ONLY_MODE:
                jitter = min_interval * 0.25
                min_interval = random.uniform(min_interval - jitter, min_interval + jitter)
            if elapsed < min_interval:
                wait = min_interval - elapsed
                logger.debug("YouTube rate limit: spacing request, sleeping %.2fs", wait)
                await asyncio.sleep(wait)
            self._last_request_time = time.monotonic()

    async def call(self, fn: Callable, *, description: str = "") -> Any:
        """Execute a YouTube API call through the rate limiter.

        Args:
            fn: Synchronous callable that performs the YouTube API call.
                Will be run in the default executor.
            description: Human-readable description for logging.

        Returns:
            The result of fn().

        Raises:
            YouTubeRateLimitedError: If the circuit breaker is open, or if all
                retries are exhausted on 429. These are retryable — the caller
                may wait for cooldown and retry.
            YouTubeTranscriptError: If fn() raises a non-429 error, or if the
                rate limiter is exhausted for unexpected reasons.
        """
        self._ensure_initialized()

        # Layer 1: Circuit breaker check
        if await self._is_circuit_open():
            remaining = self._circuit_open_until - time.monotonic()
            raise YouTubeRateLimitedError(
                f"YouTube transcript circuit breaker is open "
                f"(cooldown: {remaining:.0f}s remaining) — skipping {description}"
            )

        loop = asyncio.get_running_loop()
        if settings.SOW_FREE_ONLY_MODE:
            threshold = settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD_FREE
            max_retries = settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES_FREE
            base_delay = settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY_FREE
        else:
            threshold = settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD
            max_retries = settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES
            base_delay = settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY

        for attempt in range(max_retries + 1):
            # Layer 2: Circuit breaker check (re-check each iteration)
            if await self._is_circuit_open():
                remaining = self._circuit_open_until - time.monotonic()
                raise YouTubeRateLimitedError(
                    f"YouTube transcript circuit breaker is open "
                    f"(cooldown: {remaining:.0f}s remaining) — skipping {description}"
                )

            # Layer 3: Concurrency semaphore + min-interval throttle
            # Only wrap the API call, NOT the backoff sleep, so other tasks
            # aren't blocked during retry delays.
            try:
                if self._semaphore is not None:
                    async with self._semaphore:
                        await self._enforce_min_interval()
                        result = await loop.run_in_executor(None, fn)
                else:
                    # MAX_CONCURRENT=0: no semaphore, but still enforce min-interval
                    await self._enforce_min_interval()
                    result = await loop.run_in_executor(None, fn)
                # Success — reset circuit breaker
                await self._reset_circuit()
                return result
            except Exception as e:
                if _is_rate_limited_error(e):
                    # Layer 4: 429 retry with backoff
                    async with self._state_lock:
                        self._consecutive_429_count += 1
                        logger.warning(
                            "YouTube API rate limited (429), attempt %d/%d for %s: %s",
                            attempt + 1,
                            max_retries + 1,
                            description,
                            e,
                        )
                        should_open = self._consecutive_429_count >= threshold

                    # Open circuit breaker outside the lock to avoid deadlock
                    # (_open_circuit acquires the same lock)
                    if should_open:
                        await self._open_circuit()
                        raise YouTubeRateLimitedError(
                            f"YouTube API rate limited — circuit breaker opened "
                            f"after {self._consecutive_429_count} consecutive 429s: {e}"
                        ) from e

                    # Retry with exponential backoff + jitter (if attempts remain)
                    if attempt < max_retries:
                        delay = min(base_delay * (2**attempt), 60.0)
                        delay += random.uniform(0, delay * 0.25)
                        logger.info(
                            "Retrying YouTube API call for %s in %.1fs",
                            description,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue

                    # All retries exhausted
                    raise YouTubeRateLimitedError(
                        f"YouTube API rate limited after {max_retries + 1} "
                        f"attempts for {description}: {e}"
                    ) from e

                if _is_transient_connection_error(e):
                    # SSL/connection/proxy errors from rotating proxy — retry
                    # with backoff. These are proxy-level, not YouTube rate
                    # limiting, so the circuit breaker is NOT tripped.
                    if attempt >= max_retries:
                        # Retries exhausted — surface as retryable so queue.py's
                        # free-only-mode loop can wait and retry YouTube instead
                        # of falling back to ASR while the proxy is down.
                        raise YouTubeRateLimitedError(
                            f"YouTube API transient connection/proxy errors after "
                            f"{max_retries + 1} attempts for {description}: {e}"
                        ) from e
                    delay = min(base_delay * (2**attempt), 30.0)
                    delay += random.uniform(0, delay * 0.25)
                    logger.info(
                        "YouTube API transient connection error for %s, "
                        "attempt %d/%d, retrying in %.1fs: %s",
                        description,
                        attempt + 1,
                        max_retries + 1,
                        delay,
                        e,
                    )
                    await asyncio.sleep(delay)
                    continue

                # Non-retriable error — propagate immediately
                raise

        # Should not reach here, but safety net
        raise YouTubeTranscriptError(
            f"YouTube API rate limiter exhausted all retries for {description}"
        )

    async def is_circuit_open(self) -> bool:
        """Public accessor — also performs cooldown-expiry reset (see _is_circuit_open)."""
        return await self._is_circuit_open()

    def remaining_cooldown(self) -> float:
        """Seconds remaining in the current cooldown (0.0 if closed)."""
        if self._circuit_open_until == 0.0:
            return 0.0
        return max(0.0, self._circuit_open_until - time.monotonic())


_rate_limiter = _YouTubeRateLimiter()


async def wait_for_youtube_cooldown_if_open(
    max_heartbeat_seconds: float = 60.0,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> bool:
    """Block (async) until the YouTube circuit breaker closes, or until cancelled.

    Sleeps in increments of ``max_heartbeat_seconds`` so the caller can emit
    heartbeats (refresh job.updated_at) between sleeps and check cancellation.

    Args:
        max_heartbeat_seconds: Max seconds to sleep per iteration before
            returning so the caller can heartbeat. Default 60s.
        is_cancelled: Optional callable returning True if the job was cancelled;
            checked between sleeps. If it returns True, this function returns
            immediately with the breaker's current state.

    Returns:
        True when the breaker is closed (cooldown expired or was never open).
        The caller should re-check cancellation before retrying.
    """
    while await _rate_limiter.is_circuit_open():
        if is_cancelled is not None and is_cancelled():
            return not await _rate_limiter.is_circuit_open()
        remaining = _rate_limiter.remaining_cooldown()
        # If breaker just closed, don't sleep an extra second
        if remaining <= 0.0:
            return True
        sleep_for = min(max_heartbeat_seconds, max(remaining, 0.1))
        await asyncio.sleep(sleep_for)
    return True


def extract_video_id(youtube_url: str) -> Optional[str]:
    """Extract YouTube video ID from URL.

    Supports standard youtube.com/watch URLs and youtu.be short URLs.

    Args:
        youtube_url: YouTube URL

    Returns:
        Video ID or None if not found
    """
    # Handle youtu.be short URLs
    if "youtu.be/" in youtube_url:
        match = re.search(r"youtu\.be/([^/?]+)", youtube_url)
        if match:
            return match.group(1)

    # Handle standard youtube.com URLs
    if "youtube.com/watch" in youtube_url:
        match = re.search(r"[?&]v=([^&]+)", youtube_url)
        if match:
            return match.group(1)

    return None


def _format_transcript_text(transcript: list) -> str:
    """Format transcript snippets as timestamped text for LLM prompt.

    Args:
        transcript: List of transcript snippet objects with .text, .start attributes

    Returns:
        Formatted transcript text with timestamps
    """
    lines = []
    for snippet in transcript:
        start = snippet.start
        minutes = int(start // 60)
        seconds = start % 60
        timestamp = f"{minutes:02d}:{seconds:05.2f}"
        lines.append(f"{timestamp}\n{snippet.text}\n")
    return "\n".join(lines)


STRICT_REQUIREMENTS_BLOCK = """\
## Additional Requirements

1. Transcript lines are frequently fragments of one sung phrase. Whenever a run of
   consecutive transcribed lines together forms ONE lyric line from the Official
   Lyrics, merge them into a SINGLE output line using the FIRST merged line's timestamp.
2. If several transcribed lines share the exact same timestamp, merge them into one
   output line at that timestamp.
3. Never emit a partial phrase: each output line's text must be one full lyric line
   from the Official Lyrics — or, when one transcribed cue covers two or more official
   lyric lines, those complete lines joined with a single space (repeated phrases
   allowed).
4. Lines in the Official Lyrics consisting entirely of a [bracketed] label are
   section tags (metadata), not sung phrases — never emit them as lyric lines.
5. Repetition is not duplication: when several cues carry the same lyric line, emit that line
   once per cue at the cue's own timestamp — the final chorus of a worship song is usually
   repeated. Never drop a cue merely because its text appeared earlier; drop a cue only when
   its text matches no line of the Official Lyrics."""
WORKED_EXAMPLE_ZH = """\
## Worked Example

This example is from a different song. Learn the merge and drop behavior it shows;
never copy its lyric text into your output.

Transcribed subtitle (excerpt):

```
00:05.23
“A New Beginning”

00:08.24
Lyrics and Music by David Yu

00:15.91
Laying down all my sorrow and shame
Laying down all my sin and my pain

00:23.25
Lord, I come to You, as I am
```

Official lyrics (excerpt):

```
[Verse]
放下一切憂傷和羞愧
放下一切痛苦和纏累
主我來到祢施恩座前
```

Correct output:

```
[00:15.91] 放下一切憂傷和羞愧 放下一切痛苦和纏累
[00:23.25] 主我來到祢施恩座前
```

Why: the 00:05.23 and 00:08.24 cues are a song title and a credits card — they match
no lyric line, so they produce NO output lines. The 00:15.91 cue holds two
transcribed lines that share one timestamp and together cover TWO official lyric
lines, so they merge into ONE output line at 00:15.91, both texts joined with a
single space. The 00:23.25 cue covers one official line and keeps its own
timestamp.

Repeated ending (same example song, later excerpt):

Transcribed subtitle:
```
00:58.10
I will live to bring praise to Your name

01:04.20
I will live to bring praise to Your name
```

The Official Lyrics list that line once.

Correct output:

```
[00:58.10] 我活著要稱頌祢
[01:04.20] 我活著要稱頌祢
```

Why: the 01:04.20 cue repeats the 00:58.10 text. It is a second sung repeat, not a duplicate to
remove — it keeps its own output line and its own timestamp."""


def build_correction_prompt(
    transcript_text: str,
    official_lyrics: list[str],
    language: str = "zh",
) -> str:
    """Build LLM prompt for lyrics correction.
    Embeds the "Additional Requirements" block (merge fragment runs, dedup
    identical timestamps, full phrases or cue-covered joins, never emit section
    tags) adopted from the eval-models-for-fixing-youtube-transcription
    bake-off. The zh template also embeds a grounded worked example
    (WORKED_EXAMPLE_ZH) demonstrating merge and drop behavior.

    Args:
        transcript_text: Formatted transcript with timestamps
        official_lyrics: List of official lyric lines

    Returns:
        Correction prompt string
    """
    lyrics_str = "\n".join(official_lyrics)

    if language == "en":
        return f"""You are a lyrics correction assistant for English worship songs.

## Task
Compare the subtitle transcription against the official English lyrics. Correct each transcribed line to the matching official lyric while preserving the original timecodes.

## Rules
1. Each transcribed line corresponds to a sung phrase in the official lyrics. Replace the transcribed text with the matching official English lyric text.
2. Songs often repeat sections (verse, chorus, bridge, tag). Keep all repeated phrases with their timecodes.
3. Preserve the number of matching sung lyric lines and their timecodes. Only correct text content.
4. Preserve casing, punctuation, contractions, and line text from the official lyrics.
5. If a transcribed line clearly does not match sung lyrics (instrumental, audience noise, speech), remove that line entirely.
6. Never collapse or delete a cue because its text repeats an earlier line — a repeated chorus is
   sung again and must appear again with its own timestamp.

## Transcribed Subtitle
```
{transcript_text}
```

## Official Lyrics
```
{lyrics_str}
```

{STRICT_REQUIREMENTS_BLOCK}

## Output Format
Output ONLY corrected lines in LRC format, one per line:
[mm:ss.xx] English lyric

No blank lines, no commentary, no markdown."""

    return f"""You are a lyrics correction assistant for Chinese worship songs.

## Task
Compare the auto-generated subtitle transcription (which may be in the wrong language or contain errors) against the published Chinese lyrics. Correct each transcribed line to the matching Chinese lyrics while preserving the original timecodes.

## Rules
1. Each transcribed line corresponds to a phrase in the published lyrics. Replace the transcribed text with the correct Chinese lyrics for that phrase.
2. Songs often repeat sections (verse, chorus). The transcription reflects what was actually sung — keep all repeated phrases with their timecodes.
3. Preserve the number of lines and their timecodes exactly. Only correct the text content.
4. If a transcribed line doesn't match any published lyrics (e.g. instrumental, audience noise), remove that line entirely.
5. Never collapse or delete a cue because its text repeats an earlier line — a repeated chorus is
   sung again and must appear again with its own timestamp.

## Transcribed Subtitle (auto-generated)
```
{transcript_text}
```

## Published Lyrics (official, one unique phrase per line)
```
{lyrics_str}
```

{STRICT_REQUIREMENTS_BLOCK}

{WORKED_EXAMPLE_ZH}

## Output Format
Output ONLY corrected lines in LRC format, one per line:
[mm:ss.xx] 中文歌词

No blank lines, no commentary, no markdown."""


def parse_lrc_response(response: str) -> List[LRCLine]:
    """Parse LLM response into LRCLine objects.

    Extracts valid LRC-formatted lines from the LLM response.

    Args:
        response: LLM response text

    Returns:
        List of LRCLine objects

    Raises:
        ValueError: If no valid LRC lines found
    """
    lrc_pattern = re.compile(r"^\[(\d{2}):(\d{2}\.\d{2})\]\s*(.+)")
    lines = []

    for line in response.splitlines():
        line = line.strip()
        match = lrc_pattern.match(line)
        if match:
            minutes = int(match.group(1))
            seconds = float(match.group(2))
            text = match.group(3).strip()
            time_seconds = minutes * 60 + seconds
            lines.append(LRCLine(time_seconds=time_seconds, text=text))

    if not lines:
        raise ValueError("No valid LRC lines found in LLM response")

    return lines


@dataclass(frozen=True)
class TranscriptCue:
    """A single transcript cue with timing (seconds) from YouTube."""

    start: float
    duration: float

    @property
    def end(self) -> float:
        return self.start + self.duration


def _extract_cue_timings(transcript: Any) -> List[TranscriptCue]:
    """Extract (start, duration) timings from a fetched YouTube transcript.

    Tolerates both snippet objects (.start/.duration attributes) and raw dicts.
    Durations <= 0 are clamped to 0 so cue ends never precede their starts.
    """
    snippets: Any = transcript
    if hasattr(transcript, "snippets"):
        snippets = transcript.snippets
    cues: List[TranscriptCue] = []
    for snippet in snippets:
        if isinstance(snippet, dict):
            start = float(snippet.get("start", 0.0))
            duration = float(snippet.get("duration", 0.0))
        else:
            start = float(getattr(snippet, "start", 0.0))
            duration = float(getattr(snippet, "duration", 0.0))
        cues.append(TranscriptCue(start=start, duration=max(0.0, duration)))
    return cues


def insert_gap_placeholder_lines(
    lrc_lines: List[LRCLine],
    cues: List[TranscriptCue],
    tempo_bpm: Optional[float],
) -> List[LRCLine]:
    """Insert blank placeholder lines into long instrumental gaps.

    For each pair of consecutive LRC lines, computes the previous line's audible
    end as the end of the last transcript cue starting before the next LRC
    timestamp (span semantics: merged and dropped cues are all covered). When
    the gap next_start - line_end exceeds SOW_LRC_GAP_THRESHOLD_BEATS, inserts
    an empty-text LRCLine at line_end + SOW_LRC_GAP_PLACEHOLDER_BEATS. The
    rendered lyrics video treats an empty-text line as a blank-screen marker
    (previous lyric fades, next lyric previews 4 beats before its timestamp).

    Skips insertion when tempo_bpm is absent or non-positive, when fewer than
    two LRC lines exist, or when no cues match a line's timestamp (duration
    unknown). Only interior gaps are handled — intro and outro tails have no
    previous/next line and are already handled by the render worker's intro
    card and last-lyric fade.

    Args:
        lrc_lines: Corrected LRC lines from the LLM (start timestamps only).
        cues: Transcript cue timings (start, duration in seconds).
        tempo_bpm: Recording tempo; None/<=0 disables insertion.

    Returns:
        New list of LRCLine objects sorted by timestamp, placeholders included.
    """
    if not lrc_lines or len(lrc_lines) < 2:
        return list(lrc_lines)
    if not cues:
        logger.warning("Gap placeholder: no transcript cues available — skipping")
        return list(lrc_lines)
    if not tempo_bpm or tempo_bpm <= 0:
        logger.info("Gap placeholder: no tempo_bpm provided — skipping insertion")
        return list(lrc_lines)
    threshold_seconds = settings.SOW_LRC_GAP_THRESHOLD_BEATS * 60.0 / tempo_bpm
    placeholder_seconds = settings.SOW_LRC_GAP_PLACEHOLDER_BEATS * 60.0 / tempo_bpm
    if threshold_seconds <= 0:
        return list(lrc_lines)

    sorted_lines = sorted(lrc_lines, key=lambda ln: ln.time_seconds)
    sorted_cues = sorted(cues, key=lambda cue: cue.start)

    out: List[LRCLine] = []
    inserted = 0
    for i, line in enumerate(sorted_lines):
        out.append(line)
        if i + 1 >= len(sorted_lines):
            continue
        next_start = sorted_lines[i + 1].time_seconds

        # Line end = last cue-end among cues starting in [line_start, next_start).
        line_end: Optional[float] = None
        for cue in sorted_cues:
            if cue.start < line.time_seconds - _TIMESTAMP_MATCH_TOLERANCE:
                continue
            if cue.start >= next_start - _TIMESTAMP_MATCH_TOLERANCE:
                break
            line_end = cue.end
        if line_end is None:
            logger.warning(
                "Gap placeholder: no cue matches timestamp %.2fs — skipping gap",
                line.time_seconds,
            )
            continue

        gap = next_start - line_end
        if gap <= threshold_seconds + 1e-6:
            continue

        placeholder_time = line_end + placeholder_seconds
        if placeholder_time >= next_start - 1e-6:
            placeholder_time = next_start - 0.01

        if placeholder_time <= line.time_seconds:
            continue
        out.append(LRCLine(time_seconds=placeholder_time, text=""))
        inserted += 1
        logger.info(
            "Gap placeholder inserted at %.2fs (line end %.2fs, next %.2fs, gap %.2fs > %.2fs beats-threshold)",
            placeholder_time,
            line_end,
            next_start,
            gap,
            threshold_seconds,
        )

    logger.info(
        "Gap placeholder: %d inserted across %d LRC lines (tempo %.1f BPM)",
        inserted,
        len(lrc_lines),
        tempo_bpm,
    )
    return out


ZH_LANG_CODES = ["zh-Hant", "zh-TW", "zh-Hans", "zh-CN", "zh-HK", "zh"]
EN_LANG_CODES = ["en-US", "en"]

DEFAULT_LANGUAGES = ZH_LANG_CODES + EN_LANG_CODES


def language_preference_codes(language: str) -> list[str]:
    if language == "en":
        return EN_LANG_CODES + ZH_LANG_CODES
    return ZH_LANG_CODES + EN_LANG_CODES


def _build_proxy_config() -> Optional[RotatingProxyConfig]:
    """Build proxy config from settings if proxy is configured.

    Returns:
        RotatingProxyConfig if SOW_YOUTUBE_PROXY is set, None otherwise
    """
    if not settings.SOW_YOUTUBE_PROXY:
        return None
    urls = _parse_proxy_pool(settings.SOW_YOUTUBE_PROXY)
    return RotatingProxyConfig(
        urls=urls,
        retries_when_blocked=settings.SOW_YOUTUBE_PROXY_RETRIES,
    )


def _find_best_transcript(transcript_list: Any, language: str = "zh") -> Optional[Any]:
    """Find the best available transcript from a TranscriptList.

    Priority order follows the resolved LRC language. Manual captions are preferred
    within each language before generated captions.

    Args:
        transcript_list: Iterable of Transcript objects from YouTubeTranscriptApi.list()

    Returns:
        A Transcript object, or None if no suitable transcript found
    """
    best_zh_manual = None
    best_zh_generated = None
    best_en_manual = None
    best_en_generated = None

    for transcript in transcript_list:
        code = transcript.language_code
        is_generated = transcript.is_generated

        if code in ZH_LANG_CODES or code.startswith("zh"):
            if not is_generated and best_zh_manual is None:
                best_zh_manual = transcript
            elif is_generated and best_zh_generated is None:
                best_zh_generated = transcript
        elif code in EN_LANG_CODES or code.startswith("en"):
            if not is_generated and best_en_manual is None:
                best_en_manual = transcript
            elif is_generated and best_en_generated is None:
                best_en_generated = transcript

    if language == "en":
        return best_en_manual or best_en_generated or best_zh_manual or best_zh_generated
    return best_zh_manual or best_zh_generated or best_en_manual or best_en_generated


async def fetch_youtube_transcript(
    video_id: str,
    languages: Optional[List[str]] = None,
    language: str = "zh",
) -> list:
    """Download captions from YouTube via youtube-transcript-api.

    Two-phase approach:
    1. Try fetching directly with the given language codes (fast path).
    2. On failure, list all available transcripts and pick the best one
       (handles region-specific codes like zh-TW, zh-CN that differ from
       BCP-47 codes like zh-Hant, zh-Hans).

    Args:
        video_id: YouTube video ID
        languages: Language codes to try (default: zh-Hant, zh-TW, zh-Hans,
            zh-CN, zh-HK, zh, en-US, en)

    Returns:
        FetchedTranscript object with .snippets

    Raises:
        YouTubeTranscriptError: If transcript cannot be fetched
    """
    if languages is None:
        languages = language_preference_codes(language)

    proxy_config = _build_proxy_config()
    if proxy_config:
        redacted = _redact_proxy_url(settings.SOW_YOUTUBE_PROXY) or "(unparseable)"
        logger.info(
            "Fetching YouTube transcript via proxy %s (%d port(s))",
            redacted,
            len(_parse_proxy_pool(settings.SOW_YOUTUBE_PROXY)),
        )
    else:
        logger.info("Fetching YouTube transcript (direct, no proxy)")

    def _fetch_direct():
        from youtube_transcript_api import YouTubeTranscriptApi

        ytt_api = YouTubeTranscriptApi(proxy_config=proxy_config)
        return ytt_api.fetch(video_id, languages=languages)

    def _fetch_via_list():
        from youtube_transcript_api import YouTubeTranscriptApi

        ytt_api = YouTubeTranscriptApi(proxy_config=proxy_config)
        transcript_list = ytt_api.list(video_id)
        best = _find_best_transcript(transcript_list, language=language)
        if best is None:
            return None
        logger.info(
            f"Found transcript via list fallback: {best.language_code} "
            f"({best.language}) generated={best.is_generated}"
        )
        return best.fetch()

    # Phase 1: Direct fetch with language codes
    try:
        transcript = await _rate_limiter.call(
            _fetch_direct, description=f"direct fetch for {video_id}"
        )
        if transcript:
            logger.info(f"Fetched {len(transcript)} transcript segments from YouTube")
            return transcript
    except YouTubeRateLimitedError:
        # Phase 1 already exhausted all transient/429 retries — the list
        # fallback would repeat the full backoff cycle against the same
        # (currently dead) proxy. Surface immediately.
        raise
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

    # Phase 2: List available transcripts and pick the best one
    try:
        transcript = await _rate_limiter.call(
            _fetch_via_list, description=f"list fallback for {video_id}"
        )
        if transcript:
            logger.info(
                f"Fetched {len(transcript)} transcript segments from YouTube (via list fallback)"
            )
            return transcript
        raise YouTubeTranscriptError(
            f"No suitable transcript found for video {video_id} (tried languages: {languages})"
        )
    except YouTubeTranscriptError:
        raise
    except Exception as e:
        logger.error(f"List fallback also failed for {video_id}: {e}")
        raise YouTubeTranscriptError(
            f"Failed to fetch YouTube transcript for video {video_id}: {e}"
        ) from e


async def _llm_correct(
    prompt: str,
    llm_model: str,
    *,
    trace: Optional[LLMTrace] = None,
) -> str:
    """Call LLM to correct YouTube transcript against official lyrics.

    Uses the shared rate-limit retry utility to handle HTTP 429 responses
    with exponential backoff and jitter, respecting the provider's
    ``retry_after`` guidance. Retries for up to
    ``SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS`` (default 20 min) before giving up.

    Args:
        prompt: Correction prompt
        llm_model: LLM model identifier

    Returns:
        LLM response text

    Raises:
        LLMConfigError: If LLM is not configured
        LLMCorrectionError: If LLM call fails after all retries, or on
            non-retryable errors. Callers must NOT fall back to ASR — the ASR
            path also requires LLM correction, so the job must fail instead.
    """
    from .llm_rate_limit import (
        _is_llm_rate_limited_error,
        _is_llm_retryable_error,
        call_llm_with_retry,
    )

    if not settings.SOW_LLM_API_KEY:
        raise LLMConfigError(
            "SOW_LLM_API_KEY environment variable not set. "
            "Set this to your OpenRouter/OpenAI API key."
        )

    if not settings.SOW_LLM_BASE_URL:
        raise LLMConfigError(
            "SOW_LLM_BASE_URL environment variable not set. "
            "Set this to your OpenAI-compatible API base URL."
        )

    effective_model = llm_model or settings.SOW_LLM_MODEL
    if not effective_model:
        raise LLMConfigError(
            "LLM model not specified. Either set llm_model in the request "
            "or set SOW_LLM_MODEL environment variable."
        )

    from ..services.llm_models import split_llm_model_effort

    model_id, reasoning_effort = split_llm_model_effort(effective_model)

    loop = asyncio.get_running_loop()

    def _call_llm():
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.SOW_LLM_API_KEY,
            base_url=settings.SOW_LLM_BASE_URL,
            max_retries=0,
        )

        create_kwargs: dict = {
            "model": model_id,
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        }
        if reasoning_effort is not None:
            create_kwargs["reasoning_effort"] = reasoning_effort

        response = client.chat.completions.create(**create_kwargs)

        return response.choices[0].message.content

    logger.info(f"Calling LLM ({effective_model}) for YouTube transcript correction")
    attempt_start = time.time()
    try:
        response_text = await call_llm_with_retry(
            _call_llm,
            description=f"LLM correction ({effective_model})",
            loop=loop,
        )
    except Exception as e:
        if trace is not None:
            trace.append(
                "llm_correction_error",
                {"model": effective_model, **({"reasoning_effort": reasoning_effort} if reasoning_effort is not None else {}), "error": str(e)},
            )
        if _is_llm_rate_limited_error(e):
            raise LLMCorrectionError(
                f"LLM correction failed after rate-limit retries: {e}"
            ) from e
        if _is_llm_retryable_error(e):
            raise LLMCorrectionError(
                f"LLM correction failed after transient-error retries: {e}"
            ) from e
        raise LLMCorrectionError(f"LLM correction failed: {e}") from e
    if trace is not None:
        trace.append(
            "llm_correction_attempt",
            {
                "model": effective_model,
                **(
                    {"reasoning_effort": reasoning_effort}
                    if reasoning_effort is not None
                    else {}
                ),
                "prompt": prompt,
                "response": response_text,
                "elapsed_s": time.time() - attempt_start,
            },
        )
    return response_text


async def youtube_transcript_to_lrc(
    youtube_url: str,
    lyrics_text: str,
    llm_model: str,
    language: str = "zh",
    tempo_bpm: Optional[float] = None,
    *,
    trace: Optional[LLMTrace] = None,
) -> List[LRCLine]:
    """End-to-end: YouTube transcript -> LLM correction -> LRC lines.

    Args:
        youtube_url: YouTube video URL
        lyrics_text: Official lyrics text (newline-separated)
        llm_model: LLM model identifier
        language: LRC language ("zh" or "en")
        tempo_bpm: Recording tempo for gap placeholder insertion. None/<=0 skips
            insertion (output matches pre-gap-feature behavior).

    Returns:
        List of LRCLine objects with corrected timestamps

    Raises:
        YouTubeTranscriptError: If any step fails
    """
    start_time = time.time()

    # Step 1: Extract video ID
    video_id = extract_video_id(youtube_url)
    if not video_id:
        raise YouTubeTranscriptError(f"Could not extract video ID from URL: {youtube_url}")
    logger.info(f"Extracted video ID: {video_id}")

    # Step 2: Fetch transcript
    transcript = await fetch_youtube_transcript(video_id, language=language)

    if trace is not None:
        trace.append(
            "youtube_transcript",
            {
                "youtube_url": youtube_url,
                "video_id": video_id,
                "transcript_text": _format_transcript_text(transcript),
                "cues": [
                    {"start": c.start, "end": c.end, "duration": c.duration}
                    for c in _extract_cue_timings(transcript)
                ],
            },
        )

    # Step 3: Format transcript and build prompt
    transcript_text = _format_transcript_text(transcript)
    lyrics_lines = [line for line in lyrics_text.split("\n") if line.strip()]
    prompt = build_correction_prompt(transcript_text, lyrics_lines, language=language)

    logger.debug("YOUTUBE TRANSCRIPT LLM PROMPT")
    logger.debug("=" * 80)
    for line in prompt.split("\n"):
        logger.debug(line)
    logger.debug("=" * 80)

    # Step 4: LLM correction
    response_text = await _llm_correct(prompt, llm_model, trace=trace)

    # Log the response
    logger.debug("=" * 80)
    logger.debug("YOUTUBE TRANSCRIPT LLM RESPONSE")
    logger.debug("=" * 80)
    for line in response_text.split("\n"):
        logger.debug(line)
    logger.debug("=" * 80)

    # Step 5: Parse LRC response
    try:
        lrc_lines = parse_lrc_response(response_text)
    except ValueError as e:
        raise YouTubeTranscriptError(f"Failed to parse LLM response: {e}") from e

    # Step 6: Insert blank placeholder lines into long instrumental gaps
    pre_gap = list(lrc_lines)
    cues = _extract_cue_timings(transcript)
    lrc_lines = insert_gap_placeholder_lines(lrc_lines, cues, tempo_bpm)

    if trace is not None:
        pre_keys = [(ln.time_seconds, ln.text) for ln in pre_gap]
        inserted = [
            {"time_seconds": ln.time_seconds, "text": ln.text}
            for ln in lrc_lines
            if (ln.time_seconds, ln.text) not in pre_keys
        ]
        trace.append(
            "gap_placeholder_lines",
            {
                "tempo_bpm": tempo_bpm,
                "lines_before": len(pre_gap),
                "lines_after": len(lrc_lines),
                "inserted": inserted,
            },
        )

    elapsed = time.time() - start_time
    logger.info(f"YouTube transcript -> LRC completed: {len(lrc_lines)} lines in {elapsed:.2f}s")

    return lrc_lines
