# Render Failed Tooltip for All Render Status Badges

## Summary

Add hover/focus tooltip support anywhere `RenderStatusBadge` is rendered today:

- `/songsets` list cards via `SongsetRow`
- `/songsets/[id]` editor header via `SongsetEditor`

Use the latest render job's `errorMessage` when the songset is in `failed` state. If no message exists, show `Failed on <localized date/time>` using the failed job's `updatedAt`.

## Public Interfaces

- Extend songset summary/detail data with:
  - `latestRenderErrorMessage: string | null`
  - `latestRenderFailedAt: string | null`
- Extend client/component songset types with:
  - `renderErrorMessage?: string | null`
  - `renderFailedAt?: Date | null`
- Extend `RenderStatusBadge` props with:
  - `errorMessage?: string | null`
  - `failedAt?: Date | null`

## Implementation Changes

- In `webapp/src/lib/db/songsets.ts`, update both data paths used by badge screens:
  - `listSongsetSummaries()` for `/songsets`
  - `getSongsetEditorData()` for `/songsets/[id]`
- Select `renderJobs.errorMessage` and `renderJobs.updatedAt` from the existing latest-render-job join.
- Return the fields only when computed `renderState === "failed"`; trim blank error messages to `null`.
- In `webapp/src/app/songsets/SongsetsClient.tsx`, map API fields into `renderErrorMessage` and `renderFailedAt`.
- In `webapp/src/app/songsets/[id]/page.tsx` and `webapp/src/app/songsets/[id]/SongsetEditorClient.tsx`, pass the same fields through initial editor data and client state.
- In `webapp/src/components/songset/SongsetList.tsx`, `webapp/src/components/songset/SongsetRow.tsx`, and `webapp/src/components/songset/SongsetEditor.tsx`, pass the fields into `RenderStatusBadge`.
- In `webapp/src/components/songset/RenderStatusBadge.tsx`, wrap failed badges with existing tooltip primitives only when tooltip content exists:
  - Stored error: show trimmed `errorMessage`.
  - Fallback: `Failed on ${Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(failedAt)}`.
  - Add `tabIndex={0}` only for badges with tooltip content so keyboard focus reveals the tooltip.
  - Do not add tooltip behavior for non-failed states.
- Do not change screens that only use `renderState` text/warnings but do not render `RenderStatusBadge`.

## Test Plan

- Update DB tests for both `listSongsetSummaries()` and `getSongsetEditorData()` to verify failed-job error metadata is returned.
- Update `/api/songsets` and `/api/songsets/[id]` route tests/fixtures where they assert returned songset shapes.
- Update `RenderStatusBadge` tests for:
  - Failed badge with stored error tooltip.
  - Failed badge fallback timestamp tooltip.
  - Failed badge without message/timestamp has no tooltip.
  - Non-failed badges have no tooltip.
- Update `SongsetRow` and `SongsetEditor` tests to verify they pass/render failed tooltip content.
- Run:
  - `cd webapp && pnpm test src/test/api/songsets/db.test.ts src/test/api/songsets/route.test.ts src/test/api/songsets/[id].test.ts src/test/components/songset/RenderStatusBadge.test.tsx src/test/components/songset/SongsetRow.test.tsx src/test/components/songset/SongsetEditor.test.tsx`
  - `cd webapp && pnpm lint`
- After future implementation changes, run `graphify update .`.

## Assumptions

- The repo scan found two actual `RenderStatusBadge` render sites: `SongsetRow` and `SongsetEditor`.
- "Latest failed job" means `songsets.latestRenderJobId` when the computed render state is `failed`.
- `render_jobs.updated_at` is the failure timestamp because there is no dedicated `failed_at`.
- No database migration is needed.
