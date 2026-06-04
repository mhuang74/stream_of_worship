# Font Family Selection for Render

## Summary

Add a `fontFamily` field that flows end-to-end: **webapp UI → DB → API → render worker → frame renderer**, allowing users to choose from curated Google Fonts for rendered lyrics videos.

## Font Options

| Value | Label | Google Font | CSS/Render Fallback |
|---|---|---|---|
| `zen_old_mincho` | Traditional | Zen Old Mincho | Noto Serif TC |
| `zen_maru_gothic` | Elegant | Zen Maru Gothic | Noto Serif TC |
| `ibm_plex_sans_jp` | Modern | IBM Plex Sans JP | Noto Serif TC |
| `noto_serif_tc` | Classic (default) | Noto Serif TC | Noto Sans CJK |

## Design Decisions

- **Storage scope**: Both — user default in `user_settings` + per-job override in `render_jobs` (matches existing `fontSizePreset` pattern)
- **Font delivery to Lambda**: Bundle TTF files in Docker image at build time (zero runtime latency, no network dependency)
- **Fallback scope**: Both Docker + CSS — Noto Serif TC installed in Docker image AND used as CSS fallback in webapp

---

## Phase 1: Database Schema

### `webapp/src/db/schema.ts`

1. Add `fontFamily` column to `renderJobs` table:
   ```ts
   fontFamily: text("font_family").notNull().default("noto_serif_tc")
   ```

2. Add `defaultFontFamily` column to `userSettings` table:
   ```ts
   defaultFontFamily: text("default_font_family").notNull().default("noto_serif_tc")
   ```

3. Run migration: `npx drizzle-kit push`

---

## Phase 2: Webapp — API Layer

### `webapp/src/app/api/render-jobs/route.ts`

- Add `fontFamily` to Zod schema:
  ```ts
  fontFamily: z.enum(["zen_old_mincho", "zen_maru_gothic", "ibm_plex_sans_jp", "noto_serif_tc"]).optional()
  ```

### `webapp/src/lib/render/job-manager.ts`

- Add `fontFamily?: string` to `CreateRenderJobInput`
- Add `fontFamily: string` to `RenderJob`
- Add `fontFamily: row.fontFamily` to `mapRowToRenderJob()`
- Add `fontFamily: input.fontFamily ?? "noto_serif_tc"` to `createRenderJob()` insert values

### `webapp/src/app/api/settings/route.ts`

- Add `defaultFontFamily` validation alongside existing `defaultFontSizePreset`
- Add `VALID_FONT_FAMILIES = ["zen_old_mincho", "zen_maru_gothic", "ibm_plex_sans_jp", "noto_serif_tc"]`
- Validate and persist `defaultFontFamily` to `user_settings`

---

## Phase 3: Webapp — UI Components

### `webapp/src/components/render/RenderForm.tsx`

1. Add `fontFamily` to `RenderFormData` type:
   ```ts
   fontFamily: "zen_old_mincho" | "zen_maru_gothic" | "ibm_plex_sans_jp" | "noto_serif_tc"
   ```

2. Add `FONT_FAMILIES` constant:
   ```ts
   const FONT_FAMILIES = [
     { value: "zen_old_mincho", label: "Traditional", cssFamily: "Zen Old Mincho" },
     { value: "zen_maru_gothic", label: "Elegant", cssFamily: "Zen Maru Gothic" },
     { value: "ibm_plex_sans_jp", label: "Modern", cssFamily: "IBM Plex Sans JP" },
     { value: "noto_serif_tc", label: "Classic", cssFamily: "Noto Serif TC" },
   ] as const
   ```

3. Add a "Font" `<Select>` dropdown in the Video Settings card (between Template and Resolution selectors)

4. Default `fontFamily` from `initialData?.fontFamily ?? "noto_serif_tc"`

### `webapp/src/components/settings/SettingsForm.tsx`

- Add `defaultFontFamily: string` to `UserSettingsData`
- Add "Default font family" `<Select>` dropdown (reuse `FONT_FAMILIES` constant or define inline)

### `webapp/src/app/songsets/[id]/render/page.tsx`

- Pass `fontFamily` from completed job data as `initialData.fontFamily`
- Include `fontFamily` in the submit payload to the API

---

## Phase 4: Render Worker — Font Loading

### `services/render-worker/Dockerfile` and `Dockerfile.dev`

Add `RUN` steps to download Google Font TTF files at build time:

```dockerfile
# Install Google Fonts for render video
RUN mkdir -p /usr/share/fonts/truetype/google && \
    curl -fsSL "https://fonts.google.com/download?family=Zen+Old+Mincho" -o /tmp/zen-old-mincho.zip && \
    unzip -o /tmp/zen-old-mincho.zip -d /usr/share/fonts/truetype/google/ && \
    curl -fsSL "https://fonts.google.com/download?family=Zen+Maru+Gothic" -o /tmp/zen-maru-gothic.zip && \
    unzip -o /tmp/zen-maru-gothic.zip -d /usr/share/fonts/truetype/google/ && \
    curl -fsSL "https://fonts.google.com/download?family=IBM+Plex+Sans+JP" -o /tmp/ibm-plex-sans-jp.zip && \
    unzip -o /tmp/ibm-plex-sans-jp.zip -d /usr/share/fonts/truetype/google/ && \
    rm -rf /tmp/*.zip

# Install Noto Serif CJK as fallback
RUN yum install -y google-noto-serif-cjk-fonts && \
    yum clean all && \
    rm -rf /var/cache/yum
```

> Note: Exact download URLs and file names need to be verified at implementation time. Google Fonts direct download links may vary. An alternative is to use the `google-gfonts` npm package or manually curl specific TTF file URLs from the Google Fonts CSS `@font-face` declarations.

### `services/render-worker/src/sow_render_worker/frame_renderer.py`

1. Add font family type:
   ```python
   FontFamily = Literal["zen_old_mincho", "zen_maru_gothic", "ibm_plex_sans_jp", "noto_serif_tc"]
   ```

2. Add font family path mapping:
   ```python
   FONT_FAMILY_PATHS: dict[FontFamily, list[str]] = {
       "zen_old_mincho": [
           "/usr/share/fonts/truetype/google/ZenOldMincho-Regular.ttf",
       ],
       "zen_maru_gothic": [
           "/usr/share/fonts/truetype/google/ZenMaruGothic-Regular.ttf",
       ],
       "ibm_plex_sans_jp": [
           "/usr/share/fonts/truetype/google/IBMPlexSansJP-Regular.ttf",
       ],
       "noto_serif_tc": [
           "/usr/share/fonts/truetype/google/NotoSerifTC-Regular.ttf",
           "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
           "/usr/share/fonts/noto-cjk/NotoSerifCJK-Regular.ttc",
       ],
   }
   ```

3. Refactor `_load_font` to accept `font_family` parameter:
   ```python
   @lru_cache(maxsize=128)
   def _load_font(size: int, font_family: str = "noto_serif_tc") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
       paths = FONT_FAMILY_PATHS.get(font_family, FONT_FAMILY_PATHS["noto_serif_tc"])
       for path in paths:
           try:
               font = ImageFont.truetype(path, size)
               return font
           except (OSError, IOError):
               continue
       # Fallback to legacy sans-serif paths
       for path in _SANS_SERIF_FONT_PATHS:
           try:
               return ImageFont.truetype(path, size)
           except (OSError, IOError):
               continue
       # Final fallback
       try:
           return ImageFont.truetype("sans-serif", size)
       except (OSError, IOError):
           return ImageFont.load_default(size=size)
   ```

4. Add `font_family` parameter to `FrameRenderer.__init__`:
   ```python
   def __init__(self, template, font_size_preset="M", font_family="noto_serif_tc", resolution=None):
       self.font_family = font_family
       ...
   ```

5. Update `_get_font` to pass `font_family`:
   ```python
   def _get_font(self, size):
       return _load_font(size, self.font_family)
   ```

### `services/render-worker/src/sow_render_worker/video_engine.py`

- Add `font_family: FontFamily = "noto_serif_tc"` parameter to `VideoEngine.__init__`
- Store as `self.font_family`
- Pass to `FrameRenderer(font_family=self.font_family, ...)`

### `services/render-worker/src/sow_render_worker/pipeline.py`

- Pass `font_family=job.font_family` to `VideoEngine(...)` constructor (line ~370)

### `services/render-worker/src/sow_render_worker/db.py`

- Add `font_family: str = "noto_serif_tc"` to `RenderJob` dataclass
- Add `font_family=row.get("font_family") or "noto_serif_tc"` to deserialization

---

## Phase 5: Google Fonts CSS (Webapp)

### `webapp/src/app/layout.tsx` or render page

Load Google Fonts for live preview in the browser. Two approaches:

**Option A (preferred): `next/font/google` in render page component**
- Import each font via `next/font/google` in the render page
- Apply via CSS variables or className
- Optimized: only loads on pages that need it

**Option B: Global `<link>` in layout.tsx**
- Add `<link>` to Google Fonts CSS stylesheet in root layout
- Simpler but loads fonts on every page

The font preview should show each font name rendered in its own typeface in the `<Select>` dropdown.

---

## Phase 6: Tests

### Webapp tests

- `webapp/src/test/api/render-jobs/route.test.ts` — Add `fontFamily` validation test cases (valid enum values, invalid value rejected)
- `webapp/src/test/components/render/RenderForm.test.tsx` — Test font family select renders, defaults to "noto_serif_tc", submits correct value
- `webapp/src/test/db/schema.test.ts` — Assert `fontFamily` column exists on `renderJobs` with default "noto_serif_tc"

### Render worker tests

- `services/render-worker/tests/test_frame_renderer.py` — Test `_load_font` with each font family, test fallback behavior
- `services/render-worker/tests/test_video_engine.py` — Test `font_family` parameter passed through to FrameRenderer
- `services/render-worker/tests/test_pipeline.py` — Add `"font_family": "noto_serif_tc"` to test data

---

## Files Changed Summary

| File | Change |
|---|---|
| `webapp/src/db/schema.ts` | Add `fontFamily` to `renderJobs`, `defaultFontFamily` to `userSettings` |
| `webapp/src/app/api/render-jobs/route.ts` | Add `fontFamily` to Zod schema |
| `webapp/src/lib/render/job-manager.ts` | Add `fontFamily` to interfaces and mapping |
| `webapp/src/app/api/settings/route.ts` | Add `defaultFontFamily` validation |
| `webapp/src/components/render/RenderForm.tsx` | Add font family select dropdown |
| `webapp/src/components/settings/SettingsForm.tsx` | Add default font family select |
| `webapp/src/app/songsets/[id]/render/page.tsx` | Pass `fontFamily` in initialData and submit |
| `services/render-worker/Dockerfile` | Add Google Fonts download steps |
| `services/render-worker/Dockerfile.dev` | Add Google Fonts download steps |
| `services/render-worker/src/sow_render_worker/frame_renderer.py` | Add `FontFamily` type, `FONT_FAMILY_PATHS`, refactor `_load_font` |
| `services/render-worker/src/sow_render_worker/video_engine.py` | Add `font_family` param |
| `services/render-worker/src/sow_render_worker/pipeline.py` | Pass `font_family` to VideoEngine |
| `services/render-worker/src/sow_render_worker/db.py` | Add `font_family` to RenderJob |
| Test files (6 files) | Add font family test cases |

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Google Fonts download URLs change or are unavailable at Docker build time | Pin specific font file URLs; add fallback to existing Noto Sans CJK if all font paths fail |
| TTF file names inside Google Fonts ZIPs don't match expected paths | Verify exact file names at implementation time; use `find` in Dockerfile to locate and rename |
| `@lru_cache` signature change breaks existing callers of `_load_font(size)` | All callers go through `FrameRenderer._get_font()` which passes `font_family`; direct callers need updating |
| Docker image size increase (~5-10MB per font) | Acceptable for Lambda; fonts are critical for rendering |
| Font rendering differences between browser preview and rendered video | Inherent limitation; both use the same TTF source, but Pillow vs browser rasterization may differ slightly |
