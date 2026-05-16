# Fix: missing `@/lib/db/songsets` module breaks `/songsets` (v2)

## Context

After login, navigating to `/songsets` throws:

```
./webapp/src/app/api/songsets/route.ts:3:1
Module not found: Can't resolve '@/lib/db/songsets'
```

The webapp has the full songset feature wired up — DB schema (`songsets`, `songsetItems`, `renderJobs`, relations in `webapp/src/db/schema.ts`), four API routes (`/api/songsets`, `/api/songsets/[id]`, `/api/songsets/[id]/items`, `/api/songsets/[id]/items/reorder`), the page (`/songsets`), UI components (`SongsetList`, `SongsetRow`, `RenderStateButton`), and a vitest spec at `webapp/src/test/api/songsets/db.test.ts` that fully describes the expected function signatures. The one missing piece is the DB helper module the routes import. Creating it unblocks the Songset List feature (spec: `specs/webapp_ui_requirements_v5.md` §5.1).

**Changes from v1:** This version resolves two operational issues deferred by v1:
1. **Song hydration via Drizzle relation** — v1 deferred adding a `song` relation to `songsetItemsRelations` and proposed a second `inArray` query. This doesn't work: the existing test mocks `song` inline on items returned from `findFirst`, implying the relation approach, and `db.query.songs` is not mocked. Adding the relation is the correct fix and requires no migration (Drizzle relations are TS-only).
2. **Timestamp nullability fixed at schema level** — v1 typed four timestamp columns as `Date` in interfaces while the schema had no `.notNull()` (making TS see `Date | null`). Since there is no production data to migrate, adding `.notNull()` to the schema and generating a migration is the correct fix, not `!` assertions.

## Critical files

**To create:**
- `webapp/src/lib/db/songsets.ts` — new module exporting the functions listed below.

**To modify:**
- `webapp/src/db/schema.ts` — (a) add `.notNull()` to four timestamp columns; (b) add `song` relation to `songsetItemsRelations`.
- `webapp/drizzle/` — generated Drizzle migration for the `.notNull()` column changes.
- `webapp/src/test/api/songsets/db.test.ts` — extend `updateSongset` mock fixture to include `items: []` on the second `findFirst` call (for `itemCount` derivation).

**Reference (do not modify):**
- `webapp/src/lib/db/songs.ts` — pattern for Drizzle helpers (findMany with relations, count via `db.select({ count: sql<number>... })`).
- `webapp/src/db/schema.ts` — `songsets`, `songsetItems`, `renderJobs` tables and their relations.
- `webapp/src/test/api/songsets/db.test.ts` — defines required exports, signatures, and return shapes (drives the contract).
- `webapp/src/app/api/songsets/route.ts` and the three sibling route files — show how each function is called.
- `webapp/src/app/songsets/page.tsx` — defines the `ApiSongset` shape the list endpoint must return.
- `webapp/src/components/songset/RenderStateButton.tsx` — `RenderState = "unrendered" | "rendering" | "fresh" | "stale" | "failed"`.
- `specs/webapp_ui_requirements_v5.md` §5.1 — UX contract for Songset List, including the `stale` state semantics.

## Schema changes (`webapp/src/db/schema.ts`)

### 1. Add `.notNull()` to timestamp columns

Four columns are `timestamp({withTimezone:true}).defaultNow()` without `.notNull()`, causing Drizzle to type them as `Date | null`. Consumers treat them as non-null. Fix:

- `songsets.createdAt` → `.defaultNow().notNull()`
- `songsets.updatedAt` → `.defaultNow().notNull()`
- `songsetItems.createdAt` → `.defaultNow().notNull()`
- `renderJobs.completedAt` → `.notNull()` (no default — set explicitly when job completes)

After this change, the interfaces below can type these as `Date` without assertions.

Generate the migration:
```bash
cd webapp
pnpm drizzle-kit generate
```

### 2. Add `song` relation to `songsetItemsRelations`

`songsetItems.songId` has no FK constraint at the DB level, but a Drizzle TS-only relation enables single-query hydration:

```ts
export const songsetItemsRelations = relations(songsetItems, ({ one }) => ({
  songset: one(songsets, { fields: [songsetItems.songsetId], references: [songsets.id] }),
  song: one(songs, { fields: [songsetItems.songId], references: [songs.id] }),
}));
```

No migration required — Drizzle relations are metadata only. This enables `with: { song: true }` in `findFirst`/`findMany` queries on `songsetItems`.

## Implementation (`webapp/src/lib/db/songsets.ts`)

Modeled on `songs.ts`. Imports:

```ts
import { db } from "@/db";
import { songsets, songsetItems, renderJobs } from "@/db/schema";
import { eq, and, desc, gt, sql } from "drizzle-orm";
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
  recording: null;
}

export interface SongsetDetail extends SongsetListItem {
  items: SongsetItemDetail[];
}
```

**Note on `recording`:** `songsetItems` has no Drizzle relation to `recordings`. The existing test mock accepts `recording: null`. Keep `recording: null` for now.

### Functions

All take `userId: number` (matches `users.id` `bigint(mode: "number")`).

1. **`computeRenderState(songsetId: string): Promise<RenderState>`**
   - `db.query.songsets.findFirst({ where: eq(songsets.id, songsetId) })`. Throw `"Songset not found"` if missing.
   - If `latestRenderJobId == null` → `"unrendered"`.
   - `db.query.renderJobs.findFirst({ where: eq(renderJobs.id, latestRenderJobId) })`.
   - `queued` or `running` → `"rendering"`.
   - `failed` (or `lastFailedRenderJobId === latestRenderJobId`) → `"failed"`.
   - `completed`:
     - Any `songsetItems.createdAt > renderJob.completedAt` (via `db.query.songsetItems.findFirst({ where: and(eq(songsetItems.songsetId, songsetId), gt(songsetItems.createdAt, renderJob.completedAt)) })`) → `"stale"`.
     - Else if `songsets.updatedAt > renderJob.completedAt` → `"stale"`.
     - Else → `"fresh"`.
   - Unknown status → `"unrendered"`.

2. **`listSongsets(userId, limit = 50, offset = 0): Promise<{ songsets: SongsetListItem[]; total: number }>`**
   - `db.query.songsets.findMany({ where: eq(songsets.userId, userId), orderBy: [desc(songsets.updatedAt)], limit, offset, with: { items: { columns: { id: true } } } })`.
   - Total via `db.select({ count: sql<number>\`count(*)\` }).from(songsets).where(eq(songsets.userId, userId))`.
   - Map rows via `Promise.all` — `itemCount = row.items.length`, `renderState = await computeRenderState(row.id)`.

3. **`getSongset(id, userId): Promise<SongsetDetail | null>`**
   - `db.query.songsets.findFirst({ where: and(eq(songsets.id, id), eq(songsets.userId, userId)), with: { items: { with: { song: true } } } })`. Return `null` if missing.
   - Sort items by `position` ascending in TS.
   - Map each item to `SongsetItemDetail` with `recording: null`.
   - `renderState = await computeRenderState(id)`. `itemCount = items.length`.

4. **`createSongset(userId, { name, description? }): Promise<SongsetListItem>`**
   - `id = nanoid()`. `db.insert(songsets).values({ id, userId, name, description: description ?? null }).returning()`.
   - Return with `itemCount: 0`, `renderState: "unrendered"`, both render-job IDs `null`. (No need to call `computeRenderState` — newly created songsets are trivially `"unrendered"`.)

5. **`updateSongset(id, userId, patch): Promise<SongsetListItem | null>`**
   - Ownership check via `db.query.songsets.findFirst({ where: and(eq(songsets.id, id), eq(songsets.userId, userId)) })`. Return `null` if missing.
   - `db.update(songsets).set({ ...patch, updatedAt: new Date() }).where(and(...)).returning()`.
   - Re-fetch via `db.query.songsets.findFirst({ where: and(...), with: { items: { columns: { id: true } } } })` to derive `itemCount = items.length`.
   - `renderState = await computeRenderState(id)`. Return `SongsetListItem`.

6. **`deleteSongset(id, userId): Promise<boolean>`**
   - Ownership check via `findFirst`. Return `false` if missing.
   - `db.delete(songsets).where(and(eq(songsets.id, id), eq(songsets.userId, userId)))`. Return `true`. (`songsetItems` cascades via FK.)

7. **`addSongsetItem(songsetId, userId, data): Promise<SongsetItemDetail | null>`**
   - Ownership check via `db.query.songsets.findFirst({ where: and(eq(songsets.id, songsetId), eq(songsets.userId, userId)) })`. Return `null` if missing.
   - `id = nanoid()`. `db.insert(songsetItems).values({ id, songsetId, ...data }).returning()`.
   - Touch `songsets.updatedAt`: `db.update(songsets).set({ updatedAt: new Date() }).where(eq(songsets.id, songsetId))`.
   - Re-fetch the inserted item: `db.query.songsetItems.findFirst({ where: eq(songsetItems.id, id), with: { song: true } })`.
   - Return as `SongsetItemDetail` with `recording: null`.

8. **`updateSongsetItem(itemId, songsetId, userId, patch): Promise<SongsetItemDetail | null>`**
   - `db.query.songsetItems.findFirst({ where: eq(songsetItems.id, itemId), with: { songset: true } })`.
   - If missing, or `item.songsetId !== songsetId`, or `item.songset.userId !== userId` → `null`.
   - `db.update(songsetItems).set(patch).where(eq(songsetItems.id, itemId)).returning()`.
   - Touch `songsets.updatedAt`.
   - Re-fetch: `db.query.songsetItems.findFirst({ where: eq(songsetItems.id, itemId), with: { song: true } })`. Return as `SongsetItemDetail`.

9. **`deleteSongsetItem(itemId, songsetId, userId): Promise<boolean>`**
   - Same ownership check as #8. Return `false` if denied.
   - `db.delete(songsetItems).where(eq(songsetItems.id, itemId))`. Touch `songsets.updatedAt`. Return `true`.

## Test mock updates (`webapp/src/test/api/songsets/db.test.ts`)

Minimal changes required — the existing fixtures are mostly compatible with the above approach:

- **`getSongset` "returns songset with items":** No change needed. The existing mock supplies `items[i].song` inline on the `findFirst` result, which now correctly matches what Drizzle returns via `with: { song: true }`.
- **`updateSongset` "updates songset name and description":** Extend the `findFirst` mock to return `items: []` on the second call (post-update re-fetch). Use `mockResolvedValueOnce` for ownership check (no items needed), then `mockResolvedValueOnce` with `{ ...mockSongset, items: [] }` for the itemCount re-fetch.
- **`addSongsetItem`:** The test already mocks `db.insert(...).returning()` and `songsetItems.findFirst` for re-fetch. Verify the mock chain for the `songsets.update` touch call is compatible (the test's `db.update` mock is set up for `updateSongset`; `addSongsetItem` also calls it — may need `beforeEach` mock isolation check).
- **`gt` mock:** `gt` is not mocked in `vi.mock("drizzle-orm")` — it falls through to the real drizzle-orm helper. This is fine; the test mocks on `db.query.songsetItems.findFirst` control the return value, not the `where` argument shape.

## Out of scope

- **Reorder route bug.** `webapp/src/app/api/songsets/[id]/items/reorder/route.ts:44` uses `songsetItems.songsetId` inside `db.query.songsets.findFirst.where` — not on the failing path. Leave for the editor drag-to-reorder UX follow-up.
- **DB-level FK constraint on `songsetItems.songId`.** The Drizzle `song` relation is TS-only. A SQL `REFERENCES songs(id)` constraint is a separate migration with cascade considerations.
- **`isOfflineAvailable` / `✈ Offline` badge.** Page hard-codes `false`. Separate feature.
- **Transactions** around insert-then-touch sequences (`addSongsetItem`, `updateSongsetItem`, `deleteSongsetItem`). Low-impact eventual consistency gap; not required to unblock.
- **N+1 in `listSongsets`.** `Promise.all` over rows calling `computeRenderState` (2 queries each) is acceptable at this scale. Optimization — batch-fetch render jobs via `inArray` — is a follow-up.

## Verification

### Type check
```bash
cd webapp
pnpm tsc --noEmit
```
Verify `session.user.id` resolves as `number` (Better Auth `useNumberId` + `generateId: "serial"`). If TS resolves it as `string`, routes need `Number(session.user.id)` coercion.

### Migration
```bash
cd webapp
pnpm drizzle-kit generate
pnpm drizzle-kit migrate   # or the project's standard apply command
```

Confirm in Postgres:
```sql
\d songsets       -- created_at, updated_at must show "not null"
\d songset_items  -- created_at must show "not null"
\d render_jobs    -- completed_at must show "not null"
```

### Unit tests
```bash
cd webapp
pnpm vitest run src/test/api/songsets/db.test.ts
pnpm vitest run src/test/api/songsets/
```

All cases must pass:
- `computeRenderState` — unrendered, fresh, failed, rendering(queued), rendering(running), not-found.
- `listSongsets` — paginated result, limit/offset propagation.
- `getSongset` — found, not-found, wrong-user.
- `createSongset` — with description, without description.
- `updateSongset` — found, not-found.
- `deleteSongset` — found, not-found.
- `addSongsetItem` — found, not-found.
- `updateSongsetItem` — found, not-found, wrong-songset.
- `deleteSongsetItem` — found, not-found.

### Manual smoke test (golden path)

```bash
cd webapp
pnpm dev
```

1. Sign in → click Songsets in nav → page renders without the "Module not found" error.
2. Empty state shows ("No songsets yet").
3. Click FAB / "Create" → create "Test set" → row shows `0 songs · —` with **Render** button (`unrendered`).
4. Click row → editor opens (`getSongset` must succeed).
5. Rename via context menu → row updates.
6. Delete → row disappears.

Expected server logs:
```
GET    /api/songsets       200
POST   /api/songsets       201
GET    /api/songsets/<id>  200
PATCH  /api/songsets/<id>  200
DELETE /api/songsets/<id>  200
```
