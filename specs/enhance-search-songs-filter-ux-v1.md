# Plan: Enhance Webapp Search Songs Screen — Filter UX

## Context

The Search Songs screen is implemented as the `BrowseSheet` bottom sheet in the songset editor flow. Filters (Album, Key, BPM) are rendered via `SharedFilters`, which wraps three multi-select dropdown components: `AlbumMultiSelect`, `MusicalKeyMultiSelect`, and `BpmRangeMultiSelect`.

After clarifying questions, the following decisions were confirmed:
- Album format without series: omit empty parentheses — render `<Album Title> [<song_count>]`.
- Filter labels: stable category label (prominent) + dynamic value (secondary) inside each trigger. Category uses "All / N Selected / specific values" text.
- Clear mechanism: only inside the dropdown menus. Remove the external album selected-chips row and its X button.
- Trigger style: keep existing `variant="link"` button appearance, simply remove the `ChevronDown` icon.
- Layout: horizontal on desktop (`flex-wrap`), natural vertical stacking on mobile (already satisfied by current `flex-wrap`). Minor gap/spacing polish may be needed after removing chips.

---

## 1. Album Display Format Change

**File:** `src/lib/search/album-filter.ts`

**Current behavior:**
```ts
export function formatAlbumLabel(album: AlbumFilter): string {
  return album.albumSeries
    ? `${album.albumName} - ${album.albumSeries}`
    : album.albumName;
}
```

**Change:** Update `formatAlbumLabel` to wrap the series in parentheses instead of a dash. Also update `formatAlbumOptionLabel` to use `[songCount]` as before (no change needed there).

**New behavior:**
```ts
export function formatAlbumLabel(album: AlbumFilter): string {
  return album.albumSeries
    ? `${album.albumName} (${album.albumSeries})`
    : album.albumName;
}
```

- With series: `Hymns (Classic) [12]`
- Without series: `Worship [8]`

**Impact:** This function is used both for dropdown option labels and for the single-selected trigger text. The `[songCount]` is appended by `formatAlbumOptionLabel` for dropdown items only.

---

## 2. Remove ChevronDown Icons from All Three Filters

**Files:**
- `src/components/songset/AlbumMultiSelect.tsx`
- `src/components/songset/MusicalKeyMultiSelect.tsx`
- `src/components/songset/BpmRangeMultiSelect.tsx`

**Change:** In each component, remove the `<ChevronDown className="size-3.5 text-muted-foreground" />` element from the trigger `<Button>`. Keep the button `variant="link"`, `size="sm"`, and all existing click/keyboard behavior untouched.

---

## 3. Add Labeled Trigger Text to Each Filter

### Design Principle
Each trigger button contains **two inline spans**:
1. **Category label span** — stable, visually prominent (e.g., slightly darker / font-medium).
2. **Value span** — changes with selection, slightly muted (e.g., `text-muted-foreground`).

This gives "stable anchoring of label while filtered values may change" as requested.

---

### 3.1 Album Multi-Select Label

**File:** `src/components/songset/AlbumMultiSelect.tsx`

**Label rules:**

| State | Trigger text inside button |
|-------|---------------------------|
| No albums selected | `<Label>Albums:</Label> <Value>All {totalCount}</Value>` |
| 1 album selected | `<Label>Albums:</Label> <Value>{formatAlbumLabel(selected[0])}</Value>` |
| 2+ albums selected | `<Label>Albums,</Label> <Value>{count} Selected</Value>` |

- Example: `Albums: All 42`, `Albums: Hymns (Classic)`, `Albums, 3 Selected`

**Note:** Use a colon for 0 or 1 selected; use a comma for 2+ selected (matching the user's example exactly).

---

### 3.2 Musical Key Multi-Select Label

**File:** `src/components/songset/MusicalKeyMultiSelect.tsx`

**Label rules:**

| State | Trigger text inside button |
|-------|---------------------------|
| No keys selected | `<Label>Keys:</Label> <Value>All</Value>` |
| 1 key selected | `<Label>Keys:</Label> <Value>{key}</Value>` |
| 2 keys selected | `<Label>Keys:</Label> <Value>{k1}, {k2}</Value>` |
| 3+ keys selected | `<Label>Keys:</Label> <Value>{k1}, {k2}, +{n}</Value>` |

- Example: `Keys: All`, `Keys: E`, `Keys: E, A`, `Keys: E, A, +2`

Existing overflow logic (`+N`) should remain unchanged; only prepend `Keys:` label.

---

### 3.3 BPM Range Multi-Select Label

**File:** `src/components/songset/BpmRangeMultiSelect.tsx`

**Label rules:**

| State | Trigger text inside button |
|-------|---------------------------|
| No BPM selected | `<Label>BPM:</Label> <Value>All</Value>` |
| Any selected | `<Label>BPM:</Label> <Value>{label1}, {label2}, ...</Value>` |

- Example: `BPM: All`, `BPM: Slow`, `BPM: Slow, Moderate`

The BPM labels already join with commas for multiple selections; only prepend `BPM:` label.

---

## 4. Remove Album Filter Selected Chips / Summary Row

**File:** `src/components/songset/AlbumMultiSelect.tsx`

**Current behavior:** After the trigger button, when `selectedAlbums.length > 0`, a flex-wrapped summary row renders:
- Up to 2 selected album names (truncated).
- `+N more` overflow indicator.
- A small X icon button to clear all.

**Change:** Remove this entire summary row. Album clear-all functionality remains accessible **only inside the dropdown** via the existing `Clear all` menu item.

---

## 5. Layout & Spacing Polish

**File:** `src/components/songset/SharedFilters.tsx`

Current container:
```tsx
<div className="flex flex-wrap items-center gap-2">
```

**Assessment:** `flex-wrap` already provides horizontal layout on desktop and vertical stacking on mobile. After removing the album chips row (which added vertical height under the album trigger), the overall filter bar will be a single clean row of triggers.

**Potential tweak:** If the triggers feel cramped after text labels get longer, evaluate changing `gap-2` to `gap-3` or `gap-x-3 gap-y-2`. This is a minor Tailwind class adjustment to be tested visually.

**The `Clear all` page-level button** (rendered inside `SharedFilters`) should remain as-is; it appears only when at least one filter is active.

---

## 6. Album Ordering

**File:** `src/lib/db/songs.ts` (function `getAlbums`)

**Current behavior:**
```ts
.orderBy(songs.albumName, songs.albumSeries);
```

Albums are already returned alphabetically by `albumName` then `albumSeries` from the backend. The frontend maps the array directly, so dropdown items are already alphabetical.

**Action:** Verify behavior is correct; **no code change required** for ordering.

---

## 7. Test Updates

### 7.1 `src/test/components/songset/SharedFilters.test.tsx`

**Update these existing assertions:**
- Line 39: `All 3 Albums` → expect `Albums: All 3` (or `Albums: All 42` with real data).
- Line 61: `All Musical Keys` → expect `Keys: All`.
- Line 62: `All BPM Ranges` → expect `BPM: All`.
- Line 75: `Hymns - Classic [12]` → expect `Hymns (Classic) [12]` (album option label format change).
- Line 92–98: **Remove** the test `shows only album names in the selected summary` entirely (chips are removed).
- Line 128: `C` → expect `Keys: C`.
- Line 133: `C, D` → expect `Keys: C, D`.
- Line 138: `C, D, +2` → expect `Keys: C, D, +2`.
- Line 184–188: `Slow` → expect `BPM: Slow`.
- Line 192: `Slow, Fast` → expect `BPM: Slow, Fast`.
- Line 197: `Slow, Moderate, Fast` → expect `BPM: Slow, Moderate, Fast`.

**Note:** The album trigger text also changes when albums are selected:
- 1 selected: was `formatAlbumLabel(album)` → now `Albums: ` + `formatAlbumLabel(album)`.
- 2+ selected: was `{count} Albums` → now `Albums, {count} Selected`.
Update any assertions that checked the old selected album trigger text.

### 7.2 `src/test/components/songset/BrowseSheet.test.tsx`

Most assertions use `data-testid` (e.g., `getByTestId("album-filter")`) rather than text content, so minimal changes are expected. However:
- Review any assertions that check rendered text inside the filter triggers and update to the new `Albums: ...`, `Keys: ...`, `BPM: ...` formats.

---

## 8. Files to Modify (Summary)

| File | Change |
|------|--------|
| `src/lib/search/album-filter.ts` | Update `formatAlbumLabel` to use `()` for series |
| `src/components/songset/AlbumMultiSelect.tsx` | Remove ChevronDown; remove selected chips row; update trigger text format with label |
| `src/components/songset/MusicalKeyMultiSelect.tsx` | Remove ChevronDown; update trigger text format with `Keys:` label |
| `src/components/songset/BpmRangeMultiSelect.tsx` | Remove ChevronDown; update trigger text format with `BPM:` label |
| `src/components/songset/SharedFilters.tsx` | Review/adjust `gap-*` spacing if needed after chips removal |
| `src/components/search/SemanticSearch.tsx` | Replace `<Textarea>` with `<Input>`; update keyboard handler; update help text |
| `src/test/components/songset/SharedFilters.test.tsx` | Update text assertions; remove chips test |
| `src/test/components/songset/BrowseSheet.test.tsx` | Update any text content assertions in filter triggers |
| `src/test/components/search/SemanticSearch.test.tsx` | Update test names and assertions for single-line input behavior |

---

## 9. Verification Steps

1. Run unit tests: `pnpm test -- src/test/components/songset/SharedFilters.test.tsx src/test/components/songset/BrowseSheet.test.tsx`
2. Run linter: `pnpm lint`
3. Manual visual check:
   - Open songset editor -> Add Song -> verify filters are horizontal on desktop and stack on narrow widths.
   - Verify album dropdown options show e.g., `Hymns (Classic) [12]` and `Worship [8]`.
   - Verify no ChevronDown icons on any filter.
   - Verify album chips do not appear after selecting albums.
   - Verify `Clear all` still works inside each dropdown and at the page level.
   - Verify trigger text reads: `Albums: All 42`, `Keys: All`, `BPM: All` when nothing selected; `Albums: Hymns (Classic)` when 1 album selected; `Albums, 3 Selected` when 2+ albums selected.
