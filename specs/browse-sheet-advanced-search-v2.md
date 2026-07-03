# Browse Sheet — Expanded Search with Structured Filters

## Goal

Expand the existing **Browse** tab in `BrowseSheet.tsx` with an optional collapsible panel of structured metadata filters: musical key (multi-select chips) and BPM range (single-select chips). The keyword search and album single-select remain as the primary UI; advanced filters are hidden behind a toggle to avoid overwhelming casual users.

Remove the previously planned **Advanced** third tab and the **Theme** semantic filter from Browse entirely. Semantic/theme search stays in the **Describe** tab only.

> Future: Enhance the **Describe** tab to extract structured metadata (Key, BPM/Pace, Duration) from natural-language prompts and use them to filter results alongside semantic theme search. That is out of scope for this plan.

---

## User Example

> "slow song with D or A key"

Translation into structured criteria (all ANDed):
- Musical Key: chips `D`, `A` selected (match against `recordings.musical_key`, pitch-class only)
- BPM Range: `slow` (< 90)

---

## Decisions (confirmed with user)

| Decision | Choice |
|---|---|
| UI placement | **Expand Browse tab** — keyword + album select remain primary; structured filters in a collapsible panel below |
| Theme/semantic | **Removed from Browse** entirely; stays in Describe tab only |
| BPM bands | Slow < 90, Moderate 90–120, Fast > 120 (presets as chips; single-select or none) |
| Album multi-select | **Removed** — single album `<Select>` is sufficient; multi-select adds confusion |
| Musical key filter | 12 pitch-class chips (C, C#, D, D#, E, F, F#, G, G#, A, A#, B); multi-select; match by normalized pitch-class prefix of `recordings.musical_key` |
| Result ranking when structured filters active | Alphabetical by title (same as existing browse) |
| Fallback on empty keyword + filters | Same as today — list all songs (respecting filters) via `/api/songs` |

---

## Architecture Overview

```
BrowseSheet.tsx
 ├─ Tab "Browse"     → SongSearch (expanded)
 │                      ├─ Keyword input (existing)
 │                      ├─ Album <Select> (existing)
 │                      ├─ "Advanced filters" toggle
 │                      └─ Collapsible panel (NEW)
 │                           ├─ Musical Key: toggle chips
 │                           └─ BPM Range: toggle chips
 │                      ↓ onSearch(StructuredSearchCriteria)
 │                   handleSearch() in BrowseSheet
 │                      ↓ GET /api/songs?... or GET /api/songs/search?...
 │                   results: SongCardData[] (reuses existing render)
 │
 └─ Tab "Describe"   → SemanticSearch (existing, unchanged)
```

The expanded search reuses the existing results rendering section (`SongCard`, `handleAddSong`, `handlePlaySong`, etc.) — it only adds filter state to `SongSearch` and extends the API call with additional query parameters.

---

## Schema & Data Notes

- **BPM** lives on `recordings.tempo_bpm` (NOT on `songs`). Each song can have multiple recordings; the query joins/filter through recordings.
- **Musical key** lives on both `songs.musical_key` (often null) and `recordings.musical_key` (analysis-derived, authoritative). Query against `recordings.musical_key`; fall back to `songs.musical_key` only if recordings value is null (handled in API response mapping, not query).
- **Albums** are returned by `GET /api/songs/albums` as a flat alphabetical `string[]`.
- `albumSeries` exists in schema and `listSongs` filters but is out of scope for v1.
- Existing primitives available: `checkbox.tsx`, `badge.tsx`, `select.tsx`, `button.tsx`, `input.tsx`. **Missing**: `popover`, `command`, `accordion`, `collapsible` — not needed for v1 (simple conditional rendering is sufficient).

---

## File-by-File Implementation Plan

### 1. Shared types — NEW: `src/components/songset/search/types.ts`

```ts
export interface StructuredSearchCriteria {
  query?: string;              // free-text, ILIKE %title% / composer / lyricist / album (existing)
  keys?: string[];             // pitch classes: "C".."B"; empty = any key
  bpmRange?: "slow" | "moderate" | "fast";   // undefined = any tempo
}
```

Export BPM band constants:
```ts
export const BPM_BANDS = {
  slow:     { label: "Slow", max: 90 },
  moderate: { label: "Moderate", min: 90, max: 120 },
  fast:     { label: "Fast", min: 120 },
} as const;

export const BPM_BAND_KEYS = ["slow", "moderate", "fast"] as const;
export type BpmBandKey = (typeof BPM_BAND_KEYS)[number];
```

### 2. Constants — `src/lib/constants.ts` (append)

Add the 12 pitch classes:
```ts
export const PITCH_CLASSES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"] as const;
export type PitchClass = (typeof PITCH_CLASSES)[number];
```

### 3. UI component — MODIFY: `src/components/songset/SongSearch.tsx`

Expand `SongSearch` to accept a new `onAdvancedSearch` prop and render a collapsible panel.

**New props:**
```ts
interface SongSearchProps {
  onSearch: (query: string, albumFilter?: string) => void;           // existing (for backward compat)
  onAdvancedSearch?: (criteria: StructuredSearchCriteria) => void;   // NEW
  albums: string[];
  isLoading?: boolean;
  className?: string;
  placeholder?: string;
  debounceMs?: number;
  initialQuery?: string;
}
```

**State additions:**
```ts
const [showAdvanced, setShowAdvanced] = useState(false);
const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
const [selectedBpm, setSelectedBpm] = useState<BpmBandKey | undefined>();
```

**Behavior:**
- Keyword input + album `<Select>` remain unchanged (debounced search via `onSearch`).
- Below the album select, add a `<Button variant="ghost" size="sm">` with a `SlidersHorizontal` icon and text `"Advanced filters"`. Clicking toggles the panel.
- **Collapsible panel** (rendered when `showAdvanced` is true):
  1. **Musical Key** — `<div className="flex flex-wrap gap-1.5">` of `<Badge>`-styled toggle chips from `PITCH_CLASSES`. Multi-select; selected = `variant="default"`, unselected = `variant="outline"`.
  2. **BPM Range** — three toggle chips: Slow / Moderate / Fast. Single-select (or none). Click again to deselect.
  3. **Actions** — "Apply filters" `<Button size="sm">` and "Clear all" `<Button variant="ghost" size="sm">`.
- The panel uses a subtle bordered container (`border rounded-md p-3 space-y-4`) to visually group it.

**Search orchestration:**
- When `onAdvancedSearch` is provided and any advanced filter is active, clicking "Apply filters" or changing keyword/album calls `onAdvancedSearch({ query: currentQuery, keys: selectedKeys, bpmRange: selectedBpm })`.
- To avoid two simultaneous searches, when `showAdvanced` is true **and** an advanced filter is active, the debounced keyword search should call `onAdvancedSearch` instead of `onSearch`.
- If `showAdvanced` is false or no advanced filters are active, behavior falls back to existing `onSearch`.

**Accessibility:** each row has a `<Label>` heading; chips use `aria-pressed`. `data-testid` hooks: `advanced-filters-toggle`, `advanced-key-chips`, `advanced-bpm-chips`, `advanced-apply-button`, `advanced-clear-button`.

### 4. API route — MODIFY: `src/app/api/songs/search/route.ts` (extend)

Current: `GET /api/songs/search?q=...&limit=...&offset=...&visibilityStatus=...`

Add optional query parameters:
```
&keys=D,A                    // comma-separated pitch classes
&bpmRange=slow|moderate|fast // single band
```

**Parsing rules:**
- `keys`: split by `,`, validate each against `PITCH_CLASSES`, max 12.
- `bpmRange`: validate against `BPM_BAND_KEYS`.

Pass parsed filters to an extended `fullTextSearchSongs` (see §5) or a new `structuredSearchSongs`.

> **Decision**: Extend the existing `GET` search endpoint rather than creating a new `POST` endpoint, because the Browse tab is inherently GET-oriented and the parameter set is simple. The existing `/api/songs` (list all) endpoint also gets the same parameters so that empty-keyword + filters still works.

### 5. API route — MODIFY: `src/app/api/songs/route.ts` (extend)

Same new query parameters as §4. Pass them to `listSongs` via an expanded `ListSongsFilters` interface.

### 6. DB helper — MODIFY: `src/lib/db/search.ts` (extend `fullTextSearchSongs`)

Add optional filters to the signature:
```ts
export async function fullTextSearchSongs(
  query: string,
  limit: number = 50,
  offset: number = 0,
  visibilityStatus?: string | string[],
  options?: {
    keys?: string[];
    bpmRange?: "slow" | "moderate" | "fast";
  }
): Promise<{ songs: SongWithRecordings[]; total: number }>
```

**Implementation approach** (ORM + `EXISTS` subqueries — consistent with existing code):
- After building the existing `whereConditions` array, append:
  - **Keys filter** (if `options.keys?.length`):
    ```ts
    sql`exists (
      select 1 from recordings r2
      where r2.song_id = ${songs.id}
        and r2.deleted_at IS NULL
        and r2.musical_key ~* ${buildKeyRegex(options.keys)}
    )`
    ```
    where `buildKeyRegex` produces `^(C#|D)(maj|major|minor|min)?\b` style regex (case-insensitive, anchored at start).
  - **BPM filter** (if `options.bpmRange`):
    ```ts
    sql`exists (
      select 1 from recordings r3
      where r3.song_id = ${songs.id}
        and r3.deleted_at IS NULL
        and r3.tempo_bpm IS NOT NULL
        and ${buildBpmPredicate(options.bpmRange)}
    )`
    ```
    where `buildBpmPredicate` returns raw SQL fragments: `< 90`, `between 90 and 120`, or `> 120`.
- The `recordings` `with:` clause should still return published/review recordings for display.
- Order remains `ts_rank_cd` DESC when `query` is non-empty; when `query` is empty but filters are active, order alphabetically by `lower(songs.title)`.

### 7. DB helper — MODIFY: `src/lib/db/songs.ts` (extend `listSongs` and `ListSongsFilters`)

Extend `ListSongsFilters`:
```ts
export interface ListSongsFilters {
  albumName?: string;
  albumSeries?: string;
  composer?: string;
  lyricist?: string;
  visibilityStatus?: string | string[];
  keys?: string[];             // NEW
  bpmRange?: "slow" | "moderate" | "fast";  // NEW
}
```

In `buildSongWhereClause`, add the same keys/BPM filter logic using `EXISTS` subqueries (reusing helper functions from `search.ts` if possible, or duplicating minimally).

### 8. DB helper — NEW: `src/lib/db/search-helpers.ts`

Extract shared filter-building utilities so both `search.ts` and `songs.ts` can reuse them:

```ts
export function buildKeyRegex(keys: string[]): string {
  const escaped = keys.map(k => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return `^(${escaped.join("|")})(maj|major|minor|min)?\\b`;
}

export function buildBpmPredicate(bpmRange: "slow" | "moderate" | "fast"): SQL {
  switch (bpmRange) {
    case "slow":     return sql`r.tempo_bpm < 90`;
    case "moderate": return sql`r.tempo_bpm >= 90 AND r.tempo_bpm < 120`;
    case "fast":     return sql`r.tempo_bpm >= 120`;
  }
}
```

### 9. Host integration — MODIFY: `src/components/songset/BrowseSheet.tsx`

Changes:
- No third tab. `SearchMode` stays `"browse" | "describe"`.
- Extend `handleSearch` to accept optional `StructuredSearchCriteria` and choose the right endpoint:
  ```ts
  const handleSearch = useCallback(
    async (searchQuery: string, album?: string, advanced?: StructuredSearchCriteria) => {
      setIsLoading(true);
      setError(null);

      try {
        if (advanced && (advanced.keys?.length || advanced.bpmRange)) {
          // Advanced search with filters
          const params = new URLSearchParams();
          if (searchQuery.trim()) params.set("q", searchQuery.trim());
          if (advanced.keys?.length) params.set("keys", advanced.keys.join(","));
          if (advanced.bpmRange) params.set("bpmRange", advanced.bpmRange);
          params.set("limit", "50");
          const url = searchQuery.trim()
            ? `/api/songs/search?${params.toString()}`
            : `/api/songs?${params.toString()}`;
          const response = await fetch(url);
          // ...handle response...
        } else {
          // Existing simple search path
          // ...existing logic...
        }
      } // ...catch/finally...
    }, []
  );
  ```
- Pass `onAdvancedSearch` to `SongSearch` (wrapping `handleSearch`).
- No new state variables needed for advanced results — they flow into existing `results`/`totalCount`/`isLoading`/`error`.
- Footer count line: works unchanged (uses `totalCount`).

### 10. Tests

- **API route:** `src/test/api/songs/search/route.test.ts` (extend existing or create new)
  - 401 without session
  - 200 with keyword + keys filter
  - 200 with keys + bpmRange intersection
  - 200 with keyword + keys + bpmRange (verifies AND semantics)
  - 200 with empty keyword but active filters (falls through to `/api/songs`)
  - 400 if invalid pitch class in `keys`
- **API route:** `src/test/api/songs/route.test.ts` (extend)
  - Same filter cases for the list endpoint.
- **DB helper:** `src/test/lib/db/search.test.ts` — create this file
  - Cases for `fullTextSearchSongs` with `options` (keys regex, bpm bands, combined filters).
- **UI:** Vitest + Testing Library for expanded `SongSearch`:
  - Renders advanced panel after toggle; chips toggle; apply calls `onAdvancedSearch` with assembled criteria; clear resets.
  - `BrowseSheet` test: applying advanced filters renders results after mock fetch; Add/Play handlers invoked from cards.

Run: `pnpm test`, `pnpm lint`.

---

## Edge Cases & Behavior

- **All filters empty + no keyword** → fallback to existing empty search behavior (list all songs).
  - **Album `<Select>` + Album checkbox panel both active** → N/A; album multi-select removed.
  - **No results** → show existing "No songs found" empty state. Consider adding a hint listing active filter count (e.g. "No songs match key D + slow tempo").
  - **Songset full** → cards disabled, footer shows "Songset full" (reuse `isSongsetFull`).
- **BPM inclusivity:** slow = bpm < 90 (exclusive); moderate = 90 ≤ bpm < 120; fast = bpm ≥ 120. Document in `BPM_BANDS`.
- **KeySpec regex:** handle both `C# maj` and `C#maj` and `c#` — `^(C#)` case-insensitive, anchored on the start of the trimmed key string.

---

## Migration & Constants Touchpoints

- `src/lib/constants.ts` — add `PITCH_CLASSES`, `BPM_BANDS`, `BPM_BAND_KEYS`.
- `src/components/songset/search/types.ts` — new shared types file.
- `src/lib/db/search-helpers.ts` — new shared DB filter builders.
- No DB schema changes (all columns/tables already exist).
- No Drizzle migration needed.

---

## Phased Delivery

1. **Phase 1 — Types & constants** (`types.ts`, `constants.ts`, `search-helpers.ts`).
2. **Phase 2 — DB helpers** Extend `fullTextSearchSongs` and `listSongs` with filter options (+ tests).
3. **Phase 3 — API routes** Extend `GET /api/songs/search` and `GET /api/songs` with filter params (+ tests).
4. **Phase 4 — UI panel** Expand `SongSearch` with collapsible advanced filters (+ unit tests).
5. **Phase 5 — BrowseSheet wiring** Pass `onAdvancedSearch`, wire `handleSearch` branching, empty-state copy polish.
6. **Phase 6 — E2E polish** a11y audit, responsive layout check.

Each phase independently committable.

---

## Open Questions (non-blocking; flag if encountered)

- Whether to surface `albumSeries` as a filter row in v1 (deferred — out of scope unless requested).
- Whether selecting a BPM band should also show a numeric readout (e.g. "< 90 BPM") for clarity.
- Whether the chips should also accept flat/sharp enharmonic equivalents (e.g. selecting `D#` also matches `Eb`). v1 keeps them strictly distinct; documented as a follow-up.
