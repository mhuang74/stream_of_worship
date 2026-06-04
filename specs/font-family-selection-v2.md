# Font Family Selection for Render (v2)

## Summary

Add a `fontFamily` field that flows end-to-end: **webapp UI → DB → API → render worker → frame renderer**, allowing users to choose from curated Google Fonts for rendered lyrics videos.

## Font Options

| Value | Label | Google Font | CSS/Render Fallback |
|---|---|---|---|
| `lxgw_wenkai_tc` | Traditional | LXGW WenKai TC | Noto Serif TC |
| `chocolate_classical_sans` | Elegant | Chocolate Classical Sans | Noto Serif TC |
| `chiron_goround_tc` | Modern | Chiron GoRound TC | Noto Serif TC |
| `noto_serif_tc` | Classic (default) | Noto Serif TC | Noto Sans CJK TC |

All four fonts have full Traditional Chinese coverage suitable for worship lyrics.

## Design Decisions

- **Storage scope**: Both — user default in `user_settings` + per-job override in `render_jobs` (matches existing `fontSizePreset` pattern)
- **Font delivery to Lambda**: Vendor TTF files in the repo at `services/render-worker/fonts/` and COPY into Docker image at build time (zero runtime latency, no network dependency, deterministic filenames, fully reproducible builds)
- **Fallback scope**: Both Docker + CSS — Noto Serif TC installed in Docker image via yum AND used as CSS fallback in webapp
- **Font weights**: Regular only — lyrics videos use a single weight; bold/italic not needed
- **Shared constants**: `FONT_FAMILIES` constant defined once in `webapp/src/lib/constants.ts`, imported by both `RenderForm.tsx` and `SettingsForm.tsx`

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

### `webapp/src/lib/constants.ts`

Add shared `FONT_FAMILIES` constant (imported by both RenderForm and SettingsForm):
```ts
export const FONT_FAMILIES = [
  { value: "lxgw_wenkai_tc", label: "Traditional", cssFamily: "LXGW WenKai TC" },
  { value: "chocolate_classical_sans", label: "Elegant", cssFamily: "Chocolate Classical Sans" },
  { value: "chiron_goround_tc", label: "Modern", cssFamily: "Chiron GoRound TC" },
  { value: "noto_serif_tc", label: "Classic", cssFamily: "Noto Serif TC" },
] as const

export const VALID_FONT_FAMILIES = FONT_FAMILIES.map(f => f.value)
```

### `webapp/src/app/api/render-jobs/route.ts`

- Add `fontFamily` to Zod schema:
  ```ts
  fontFamily: z.enum(["lxgw_wenkai_tc", "chocolate_classical_sans", "chiron_goround_tc", "noto_serif_tc"]).optional()
  ```

### `webapp/src/lib/render/job-manager.ts`

- Add `fontFamily?: string` to `CreateRenderJobInput`
- Add `fontFamily: string` to `RenderJob`
- Add `fontFamily: row.fontFamily` to `mapRowToRenderJob()`
- Add `fontFamily: input.fontFamily ?? "noto_serif_tc"` to `createRenderJob()` insert values

### `webapp/src/app/api/settings/route.ts`

- Add `defaultFontFamily` to `DEFAULTS` constant:
  ```ts
  defaultFontFamily: "noto_serif_tc",
  ```
- Add `VALID_FONT_FAMILIES` constant (import from `constants.ts` or define inline matching the same values)
- Validate and persist `defaultFontFamily` to `user_settings` (same pattern as `defaultFontSizePreset`)

---

## Phase 3: Webapp — UI Components

### `webapp/src/components/render/RenderForm.tsx`

1. Add `fontFamily` to `RenderFormData` type:
   ```ts
   fontFamily: "lxgw_wenkai_tc" | "chocolate_classical_sans" | "chiron_goround_tc" | "noto_serif_tc"
   ```

2. Import `FONT_FAMILIES` from `@/lib/constants`

3. Add a "Font" `<Select>` dropdown in the Video Settings card (between Template and Resolution selectors)

4. Default `fontFamily` from `initialData?.fontFamily ?? "noto_serif_tc"`

5. Add a **font preview card** below the font selector showing sample Chinese worship lyrics rendered in the selected font:
   ```tsx
   <div className="mt-2 rounded-md border bg-muted/50 p-4 text-center"
        style={{ fontFamily: `'${selectedFont.cssFamily}', 'Noto Serif TC', serif` }}>
     耶和華是我的牧者<br/>我必不至缺乏
   </div>
   ```

### `webapp/src/components/settings/SettingsForm.tsx`

- Add `defaultFontFamily: string` to `UserSettingsData`
- Import `FONT_FAMILIES` from `@/lib/constants`
- Add "Default font family" `<Select>` dropdown (same pattern as `defaultFontSizePreset`)
- Add the same font preview card below the selector

### `webapp/src/app/songsets/[id]/render/page.tsx`

- Pass `fontFamily` from completed job data as `initialData.fontFamily`
- Include `fontFamily` in the submit payload to the API

---

## Phase 4: Render Worker — Font Loading

### `services/render-worker/fonts/` (new directory)

Vendor the Regular-weight TTF files in the repo:

```
services/render-worker/fonts/
├── LXGWWenKaiTC-Regular.ttf
├── ChocolateClassicalSans-Regular.ttf
├── ChironGoRoundTC-Regular.ttf
└── NotoSerifTC-Regular.ttf
```

Source: Download from Google Fonts CSS `@font-face` declarations at implementation time to get exact TTF URLs. Commit the files directly.

### `services/render-worker/Dockerfile` and `Dockerfile.dev`

Replace the fragile `curl | unzip` approach with a simple `COPY`:

```dockerfile
# Install vendor fonts for render video
COPY fonts/ /usr/share/fonts/truetype/vendor/

# Install Noto Serif CJK as fallback (yum package provides .ttc files)
RUN yum install -y google-noto-serif-cjk-fonts && \
    yum clean all && \
    rm -rf /var/cache/yum
```

This replaces the existing `google-noto-sans-cjk-fonts` yum install with `google-noto-serif-cjk-fonts` (matching the default font choice). The vendor fonts are copied from the repo — no network dependency at build time.

### `services/render-worker/src/sow_render_worker/frame_renderer.py`

1. Add font family type:
   ```python
   FontFamily = Literal["lxgw_wenkai_tc", "chocolate_classical_sans", "chiron_goround_tc", "noto_serif_tc"]
   ```

2. Add font family path mapping (all paths verified against Dockerfile COPY target + yum install locations):
   ```python
   FONT_FAMILY_PATHS: dict[FontFamily, list[str]] = {
       "lxgw_wenkai_tc": [
           "/usr/share/fonts/truetype/vendor/LXGWWenKaiTC-Regular.ttf",
       ],
       "chocolate_classical_sans": [
           "/usr/share/fonts/truetype/vendor/ChocolateClassicalSans-Regular.ttf",
       ],
       "chiron_goround_tc": [
           "/usr/share/fonts/truetype/vendor/ChironGoRoundTC-Regular.ttf",
       ],
       "noto_serif_tc": [
           "/usr/share/fonts/truetype/vendor/NotoSerifTC-Regular.ttf",
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
       for path in _SANS_SERIF_FONT_PATHS:
           try:
               return ImageFont.truetype(path, size)
           except (OSError, IOError):
               continue
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
- Add `font_family=row.get("font_family") or "noto_serif_tc"` to `_row_to_render_job()` deserialization
- Note: SQL queries use `SELECT *` so no query changes needed — the new column is automatically included once the DB migration runs

---

## Phase 5: Google Fonts CSS (Webapp)

### `webapp/src/app/songsets/[id]/render/page.tsx`

Load Google Fonts for live preview using `next/font/google`:

```tsx
import { LXGW_WenKai_TC, Chocolate_Classical_Sans, Chiron_GoRound_TC, Noto_Serif_TC } from "next/font/google"

const lxgwWenkai = LXGW_WenKai_TC({ weight: "400", subsets: ["latin-ext"], variable: "--font-lxgw-wenkai" })
const chocolateClassical = Chocolate_Classical_Sans({ weight: "400", subsets: ["latin-ext"], variable: "--font-chocolate" })
const chironGoRound = Chiron_GoRound_TC({ weight: "400", subsets: ["latin-ext"], variable: "--font-chiron" })
const notoSerifTC = Noto_Serif_TC({ weight: "400", subsets: ["latin-ext"], variable: "--font-noto-serif" })
```

Apply CSS variables to the page container so the preview card can reference them. The preview card uses inline `style` with the selected font's CSS variable or family name.

> Note: Exact `next/font/google` import names and subset options need verification at implementation time. Next.js auto-detects available fonts. If a font is not in `next/font/google`, fall back to a `<link>` stylesheet in the render page layout.

---

## Phase 6: Tests

### Webapp tests

- `webapp/src/test/api/render-jobs/route.test.ts` — Add `fontFamily` validation test cases (valid enum values accepted, invalid value rejected)
- `webapp/src/test/components/render/RenderForm.test.tsx` — Test font family select renders, defaults to "noto_serif_tc", submits correct value, preview card shows sample text
- `webapp/src/test/db/schema.test.ts` — Assert `fontFamily` column exists on `renderJobs` with default "noto_serif_tc"

### Render worker tests

- `services/render-worker/tests/test_frame_renderer.py` — Test `_load_font` with each font family, test fallback behavior when font file missing, test `font_family` parameter on `FrameRenderer`
- `services/render-worker/tests/test_video_engine.py` — Test `font_family` parameter passed through to FrameRenderer
- `services/render-worker/tests/test_pipeline.py` — Add `"font_family": "noto_serif_tc"` to test data

---

## Files Changed Summary

| File | Change |
|---|---|
| `webapp/src/db/schema.ts` | Add `fontFamily` to `renderJobs`, `defaultFontFamily` to `userSettings` |
| `webapp/src/lib/constants.ts` | Add `FONT_FAMILIES` and `VALID_FONT_FAMILIES` shared constants |
| `webapp/src/app/api/render-jobs/route.ts` | Add `fontFamily` to Zod schema |
| `webapp/src/lib/render/job-manager.ts` | Add `fontFamily` to interfaces and mapping |
| `webapp/src/app/api/settings/route.ts` | Add `defaultFontFamily` validation |
| `webapp/src/components/render/RenderForm.tsx` | Add font family select + preview card |
| `webapp/src/components/settings/SettingsForm.tsx` | Add default font family select + preview card |
| `webapp/src/app/songsets/[id]/render/page.tsx` | Pass `fontFamily` in initialData/submit, load Google Fonts via `next/font/google` |
| `services/render-worker/fonts/*.ttf` | **New** — vendored TTF font files |
| `services/render-worker/Dockerfile` | Replace yum sans-cjk with serif-cjk; COPY vendor fonts |
| `services/render-worker/Dockerfile.dev` | Same Dockerfile changes |
| `services/render-worker/src/sow_render_worker/frame_renderer.py` | Add `FontFamily` type, `FONT_FAMILY_PATHS`, refactor `_load_font` |
| `services/render-worker/src/sow_render_worker/video_engine.py` | Add `font_family` param |
| `services/render-worker/src/sow_render_worker/pipeline.py` | Pass `font_family` to VideoEngine |
| `services/render-worker/src/sow_render_worker/db.py` | Add `font_family` to RenderJob |
| Test files (6 files) | Add font family test cases |

---

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Vendored TTF filenames don't match actual Google Fonts filenames | Verify exact filenames when downloading at implementation time; filenames are deterministic per font release |
| `@lru_cache` signature change breaks existing callers of `_load_font(size)` | All callers go through `FrameRenderer._get_font()` which passes `font_family`; direct callers need updating |
| Docker image size increase (~5-10MB per font, ~20-40MB total) | Acceptable for Lambda; fonts are critical for rendering. Vendored approach avoids network at build time |
| Font rendering differences between browser preview and rendered video | Inherent limitation; both use the same TTF source, but Pillow vs browser rasterization may differ slightly. Preview card sets expectations |
| `next/font/google` may not support all four fonts | Fall back to `<link>` stylesheet in render page layout if `next/font/google` doesn't list a font |
| `noto_serif_tc` yum package path differs across Amazon Linux versions | `FONT_FAMILY_PATHS` lists multiple candidate paths; vendored TTF is tried first (guaranteed to exist) |
