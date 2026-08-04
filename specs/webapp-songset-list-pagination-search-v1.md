# Webapp Songset List: Pagination + Search v1

## Summary

The `/songsets` page currently fetches only the first 50 songsets (hardcoded `limit=50, offset=0`) with no pagination controls and no search. As users create more songsets, earlier ones disappear from the list with no way to reach them. This spec adds:

1. **Server-side pagination** — page-numbered navigation (20 per page) with Prev/Next and page numbers
2. **Server-side search** — filter songsets by name and description via API query parameter

The backend (DB layer + API route) already supports `limit`/`offset` and returns `total` — the frontend simply never uses them beyond the first page. The DB layer needs a new `search` parameter added.

## User Decisions

| Decision | Choice |
|----------|--------|
| Search scope | Server-side (API query param, DB filters) |
| Pagination style | Page numbers (1, 2, 3 ... N) |
| Page size | 20 per page |
| Search fields | Name + description (`ilike`) |

## Current State

### Data flow

```
page.tsx (SSR, hardcoded limit=50 offset=0)
  → listSongsetSummaries(userId, 50, 0)     [src/lib/db/songsets.ts:337]
  → SongsetsClient (initialData)             [src/app/songsets/SongsetsClient.tsx:59]
    → SongsetList (renders rows)             [src/components/songset/SongsetList.tsx:54]
```

On client-side refresh (after create/rename/delete/duplicate), `SongsetsClient` fetches `/api/songsets` with **no query params** — the API defaults to `limit=50, offset=0` (`src/app/api/songsets/route.ts:22-25`).

### What already exists

- `listSongsetSummaries(userId, limit, offset)` — accepts limit/offset, returns `{ songsets, total }` (`src/lib/db/songsets.ts:337-410`)
- API GET handler parses `limit` (default 50, max 100) and `offset` (default 0) from search params (`src/app/api/songsets/route.ts:21-25`)
- Count query returns `total` (`src/lib/db/songsets.ts:375-378`)
- Android app already has pagination via `limit`/`offset` params (`delivery/android/.../SongsetsApi.kt`)
- `SongSearch` component (`src/components/songset/SongSearch.tsx`) — reusable search input pattern with clear button, loading spinner, aria labels

### What's missing

- DB layer: no `search` parameter in `listSongsetSummaries`
- API route: no `search` query param parsing
- Server page: no URL search param reading, hardcoded `limit=50, offset=0`
- Client component: no pagination state, no search state, no URL sync, refresh fetches without params
- SongsetList component: no search bar, no pagination controls
- No `ilike`/`or` imports from drizzle-orm in `src/lib/db/songsets.ts` (currently imports `eq, and, desc, gt, sql, asc`)

---

## Implementation Plan

### Phase 1: DB Layer — Add search filter

**File:** `delivery/webapp/src/lib/db/songsets.ts`

#### 1a. Add imports

Add `ilike`, `or` to the drizzle-orm import on line 11:

```typescript
import { eq, and, desc, gt, sql, asc, ilike, or } from "drizzle-orm";
```

#### 1b. Modify `listSongsetSummaries` signature (line 337)

Add optional `search` parameter:

```typescript
export async function listSongsetSummaries(
  userId: number,
  limit = 50,
  offset = 0,
  search?: string
): Promise<{ songsets: SongsetListItem[]; total: number }> {
```

#### 1c. Build the where condition

Inside the function (after `timePageLoad` opens), construct a reusable where condition:

```typescript
const trimmedSearch = search?.trim();
const whereCondition = trimmedSearch
  ? and(
      eq(songsets.userId, userId),
      or(
        ilike(songsets.name, `%${trimmedSearch}%`),
        ilike(songsets.description, `%${trimmedSearch}%`)
      )
    )
  : eq(songsets.userId, userId);
```

#### 1d. Apply to data query (line 366)

Replace `.where(eq(songsets.userId, userId))` with `.where(whereCondition)`.

#### 1e. Apply to count query (line 378)

Replace `.where(eq(songsets.userId, userId))` with `.where(whereCondition)`.

#### Security note

The `ilike` pattern uses parameterized queries via Drizzle — the `%${trimmedSearch}%` interpolation is safe because Drizzle treats it as a bound parameter, not raw SQL. No additional escaping needed.

---

### Phase 2: API Route — Parse search param

**File:** `delivery/webapp/src/app/api/songsets/route.ts`

#### 2a. Parse `search` from query string (after line 25)

Add parsing of the `search` query parameter:

```typescript
const search = searchParams.get("search")?.trim() || undefined;
```

#### 2b. Pass to DB function (line 27)

```typescript
const result = await listSongsetSummaries(Number(session.user.id), limit, offset, search);
```

No other changes to the API route — `limit` and `offset` are already parsed.

---

### Phase 3: Server Page — URL-driven SSR

**File:** `delivery/webapp/src/app/songsets/page.tsx`

#### 3a. Accept `searchParams` prop

Next.js 16 pages receive `searchParams` as a `Promise`. Update the page signature:

```typescript
export default async function SongsetsPage({
  searchParams,
}: {
  searchParams: Promise<{ page?: string; search?: string }>;
}) {
```

#### 3b. Parse page + search from URL

```typescript
const params = await searchParams;
const page = Math.max(1, parseInt(params.page ?? "1") || 1);
const search = params.search?.trim() || undefined;
const pageSize = 20;
const offset = (page - 1) * pageSize;
```

#### 3c. Pass to DB function

```typescript
const result = await listSongsetSummaries(
  Number(session.user.id),
  pageSize,
  offset,
  search
);
```

#### 3d. Pass new props to `SongsetsClient`

```typescript
return (
  <SongsetsClient
    initialData={{
      total: result.total,
      songsets: result.songsets.map((songset) => ({
        ...songset,
        createdAt: songset.createdAt.toISOString(),
        updatedAt: songset.updatedAt.toISOString(),
        failedAt: songset.failedAt?.toISOString() ?? null,
      })),
    }}
    currentPage={page}
    pageSize={pageSize}
    initialSearch={search ?? ""}
  />
);
```

---

### Phase 4: Client Component — Pagination + search state

**File:** `delivery/webapp/src/app/songsets/SongsetsClient.tsx`

#### 4a. Update props interface

```typescript
interface SongsetsClientProps {
  initialData: ApiResponse;
  currentPage: number;
  pageSize: number;
  initialSearch: string;
}
```

#### 4b. Add state

```typescript
const [page, setPage] = useState(currentPage);
const [search, setSearch] = useState(initialSearch);
const [debouncedSearch, setDebouncedSearch] = useState(initialSearch);
```

#### 4c. Debounce search (300ms)

Add a `useEffect` that debounces the search input:

```typescript
useEffect(() => {
  const timer = setTimeout(() => {
    setDebouncedSearch(search);
    setPage(1); // Reset to first page on new search
  }, 300);
  return () => clearTimeout(timer);
}, [search]);
```

#### 4d. Replace refresh effect with paginated fetch

Replace the existing `refreshKey`-based `useEffect` (lines 70-109) with a fetch that depends on `page`, `debouncedSearch`, and `refreshKey`:

```typescript
useEffect(() => {
  let cancelled = false;

  async function loadSongsets() {
    try {
      setIsLoading(true);
      setError(null);

      const offset = (page - 1) * pageSize;
      const params = new URLSearchParams({
        limit: String(pageSize),
        offset: String(offset),
      });
      if (debouncedSearch.trim()) {
        params.set("search", debouncedSearch.trim());
      }

      const response = await fetch(`/api/songsets?${params}`);
      if (!response.ok) {
        if (response.status === 401) {
          throw new Error("Please sign in to view your songsets");
        }
        throw new Error("Failed to load songsets");
      }

      const data: ApiResponse = await response.json();
      if (cancelled) return;

      setSongsets(transformSongsets(data.songsets));
    } catch (err) {
      if (!cancelled) {
        setError(err instanceof Error ? err.message : "Failed to load songsets");
      }
    } finally {
      if (!cancelled) {
        setIsLoading(false);
      }
    }
  }

  loadSongsets();

  return () => {
    cancelled = true;
  };
}, [page, debouncedSearch, pageSize, refreshKey]);
```

#### 4e. URL sync

Add a `useEffect` that syncs `page` and `debouncedSearch` to the URL using `router.replace` (shallow, no full navigation):

```typescript
useEffect(() => {
  const params = new URLSearchParams();
  if (page > 1) params.set("page", String(page));
  if (debouncedSearch.trim()) params.set("search", debouncedSearch.trim());
  const qs = params.toString();
  router.replace(qs ? `/songsets?${qs}` : "/songsets");
}, [page, debouncedSearch, router]);
```

#### 4f. Page change handler

```typescript
const handlePageChange = useCallback((newPage: number) => {
  setPage(newPage);
  window.scrollTo({ top: 0, behavior: "smooth" });
}, []);
```

#### 4g. Search change handler

```typescript
const handleSearchChange = useCallback((value: string) => {
  setSearch(value);
}, []);
```

#### 4h. Pass props to SongsetList

```typescript
<SongsetList
  songsets={songsets}
  isLoading={isLoading}
  error={error}
  onCreateSongset={handleCreateSongset}
  onRender={handleRender}
  onPlay={handlePlay}
  onRetry={handleRetry}
  onRename={handleRename}
  onDuplicate={handleDuplicate}
  onShare={handleShare}
  onDownloadAudio={handleDownloadAudio}
  onDownloadVideo={handleDownloadVideo}
  onDelete={handleDelete}
  currentPage={page}
  totalPages={Math.max(1, Math.ceil(total / pageSize))}
  onPageChange={handlePageChange}
  search={search}
  onSearchChange={handleSearchChange}
  isSearching={isLoading && search !== debouncedSearch}
/>
```

Note: `total` needs to be tracked in state. Add:

```typescript
const [total, setTotal] = useState(initialData.total);
```

And update it in the fetch effect: `setTotal(data.total);`

#### 4i. Keep `refreshSongsets` working

The existing `refreshSongsets` callback (line 111-113) stays the same — it bumps `refreshKey`, which triggers the fetch effect. After mutations (create/rename/delete/duplicate), the current page is refetched.

---

### Phase 5: SongsetList Component — Search bar + pagination UI

**File:** `delivery/webapp/src/components/songset/SongsetList.tsx`

#### 5a. Add new props

```typescript
interface SongsetListProps {
  // ... existing props ...
  currentPage: number;
  totalPages: number;
  onPageChange: (page: number) => void;
  search: string;
  onSearchChange: (value: string) => void;
  isSearching?: boolean;
}
```

#### 5b. Add imports

```typescript
import { Search, X, ChevronLeft, ChevronRight, Loader2 } from "lucide-react";
```

(`Plus` and `Loader2` already imported; add `Search`, `X`, `ChevronLeft`, `ChevronRight`)

#### 5c. Search bar

Render above the songset list (inside the main return, before the `space-y-3` div). Follow the existing `SongSearch` component pattern (`src/components/songset/SongSearch.tsx:111-168`):

```tsx
<div className="relative mb-4">
  <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground pointer-events-none" />
  <Input
    type="text"
    value={search}
    onChange={(e) => onSearchChange(e.target.value)}
    placeholder="Search songsets by name or description..."
    className="pl-9 pr-10"
    aria-label="Search songsets"
    data-testid="songset-search-input"
  />
  {search.length > 0 && (
    <Button
      variant="ghost"
      size="icon-sm"
      className="absolute right-2 top-1/2 -translate-y-1/2"
      onClick={() => onSearchChange("")}
      aria-label="Clear search"
      data-testid="songset-clear-search-button"
    >
      <X className="size-4" />
    </Button>
  )}
  {isSearching && (
    <Loader2
      className="absolute right-3 top-1/2 -translate-y-1/2 size-4 animate-spin text-muted-foreground"
      aria-hidden="true"
    />
  )}
  {isSearching && (
    <span className="sr-only" role="status" aria-live="polite">Searching songsets...</span>
  )}
</div>
```

#### 5d. Pagination controls

Render below the songset list (after the `space-y-3` div, before the FAB). Only show when `totalPages > 1`:

```tsx
{totalPages > 1 && (
  <nav aria-label="Songset pagination" className="flex items-center justify-center gap-2 mt-6">
    <Button
      variant="outline"
      size="sm"
      onClick={() => onPageChange(currentPage - 1)}
      disabled={currentPage <= 1}
      aria-label="Previous page"
      data-testid="pagination-prev"
    >
      <ChevronLeft className="size-4" />
      Prev
    </Button>

    {pageNumbers.map((pageNum) => (
      <Button
        key={pageNum}
        variant={pageNum === currentPage ? "default" : "outline"}
        size="icon-sm"
        onClick={() => onPageChange(pageNum)}
        aria-current={pageNum === currentPage ? "page" : undefined}
        aria-label={`Page ${pageNum}`}
        data-testid={`pagination-page-${pageNum}`}
      >
        {pageNum}
      </Button>
    ))}

    <Button
      variant="outline"
      size="sm"
      onClick={() => onPageChange(currentPage + 1)}
      disabled={currentPage >= totalPages}
      aria-label="Next page"
      data-testid="pagination-next"
    >
      Next
      <ChevronRight className="size-4" />
    </Button>
  </nav>
)}
```

#### 5e. Page number calculation

Compute the page numbers to display (show up to 5 pages around the current page with ellipsis):

```typescript
const pageNumbers = useMemo(() => {
  const maxVisible = 5;
  if (totalPages <= maxVisible) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }

  const half = Math.floor(maxVisible / 2);
  let start = Math.max(1, currentPage - half);
  const end = Math.min(totalPages, start + maxVisible - 1);

  if (end - start + 1 < maxVisible) {
    start = Math.max(1, end - maxVisible + 1);
  }

  return Array.from({ length: end - start + 1 }, (_, i) => start + i);
}, [currentPage, totalPages]);
```

Add `useMemo` to the React import on line 3:

```typescript
import { useState, useCallback, useMemo } from "react";
```

#### 5f. Empty state with search

Update the empty state (line 168-238) to differentiate between "no songsets at all" and "no search results":

```tsx
if (songsets.length === 0) {
  const isSearchActive = search.trim().length > 0;
  return (
    <div className={cn("text-center py-12", className)}>
      {/* Search bar still visible when search yields no results */}
      <div className="relative mb-4 max-w-md mx-auto">
        {/* ... search input as above ... */}
      </div>
      <p className="text-muted-foreground mb-4">
        {isSearchActive
          ? "No songsets match your search."
          : "No songsets yet. Create one to get started."}
      </p>
      {!isSearchActive && (
        <Button onClick={() => setIsCreateDialogOpen(true)}>
          <Plus className="size-4 mr-2" />
          Create Songset
        </Button>
      )}
      {/* Create Dialog (existing) */}
    </div>
  );
}
```

#### 5g. Layout structure (final)

```
┌─────────────────────────────────┐
│  [🔍 Search songsets...     ✕]  │  ← search bar (always visible)
├─────────────────────────────────┤
│  [Songset Row 1]                │
│  [Songset Row 2]                │
│  ...                            │
│  [Songset Row 20]               │
├─────────────────────────────────┤
│  [< Prev] [1] [2] [3] [Next >]  │  ← pagination (only if totalPages > 1)
└─────────────────────────────────┘
                           [+] ← FAB (existing)
```

---

### Phase 6: Tests

#### 6a. `delivery/webapp/src/test/components/songset/SongsetList.test.tsx`

Add test suites:

**Search bar tests:**
- Renders search input with `aria-label="Search songsets"`
- Calls `onSearchChange` when typing in search input
- Shows clear button when search has text, hides when empty
- Calls `onSearchChange("")` when clear button clicked
- Shows loading spinner when `isSearching` is true
- Renders sr-only "Searching songsets..." status when searching

**Pagination tests:**
- Does not render pagination when `totalPages <= 1`
- Renders pagination nav with `aria-label="Songset pagination"` when `totalPages > 1`
- Renders correct page number buttons
- Marks current page with `aria-current="page"`
- Calls `onPageChange(currentPage - 1)` when Prev clicked
- Calls `onPageChange(currentPage + 1)` when Next clicked
- Disables Prev button on first page
- Disables Next button on last page
- Shows ellipsis / limited page numbers when `totalPages > 5`

**Empty state with search:**
- Shows "No songsets match your search." when `search` is non-empty and list is empty
- Shows "No songsets yet." when `search` is empty and list is empty
- Hides create button when search is active and no results

#### 6b. `delivery/webapp/src/test/accessibility/accessibility.test.tsx`

Add to the existing `SongsetList` describe block:
- Search input has `aria-label="Search songsets"`
- Clear search button has `aria-label="Clear search"`
- Pagination nav has `aria-label="Songset pagination"`
- Page buttons have `aria-current="page"` when active
- Prev/Next buttons have descriptive `aria-label`s

#### 6c. DB layer tests (if test file exists for `src/lib/db/songsets.ts`)

- `listSongsetSummaries` with `search` param filters by name (ilike, case-insensitive)
- `listSongsetSummaries` with `search` param filters by description (ilike, case-insensitive)
- `listSongsetSummaries` with `search` param matches either name OR description
- `listSongsetSummaries` without `search` returns all user songsets (no filter)
- `listSongsetSummaries` with empty string `search` behaves same as no search
- `listSongsetSummaries` count query respects search filter

#### 6d. API route tests (if test file exists for `src/app/api/songsets/route.ts`)

- GET with `?search=foo` passes search to `listSongsetSummaries`
- GET without `search` passes `undefined` to `listSongsetSummaries`
- GET with `?search=` (empty) passes `undefined`
- GET with `?page=2&limit=20` computes correct offset

---

## Files Changed (Summary)

| File | Change |
|------|--------|
| `delivery/webapp/src/lib/db/songsets.ts` | Add `search` param to `listSongsetSummaries`, add `ilike`/`or` imports |
| `delivery/webapp/src/app/api/songsets/route.ts` | Parse `search` query param, pass to DB function |
| `delivery/webapp/src/app/songsets/page.tsx` | Accept URL `searchParams`, pass page/search/pageSize to client |
| `delivery/webapp/src/app/songsets/SongsetsClient.tsx` | Pagination + search state, debounced fetch, URL sync, pass props to SongsetList |
| `delivery/webapp/src/components/songset/SongsetList.tsx` | Search bar + pagination controls, new props, empty state with search |
| `delivery/webapp/src/test/components/songset/SongsetList.test.tsx` | New tests for search + pagination |
| `delivery/webapp/src/test/accessibility/accessibility.test.tsx` | New aria-label tests |

## Out of Scope

- Android app (already has pagination via `limit`/`offset`)
- Infinite scroll / load-more variants
- Sorting (currently `ORDER BY updatedAt DESC`)
- Search by render state, date range, or other metadata
- Server-side search index (Postgres `ilike` is sufficient for expected scale)
- Debounce library (using native `setTimeout` — consistent with codebase patterns)
