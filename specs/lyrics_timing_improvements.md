# Video Lyrics Display Improvements - Implementation Plan

## Context

Currently, the video engine has two awkward display issues during song transitions:

1. **Long intros without lyrics**: When a song has a long instrumental intro before the first lyric, the title is not displayed until the first lyric timestamp is reached. This results in several seconds of blank screen during the intro.

2. **Long outros without lyrics**: When a song ends with a long instrumental outro, the last lyric remains on screen during the outro. Additionally, when transitioning to the next song, the next song's first lyric may appear during the previous song's outro (before that song has actually started).

## Root Cause Analysis

The current `_render_frame` method in `video_engine.py` determines the current song title by looking at the **most recent lyric line**:

```python
# Current logic (lines 260-266)
title = ""
for line in lyrics:
    if line.global_time_seconds <= current_time:
        title = line.title
    else:
        break
```

This means:
- Before the first lyric of a song: `title = ""` (blank title during intro)
- After the last lyric of a song: title still shows that song, but the last lyric remains visible
- During gaps between songs: title/lyrics from the previous song persist until the next song's first lyric

## Proposed Solution

Use the **audio segment timing** from `ExportResult.segments` to determine:
1. Which song is currently playing (by `start_time_seconds` and `duration_seconds`)
2. Whether to show lyrics for that song (only between first and last lyric timestamps)

### Changes to `video_engine.py`

#### 1. Modify `generate_lyrics_video` method (lines 314-414)

Pass `audio_result.segments` to `_render_frame`:

```python
# In the frame generation loop (around line 400):
img = self._render_frame(all_lyrics, audio_result.segments, current_time)
```

#### 2. Modify `_render_frame` method signature (line 241)

Add `segments` parameter:

```python
def _render_frame(
    self,
    lyrics: list[GlobalLRCLine],
    segments: list[AudioSegmentInfo],
    current_time: float,
) -> Image.Image:
```

#### 3. Implement new title determination logic

Replace the lyric-based title lookup with segment-based lookup:

```python
# Find current song based on segment timing
current_title = ""
for segment in segments:
    segment_start = segment.start_time_seconds
    segment_end = segment_start + segment.duration_seconds
    if segment_start <= current_time < segment_end:
        current_title = segment.item.song_title or "Unknown"
        break
```

#### 4. Implement constrained lyric display logic

Group lyrics by song title and only show lyrics when:
1. The current time falls within that song's segment timing
2. The current time falls between that song's first and last lyric timestamps

```python
# Group lyrics by title for easy lookup
lyrics_by_song: dict[str, list[GlobalLRCLine]] = {}
for line in lyrics:
    if line.title not in lyrics_by_song:
        lyrics_by_song[line.title] = []
    lyrics_by_song[line.title].append(line)

# Find active lyrics only for the current song
current_song_lyrics = lyrics_by_song.get(current_title, [])

# Only show lyrics if current time is within this song's lyric time range
if current_song_lyrics:
    first_lyric_time = current_song_lyrics[0].global_time_seconds
    last_lyric_time = current_song_lyrics[-1].global_time_seconds

    # Only render lyrics if we're within the lyric time range
    if first_lyric_time <= current_time <= last_lyric_time:
        # Find current lyric index within this song's lyrics
        current_index = -1
        for i, line in enumerate(current_song_lyrics):
            if line.global_time_seconds <= current_time:
                current_index = i
            else:
                break
        # Render current and next lyric...
```

### Visual Result

```
Timeline:          Song 1 (180s)          Gap (10s)         Song 2 (200s)
                   |======================|----------|========================|
Audio:             [====INTRO====LYRICS====OUTRO]     [====INTRO====LYRICS====]
Time:              0s                    180s         190s                   390s

OLD Behavior:
Title:             ""       "Song 1"------------------"Song 2"
Lyrics:            ""       "Line 1"..."Line N"-------"Line 1"...
                                    ^^^^^ last lyric stuck during outro
                   ^^^^^ blank during intro

NEW Behavior:
Title:             "Song 1"---------------------------"Song 2"-----------------
Lyrics:            ""       "Line 1"..."Line N"-------""       "Line 1"...
                   ^^^^^ okay (intro)                  ^^^^^ okay (gap/outro)
```

## Critical Files to Modify

| File | Lines | Change |
|------|-------|--------|
| `src/stream_of_worship/app/services/video_engine.py` | 241-312 | Update `_render_frame` signature and logic |
| `src/stream_of_worship/app/services/video_engine.py` | 314-414 | Pass segments to `_render_frame` |

## Import Requirements

Add import for `AudioSegmentInfo`:

```python
from stream_of_worship.app.services.audio_engine import ExportResult, AudioSegmentInfo
```

## Verification Steps

1. Create a test songset with songs that have:
   - Long instrumental intros (first lyric at 10+ seconds)
   - Long instrumental outros (gap between last lyric and song end)

2. Export the video and verify:
   - [ ] Title appears immediately when song audio starts (during intro)
   - [ ] Lyrics area is blank during long intros
   - [ ] Last lyric disappears when song ends (during outro)
   - [ ] Next song's lyrics only appear when that song's lyrics actually start

3. Test edge cases:
   - [ ] Songs with no lyrics (LRC file missing or empty) - title should still display
   - [ ] Very short songs - normal behavior
   - [ ] Songs back-to-back with no gap
