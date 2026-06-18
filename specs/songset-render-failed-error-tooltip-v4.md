# Songset Render Failure Details (v4)

## Summary

When a SongSet render fails, show the failure reason without exposing raw worker output. The list page stays compact with a failed-badge tooltip, while the editor page shows a visible inline alert that asks the user to render again.

This plan supersedes `songset-render-failed-error-tooltip-v3.md` but does not edit or remove it.

## Review Findings Addressed

- **UX experience**: v3 relies on hover-only details on the list and does not account for the badge currently living inside a row link. v4 keeps the compact badge tooltip, adds keyboard focus support, and requires the row structure to avoid nested interactive/link behavior.
- **Data correctness**: failure details must describe the current latest failed job only. Prior failed jobs must not leak into fresh, stale, rendering, unrendered, duplicate, or create responses.
- **Runtime issues**: timestamps must serialize as ISO strings across server/client boundaries, and tooltips must be tested through hover/focus because tooltip content is portaled.
- **Operational concerns**: worker errors can come from Next.js dispatch, Lambda worker code, FFmpeg, R2, Postgres, or orphan recovery. UI sanitization is the trust boundary.

## Goals

1. Surface the current failed render job's reason in a safe, short form.
2. Keep the songset list dense while still making failure details discoverable by hover and keyboard focus.
3. Show an always-visible failure alert on the editor page.
4. Fall back to `startedAt ?? createdAt` with copy that asks the user to render again when no safe error text exists.
5. Avoid carrying full raw tracebacks through list payloads.

## Non-Goals

- Do not change the render worker, render job schema, or database migrations.
- Do not surface failure details on public/share pages in this change.
- Do not show raw stack traces, absolute paths, URLs, secrets, or infrastructure internals to end users.

## Data Model

`render_jobs` already has the needed columns:

- `errorMessage`
- `startedAt`
- `createdAt`
- `updatedAt`

Add these fields to `SongsetListItem` and therefore `SongsetDetail`:

```typescript
renderErrorMessage: string | null;
failedAt: Date | null;
```

These fields must be populated only when the derived `renderState` is `"failed"` for `songsets.latestRenderJobId`.

## Error Message Helper

Add `webapp/src/lib/render/error-message.ts`.

Behavior:

- `sanitizeRenderErrorMessage(message)`:
  - returns `null` for non-string, empty, whitespace-only, or fully redacted messages
  - strips ANSI escape sequences and control characters
  - selects the first useful non-empty line
  - removes common stack-frame prefixes and obvious traceback framing
  - redacts URLs as `[url]`
  - redacts Unix/macOS/Windows absolute paths as `[path]`
  - redacts obvious secret-like fragments such as `TOKEN=...`, `API_KEY=...`, `PASSWORD=...`, `SECRET=...`, `DATABASE_URL=...`, and `SOW_*_KEY=...`
  - collapses repeated whitespace
  - truncates to 250 characters with an ellipsis
- `formatRenderFailedAt(date)`:
  - formats a `Date` for user-facing fallback text
- `getRenderFailureText(errorMessage, failedAt)`:
  - returns the sanitized error when available
  - otherwise returns `Render failed around <formatted date>. Please render again.` when `failedAt` exists
  - otherwise returns `Render failed. Please render again.`

Use this helper from both the DB mapping layer and UI components so list, editor alert, and tooltip text stay consistent.

## DB And API Changes

### `webapp/src/lib/db/songsets.ts`

In `listSongsetSummaries`:

- Select the latest job's bounded error text and timestamps from the existing `leftJoin(renderJobs, eq(renderJobs.id, songsets.latestRenderJobId))`.
- Use a bounded SQL expression for the list payload source, for example:

```typescript
renderErrorMessage: sql<string | null>`left(${renderJobs.errorMessage}, 4000)`,
latestJobStartedAt: renderJobs.startedAt,
latestJobCreatedAt: renderJobs.createdAt,
```

- Add the selected render job fields to `GROUP BY`.
- Compute `renderState` once with `mapRenderStateFromSnapshot`.
- Return failure fields only when `renderState === "failed"`:

```typescript
const renderState = mapRenderStateFromSnapshot(...);
const failedAt = row.latestJobStartedAt ?? row.latestJobCreatedAt;

return {
  ...,
  renderState,
  renderErrorMessage:
    renderState === "failed" ? sanitizeRenderErrorMessage(row.renderErrorMessage) : null,
  failedAt: renderState === "failed" ? failedAt : null,
};
```

In `getSongsetEditorData`:

- Select `renderJobs.errorMessage`, `renderJobs.startedAt`, and `renderJobs.createdAt`.
- Use the same current-latest-job-only logic as the list query.

In `createSongset`, `updateSongset`, and `getSongset`:

- Include `renderErrorMessage: null` and `failedAt: null` unless those functions are updated to query and map the current latest render job.
- `duplicateSongset` can rely on `getSongset(newId, userId)` returning null failure fields because the duplicate has no render job.

### Server Serialization

In `webapp/src/app/songsets/page.tsx` and `webapp/src/app/songsets/[id]/page.tsx`:

- Serialize `failedAt` as ISO or `null`:

```typescript
failedAt: songset.failedAt?.toISOString() ?? null,
```

### Client API Types

In `SongsetsClient.tsx` and `SongsetEditorClient.tsx`:

- Add:

```typescript
renderErrorMessage: string | null;
failedAt: string | null;
```

- Convert `failedAt` to `Date | null` in local component state.
- Ensure local optimistic stale transitions clear failure display fields:

```typescript
renderErrorMessage: null,
failedAt: null,
```

This applies to reorder, remove, add song, and transition update paths that set `renderState: "stale"`.

## UI Changes

### `RenderStatusBadge`

Extend props:

```typescript
interface RenderStatusBadgeProps {
  state: RenderState;
  errorMessage?: string | null;
  failedAt?: Date | null;
  className?: string;
}
```

For `state === "failed"`, wrap the badge with the existing tooltip primitives only when `getRenderFailureText(errorMessage, failedAt)` returns text.

Requirements:

- Tooltip opens on hover and keyboard focus.
- Tooltip content uses constrained width and wrapping, for example `max-w-80 whitespace-normal break-words`.
- The trigger must be focusable when the badge is not already focusable.
- Do not create a tooltip for non-failed states.

### `SongsetRow`

Add optional props:

```typescript
renderErrorMessage?: string | null;
failedAt?: Date | null;
```

Pass them to `RenderStatusBadge`.

Because the current badge is inside a `Link`, restructure the row so the failed badge tooltip trigger is not nested inside the link. Keep the title/metadata clickable and preserve existing row actions.

### `SongsetList`

Add optional fields to the `Songset` interface:

```typescript
renderErrorMessage?: string | null;
failedAt?: Date | null;
```

No additional rendering change is needed if `SongsetRow` receives `{...songset}`.

### `SongsetEditor`

Add to the `songset` prop shape:

```typescript
renderErrorMessage?: string | null;
failedAt?: Date | null;
```

Pass both fields into the app-bar `RenderStatusBadge`.

Show an inline destructive alert below the app bar when `songset.renderState === "failed"`:

- icon: `AlertCircle`
- title: `Render failed`
- description: `getRenderFailureText(songset.renderErrorMessage, songset.failedAt)`
- action: `Render again`, wired to `onRender`

Do not make this alert dismissible. A failed render is current state, not a transient notice.

## Files To Modify

- `webapp/src/lib/render/error-message.ts`
- `webapp/src/lib/db/songsets.ts`
- `webapp/src/app/songsets/page.tsx`
- `webapp/src/app/songsets/SongsetsClient.tsx`
- `webapp/src/app/songsets/[id]/page.tsx`
- `webapp/src/app/songsets/[id]/SongsetEditorClient.tsx`
- `webapp/src/components/songset/RenderStatusBadge.tsx`
- `webapp/src/components/songset/SongsetList.tsx`
- `webapp/src/components/songset/SongsetRow.tsx`
- `webapp/src/components/songset/SongsetEditor.tsx`

## Files Not To Modify

- `webapp/src/db/schema.ts`
- `webapp/src/app/api/songsets/route.ts`
- `webapp/src/app/api/songsets/[id]/route.ts`
- `webapp/src/app/api/songsets/[id]/duplicate/route.ts`
- render worker code
- existing spec files, including `specs/songset-render-failed-error-tooltip-v3.md`

## Tests

Add or update tests for:

- `sanitizeRenderErrorMessage`:
  - strips ANSI escape codes
  - uses the first useful non-empty line
  - redacts URLs
  - redacts absolute paths
  - redacts secret-like key/value fragments
  - returns `null` for empty, whitespace-only, or fully redacted input
  - truncates long messages to 250 characters
- `getRenderFailureText`:
  - prefers sanitized text
  - falls back to `Render failed around <date>. Please render again.`
  - falls back to `Render failed. Please render again.`
- `listSongsetSummaries`:
  - returns sanitized failure fields for a latest failed job
  - returns `failedAt` from `startedAt ?? createdAt`
  - returns null failure fields for non-failed states even when prior failures exist
- `getSongsetEditorData`:
  - same latest-failed-job mapping behavior as the list query
- `RenderStatusBadge`:
  - failed state tooltip opens on hover
  - failed state tooltip opens on focus
  - no tooltip appears for non-failed states
- `SongsetRow`:
  - passes failure fields to the badge
  - tooltip interaction does not trigger row navigation
- `SongsetEditor`:
  - shows inline failure alert
  - shows fallback text when error message is absent
  - `Render again` calls the render handler

## Verification

Run:

```bash
cd webapp && pnpm test -- --run src/test/components/songset/RenderStatusBadge.test.tsx
cd webapp && pnpm test -- --run src/test/components/songset/SongsetRow.test.tsx
cd webapp && pnpm test -- --run src/test/components/songset/SongsetEditor.test.tsx
cd webapp && pnpm test -- --run src/test/api/songsets/db.test.ts
cd webapp && pnpm lint
cd webapp && pnpm build
```

## Assumptions

- The list UX should remain compact; persistent detail belongs on the editor page.
- Tooltip discoverability is acceptable on the list as long as hover and keyboard focus work.
- End users should see safe summaries, not raw worker tracebacks.
- `startedAt ?? createdAt` is the fallback timestamp policy, and fallback copy should ask the user to render again.
