# Plan: Enhance Webapp Search Songs Screen — Filter UX (v2)

> Supersedes `enhance-search-songs-filter-ux-v1.md`. Incorporates review feedback:
> consistent separator (always colon), subtle chevron instead of removal,
> shortened Describe placeholder, missing positive trigger-text test coverage,
> explicit `totalCount` definition, pinned spacing, and replacement test for
> Enter-without-Ctrl case.

## Context

The Search Songs screen is implemented as the `BrowseSheet` bottom sheet in the songset editor flow. Filters (Album, Key, BPM) are rendered via `SharedFilters`, which wraps three multi-select dropdown components: `AlbumMultiSelect`, `MusicalKeyMultiSelect`, and `BpmRangeMultiSelect`.

Confirmed decisions (from v1 review):
- Album format with series: wrap series in parentheses — render `<AlbumName> (<albumSeries>) [<song_count>]`. Without series: `<AlbumName> [<song_count>]`.
- Filter labels: stable category label (prominent) + dynamic value (secondary) inside each trigger. Category uses "All / N Selected / specific values" text. **Separator is always a colon** for all filters and all states.
- Clear mechanism: only inside the dropdown menus. Remove the external album selected-chips row and its X button.
- Trigger style: keep existing `variant="link"` button appearance and **retain a subdued ChevronDown icon** (smaller size, lower opacity) for dropdown affordance, since the link styling provides no other visual cue.
- Layout: horizontal on desktop (`flex-wrap`), natural vertical stacking on mobile. Spacing pinned to `gap-x-3 gap-y-2`.

---

## 1. Album Display Format Change

**File:** `src/lib/search/album-filter.ts`

**Current behavior (verified):**
```ts
export function formatAlbumLabel(album: AlbumFilter): string {
  return album.albumSeries
    ? `${album.albumName} - ${album.albumSeries}`
    : album.albumName;
}
```

**Change:** Wrap the series in parentheses instead of using a dash. No change to `formatAlbumOptionLabel` (it appends `[songCount]` for dropdown items, which is still desired).

**New behavior:**
```ts
export function formatAlbumLabel(album: AlbumFilter): string {
  return album.albumSeries
    ? `${album.albumName} (${album.albumSeries})`
    : album.albumName;
}
```

- With series (option label): `Hymns (Classic) [12]`
- Without series (option label): `Worship [8]`
- Single-selected trigger value (no count): `Hymns (Classic)` / `Worship`

**Impact:** This function is used both for dropdown option labels (via `formatAlbumOptionLabel`) and for the single-selected trigger text. The `[songCount]` suffix remains dropdown-only.

---

## 2. Subdue ChevronDown Icons on All Three Filters

**Files:**
- `src/components/songset/AlbumMultiSelect.tsx` (line ~76)
- `src/components/songset/MusicalKeyMultiSelect.tsx` (line ~71)
- `src/components/songset/BpmRangeMultiSelect.tsx` (line ~72)

**Change:** In each component, keep the `<ChevronDown />` element but reduce its size and opacity. Replace:

```tsx
<ChevronDown className="size-3.5 text-muted-foreground" />
```

with:

```tsx
<ChevronDown className="size-3 text-muted-foreground/60" />
```

Rationale: the trigger uses `variant="link"` (no border, no background). Removing the chevron entirely hides the dropdown affordance. A small muted chevron preserves discoverability while reducing visual weight versus the label text.

Keep the button `variant="link"`, `size="sm"`, and all existing click/keyboard behavior untouched.

---

## 3. Add Labeled Trigger Text to Each Filter

### Design Principle

Each trigger button contains **two inline spans**:
1. **Category label span** — stable, visually prominent (e.g. `font-medium`).
2. **Value span** — changes with selection, slightly muted (e.g. `text-muted-foreground`).

Separator convention: **always a colon**, immediately following the category label, for all filters and all states. (Previously considered a comma for "2+ selected" — dropped for consistency with `Keys:` / `BPM:`.)

For all three filters, wrap each trigger's text content in a single span that combines both inner spans:

```tsx
<span className="max-w-[18rem] truncate whitespace-nowrap">
  <span className="font-medium">Albums:</span>{" "}
  <span className="text-muted-foreground">{triggerValue}</span>
</span>
```

Note the `whitespace-nowrap` addition — prevents mid-word wrap on narrow viewports so truncation cuts cleanly at the `max-w-[18rem]` boundary.

---

### 3.1 Album Multi-Select Label

**File:** `src/components/songset/AlbumMultiSelect.tsx`

**Label rules:**

| State | Category | Value text |
|-------|----------|------------|
| No albums selected | `Albums:` | `All {albums.length}` |
| 1 album selected | `Albums:` | `{formatAlbumLabel(selected[0])}` |
| 2+ albums selected | `Albums:` | `{count} Selected` |

Examples:
- `Albums: All 42`
- `Albums: Hymns (Classic)`
- `Albums: 3 Selected`

**Definition of `totalCount`:** There is no separate prop. The total album count is `albums.length` — i.e. the number of `AlbumOption` entries returned by `getAlbums()` and passed into the component by `SharedFilters`. Do **not** introduce a new prop or computed total; reference `albums.length` directly.

**Existing trigger logic to replace (lines 56-61):**

```tsx
const triggerText =
  selectedAlbums.length === 0
    ? `All ${albums.length} Albums`
    : selectedAlbums.length === 1
      ? formatAlbumLabel(selectedAlbums[0])
      : `${selectedAlbums.length} Albums`;
```

**New logic:**

```tsx
const triggerValue =
  selectedAlbums.length === 0
    ? `All ${albums.length}`
    : selectedAlbums.length === 1
      ? formatAlbumLabel(selectedAlbums[0])
      : `${selectedAlbums.length} Selected`;
```

Then render the labeled spans using `triggerValue` (see Design Principle).

Also remove the unused `summary` / `overflowCount` variables (lines 54-55) once the chips row is removed in Section 4.

---

### 3.2 Musical Key Multi-Select Label

**File:** `src/components/songset/MusicalKeyMultiSelect.tsx`

**Label rules:**

| State | Category | Value text |
|-------|----------|------------|
| No keys selected | `Keys:` | `All` |
| 1 key selected | `Keys:` | `{key}` |
| 2 keys selected | `Keys:` | `{k1}, {k2}` |
| 3+ keys selected | `Keys:` | `{k1}, {k2}, +{n}` |

Examples:
- `Keys: All`
- `Keys: E`
- `Keys: E, A`
- `Keys: E, A, +2`

The existing `triggerText` logic (sorted keys, `+N` overflow at 3+) remains unchanged — only prepend the `Keys:` category label span and the value span. Replace:

```tsx
<span className="max-w-[18rem] truncate">{triggerText}</span>
```

with:

```tsx
<span className="max-w-[18rem] truncate whitespace-nowrap">
  <span className="font-medium">Keys:</span>{" "}
  <span className="text-muted-foreground">{triggerText}</span>
</span>
```

---

### 3.3 BPM Range Multi-Select Label

**File:** `src/components/songset/BpmRangeMultiSelect.tsx`

**Label rules:**

| State | Category | Value text |
|-------|----------|------------|
| No BPM selected | `BPM:` | `All` |
| Any selected | `BPM:` | `{label1}, {label2}, ...` |

Examples:
- `BPM: All`
- `BPM: Slow`
- `BPM: Slow, Moderate`

The existing `triggerText` logic (comma-joined band labels) remains unchanged — only prepend the `BPM:` category label span and the value span. Replace:

```tsx
<span className="max-w-[18rem] truncate">{triggerText}</span>
```

with:

```tsx
<span className="max-w-[18rem] truncate whitespace-nowrap">
  <span className="font-medium">BPM:</span>{" "}
  <span className="text-muted-foreground">{triggerText}</span>
</span>
```

---

## 4. Remove Album Filter Selected Chips / Summary Row

**File:** `src/components/songset/AlbumMultiSelect.tsx`

**Current behavior (verified, lines 108-127):** After the trigger button, when `selectedAlbums.length > 0`, a `flex flex-wrap` summary row renders:
- Up to 2 selected album names (truncated).
- `+N more` overflow indicator.
- A small X icon button (`data-testid="album-summary-clear"`) to clear all.

**Change:** Remove the entire summary row JSX block. Album clear-all functionality remains accessible **only inside the dropdown** via the existing `Clear all` menu item (`data-testid="album-clear-all"`, line 84), which is unchanged.

Also delete the now-unused `summary` and `overflowCount` constant declarations near the top of the component (lines 54-55).

---

## 5. Layout & Spacing Polish

**File:** `src/components/songset/SharedFilters.tsx`

**Current container (verified, line 41):**

```tsx
<div className="flex flex-wrap items-center gap-2">
```

**Change:** Pin the spacing decision (do not defer to "visual evaluation"):

```tsx
<div className="flex flex-wrap items-center gap-x-3 gap-y-2">
```

**Acceptance criterion:** At 375px viewport width, no two adjacent triggers visually touch; row wrap occurs between triggers, not mid-text. Horizontal gap `gap-x-3` provides breathing room on desktop; `gap-y-2` keeps wrapped rows from creating excessive vertical height on mobile.

**The page-level `Clear all` button** (rendered inside `SharedFilters`, lines 63-74) should remain unchanged; it appears only when at least one filter is active.

---

## 6. Album Ordering

**File:** `src/lib/db/songs.ts` (function `getAlbums`, verified at line 409)

**Current behavior (verified, line 425):**

```ts
.orderBy(songs.albumName, songs.albumSeries);
```

Albums are already returned alphabetically by `albumName` then `albumSeries` from the backend. The frontend maps the array directly, so dropdown items are already alphabetical.

**Action:** Verify behavior is correct; **no code change required** for ordering.

---

## 7. Test Updates

### 7.1 `src/test/components/songset/SharedFilters.test.tsx`

**Update these existing assertions (verified against actual file):**

| Line | Current assertion | New assertion |
|------|------------------|---------------|
| 39 | `toHaveTextContent("All 3 Albums")` | `toHaveTextContent("Albums: All 3")` |
| 61 | `toHaveTextContent("All Musical Keys")` | `toHaveTextContent("Keys: All")` |
| 62 | `toHaveTextContent("All BPM Ranges")` | `toHaveTextContent("BPM: All")` |
| 75 | `getByText("Hymns - Classic [12]")` | `getByText("Hymns (Classic) [12]")` |
| 128 | `toHaveTextContent("C")` | `toHaveTextContent("Keys: C")` |
| 133 | `toHaveTextContent("C, D")` | `toHaveTextContent("Keys: C, D")` |
| 138 | `toHaveTextContent("C, D, +2")` | `toHaveTextContent("Keys: C, D, +2")` |
| 186 | BPM one selected: `toHaveTextContent("Slow")` | `toHaveTextContent("BPM: Slow")` |
| 192 | BPM two selected: `toHaveTextContent("Slow, Fast")` | `toHaveTextContent("BPM: Slow, Fast")` |
| 197 | BPM three selected: `toHaveTextContent("Slow, Moderate, Fast")` | `toHaveTextContent("BPM: Slow, Moderate, Fast")` |

**Remove entirely:**
- Lines 92-98: test `"shows only album names in the selected summary"` (chips row is removed; `data-testid="album-selected-summary"` no longer exists).

**Add new positive assertions** (replaces coverage lost by removing the chips test, and covers the new 1-album / 2+-album trigger branches that previously had no test):

```tsx
it("shows 'Albums: <label>' when one album selected", () => {
  renderFilters({ selectedAlbums: [hymns] });
  const trigger = screen.getByTestId("album-filter");
  expect(trigger).toHaveTextContent("Albums: Hymns (Classic)");
});

it("shows 'Albums: N Selected' when 2+ albums selected", () => {
  renderFilters({
    selectedAlbums: [hymns, { albumName: "Worship", albumSeries: null }],
  });
  expect(screen.getByTestId("album-filter")).toHaveTextContent("Albums: 2 Selected");
});
```

**Do not rename** any test purely for cosmetic reasons (e.g. leave `"renders the textarea"` and `"renders the input"` alone here — that note applies to SemanticSearch; see 7.3). Only rename or rewrite tests where behavior实际上 changed.

---

### 7.2 `src/test/components/songset/BrowseSheet.test.tsx`

**Verified:** Most assertions use `data-testid` (e.g. `getByTestId("album-filter")`) or URL pattern matchers (line 209), so minimal changes are expected. The `[12]` count appears in `mockAlbums` setup.

**Action:** Review assertions that check rendered text inside filter triggers and update to the new `Albums: ...`, `Keys: ...`, `BPM: ...` formats. No `data-testid` strings need to change.

---

### 7.3 `src/test/components/search/SemanticSearch.test.tsx`

**Update these existing assertions (verified against actual file):**

| Line | Current | Change |
|------|---------|--------|
| 143-148 | Test `"renders describe help text"` asserts text contains `Ctrl+Enter` | Update assertion to expect `Enter` (substring) instead of `Ctrl+Enter`. |
| 310-324 | Test `"triggers search on Ctrl+Enter"` fires `keyDown` with `ctrlKey: true` | Rename to `"triggers search on Enter"`. Fire `keyDown(input, { key: "Enter" })` with **no** `ctrlKey`. Expect `mockFetch` to be called. |

**Remove entirely:**
- Lines 326-333: test `"does not trigger search on Enter without Ctrl"` — this test becomes obsolete because plain Enter now triggers search.

**Add replacement test** (covers the new `Enter`-triggers-search branch and verifies `preventDefault` is invoked so future form wrapping doesn't break):

```tsx
it("fires search on plain Enter and calls preventDefault", async () => {
  mockFetch.mockResolvedValue({
    ok: true,
    json: () => Promise.resolve({ songs: [], query: "test", total: 0 }),
  });
  renderComponent();
  const input = screen.getByTestId("semantic-search-input");
  fireEvent.change(input, { target: { value: "worship songs" } });

  const preventDefault = vi.fn();
  fireEvent.keyDown(input, { key: "Enter", preventDefault });

  await waitFor(() => {
    expect(mockFetch).toHaveBeenCalled();
  });
  expect(preventDefault).toHaveBeenCalled();
});
```

**Skip the cosmetic rename** of `"renders the textarea"` (line 116) → `"renders the input"`. The test still passes; the `data-testid="semantic-search-input"` selector is unchanged. Renaming provides no coverage gain.

---

## 8. Describe Input — Change from Textarea to Single-line Input

**File:** `src/components/search/SemanticSearch.tsx`

**Current behavior (verified, lines 282-290):**
- Uses `<Textarea>` element.
- `className="min-h-[80px] resize-none"` (80px tall, multi-line).
- Help text (line 293) says: `... · Press Ctrl+Enter to search`.
- `handleKeyDown` is typed for `HTMLTextAreaElement` (line 174) and checks `e.ctrlKey || e.metaKey` (line 175).

**Problem:** The Describe input is much taller than the (single-line) Keyword input on the parallel content tab. Users typically enter only a short sentence, so the multi-line textarea is unnecessary overhead.

**Changes:**

1. **Import:** Replace
   ```tsx
   import { Textarea } from "@/components/ui/textarea";
   ```
   with
   ```tsx
   import { Input } from "@/components/ui/input";
   ```
   (Both `input.tsx` and `textarea.tsx` exist under `src/components/ui/` — verified.)

2. **Element swap:** Replace
   ```tsx
   <Textarea
     value={query}
     onChange={(e) => setQuery(e.target.value)}
     onKeyDown={handleKeyDown}
     placeholder="Describe the songs you're looking for... (e.g. '关于神的恩典的赞美诗' or 'upbeat praise songs about grace')"
     className="min-h-[80px] resize-none"
     aria-label="Describe songs to search for"
     data-testid="semantic-search-input"
   />
   ```
   with
   ```tsx
   <Input
     value={query}
     onChange={(e) => setQuery(e.target.value)}
     onKeyDown={handleKeyDown}
     placeholder="Describe songs by theme or feeling..."
     aria-label="Describe songs to search for"
     data-testid="semantic-search-input"
   />
   ```
   - **Shortened placeholder** (~35 chars): the previous ~90-char bilingual example was truncated with ellipsis on most single-line viewport widths. Examples remain visible in the help text below (which already contains `关于神的恩典与怜悯的赞美` + `upbeat praise songs about grace`).

3. **`handleKeyDown` update (lines 173-181):**
   - Change type parameter from `React.KeyboardEvent<HTMLTextAreaElement>` to `React.KeyboardEvent<HTMLInputElement>`.
   - On a single-line `<Input>`, plain `Enter` triggers search directly. Remove the `ctrlKey || metaKey` requirement.
   - **Keep** `e.preventDefault()` so the input remains immune to implicit form submission if a `<form>` is later wrapped around it.

   ```tsx
   const handleKeyDown = useCallback(
     (e: React.KeyboardEvent<HTMLInputElement>) => {
       if (e.key === "Enter") {
         e.preventDefault();
         handleSearch();
       }
     },
     [handleSearch]
   );
   ```

4. **Help text update (line 293):**
   - Change `· Press Ctrl+Enter to search` to `· Press Enter to search`.
   - Keep the bilingual example phrase (`关于神的恩典与怜悯的赞美`, `upbeat praise songs about grace`) and the `Tip:` prefix.

5. **Accessibility:** `data-testid="semantic-search-input"` and `aria-label="Describe songs to search for"` both remain unchanged so existing test selectors and screen-reader behavior continue working.

**Runtime note (positive):** Switching to plain `Enter` on a single-line `<Input>` means the iOS soft-keyboard "Go" key now fires search directly — previously the mobile path required Cmd/Ctrl, which mobile keyboards lack. This is a real mobile UX improvement.

---

## 9. Files to Modify (Summary)

| File | Change |
|------|--------|
| `src/lib/search/album-filter.ts` | `formatAlbumLabel` uses `()` for series instead of ` - ` |
| `src/components/songset/AlbumMultiSelect.tsx` | Subdue chevron (size-3 /60); remove selected chips row + unused `summary`/`overflowCount` vars; rewrite trigger as labeled spans with colons; drop `triggerText` in favor of `triggerValue` |
| `src/components/songset/MusicalKeyMultiSelect.tsx` | Subdue chevron; wrap trigger text with `Keys:` + value spans; add `whitespace-nowrap` |
| `src/components/songset/BpmRangeMultiSelect.tsx` | Subdue chevron; wrap trigger text with `BPM:` + value spans; add `whitespace-nowrap` |
| `src/components/songset/SharedFilters.tsx` | Container spacing `gap-2` → `gap-x-3 gap-y-2` |
| `src/components/search/SemanticSearch.tsx` | `<Textarea>` → `<Input>`; short placeholder; `handleKeyDown` type + Enter-only logic; help text `Ctrl+Enter` → `Enter` |
| `src/test/components/songset/SharedFilters.test.tsx` | Update 10 text assertions; remove 1 chips test; add 2 new positive album-trigger tests |
| `src/test/components/songset/BrowseSheet.test.tsx` | Review + update any trigger text-content assertions |
| `src/test/components/search/SemanticSearch.test.tsx` | Update help-text assertion (`Ctrl+Enter`→`Enter`); rename + rewrite Ctrl+Enter test; remove obsolete Enter-no-Ctrl test; add replacement Enter + preventDefault test |

---

## 10. Verification Steps

1. **Unit tests:**
   ```bash
   pnpm test -- src/test/components/songset/SharedFilters.test.tsx \
                  src/test/components/songset/BrowseSheet.test.tsx \
                  src/test/components/search/SemanticSearch.test.tsx
   ```

2. **Linter:**
   ```bash
   pnpm lint
   ```

3. **Manual visual check** — open songset editor → Add Song:
   - Filters are horizontal on desktop and stack on narrow widths (no two triggers touching at 375px).
   - Album dropdown options show e.g. `Hymns (Classic) [12]` and `Worship [8]`.
   - ChevronDown icons appear on all three filters, but smaller and lower-opacity than before (size-3 /60).
   - Album chips do not appear after selecting albums.
   - `Clear all` still works inside each dropdown and at the page level.
   - Trigger text reads (with no selection): `Albums: All 42`, `Keys: All`, `BPM: All`.
   - 1 album selected: `Albums: Hymns (Classic)`.
   - 2+ albums selected: `Albums: 3 Selected`.
   - Trigger truncation cuts at word boundary at 375px (e.g. `Albums: Hymns (Cla…`), no mid-word wrap.
   - Switch to "Describe" tab: input is single-line (same height as Keyword input).
   - Pressing `Enter` in the Describe input triggers search.
   - On iOS / mobile, the on-screen keyboard's "Go" key fires Describe search directly.

---

## 11. Out of Scope (Not in this plan)

- Changing trigger style away from `variant="link"` (kept for visual continuity).
- Adding a `totalCount` prop or computed count to `AlbumMultiSelect` (use `albums.length` directly).
- Reordering album list (already alphabetical from backend; verified).
- Renaming purely cosmetic test names that do not reflect behavioral changes.
- Anything outside the Search Songs / BrowseSheet / Describe tabs.

---

## Change Log vs v1

| # | Change from v1 | Rationale |
|---|----------------|-----------|
| 3.1 | Drop the comma-for-2+ rule; use colon always | Consistency with `Keys:` / `BPM:`; matches review decision |
| 2 | Keep ChevronDown, subdue instead of removing | `variant="link"` provides no other affordance; review decision |
| 3.x | Add `whitespace-nowrap` to trigger span | Prevents mid-word wrap at 375px |
| 3.1 | Define `totalCount` = `albums.length` (not a new prop) | Prevents implementer introducing an undocumented prop |
| 7.1 | Add 2 new positive album-trigger tests | Replaces coverage lost by removing chips test |
| 7.3 | Replace (not just remove) Enter-without-Ctrl test | Cover new Enter-triggers-search branch + assert `preventDefault` |
| 7.3 | Skip cosmetic rename `"renders the textarea"`→`"renders the input"` | Pure churn, no coverage gain |
| 8 | Shorten Describe placeholder to ~35 chars | Avoid ellipsis truncation of bilingual example on single-line input |
| 5 | Pin `gap-x-3 gap-y-2` with acceptance criterion | Removes "evaluate visually" ambiguity from spec |
