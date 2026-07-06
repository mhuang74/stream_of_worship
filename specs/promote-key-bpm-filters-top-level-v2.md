# Promote Musical Key & BPM Filters to Top-Level Dropdowns (v2)

## Summary

The Search Songs screen (the bottom `Sheet` opened from the Songset Editor) currently nests Musical Key and BPM Range filters inside a collapsible "Advanced filters" panel in `SharedFilters.tsx`, while the Album filter sits as an always-visible dropdown (`AlbumMultiSelect`) at the top level. This buries two useful filters behind an extra click and obscures their active state behind an "Advanced filters" badge count.

This spec promotes Musical Key and BPM Range to **independent top-level dropdowns at the same level as Album**, removes the "Advanced filters" toggle entirely, gives each filter a transparent trigger-summary text (mirroring Album's `"All N Albums"` pattern), and switches BPM Range from single-select to multi-select.

## Goals

1. Make Musical Key and BPM filters visibly transparent and discoverable at first glance, alongside Album.
2. Each filter's trigger shows its current selection state via summary text.
3. Remove the indirection of the "Advanced filters" collapsible panel and its count badge.
4. BPM Range becomes multi-select (any of Slow / Moderate / Fast).
5. No "number of filters applied" badge anywhere.

## Non-Goals

- Changing the Album filter UX (it stays as-is, including its selected-chips strip below the trigger).
- Adding selected-chip strips to Key or BPM dropdowns — their trigger summary text alone is sufficient.
- URL-syncing filter state.
- Touching the search results list, sorting, pagination, or `SemanticSearch` describe-input flow beyond the necessary prop wiring.
- Changing the `StructuredSearchCriteria` type beyond what is required for multi-BPM support (see Data Model).

## Current State (for reference)

| Aspect | Where | Notes |
|---|---|---|
| Sheet container / state owner | `delivery/webapp/src/components/songset/BrowseSheet.tsx` lines 73-90 | Holds `selectedAlbums`, `selectedKeys`, `selectedBpm` via `useState`. `selectedBpm` is currently `BpmBandKey \| undefined` (single). |
| Filter UI host | `delivery/webapp/src/components/songset/SharedFilters.tsx` | Renders `AlbumMultiSelect` outside the advanced panel; renders Key (lines 104-131) + BPM (lines 133-169) as inline chip groups **inside** the `#advanced-filters-panel` (lines 98-183), gated by `showAdvanced` useState (line 43). |
| Album dropdown (reference pattern) | `delivery/webapp/src/components/songset/AlbumMultiSelect.tsx` | shadcn/Base UI `DropdownMenu` + `DropdownMenuCheckboxItem` + trigger summary text + secondary chip strip + "Clear all" item. |
| Constants | `delivery/webapp/src/lib/constants.ts` lines 74-81 | `PITCH_CLASSES`, `BPM_BANDS` (slow/moderate/fast with `min`/`max`), `BPM_BAND_KEYS`, `BpmBandKey`. |
| Filter criteria type | `delivery/webapp/src/components/songset/search/types.ts` | `StructuredSearchCriteria.bpmRange?: "slow" \| "moderate" \| "fast"` (single). Note: this file also duplicates `BPM_BANDS` — the duplicate should be removed in favor of `lib/constants.ts`. |
| API serialization | `BrowseSheet.handleSearch` lines 132-150 (keyword path) and `SemanticSearch.tsx` lines 104-119 (semantic path) | Builds `URLSearchParams` with `bpmRange=<single band key>` for keyword path; JSON body with `bpmRange` for semantic path. |

## Target UX

### Layout

A single top-level filter row at the top of `SharedFilters`, ordered: **Album | Musical Key | BPM Range**, arranged with `flex flex-wrap gap-2`. Each is an independent dropdown rendered as a sibling (no parent toggle, no panel).

### Per-filter trigger summary text

Each dropdown trigger mirrors Album's link-style `Button` (`variant="link"`, `truncate`, `ChevronDown`) and shows summary text:

- **Album** — unchanged: `"All N Albums"` / single label / `"N Albums"`.
- **Musical Key**
  - Default (no selection): `"All Musical Keys"`
  - One selected: the key name, e.g. `"C"`
  - Two selected: `"C, D"`
  - Three+ selected: first two + overflow count, e.g. `"C, D, +2"` (matches Album's overflow pattern philosophy, compact)
- **BPM Range**
  - Default (no selection): `"All BPM Ranges"`
  - One selected: band label only, e.g. `"Slow"` (label only, no range text)
  - Two/three selected: comma-joined labels, e.g. `"Slow, Fast"`
  - Range text helper: reuse the existing `rangeText` logic from `SharedFilters.tsx` lines 140-148 for dropdown item labels, extracted into a small pure helper in `lib/constants.ts` or a sibling helper module.

### Dropdown content

Each Key/BPM dropdown uses the same shadcn/Base UI `DropdownMenu` + `DropdownMenuCheckboxItem` pattern as `AlbumMultiSelect`:

- `DropdownMenuLabel` header ("Musical Key" / "BPM Range").
- Optional "Clear all" `DropdownMenuItem` at the top, shown only when at least one item is selected.
- One `DropdownMenuCheckboxItem` per option, `checked` reflecting selection.
- `data-testid` conventions:
  - Triggers: `key-filter`, `bpm-filter` (mirroring `album-filter`).
  - Options: `key-option-{key}` (with `#` → `sharp`), `bpm-option-{band}`.

### Selection logic

- **Musical Key** — multi-select, toggle on click. Active key can be deselected by clicking it again. Identical to current `toggleKey` semantics.
- **BPM Range** — multi-select (was single-select). Clicking an unchecked band adds it; clicking a checked band removes it. Songs matching **any** selected band pass the filter (OR semantics).

### Removed elements

- The "Advanced filters" toggle `<Button>` and its `SlidersHorizontal` icon import (lines 78-95 of `SharedFilters.tsx`).
- The numeric `Badge` showing advanced filter count.
- The `#advanced-filters-panel` wrapper `div` (lines 98-183).
- The `useState(showAdvanced)` state (line 43) and `advancedFilterCount`/`hasAdvancedFilters` derived values.
- The standalone "Clear all" button inside the advanced panel (lines 171-182) — replaced by per-dropdown "Clear all" items.

## Data Model & API Changes

### Type change — `StructuredSearchCriteria.bpmRange`

`delivery/webapp/src/components/songset/search/types.ts`:

```ts
// Before
export interface StructuredSearchCriteria {
  query?: string;
  keys?: string[];
  bpmRange?: "slow" | "moderate" | "fast";
  albums?: AlbumFilter[];
}

// After
export interface StructuredSearchCriteria {
  query?: string;
  keys?: string[];
  bpmRange?: BpmBandKey[];      // empty/undefined = no BPM filter; one or more = OR match
  albums?: AlbumFilter[];
}
```

Also remove the duplicated `BPM_BANDS` declaration from this file; import from `@/lib/constants`.

### State change — `BrowseSheet`

`selectedBpm` changes from `BpmBandKey | undefined` to `BpmBandKey[]`:

```ts
const [selectedBpm, setSelectedBpm] = useState<BpmBandKey[]>([]);
```

The `onSelectedBpmChange` prop on `SharedFilters` (and any consumer) becomes `(next: BpmBandKey[]) => void`.

### `SongSearch` prop update

`SongSearch.tsx` currently has `selectedBpm?: BpmBandKey` (single). Update to `selectedBpm?: BpmBandKey[]` to keep prop types consistent across the component tree. The `hasAdvancedFilters` logic and `onAdvancedSearch` call site must check `selectedBpm.length > 0` instead of `selectedBpm !== undefined`.

### API serialization

`BrowseSheet.handleSearch` (keyword path, lines ~132-150) must emit repeated `bpmRange` params (one per selected band) instead of a single `bpmRange`:

```ts
selectedBpm.forEach((band) => params.append("bpmRange", band));
```

`SemanticSearch.tsx` (semantic path, lines ~104-119) must send `bpmRange: BpmBandKey[]` (an array) in the JSON body instead of a single string.

### Backend (READ BEFORE IMPLEMENTING)

The keyword search API endpoint that consumes these params must accept multiple `bpmRange` query params and OR-match across bands. **Action required**: locate the route handler (likely under `delivery/webapp/src/app/api/songs/search/route.ts` or similar), check how it currently parses `bpmRange` (single value via `params.get("bpmRange")`), and update to `params.getAll("bpmRange")` with OR semantics. If the underlying DB query is `bpmRange = ?`, change to `bpmRange IN (?)`. Verify against the existing BPM band SQL/Prisma logic before editing.

The semantic search handler that reads the JSON body must likewise accept `bpmRange` as a string array.

If the backend already accepts multi-valued `bpmRange` (unlikely), this is a no-op.

## Component Refactor

### Extract two new dropdown components

Following the `AlbumMultiSelect` pattern, create:

1. **`delivery/webapp/src/components/songset/MusicalKeyMultiSelect.tsx`**
   - Props: `{ selectedKeys: string[]; onSelectedKeysChange: (keys: string[]) => void; disabled?: boolean }`
   - Iterates over `PITCH_CLASSES` from `@/lib/constants`.
   - Implements the trigger summary logic described in Target UX.
   - Contains a "Clear all" item shown when `selectedKeys.length > 0`.

2. **`delivery/webapp/src/components/songset/BpmRangeMultiSelect.tsx`**
   - Props: `{ selectedBpm: BpmBandKey[]; onSelectedBpmChange: (bands: BpmBandKey[]) => void; disabled?: boolean }`
   - Iterates over `BPM_BAND_KEYS` from `@/lib/constants` and renders each item with `"label (rangeText)"` format (same as existing chips).
   - Implements the trigger summary logic described in Target UX (label only, no range text in trigger).

### Simplify `SharedFilters.tsx`

- Drop `showAdvanced` state, the toggle button, the panel wrapper, and `advancedFilterCount`/`hasAdvancedFilters`.
- Drop the `SlidersHorizontal` and `Badge` imports if no longer used.
- Render a top-level `flex flex-wrap gap-2` row containing `AlbumMultiSelect`, `MusicalKeyMultiSelect`, `BpmRangeMultiSelect`.
- Add a page-level "Clear all" inline button at the end of the filter row that resets **all three** filters (Album, Key, BPM) at once. This preserves the existing affordance from the removed panel-level Clear button.

### Test IDs (migration)

Old → new (update affected tests):

| Old | New |
|---|---|
| `advanced-filters-toggle` | removed |
| `advanced-filters-panel` | removed |
| `advanced-key-chips` | `key-filter` (trigger) + `key-option-{key}` (items) |
| `key-chip-{key}` | `key-option-{key}` (with `#` → `sharp`) |
| `advanced-bpm-chips` | `bpm-filter` (trigger) + `bpm-option-{band}` (items) |
| `bpm-chip-{band}` | `bpm-option-{band}` |

## Test Plan

Component / integration tests under `delivery/webapp/src/components/songset/` and `delivery/webapp/__tests__/` (or wherever the existing Search Songs tests live). Update existing tests that referenced `advanced-*` test IDs and add:

- `MusicalKeyMultiSelect`
  - Renders `"All Musical Keys"` summary when empty.
  - Opens dropdown, selects 1 key → summary shows `"C"`; selects a second non-conflicting key → `"C, D"`.
  - Selecting 3+ keys → summary shows first two + `+N` overflow.
  - "Clear all" item appears only when ≥1 selected and resets to empty.
- `BpmRangeMultiSelect`
  - Renders `"All BPM Ranges"` summary when empty.
  - Selecting one band → summary shows `"Slow"` (label only, no range text).
  - Selecting multiple → comma-joined labels, e.g. `"Slow, Fast"`.
  - Selecting all three → all three labels rendered (no overflow collapse for ≤3 bands).
  - Deselecting an active band removes it (multi-select toggle).
- `SharedFilters`
  - No `advanced-filters-toggle`, no panel.
  - All three dropdowns rendered at top level as siblings.
  - Page-level "Clear all" button resets all three filters.
- `BrowseSheet` integration
  - Keyword search emits repeated `bpmRange` params when multiple bands selected.
  - Semantic search sends `bpmRange` array in JSON body.
  - Clearing filters resets `selectedBpm` to `[]`.
- Backend route
  - Accepts repeated `bpmRange` params; returns OR-matched results.

Run: `pnpm --filter sow-webapp test && pnpm --filter sow-webapp lint`.

## Implementation Order

1. Update `StructuredSearchCriteria` type + remove duplicate `BPM_BANDS` from `search/types.ts`.
2. Investigate and (if needed) update the backend `/api/songs/search` route + semantic search handler to accept multi-valued `bpmRange` with OR semantics.
3. Add a `formatBpmBandRangeText(band: BpmBandKey): string` helper (extract from existing inline logic in `SharedFilters.tsx`).
4. Create `MusicalKeyMultiSelect.tsx` and `BpmRangeMultiSelect.tsx` (mirror `AlbumMultiSelect`).
5. Refactor `SharedFilters.tsx` to drop Advanced panel and render the three dropdowns as siblings, with a page-level "Clear all".
6. Update `BrowseSheet.tsx`: change `selectedBpm` to `BpmBandKey[]`, update both search paths' serialization.
7. Update `SongSearch.tsx`: change `selectedBpm` prop to `BpmBandKey[]`, update `hasAdvancedFilters` logic.
8. Update `SemanticSearch.tsx` JSON body shape.
9. Update/add tests; fix broken test IDs.
10. `pnpm --filter sow-webapp lint && pnpm --filter sow-webapp test`.
11. Manual smoke check via `pnpm --filter sow-webapp dev`.

## Risks & Open Items

- **Backend multi-value `bpmRange`**: must verify current parser behavior before assuming it needs changes.
- **Summary overflow threshold for Key**: spec'd as first 2 + `+N` (matches Album's 2-item preview). If a 3-width is preferred instead of `+N`, trivial to adjust.
- **Page-level Clear all**: clears all three filters (Album, Key, BPM) to preserve the existing global-reset affordance.

(End of file)
