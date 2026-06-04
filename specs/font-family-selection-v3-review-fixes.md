# Font Family Selection v3 Review Fixes

## Summary

This plan addresses review findings from the current implementation of
`specs/font-family-selection-v3.md`.

The current implementation added much of the API/UI/worker plumbing, but it is
not ready to ship because:

- Render-worker font files are missing, so selected fonts fall back at runtime.
- Render-page initialization does not implement the v3 precedence rules.
- Invalid stored font values are not normalized consistently.
- Drizzle migration metadata is incomplete.
- Tests do not cover the default-precedence behavior that v3 requires.

This is a remediation plan only. Do not implement code while creating or
reviewing this document.

## Goals

- Make rendered video output use the actual selected curated font whenever
  possible.
- Make render-page defaults follow the v3 product rules exactly:
  latest job configuration -> user settings -> app defaults.
- Make legacy, missing, or invalid stored font values deterministic and safe.
- Make database migration state complete and reproducible.
- Add focused tests that would fail for the reviewed implementation.

## Non-Goals

- Do not change the set of four font choices unless a licensing or coverage
  check fails.
- Do not redesign the render form or settings page.
- Do not add new render parameters beyond `fontFamily`.
- Do not change audio rendering behavior.

## Finding 1: Missing Render-Worker Font Assets

### Problem

`services/render-worker/src/sow_render_worker/frame_renderer.py` maps selected
font families to vendored font paths:

```text
/usr/share/fonts/truetype/vendor/LXGWWenKaiTC-Regular.ttf
/usr/share/fonts/truetype/vendor/ChocolateClassicalSans-Regular.ttf
/usr/share/fonts/truetype/vendor/ChironGoRoundTC-Regular.ttf
/usr/share/fonts/truetype/vendor/NotoSerifTC-Regular.ttf
```

But `services/render-worker/fonts/` currently contains only `MANIFEST.md`.
The Dockerfiles copy this directory, so worker containers will never load the
curated fonts and will silently use fallback fonts.

The manifest also contains placeholder checksum and date values.

### Implementation Steps

1. Download canonical regular-weight font artifacts from upstream release
   sources.
2. Place exactly these files under `services/render-worker/fonts/`:

   ```text
   LXGWWenKaiTC-Regular.ttf
   ChocolateClassicalSans-Regular.ttf
   ChironGoRoundTC-Regular.ttf
   NotoSerifTC-Regular.ttf
   ```

3. Prefer upstream release assets over Google Fonts generated CSS URLs.
4. For each file, verify:

   - The file exists in the repo and is not a placeholder.
   - `file` identifies it as a TrueType/OpenType font.
   - `shasum -a 256` has a stable value recorded in the manifest.
   - The font can be loaded by Pillow with `ImageFont.truetype`.
   - Traditional Chinese sample text renders without missing-glyph boxes.

5. Update `services/render-worker/fonts/MANIFEST.md` with:

   - Font display name.
   - Exact source URL or release tag.
   - License name.
   - License URL.
   - SHA-256 checksum.
   - Date downloaded.

6. If any chosen font cannot be legally vendored or lacks Traditional Chinese
   lyric coverage, stop and update `specs/font-family-selection-v3.md` before
   changing implementation.

### Acceptance Criteria

- `find services/render-worker/fonts -maxdepth 1 -type f` lists the manifest and
  all four font files.
- Manifest checksums are filled in and match local files.
- A render-worker test proves each vendored path can be loaded when present.
- Worker logs show the selected vendor path for each supported family in a local
  smoke run.

## Finding 2: Render Page Default Precedence Is Incomplete

### Problem

The v3 spec requires render-page initialization to use this precedence:

1. Latest render job configuration for the songset, if present.
2. User settings.
3. App defaults.

The reviewed implementation only fetches the songset and latest job. It does
not fetch `/api/settings`, so a songset with no remembered job cannot initialize
from `user_settings.default_font_family`.

It also only sets `initialData` for completed jobs. v3 requires latest
completed, failed, queued, or running jobs with stored render parameters to
count as remembered songset configuration.

### Implementation Steps

1. Add an app-default render configuration object in
   `webapp/src/app/songsets/[id]/render/page.tsx` or import an existing one if
   the codebase already has a shared default.

   It should include all render fields that have user defaults:

   - `template`
   - `resolution`
   - `fontSizePreset`
   - `fontFamily`
   - offline option, if this page already treats it as a render default

2. During render-page load, fetch songset data and user settings before
   finalizing `initialData`.

   A practical sequence:

   - Fetch `/api/songsets/${songsetId}`.
   - Fetch `/api/settings`.
   - If `latestRenderJobId` exists, fetch `/api/render-jobs/${latestRenderJobId}`.
   - Build initial form data from the best available source.

3. Build a helper local to the page or a small shared helper:

   ```ts
   function buildInitialRenderData({
     latestJob,
     userSettings,
   }): Partial<RenderFormData>
   ```

4. If a latest job exists and has a relevant status, seed all remembered render
   fields from the job.

   Relevant statuses:

   - `completed`
   - `failed`
   - `queued`
   - `running`

5. Preserve existing screen-state behavior:

   - Running or queued jobs should still show the submitted/progress screen.
   - Completed fresh jobs should still show the completion screen.
   - Failed or stale completed jobs should allow the form to open with the
     remembered job settings.

6. If no latest job configuration is usable, seed from fetched settings merged
   onto app defaults.

7. If `/api/settings` fails with 401, keep the existing login redirect behavior.
   For other failures, either surface the error or fall back to app defaults
   only if the existing UX pattern supports that.

### Acceptance Criteria

- A songset with no latest job opens with `defaultFontFamily` from user settings.
- A songset with a latest completed job opens/re-renders with that job's
  `fontFamily`, even if user settings changed later.
- A songset with a latest failed job opens the form with that failed job's
  stored `fontFamily`.
- A songset with a latest queued/running job keeps the submitted screen behavior
  and does not discard the job's stored render configuration.
- Changing global settings does not alter remembered render settings for a
  songset that already has a latest job.

## Finding 3: Invalid Stored Font Values Are Not Normalized

### Problem

The webapp and worker currently handle missing/null font values, but invalid
stored values can still pass through.

This matters for:

- Legacy rows.
- Manual database edits.
- Partial rollouts.
- Defensive worker behavior when a job payload contains an unexpected value.

The v3 spec requires invalid or missing values to fall back to `noto_serif_tc`.

### Webapp Implementation Steps

1. Add a shared helper in `webapp/src/lib/constants.ts` or a nearby render
   utility:

   ```ts
   export function normalizeFontFamily(value: unknown): FontFamilyValue {
     return VALID_FONT_FAMILIES.includes(value as FontFamilyValue)
       ? (value as FontFamilyValue)
       : "noto_serif_tc"
   }
   ```

2. Use the helper in `webapp/src/lib/render/job-manager.ts` when mapping
   database rows to `RenderJob`.

3. Use the helper in `webapp/src/app/songsets/[id]/render/page.tsx` when
   converting fetched latest-job data into `RenderFormData`.

4. Keep API create/update validation strict: user-submitted invalid values
   should still return `400`.

### Worker Implementation Steps

1. Define supported values in `services/render-worker/src/sow_render_worker/db.py`
   or import from a worker-local constants module:

   ```python
   VALID_FONT_FAMILIES = {
       "lxgw_wenkai_tc",
       "chocolate_classical_sans",
       "chiron_goround_tc",
       "noto_serif_tc",
   }
   ```

2. Add a helper:

   ```python
   def _normalize_font_family(value: object) -> str:
       if isinstance(value, str) and value in VALID_FONT_FAMILIES:
           return value
       logger.warning("Unknown font_family=%r; falling back to noto_serif_tc", value)
       return "noto_serif_tc"
   ```

3. Use it in `_row_to_render_job`.

4. Optionally use the same helper before passing `job.font_family` to
   `VideoEngine`, if pipeline construction can receive jobs from tests or other
   sources that bypass the DB mapper.

### Acceptance Criteria

- Webapp `RenderJob.fontFamily` is always one of the valid constants.
- Worker `RenderJob.font_family` is always one of the valid constants after DB
  deserialization.
- Missing/null values fall back without noisy logs.
- Invalid non-null values log one warning and fall back to `noto_serif_tc`.

## Finding 4: Drizzle Migration Metadata Is Incomplete

### Problem

The implementation has a migration SQL file for the new columns, and
`webapp/drizzle/meta/_journal.json` references `0010_add_font_family_columns`.
However, there is no matching `webapp/drizzle/meta/0010_snapshot.json`.

This can make future Drizzle generation inconsistent and makes the migration
state incomplete.

### Implementation Steps

1. From `webapp/`, run the repo-standard Drizzle generation flow:

   ```bash
   npx drizzle-kit generate
   ```

2. Confirm the generated migration is exactly the intended schema change:

   ```sql
   ALTER TABLE "render_jobs"
     ADD COLUMN "font_family" text DEFAULT 'noto_serif_tc' NOT NULL;

   ALTER TABLE "user_settings"
     ADD COLUMN "default_font_family" text DEFAULT 'noto_serif_tc' NOT NULL;
   ```

3. Confirm `webapp/drizzle/meta/0010_snapshot.json` exists.

4. Confirm `webapp/drizzle/meta/_journal.json` has a matching entry for the
   generated migration.

5. If generation creates a differently named migration, prefer the generated
   name and keep journal/snapshot/SQL in sync. Do not hand-edit only one of the
   three migration artifacts.

### Acceptance Criteria

- `webapp/drizzle/0010_*.sql` exists and contains only the two expected column
  additions.
- `webapp/drizzle/meta/0010_snapshot.json` exists.
- `webapp/drizzle/meta/_journal.json` references the same migration tag.
- `pnpm --filter sow-webapp build` does not fail due to schema/migration typing.

## Finding 5: Missing Default-Precedence Tests

### Problem

Existing tests cover pieces of the API and form, but not the render-page
precedence behavior that v3 introduced. The reviewed implementation could pass
its current tests while still ignoring `user_settings.defaultFontFamily`.

### Webapp Tests To Add

Add or extend `webapp/src/test/app/render-page.test.tsx`.

Required cases:

1. **No latest job uses user settings**

   - Mock `/api/songsets/${id}` with `latestRenderJobId: null`.
   - Mock `/api/settings` with `defaultFontFamily: "lxgw_wenkai_tc"`.
   - Assert the render form receives or displays LXGW WenKai TC as selected.

2. **Latest completed job overrides user settings**

   - Mock `/api/songsets/${id}` with `latestRenderJobId: "job-1"`.
   - Mock `/api/settings` with `defaultFontFamily: "lxgw_wenkai_tc"`.
   - Mock `/api/render-jobs/job-1` with `fontFamily: "chiron_goround_tc"`.
   - Assert the form initial value is Chiron GoRound TC.

3. **Latest failed job restores remembered settings**

   - Mock latest job with `status: "failed"` and
     `fontFamily: "chocolate_classical_sans"`.
   - Assert the form appears and uses Chocolate Classical Sans.

4. **Invalid latest job font falls back**

   - Mock latest job with `fontFamily: "bad_value"`.
   - Assert the form uses `noto_serif_tc`.

5. **Settings change does not overwrite remembered job**

   - Same setup as case 2, but use a settings font different from the job font.
   - Assert the job font wins.

### Webapp Unit Tests To Add

Add or extend tests around the normalization helper:

- Valid values return themselves.
- `null`, `undefined`, empty string, and unknown strings return
  `noto_serif_tc`.

Add or extend job-manager tests:

- `mapRowToRenderJob` normalizes unknown `row.fontFamily`.
- Missing `row.fontFamily` returns `noto_serif_tc`.

### Worker Tests To Add

Add or extend `services/render-worker/tests/test_db.py` if present, or add a new
focused test file for DB mapping:

- Missing `font_family` returns `noto_serif_tc`.
- Known `font_family` returns itself.
- Unknown `font_family` logs a warning and returns `noto_serif_tc`.

Strengthen frame-renderer tests:

- When vendor font files are present, `_load_font(size, family)` loads the
  vendor path before fallback.
- If a vendor file is absent, fallback behavior is deterministic.

### Acceptance Criteria

- The render-page tests fail against the reviewed implementation and pass after
  remediation.
- Worker DB normalization tests fail against the reviewed implementation and
  pass after remediation.
- Existing API validation tests still pass.

## Suggested Implementation Order

1. Complete Drizzle migration metadata.
2. Add font normalization helpers in webapp and worker.
3. Fix render-page initialization precedence.
4. Add render-page and normalization tests.
5. Vendor font files and finalize `MANIFEST.md`.
6. Run focused tests.
7. Run broader webapp and render-worker verification.
8. Run `graphify update .` after code changes.

This order keeps database and data-shape fixes ahead of UI behavior, and leaves
large binary font assets until the code path is deterministic.

## Verification Commands

Run from the project root unless noted.

### Webapp

```bash
pnpm --filter sow-webapp test
pnpm --filter sow-webapp lint
pnpm --filter sow-webapp build
```

If tests are slow, run focused cases first:

```bash
cd webapp
pnpm test src/test/app/render-page.test.tsx
pnpm test src/test/api/render-jobs/route.test.ts
pnpm test src/test/api/settings/route.test.ts
pnpm test src/test/components/render/RenderForm.test.tsx
pnpm test src/test/components/settings/SettingsForm.test.tsx
```

### Render Worker

```bash
cd services/render-worker
PYTHONPATH=src pytest tests/test_frame_renderer.py tests/test_video_engine.py tests/test_pipeline.py -v
```

Add the DB normalization test file to the command once it exists:

```bash
cd services/render-worker
PYTHONPATH=src pytest tests/test_db.py -v
```

### Font Asset Smoke Check

```bash
find services/render-worker/fonts -maxdepth 1 -type f -print
shasum -a 256 services/render-worker/fonts/*.ttf
```

Run a small Pillow load check inside the render-worker environment or container
after the files are vendored.

### Graphify

After code changes, update the project graph:

```bash
graphify update .
```

## Rollout Notes

Use the original v3 rollout order:

1. Apply DB migration first.
2. Deploy worker code that can read and normalize missing or unknown
   `font_family`.
3. Deploy webapp API/UI code that writes and displays `fontFamily`.
4. Deploy render-worker image containing vendored fonts.

Do not deploy the webapp schema changes before the database migration is
applied, because the webapp and worker will query columns that may not exist.

## Completion Checklist

- [ ] Migration SQL, journal, and snapshot are complete and committed together.
- [ ] Render page fetches settings and applies latest job -> settings -> app
  defaults precedence.
- [ ] Latest failed jobs restore remembered render settings.
- [ ] Latest queued/running jobs retain submitted-screen behavior.
- [ ] Webapp normalizes stored invalid font values.
- [ ] Worker normalizes stored invalid font values and logs warnings.
- [ ] Four vendored font files exist.
- [ ] Font manifest has exact sources, licenses, checksums, and download dates.
- [ ] Render-page precedence tests are added.
- [ ] Worker DB normalization tests are added.
- [ ] Focused webapp and render-worker tests pass.
- [ ] `graphify update .` has been run after implementation changes.
