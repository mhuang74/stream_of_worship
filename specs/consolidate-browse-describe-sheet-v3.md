# Consolidated Song Search Sheet V3 — Shared Filter Visibility Fix

## Summary

The v2 implementation (`consolidate-browse-describe-sheet-v2.md`) correctly lifted shared filter **state** (`selectedAlbums`, `selectedKeys`, `selectedBpm`) into `BrowseSheet` and correctly passes filter **values** to `SemanticSearch` via props. However, the filter **UI controls** (AlbumMultiSelect, advanced key/BPM chips) live entirely inside `SongSearch.tsx`, which is only rendered when `mode === "keyword"` (`BrowseSheet.tsx:355`). As a result, in Describe mode the user has no visible way to set or adjust albums/keys/BPM — they must switch to Keyword, set filters, then switch back.

This plan fixes the bug by extracting the shared filter controls into a standalone `SharedFilters` component rendered **above** the mode-conditional panels in `BrowseSheet`, so filters are visible and editable in both Keyword and Describe modes. No backend changes are required — the v2 backend work (repeated `albumName` params, semantic body filters) is already correct.

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

## Design Decision

Extract the shared filter controls (AlbumMultiSelect + advanced filters toggle + key/BPM chips panel) into a new `SharedFilters` component. Render `SharedFilters` in `BrowseSheet` **above** the mode-conditional panels so it is always visible regardless of mode. `SongSearch` retains only the keyword input + debounce logic. `SemanticSearch` retains only the textarea + explicit search button + semantic result rendering.

This approach:
- Makes filters visible and editable in both modes (satisfies v2 spec lines 20, 24, 42).
- Preserves the v2 "avoid full shared result-grid refactor" principle (v2 spec line 46) — each mode still renders its own results.
- Minimizes churn — `SongSearch` and `SemanticSearch` keep their existing result-rendering logic; only the filter UI moves out.
- No backend changes needed.

## Implementation Plan

### Phase 1 — Create `SharedFilters` component

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

### Phase 2 — Refactor `SongSearch.tsx`

Remove from `SongSearch.tsx`:
- The `AlbumMultiSelect` import and render block.
- The advanced filters toggle button.
- The collapsible advanced filters panel (key chips, BPM chips, apply/clear actions).
- The `showAdvanced` state.
- The `toggleKey`, `toggleBpm`, `handleApplyFilters`, `handleClearFilters` callbacks (these move to `BrowseSheet` or into `SharedFilters`).

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

### Phase 3 — Update `BrowseSheet.tsx`

**3.1 Render `SharedFilters` above mode panels**

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

**3.2 Wire album change to trigger keyword search**

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

**3.3 Update `SongSearch` usage**

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

**3.4 Footer update**

The footer (`BrowseSheet.tsx:473-486`) currently shows count only in keyword mode. Per v2 spec line 47: "For describe mode, show `${results.length} matches` or no count until searched." This is already partially handled — `SemanticSearch` shows its own result count (`SemanticSearch.tsx:281`). No change needed unless we want to unify the footer. Leave as-is for this pass.

### Phase 4 — Tests

**4.1 Update `BrowseSheet.test.tsx`**

Existing tests that interact with filter controls (`album-filter`, `advanced-filters-toggle`, `key-chip-*`, `bpm-chip-*`, `advanced-apply-button`, `advanced-clear-button`) should continue to pass because the `data-testid` attributes are preserved in `SharedFilters`. Verify:

- `renders album filter` (line 156) — should pass; `SharedFilters` is always rendered.
- `sends repeated albumName params for selected albums` (line 204) — should pass; album selection still triggers keyword search.
- `renders advanced filters toggle` (line 375) — should pass; toggle is in `SharedFilters`.
- `opens advanced panel when toggle is clicked` (line 382) — should pass.
- `renders results after applying advanced filters` (line 395) — should pass.
- `renders results after applying BPM filter` (line 412) — should pass.
- `shows filter-specific empty state when no results match filters` (line 429) — should pass.

**4.2 Add new tests**

Add to `BrowseSheet.test.tsx`:

- `renders album filter in describe mode` — switch to Describe mode, assert `album-filter` testid is still present.
- `renders advanced filters toggle in describe mode` — switch to Describe mode, assert `advanced-filters-toggle` is present.
- `can open advanced filters panel in describe mode` — switch to Describe mode, click toggle, assert `advanced-filters-panel` is present.
- `filter selections persist across mode switch` — select albums + key + BPM in Keyword mode, switch to Describe, switch back, assert selections are preserved (the state is in `BrowseSheet`, so this should work).
- `describe mode does not auto-search on filter change` — switch to Describe mode, select an album, assert no fetch to `/api/songs/search/semantic` until the Search button is clicked.
- `describe mode sends selected filters in semantic body` — select albums + key + BPM, switch to Describe, type query, click Search, assert the POST body includes `albums`, `keys`, `bpmRange`.

**4.3 Update `SongSearch.test.tsx`** (if it exists)

If `SongSearch` has its own tests for filter UI, those tests need to move to `SharedFilters` tests or `BrowseSheet` integration tests. Check for existing `SongSearch.test.tsx` and update accordingly.

**4.4 New `SharedFilters.test.tsx`** (optional)

Unit test the `SharedFilters` component in isolation:
- Renders album multi-select when albums are provided.
- Toggling advanced panel shows/hides key and BPM chips.
- Selecting/deselecting albums calls `onSelectedAlbumsChange`.
- Selecting/deselecting keys calls `onSelectedKeysChange`.
- Selecting BPM calls `onSelectedBpmChange`.
- Apply button calls `onApplyFilters`.
- Clear all button calls `onClearFilters` and resets selections.

## Files Changed

| File | Change |
|---|---|
| `delivery/webapp/src/components/songset/SharedFilters.tsx` | **New** — extracted filter UI |
| `delivery/webapp/src/components/songset/BrowseSheet.tsx` | Render `SharedFilters` above mode panels; wire filter change handlers; remove filter props from `SongSearch` call |
| `delivery/webapp/src/components/songset/SongSearch.tsx` | Remove AlbumMultiSelect, advanced toggle, key/BPM chips, `showAdvanced` state, `toggleKey`/`toggleBpm`/`handleApplyFilters`/`handleClearFilters`; keep keyword input + debounce; make filter props read-only |
| `delivery/webapp/src/test/components/songset/BrowseSheet.test.tsx` | Add tests for filter visibility in Describe mode + filter persistence across mode switch |
| `delivery/webapp/src/test/components/songset/SharedFilters.test.tsx` | **New** — unit tests for `SharedFilters` (optional) |

## Verification

```bash
pnpm --filter sow-webapp test -- src/test/components/songset src/test/components/search
pnpm --filter sow-webapp lint
pnpm --filter sow-webapp build
```

Manual smoke test (`pnpm --filter sow-webapp dev`):
1. Open the search sheet — filters (Albums button, Advanced filters toggle) visible below mode tabs.
2. Switch to Describe mode — filters remain visible.
3. Select albums + key + BPM in Describe mode — no auto-search fires.
4. Type a description and click Search — POST body includes `albums`, `keys`, `bpmRange`.
5. Switch back to Keyword mode — filter selections are preserved.
6. Change album selection in Keyword mode — debounced re-search fires with new album filter.

## Out of Scope

- Unifying the result grid between Keyword and Describe modes (deferred per v2 spec line 46).
- Renaming `BrowseSheet` to `SongSearchSheet` (deferred per v2 spec line 13).
- Backend changes (already complete from v2).
- Android client changes (not needed).
- Footer counter unification (Describe mode shows its own count in `SemanticSearch`).
