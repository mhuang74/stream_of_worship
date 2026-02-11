#!/usr/bin/env python3
"""YouTube Subtitle LRC Generation Prototype.

Downloads YouTube transcripts via youtube-transcript-api, corrects them
against official lyrics via LLM, and outputs LRC format.
"""

import os
import re
import sys
from pathlib import Path
from typing import Optional

import typer
from openai import OpenAI

from youtube_transcript_api import YouTubeTranscriptApi

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from stream_of_worship.app.config import AppConfig
from stream_of_worship.app.db.read_client import ReadOnlyClient
from stream_of_worship.app.services.catalog import CatalogService

app = typer.Typer(help="YouTube Subtitle LRC Generation")


def extract_video_id(youtube_url: str) -> Optional[str]:
    """Extract YouTube video ID from URL.

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


def format_timestamp(seconds: float) -> str:
    """Format seconds as [mm:ss.xx] timestamp.

    Args:
        seconds: Time in seconds

    Returns:
        Formatted timestamp string
    """
    minutes = int(seconds // 60)
    secs = seconds % 60
    return f"[{minutes:02d}:{secs:05.2f}]"


def format_transcript_text(transcript: list[object]) -> str:
    """Format transcript snippets as timestamped text for LLM prompt.

    Args:
        transcript: List of transcript snippet objects with .text, .start, .duration attributes

    Returns:
        Formatted transcript text
    """
    lines = []
    for snippet in transcript:
        start = snippet.start
        # Format as HH:MM:SS or MM:SS
        minutes = int(start // 60)
        seconds = start % 60
        hours = start // 3600
        if hours > 0:
            timestamp = f"{hours:02d}:{minutes % 60:02d}:{seconds:05.2f}"
        else:
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
        Correction prompt
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


def parse_lrc_response(response: str) -> str:
    """Parse LLM response and extract valid LRC lines.

    Args:
        response: LLM response text

    Returns:
        Valid LRC content
    """
    lrc_pattern = re.compile(r"^\[\d{2}:\d{2}\.\d{2}\].*")
    lines = []

    for line in response.splitlines():
        line = line.strip()
        if line and lrc_pattern.match(line):
            lines.append(line)

    return "\n".join(lines)


@app.command()
def main(
    song_id: str = typer.Argument(..., help="Song ID (e.g., wo_yao_quan_xin_zan_mei_244)"),
    youtube_url: Optional[str] = typer.Option(
        None, "--youtube-url", "-u", help="Override YouTube URL (skip DB lookup)"
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Output file (default: stdout)"
    ),
    lang: str = typer.Option("en-US", "--lang", help="Subtitle language (default: en-US)"),
    model: Optional[str] = typer.Option(
        None, "--model", "-m", help="LLM model (default: SOW_LLM_MODEL env var)"
    ),
):
    """Generate LRC from YouTube transcript corrected by LLM.

    Downloads YouTube transcripts via youtube-transcript-api, corrects them
    against official lyrics via LLM, and outputs LRC format.
    """
    # Load config
    try:
        config = AppConfig.load()
    except FileNotFoundError:
        typer.echo(
            "Error: Config file not found. Please run 'sow-app' first to create config.",
            err=True,
        )
        raise typer.Exit(1)

    # Get LLM config from environment
    api_key = os.environ.get("SOW_LLM_API_KEY")
    base_url = os.environ.get("SOW_LLM_BASE_URL")
    llm_model = model or os.environ.get("SOW_LLM_MODEL", "gpt-4")

    if not api_key:
        typer.echo("Error: SOW_LLM_API_KEY environment variable not set", err=True)
        raise typer.Exit(1)

    if not base_url:
        typer.echo("Error: SOW_LLM_BASE_URL environment variable not set", err=True)
        raise typer.Exit(1)

    # Resolve YouTube URL
    video_url: Optional[str] = youtube_url

    if not video_url:
        # Look up song from database
        db_client = ReadOnlyClient(config.db_path)
        catalog = CatalogService(db_client)

        song_with_recording = catalog.get_song_with_recording(song_id)
        if not song_with_recording:
            typer.echo(f"Error: Song not found: {song_id}", err=True)
            raise typer.Exit(1)

        if not song_with_recording.recording:
            typer.echo(f"Error: No recording found for song: {song_id}", err=True)
            raise typer.Exit(1)

        video_url = song_with_recording.recording.youtube_url
        song = song_with_recording.song

        typer.echo(f"Song: {song.title}", err=True)
        typer.echo(f"Recording: {song_with_recording.recording.hash_prefix}", err=True)
    else:
        # Still need song for lyrics - look up by ID
        db_client = ReadOnlyClient(config.db_path)
        catalog = CatalogService(db_client)
        song_with_recording = catalog.get_song_with_recording(song_id)
        if not song_with_recording:
            typer.echo(f"Error: Song not found: {song_id}", err=True)
            raise typer.Exit(1)
        song = song_with_recording.song

    if not video_url:
        typer.echo(
            f"Error: No YouTube URL found for song: {song_id}. "
            f"Use --youtube-url to provide one.",
            err=True,
        )
        raise typer.Exit(1)

    typer.echo(f"YouTube URL: {video_url}", err=True)

    # Extract video ID
    video_id = extract_video_id(video_url)
    if not video_id:
        typer.echo(f"Error: Could not extract video ID from URL: {video_url}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Video ID: {video_id}", err=True)

    # Download transcript
    try:
        typer.echo(f"Fetching transcript (lang={lang})...", err=True)
        transcript_data = YouTubeTranscriptApi().fetch(video_id, languages=[lang])
        transcript_text = format_transcript_text(transcript_data)
        typer.echo(f"Fetched {len(transcript_data)} transcript segments", err=True)
    except Exception as e:
        typer.echo(f"Error fetching transcript: {e}", err=True)
        raise typer.Exit(1)

    # Get official lyrics
    lyrics = song.lyrics_list
    if not lyrics:
        typer.echo(f"Warning: No official lyrics found for song: {song_id}", err=True)
        lyrics = []

    # Build correction prompt
    prompt = build_correction_prompt(transcript_text, lyrics)

    # Print the full prompt for transparency
    typer.echo("\n" + "=" * 80, err=True)
    typer.echo("LLM PROMPT:", err=True)
    typer.echo("=" * 80, err=True)
    typer.echo(prompt, err=True)
    typer.echo("=" * 80 + "\n", err=True)

    # Call LLM
    typer.echo(f"Calling LLM: {llm_model}", err=True)
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
    )

    try:
        response = client.chat.completions.create(
            model=llm_model,
            messages=[
                {"role": "system", "content": prompt},
            ],
            temperature=0.1,
        )
        llm_output = response.choices[0].message.content or ""
    except Exception as e:
        typer.echo(f"Error calling LLM: {e}", err=True)
        raise typer.Exit(1)

    # Parse LRC response
    lrc_content = parse_lrc_response(llm_output)

    if not lrc_content:
        typer.echo(
            "Warning: No valid LRC output found in LLM response", err=True
        )
        # Output raw response for debugging
        typer.echo("Raw LLM response:", err=True)
        typer.echo(llm_output, err=True)
        raise typer.Exit(1)

    typer.echo(f"Generated {lrc_content.count(chr(10)) + 1} LRC lines", err=True)

    # Output
    if output:
        output.write_text(lrc_content, encoding="utf-8")
        typer.echo(f"Wrote LRC to: {output}", err=True)
    else:
        print(lrc_content)


if __name__ == "__main__":
    app()
