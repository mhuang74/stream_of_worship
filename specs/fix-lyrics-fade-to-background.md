# Fix Lyrics Fade To Background

## Summary

Rendered lyric fade colors currently darken toward black because `FrameRenderer` scales RGB
foreground values directly by alpha. This is visible when lyrics fade out: text dims from white,
passes through black, then disappears. The fade should instead composite lyric text over the
active template background so alpha `0` equals `template.background_color`.

The bug is in `services/render-worker/src/sow_render_worker/frame_renderer.py`, not
`services/render-worker/src/sow_render_worker/pipeline.py`. The pipeline only constructs
`VideoEngine`; `VideoEngine` delegates frame generation to `FrameRenderer`.

## Implementation Changes

- Add a private helper on `FrameRenderer` for alpha compositing RGB text over the template
  background:
  - Input: foreground RGB tuple and integer alpha in the existing `0..255` range.
  - Output: RGB tuple computed per channel as
    `background + (foreground - background) * alpha / 255`.
  - Clamp alpha to `0..255` before computing to keep the helper defensive.
- Replace every alpha-driven text fill that currently does `int(color * alpha / 255)` with the
  helper:
  - Intro song info fade in `render_intro_info`.
  - Previous lyric fade shown during blank lyric lines.
  - Current/last lyric fade in `render_lyrics`.
  - Next lyric preview fade in `_render_next_lyric_preview`.
- Keep existing fade timing, easing math, title rendering, cache key quantization, templates, and
  public interfaces unchanged.

## Test Plan

- Add unit coverage in `services/render-worker/tests/test_frame_renderer.py`:
  - Helper returns foreground at alpha `255`.
  - Helper returns `template.background_color` at alpha `0`.
  - Helper returns a mid-blend color between foreground and background, not a black-scaled color.
  - A fading lyric rendered on a non-black template background does not create pixels darker than
    the background in the lyric area.
- Run:

```bash
PYTHONPATH=src pytest services/render-worker/tests/test_frame_renderer.py -v
```

## Acceptance Criteria

- Lyric fade-out visually ends at the active template background color.
- No fade path temporarily renders black text unless the template background itself is black.
- Existing frame cache behavior and generated frame byte format remain unchanged.

## Assumptions

- The intended behavior applies to all lyric-related faded text, including previews and blank-line
  previous lyric fades.
- The current fade durations and easing curves are intentional and should not be changed.
