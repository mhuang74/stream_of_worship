# Fix Favorites Page: Empty-Favorites Filter & Pagination Spinner — v2

## Summary

Two bugs in the `/favorites` page (`delivery/webapp`):

1. **All songs show as favorites.** The logged-in user has **0 favorites** in `user_favorite_songs`, yet the page lists the entire catalog (438 songs) with every heart filled.
2. **Pagination beyond page 1 is stuck on a spinner** (reproduced 40s+; the network completes in 1–2s, so it is a client-side loading-state bug, not server slowness).

## Root Cause Analysis

### Bug 1 — Empty favorites ignored (`favoritesOnly` filter is a no-op)

The filter is dropped at **two** layers:

**Layer A — DB predicate (`favorites.ts:54`):** `favoritesOnlyPredicate()` returns `undefined` when `favoriteSongIds` is empty:
```ts
if (!favoriteSongIds || favoriteSongIds.length === 0) return undefined;
```
In `listSongs()` (`songs.ts:302`) the favorites clause becomes `undefined`, so `listWhereClause = whereClause` — the filter is dropped and **all** songs are returned.

**Layer B — API route gate (`route.ts:78` and `search/route.ts:57`):** Both API routes suppress `favoriteSongIds` when the array is empty:
```ts
// /api/songs — route.ts:78
if (favoriteSongIds.length > 0) {
  filters.favoriteSongIds = favoriteSongIds;  // never set when 0 favorites
}

// /api/songs/search — search/route.ts:57
...(favoriteSongIds.length > 0 ? { favoriteSongIds } : {}),
```
When a user has 0 favorites, `filters.favoriteSongIds` is never set (i.e., `undefined`). Even if Layer A is fixed, the predicate receives `undefined`, hits the first branch, and returns **no filter** — all songs are returned.

**Impact matrix:**

| Path | Layer A fix alone | Layer A + Layer B fix |
|------|-------------------|-----------------------|
| SSR `/favorites` page (`page.tsx:25-28`) | Fixed (passes `[]` explicitly) | Fixed |
| Client fetch (`FavoritesClient` → `/api/songs?favoritesOnly=1`) | **Not fixed** (gate suppresses `[]`) | Fixed |
| Favorites-only search (`/api/songs/search?favoritesOnly=1`) | **Not fixed** (gate suppresses `[]`) | Fixed |

`FavoritesClient.tsx:156` hardcodes `isFavorite` on every card, so all 438 appear as favorites instead of the intended "No favorites yet" empty state (`FavoritesClient.tsx:119`).

### Bug 2 — Pagination spinner stuck (client race condition)

`FavoritesClient.tsx:42-75` fetch effect depends on `[page, pageSize, currentPage, initialSongs.length]` and has an SSR-skip early-return.

Clicking a page does **two** things: `setPage(2)` (starts a client fetch) **and** `router.replace('/favorites?page=2')` (line 78), which triggers an RSC navigation that changes the `currentPage` prop.

Because `currentPage` is in the effect deps, the effect re-runs → its cleanup sets `cancelled=true` → the in-flight fetch's `finally` (`setIsLoading(false)`) is skipped → the new effect early-returns (`page === currentPage`) **without** resetting `isLoading`.

Result: `isLoading` stuck `true` → infinite spinner, list never renders.

The **working** `SongsetsClient` fetch effect (`SongsetsClient.tsx:82-130`) deps are `[page, committedSearch, pageSize, refreshKey]` — no `currentPage`, no SSR-skip guard — which is why songset pagination works. Favorites diverged from this pattern.

---

## Fix Plan

### Phase 1 — DB layer: empty favorites must yield zero results

**File:** `delivery/webapp/src/lib/db/favorites.ts`

Modify `favoritesOnlyPredicate` so an empty favorite list means "match nothing" instead of "no filter":

```ts
export function favoritesOnlyPredicate(
  favoriteSongIds: string[] | undefined
): SQL | undefined {
  if (!favoriteSongIds) return undefined; // context not loaded → no-op
  if (favoriteSongIds.length === 0) return sql`false`; // favorites-only, no favorites → match nothing
  return sql`${songs.id} = ANY(${favoriteSongIds})`;
}
```

Effect: When `favoriteSongIds` is `[]` and `favoritesOnly` is true, the query returns `{ songs: [], total: 0 }` → `FavoritesClient` renders the existing "No favorites yet" empty state.

### Phase 2 — API routes: remove the `length > 0` gate so the fix propagates

Phase 1 alone does **not** fix client-side fetches or search — both API routes suppress `favoriteSongIds` when the array is empty (see Root Cause Layer B). Remove the gate so the empty array reaches the DB layer.

**File:** `delivery/webapp/src/app/api/songs/route.ts`

Replace the conditional (lines 78-80):
```ts
// Before
if (favoriteSongIds.length > 0) {
  filters.favoriteSongIds = favoriteSongIds;
}

// After
filters.favoriteSongIds = favoriteSongIds;
```

**File:** `delivery/webapp/src/app/api/songs/search/route.ts`

Replace the conditional spread (line 57):
```ts
// Before
...(favoriteSongIds.length > 0 ? { favoriteSongIds } : {}),

// After
favoriteSongIds,
```

**No regression for non-favorites-only requests:** `favoritesFirstOrder([])` already returns `undefined` (no-op), so unconditionally passing an empty array does not alter ordering or filtering when `favoritesOnly` is false.

### Phase 3 — Client: fix stuck spinner, handle back/forward, fall back on error

**File:** `delivery/webapp/src/app/favorites/FavoritesClient.tsx`

Three changes to the fetch effect and error handling:

#### 3a. Mount-once guard + deps scoped to `[page, pageSize]`

Replace the fetch effect (lines 42–75) with a mount-once guard + deps scoped to `[page, pageSize]`:

```tsx
const skipInitialFetchRef = useRef(true);

useEffect(() => {
  if (skipInitialFetchRef.current) {
    skipInitialFetchRef.current = false;
    return; // don't refetch SSR page 1 on mount
  }
  let cancelled = false;
  async function loadPage() {
    setIsLoading(true);
    try {
      const offset = (page - 1) * pageSize;
      const params = new URLSearchParams({
        limit: String(pageSize),
        offset: String(offset),
        favoritesOnly: "1",
        visibilityStatus: "published,review",
      });
      const res = await fetch(`/api/songs?${params.toString()}`);
      if (!res.ok) throw new Error("Failed to load favorites");
      const data = await res.json();
      if (cancelled) return;
      setSongs(toSongCardData(data.songs));
      setTotal(data.total);
    } catch {
      if (!cancelled) {
        toast.error("Failed to load favorites");
        setSongs(initialSongs);   // fall back to SSR-provided data
        setTotal(initialTotal);
      }
    } finally {
      if (!cancelled) setIsLoading(false);
    }
  }
  loadPage();
  return () => { cancelled = true; };
}, [page, pageSize]);
```

- Add `useRef` to the React import (line 3).
- Why it works: RSC navigation changes `currentPage`/`initialSongs`, but neither is in the deps, so the effect no longer re-runs mid-flight and never cancels the in-flight fetch. `isLoading` always clears in `finally`.

#### 3b. Reconcile `page` state on RSC navigation (back/forward)

Add a separate effect that syncs `page` to `currentPage` when they diverge after a browser back/forward navigation (RSC restores `currentPage` prop, but client `page` state may be stale):

```tsx
useEffect(() => {
  if (page !== currentPage) {
    setPage(currentPage);
  }
}, [currentPage]);
```

This handles the case where the user clicks page 2, then hits the browser back button — RSC restores `currentPage=1`, this effect sets `page=1`, and the fetch effect (3a) re-runs to load page 1. Because `skipInitialFetchRef` is already `false` after mount, the fetch proceeds normally.

#### 3c. Keep existing effects unchanged

- Keep the URL-sync effect (78–83).
- Keep the fallback-to-page-1 effect (97–102).
- Keep the empty-state render (119–134) unchanged.

---

## Tests

### DB layer

**`delivery/webapp/src/test/lib/db/songs.test.ts`**

Add two tests:

1. **Empty favorites → empty result (explicit `[]`):**
   ```ts
   it("returns zero results when favoritesOnly is set and favoriteSongIds is empty", async () => {
     // ...mock setup...
     await listSongs(50, 0, { favoriteSongIds: [], favoritesOnly: true });
     const findManyArgs = vi.mocked(db.query.songs.findMany).mock.calls[0][0];
     const query = dialect.sqlToQuery(findManyArgs.where);
     expect(query.sql).toContain("false");
   });
   ```

2. **Undefined favoriteSongIds + favoritesOnly → still returns all songs (documents the no-op contract):**
   ```ts
   it("does not restrict when favoritesOnly is set but favoriteSongIds is undefined", async () => {
     // ...mock setup...
     await listSongs(50, 0, { favoritesOnly: true });
     const findManyArgs = vi.mocked(db.query.songs.findMany).mock.calls[0][0];
     const query = dialect.sqlToQuery(findManyArgs.where);
     expect(query.sql).not.toContain("false");
     expect(query.sql).not.toContain("ANY");
   });
   ```
   This test documents why Phase 2 (API route gate removal) is necessary — the DB layer alone cannot fix the API path.

### API route

**`delivery/webapp/src/test/api/songs/route.test.ts`**

Add a test asserting that 0 favorites + `favoritesOnly=1` passes `favoriteSongIds: []` to `listSongs` (not `undefined`):

```ts
it("passes empty favoriteSongIds to listSongs when favoritesOnly=1 and user has 0 favorites", async () => {
  vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
  vi.mocked(getFavoriteSongIds).mockResolvedValue([]);
  vi.mocked(listSongs).mockResolvedValue({ songs: [], total: 0 });

  const request = createMockRequest(
    "http://localhost:3000/api/songs?favoritesOnly=1"
  );
  await GET(request);

  expect(listSongs).toHaveBeenCalledWith(
    50,
    0,
    expect.objectContaining({
      favoriteSongIds: [],
      favoritesOnly: true,
    })
  );
});
```

### Client component

**`delivery/webapp/src/test/app/favorites/FavoritesClient.test.tsx`**

Add/update tests:

1. **Empty favorites renders "No favorites yet":**
   ```tsx
   it("renders empty state when initialSongs is empty", () => {
     render(
       <FavoritesClient
         initialSongs={[]}
         initialTotal={0}
         currentPage={1}
         pageSize={20}
       />
     );
     expect(screen.getByText("No favorites yet")).toBeInTheDocument();
   });
   ```

2. **Page change renders fetched songs (not stuck spinner):**
   ```tsx
   it("renders fetched songs after clicking page 2 and clears isLoading", async () => {
     const fetchMock = vi.fn().mockResolvedValue({
       ok: true,
       json: async () => ({ songs: makeSongs(20), total: 45 }),
     });
     vi.stubGlobal("fetch", fetchMock);

     render(
       <FavoritesClient
         initialSongs={makeSongs(20)}
         initialTotal={45}
         currentPage={1}
         pageSize={20}
       />
     );

     fireEvent.click(screen.getByTestId("pagination-page-2"));

     await waitFor(() => {
       expect(screen.getByTestId("favorites-list")).toBeInTheDocument();
     });
     // Spinner should be gone
     expect(screen.queryByRole("status")).not.toBeInTheDocument();

     vi.unstubAllGlobals();
   });
   ```

3. **Repeated page clicks don't leave the loader stuck:**
   ```tsx
   it("does not leave the loader stuck after rapid page changes", async () => {
     const fetchMock = vi.fn().mockResolvedValue({
       ok: true,
       json: async () => ({ songs: makeSongs(20), total: 45 }),
     });
     vi.stubGlobal("fetch", fetchMock);

     render(
       <FavoritesClient
         initialSongs={makeSongs(20)}
         initialTotal={45}
         currentPage={1}
         pageSize={20}
       />
     );

     fireEvent.click(screen.getByTestId("pagination-page-2"));
     fireEvent.click(screen.getByTestId("pagination-page-3"));

     await waitFor(() => {
       expect(screen.getByTestId("favorites-list")).toBeInTheDocument();
     });

     vi.unstubAllGlobals();
   });
   ```

4. **Fetch failure falls back to SSR data:**
   ```tsx
   it("falls back to initialSongs when client fetch fails", async () => {
     const fetchMock = vi.fn().mockRejectedValue(new Error("Network error"));
     vi.stubGlobal("fetch", fetchMock);

     render(
       <FavoritesClient
         initialSongs={makeSongs(20)}
         initialTotal={45}
         currentPage={1}
         pageSize={20}
       />
     );

     fireEvent.click(screen.getByTestId("pagination-page-2"));

     await waitFor(() => {
       expect(screen.getByText("Song 1")).toBeInTheDocument();
     });

     vi.unstubAllGlobals();
   });
   ```

## Files Changed

| File | Change |
|------|--------|
| `delivery/webapp/src/lib/db/favorites.ts` | `favoritesOnlyPredicate` returns `sql`false`` for empty array |
| `delivery/webapp/src/app/api/songs/route.ts` | Remove `length > 0` gate; always set `filters.favoriteSongIds` |
| `delivery/webapp/src/app/api/songs/search/route.ts` | Remove `length > 0` conditional spread; always pass `favoriteSongIds` |
| `delivery/webapp/src/app/favorites/FavoritesClient.tsx` | Fetch effect deps → `[page, pageSize]` + mount-once ref guard + error fallback to `initialSongs` + `currentPage` reconciliation effect |
| `delivery/webapp/src/test/lib/db/songs.test.ts` | Empty-favorites filter test + undefined-favoriteSongIds contract test |
| `delivery/webapp/src/test/api/songs/route.test.ts` | 0-favorites + favoritesOnly=1 passes `favoriteSongIds: []` to listSongs |
| `delivery/webapp/src/test/app/favorites/FavoritesClient.test.tsx` | Empty-state + page-change-renders + rapid-clicks + fetch-failure-fallback tests |

## Verification (manual, Chrome DevTools)

1. Log in as the user with 0 favorites → `/favorites` shows "No favorites yet" (not 438 songs).
2. To test pagination, seed `>20` rows into `user_favorite_songs` (dev DB only — favoriting in-app requires the ≥90% hearing gate / ADR-0002, so direct seeding is the practical path). Then:
   - Click page 2/3 → songs render immediately, no spinner.
   - Click page 2, then browser back → page 1 renders correctly.
   - Throttle network to "Slow 3G" in DevTools, click page 2 → spinner shows during fetch, then songs render.
3. `pnpm test`, `pnpm lint`, `pnpm build` in `delivery/webapp`.

## Out of Scope

- Changing the ≥90% favoriting gate / ADR-0002.
- The count discrepancy (483 vs 438) — 438 is just songs with `published`/`review` recordings; unrelated to the filter bug.
- Other paginated pages (songsets already follow the correct pattern).
- **Double-fetch per page navigation** (client fetch + RSC navigation both hit `listSongs`): inherited pattern shared with `SongsetsClient`. File as a follow-up issue; fixing requires a larger refactor (e.g., remove client fetch entirely and rely on RSC, or vice versa).
