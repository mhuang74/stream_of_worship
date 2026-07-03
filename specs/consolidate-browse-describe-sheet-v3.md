# Consolidated Song Search Sheet V3 — Shared Filter Visibility + Missing v1/v2 Items

## Summary

The v2 implementation (`consolidate-browse-describe-sheet-v2.md`) correctly lifted shared filter **state** (`selectedAlbums`, `selectedKeys`, `selectedBpm`) into `BrowseSheet` and correctly passes filter **values** to `SemanticSearch` via props. However, the filter **UI controls** (AlbumMultiSelect, advanced key/BPM chips) live entirely inside `SongSearch.tsx`, which is only rendered when `mode === "keyword"` (`BrowseSheet.tsx:355`). As a result, in Describe mode the user has no visible way to set or adjust albums/keys/BPM — they must switch to Keyword, set filters, then switch back.

A systematic comparison of the v1 plan (`consolidate-browse-describe-sheet.md`) against the actual codebase revealed four additional gaps that were required by both v1 and v2 but never implemented:

1. **Gap #1 (primary bug):** Shared filter UI controls not visible in Describe mode.
2. **Gap #2:** No contextual help text with examples near the search inputs (v1 §3.1; v2 line 23).
3. **Gap #3:** No `<SheetDescription>` rendered — v1 §0.4 specified "Search the catalog and add songs to your songset".
4. **Gap #4:** No test asserting filter selections persist across mode switches (v1 §5.1; v2 Test Plan).
5. **Gap #5:** `data-testid="browse-mode-tab"` was never renamed to `"keyword-mode-tab"` (v1 §0.4).

This plan addresses all five gaps. No backend changes are required — the v2 backend work (repeated `albumName` params, semantic body filters) is already correct.

## Problem Analysis

### What v2 required (and is correctly implemented at the data layer)
- `BrowseSheet.tsx:49-51` — shared filter state (`selectedAlbums`, `selectedKeys`, `selectedBpm`) lifted into the sheet.
- `BrowseSheet.tsx:465-468` — filter values passed to `SemanticSearch` as props.
- `SemanticSearch.tsx:74-76` — filters forwarded into the semantic POST body.
- `SemanticSearch.test.tsx:165-194` — test confirms filters reach the API.

### What v2 required (and is broken at the UI layer)
- v2 spec line 20: "shared `selectedAlbums`, `selectedKeys`, and `selectedBpm`" — state is shared, but UI is not.
- v2 spec line 24: "Add album multi-select as a compact control" — the control exists (`AlbumMultiSelect.tsx`) but is only rendered inside `SongSearch`, which is conditionally rendered in Keyword mode only.
- v2 spec line 42: "Lift shared filter state into `BrowseSheet` so mode switching preserves filters" — state is lifted, but the **controls** were not lifted, so the user cannot see/set filters in Describe mode.

### Root cause
`BrowseSheet.tsx:355-455` renders `SongSearch` (which contains AlbumMultiSelect + advanced filter toggle + key/BPM chips) only inside the `mode === "keyword"` panel. The `mode === "describe"` panel (`BrowseSheet.tsx:457-470`) renders only `SemanticSearch` (textarea + search button + results), with no filter controls.

## Design Decisions

### Gap #1: Shared filter visibility

Extract the shared filter controls (AlbumMultiSelect + advanced filters toggle + key/BPM chips panel) into a new `SharedFilters` component. Render `SharedFilters` in `BrowseSheet` **above** the mode-conditional panels so it is always visible regardless of mode. `SongSearch` retains only the keyword input + debounce logic. `SemanticSearch` retains only the textarea + explicit search button + semantic result rendering.

This approach:
- Makes filters visible and editable in both modes (satisfies v2 spec lines 20, 24, 42).
- Preserves the v2 "avoid full shared result-grid refactor" principle (v2 spec line 46) — each mode still renders its own results.
- Minimizes churn — `SongSearch` and `SemanticSearch` keep their existing result-rendering logic; only the filter UI moves out.

### Gap #2: Contextual help text

Add concise contextual help text below each mode's input. The text swaps per mode:
- **Keyword mode:** `Tip: search by title, pinyin, or composer — e.g. '奇异恩典', 'Amazing Grace', '约瑟夫'`
- **Describe mode:** `Tip: describe the song by theme or feeling — e.g. '关于神的恩典与怜悯的赞美', 'upbeat praise songs about grace'`

These are the exact example strings from v1 §3.1. The help text is rendered as a small `<p>` with `text-xs text-muted-foreground` to avoid crowding the sheet (v2 line 23: "concise contextual examples near the input, but keep them short enough not to crowd the sheet").

In Keyword mode, the help text lives in `SongSearch.tsx` below the input. In Describe mode, it lives in `SemanticSearch.tsx` below the textarea (replacing the current "Press Ctrl+Enter to search" hint — combine both into one line: `Tip: describe by theme or feeling — e.g. '关于神的恩典' · Press Ctrl+Enter to search`).

### Gap #3: Sheet description

Add `<SheetDescription>` to the `SheetHeader` in `BrowseSheet.tsx`, using the v1 §0.4 text: `"Search the catalog and add songs to your songset"`. Import `SheetDescription` from `@/components/ui/sheet` (already exported, `sheet.tsx:137`).

### Gap #4: Filter persistence test

Add a test to `BrowseSheet.test.tsx` that selects albums + key + BPM in Keyword mode, switches to Describe, switches back, and asserts the selections are preserved. This satisfies v1 §5.1 and the v2 Test Plan ("Mode switch preserves selected albums/key/BPM filters").

### Gap #5: testid rename

Rename `data-testid="browse-mode-tab"` → `"keyword-mode-tab"` in `BrowseSheet.tsx`. Update all test references:
- `BrowseSheet.test.tsx:329` — `getByTestId("browse-mode-tab")` → `getByTestId("keyword-mode-tab")`
- `BrowseSheet.test.tsx:367` — same

The accessibility tests (`accessibility.test.tsx`) use `getByRole("tab", { name: /keyword/i })` — not the testid — so they are unaffected.

## Implementation Plan

### Phase 1 — Create `SharedFilters` component (Gap #1)

**New file:** `delivery/webapp/src/components/songset/SharedFilters.tsx`

Extract the following UI from `SongSearch.tsx` into `SharedFilters`:
- `AlbumMultiSelect` render block (`SongSearch.tsx:263-270`).
- Advanced filters toggle button (`SongSearch.tsx:273-291`).
- Collapsible advanced filters panel: key chips + BPM chips + apply/clear actions (`SongSearch.tsx:294-387`).

Props:
```typescript
interface SharedFiltersProps {
  albums: string[];
  selectedAlbums: string[];
  onSelectedAlbumsChange: (albums: string[]) => void;
  selectedKeys: string[];
  onSelectedKeysChange: (keys: string[]) => void;
  selectedBpm?: BpmBandKey;
  onSelectedBpmChange: (bpm: BpmBandKey | undefined) => void;
  onApplyFilters: () => void;
  onClearFilters: () => void;
  isLoading?: boolean;
  className?: string;
}
```

The `SharedFilters` component manages only the `showAdvanced` local toggle state (open/collapse). All filter values and change handlers are controlled by the parent.

Keep all existing `data-testid` attributes (`album-filter`, `album-option-{name}`, `album-clear-all`, `album-summary-clear`, `advanced-filters-toggle`, `advanced-filters-panel`, `advanced-key-chips`, `key-chip-{key}`, `advanced-bpm-chips`, `bpm-chip-{band}`, `advanced-apply-button`, `advanced-clear-button`) so existing tests continue to work.

### Phase 2 — Refactor `SongSearch.tsx` (Gap #1 + Gap #2)

**2.1 Remove filter UI (Gap #1)**

Remove from `SongSearch.tsx`:
- The `AlbumMultiSelect` import and render block.
- The advanced filters toggle button.
- The collapsible advanced filters panel (key chips, BPM chips, apply/clear actions).
- The `showAdvanced` state.
- The `toggleKey`, `toggleBpm`, `handleApplyFilters`, `handleClearFilters` callbacks (these move to `SharedFilters`).

Keep in `SongSearch.tsx`:
- The keyword `Input` + clear button + loading spinner.
- The debounce logic and `triggerSearch` / `debouncedSearch` / `handleQueryChange` / `handleClear` callbacks.
- The `onSearch` and `onAdvancedSearch` prop callbacks (still called from debounce logic).

`SongSearch` no longer needs `selectedAlbums`, `selectedKeys`, `selectedBpm`, `onSelectedAlbumsChange`, `onSelectedKeysChange`, `onSelectedBpmChange` props — these move to `SharedFilters`. However, `SongSearch` still needs `selectedAlbums` internally for `triggerSearch` (to pass album filters into the debounced search call). Resolution: `BrowseSheet` passes `selectedAlbums` to both `SharedFilters` (for the UI) and `SongSearch` (for the search payload), or `SongSearch` receives the current filter values via a single `filters` prop.

Simpler approach: `SongSearch` keeps `selectedAlbums` as a read-only prop (no setter) used only for building the search request. `SharedFilters` receives both the values and setters. `BrowseSheet` owns the state and passes to both.

Updated `SongSearch` props:
```typescript
interface SongSearchProps {
  onSearch: (query: string, albumFilters?: string[]) => void;
  onAdvancedSearch?: (criteria: StructuredSearchCriteria) => void;
  query?: string;
  onQueryChange?: (query: string) => void;
  selectedAlbums?: string[];        // read-only, for search payload
  selectedKeys?: string[];           // read-only, for search payload
  selectedBpm?: BpmBandKey;          // read-only, for search payload
  isLoading?: boolean;
  className?: string;
  placeholder?: string;
  debounceMs?: number;
  initialQuery?: string;
}
```

The `triggerSearch` callback in `SongSearch` still uses `selectedAlbums`, `selectedKeys`, `selectedBpm` to build the `StructuredSearchCriteria` — these are now read-only props from the parent. The `showAdvanced` conditional in `triggerSearch` (`SongSearch.tsx:115`) should be removed; instead, `triggerSearch` always includes filters if they are non-empty (the advanced panel is now always available via `SharedFilters`, so there is no "showAdvanced" gate).

**2.2 Add contextual help text (Gap #2)**

Add a `<p>` element below the search input (after the input container `div`, before the closing `</div>` of the component root):

```tsx
<p className="text-xs text-muted-foreground px-1" data-testid="keyword-help-text">
  Tip: search by title, pinyin, or composer — e.g. '奇异恩典', 'Amazing Grace', '约瑟夫'
</p>
```

### Phase 3 — Update `BrowseSheet.tsx` (Gap #1 + Gap #3 + Gap #5)

**3.1 Add `SheetDescription` (Gap #3)**

Import `SheetDescription` from `@/components/ui/sheet` and add it to the `SheetHeader`:

```tsx
<SheetHeader className="pb-2">
  <SheetTitle>Search Songs</SheetTitle>
  <SheetDescription>Search the catalog and add songs to your songset</SheetDescription>
</SheetHeader>
```

**3.2 Rename testid (Gap #5)**

In `BrowseSheet.tsx:336`, change `data-testid="browse-mode-tab"` to `data-testid="keyword-mode-tab"`.

**3.3 Render `SharedFilters` above mode panels (Gap #1)**

Insert `<SharedFilters>` between the mode tabs (`BrowseSheet.tsx:328-353`) and the mode-conditional panels (`BrowseSheet.tsx:355` and `:457`):

```tsx
{/* Mode tabs */}
<div className="flex gap-1 pb-4 border-b mb-4" role="tablist" ...>
  ...existing tab buttons...
</div>

{/* Shared filters — visible in both modes */}
<SharedFilters
  albums={albums}
  selectedAlbums={selectedAlbums}
  onSelectedAlbumsChange={setSelectedAlbums}
  selectedKeys={selectedKeys}
  onSelectedKeysChange={setSelectedKeys}
  selectedBpm={selectedBpm}
  onSelectedBpmChange={setSelectedBpm}
  onApplyFilters={() => handleSearch(keywordQuery, selectedAlbums, {
    query: keywordQuery.trim() || undefined,
    albums: selectedAlbums.length > 0 ? selectedAlbums : undefined,
    keys: selectedKeys.length > 0 ? selectedKeys : undefined,
    bpmRange: selectedBpm,
  })}
  onClearFilters={() => {
    setSelectedAlbums([]);
    setSelectedKeys([]);
    setSelectedBpm(undefined);
    handleSearch(keywordQuery, undefined);
  }}
  isLoading={isLoading || isLoadingAlbums}
  className="px-1 pb-4"
/>
```

**3.4 Wire album change to trigger keyword search**

In Keyword mode, changing albums should trigger a debounced re-search (current behavior in `SongSearch.handleAlbumChange`). Since `SharedFilters` is now outside `SongSearch`, `BrowseSheet` needs to handle album-change-triggered re-search. Add a `useEffect` or callback in `BrowseSheet` that fires `handleSearch` when `selectedAlbums` changes (in keyword mode only):

```typescript
const prevAlbumsRef = useRef(selectedAlbums);
useEffect(() => {
  if (mode !== "keyword") return;
  if (prevAlbumsRef.current === selectedAlbums) return;
  prevAlbumsRef.current = selectedAlbums;
  handleSearch(keywordQuery, selectedAlbums, {
    query: keywordQuery.trim() || undefined,
    albums: selectedAlbums.length > 0 ? selectedAlbums : undefined,
    keys: selectedKeys.length > 0 ? selectedKeys : undefined,
    bpmRange: selectedBpm,
  });
}, [selectedAlbums, mode, keywordQuery, selectedKeys, selectedBpm, handleSearch]);
```

Alternatively, debounce the album-change-triggered search to match the existing keyword debounce behavior. The simplest approach: `SharedFilters` calls `onApplyFilters` automatically when albums/keys/bpm change (with a debounce), and `BrowseSheet` wires `onApplyFilters` to `handleSearch`. But this may cause unexpected re-searches in Describe mode (where search is explicit). Decision: in Describe mode, filter changes do NOT auto-trigger search (consistent with v2 spec line 22: "Describe mode uses an explicit Search button"). In Keyword mode, filter changes trigger a debounced re-search.

**3.5 Update `SongSearch` usage**

Remove the filter-related props from the `SongSearch` call (`BrowseSheet.tsx:359-375`):

```tsx
<SongSearch
  onSearch={handleSearch}
  onAdvancedSearch={(criteria) =>
    handleSearch(criteria.query ?? "", criteria.albums, criteria)
  }
  query={keywordQuery}
  onQueryChange={setKeywordQuery}
  selectedAlbums={selectedAlbums}
  selectedKeys={selectedKeys}
  selectedBpm={selectedBpm}
  isLoading={isLoading || isLoadingAlbums}
  initialQuery={initialSearchQuery}
/>
```

Remove `albums`, `onSelectedAlbumsChange`, `onSelectedKeysChange`, `onSelectedBpmChange` from the `SongSearch` call — those are now on `SharedFilters`.

**3.6 Footer update**

The footer (`BrowseSheet.tsx:473-486`) currently shows count only in keyword mode. Per v2 spec line 47: "For describe mode, show `${results.length} matches` or no count until searched." This is already partially handled — `SemanticSearch` shows its own result count (`SemanticSearch.tsx:281`). No change needed unless we want to unify the footer. Leave as-is for this pass.

### Phase 4 — Update `SemanticSearch.tsx` (Gap #2)

Add contextual help text below the textarea, combining the describe-mode examples with the existing Ctrl+Enter hint into a single line:

Replace the current hint (`SemanticSearch.tsx:234`):
```tsx
<p className="text-xs text-muted-foreground" aria-hidden="true">Press Ctrl+Enter to search</p>
```

With:
```tsx
<p className="text-xs text-muted-foreground" aria-hidden="true" data-testid="describe-help-text">
  Tip: describe by theme or feeling — e.g. '关于神的恩典与怜悯的赞美', 'upbeat praise songs about grace' · Press Ctrl+Enter to search
</p>
```

### Phase 5 — Tests (Gap #4 + testid updates)

**5.1 Update `BrowseSheet.test.tsx` — testid rename (Gap #5)**

Update all references from `browse-mode-tab` to `keyword-mode-tab`:
- Line 329: `screen.getByTestId("browse-mode-tab")` → `screen.getByTestId("keyword-mode-tab")`
- Line 367: same

**5.2 Add new tests to `BrowseSheet.test.tsx`**

Add the following tests:

*Filter visibility in Describe mode (Gap #1):*
- `renders album filter in describe mode` — switch to Describe mode, assert `album-filter` testid is still present.
- `renders advanced filters toggle in describe mode` — switch to Describe mode, assert `advanced-filters-toggle` is present.
- `can open advanced filters panel in describe mode` — switch to Describe mode, click toggle, assert `advanced-filters-panel` is present.

*Filter persistence across mode switch (Gap #4):*
- `filter selections persist across mode switch` — select albums (Hymns, Worship) + key (D) + BPM (slow) in Keyword mode via SharedFilters, switch to Describe, switch back to Keyword, assert:
  - `album-filter` button text contains "2"
  - `key-chip-D` has `aria-pressed="true"`
  - `bpm-chip-slow` has `aria-pressed="true"`

*Describe mode filter behavior:*
- `describe mode does not auto-search on filter change` — switch to Describe mode, select an album, assert no fetch to `/api/songs/search/semantic` until the Search button is clicked.
- `describe mode sends selected filters in semantic body` — select albums + key + BPM, switch to Describe, type query, click Search, assert the POST body includes `albums`, `keys`, `bpmRange`.

*Sheet description (Gap #3):*
- `renders sheet description` — assert `Search the catalog and add songs to your songset` text is present.

*Contextual help text (Gap #2):*
- `renders keyword help text in keyword mode` — assert `keyword-help-text` testid is present with expected example strings.
- `renders describe help text in describe mode` — switch to Describe, assert `describe-help-text` testid is present with expected example strings.

**5.3 Update `SongSearch.test.tsx`**

Since the filter UI (AlbumMultiSelect, advanced toggle, key/BPM chips) is moving out of `SongSearch` into `SharedFilters`, the following test sections in `SongSearch.test.tsx` need to be removed or moved:

- `describe("album filter", ...)` (lines 122-180) — remove; these tests now belong in `SharedFilters.test.tsx` or `BrowseSheet.test.tsx` integration tests.
- `describe("advanced filters", ...)` (lines 205-394) — remove; same reason.

Keep in `SongSearch.test.tsx`:
- `describe("rendering", ...)` (lines 29-54) — update: remove `renders album filter when albums are provided` and `does not render album filter when no albums` tests (no longer in SongSearch). Add `renders keyword help text` test.
- `describe("search functionality", ...)` (lines 56-120) — keep as-is.
- `describe("loading state", ...)` (lines 182-187) — keep as-is.
- `describe("accessibility", ...)` (lines 189-203) — keep as-is.

Update `defaultProps` to remove `albums` prop (no longer needed by SongSearch).

**5.4 New `SharedFilters.test.tsx`**

Unit test the `SharedFilters` component in isolation:
- Renders album multi-select when albums are provided.
- Does not render album multi-select when albums array is empty.
- Toggling advanced panel shows/hides key and BPM chips.
- Selecting/deselecting albums calls `onSelectedAlbumsChange`.
- Selecting/deselecting keys calls `onSelectedKeysChange`.
- Selecting BPM calls `onSelectedBpmChange`.
- Apply button calls `onApplyFilters`.
- Clear all button calls `onClearFilters`.
- Active filter count badge shows correct count.

**5.5 Update `SemanticSearch.test.tsx`**

Add test:
- `renders describe help text` — assert `describe-help-text` testid is present with expected example strings.

## Files Changed

| File | Change | Gap |
|---|---|---|
| `delivery/webapp/src/components/songset/SharedFilters.tsx` | **New** — extracted filter UI | #1 |
| `delivery/webapp/src/components/songset/BrowseSheet.tsx` | Render `SharedFilters` above mode panels; wire filter change handlers; remove filter props from `SongSearch` call; add `SheetDescription`; rename `browse-mode-tab` testid to `keyword-mode-tab` | #1, #3, #5 |
| `delivery/webapp/src/components/songset/SongSearch.tsx` | Remove AlbumMultiSelect, advanced toggle, key/BPM chips, `showAdvanced` state, `toggleKey`/`toggleBpm`/`handleApplyFilters`/`handleClearFilters`; keep keyword input + debounce; make filter props read-only; add keyword help text | #1, #2 |
| `delivery/webapp/src/components/search/SemanticSearch.tsx` | Add describe help text with examples (replace standalone Ctrl+Enter hint with combined line) | #2 |
| `delivery/webapp/src/test/components/songset/BrowseSheet.test.tsx` | Rename `browse-mode-tab` references to `keyword-mode-tab`; add tests for filter visibility in Describe mode, filter persistence across mode switch, sheet description, contextual help text | #1, #3, #4, #5 |
| `delivery/webapp/src/test/components/songset/SongSearch.test.tsx` | Remove `album filter` and `advanced filters` test sections (moved to `SharedFilters`); remove `albums` from defaultProps; add keyword help text test | #1, #2 |
| `delivery/webapp/src/test/components/search/SemanticSearch.test.tsx` | Add describe help text test | #2 |
| `delivery/webapp/src/test/components/songset/SharedFilters.test.tsx` | **New** — unit tests for `SharedFilters` | #1 |

## Verification

```bash
pnpm --filter sow-webapp test -- src/test/components/songset src/test/components/search
pnpm --filter sow-webapp lint
pnpm --filter sow-webapp build
```

Manual smoke test (`pnpm --filter sow-webapp dev`):
1. Open the search sheet — sheet description "Search the catalog and add songs to your songset" visible below title.
2. Keyword help text visible below search input with example strings.
3. Filters (Albums button, Advanced filters toggle) visible below mode tabs.
4. Switch to Describe mode — filters remain visible; describe help text visible below textarea.
5. Select albums + key + BPM in Describe mode — no auto-search fires.
6. Type a description and click Search — POST body includes `albums`, `keys`, `bpmRange`.
7. Switch back to Keyword mode — filter selections are preserved.
8. Change album selection in Keyword mode — debounced re-search fires with new album filter.

## Gap Summary

| Gap | Description | v1 § | v2 § | Phase |
|---|---|---|---|---|
| #1 | Shared filter UI not visible in Describe mode | 4.2 | Lines 20, 24, 42 | 1, 2, 3 |
| #2 | No contextual help text with examples | 3.1 | Line 23 | 2, 4, 5 |
| #3 | No `<SheetDescription>` rendered | 0.4 | (not overridden) | 3 |
| #4 | No test for filter persistence across mode switch | 5.1 | Test Plan | 5 |
| #5 | `browse-mode-tab` testid not renamed to `keyword-mode-tab` | 0.4 | (silent) | 3, 5 |

## Out of Scope

- Unifying the result grid between Keyword and Describe modes (deferred per v2 spec line 46).
- Renaming `BrowseSheet` to `SongSearchSheet` (deferred per v2 spec line 13).
- Backend changes (already complete from v2).
- Android client changes (not needed).
- Footer counter unification (Describe mode shows its own count in `SemanticSearch`).
- Per-album removable Badge chips (`album-chip-{name}`) — v2 deliberately replaced these with a compact summary row (v2 line 24).
