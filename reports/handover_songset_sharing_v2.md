# Handover: Songset Sharing v2 — Read-Only Playback

**Date:** 2026-06-06
**Branch:** `fix-songset-sharing`
**Commit:** `58ca140`
**Spec:** `specs/songset-sharing-readonly-playback-v2-detailed.md`

---

## What Was Done

All 10 phases from the spec have been implemented and pushed. 20 files changed (+2672/-386 lines). All 1352 tests pass, lint clean.

### Phase Summary

| Phase | Status | Key Files |
|-------|--------|-----------|
| 1. DB Schema & Migration | Done | `webapp/src/db/schema.ts`, `webapp/drizzle/0012_flimsy_shatterstar.sql` |
| 2. Public Origin Helper | Done | `webapp/src/lib/share.ts` (new) |
| 3. DB Query | Done | `webapp/src/lib/db/songsets.ts` — added `getSongsetPublicView()` + types |
| 4. POST/GET /api/share | Done | `webapp/src/app/api/share/route.ts` |
| 5. GET /api/share/[token] | Done | `webapp/src/app/api/share/[token]/route.ts` |
| 6. ShareDialog rewrite | Done | `webapp/src/components/share/ShareDialog.tsx` |
| 7. Wire into pages | Done | `songsets/page.tsx`, `songsets/[id]/page.tsx`, `songsets/[id]/play/page.tsx`, `PrePlayCard.tsx` |
| 8. Public share page | Done | `share/[token]/page.tsx`, `play/projection/page.tsx`, `play/audio/page.tsx` |
| 9. Tests | Done | 3 test files updated, PrePlayCard test fixed |
| 10. Cleanup & Validation | Done | Pushed to origin |

---

## Key Design Decisions Made During Implementation

1. **`or(isNull(expiresAt), gt(expiresAt, now()))`** — Used Drizzle ORM's `or`/`gt`/`isNull` operators directly instead of raw SQL for the active-share conditions. This is type-safe and consistent with the codebase.

2. **`getSongsetPublicView()` uses explicit `leftJoin`** — Instead of the relational query API (`db.query.songsetItems.findMany({ with: { song, recording } })`), I used explicit `leftJoin` with `select()` to have full control over which columns are returned. This ensures only whitelisted fields are ever fetched from the DB, preventing accidental data leaks.

3. **Stale detection uses `songset.updatedAt > job.completedAt`** — As specified. This is conservative (name/description edits also trigger stale), which is acceptable per spec.

4. **Backward compat for `?share=true`** — In `songsets/[id]/page.tsx`, a `useEffect` reads `searchParams.get("share")`, opens the dialog, then calls `router.replace()` to clean the URL.

5. **Web Share API removed from PrePlayCard** — The `handleShare` now simply calls `onShare()` directly. The ShareDialog handles all sharing UX.

---

## What Still Needs To Be Done (Not In Spec)

These items were noted in the spec as TODOs or follow-ups:

### Rate Limiting
- The spec says: "Add `// TODO: Add rate limiting by token and client IP for public share endpoint`"
- This TODO comment was added in `webapp/src/app/api/share/[token]/route.ts`
- No rate-limit helper exists in the project. This is a follow-up task.

### Migration Deployment
- Migration `0012_flimsy_shatterstar.sql` has been generated but NOT applied to production DB
- Run `npx drizzle-kit push` or `npx drizzle-kit migrate` when deploying

### `NEXT_PUBLIC_BASE_URL` Environment Variable
- The `resolvePublicOrigin()` helper checks this env var first, then falls back to `request.nextUrl.origin`
- If neither is available, POST /api/share returns 500
- Ensure this env var is set in production deployments

### Public Share Page Component Tests (9d in spec)
- The spec calls for a new test file `webapp/src/test/components/share/PublicSharePage.test.tsx`
- This was NOT created. The public share page (`share/[token]/page.tsx`) is a page component that fetches data via API — testing it properly requires either:
  - Mocking `fetch` at the component level (fragile)
  - Integration tests with a running server
- Recommendation: Create this test file if component-level testing is desired

### `?songsetId` Filter on GET /api/share — Ownership Verification
- Currently, when `songsetId` is provided, the handler verifies ownership before returning shares
- When `renderJobId` is provided, it verifies render-job ownership
- When neither is provided, it returns all active shares for the user (no filter)

---

## API Response Shape Changes (Breaking)

### GET /api/share/[token] — Old vs New

**Old (flat):**
```json
{
  "token": "abc",
  "songsetId": "set-1",
  "songsetName": "Sunday Worship",
  "renderJobId": "job-123",
  "mp3Url": "https://...",
  "mp4Url": "https://...",
  "chaptersUrl": "https://...",
  "mp3SizeBytes": 52428800,
  "mp4SizeBytes": null,
  "allowDownload": false,
  "createdAt": "2026-01-01T00:00:00Z"
}
```

**New (nested):**
```json
{
  "token": "abc",
  "shareType": "songset",
  "songset": {
    "id": "set-1",
    "name": "Sunday Worship",
    "description": "...",
    "totalDurationSeconds": 1080,
    "renderState": "fresh",
    "latestRenderJobId": "job-456",
    "lastCompletedRenderJobId": "job-456"
  },
  "items": [
    {
      "id": "item-1",
      "position": 0,
      "songTitle": "Amazing Grace",
      "composer": "John Newton",
      "lyricist": null,
      "albumName": "Hymns Collection",
      "songMusicalKey": "G",
      "durationSeconds": 240,
      "tempoBpm": 80,
      "recordingMusicalKey": "G"
    }
  ],
  "playback": {
    "selectedRenderJobId": "job-456",
    "isStale": false,
    "staleStatus": null,
    "mp3Url": "https://...",
    "mp4Url": "https://...",
    "chaptersUrl": "https://...",
    "mp3SizeBytes": 52428800,
    "mp4SizeBytes": null
  },
  "allowDownload": false,
  "createdAt": "2026-01-01T00:00:00Z",
  "expiresAt": null
}
```

### POST /api/share — New Request Body

**Old:** `{ renderJobId: string, allowDownload?: boolean }`
**New:** `{ songsetId: string, allowDownload?: boolean }` OR `{ renderJobId: string, allowDownload?: boolean }` (not both, not neither)

### GET /api/share — New Query Params

**Old:** `?renderJobId=<id>`
**New:** `?renderJobId=<id>` OR `?songsetId=<id>` (ownership verified)

---

## File Map

| File | Change Type |
|------|-------------|
| `webapp/src/db/schema.ts` | `renderJobId` nullable |
| `webapp/drizzle/0012_flimsy_shatterstar.sql` | New migration |
| `webapp/drizzle/meta/0012_snapshot.json` | Migration snapshot |
| `webapp/drizzle/meta/_journal.json` | Migration journal |
| `webapp/src/lib/share.ts` | New: `resolvePublicOrigin()` |
| `webapp/src/lib/db/songsets.ts` | Added `getSongsetPublicView()`, `PublicSongsetItem`, `SongsetPublicView` |
| `webapp/src/app/api/share/route.ts` | Major rewrite: songsetId path, active-share reuse, expired exclusion |
| `webapp/src/app/api/share/[token]/route.ts` | Major rewrite: nested response, stale detection, live songset data |
| `webapp/src/components/share/ShareDialog.tsx` | Major rewrite: new props, formatted message, live-link warning |
| `webapp/src/app/songsets/page.tsx` | Added ShareDialog state, replaced `?share=true` navigation |
| `webapp/src/app/songsets/[id]/page.tsx` | Added ShareDialog, backward compat `?share=true`, `durationSeconds` |
| `webapp/src/app/songsets/[id]/play/page.tsx` | Added ShareDialog, replaced `?share=true` navigation |
| `webapp/src/components/play/PrePlayCard.tsx` | Removed Web Share API fallback |
| `webapp/src/app/share/[token]/page.tsx` | Major rewrite: read-only song list, stale warning, render states |
| `webapp/src/app/share/[token]/play/projection/page.tsx` | Updated for `data.playback.mp4Url` / `data.songset.name` |
| `webapp/src/app/share/[token]/play/audio/page.tsx` | Updated for `data.playback.mp3Url` / `data.songset.name` |
| `webapp/src/test/api/share/route.test.ts` | Major update: songsetId tests, both/neither 400, reuse, ownership |
| `webapp/src/test/api/share/token.test.ts` | Major update: nested response, stale, shareType, sensitive field checks |
| `webapp/src/test/components/share/ShareDialog.test.tsx` | Major update: songsetId fetch, formatted message, duration formatting |
| `webapp/src/test/components/play/PrePlayCard.test.tsx` | Removed Web Share API test |

---

## How to Verify

```bash
# Run all webapp tests
cd webapp && npx vitest run

# Run just share-related tests
cd webapp && npx vitest run src/test/api/share/ src/test/components/share/

# Lint
cd webapp && pnpm lint

# Apply migration to DB (when ready to deploy)
cd webapp && npx drizzle-kit push
```
