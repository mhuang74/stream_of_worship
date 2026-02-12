# Plan: Auto-hide last lyric line during long outros

## Context
When a song has a long outro, the last lyrics phrase stays on screen for the entire outro duration, which is distracting. We need to estimate how long the last lyric should display and then fade it out.

## Approach

**Two-tier duration estimation for the last lyric line of each song:**

1. **Primary — match previous occurrence**: Scan earlier lines in the same song for the same `text`. If found, use the known duration (gap to the line that followed it). This is the most accurate estimate since it uses actual timing from the same song.

2. **Fallback — character count + BPM**: Count Chinese characters (+ non-whitespace ASCII as ~0.5 chars), multiply by 2 beats per character, convert to seconds using BPM. Use `segment.item.tempo_bpm` or default to 70 BPM if unavailable. Add a minimum floor of 3 seconds.

**Fade out**: Once estimated duration expires, fade the lyric opacity from 255 to 0 over ~1 second.

## Changes

### File: `src/stream_of_worship/app/services/video_engine.py`

#### 1. Add helper method `_estimate_last_lyric_duration`

```python
def _estimate_last_lyric_duration(
    self, song_lyrics: list[GlobalLRCLine], tempo_bpm: Optional[float]
) -> float:
```

- Takes the list of lyrics for a single song and the song's BPM
- Looks at the last line's text, searches backward for an earlier line with identical text
- If found: returns `next_line.global_time - matched_line.global_time` (the known display duration of that earlier instance)
- If not found: counts characters, estimates `char_count * 2 * (60 / bpm)` seconds, with 70 BPM default. Minimum 3 seconds, maximum 15 seconds.

#### 2. Modify `_render_frame` (around lines 340-421)

In the block that renders the current lyric when `current_index` equals the last index:

- Compute `elapsed_since_last_lyric = current_time - current_line.global_time_seconds`
- Call `_estimate_last_lyric_duration` to get `max_display`
- If `elapsed_since_last_lyric > max_display + 1.0` (past fade window): skip rendering entirely
- If `elapsed_since_last_lyric > max_display`: compute fade alpha `= 255 * (1 - (elapsed - max_display) / 1.0)`, apply to both highlight_color and next-line color

Need to pass `segments` info to determine BPM for the current song — already available as a parameter.

#### 3. Find current segment's BPM

Already have `current_title` and segment lookup in `_render_frame`. Extract `tempo_bpm` from the matched segment's `item.tempo_bpm`.

## Key files
- `src/stream_of_worship/app/services/video_engine.py` — only file to modify

## Verification
- Run existing tests: `PYTHONPATH=src uv run --extra app pytest tests/app/services/test_video_engine.py -v`
- Generate a test video with a song that has a long outro and verify the last lyric fades out
