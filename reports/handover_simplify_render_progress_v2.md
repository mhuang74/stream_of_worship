# Handover: Simplify Render Progress Notification v2

**Spec**: `specs/simplify-render-progress-notification-v2.md`
**Date**: 2026-05-29
**Status**: Phases 1-4 code changes complete; Phase 5 (cleanup/verification) partially done; needs lint/typecheck/test run and push.

---

## What Was Done

### Phase 1: Songset Size Limit — DONE

| Change | File |
|--------|------|
| New constants file | `webapp/src/lib/constants.ts` — `SONGSET_MAX_SONGS=5`, `SONGSET_MAX_DURATION_SECONDS=1500` |
| API validation | `webapp/src/app/api/render-jobs/route.ts` — Added `db.query.songsetItems.findMany` with recording relation to check item count and total duration before `createRenderJob`. Returns 400 if exceeded. |
| DB layer check | `webapp/src/lib/db/songsets.ts` — Added `SONGSET_MAX_SONGS` import and item count check in `addSongsetItem()` before insert. Throws error if at limit. |
| UI: SongsetEditor | `webapp/src/components/songset/SongsetEditor.tsx` — Removed `renderProgress` prop. Hides FAB and shows "Maximum 5 songs reached" text when `items.length >= SONGSET_MAX_SONGS`. Shows "Over 25 min" badge when total duration exceeds limit. |
| UI: BrowseSheet | `webapp/src/components/songset/BrowseSheet.tsx` — Added `itemCount` prop. When `itemCount >= SONGSET_MAX_SONGS`, all SongCard `isAdded` props are forced true (disabling add buttons). Footer shows "Songset full" message. |
| Page passes itemCount | `webapp/src/app/songsets/[id]/page.tsx` — Now passes `itemCount={items.length}` to `BrowseSheet`. |
| Worker guard | `services/render-worker/src/sow_render_worker/pipeline.py` — Added `MAX_SONGSET_ITEMS=5`, `MAX_SONGSET_DURATION_SECONDS=1500` constants and validation guard after `fetch_songset_items()`. |

### Phase 2: Simplify Render Progress Screen — DONE

| Change | File |
|--------|------|
| New component | `webapp/src/components/render/RenderSubmitted.tsx` — Static card with estimated time, "You can leave this page" message, and cancel button. No SSE/polling. |
| Updated render page | `webapp/src/app/songsets/[id]/render/page.tsx` — Replaced `RenderProgress` with `RenderSubmitted`. Screen state changed from `"progress"` to `"submitted"`. Computes `estimatedMinutes` from `job.estimatedTotalSeconds`. Cancel now calls `DELETE /api/render-jobs/[id]` API. Removed `handleError` callback. On page load, if job is already running/queued, shows submitted state; if completed, shows complete state. |
| Deleted SSE endpoint | `webapp/src/app/api/render-jobs/[id]/events/route.ts` — DELETED |
| Removed from vercel.json | `webapp/vercel.json` — Removed `src/app/api/render-jobs/[id]/events/route.ts` function config |

### Phase 3: Status Badge — DONE

| Change | File |
|--------|------|
| New component | `webapp/src/components/songset/RenderStatusBadge.tsx` — Text badge with icon per state (unrendered/rendering/fresh/stale/failed). Exports `RenderState` type. |
| Deleted old component | `webapp/src/components/songset/RenderStateButton.tsx` — DELETED |
| Updated SongsetRow | `webapp/src/components/songset/SongsetRow.tsx` — Replaced `RenderStateButton` with `RenderStatusBadge`. Removed `renderProgress` prop. Removed action buttons section (Render/Play/Retry now only in dropdown). Removed `handlePlayAnyway`. |
| Updated SongsetEditor | `webapp/src/components/songset/SongsetEditor.tsx` — Replaced `RenderStateButton` with `RenderStatusBadge`. Removed `renderProgress` prop. |
| Updated SongsetList | `webapp/src/components/songset/SongsetList.tsx` — Removed `renderProgress` from `Songset` interface. Changed import from `RenderStateButton` to `RenderStatusBadge`. |
| Updated page imports | `webapp/src/app/songsets/page.tsx` and `webapp/src/app/songsets/[id]/page.tsx` — Changed `RenderState` import from `RenderStateButton` to `RenderStatusBadge`. |
| Removed from job-manager | `webapp/src/lib/render/job-manager.ts` — Removed `percentComplete` and `estimatedSecondsLeft` from `RenderJob` interface, `mapRowToRenderJob()`, `createRenderJob()` insert values, and `completeRenderJob()` set values. |

### Phase 4: Update Default Render Ratios — DONE

| Change | File |
|--------|------|
| Updated ratios | `services/render-worker/src/sow_render_worker/pipeline.py` — `720p_video`: 0.8→0.5, `1080p_video`: 0.65→0.5. Audio ratios unchanged. |

### Phase 5: Cleanup — PARTIALLY DONE

**Completed:**
- Deleted `webapp/src/components/render/RenderProgress.tsx`
- Deleted `webapp/src/components/songset/RenderStateButton.tsx`
- Deleted `webapp/src/test/components/render/RenderProgress.test.tsx`
- Deleted `webapp/src/test/components/songset/RenderStateButton.test.tsx`
- Deleted `webapp/src/test/api/render-jobs/events.test.ts`
- New test: `webapp/src/test/components/songset/RenderStatusBadge.test.tsx`
- New test: `webapp/src/test/components/render/RenderSubmitted.test.tsx`
- Updated `SongsetRow.test.tsx` — Changed import, replaced "render state button" tests with "render status badge" tests, replaced "Play anyway" tests with "stale state badge" tests
- Updated `SongsetEditor.test.tsx` — Changed import, removed `renderProgress` from mock data, updated callback tests to use dropdown menu instead of button, updated "has render state button" to "has render status badge"
- Updated `SongsetList.test.tsx` — Changed import
- Updated `job-manager.test.ts` — Removed `percentComplete`/`estimatedSecondsLeft` from mock data and assertions, removed "updates percentComplete" and "updates estimatedSecondsLeft" tests, updated completeRenderJob test
- Updated `route.test.ts` (render-jobs API) — Removed `percentComplete`/`estimatedSecondsLeft` from mock jobs, added `db` mock for `songsetItems.findMany`
- Updated `[id].test.ts` (render-jobs API) — Removed `percentComplete`/`estimatedSecondsLeft` from mock data and assertions
- Updated `deployment.test.ts` — Removed SSE events route assertions

**NOT completed:**
- `pnpm lint` / `pnpm build` / typecheck have NOT been run (node_modules not installed)
- Tests have NOT been run
- `git add` / `git commit` / `git push` have NOT been done

---

## Remaining Work

### 1. Install dependencies and run verification

```bash
cd webapp && pnpm install --frozen-lockfile
pnpm lint
pnpm build   # typecheck is included in build
pnpm test
```

### 2. Fix any lint/typecheck/test failures

Likely issues to watch for:
- The `db.query.songsetItems.findMany` in `route.ts` uses `with: { recording: { columns: { durationSeconds: true } } }` — verify this works with the Drizzle schema relations
- The `[id].test.ts` uses `{ params: { id: "job-1" } }` but the actual route uses `{ params: Promise<{ id: string }> }` — the test may need `await params` handling
- The `route.test.ts` mock for `db` may need adjustment if the actual query shape differs
- `SongsetEditor.tsx` now imports `SONGSET_MAX_DURATION_SECONDS` but the `isDurationOverLimit` variable uses it — verify no unused import warnings

### 3. Git commit and push

```bash
git add -A
git commit -m "feat: simplify render progress notification v2

- Add songset size limit (5 songs / 25 min) with API, DB, UI, and worker enforcement
- Replace RenderProgress (SSE/polling) with static RenderSubmitted card
- Replace RenderStateButton with RenderStatusBadge (no percentage)
- Remove percentComplete and estimatedSecondsLeft from RenderJob interface
- Update default render ratios (720p/1080p video: 0.5)
- Delete SSE events endpoint and related tests
- Add constants, RenderSubmitted, RenderStatusBadge components and tests"

git pull --rebase
git push
```

### 4. Update `reports/current_impl_status.md` (per AGENTS.md instructions)

---

## Key Files Changed

| File | Action |
|------|--------|
| `webapp/src/lib/constants.ts` | NEW |
| `webapp/src/components/render/RenderSubmitted.tsx` | NEW |
| `webapp/src/components/songset/RenderStatusBadge.tsx` | NEW |
| `webapp/src/test/components/songset/RenderStatusBadge.test.tsx` | NEW |
| `webapp/src/test/components/render/RenderSubmitted.test.tsx` | NEW |
| `webapp/src/app/api/render-jobs/route.ts` | MODIFIED (added size validation) |
| `webapp/src/lib/db/songsets.ts` | MODIFIED (added item count check) |
| `webapp/src/components/songset/SongsetEditor.tsx` | MODIFIED (badge, max songs UI) |
| `webapp/src/components/songset/BrowseSheet.tsx` | MODIFIED (disable add when full) |
| `webapp/src/components/songset/SongsetRow.tsx` | MODIFIED (badge replaces button) |
| `webapp/src/components/songset/SongsetList.tsx` | MODIFIED (removed renderProgress) |
| `webapp/src/app/songsets/page.tsx` | MODIFIED (import change) |
| `webapp/src/app/songsets/[id]/page.tsx` | MODIFIED (passes itemCount) |
| `webapp/src/app/songsets/[id]/render/page.tsx` | MODIFIED (RenderSubmitted) |
| `webapp/src/lib/render/job-manager.ts` | MODIFIED (removed deprecated fields) |
| `webapp/vercel.json` | MODIFIED (removed SSE config) |
| `services/render-worker/src/sow_render_worker/pipeline.py` | MODIFIED (size guard + ratios) |
| `webapp/src/components/render/RenderProgress.tsx` | DELETED |
| `webapp/src/components/songset/RenderStateButton.tsx` | DELETED |
| `webapp/src/app/api/render-jobs/[id]/events/route.ts` | DELETED |
| `webapp/src/test/components/render/RenderProgress.test.tsx` | DELETED |
| `webapp/src/test/components/songset/RenderStateButton.test.tsx` | DELETED |
| `webapp/src/test/api/render-jobs/events.test.ts` | DELETED |
| `webapp/src/test/components/songset/SongsetRow.test.tsx` | MODIFIED |
| `webapp/src/test/components/songset/SongsetEditor.test.tsx` | MODIFIED |
| `webapp/src/test/components/songset/SongsetList.test.tsx` | MODIFIED |
| `webapp/src/test/lib/render/job-manager.test.ts` | MODIFIED |
| `webapp/src/test/api/render-jobs/route.test.ts` | MODIFIED |
| `webapp/src/test/api/render-jobs/[id].test.ts` | MODIFIED |
| `webapp/src/test/deployment/deployment.test.ts` | MODIFIED |

## Notes

- The DB schema columns `percentComplete` and `estimatedSecondsLeft` are **intentionally kept** in `schema.ts` (marked `@deprecated`) for backward compatibility with existing DB data
- The `RenderState` type is now exported from `RenderStatusBadge.tsx` instead of `RenderStateButton.tsx` — all consumers updated
- The `SongsetEditor` no longer has a clickable render/play/retry button in the app bar — those actions are only in the overflow dropdown menu
- The `SongsetRow` no longer has inline action buttons — render/play/retry are only in the dropdown menu
