# Fix: FFmpeg EPIPE on stdin write — title card frame count causes premature pipe close

## Problem

The render worker raises `RuntimeError("FFmpeg process closed prematurely (EPIPE on stdin write)")` during video encoding, even though FFmpeg completed successfully (exit code 0, full output written).

### Evidence from the error log

```
FFmpeg process closed prematurely (EPIPE on stdin write). FFmpeg stderr (last 2000 chars):
...
[out#0/mp4 @ 0x45519240] video:19545KiB audio:22856KiB subtitle:0KiB other streams:0KiB global headers:0KiB muxing overhead: 1.309436%
frame=23506 fps= 37 q=-1.0 Lsize= 42956KiB time=00:16:19.41 bitrate= 359.3kbits/s speed=1.56x
[libx264 @ 0x454f3fc0] frame I:95 Avg QP: 9.38 size: 92580
[libx264 @ 0x454f3fc0] frame P:23411 Avg QP:12.21 size: 479
...
[libx264 @ 0x454f3fc0] kb/s:163.47
[aac @ 0x45509380] Qavg: 1646.435
```

Key observations:

- `q=-1.0` and `Lsize` indicate the **final frame** was written — FFmpeg finished encoding normally
- 23,506 frames encoded at 24fps = 979.4s = 16:19.4, matching the audio duration
- FFmpeg exited with return code 0
- The error is raised by the Python `BrokenPipeError` handler, **not** by FFmpeg

### Root cause: `total_frames` exceeds what `-shortest` allows

In `video_engine.py:131`:

```python
total_frames = math.ceil(total_duration_seconds * self.fps) + title_card_frames
```

This adds `title_card_frames` (5s × 24fps = 120 frames) **on top of** the audio-duration frames. But FFmpeg is invoked with `-shortest` (line 289), which stops encoding when the audio stream ends. FFmpeg only accepts `audio_duration × fps` frames, then closes stdin and exits with code 0. Python tries to write 120 more frames → `BrokenPipeError` → `RuntimeError`.

**Why this happens:**

1. Python calculates `total_frames = ceil(979.41 * 24) + 120 = 23,506 + 120 = 23,626`
2. Python writes frames 0 through 23,505 (the audio-duration frames)
3. FFmpeg finishes encoding (audio stream ended, `-shortest` triggered), closes stdin, exits with code 0
4. Python tries to write frame 23,506 (the first frame beyond what FFmpeg needs)
5. `process.stdin.write(frame_bytes)` raises `BrokenPipeError`
6. The error handler catches it and raises `RuntimeError("FFmpeg process closed prematurely (EPIPE on stdin write)")`

The title card is meant to be shown **during** the first 5 seconds of audio (both FFmpeg inputs start at time 0), not **in addition to** the audio. So `title_card_frames` should be counted **within** the audio-duration budget, not added on top.

### Secondary bug: Lyrics are 5 seconds behind audio

In `video_engine.py:340`:

```python
current_time = lyrics_frame_index / self.fps  # starts at 0 when audio is already 5s in
```

When the first lyrics frame renders (frame 120 at 24fps), `lyrics_frame_index = 0` → `current_time = 0`. But the audio is already at 5 seconds. Lyrics are timed relative to the audio timeline (via `segment.start_time_seconds` from `audio_engine.py:241`, where the first segment starts at 0.0), so they'll be 5 seconds late.

**Example:** A lyric line at `global_time_seconds = 10` would be shown when `current_time = 10`, which is at frame `120 + 10*24 = 360` — 15 seconds into the video/audio. But the audio is at 15 seconds, and the lyric should appear at 10 seconds. The lyric appears 5 seconds late.

### Tertiary bug: `BrokenPipeError` handler always raises, even when FFmpeg succeeded

In `video_engine.py:346-363`, the `BrokenPipeError` handler unconditionally raises `RuntimeError`. If FFmpeg exited with code 0 (legitimate early close due to `-shortest`), this should be treated as success, not failure. This is a defense-in-depth concern — even after fixing the frame count, any future mismatch would cause a false-positive error.

---

## File Changes

### 1. `src/sow_render_worker/video_engine.py`

#### 1a. Fix `total_frames` calculation (line 131)

Remove the `+ title_card_frames` addition. The title card occupies the first N seconds of the audio timeline — it doesn't extend beyond it.

```python
# Before:
total_frames = math.ceil(total_duration_seconds * self.fps) + title_card_frames

# After:
total_frames = math.ceil(total_duration_seconds * self.fps)
```

**Why this is correct:** The FFmpeg command has two inputs that both start at time 0: stdin (raw video frames) and the audio file. With `-shortest`, the output duration equals the audio duration. The title card is shown during the first `title_card_duration_seconds` of the audio, replacing what would otherwise be lyrics/intro-info frames. The total number of frames Python needs to write equals the audio duration in frames — no more.

#### 1e. Remove dead `title_card_frames` variable in `generate_video` (lines 126-130)

After fix 1a, the `title_card_frames` variable is no longer referenced. Remove it to avoid dead code confusion.

```python
# Before:
title_card_frames = (
    math.ceil(self.title_card_duration_seconds * self.fps)
    if self.include_title_card
    else 0
)
total_frames = math.ceil(total_duration_seconds * self.fps)

# After:
total_frames = math.ceil(total_duration_seconds * self.fps)
```

#### 1b. Fix lyrics timeline sync (lines 337-342)

Use `frame_count` (which tracks the global frame position including title card) instead of `lyrics_frame_index` (which resets to 0 after the title card). This makes `current_time` track the audio timeline correctly.

```python
# Before:
lyrics_frame_index = (
    frame_count - title_card_frame_count if title_card_config else frame_count
)
current_time = lyrics_frame_index / self.fps

# After:
current_time = frame_count / self.fps
```

**Why this is correct:** When the first lyrics frame appears (frame 120 at 24fps), `current_time = 5.0`, matching the audio position. The `render_frame()` method uses `current_time` to look up which segment is active and which lyrics line to show. With `current_time = 5.0`, it correctly finds the segment at 5 seconds into the audio and shows the corresponding lyrics.

The `lyrics_frame_index` variable is no longer needed and can be removed entirely.

#### 1c. Make `BrokenPipeError` handler resilient (lines 346-363)

If FFmpeg exited with code 0, treat it as success — `return` from the method directly (logging elapsed time and firing the progress callback) rather than raising an error. Using `return` instead of `break` avoids falling through to the redundant post-loop `stderr_thread.join()` + `process.wait()` calls, and ensures the progress callback reports completion. This is defense-in-depth for any future frame-count mismatches.

```python
# Before:
except BrokenPipeError:
    logger.error(
        "[%s] FFmpeg pipe broken at frame %d/%d",
        job_id or "unknown", frame_count, total_frames,
    )
    process.stdin.close()
    if stderr_thread:
        stderr_thread.join(timeout=5)
    process.wait()
    stderr_output = b"".join(stderr_chunks).decode("utf-8", errors="replace")
    stderr_info = (
        f"\nFFmpeg stderr (last 2000 chars): {stderr_output[-2000:]}"
        if stderr_output
        else ""
    )
    raise RuntimeError(
        f"FFmpeg process closed prematurely (EPIPE on stdin write).{stderr_info}"
    )

# After:
except BrokenPipeError:
    process.stdin.close()
    if stderr_thread:
        stderr_thread.join(timeout=5)
    process.wait()
    if process.returncode == 0:
        logger.info(
            "[%s] FFmpeg completed early (stopped reading at frame %d/%d)",
            job_id or "unknown", frame_count, total_frames,
        )
        ffmpeg_elapsed = time.monotonic() - ffmpeg_start
        logger.info(
            "[%s] FFmpeg process exited with code 0 in %.1fs",
            job_id or "unknown", ffmpeg_elapsed,
        )
        if progress_callback:
            progress_callback(total_frames, total_frames)
        return
    logger.error(
        "[%s] FFmpeg pipe broken at frame %d/%d",
        job_id or "unknown", frame_count, total_frames,
    )
    stderr_output = b"".join(stderr_chunks).decode("utf-8", errors="replace")
    stderr_info = (
        f"\nFFmpeg stderr (last 2000 chars): {stderr_output[-2000:]}"
        if stderr_output
        else ""
    )
    raise RuntimeError(
        f"FFmpeg process closed prematurely (EPIPE on stdin write).{stderr_info}"
    )
```

#### 1d. No change needed for stdin close after loop

The original spec proposed guarding `process.stdin.close()` with `if frame_count >= total_frames` to avoid double-closing after an early `break`. Since fix 1c now uses `return` instead of `break`, the early-exit path never reaches the post-loop code. The existing `try/except BrokenPipeError: pass` pattern is sufficient and should remain unchanged.

```python
# Unchanged:
try:
    process.stdin.close()
except BrokenPipeError:
    pass
```

---

## Test Updates

### `tests/test_video_engine.py`

#### Test 1: `total_frames` no longer includes title card frames

Update `test_with_lyrics_encodes_video` and `test_title_card_config_passed` to verify that when `include_title_card=True`, `total_frames` equals `ceil(duration * fps)` without the title card addition.

In `test_title_card_config_passed`, the `encode_video_with_ffmpeg` call should receive `total_frames = ceil(180.0 * 24) = 4320` (not `4320 + 120 = 4440`).

#### Test 2: Lyrics timeline sync with title card

Add a test that verifies `current_time` passed to `render_frame()` starts at `title_card_duration_seconds` (not 0) for the first lyrics frame after the title card. This can be tested by:

1. Setting up `encode_video_with_ffmpeg` with a title card config
2. Mocking `frame_renderer.render_frame` to capture the `current_time` argument
3. Verifying that the first call to `render_frame` after the title card frames receives `current_time >= title_card_duration_seconds`

#### Test 3: `BrokenPipeError` with FFmpeg exit code 0 is not an error

Add a test where `process.stdin.write` raises `BrokenPipeError` but `process.returncode` is 0. Verify the method completes successfully instead of raising `RuntimeError`, and that the progress callback is called with `(total_frames, total_frames)`.

```python
def test_encode_broken_pipe_with_zero_exit_code_succeeds(self, tmp_path):
    output_path = str(tmp_path / "video.mp4")
    fetcher = MockAssetFetcher()
    engine = VideoEngine(fetcher)

    lyrics: list[GlobalLRCLine] = []
    segments: list[SegmentInfo] = []

    progress_calls: list[tuple[int, int]] = []

    def progress_cb(current: int, total: int) -> None:
        progress_calls.append((current, total))

    mock_process = MagicMock()
    mock_process.stdin = MagicMock()
    mock_process.stdin.write.side_effect = BrokenPipeError()
    mock_process.stdin.close = MagicMock()
    mock_process.wait.return_value = 0
    mock_process.returncode = 0
    mock_process.stderr = MagicMock()
    mock_process.stderr.read.return_value = b""

    with patch("sow_render_worker.video_engine.subprocess.Popen") as mock_popen:
        mock_popen.return_value = mock_process
        # Should NOT raise — FFmpeg exited successfully
        engine.encode_video_with_ffmpeg(
            "/tmp/audio.mp3",
            output_path,
            total_frames=10,
            total_duration_seconds=0.5,
            lyrics=lyrics,
            segments=segments,
            progress_callback=progress_cb,
        )

    # Progress callback should report completion
    assert progress_calls[-1] == (10, 10)
```

#### Test 4: `BrokenPipeError` with FFmpeg exit code 1 still raises

The existing test `test_encode_handles_broken_pipe` already covers this (it sets `returncode=1`). It should continue to pass unchanged.

---

## Implementation Order

1. Fix `total_frames` calculation (1a) — primary bug fix
2. Remove dead `title_card_frames` variable (1e) — cleanup after 1a
3. Fix lyrics timeline sync (1b) — secondary bug fix
4. Make `BrokenPipeError` handler resilient (1c) — defense-in-depth
5. Add/update tests
6. Run `PYTHONPATH=src pytest tests/test_video_engine.py -v` to verify

---

## Edge Cases

### Title card duration > audio duration

If audio is shorter than `title_card_duration_seconds` (e.g., 3s audio, 5s title card), `total_frames = ceil(3 * 24) = 72`. All 72 frames would be title card frames — no lyrics frames rendered. The title card gets truncated to 3s by `-shortest`. This is unlikely in practice (worship sets are long) but is a behavioral change from the old code which would have attempted 72 title card + some lyrics frames (and then crashed with EPIPE).

---

## Verification Checklist

- [ ] `total_frames = ceil(duration * fps)` without `+ title_card_frames`
- [ ] Dead `title_card_frames` variable removed from `generate_video`
- [ ] `current_time = frame_count / fps` (not `lyrics_frame_index / fps`)
- [ ] `lyrics_frame_index` variable removed
- [ ] `BrokenPipeError` with `returncode == 0` returns success (logs + progress callback + `return`, not `break`)
- [ ] `BrokenPipeError` with `returncode != 0` still raises `RuntimeError`
- [ ] Post-loop `process.stdin.close()` unchanged (guard no longer needed due to `return`)
- [ ] All existing tests pass
- [ ] New tests for the three fixes pass
