# Plan: Stream of Worship browser tab icon (favicon)

Status: prompt only — NOT implemented. Hand this prompt to an implementation agent as-is.

## Prompt

Create a browser tab icon (favicon) for "Stream of Worship" — a web app for small-group
worship: it plays songs and lyric videos with seamless transitions between songs.

Brand context
- The brand is strictly monochrome. Header wordmark is near-black on white (light mode),
  near-white on near-black (dark mode). Do NOT introduce any hue.
- Palette (from the app theme): tile oklch(0.205 0 0) ≈ #1E1E1E; glyph oklch(0.985 0 0) ≈ #FCFCFC.
- App aesthetic: flat design, rounded corners (--radius 0.625rem), minimal.

Design direction
- Flat vector. One glyph centered on an opaque rounded-square tile (corner radius ≈ 22% of canvas).
- Glyph: a flowing sound-wave — two horizontal wave strokes crossing the tile left-to-right;
  top stroke solid white, bottom stroke white at ~55% opacity; rounded line caps;
  stroke width ≈ 7% of canvas width. It must read as a continuous stream of music
  (the product's core promise: songs flowing into each other without breaks).
- Fallback if the wave turns to mush at small sizes: three rounded horizontal bars
  (equalizer style, middle one longest) on the same tile.
- Forbidden: text/letters, crosses or any religious symbols, gradients, shadows,
  more than two glyph elements, transparency in the tile.
- Must stay legible at 16×16 px: bold silhouette, high contrast, no thin detail.

Deliverables (repo root: delivery/webapp/)
1. design/icon.svg — master vector, square 512×512 viewBox, flat fills only.
2. src/app/favicon.ico — REPLACE the existing file (it is the default Next.js scaffold
   icon: black circle with a white triangle). Multi-resolution ICO containing exactly:
   16×16, 32×32, 48×48, 256×256.
3. src/app/apple-icon.png — 180×180 PNG, opaque tile version (Next.js picks this up
   automatically for apple-touch-icon).

Build method
- Render the vector at each target size (headless Chromium screenshot of the SVG at each
  size, or resvg). Do not obtain small sizes by downscaling a large raster.
- When rasterizing with headless Chrome, the screenshot must have a TRANSPARENT background
  outside the rounded tile: pass `--default-background-color=00000000` (and do not paint a
  page background). The rounded-corner area outside the tile must stay alpha=0 so the icon
  is not a white square in dark browser themes.
- Assemble the ICO from the four separately rendered PNGs (16/32/48/256) — Pillow ≥9.1
  matches frames by exact size:
    img256.save("src/app/favicon.ico",
                sizes=[(16,16),(32,32),(48,48),(256,256)],
                append_images=[img16, img32, img48])
  (`sizes=` alone on a single base image downscales — that defeats the per-size rendering.)

Acceptance checks
- `pnpm dev`, open http://localhost:8080 in a browser: tab shows the new icon
  (hard-refresh — favicons cache aggressively).
- Inspect each ICO layer at 100% zoom: the 16px layer must read as wave-on-tile.
- Alpha check (script, e.g. with Pillow): for every ICO frame and the apple-icon PNG,
  load and assert the corner pixels (outside the rounded tile) have alpha == 0; the tile
  interior must be fully opaque (alpha == 255). `read :img` previews render on white and
  CANNOT reveal this defect — the scripted alpha assertion is required.
- Only the three files above are created/modified; nothing else in public/ is touched.