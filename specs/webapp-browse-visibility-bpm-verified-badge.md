# Browse Song Surface: Visibility Relaxation, BPM Rounding, Verified Badge

## Problem

Three targeted enhancements to the webapp Browse Song experience (`BrowseSheet` modal reachable from the Songset Editor at `/songsets/[id]`, including the "Describe" semantic-search tab):

1. **Too few LRC-ready songs in Browse.** Only recordings with `visibility_status = 'published'` are returned by the browse-facing endpoints. Songs whose recordings are in `'review'` (i.e. lyrics LRC work is progressing / awaiting publication) are excluded, hiding usable candidates from the composer.
2. **BPM displays fractional values.** `recordings.tempo_bpm` is a PostgreSQL `real` (single-precision float). Some surfaces render the raw float (e.g. `127.97 BPM`), inconsistent with the share page and `TransitionControls` which already round.
3. **No visual trust signal for published songs.** Once `review` songs appear in Browse alongside `published` songs, users need an at-a-glance indicator that a song is "Verified" (i.e. its recording has `visibility_status = 'published'`).

## Current State

### Visibility filter topology

| Surface | File | Behavior |
|---|---|---|
| `GET /api/songs` | `src/app/api/songs/route.ts:44` | Defaults `visibilityStatus = "published"`; passes single string to `listSongs()` |
| `GET /api/songs/search` | `src/app/api/songs/search/route.ts:31` | Defaults `"published"`; passes to `fullTextSearchSongs()` |
| `POST /api/songs/search/semantic` | `src/app/api/songs/search/semantic/route.ts:51` | Calls `semanticSearchSongs(embedding, model, limit)` — no visibility param threaded |
| `GET /api/songs/albums` | `src/app/api/songs/albums/route.ts:15` | Calls `getAlbums()` — **no visibility filter at all** |
| `ListSongsFilters.visibilityStatus` | `src/lib/db/songs.ts:46` | Single string; `"all"` is a sentinel meaning "no filter" |

The DB layer builds the visibility predicate in three places:

- **`buildPublishedRecordingExistsClause()`** — `src/lib/db/songs.ts:49-61`: emits `recordings.visibility_status = ${visibilityStatus}` as a song-level `EXISTS` subquery. Returns `undefined` when status is falsy or `"all"`.
- **Recording join filter** — `src/lib/db/songs.ts:113-114` (in `listSongs`), `L176-177` (in `getSong`), `L243-244` (in `searchSongs`): `eq(recordings.visibilityStatus, filters.visibilityStatus)` applied to the joined recordings relation.
- **`fullTextSearchSongs`** — `src/lib/db/search.ts:28-47` (EXISTS subquery inline) and `L67-73` (recording join filter): same single-string pattern.
- **`semanticSearchSongs`** — `src/lib/db/songs.ts:368-371`: raw SQL `JOIN recordings r ON r.song_id = s.id AND r.visibility_status = 'published' AND r.deleted_at IS NULL`. Hardcoded.
- **`getAlbums`** — `src/lib/db/songs.ts:300-308`: `selectDistinct({ albumName }).from(songs).where(albumName IS NOT NULL)`. No visibility join — albums with no browseable recordings still appear in the dropdown.

### BPM display audit (existing rounding state)

| Surface | File:Line | Current | Rounded? |
|---|---|---|---|
| **SongCard (Browse results)** | `src/components/songset/SongCard.tsx:129` | `{tempo} BPM` (raw) | **No** |
| **TransitionPanel (song info header)** | `src/components/songset/TransitionPanel.tsx:196, 208` | `{fromSong.tempoBpm} BPM` / `{toSong.tempoBpm} BPM` (raw) | **No** |
| TransitionControls (computed tempo) | `src/components/transition/TransitionControls.tsx:182` | `currentBpm = Math.round(refBpm * tempoRatio)` | Yes |
| Share page | `src/app/share/[token]/page.tsx:233` | `Math.round(item.tempoBpm)` | Yes |

Note: `TransitionControls.tsx:65` computes `bpmDelta = Math.round(refBpm * tempoRatio) - refBpm` — mixing a rounded product with raw `refBpm`. This produces slightly off deltas (e.g. `0.5`-off) but is out of scope for this spec; only the **display** rounding is in scope.

### Verified badge — data availability

`visibilityStatus` is already populated at every layer:

- DB mapper populates it from `r.visibilityStatus` on all of `listSongs` (`songs.ts:162`), `getSong` (`songs.ts:224`), `searchSongs` (`songs.ts:292`), `fullTextSearchSongs` (`search.ts:100`), `semanticSearchSongs` (`songs.ts:409`).
- API routes return the helpers' result verbatim via `NextResponse.json(result)` — no field stripping.
- `SongCardData` (defined inline in `SongCard.tsx:10-24`) is a narrowed type and **omits** `visibilityStatus` from its `recordings[]` element type. Runtime data is present (structural typing passes the field through), but the type is incomplete.
- `BrowseSheet.tsx:33-36` casts the JSON response to `SearchResult { songs: SongCardData[] }` — type-level strip only; runtime field survives.

### Existing icon/badge precedents

- `Badge` UI primitive — `src/components/ui/badge.tsx` (variants: `default | secondary | destructive | outline | ghost | link`).
- `SongCard` already renders an `outline`-variant `Badge` for the musical key at `SongCard.tsx:124-126`.
- `RenderStatusBadge.tsx` is the strongest precedent for icon-paired status badges — uses `Badge` + lucide icon (e.g. `CheckCircle2`) with configurable variant/label via a `STATE_CONFIG` record, plus optional `Tooltip` wrapper.
- `BadgeCheck`, `ShieldCheck`, `Verified` are **not** currently imported anywhere in `src/components/`. `CheckCircle2` is used in `RenderStatusBadge` (L44) and `RenderComplete.tsx` (L78). `Check` (plain) is used in `SongCard.tsx:156` for the green "added" checkmark.

## Implementation Plan

### Step 1: Extend DB layer to accept multiple visibility statuses

**File:** `src/lib/db/songs.ts`

**1.1.** Change `ListSongsFilters.visibilityStatus` (L46) from `string` to `string | string[]`:

```typescript
visibilityStatus?: string | string[];
```

**1.2.** Rewrite `buildPublishedRecordingExistsClause()` (L49-61) to accept a string or array and emit an `IN (...)` clause for the array case:

```typescript
function buildPublishedRecordingExistsClause(
  visibilityStatus?: string | string[]
) {
  if (!visibilityStatus || visibilityStatus === "all") {
    return undefined;
  }

  if (Array.isArray(visibilityStatus)) {
    if (visibilityStatus.length === 0) return undefined;
    return sql`exists (
      select 1
      from recordings
      where recordings.song_id = ${songs.id}
        and recordings.visibility_status = ANY(${sql`ARRAY[${sql.join(visibilityStatus.map(s => sql`${s}`), sql`, `)}]::text[]`})
        and recordings.deleted_at IS NULL
    )`;
  }

  return sql`exists (
    select 1
    from recordings
    where recordings.song_id = ${songs.id}
      and recordings.visibility_status = ${visibilityStatus}
      and recordings.deleted_at IS NULL
  )`;
}
```

Alternatively, for the array branch use `recordings.visibility_status IN ${...}` — both forms work; `ANY(ARRAY[...]::text[])` is cleaner with parameterized placeholders. Pick whichever reads cleanly; the helper is private so either is fine.

**1.3.** Add a private helper to normalize a status value into a predicate fragment for the recording join (used by `listSongs`, `searchSongs`, `getSong`):

```typescript
function recordingVisibilityPredicate(
  visibilityStatus?: string | string[],
  recordingsAlias = recordings
) {
  // Returns undefined to skip filtering, or a condition for the join's WHERE.
  if (!visibilityStatus || visibilityStatus === "all") return undefined;
  if (Array.isArray(visibilityStatus)) {
    if (visibilityStatus.length === 0) return undefined;
    return recordingsAlias.visibilityStatus
      ? inArray(recordingsAlias.visibilityStatus, visibilityStatus)
      : undefined;
  }
  return eq(recordingsAlias.visibilityStatus, visibilityStatus);
}
```

(Requires importing `inArray` from `drizzle-orm` at L3.) Where the join's WHERE callback signature (`(recordings, { and, eq, isNull }) => ...`) is used (e.g. `search.ts:67`), use the destructured helpers; the inArray-equivalent there must use the callback's table alias. See Step 2.2 below for the `search.ts` adaptation.

**1.4.** Update `listSongs()` (L113-114) to use the helper:

```typescript
const recordingWhereConditions = [];
const visPredicate = recordingVisibilityPredicate(filters?.visibilityStatus);
if (visPredicate) recordingWhereConditions.push(visPredicate);
recordingWhereConditions.push(isNull(recordings.deletedAt));
```

**1.5.** Leave `getSong()` (L172) with its `"published"` default. It is used by the Songset Editor detail view (single-song lookup), which intentionally stays published-only. No change to its default. If a future caller needs the array form, the signature already supports it via `ListSongsFilters`.

**1.6.** Update `searchSongs()` (L243-244) the same way as 1.4 (use `recordingVisibilityPredicate`).

**1.7.** Update `semanticSearchSongs()` (L333-414):

- Add a parameter: `visibilityStatuses: string[] = ["published", "review"]` to the signature (L333-337).
- Change the SQL JOIN condition (L368-371) from:
  ```
  JOIN recordings r ON r.song_id = s.id
    AND r.visibility_status = 'published'
    AND r.deleted_at IS NULL
  ```
  to:
  ```
  JOIN recordings r ON r.song_id = s.id
    AND r.visibility_status = ANY(${sql`ARRAY[${sql.join(visibilityStatuses.map(s => sql`${s}`), sql`, `)}]::text[]`)})
    AND r.deleted_at IS NULL
  ```
  (Defensive note: `visibilityStatuses` defaults to `["published", "review"]`, matching the browse relaxation. The semantic route does not accept a client-supplied visibility param, so client abuse is not a concern.)

**1.8.** Update `getAlbums()` (L300-308) to filter to albums that have at least one published-or-review, non-deleted recording. This keeps the album dropdown consistent with the song list (otherwise an album with only `draft`/`pending` recordings would appear but yield no songs when selected):

```typescript
export async function getAlbums(): Promise<string[]> {
  const result = await db
    .selectDistinct({ albumName: songs.albumName })
    .from(songs)
    .where(sql`${songs.albumName} IS NOT NULL
      AND exists (
        select 1 from recordings
        where recordings.song_id = ${songs.id}
          and recordings.visibility_status IN ('published', 'review')
          and recordings.deleted_at IS NULL
      )`)
    .orderBy(songs.albumName);

  return result.map((r) => r.albumName).filter((name): name is string => name !== null);
}
```

`getAlbums` has no caller-supplied filter today, so hardcoding the `('published', 'review')` set is consistent with the browse-default intent. (If a future admin caller needs all statuses, add a param at that time.)

### Step 2: Mirror the change in the full-text search helper

**File:** `src/lib/db/search.ts`

**2.1.** Change `fullTextSearchSongs` signature (L10) to accept `string | string[]`:

```typescript
visibilityStatus?: string | string[]
```

**2.2.** Update the EXISTS subquery (L28-47): when `visibilityStatus` is an array (and non-empty), emit `recordings.visibility_status = ANY(ARRAY[...]::text[])`; keep the existing single-string branch and the `"all"`/fallback branch unchanged.

**2.3.** Update the recording join WHERE callback (L67-73): when `visibilityStatus` is an array, push `inArray(recordings.visibilityStatus, visibilityStatus)` onto the conditions list (using the callback's `recordings` table alias; the `inArray` operator must be destructured from the callback's second arg: `(recordings, { and, eq, isNull, inArray }) => {...}`).

**2.4.** The mapper (L78-103) needs no change — it already copies `r.visibilityStatus` through.

### Step 3: Update API route defaults

**3.1.** `src/app/api/songs/route.ts:44` — change default from `"published"` to `["published", "review"]`:

```typescript
const visibilityParam = searchParams.get("visibilityStatus");
const visibilityStatus: string | string[] = visibilityParam
  ? visibilityParam  // respect explicit client override (e.g. "all", "published", "review")
  : ["published", "review"];  // browse default
filters.visibilityStatus = visibilityStatus;
```

**3.2.** `src/app/api/songs/search/route.ts:31` — same change:

```typescript
const visibilityParam = searchParams.get("visibilityStatus");
const visibilityStatus: string | string[] = visibilityParam
  ? visibilityParam
  : ["published", "review"];
```

**3.3.** `src/app/api/songs/search/semantic/route.ts` — no route-level change needed; `semanticSearchSongs`'s new `visibilityStatuses` default (`["published", "review"]`) applies automatically. Optionally, to make the relaxation explicit at the call site (L51) for grep-ability:

```typescript
const songs = await semanticSearchSongs(
  queryEmbedding, QUERY_MODEL, overfetchLimit,
  ["published", "review"],
);
```

**3.4.** `src/app/api/songs/albums/route.ts` — no change. The hardcoded set inside `getAlbums()` (Step 1.8) applies.

### Step 4: Round BPM display in SongCard

**File:** `src/components/songset/SongCard.tsx`

**4.1.** Line 129 — change:

```tsx
{tempo && (
  <span data-testid="song-tempo">{tempo} BPM</span>
)}
```

to:

```tsx
{tempo && (
  <span data-testid="song-tempo">{Math.round(tempo)} BPM</span>
)}
```

The existing truthiness guard (`tempo &&`) already handles `null`/`0`/`undefined`; `Math.round` operates only on the truthy numeric value. No NaN risk.

### Step 5: Round BPM display in TransitionPanel

**File:** `src/components/songset/TransitionPanel.tsx`

**5.1.** Line 196 — change:

```tsx
{fromSong.tempoBpm && `${fromSong.tempoBpm} BPM`}
```

to:

```tsx
{fromSong.tempoBpm && `${Math.round(fromSong.tempoBpm)} BPM`}
```

**5.2.** Line 208 — same change for `toSong.tempoBpm`:

```tsx
{toSong.tempoBpm && `${Math.round(toSong.tempoBpm)} BPM`}
```

### Step 6: Add Verified badge to SongCard

**File:** `src/components/songset/SongCard.tsx`

**6.1.** Extend the `SongCardData.recordings[]` element type (L17-23) to include `visibilityStatus` so the field is typed explicitly (it already flows at runtime via structural typing from the API JSON):

```typescript
recordings: {
  contentHash: string;
  hashPrefix: string;
  durationSeconds: number | null;
  tempoBpm: number | null;
  musicalKey: string | null;
  visibilityStatus: string | null;
}[];
```

**6.2.** Add `BadgeCheck` to the lucide-react import on L6:

```typescript
import { Music, Clock, Disc, Plus, Check, BadgeCheck, Play, Pause, Loader2 } from "lucide-react";
```

(Choosing `BadgeCheck` over `CheckCircle2` because `BadgeCheck` reads unambiguously as "verified" rather than "success/complete" which is what `CheckCircle2` already signals elsewhere in `RenderStatusBadge` / `RenderComplete`. This introduces a visually-distinct, semantically-correct icon for the published trust signal.)

**6.3.** Derive the verified flag (near L59-63, alongside the existing `tempo` / `recordingKey` derivations). Because a song may carry multiple recordings with mixed statuses, check **any** recording is published — not just `recordings[0]` — for a stable result:

```typescript
const isVerified = song.recordings.some(
  (r) => r.visibilityStatus === "published"
);
```

**6.4.** Render the badge inside the `<h4>` title element (L107-109). Preserve the title's truncation by wrapping the title text in an inner `<span className="truncate">` and switching the `<h4>` to `flex items-center gap-1`:

```tsx
<h4 className="font-medium text-sm truncate flex items-center gap-1" data-testid="song-title">
  <span className="truncate">{song.title}</span>
  {isVerified && (
    <BadgeCheck
      className="size-3.5 text-emerald-600 shrink-0"
      data-testid="verified-badge"
      aria-label="Verified"
    />
  )}
</h4>
```

Notes:
- `shrink-0` ensures the icon does not collapse when the title truncates.
- `text-emerald-600` is the trust/positive color, consistent with the green checkmarks used for "added" / "rendered success" elsewhere.
- `size-3.5` matches the small-icon scale (`size-3` is used by the existing meta-row icons; `3.5` reads slightly more prominently next to the title for discoverability — tune during implementation review if needed).
- Places the badge **next to the title** (the primary visual anchor), per the social-media-style "verified account" convention. This is the recommended placement over the meta row because "verified" is a property of the song/recording, not a secondary metadata attribute like key/BPM.

## Files to Modify

| File | Change |
|---|---|
| `delivery/webapp/src/lib/db/songs.ts` | `ListSongsFilters.visibilityStatus` -> `string\|string[]`; rewrite `buildPublishedRecordingExistsClause` for array branch; add `recordingVisibilityPredicate` helper; update `listSongs`, `searchSongs` recording-join filters; add `visibilityStatuses` param to `semanticSearchSongs` + SQL change; filter `getAlbums` by published/review EXISTS |
| `delivery/webapp/src/lib/db/search.ts` | `fullTextSearchSongs` signature -> `string\|string[]`; array branch in EXISTS subquery and recording-join WHERE callback |
| `delivery/webapp/src/app/api/songs/route.ts` | Default `visibilityStatus` -> `["published", "review"]` (respect explicit query-param override) |
| `delivery/webapp/src/app/api/songs/search/route.ts` | Same default change |
| `delivery/webapp/src/app/api/songs/search/semantic/route.ts` | (Optional) Pass explicit `["published", "review"]` to `semanticSearchSongs` at call site |
| `delivery/webapp/src/components/songset/SongCard.tsx` | Add `visibilityStatus` to `SongCardData.recordings`; import `BadgeCheck`; derive `isVerified`; render badge next to title; round BPM with `Math.round` |
| `delivery/webapp/src/components/songset/TransitionPanel.tsx` | Round BPM with `Math.round` at L196 and L208 |

No new files. No DB migrations (the `recordings.visibility_status` column already exists; the `'review'` value is application-level convention, not DB-enforced).

## Design Decisions

### D1. Array vs. sentinel for the multi-status query

**Chosen:** Extend the helper signatures to accept `string | string[]`, treating arrays via `IN`/`ANY(...)` SQL. The `"all"` sentinel is preserved as the "no filter" escape hatch.

**Rationale:** Sentinel strings like `"browse"` would be magic and leak domain semantics into the query layer. The array form is general (a future admin filter for any combination of statuses is now expressible) and matches how Drizzle idioms like `inArray()` work. `getSong` keeps its `"published"` single-string default (it is used by the editor detail view, not Browse), validating that the union type accommodates both call patterns cleanly.

### D2. `getSong` stays published-only

The Songset Editor's per-song detail view should not expose `review` recordings. `getSong(id, visibilityStatus = "published")` keeps its existing default. Only the list/search/semantic/album endpoints switch to the `["published", "review"]` default. This keeps the relaxation scoped to the Browse surface.

### D3. Verified predicate uses `.some()` over all recordings, not `recordings[0]`

After the relaxation, a song's joined recordings may include both `published` and `review` rows; `recordings[0]` ordering is not guaranteed. Using `song.recordings.some(r => r.visibilityStatus === "published")` gives a stable, semantically-correct "this song has at least one published recording" signal. Contrast: checking only `recordings[0]` would make the badge flicker between renders depending on ORM/DB ordering, eroding trust in the indicator.

### D4. Verified shown for published only (not review)

Explicit per user clarification: the badge marks `visibility_status === "published"` songs exclusively. Review songs appear in Browse (per Step 1) but **without** the badge — that contrast is precisely the trust signal users requested. No "Review" badge is added (out of scope; negative states would dilute the "Verified" signal).

### D5. Badge placement: title-adjacent icon, not meta-row Badge

**Chosen:** `BadgeCheck` icon immediately after the title text inside the `<h4>`.

**Rationale:** "Verified" is a property of the song/recording, not a secondary metadata attribute like key/BPM/duration. The title row is the primary visual anchor and the established location for "verified account" affordances. The meta row's `outline` Badge is reserved for piece of metadata (the musical key). Sizing the icon at `size-3.5` with `shrink-0` ensures the badge stays visible even when the title truncates.

### D6. BPM rounding scope: display only, no data mutation

`Math.round()` is applied at render time in `SongCard` and `TransitionPanel`. The underlying `recordings.tempo_bpm` column and the API response continue to carry the float — useful for any downstream audio-processing logic that benefits from sub-BPM precision. `TransitionControls` already rounds its computed current BPM and is left untouched. The `bpmDelta` rounding mismatch at `TransitionControls.tsx:65` (rounded product minus raw ref) is out of scope — only the display rounding is in scope.

### D7. Album filter consistency

`getAlbums` is updated to filter albums to those with at least one browse-visible (published-or-review) recording, matching the relaxed list filter. This prevents an album appearing in the dropdown whose songs then get filtered out of the list. The filter is hardcoded to `('published', 'review')` rather than parametrized because `getAlbums` has no caller-supplied filter today — YAGNI until an admin caller needs otherwise.

## Verification

### Automated

```bash
pnpm --filter sow-webapp lint
pnpm --filter sow-webapp test
pnpm --filter sow-webapp build
```

Existing tests to watch for regressions:
- `src/test/api/songs/*.test.ts` — assert that the default (no `visibilityStatus` query param) now returns both `published` and `review` recordings. Add a test fixture with a `review`-status recording and assert it appears in the list. Mirror for `/api/songs/search` and `/api/songs/search/semantic`.
- `src/test/api/songs/albums.test.ts` (if present) — assert an album with only `draft` recordings does not appear; one with a `review` recording does.
- `src/test/components/songset/SongCard.test.tsx` — add assertions:
  - When `song.recordings[0].visibilityStatus === "published"`: `data-testid="verified-badge"` is present.
  - When `song.recordings[0].visibilityStatus === "review"`: badge is absent.
  - When `song.recordings` is empty: badge is absent (no crash).
  - BPM displays as the nearest integer (e.g. `127.97` -> `128 BPM`).

### Manual (dev)

1. `pnpm --filter sow-webapp dev`
2. Open `/songsets/<id>` editor -> click "Add songs".
3. Confirm `review`-status songs now appear alongside `published` ones.
4. Confirm only the published songs display the green `BadgeCheck` icon next to the title; review songs do not.
5. Confirm BPM shows as an integer (no fractional digits) in the card meta row.
6. Open the transition sheet for a songset item and confirm the "From/To" song info header BPM is an integer.
7. Switch to the "Describe" (semantic) tab; repeat the visibility / badge / BPM checks on semantic-search results.
8. Open the album dropdown; confirm only albums with at least one published/review song appear; pick one and confirm the filtered list aligns (no empty result for a chosen album).

## Out of Scope

- "Review" badge or any UI affordance distinguishing review songs visually beyond the absence of the Verified badge.
- `TransitionControls` `bpmDelta` rounding logic fix (display-only round here).
- Filtering browse by LRC lyric presence (e.g. `r2_lrc_url IS NOT NULL`). User confirmed the visibility relaxation alone surfaces LRC-ready candidates; no additional lyrics filter.
- Threading a client-supplied visibility filter into `semanticSearchSongs` (it remains default-only). The semantic route does not accept a `visibilityStatus` body field.
- Mobile / Android client changes — the Android app consumes the same JSON APIs and will receive the relaxed set automatically. No badge rendering on Android is in scope here.
- Migration / DB-level enum constraint on `visibility_status` (it remains a free-form `text` column governed by application convention).

## Risk Assessment

**Low:**
- BPM rounding in `SongCard`/`TransitionPanel` — pure display change, single-line edits, no data flow impact.
- Verified badge in `SongCard` — additive UI, data already flows through. Worst case: a `review` song briefly shows the badge if predicate logic is wrong — covered by tests.

**Medium:**
- DB-layer array signature change — touches `ListSongsFilters` which is imported by the API routes. Existing callers passing a string are unaffected (union type). Risk: `inArray` import missing in `search.ts` callback destructure — easily caught by `pnpm build` / lint.
- `semanticSearchSongs` SQL change — raw SQL parameterization is the risky part; the `sql.join` pattern is already used at `songs.ts:436` for `findTopMatchingLines`, so the precedent exists. Test by running a semantic search against a dev DB with at least one `review` song and confirming it returns.
- `getAlbums` SQL change — verify the EXISTS subquery renders correctly with `pnpm dev` + manual dropdown check.

**Open question for the user (low priority):**
The `search.ts` and `songs.ts` recording-join filters currently apply **both** a song-level `EXISTS` clause and a recording-level `eq(visibilityStatus, ...)` filter. The `EXISTS` (song-level) determines whether the song appears at all; the recording-join filter determines which of the song's recordings are populated in the result. After relaxation, the song-level EXISTS should accept `[published, review]` (song has at least one browseable recording) AND the recording-join should return **all browseable recordings** (both published and review) — which is exactly what the array branch in both spots does. Confirm there is no expectation that the recording join returns *only* published recordings even when the song satisfies the EXISTS via a review recording. I believe returning both is correct (the Verified badge's `.some()` predicate handles mixed-status correctly, and exposing the review recording to the client is fine since it's already authenticated as a songset editor). Flagging only for transparency.
