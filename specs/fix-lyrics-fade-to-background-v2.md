# Fix Lyrics Fade To Background V2

## Summary

Rendered lyric fade colors currently darken toward black because `FrameRenderer` scales RGB
foreground values directly by alpha. The fix should composite faded text over the current flat
template background color so alpha `0` visually equals `template.background_color`.

This plan intentionally keeps the fix narrow. It does not change fade timing, easing, cache
quantization, templates, public interfaces, generated frame byte format, or fade smoothness.

## Implementation Changes

- Add a private helper on `FrameRenderer` for flat-background RGB compositing:
  - Input: foreground RGB tuple and integer alpha in the existing `0..255` range.
  - Output: RGB tuple computed per channel as
    `background + (foreground - background) * alpha / 255`.
  - Clamp alpha to `0..255` before computing.
  - Use `self.template.background_color` as the background.
- Replace only the current alpha-scaled text fill calculations that do
  `int(color * alpha / 255)`:
  - Intro song info fade in `render_intro_info`.
  - Previous lyric fade shown during blank lyric lines.
  - Current/last lyric fade in `render_lyrics`.
  - Next lyric preview fade in `_render_next_lyric_preview`.
- Preserve the existing RGB frame rendering path. Do not introduce RGBA layers, masks, or new
  per-frame image allocations beyond the existing frame rendering work.
- Keep frame cache key quantization unchanged. Existing fade stepping is out of scope for this
  fix.
- Target the current flat-color template model only. Per-pixel compositing for future gradient or
  image backgrounds is out of scope.

## Test Plan

- Add focused unit coverage in `services/render-worker/tests/test_frame_renderer.py`:
  - Helper returns foreground at alpha `255`.
  - Helper returns `template.background_color` at alpha `0`.
  - Helper clamps alpha values below `0` and above `255`.
  - Helper returns a mid-blend color between foreground and background, not a black-scaled color.
  - A fading lyric rendered on a non-black template background does not create changed lyric pixels
    below the background color.
- Make the rendered-pixel assertion channel-wise and scoped to pixels changed from the background,
  using a template where text/highlight channels are brighter than the background.
- Run:

```bash
PYTHONPATH=src pytest services/render-worker/tests/test_frame_renderer.py -v
```

## Acceptance Criteria

- Lyric fade-out visually ends at the active flat template background color.
- No fade path temporarily renders black text unless the template background itself is black.
- Existing fade timing, easing, cache behavior, generated RGB frame byte format, and public
  interfaces remain unchanged.
- Existing fade smoothness is unchanged and explicitly out of scope.

## Assumptions

- The intended behavior applies to all lyric-related faded text, including intro info, previews,
  and blank-line previous lyric fades.
- The current flat `template.background_color` is the runtime background model for this fix.
- Unit tests are sufficient validation; no frame snapshot or FFmpeg video smoke test is required.
