# Browse Sheet — Advanced Search (Multi-Criteria AND)

## Goal

Add a third "Advanced" search tab to `BrowseSheet.tsx` that lets users combine multiple song-metadata criteria with AND semantics, including semantic (embedding-based) theme matching. Reuses the result list, add-to-songset, and audio-preview flows already present in the Browse tab.

## User Example

> "slow song from last 5 albums (checkbox) with D or A key, slow song, with theme of Redemption"

Translation into Advanced Search criteria (all ANDed):
- Albums: multi-select (checkbox list, sorted by name — album names already encode series order)
- Musical Key: chips `D`, `A` selected (match against `recordings.musical_key`, pitch-class only)
- BPM Range: `slow` (< 90)
- Theme: free-text `"Redemption"` → semantic embedding match against `song_embedding`

## Decisions (confirmed with user)

| Decision | Choice |
|---|---|
| UI placement | **Third tab** ("Advanced") next to Browse / Describe |
| Theme + structured combination | **New unified endpoint** `POST /api/songs/search/advanced` — server runs structured SQL AND semantic embedding, intersects ID sets |
| BPM bands | Slow < 90, Moderate 90–120, Fast > 120 (presets as chips; pre-defined bands, no custom slider for v1) |
| Album multi-select | Multi-select checkbox list, sorted by name (album names encode series index) |
| Musical key filter | 12 pitch-class chips (C, C#, D, D#, E, F, F#, G, G#, A, A#, B); match by normalized pitch-class prefix of `recordings.musical_key` |
| Result ranking when no theme | Alphabetical by title |
| Result ranking with theme | By semantic similarity DESC (cosine), tie-broken by title |

---

## Architecture Overview

```
BrowseSheet.tsx
 ├─ Tab "Browse"     → SongSearch (existing)
 ├─ Tab "Describe"   → SemanticSearch (existing)
 └─ Tab "Advanced"   → AdvancedSearchPanel (NEW)
                        ↓ onSearch(AdvancedSearchCriteria)
                      handleAdvancedSearch()
                        ↓ POST /api/songs/search/advanced
                      results: SongCardData[]  (reuses SongCard render + add/preview)
```

The new tab reuses the existing results rendering section (`SongCard`, `handleAddSong`, `handlePlaySong`, `isSongAdded`, etc.) — it only swaps the input panel and the API call. The result-list, audio-preview, footer, and "songset full" logic in `BrowseSheet` are shared.

---

## Schema & Data Notes

- **BPM** lives on `recordings.tempo_bpm` (NOT on `songs`). Each song can have multiple recordings; the analyzed recording is the first published one. The advanced query joins through recordings.
- **Musical key** lives on both `songs.musical_key` (often null) and `recordings.musical_key` (analysis-derived, the authoritative source). Query against `recordings.musical_key`, fall back to `songs.musical_key` only if recordings value is null.
- **Theme** has no dedicated column. The only semantic path is `song_embedding` (1536-dim, model-filtered by `QUERY_MODEL`) via cosine distance.
- **Albums** are returned by `GET /api/songs/albums` as a flat alphabetical `string[]`.
- `albumSeries` exists in schema and `listSongs` filters but is out of scope for v1.
- Existing primitives available: `checkbox.tsx`, `slider.tsx`, `badge.tsx`, `select.tsx`, `button.tsx`, `input.tsx`, `textarea.tsx`. **Missing**: `popover`, `command`, `accordion`, `collapsible` — not needed for v1 (inline panel is sufficient).

---

## File-by-File Implementation Plan

### 1. Shared types — NEW: `src/components/songset/advanced-search/types.ts`

```ts
export interface AdvancedSearchCriteria {
  title?: string;              // free-text, ILIKE %title% (also matches title_pinyin)
  albums?: string[];           // multi-select; empty/undefined = any album
  keys?: string[];             // pitch classes: "C".."B"; empty = any key
  bpmRange?: "slow" | "moderate" | "fast";   // undefined = any tempo
  theme?: string;              // free text; embedded and cosine-matched
}
```

Export BPM band constants here (or in `src/lib/constants.ts`):
```ts
export const BPM_BANDS = {
  slow:     { max: 90 },
  moderate: { min: 90, max: 120 },
  fast:     { min: 120 },
} as const;
```

### 2. Constants — `src/lib/constants.ts` (append)

Add the 12 pitch classes:
```ts
export const PITCH_CLASSES = ["C","C#","D","D#","E","F","F#","G","G#","A","A#","B"] as const;
export type PitchClass = (typeof PITCH_CLASSES)[number];
```

### 3. UI component — NEW: `src/components/songset/advanced-search/AdvancedSearchPanel.tsx`

A `"use client"` form panel rendered inside `BrowseSheet`'s Advanced tab. Layout is a vertical stack of filter rows inside a scrollable container (the sheet content already handles overflow).

**Rows:**

1. **Title** — `<Input>` with search icon (mirror `SongSearch` styling). Optional.
2. **Albums** — `<div className="flex flex-wrap gap-2">` of `<Checkbox>` + `<Label>` pairs, one per album (from `albums` prop, alphabetically sorted). "Select all"/"Clear" `<Button variant="ghost" size="sm">` helpers at the row header. If list is long, wrap in a `max-h-48 overflow-y-auto` scroll area styled like the album dropdown content.
3. **Musical Key** — `<div className="flex flex-wrap gap-1.5">` of `<Badge>`-styled toggle chips (use `button` with `Badge` look, or `Badge` with `onClick`). 12 pitch classes from `PITCH_CLASSES`. Multi-select; selected state = `variant="default"`, unselected = `variant="outline"`.
4. **BPM Range** — three toggle chips: Slow / Moderate / Fast. Single-select (or none). Click again to deselect.
5. **Theme** — `<Textarea>` (placeholder: "Theme, e.g. Redemption, grace, cross…"). Optional. On submit, the server embeds this.

**Actions row (sticky at bottom of panel):**
- `<Button data-testid="advanced-search-submit">` "Search" (disabled when all criteria empty)
- `<Button variant="ghost" size="sm">` "Clear all"
- Live criteria summary as small muted text: e.g. `"D, A · slow · 3 albums · theme: Redemption"` (built from current criteria)

**Props:**
```ts
interface AdvancedSearchPanelProps {
  albums: string[];
  isLoading: boolean;
  onSearch: (criteria: AdvancedSearchCriteria) => void;
  initialCriteria?: AdvancedSearchCriteria;
}
```

Call `onSearch(criteria)` only on explicit Search button click (NO debounce — advanced search is intentful). If `theme` is present, results are ranked by similarity; otherwise alphabetical.

**Accessibility:** each row has a `<Label>` heading; checkboxes use `<label>` wrappers; chips use `aria-pressed`. `role="group"` per criterion row. `data-testid` hooks: `advanced-search-panel`, `advanced-title-input`, `advanced-album-checkboxes`, `advanced-key-chips`, `advanced-bpm-chips`, `advanced-theme-input`, `advanced-search-submit`, `advanced-clear`.

### 4. Host integration — `src/components/songset/BrowseSheet.tsx`

Changes:
- Extend `type SearchMode = "browse" | "describe" | "advanced";`
- Add state:
  ```ts
  const [advancedCriteria, setAdvancedCriteria] = useState<AdvancedSearchCriteria>({});
  const [advancedResults, setAdvancedResults] = useState<SongCardData[] | null>(null);
  const [advancedTotal, setAdvancedTotal] = useState(0);
  const [isAdvancedLoading, setIsAdvancedLoading] = useState(false);
  const [advancedError, setAdvancedError] = useState<string | null>(null);
  const [advancedInitialCriteria, setAdvancedInitialCriteria] = useState<AdvancedSearchCriteria | undefined>();
  ```
- Add third tab button in the tablist: `<Sparkles>` or `<SlidersHorizontal>` icon + "Advanced", `data-testid="advanced-mode-tab"`.
- New handler:
  ```ts
  const handleAdvancedSearch = useCallback(async (criteria: AdvancedSearchCriteria) => {
    setAdvancedCriteria(criteria);
    setIsAdvancedLoading(true);
    setAdvancedError(null);
    try {
      const res = await fetch("/api/songs/search/advanced", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...criteria, limit: 50 }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => ({})).error) ?? "Advanced search failed");
      const data = await res.json();        // { songs: SongCardData(+similarity?)[]; total: number }
      setAdvancedResults(data.songs ?? []);
      setAdvancedTotal(data.total ?? 0);
    } catch (e) {
      setAdvancedError(e instanceof Error ? e.message : "Advanced search failed");
      setAdvancedResults([]); setAdvancedTotal(0);
    } finally {
      setIsAdvancedLoading(false);
    }
  }, []);
  ```
- Render `AdvancedSearchPanel` in `mode === "advanced"` panel, feeding it `albums`, `isAdvancedLoading`, `handleAdvancedSearch`, `advancedInitialCriteria`.
- Reuse the existing results-render block but branch the source: when `mode === "advanced"`, render from `advancedResults` with `isAdvancedLoading`/`advancedError`/`advancedTotal`. The `SongCard`, `handleAddSong`, `handlePlaySong`, `isSongAdded`, `isSongAdding`, `isSongsetFull`, `playingSongId`, `previewLoadingSongId` are all reused unchanged.
- When embedding 503 is returned for theme-only failure, fall back by: clear `theme` from criteria, retry the structured-only search, and `toast.error("Theme search unavailable; showing structured results without theme filter.")`.
- Reset advanced state on sheet close (in the existing cleanup `useEffect`): set all advanced* states back to defaults and `setMode("browse")`.
- Footer count line: extend to `mode === "advanced"` → use `advancedTotal`.

### 5. API route — NEW: `src/app/api/songs/search/advanced/route.ts`

`POST`, auth-gated (mirror `semantic/route.ts`).

**Request schema** (Zod):
```ts
const RequestSchema = z.object({
  title:   z.string().trim().max(200).optional(),
  albums:  z.array(z.string().min(1).max(200)).max(100).optional(),
  keys:    z.array(z.enum(PITCH_CLASSES)).max(12).optional(),
  bpmRange: z.enum(["slow","moderate","fast"]).optional(),
  theme:   z.string().trim().min(1).max(500).optional(),
  limit:   z.number().int().min(1).max(50).default(50),
}).refine(d => d.title || d.albums?.length || d.keys?.length || d.bpmRange || d.theme,
  { message: "At least one filter is required" });
```

**Pipeline:**

1. Auth check (reuse pattern from sibling routes).
2. Validate body; default `limit`.
3. **Structured candidate set** — call a new DB helper `advancedSearchSongs` (see §6) with the structured filters (title, albums, keys, bpmRange). If no structured filters are set (theme only), structured set = all song IDs (with a cap, e.g. 5000 — see Open Questions).
4. **Theme set** (only if `theme` present):
   - `embedQuery(theme)` — catch and return `503` (`{ error: "Theme search unavailable" }`) so client can fall back to structured-only.
   - `semanticSearchSongs(embedding, QUERY_MODEL, limit * 4, ["published","review"])` — overfetch.
   - Keep only songs whose ID is in the structured candidate set (the AND intersection).
5. **If no theme**: rank structured results alphabetically by `title` (case-insensitive). Trim to `limit`.
6. **If theme**: attach `similarity` to each intersected song, sort by `similarity DESC`, tie-break `title ASC`. Trim to `limit`.
7. **Snippets**: only compute `findTopMatchingLines` when theme is active (informative match badges). Reuse the same snippet/rerank logic sparingly.
8. Return `{ songs: SongCardData(+ similarity?, matchingSnippet?, whyThisMatch?)[]; total: number }`.

Each returned song's `recordings[]` projection follows the existing `SongCardData` shape — at minimum include the published recording used for key/BPM matching, plus the playback fields (`hashPrefix`, `durationSeconds`, `tempoBpm`, `musicalKey`, `visibilityStatus`).

### 6. DB helper — NEW: `src/lib/db/search.ts` (append `advancedSearchSongs`)

```ts
export async function advancedSearchSongs(filters: {
  title?: string;
  albums?: string[];
  keys?: string[];
  bpmRange?: "slow" | "moderate" | "fast";
}, limit: number = 200): Promise<SongWithRecordings[]>
```

Implementation notes (Drizzle raw `sql` following the `semanticSearchSongs` pattern):
- `SELECT DISTINCT ON (s.id) ... FROM songs s JOIN recordings r ON r.song_id = s.id AND r.visibility_status = ANY(...) AND r.deleted_at IS NULL WHERE s.deleted_at IS NULL AND <criteria>`.
- **Title filter:** `(s.title ILIKE %q% OR s.title_pinyin ILIKE %q%)`.
- **Albums filter:** `s.album_name = ANY(ARRAY[...])`.
- **Keys filter:** normalize each candidate recording's `musical_key` to a pitch class and match: `r.musical_key IS NOT NULL AND left(trim(lower(r.musical_key)), 1 OR 2) ...`. Simpler approach: store normalized lookup in SQL: `lower(split_part(r.musical_key, ' ', 1))` matches `lower(key)` for natural-accidentals, plus map sharps (`C#` / `c#`). Concretely: `r.musical_key ~* '^(C#|D)'` when those keys selected — build the regex alternation from selected chips (`["C#","D"] → "^(C#|D)(maj|major|minor|min)?\\b"`).
- **BPM filter:** apply to `r.tempo_bpm`:
  - slow: `r.tempo_bpm IS NOT NULL AND r.tempo_bpm < 90`
  - moderate: `between 90 and 120` (inclusive lower, exclusive upper — document explicitly)
  - fast: `> 120`
  - Songs whose recording has NULL BPM are excluded only when `bpmRange` is set.
- Order: alphabetical by `lower(s.title)` when no theme; otherwise leave unranked (the route ranks by similarity).
- Cap intermediate result to e.g. 2000 rows to keep intersection tractable.
- Reuse existing projection to `SongWithRecordings` row mapping.

### 7. Tests

- **API route:** `src/test/api/songs/search/advanced.test.ts`
  - 401 without session
  - 400 if no criteria; 400 if invalid pitch class
  - 200 with title-only; verifies only matched songs
  - 200 with albums multi-select (intersect across albums)
  - 200 with keys + bpmRange intersection
  - 200 with theme (mock `embedQuery`); verifies similarity attached and ranked DESC
  - 503 when `embedQuery` throws (theme path)
  - Theme + structured: only songs passing BOTH filters appear
- **DB helper:** `src/test/lib/db/search.test.ts` add cases for `advancedSearchSongs` (title ILIKE, albums `ANY`, keys regex, bpm bands, no-filters returns recent cap).
- **UI:** Vitest + Testing Library for `AdvancedSearchPanel`:
  - Renders all rows; chips toggle; checkboxes toggle; submit disabled when empty; calls `onSearch` with assembled criteria; "Clear" resets.
  - `BrowseSheet` test extension: switching to Advanced tab renders panel; results render after mock fetch; Add/Play handlers invoked from cards.

Run: `pnpm test`, `pnpm lint`.

---

## Edge Cases & Behavior

- **All criteria empty** → Search button disabled; no fetch.
- **Theme without structured filters** → behaves like Describe mode but with explicit criteria UI (still goes through `/advanced` endpoint; effectively a theme-only search).
- **No results** → show the existing "No songs found" empty state with a hint listing the active criteria ("No songs matching D/A key + slow + Redemption").
- **Songset full** → cards disabled, footer shows "Songset full" (reuse `isSongsetFull`).
- **Embedding model mismatch / disabled** → 503 from route; client falls back to structured-only (drops theme) with a toast. Advanced tab stays usable for structured-only search.
- **Title text** is treated as ILIKE substring (not FTS) for predictability; revisit later if performance suffers (use `search_vector @@` as optimization).
- **BPM inclusivity:** slow = bpm < 90 (exclusive); moderate = 90 ≤ bpm < 120; fast = bpm ≥ 120. Document in `BPM_BANDS`.
- **KeySpec regex:** handle both `C# maj` and `C#maj` and `c#` — `^(C#)` case-insensitive, anchored on the start of the trimmed key string.

---

## Migration & Constants Touchpoints

- `src/lib/constants.ts` — add `PITCH_CLASSES`, optionally `BPM_BANDS`.
- No DB schema changes (all columns/tables/embeddings already exist).
- No Drizzle migration needed.

---

## Phased Delivery

1. **Phase 1 — Types & constants** (`types.ts`, `constants.ts`).
2. **Phase 2 — DB helper** `advancedSearchSongs` (+ tests).
3. **Phase 3 — API route** `POST /api/songs/search/advanced` (+ tests, mock embeddings).
4. **Phase 4 — UI panel** `AdvancedSearchPanel` (+ unit tests).
5. **Phase 5 — BrowseSheet wiring** (third tab, state, result render branch, 503 fallback, close-reset).
6. **Phase 6 — E2E polish** (empty-state copy, criteria summary, a11y audit).

Each phase independently committable.

---

## Open Questions (non-blocking; flag if encountered)

- Default `limit` for the ADV endpoint's structured-only result cap (proposed 2000). May need a `LIMIT` after the `DISTINCT ON` once Postgres proves an efficient plan.
- Whether to also surface `albumSeries` as a filter row in v1 (deferred — out of scope unless requested).
- Whether the Advanced tab should auto-populate a `theme` chip suggestion list from common categories (e.g. Grace, Redemption, Cross) — deferred to a future enhancement.
- Whether the chips should also accept flat/sharp enharmonic equivalents (e.g. selecting `D#` also matches `Eb`). v1 keeps them strictly distinct; documented as a follow-up.
