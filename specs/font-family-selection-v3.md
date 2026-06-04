# Font Family Selection for Render (v3)

## Summary

Add a `fontFamily` setting that flows end-to-end:

**webapp UI -> DB -> API -> render worker -> frame renderer**

Users can choose from curated Traditional Chinese-capable fonts for rendered lyrics videos.

This version clarifies default behavior:

- `user_settings.default_font_family` is the default for a songset that has no remembered render configuration.
- Once a user changes any render parameter for a songset and submits a render, that job's render configuration becomes the remembered songset-specific configuration.
- Returning to the render page for that songset should restore the remembered job settings, not revert to `user_settings`.
- Changes to global settings affect future songsets or songsets without remembered render settings only.

## Font Options

| Value | UI Label | Font Name | Render Fallback |
|---|---|---|---|
| `lxgw_wenkai_tc` | Traditional - LXGW WenKai TC | LXGW WenKai TC | Noto Serif TC |
| `chocolate_classical_sans` | Elegant - Chocolate Classical Sans | Chocolate Classical Sans | Noto Serif TC |
| `chiron_goround_tc` | Modern - Chiron GoRound TC | Chiron GoRound TC | Noto Serif TC |
| `noto_serif_tc` | Classic - Noto Serif TC | Noto Serif TC | Noto Sans CJK TC |

All four selected fonts must be verified at implementation time for Traditional Chinese lyric coverage, browser availability, render-worker availability, and licensing.

## Product Behavior

### Initial Render Defaults

When a user opens the render page for a songset:

1. If the songset has a latest completed, failed, queued, or running render job with stored render parameters, initialize the form from that job.
2. Otherwise, initialize the form from `user_settings`.
3. If the user has no saved settings row, initialize from app defaults.

This should apply consistently to all render parameters that have user defaults, including:

- template
- resolution
- font size preset
- font family
- offline option, if the product already treats it as a render default

### Remembered Songset Choices

Submitting a render stores the selected `fontFamily` on `render_jobs`, together with the other render parameters.

For that songset, future visits to the render page should restore from the latest relevant job. This preserves user intent after they adjust any render parameter for that songset.

Changing Settings later must not overwrite remembered render choices for existing songsets that already have stored job settings.

### Re-render From Completed Job

When a completed render exists, the render form should use that job's full configuration as `initialData`, including `fontFamily`.

If an older job predates the `font_family` column or has an invalid/missing value, fall back to `noto_serif_tc`.

## Phase 1: Database Schema

### `webapp/src/db/schema.ts`

Add `fontFamily` column to `renderJobs`:

```ts
fontFamily: text("font_family").notNull().default("noto_serif_tc")
```

Add `defaultFontFamily` column to `userSettings`:

```ts
defaultFontFamily: text("default_font_family").notNull().default("noto_serif_tc")
```

### Migration

Generate a migration rather than relying only on push:

```bash
cd webapp
npx drizzle-kit generate
npx drizzle-kit migrate
```

Operational rollout order:

1. Apply DB migration first.
2. Deploy worker code that can read `font_family` and safely default missing/unknown values.
3. Deploy webapp API/UI code that writes and displays `fontFamily`.

## Phase 2: Shared Constants

### `webapp/src/lib/constants.ts`

Add shared font metadata:

```ts
export const FONT_FAMILIES = [
  {
    value: "lxgw_wenkai_tc",
    label: "Traditional - LXGW WenKai TC",
    cssFamily: "LXGW WenKai TC",
    cssVariable: "--font-lxgw-wenkai-tc",
  },
  {
    value: "chocolate_classical_sans",
    label: "Elegant - Chocolate Classical Sans",
    cssFamily: "Chocolate Classical Sans",
    cssVariable: "--font-chocolate-classical-sans",
  },
  {
    value: "chiron_goround_tc",
    label: "Modern - Chiron GoRound TC",
    cssFamily: "Chiron GoRound TC",
    cssVariable: "--font-chiron-goround-tc",
  },
  {
    value: "noto_serif_tc",
    label: "Classic - Noto Serif TC",
    cssFamily: "Noto Serif TC",
    cssVariable: "--font-noto-serif-tc",
  },
] as const

export const VALID_FONT_FAMILIES = FONT_FAMILIES.map((font) => font.value)
export type FontFamilyValue = (typeof FONT_FAMILIES)[number]["value"]
```

Use this single source for:

- Render form select options
- Settings form select options
- API validation
- Browser preview mapping

## Phase 3: Webapp API

### `webapp/src/app/api/render-jobs/route.ts`

Add `fontFamily` to the create-job Zod schema:

```ts
fontFamily: z.enum([
  "lxgw_wenkai_tc",
  "chocolate_classical_sans",
  "chiron_goround_tc",
  "noto_serif_tc",
]).optional()
```

Include `fontFamily` in the active-job conflict response config if the UI displays existing-job configuration.

### `webapp/src/lib/render/job-manager.ts`

Add `fontFamily?: string` to `CreateRenderJobInput`.

Add `fontFamily: string` to `RenderJob`.

Map `row.fontFamily` in `mapRowToRenderJob()`, with a defensive fallback:

```ts
fontFamily: row.fontFamily ?? "noto_serif_tc"
```

Insert:

```ts
fontFamily: input.fontFamily ?? "noto_serif_tc"
```

### `webapp/src/app/api/settings/route.ts`

Add `defaultFontFamily` to `DEFAULTS`.

Validate against `VALID_FONT_FAMILIES`.

Return and persist `defaultFontFamily`.

Avoid inline duplicate enum lists where practical; import the shared constants.

## Phase 4: Webapp Settings UI

### `webapp/src/app/settings/page.tsx`

Add `defaultFontFamily` to `DEFAULT_SETTINGS`.

Ensure fetched settings are merged with this value:

```ts
return { ...DEFAULT_SETTINGS, ...data.settings }
```

### `webapp/src/components/settings/SettingsForm.tsx`

Add `defaultFontFamily` to `UserSettingsData`.

Add a "Default font family" select in the Video card near default font size.

Show labels that include the font name, not only style words.

Add a compact preview below the selector using the same preview component or helper used by the render form.

## Phase 5: Render Form Defaults and UI

### `webapp/src/app/songsets/[id]/render/page.tsx`

Load user settings before constructing `initialData`.

Initialization precedence:

1. Latest job configuration for the songset, if available.
2. User settings.
3. App defaults.

Include `fontFamily` in `initialData` when loading a latest job.

Include `fontFamily` in the submit payload:

```ts
fontFamily: formData.fontFamily
```

### `webapp/src/components/render/RenderForm.tsx`

Add `fontFamily` to `RenderFormData`:

```ts
fontFamily:
  | "lxgw_wenkai_tc"
  | "chocolate_classical_sans"
  | "chiron_goround_tc"
  | "noto_serif_tc"
```

Default from `initialData?.fontFamily ?? "noto_serif_tc"`.

Add a "Font family" select in Video Settings near the font size selector.

Add a preview card using sample Traditional Chinese worship lyrics:

```tsx
耶和華是我的牧者
我必不至缺乏
```

The preview should use the loaded browser font class/variable, not only a raw family name.

## Phase 6: Browser Font Loading

Prefer `next/font/google` only if all chosen fonts are supported by the installed Next.js version.

If any font is not supported by `next/font/google`, use a page-level stylesheet link for all preview fonts to keep the implementation consistent.

The preview implementation must satisfy:

- The selected preview visibly changes for each option.
- The preview uses the same named font family as the option selected.
- If loading fails, the UI still renders with the documented fallback.

Implementation note: do not assume `next/font/google` imports make raw CSS family names globally available. Wire generated classes or CSS variables explicitly into the preview.

## Phase 7: Render Worker Font Assets

### `services/render-worker/fonts/`

Vendor regular-weight font files:

```text
services/render-worker/fonts/
├── LXGWWenKaiTC-Regular.ttf
├── ChocolateClassicalSans-Regular.ttf
├── ChironGoRoundTC-Regular.ttf
└── NotoSerifTC-Regular.ttf
```

Also commit a small manifest:

```text
services/render-worker/fonts/MANIFEST.md
```

The manifest must include:

- Font display name
- Source URL or release tag
- License name
- License URL
- File checksum
- Date downloaded

Prefer canonical upstream release artifacts over UA-dependent Google Fonts CSS URLs. If Google Fonts CSS must be used, record the exact CSS URL, generated font file URL, checksum, and date.

### Dockerfiles

Update both:

- `services/render-worker/Dockerfile`
- `services/render-worker/Dockerfile.dev`

Copy vendored fonts:

```dockerfile
COPY fonts/ /usr/share/fonts/truetype/vendor/
```

Install a CJK fallback package. If switching from Noto Sans CJK to Noto Serif CJK, update Python fallback paths accordingly. Keep a final Noto Sans fallback as an emergency fallback only if the package is still installed.

## Phase 8: Render Worker Code

### `services/render-worker/src/sow_render_worker/frame_renderer.py`

Add:

```python
FontFamily = Literal[
    "lxgw_wenkai_tc",
    "chocolate_classical_sans",
    "chiron_goround_tc",
    "noto_serif_tc",
]
```

Add font path mapping with vendored font first and CJK fallback paths second.

Refactor `_load_font` to accept `font_family`:

```python
@lru_cache(maxsize=128)
def _load_font(
    size: int,
    font_family: str = "noto_serif_tc",
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    paths = FONT_FAMILY_PATHS.get(font_family, FONT_FAMILY_PATHS["noto_serif_tc"])
    ...
```

Add `font_family` to `FrameRenderer.__init__` and include it in logs.

Update `_get_font()` to pass `self.font_family`.

Update the frame-cache key to include `self.font_family`; otherwise a long-lived renderer or future reuse could serve cached frames rendered with a different font.

### `services/render-worker/src/sow_render_worker/video_engine.py`

Add `font_family` parameter to `VideoEngine.__init__`.

Store it and pass it to `FrameRenderer`.

### `services/render-worker/src/sow_render_worker/pipeline.py`

Pass `font_family=job.font_family` to `VideoEngine`.

### `services/render-worker/src/sow_render_worker/db.py`

Add `font_family: str = "noto_serif_tc"` to `RenderJob`.

Deserialize with fallback:

```python
font_family=row.get("font_family") or "noto_serif_tc"
```

If an unexpected value is read, log a warning and use `noto_serif_tc`.

## Phase 9: Tests

### Webapp Tests

Add or update:

- `webapp/src/test/api/render-jobs/route.test.ts`
  - valid font families accepted
  - invalid font family rejected
  - created job response includes `fontFamily`
- `webapp/src/test/api/settings/route.test.ts`
  - `defaultFontFamily` returned with defaults
  - valid value persists
  - invalid value rejected
- `webapp/src/test/components/settings/SettingsForm.test.tsx`
  - default font select renders
  - changing it enables save and submits the value
  - preview text appears
- `webapp/src/test/components/render/RenderForm.test.tsx`
  - initializes from `initialData.fontFamily`
  - submits selected value
  - preview updates when selection changes
- Render page test, if existing test infrastructure supports it:
  - no latest job uses `user_settings.defaultFontFamily`
  - latest job overrides `user_settings.defaultFontFamily`

### Render Worker Tests

Add or update:

- `services/render-worker/tests/test_frame_renderer.py`
  - each supported family loads or falls back deterministically
  - unknown value falls back to `noto_serif_tc`
  - `FrameRenderer` passes family into `_load_font`
  - rendered-frame smoke test for long Traditional Chinese lines per font
- `services/render-worker/tests/test_video_engine.py`
  - `font_family` is passed to `FrameRenderer`
- `services/render-worker/tests/test_pipeline.py`
  - test job data includes `font_family`
  - missing `font_family` uses default

## Files Changed Summary

| File | Change |
|---|---|
| `webapp/src/db/schema.ts` | Add `fontFamily` and `defaultFontFamily` columns |
| `webapp/src/lib/constants.ts` | Add shared font metadata and type |
| `webapp/src/app/api/render-jobs/route.ts` | Validate and accept `fontFamily` |
| `webapp/src/lib/render/job-manager.ts` | Store, map, and return `fontFamily` |
| `webapp/src/app/api/settings/route.ts` | Validate, return, and persist `defaultFontFamily` |
| `webapp/src/app/settings/page.tsx` | Add settings page default |
| `webapp/src/components/settings/SettingsForm.tsx` | Add default font selector and preview |
| `webapp/src/app/songsets/[id]/render/page.tsx` | Apply default precedence and submit `fontFamily` |
| `webapp/src/components/render/RenderForm.tsx` | Add font selector and preview |
| `services/render-worker/fonts/*` | Add vendored font files and manifest |
| `services/render-worker/Dockerfile` | Copy vendored fonts and install fallback package |
| `services/render-worker/Dockerfile.dev` | Same Dockerfile changes |
| `services/render-worker/src/sow_render_worker/frame_renderer.py` | Add font family loading and renderer integration |
| `services/render-worker/src/sow_render_worker/video_engine.py` | Pass font family to renderer |
| `services/render-worker/src/sow_render_worker/pipeline.py` | Pass job font family to video engine |
| `services/render-worker/src/sow_render_worker/db.py` | Read and validate job font family |
| Test files | Cover API, UI, defaults precedence, worker fallback, and visual smoke tests |

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Settings default unexpectedly overwrites songset choices | Use explicit initialization precedence: latest job -> user settings -> app defaults |
| Browser preview does not match render output | Use explicit loaded font variables/classes and validate preview changes per option |
| Font asset provenance is unclear | Commit a font manifest with source, license, checksum, and date |
| DB/webapp deploy order breaks inserts | Apply migration before webapp deploy |
| Worker receives unknown values from old/manual data | Validate in worker and fall back to `noto_serif_tc` |
| Font metrics cause lyric clipping or excessive shrinkage | Add rendered-frame smoke tests with long Traditional Chinese lines |
| Docker fallback package paths differ by base image | Keep multiple known CJK fallback paths and test in the worker image |
