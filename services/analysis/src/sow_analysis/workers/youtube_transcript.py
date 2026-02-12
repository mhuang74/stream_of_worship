"""YouTube transcript-based LRC generation.

Downloads YouTube captions via youtube-transcript-api, corrects them
against official lyrics using an LLM, and produces timestamped LRC lines.

This is the primary (preferred) path for LRC generation — YouTube often
has human-curated subtitles with accurate timing. The Whisper+LLM pipeline
serves as fallback when YouTube transcripts are unavailable.
"""

import asyncio
import logging
import re
import time
from typing import List, Optional

from ..config import settings
from .lrc import LLMConfigError, LRCLine, LRCWorkerError

logger = logging.getLogger(__name__)


class YouTubeTranscriptError(LRCWorkerError):
    """Raised when YouTube transcript fetch or processing fails."""

    pass


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
) -> str:
    """Build LLM prompt for lyrics correction.

    Args:
        transcript_text: Formatted transcript with timestamps
        official_lyrics: List of official lyric lines

    Returns:
        Correction prompt string
    """
    lyrics_str = "\n".join(official_lyrics)

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


async def fetch_youtube_transcript(
    video_id: str,
    languages: Optional[List[str]] = None,
) -> list:
    """Download captions from YouTube via youtube-transcript-api.

    Tries multiple language codes in order. Returns transcript snippet objects.

    Args:
        video_id: YouTube video ID
        languages: Language codes to try (default: zh-Hant, zh-Hans, zh, en)

    Returns:
        List of transcript snippet objects

    Raises:
        YouTubeTranscriptError: If transcript cannot be fetched
    """
    if languages is None:
        languages = ["zh-Hant", "zh-Hans", "zh", "en"]

    loop = asyncio.get_event_loop()

    def _fetch():
        from youtube_transcript_api import YouTubeTranscriptApi

        ytt_api = YouTubeTranscriptApi()
        return ytt_api.fetch(video_id, languages=languages)

    try:
        transcript = await loop.run_in_executor(None, _fetch)
    except Exception as e:
        raise YouTubeTranscriptError(
            f"Failed to fetch YouTube transcript for video {video_id}: {e}"
        ) from e

    if not transcript:
        raise YouTubeTranscriptError(
            f"YouTube returned empty transcript for video {video_id}"
        )

    logger.info(f"Fetched {len(transcript)} transcript segments from YouTube")
    return transcript


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
        raise YouTubeTranscriptError(
            f"Could not extract video ID from URL: {youtube_url}"
        )
    logger.info(f"Extracted video ID: {video_id}")

    # Step 2: Fetch transcript
    transcript = await fetch_youtube_transcript(video_id)

    # Step 3: Format transcript and build prompt
    transcript_text = _format_transcript_text(transcript)
    lyrics_lines = [line for line in lyrics_text.split("\n") if line.strip()]
    prompt = build_correction_prompt(transcript_text, lyrics_lines)

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
    logger.info(
        f"YouTube transcript -> LRC completed: {len(lrc_lines)} lines in {elapsed:.2f}s"
    )

    return lrc_lines
