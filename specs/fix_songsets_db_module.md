# Fix: missing `@/lib/db/songsets` module breaks `/songsets`

## Context

After login, navigating to `/songsets` throws:

```
./webapp/src/app/api/songsets/route.ts:3:1
Module not found: Can't resolve '@/lib/db/songsets'
```

The webapp has the full songset feature wired up — DB schema (`songsets`, `songsetItems`, `renderJobs`, relations in `webapp/src/db/schema.ts`), four API routes (`/api/songsets`, `/api/songsets/[id]`, `/api/songsets/[id]/items`, `/api/songsets/[id]/items/reorder`), the page (`/songsets`), UI components (`SongsetList`, `SongsetRow`, `RenderStateButton`), and a vitest spec at `webapp/src/test/api/songsets/db.test.ts` that fully describes the expected function signatures. The one missing piece is the DB helper module the routes import. Creating it unblocks the Songset List feature (spec: `specs/webapp_ui_requirements_v5.md` §5.1).

## Critical files

**To create:**
- `webapp/src/lib/db/songsets.ts` — new module exporting the functions listed below.

**Reference (do not modify):**
- `webapp/src/lib/db/songs.ts` — pattern for Drizzle helpers in this codebase (query.findMany with relations, count via `db.select({ count: sql<number>... })`).
- `webapp/src/db/schema.ts` — `songsets`, `songsetItems`, `renderJobs` tables and their relations (already correct).
- `webapp/src/test/api/songsets/db.test.ts` — defines required exports, signatures, and return shapes (drives the contract).
- `webapp/src/app/api/songsets/route.ts` and the three sibling route files — show how each function is called.
- `webapp/src/app/songsets/page.tsx` — defines the `ApiSongset` shape the list endpoint must return.
- `webapp/src/components/songset/RenderStateButton.tsx` — `RenderState = "unrendered" | "rendering" | "fresh" | "stale" | "failed"`.
- `specs/webapp_ui_requirements_v5.md` §5.1 — UX contract for Songset List, including the `stale` state semantics.

## Implementation

Create `webapp/src/lib/db/songsets.ts` modeled on `songs.ts`. Use `nanoid()` for new IDs (already used elsewhere in webapp). Imports:

```ts
import { db } from "@/db";
import { songsets, songsetItems, renderJobs, songs } from "@/db/schema";
import { eq, and, desc, gt, inArray, sql } from "drizzle-orm";
import { nanoid } from "nanoid";
```

### Types

```ts
export type RenderState = "unrendered" | "rendering" | "fresh" | "stale" | "failed";

export interface SongsetListItem {
  id: string;
  name: string;
  description: string | null;
  createdAt: Date;
  updatedAt: Date;
  renderState: RenderState;
  itemCount: number;
  latestRenderJobId: string | null;
  lastFailedRenderJobId: string | null;
}

export interface SongsetItemDetail {
  id: string;
  songId: string;
  recordingHashPrefix: string | null;
  position: number;
  gapBeats: number | null;
  crossfadeEnabled: number | null;
  crossfadeDurationSeconds: number | null;
  keyShiftSemitones: number | null;
  tempoRatio: number | null;
  song: {
    id: string;
    title: string;
    composer: string | null;
    lyricist: string | null;
    albumName: string | null;
    musicalKey: string | null;
  } | null;
  recording: null; // see note below
}

export interface SongsetDetail extends SongsetListItem {
  items: SongsetItemDetail[];
}
```

**Note on `recording`:** `songsetItems` does not declare a Drizzle relation to `recordings`. The existing test mock accepts `recording: null`, and the Songset List page doesn't surface recording fields. Keep `recording: null` for now; do not invent a relation in this fix.

**Note on `song`:** Likewise no Drizzle relation exists on `songsetItems.songId`. For `getSongset`, fetch song metadata in a second query using `inArray(songs.id, distinctSongIds)` and attach. Avoids a schema change.

### Functions

All take `userId: number` (matches `users.id` `bigint(mode: "number")`).

1. **`computeRenderState(songsetId: string): Promise<RenderState>`**
   - Fetch songset; throw `"Songset not found"` if missing.
   - If `latestRenderJobId == null` → `"unrendered"`.
   - Fetch the latest render job by `id = latestRenderJobId`.
   - If job `status` is `queued` or `running` → `"rendering"`.
   - If job `status === "failed"` (or `lastFailedRenderJobId === latestRenderJobId`) → `"failed"`.
   - If job `status === "completed"`:
     - If any `songsetItems` row with `songsetId` has `createdAt > renderJob.completedAt` → `"stale"`.
     - Else if `songsets.updatedAt > renderJob.completedAt` → `"stale"`.
     - Otherwise → `"fresh"`.
   - Default fallback for unknown statuses → `"unrendered"`.

   The `stale` branch matches spec §5.1 ("render complete, but edits since"). The existing test does not cover `stale`, but the UI does (`RenderStateButton`, `SongsetRow`, `PrePlayCard` all branch on `"stale"`). Adding it is consistent with the schema and the page contract.

2. **`listSongsets(userId, limit = 50, offset = 0): Promise<{ songsets: SongsetListItem[]; total: number }>`**
   - `db.query.songsets.findMany({ where: eq(songsets.userId, userId), orderBy: [desc(songsets.updatedAt)], limit, offset, with: { items: true } })`.
   - Total count via `db.select({ count: sql<number>\`count(*)\` }).from(songsets).where(eq(songsets.userId, userId))`.
   - Map each row → `SongsetListItem` with `itemCount = row.items.length` and `renderState = await computeRenderState(row.id)`. Use `Promise.all` over the row mappings.

3. **`getSongset(id, userId): Promise<SongsetDetail | null>`**
   - `db.query.songsets.findFirst({ where: and(eq(songsets.id, id), eq(songsets.userId, userId)), with: { items: true } })`. Return `null` if missing.
   - Resolve song metadata via a single `db.query.songs.findMany({ where: inArray(songs.id, [...distinctSongIds]) })` and attach as `item.song`.
   - Sort items by `position` ascending.
   - Attach `renderState` via `computeRenderState`.

4. **`createSongset(userId, { name, description? }): Promise<SongsetListItem>`**
   - `id = nanoid()`. Insert via `db.insert(songsets).values({ id, userId, name, description: description ?? null }).returning()`.
   - Return shape with `itemCount: 0`, `renderState: "unrendered"`, both render-job IDs `null`.

5. **`updateSongset(id, userId, patch): Promise<SongsetListItem | null>`**
   - First `findFirst` with `(id, userId)` for existence/ownership check (returns `null` if not found).
   - `db.update(songsets).set({ ...patch, updatedAt: new Date() }).where(and(eq(songsets.id, id), eq(songsets.userId, userId))).returning()`.
   - Compute `itemCount` via a `count(*)` on `songsetItems` for the songset. Compute `renderState` via `computeRenderState`. Return as `SongsetListItem`.

6. **`deleteSongset(id, userId): Promise<boolean>`**
   - Ownership check via `findFirst`. If missing → `false`.
   - `db.delete(songsets).where(and(eq(songsets.id, id), eq(songsets.userId, userId)))`. Return `true`. (`songsetItems` cascades via FK.)

7. **`addSongsetItem(songsetId, userId, data): Promise<SongsetItemDetail | null>`**
   - Verify songset ownership via `findFirst` with `(id, userId)`. If missing → `null`.
   - `id = nanoid()`. Insert via `db.insert(songsetItems).values({ id, songsetId, ...data }).returning()`.
   - Hydrate `song` via a `songs.findFirst` lookup; `recording: null`.
   - Touch `songsets.updatedAt = new Date()` (so `stale` detection works on add).

8. **`updateSongsetItem(itemId, songsetId, userId, patch): Promise<SongsetItemDetail | null>`**
   - Fetch item via `db.query.songsetItems.findFirst({ where: eq(songsetItems.id, itemId), with: { songset: true } })`.
   - If item missing, or `item.songsetId !== songsetId`, or `item.songset.userId !== userId` → `null`.
   - `db.update(songsetItems).set(patch).where(eq(songsetItems.id, itemId)).returning()`.
   - Touch `songsets.updatedAt`.
   - Re-fetch with hydrated `song`; return `SongsetItemDetail`.

9. **`deleteSongsetItem(itemId, songsetId, userId): Promise<boolean>`**
   - Same ownership check as #8.
   - `db.delete(songsetItems).where(eq(songsetItems.id, itemId))`. Touch `songsets.updatedAt`. Return `true`/`false`.

### Out of scope for this fix

- **Reorder route bug.** `webapp/src/app/api/songsets/[id]/items/reorder/route.ts` has a broken Drizzle query (uses `songsetItems.songsetId` inside a `songsets.findFirst` `where`). It's not on the failing path — the Songset List page doesn't call it. Leave it for a follow-up that lands with the editor drag-to-reorder UX.
- **Adding a Drizzle `song` relation to `songsetItems`.** Manual two-query join keeps the schema unchanged. Worth doing later for `getSongset` performance if it becomes hot, but not required to unblock the list.
- **`isOfflineAvailable` / `✈ Offline` badge.** The page hard-codes `isOfflineAvailable: false`. Offline cache detection is a separate feature, tracked in the requirements spec but not in this fix.

## Verification

### Manual smoke test (golden path)

```bash
cd webapp
pnpm dev
```

1. Sign in → click Songsets in nav → page renders without the "Module not found" error.
2. With no songsets: empty state shows ("No songsets yet").
3. Click FAB / "Create" → create "Test set" → list refreshes, row shows `0 songs · —` with **Render** button (state = `unrendered`).
4. Click row → editor opens (won't crash; editor not in scope for this fix but `getSongset` must succeed).
5. Rename via context menu → row updates.
6. Delete → row disappears, no error.

Expected server logs:

```
GET /api/songsets 200
POST /api/songsets 201
GET /api/songsets/<id> 200
PATCH /api/songsets/<id> 200
DELETE /api/songsets/<id> 200
```

### Unit tests

```bash
cd webapp
pnpm vitest run src/test/api/songsets/db.test.ts
```

All cases in the existing spec must pass:
- `computeRenderState` — unrendered, fresh, failed, rendering(queued), rendering(running), not-found.
- `listSongsets` — paginated result, limit/offset propagation.
- `getSongset` — found, not-found, wrong-user.
- `createSongset` — with description, without description.
- `updateSongset` — found, not-found.
- `deleteSongset` — found, not-found.
- `addSongsetItem` — found, not-found.
- `updateSongsetItem` — found, not-found, wrong-songset.
- `deleteSongsetItem` — found, not-found.

The existing test mocks `nanoid` to return `"test-id"` and mocks `drizzle-orm`'s `eq/and/desc/sql`, so the implementation must not depend on those helpers' return shapes beyond passing them to Drizzle.

Also run the route tests:

```bash
pnpm vitest run src/test/api/songsets/
```
