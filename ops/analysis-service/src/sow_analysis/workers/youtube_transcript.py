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
from typing import Any, Callable, List, Optional

from ..config import settings
from ..workers.exceptions import LLMConfigError
from .lrc import LRCLine, LRCWorkerError

logger = logging.getLogger(__name__)


class RotatingProxyConfig:
    """Proxy config with rotating-proxy features for IP ban avoidance.

    Wraps youtube-transcript-api's GenericProxyConfig to add:
    - prevent_keeping_connections_alive=True (triggers IP rotation per request)
    - retries_when_blocked=N (retries on HTTP 429, also triggers rotation)

    This is essential for rotating residential proxy services where each retry
    gets a new IP from the pool.
    """

    def __init__(self, http_url: str, https_url: str, retries_when_blocked: int = 3):
        from youtube_transcript_api.proxies import GenericProxyConfig

        self._generic_config = GenericProxyConfig(http_url=http_url, https_url=https_url)
        self._retries_when_blocked = retries_when_blocked

    def to_requests_dict(self) -> dict:
        return self._generic_config.to_requests_dict()

    @property
    def prevent_keeping_connections_alive(self) -> bool:
        return True

    @property
    def retries_when_blocked(self) -> int:
        return self._retries_when_blocked


class YouTubeTranscriptError(LRCWorkerError):
    """Raised when YouTube transcript fetch or processing fails."""

    pass


def _is_rate_limited_error(e: Exception) -> bool:
    """Check if an exception is caused by YouTube rate limiting (HTTP 429).

    Tries three detection strategies in order:
    1. Check for HTTP 429 status_code on urllib3/requests exceptions.
    2. Check the exception string and its __cause__ chain for "429".
    3. Fall back to False (non-429 error — do not retry).

    The youtube-transcript-api library (via urllib3) raises errors like:
        ResponseError('too many 429 error responses')

    Strategy 1 is more robust; strategy 2 is a pragmatic fallback.
    """
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

    def _is_circuit_open(self) -> bool:
        """Check if the circuit breaker is currently open."""
        return time.monotonic() < self._circuit_open_until

    def _open_circuit(self) -> None:
        """Open the circuit breaker for the configured cooldown period."""
        cooldown = settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN
        self._circuit_open_until = time.monotonic() + cooldown
        logger.warning(
            "YouTube transcript circuit breaker OPENED after %d consecutive 429s, "
            "cooldown %ds — all YouTube transcript fetches will skip to fallback",
            self._consecutive_429_count,
            cooldown,
        )

    def _reset_circuit(self) -> None:
        """Reset the circuit breaker on a successful request."""
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
            min_interval = settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS
            if elapsed < min_interval:
                wait = min_interval - elapsed
                logger.debug(
                    "YouTube rate limit: spacing request, sleeping %.2fs", wait
                )
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
            YouTubeTranscriptError: If the circuit breaker is open, or if all
                retries are exhausted on 429, or if fn() raises a non-429 error.
        """
        self._ensure_initialized()

        # Layer 1: Circuit breaker check
        if self._is_circuit_open():
            remaining = self._circuit_open_until - time.monotonic()
            raise YouTubeTranscriptError(
                f"YouTube transcript circuit breaker is open "
                f"(cooldown: {remaining:.0f}s remaining) — skipping {description}"
            )

        loop = asyncio.get_running_loop()
        max_retries = settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES
        base_delay = settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY
        threshold = settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD

        for attempt in range(max_retries + 1):
            # Layer 2: Circuit breaker check (re-check each iteration)
            if self._is_circuit_open():
                remaining = self._circuit_open_until - time.monotonic()
                raise YouTubeTranscriptError(
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
                self._reset_circuit()
                return result
            except Exception as e:
                if not _is_rate_limited_error(e):
                    # Non-429 error — don't retry, propagate immediately
                    raise

                # Layer 4: 429 retry with backoff
                self._consecutive_429_count += 1
                logger.warning(
                    "YouTube API rate limited (429), attempt %d/%d for %s: %s",
                    attempt + 1,
                    max_retries + 1,
                    description,
                    e,
                )

                # Check if circuit breaker should open
                if self._consecutive_429_count >= threshold:
                    self._open_circuit()
                    raise YouTubeTranscriptError(
                        f"YouTube API rate limited — circuit breaker opened "
                        f"after {self._consecutive_429_count} consecutive 429s: {e}"
                    ) from e

                # Retry with exponential backoff + jitter (if attempts remain)
                if attempt < max_retries:
                    delay = min(base_delay * (2 ** attempt), 60.0)
                    delay += random.uniform(0, delay * 0.25)
                    logger.info(
                        "Retrying YouTube API call for %s in %.1fs",
                        description,
                        delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                # All retries exhausted
                raise YouTubeTranscriptError(
                    f"YouTube API rate limited after {max_retries + 1} "
                    f"attempts for {description}: {e}"
                ) from e

        # Should not reach here, but safety net
        raise YouTubeTranscriptError(
            f"YouTube API rate limiter exhausted all retries for {description}"
        )


_rate_limiter = _YouTubeRateLimiter()


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


def build_correction_prompt(
    transcript_text: str,
    official_lyrics: list[str],
    language: str = "zh",
) -> str:
    """Build LLM prompt for lyrics correction.

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

## Transcribed Subtitle
```
{transcript_text}
```

## Official Lyrics
```
{lyrics_str}
```

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

## Transcribed Subtitle (auto-generated)
```
{transcript_text}
```

## Published Lyrics (official, one unique phrase per line)
```
{lyrics_str}
```

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
    return RotatingProxyConfig(
        http_url=settings.SOW_YOUTUBE_PROXY,
        https_url=settings.SOW_YOUTUBE_PROXY,
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
        logger.info(f"Fetching YouTube transcript via proxy: {settings.SOW_YOUTUBE_PROXY}")
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
    except Exception as e:
        logger.error(f"Direct fetch failed for {video_id}, trying list fallback: {e}")

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
) -> str:
    """Call LLM to correct YouTube transcript against official lyrics.

    Args:
        prompt: Correction prompt
        llm_model: LLM model identifier

    Returns:
        LLM response text

    Raises:
        LLMConfigError: If LLM is not configured
        YouTubeTranscriptError: If LLM call fails
    """
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

    loop = asyncio.get_event_loop()

    def _call_llm():
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.SOW_LLM_API_KEY,
            base_url=settings.SOW_LLM_BASE_URL,
        )

        response = client.chat.completions.create(
            model=effective_model,
            messages=[
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )

        return response.choices[0].message.content

    logger.info(f"Calling LLM ({effective_model}) for YouTube transcript correction")
    try:
        return await loop.run_in_executor(None, _call_llm)
    except Exception as e:
        raise YouTubeTranscriptError(f"LLM correction failed: {e}") from e


async def youtube_transcript_to_lrc(
    youtube_url: str,
    lyrics_text: str,
    llm_model: str,
    language: str = "zh",
) -> List[LRCLine]:
    """End-to-end: YouTube transcript -> LLM correction -> LRC lines.

    Args:
        youtube_url: YouTube video URL
        lyrics_text: Official lyrics text (newline-separated)
        llm_model: LLM model identifier

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

    # Step 3: Format transcript and build prompt
    transcript_text = _format_transcript_text(transcript)
    lyrics_lines = [line for line in lyrics_text.split("\n") if line.strip()]
    prompt = build_correction_prompt(transcript_text, lyrics_lines, language=language)

    # Log the prompt
    logger.info("=" * 80)
    logger.info("YOUTUBE TRANSCRIPT LLM PROMPT")
    logger.info("=" * 80)
    for line in prompt.split("\n"):
        logger.info(line)
    logger.info("=" * 80)

    # Step 4: LLM correction
    response_text = await _llm_correct(prompt, llm_model)

    # Log the response
    logger.info("=" * 80)
    logger.info("YOUTUBE TRANSCRIPT LLM RESPONSE")
    logger.info("=" * 80)
    for line in response_text.split("\n"):
        logger.info(line)
    logger.info("=" * 80)

    # Step 5: Parse LRC response
    try:
        lrc_lines = parse_lrc_response(response_text)
    except ValueError as e:
        raise YouTubeTranscriptError(f"Failed to parse LLM response: {e}") from e

    elapsed = time.time() - start_time
    logger.info(f"YouTube transcript -> LRC completed: {len(lrc_lines)} lines in {elapsed:.2f}s")

    return lrc_lines
