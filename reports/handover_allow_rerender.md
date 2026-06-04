# Allow Re-render from Render Page — Handover

**Date:** 2026-06-04
**Spec:** `specs/allow-rerender-from-render-page-v3.md`
**Branch:** `allow_rerender`

## Status: Implementation Complete, Not Yet Pushed

All code changes are done and verified locally. The branch needs `git push`.

## What Was Implemented

Per the spec, the render page now **always shows the parameter form** when a previous render exists, with an info banner and confirmation dialog. A `lastCompletedRenderJobId` field ensures download links survive failed re-renders.

### Changes by File

#### Schema & Migration
- `webapp/src/db/schema.ts` — Added `lastCompletedRenderJobId` text column to `songsets` table
- `webapp/drizzle/0011_modern_exiles.sql` — Migration with ALTER + backfill UPDATE

#### Backend (render-worker)
- `services/render-worker/src/sow_render_worker/db.py` — `complete_render_job()` now wraps both render_jobs and songsets updates in a transaction; sets `last_completed_render_job_id` on completion

#### Backend (webapp)
- `webapp/src/lib/render/job-manager.ts` — `completeRenderJob()` now updates `songsets.lastCompletedRenderJobId` after job row update

#### TypeScript Interfaces
- `webapp/src/lib/db/songsets.ts` — Added `lastCompletedRenderJobId` to `SongsetListItem` and all mapping functions (`listSongsets`, `getSongset`, `createSongset`, `updateSongset`)

#### Download Handlers & UI Disabled Conditions
- `webapp/src/app/songsets/[id]/page.tsx` — Download handlers use `lastCompletedRenderJobId`; added field to API interfaces
- `webapp/src/app/songsets/page.tsx` — Same changes for list page download handlers
- `webapp/src/components/songset/SongsetEditor.tsx` — Kebab menu disabled condition uses `lastCompletedRenderJobId`
- `webapp/src/components/songset/SongsetRow.tsx` — Row download disabled condition uses `lastCompletedRenderJobId`
- `webapp/src/components/songset/SongsetList.tsx` — Added `lastCompletedRenderJobId` to `Songset` interface

#### UI Components
- `webapp/src/components/ui/alert-dialog.tsx` — New shadcn AlertDialog component (installed via `pnpm exec shadcn add alert-dialog`)
- `webapp/src/components/ui/button.tsx` — Updated by shadcn (minor)

#### Shared Utilities
- `webapp/src/lib/format.ts` — New file; extracted `formatDuration` from `RenderComplete.tsx`
- `webapp/src/components/render/RenderComplete.tsx` — Now imports `formatDuration` from `@/lib/format`; local function removed

#### RenderForm
- `webapp/src/components/render/RenderForm.tsx` — Added `previousRenderJob` prop; blue info banner ("Previously rendered at {date}"); AlertDialog confirmation with parameter summary before re-render

#### RenderSubmitted
- `webapp/src/components/render/RenderSubmitted.tsx` — Added `submittedAt` prop; displays "Submitted at {date}" timestamp

#### Render Page
- `webapp/src/app/songsets/[id]/render/page.tsx` — Removed "complete" screen, `RenderComplete` import, `handleDone`/`handleShare`; simplified `RenderScreenState` to `"form" | "submitted"`; added `lastCompletedRenderJobId` to songset data; fetches previous completed job; passes `previousRenderJob` and `submittedAt` props

#### Tests
- `webapp/src/test/lib/render/job-manager.test.ts` — Added `lastCompletedRenderJobId: null` to mock songset; fixed `completeRenderJob` test to handle second `db.update` call
- `webapp/src/test/components/songset/SongsetEditor.test.tsx` — Added `lastCompletedRenderJobId`
- `webapp/src/test/components/play/PrePlayCard.test.tsx` — Added `lastCompletedRenderJobId`
- `webapp/src/test/app/projection-page.test.tsx` — Added `lastCompletedRenderJobId`
- `webapp/src/test/app/render-page.test.tsx` — Added `lastCompletedRenderJobId`
- `webapp/src/test/app/controller-page.test.tsx` — Added `lastCompletedRenderJobId` to all mock songset objects
- `webapp/src/test/api/songsets/db.test.ts` — Added `lastCompletedRenderJobId` to all mock songset objects
- `webapp/src/test/api/songsets/route.test.ts` — Added `lastCompletedRenderJobId` to mock songset objects
- `webapp/src/test/api/songsets/[id].test.ts` — Added `lastCompletedRenderJobId` to mock songset objects
- `services/render-worker/tests/test_db.py` — Fixed `complete_render_job` tests to use `call_args_list[0]` for first SQL statement; added tests for `last_completed_render_job_id` update and transaction usage

## Verification Results

- `pnpm lint` — 0 errors (1 pre-existing warning about custom fonts)
- `pnpm test` — 82 test files, 1329 tests passed
- `pnpm build` — TypeScript compilation succeeds; build fails at "Collecting page data" due to missing `SOW_DATABASE_URL` env var (pre-existing, unrelated)
- Render-worker DB tests — 89 passed

## Deferred Items (per spec)

These are explicitly deferred to a follow-up PR:
- Use `lastCompletedRenderJobId` in play pages (`/songsets/[id]/play`, controller, projection)
- Simplify `computeRenderState()` to use `lastCompletedRenderJobId` directly for "fresh"/"stale" determination

## Remaining Work Before Merge

1. **Push the branch:** `git pull --rebase && git push`
2. **Manual testing:** Navigate to `/songsets/[id]/render` after a completed render; verify form shows with blue info banner; verify confirmation dialog appears on submit; verify download links still work after a failed re-render
3. **Run migration on staging/production:** `npx drizzle-kit push` or `npx drizzle-kit migrate`
4. **Create/update PR** with the implementation summary

## Key Design Decisions

- `complete_render_job()` in render-worker now uses transactions (matching `fail_render_job()` pattern) to ensure atomicity of render_jobs + songsets updates
- `SongsetRow.tsx` keeps `latestRenderJobId` in its interface (for spread from `SongsetList`) but no longer destructures it
- `RenderComplete` component is preserved but no longer rendered from the render page (for future email notification landing page)
- All date-time formatting uses `Intl.DateTimeFormat` (no `date-fns` dependency)
