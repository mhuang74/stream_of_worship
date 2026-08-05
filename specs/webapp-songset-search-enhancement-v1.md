# Webapp Songset List: Search Enhancement v1

## Summary

The `/songsets` page search feature has two issues:

1. **Search bar shrinks on no results** — When `songsets.length === 0`, the `renderSearchBar` helper is called with `"mb-4 max-w-md mx-auto"`, constraining the search bar to `max-w-md` and centering it. In the normal state (with results), it uses only `"mb-4"`. The search bar should maintain the same full-width layout in both states.

2. **Auto-search breaks Chinese IME input** — The current 300ms debounce `useEffect` in `SongsetsClient.tsx` fires `setDebouncedSearch(search)` on every keystroke. For Chinese text input via IME (Input Method Editor), each keystroke is composing a character, and the debounce fires mid-composition — triggering a search before the user finishes typing the Chinese characters. Replace the debounced auto-search with a dedicated **Search button** + **Enter key** trigger.

## User Decisions

| Decision | Choice |
|----------|--------|
| Search bar sizing on no results | Full width (remove `max-w-md mx-auto`, use `"mb-4"` in all states) |
| Search button placement | Inline right of input (same row: `[Search input] [Search button]`) |
| Auto-search behavior | Remove debounce entirely; search only fires on button click or Enter key |
| Enter key behavior | Enter triggers search (in addition to Search button click) |
| Testing | Include Vitest test plan |

## Current State

### Files Involved

| File | Role |
|------|------|
| `delivery/webapp/src/components/songset/SongsetList.tsx:114-148` | `renderSearchBar` helper — renders the search input, clear button, and loading spinner |
| `delivery/webapp/src/components/songset/SongsetList.tsx:237` | Empty-state search bar — uses `"mb-4 max-w-md mx-auto"` (shrinks) |
| `delivery/webapp/src/components/songset/SongsetList.tsx:314` | Normal-state search bar — uses `"mb-4"` (full width) |
| `delivery/webapp/src/app/songsets/SongsetsClient.tsx:82-88` | Debounce `useEffect` — 300ms timer sets `debouncedSearch` and resets page to 1 |
| `delivery/webapp/src/app/songsets/SongsetsClient.tsx:90-138` | Fetch `useEffect` — depends on `[page, debouncedSearch, pageSize, refreshKey]` |
| `delivery/webapp/src/app/songsets/SongsetsClient.tsx:140-146` | URL sync `useEffect` — depends on `[page, debouncedSearch, router]` |
| `delivery/webapp/src/app/songsets/SongsetsClient.tsx:157-159` | `handleSearchChange` — updates `search` state immediately |
| `delivery/webapp/src/app/songsets/SongsetsClient.tsx:336-338` | Props passed to `SongsetList` — `search={search}`, `onSearchChange={handleSearchChange}`, `isSearching={isLoading && search !== debouncedSearch}` |
| `delivery/webapp/src/test/components/songset/SongsetList.test.tsx` | Existing tests for search bar, pagination, empty state |

### Current Data Flow (debounced auto-search)

```
User types in Input
  → onChange → onSearchChange(e.target.value)
  → setSearch(value)                    [immediate UI update]
  → useEffect [search] → 300ms timer → setDebouncedSearch(search) + setPage(1)
  → useEffect [page, debouncedSearch, ...] → fetch /api/songsets?search=...
  → useEffect [page, debouncedSearch, router] → router.replace(URL)
```

### Problem with Chinese IME

Chinese input via IME composes characters one keystroke at a time (e.g., typing "敬" requires pressing multiple keys). The `onChange` fires on every intermediate keystroke. Even with 300ms debounce, the timer fires before the user finishes composing the character because IME composition is slower than typing Latin characters. This results in searching for partial/garbled text.

---

## Implementation Plan

### Phase 1: SongsetList Component — Fix search bar sizing + add Search button

**File:** `delivery/webapp/src/components/songset/SongsetList.tsx`

#### 1a. Fix no-results search bar sizing (line 237)

Change the empty-state `renderSearchBar` call from:
```tsx
{renderSearchBar("mb-4 max-w-md mx-auto")}
```
to:
```tsx
{renderSearchBar("mb-4")}
```

This makes the search bar use the same full-width layout in both the empty-state and the normal state.

#### 1b. Add `onSearch` callback prop

Add to `SongsetListProps` interface (line 37-58):
```typescript
onSearch?: () => void;
```

Add to the component destructuring (line 60-81):
```typescript
onSearch,
```

#### 1c. Add `onKeyDown` handler to search input

In the `renderSearchBar` helper (lines 114-148), add `onKeyDown` to the `<Input>`:
```tsx
<Input
  type="text"
  value={search}
  onChange={(e) => onSearchChange?.(e.target.value)}
  onKeyDown={(e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      onSearch?.();
    }
  }}
  placeholder="Search songsets by name or description..."
  className="pl-9 pr-10"
  aria-label="Search songsets"
  data-testid="songset-search-input"
/>
```

#### 1d. Add Search button inline right of input

Restructure the `renderSearchBar` container from a single `<div className="relative">` to a flex row containing the search input wrapper and a Search button:

```tsx
const renderSearchBar = (containerClassName?: string) => (
  <div className={cn("flex items-center gap-2", containerClassName)}>
    <div className="relative flex-1">
      <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground pointer-events-none" />
      <Input
        type="text"
        value={search}
        onChange={(e) => onSearchChange?.(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            onSearch?.();
          }
        }}
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
          onClick={() => {
            onSearchChange?.("");
            onSearch?.();
          }}
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
    <Button
      variant="default"
      size="default"
      onClick={() => onSearch?.()}
      aria-label="Search"
      data-testid="songset-search-button"
    >
      <Search className="size-4 mr-2" />
      Search
    </Button>
  </div>
);
```

**Key changes in `renderSearchBar`:**
- Outer container: Changed from `relative` to `flex items-center gap-2` to lay out input + button side by side.
- Inner wrapper: New `<div className="relative flex-1">` wraps the input and its absolutely-positioned icons, taking remaining space.
- Clear button click: Now also calls `onSearch?.()` immediately after clearing, so clearing the search triggers a re-search without waiting.
- Search button: New `<Button>` after the inner wrapper, with `Search` icon + "Search" text label, `data-testid="songset-search-button"`.

#### 1e. Responsive layout note

On small screens, the flex row with `gap-2` will keep the search button to the right of the input. The input has `flex-1` so it shrinks first. This matches the user's choice of "Inline right of input."

---

### Phase 2: SongsetsClient — Remove debounce, add explicit search trigger

**File:** `delivery/webapp/src/app/songsets/SongsetsClient.tsx`

#### 2a. Remove debounce useEffect (lines 82-88)

Delete the entire debounce `useEffect`:
```typescript
// DELETE:
useEffect(() => {
  const timer = setTimeout(() => {
    setDebouncedSearch(search);
    setPage(1);
  }, 300);
  return () => clearTimeout(timer);
}, [search]);
```

#### 2b. Replace `debouncedSearch` state with `committedSearch` state

Rename `debouncedSearch` to `committedSearch` throughout the file. `committedSearch` represents the search value that has been "committed" by the user (via Search button or Enter key). It only changes when the user explicitly triggers a search.

```typescript
const [committedSearch, setCommittedSearch] = useState(initialSearch);
```

#### 2c. Add `handleSearch` callback

Add a new callback that commits the search and resets to page 1:
```typescript
const handleSearch = useCallback(() => {
  setCommittedSearch(search);
  setPage(1);
}, [search]);
```

#### 2d. Update fetch useEffect dependencies (lines 90-138)

Change the dependency array from `[page, debouncedSearch, pageSize, refreshKey]` to `[page, committedSearch, pageSize, refreshKey]`.

Also update the `params.set` line:
```typescript
if (committedSearch.trim()) {
  params.set("search", committedSearch.trim());
}
```

#### 2e. Update URL sync useEffect (lines 140-146)

Change dependencies from `[page, debouncedSearch, router]` to `[page, committedSearch, router]`.

Update the param building:
```typescript
if (committedSearch.trim()) params.set("search", committedSearch.trim());
```

#### 2f. Update `isSearching` prop calculation (line 338)

The `isSearching` prop indicates whether a search is in-flight. Since we no longer debounce, the "searching" state is simply when `isLoading` is true and there's an active search term:

```typescript
isSearching={isLoading && committedSearch.trim().length > 0}
```

#### 2g. Pass `onSearch` to SongsetList (line 319-339)

Add the `onSearch` prop:
```typescript
<SongsetList
  // ... existing props ...
  search={search}
  onSearchChange={handleSearchChange}
  onSearch={handleSearch}
  isSearching={isLoading && committedSearch.trim().length > 0}
/>
```

#### 2h. Keep `handleSearchChange` as-is (lines 157-159)

The `handleSearchChange` callback only updates `search` state (for immediate UI responsiveness in the input). It does NOT trigger a fetch. This is the key fix — typing in the search input updates the input value but does not fire a search until the user clicks Search or presses Enter.

```typescript
const handleSearchChange = useCallback((value: string) => {
  setSearch(value);
}, []);
```

---

### Phase 3: Server Page — No changes needed

**File:** `delivery/webapp/src/app/songsets/page.tsx`

The server page already reads `search` from URL params and passes `initialSearch` to the client. No changes needed — the SSR initial load uses the URL search param, and client-side navigations update the URL via the `router.replace` effect.

---

### Phase 4: Tests

**File:** `delivery/webapp/src/test/components/songset/SongsetList.test.tsx`

#### 4a. Update `defaultProps` to include `onSearch`

Add `onSearch: vi.fn()` to the `defaultProps` object (line 32-50).

#### 4b. Add tests for Search button

Add to the `"search bar"` describe block:

```typescript
it("renders a Search button", () => {
  renderList();
  expect(screen.getByTestId("songset-search-button")).toBeInTheDocument();
});

it("calls onSearch when Search button clicked", () => {
  const onSearch = vi.fn();
  renderList({ onSearch });

  fireEvent.click(screen.getByTestId("songset-search-button"));

  expect(onSearch).toHaveBeenCalledTimes(1);
});

it("calls onSearch when Enter key is pressed in search input", () => {
  const onSearch = vi.fn();
  renderList({ onSearch });

  const input = screen.getByLabelText(/search songsets/i);
  fireEvent.keyDown(input, { key: "Enter" });

  expect(onSearch).toHaveBeenCalledTimes(1);
});

it("does not call onSearch when non-Enter key is pressed", () => {
  const onSearch = vi.fn();
  renderList({ onSearch });

  const input = screen.getByLabelText(/search songsets/i);
  fireEvent.keyDown(input, { key: "a" });

  expect(onSearch).not.toHaveBeenCalled();
});

it("does not call onSearch when typing in input (only calls onSearchChange)", () => {
  const onSearch = vi.fn();
  const onSearchChange = vi.fn();
  renderList({ onSearch, onSearchChange });

  const input = screen.getByLabelText(/search songsets/i);
  fireEvent.change(input, { target: { value: "test" } });

  expect(onSearchChange).toHaveBeenCalledWith("test");
  expect(onSearch).not.toHaveBeenCalled();
});
```

#### 4c. Add test for clear button triggering search

Update existing clear button test or add a new test:

```typescript
it("calls onSearch when clear button clicked", () => {
  const onSearch = vi.fn();
  const onSearchChange = vi.fn();
  renderList({ search: "Sunday", onSearch, onSearchChange });

  fireEvent.click(screen.getByLabelText(/clear search/i));

  expect(onSearchChange).toHaveBeenCalledWith("");
  expect(onSearch).toHaveBeenCalledTimes(1);
});
```

#### 4d. Add test for search bar sizing consistency

Add to the `"empty state with search"` describe block:

```typescript
it("search bar in empty state has same full-width layout as normal state", () => {
  // Render normal state (with results)
  const { rerender } = renderList();
  const normalSearchContainer = screen.getByTestId("songset-search-input").closest("div.flex");
  expect(normalSearchContainer).toHaveClass("flex", "items-center", "gap-2");

  // Render empty state (no results, with search active)
  rerender(
    <SongsetList {...defaultProps} songsets={[]} search="nonexistent" />
  );
  const emptySearchContainer = screen.getByTestId("songset-search-input").closest("div.flex");
  expect(emptySearchContainer).toHaveClass("flex", "items-center", "gap-2");

  // The outer container should NOT have max-w-md in empty state
  expect(emptySearchContainer?.className).not.toContain("max-w-md");
});
```

#### 4e. Update existing search bar test for layout change

The existing test at line 264 ("calls onSearchChange when typing in search input") will still pass since `onChange` still calls `onSearchChange`. But the DOM structure has changed (input is now inside an inner `relative flex-1` div within an outer `flex items-center gap-2` div). Ensure tests that query the input still work — `getByLabelText` is resilient to DOM nesting, so they should pass without changes.

---

### Phase 5: Manual Verification Checklist

After implementation, verify:

1. **No-results sizing**: Search for a non-existent term → search bar should be full width (same as when results are shown).
2. **Chinese IME**: Type Chinese characters in search input → no search fires until Search button or Enter. The input value updates normally as you compose.
3. **Search button**: Click "Search" button → search fires, results update, URL syncs.
4. **Enter key**: Press Enter in search input → search fires.
5. **Clear button**: Click X clear button → search clears AND a search fires for empty term (showing all songsets).
6. **URL sync**: After search, URL updates to `?search=<term>`. Reloading the page preserves the search.
7. **Pagination reset**: Searching while on page 3 → resets to page 1.
8. **Loading spinner**: While search is fetching, spinner appears in the input.
9. **No debounce**: Typing "Sunday" quickly → no intermediate fetches for "S", "Su", "Sun", etc. Only the final committed search fires.

---

## Files Changed (Summary)

| File | Change |
|------|--------|
| `delivery/webapp/src/components/songset/SongsetList.tsx` | Fix empty-state search bar sizing (`"mb-4"` instead of `"mb-4 max-w-md mx-auto"`); restructure `renderSearchBar` to flex layout with Search button; add `onSearch` prop and `onKeyDown` Enter handler |
| `delivery/webapp/src/app/songsets/SongsetsClient.tsx` | Remove 300ms debounce `useEffect`; rename `debouncedSearch` → `committedSearch`; add `handleSearch` callback; update fetch/URL-sync effects to use `committedSearch`; pass `onSearch` to `SongsetList` |
| `delivery/webapp/src/test/components/songset/SongsetList.test.tsx` | Add `onSearch` to defaultProps; add tests for Search button, Enter key, clear-then-search, sizing consistency |

## Out of Scope

- Debounce library removal (was using native `setTimeout`, now simply removed — no dependency to uninstall)
- Android app search (Android uses its own search implementation via `SongsetsApi.kt`)
- Search history / autocomplete suggestions
- Server-side search improvements (the DB `ilike` query and API route are unchanged)
- Pagination component changes (only the search trigger mechanism changes)
