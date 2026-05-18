# Fix: Lyrics Text Clipping at Frame Edges in Video Rendering

> **Status:** Plan only — not yet implemented.
> **Date:** 2026-05-18

---

## Problem

In generated worship lyric videos, long Chinese text lines are clipped on the left and right edges of the frame. The text is rendered centered with `ctx.textAlign = "center"` at `width / 2`, but there is no bounds checking or margin. When a lyric line is wider than the canvas, the overflow is silently truncated by the frame boundary.

**Measured impact:** Any lyric line wider than the canvas width at the rendered font size will be clipped. At `baseFontSize * 2` (e.g., 96px for preset "M"), a 12-character Chinese line can easily exceed 1920px on a 1080p canvas.

---

## Root Cause

The `FrameRenderer` class (`webapp/src/lib/render/frame-renderer.ts`) renders all text with a fixed font size and no width constraints:

| Text Element | Method | Font Size | Max Width Check? | Margin? |
|-------------|--------|-----------|------------------|---------|
| Current lyric line | `renderLyrics` | `baseFontSize * 2` | No | No |
| Next lyric line | `renderLyrics` | `baseFontSize` | No | No |
| Song title | `renderFrame` | `baseFontSize * 0.8` | No | No |
| Intro info lines | `renderIntroInfo` | `baseFontSize * 0.9` | No | No |
| Title card songset name | `renderTitleCard` | `baseFontSize * 2` | No | No |

The `node-canvas` `fillText()` call does not auto-scale or wrap. Text that exceeds the canvas boundary is simply clipped.

---

## Proposed Fix: Dynamic Font Scaling with Character-Width Margin

### Change 1: Add `fitText()` helper to `FrameRenderer`

**File:** `webapp/src/lib/render/frame-renderer.ts`

Add a private method that measures text at a target font size and scales it down proportionally if it exceeds a maximum width:

```typescript
private fitText(
  ctx: CanvasRenderingContext2D,
  text: string,
  targetFontSize: number,
  maxWidth: number
): number {
  ctx.font = this.getFontString(targetFontSize);
  const metrics = ctx.measureText(text);
  if (metrics.width <= maxWidth) {
    return targetFontSize;
  }
  const scale = maxWidth / metrics.width;
  return Math.floor(targetFontSize * scale);
}
```

### Change 2: Add `getMargin()` helper

Add a private method that computes a single-character margin width. Use `"中"` as the reference character (appropriate for Traditional Chinese lyrics):

```typescript
private getMargin(ctx: CanvasRenderingContext2D, fontSize: number): number {
  ctx.font = this.getFontString(fontSize);
  return ctx.measureText("中").width;
}
```

### Change 3: Apply fitting to all text rendering sites

For each text rendering location, compute `maxWidth = canvasWidth - margin * 2`, call `fitText()`, and re-set `ctx.font` before `fillText()`.

**3a. Current lyric line (`renderLyrics`, ~line 413)**

```typescript
const currentFontSize = this.fitText(
  ctx,
  currentLine.text,
  this.baseFontSize * 2,
  width - this.getMargin(ctx, this.baseFontSize * 2) * 2
);
ctx.font = this.getFontString(currentFontSize);
ctx.fillText(currentLine.text, width / 2, y);
```

**3b. Next lyric line (`renderLyrics`, ~line 433)**

```typescript
const nextFontSize = this.fitText(
  ctx,
  nextLine.text,
  this.baseFontSize,
  width - this.getMargin(ctx, this.baseFontSize) * 2
);
ctx.font = this.getFontString(nextFontSize);
ctx.fillText(nextLine.text, width / 2, nextY);
```

**3c. Song title (`renderFrame`, ~line 206)**

```typescript
const titleFontSize = this.fitText(
  ctx,
  currentTitle,
  Math.floor(this.baseFontSize * 0.8),
  width - this.getMargin(ctx, Math.floor(this.baseFontSize * 0.8)) * 2
);
ctx.font = this.getFontString(titleFontSize);
ctx.fillText(currentTitle, width / 2, 50);
```

**3d. Intro info lines (`renderIntroInfo`, ~line 320)**

Loop through `infoLines`, fit each line individually:

```typescript
const introFontSize = Math.floor(this.baseFontSize * 0.9);
const margin = this.getMargin(ctx, introFontSize);
const maxWidth = width - margin * 2;
ctx.font = this.getFontString(this.fitText(ctx, line, introFontSize, maxWidth));
ctx.fillText(line, width / 2, baseY + i * lineHeight + lineHeight / 2);
```

**3e. Title card songset name (`renderTitleCard`, ~line 460)**

```typescript
const titleCardFontSize = this.fitText(
  ctx,
  config.songsetName,
  this.baseFontSize * 2,
  width - this.getMargin(ctx, this.baseFontSize * 2) * 2
);
ctx.font = this.getFontString(titleCardFontSize);
ctx.fillText(config.songsetName, width / 2, height * 0.4);
```

---

## Why This Approach

| Approach | Pros | Cons |
|----------|------|------|
| **Dynamic font scaling** (chosen) | Preserves full text, simple, no wrapping artifacts | Font size varies per line |
| Text wrapping | Consistent font size | Complex line-breaking for Chinese, harder to read |
| Truncation with ellipsis | Consistent font size | Loses information |
| Fixed smaller font size | Simple | Wastes space on short lines, may still clip very long lines |

Dynamic scaling is the best tradeoff: it guarantees no clipping, preserves readability for most lines, and only shrinks text when necessary.

---

## Files to Change

| File | Change |
|------|--------|
| `webapp/src/lib/render/frame-renderer.ts` | Add `fitText()` and `getMargin()` helpers; apply to 5 text rendering sites |

---

## Verification

1. Generate a video with a song containing a long lyric line (≥12 Chinese characters)
2. Inspect frames at the relevant timestamp — confirm no characters are clipped at left or right edges
3. Confirm short lines retain the original (larger) font size
4. Confirm the title card, intro info, and song title also respect margins
5. Run existing video engine tests to ensure no regressions

---

## Out of Scope

- Refactoring the `FrameRenderer` into smaller classes
- Adding text wrapping or multi-line support
- Changing font family or template colors
- Adjusting vertical positioning of lyrics
- Any changes to the web player UI (this is a video generation issue, not a playback issue)
