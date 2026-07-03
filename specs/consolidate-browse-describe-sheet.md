# Consolidate Search + Describe into Single Tab with Unified Filters

## 1. Problem Statement

`BrowseSheet.tsx` (to be renamed `SongSearchSheet.tsx`) currently renders two mutually‑exclusive tabs — **Browse** (keyword search via `SongSearch.tsx`) and **Describe** (semantic search via `SemanticSearch.tsx`). Each mode has divergent filter support:

| Capability | Keyword (`/api/songs` + `/api/songs/search`) | Describe (`/api/songs/search/semantic`) |
|---|---|---|
| Keyword query | ✅ | ❌ (description text) |
| Semantic / description query | ❌ | ✅ |
| Album filter | ⚠️ single‑select, list path only — `/api/songs/search` ignores `albumName` entirely | ❌ not supported |
| Musical key filter (chips) | ✅ | ❌ not supported |
| BPM range filter (chips) | ✅ | ❌ not supported |
| Help text / examples | placeholder only | placeholder only |

Goals from product:
1. Consolidate Search + Describe into **one tab** with a **segmented control** to switch between keyword and description/semantic search.
2. **Advanced filters (key, BPM, album) must apply for both modes.**
3. Album selection becomes **multi‑select** (instead of single‑select).
4. Add **contextual help text** with examples for keyword vs. semantic search.
5. Rename component `BrowseSheet` → `SongSearchSheet` and update user-facing strings from "Browse" → "Search"/"Keyword" (the user is searching the catalog, not browsing).

This requires backend extension because `semanticSearchSongs` and `/api/songs/search/semantic` accept only `{ query, limit }` — no filter parameters — and `/api/songs/search` (keyword) doesn't accept album filtering at all.

## 2. Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Tab structure | **Single tab**; segmented control swaps input + help text | User explicitly wants one tab; preserves both search modes |
| Mode selector UI | **Segmented control** (two `Button`s in a `role="tablist"`-like group) above the input | Matches existing tab styling; minimal layout disruption |
| Mode labels | **"Keyword"** / **"Describe"** | "Keyword" is more precise than "Browse" given the rich filters now available; distinguishes from the overarching sheet name |
| Mode state | `SearchMode = "keyword" \| "describe"` (renamed from `"browse"`) | Existing footer/counter logic reuses `mode` |
| Component rename | `BrowseSheet` → `SongSearchSheet`; file `BrowseSheet.tsx` → `SongSearchSheet.tsx`; interface `BrowseSheetProps` → `SongSearchSheetProps` | Reflects that the user is searching the catalog, not browsing |
| Help text | **Contextual** — swaps per mode; keyword shows song title examples, semantic shows lyrical/theme examples | Clear guidance without crowding UI |
| Album multi-select UI | **dropdown-menu + checkbox + selected badge chips** built from existing components | No new dependency; consistent with `dropdown-menu.tsx` / `checkbox.tsx` / `badge.tsx` already in `src/components/ui/` |
| Album param encoding | **Comma-separated `albumName` param** (e.g. `albumName=Album1,Album2`) | Matches existing `keys=...` convention; minimal API change. URL-encoding handled per-value. |
| Filter state location | Lift shared filter state (albums[], keys[], bpmRange) into `SongSearchSheet`; pass to whichever search component is active | Avoids drift between modes; keeps a single source of truth |
| Backend filter scope | **Full extension** of `semanticSearchSongs` + `/api/songs/search/semantic` + `fullTextSearchSongs` + `/api/songs/search` for albums/keys/bpm | Required to satisfy "advanced filters apply for both modes"; chosen over client-side post-filter (would skew pagination/limit + reduce accuracy for semantic) |
| Semantic filter application point | Apply filters **inside the `semanticSearchSongs` SQL** as additional `WHERE` clauses; filters applied before `LIMIT`/re-ranking so result count stays correct | True DB-level filtering; overfetch × 2 already happens so the limit window has room |

## 3. Architecture / Data Flow

```
SongSearchSheet (single panel)
  ├─ Segmented control [Keyword | Describe]
  ├─ Search input (Input for keyword, Textarea for describe) — swaps per mode
  ├─ Contextual help text (swaps per mode)
  ├─ AlbumMultiSelect (dropdown-menu + checkbox + badge chips)  ← new component
  ├─ Advanced filters (key chips, bpm chips) — shared
  └─ Results list (SongCard grid)

Mode = "keyword"
  → handleSearch(query, albums[], filters) → GET /api/songs?q=&albumName=A,B&keys=&bpmRange=&limit=50
Mode = "describe"
  → handleSemanticSearch(query, albums[], filters) → POST /api/songs/search/semantic { query, albums, keys, bpmRange, limit }
```

## 4. Implementation Plan

### Phase 0 — Rename BrowseSheet → SongSearchSheet

**0.1 Component file rename**
- `delivery/webapp/src/components/songset/BrowseSheet.tsx` → `SongSearchSheet.tsx`
- Component `BrowseSheet` → `SongSearchSheet`
- Interface `BrowseSheetProps` → `SongSearchSheetProps`

**0.2 Caller updates** — `delivery/webapp/src/app/songsets/[id]/SongsetEditorClient.tsx`
- Line 14-15: update dynamic import path + name (`@/components/songset/SongSearchSheet` → `m.SongSearchSheet`)
- Line 134, 145, 423: rename state `isBrowseSheetOpen` → `isSongSearchSheetOpen` (and setter)
- Line 527-529: update JSX `<BrowseSheet ...>` → `<SongSearchSheet ...>`

**0.3 Test updates**
- `delivery/webapp/src/test/components/songset/BrowseSheet.test.tsx` → rename file to `SongSearchSheet.test.tsx`; update import, `describe` block name, render calls
- `delivery/webapp/src/test/accessibility/accessibility.test.tsx:144,523,524,540,548,556` — update import path + component name; update `describe("BrowseSheet", ...)` → `describe("SongSearchSheet", ...)`

**0.4 User-facing string changes**

| Location | Old | New |
|---|---|---|
| Sheet title (`SheetTitle`) | "Browse Songs" | "Search Songs" |
| Sheet description | "Search and add songs to your songset" | "Search the catalog and add songs to your songset" |
| Segmented control label (keyword side) | "Browse" | "Keyword" |
| Segmented control label (semantic side) | "Describe" | "Describe" (unchanged) |
| `SearchMode` type | `"browse" \| "describe"` | `"keyword" \| "describe"` |
| `data-testid="browse-mode-tab"` | "browse-mode-tab" | "keyword-mode-tab" |
| Footer counter condition | `mode === "browse"` | `mode === "keyword"` |

### Phase 1 — Backend: extend filter support

**1.1 `StructuredSearchCriteria` type** — `delivery/webapp/src/components/songset/search/types.ts`
- Change `album?: string` → `albums?: string[]` (rename to plural).
- Update any consumer that references `criteria.album` (in `SongSearchSheet.handleSearch` and `SongSearch.triggerSearch`).

**1.2 `ListSongsFilters`** — `delivery/webapp/src/lib/db/songs.ts:47`
- Change `albumName?: string` → `albumNames?: string[]`.
- Update `buildSongWhereClause` (line ~116): replace `eq(songs.albumName, filters.albumName)` with `inArray(songs.albumName, filters.albumNames)` when array non-empty.

**1.3 `listSongs`** — `delivery/webapp/src/lib/db/songs.ts:168`
- Reads `filters.albumNames[]` (no other change).

**1.4 `fullTextSearchSongs`** — `delivery/webapp/src/lib/db/search.ts`
- Add `albums?: string[]` to `FullTextSearchOptions`.
- When non-empty push `inArray(songs.albumName, options.albums)` into `whereConditions`.

**1.5 `semanticSearchSongs`** — `delivery/webapp/src/lib/db/songs.ts:399`
- Add optional params: `albums?: string[]`, `keys?: string[]`, `bpmRange?: BpmBandKey`.
- Inside the inner `SELECT ... WHERE s.deleted_at IS NULL AND ...` block, append:
  - Albums: `AND s.album_name = ANY(ARRAY[...])` (filter to **only** matching albums, so plain `ANY`).
  - Keys: reuse `buildKeyRegex(keys)` → `AND EXISTS (SELECT 1 FROM recordings r2 WHERE r2.song_id = s.id AND r2.deleted_at IS NULL AND r2.musical_key ~* <regex>)`.
  - BPM: reuse `buildBpmPredicate(bpmRange, "r3")` → `AND EXISTS (SELECT 1 FROM recordings r3 WHERE r3.song_id = s.id AND r3.deleted_at IS NULL AND r3.tempo_bpm IS NOT NULL AND <bpm>)`.
- Visibility predicate on the main JOIN stays unchanged.
- Keep the existing overfetch (`limit * 2`) — filters reduce result set before `LIMIT`, so this still gives rerank headroom.

**1.6 `/api/songs` route** — `delivery/webapp/src/app/api/songs/route.ts`
- Parse `albumName` param: split on `,`, trim, de-dupe. Pass as `albumNames: string[]` into `listSongs`. Drop single-string assignment.
- Backward compatibility: if a single value is sent (legacy client), still works.

**1.7 `/api/songs/search` route** — `delivery/webapp/src/app/api/songs/search/route.ts`
- Add `albumName` parsing (comma-split, like 1.6).
- Pass `albums` into `fullTextSearchSongs(..., { keys, bpmRange, albums })`.

**1.8 `/api/songs/search/semantic` route** — `delivery/webapp/src/app/api/songs/search/semantic/route.ts`
- Extend `RequestSchema` with `albums: z.array(z.string()).optional()`, `keys: z.array(z.string()).optional()`, `bpmRange: z.enum(["slow","moderate","fast"]).optional()`.
- Pass validated values into `semanticSearchSongs(embedding, model, overfetchLimit, visibilityStatuses, { albums, keys, bpmRange })`.

### Phase 2 — Frontend: new AlbumMultiSelect component

**2.1 New file** `delivery/webapp/src/components/songset/AlbumMultiSelect.tsx`
- Props: `albums: string[]`, `selected: string[]`, `onChange: (next: string[]) => void`, `isLoading?: boolean`.
- Uses `DropdownMenu` (from `dropdown-menu.tsx`), `Checkbox` (from `checkbox.tsx`), `Badge` (from `badge.tsx`), `Button`.
- Trigger button: shows `"Albums"` + count badge when any selected.
- Dropdown: alphabetical list of `Checkbox`es, "All" resets to `[]`.
- Below trigger: render selected albums as removable `Badge` chips (X button calls `onChange(next.filter(a => a !== album))`).
- `data-testid="album-multi-select"`, `album-multi-select-trigger`, `album-chip-{name}`.

### Phase 3 — Frontend: refactor SongSearch → shared search bar

**3.1 `SongSearch.tsx`** — `delivery/webapp/src/components/songset/SongSearch.tsx`
- Add prop `mode: "keyword" | "describe"` to switch input element:
  - `keyword`: existing `Input` (single line).
  - `describe`: existing `Textarea` with placeholder describing songs.
- Replace single-select album Select with `<AlbumMultiSelect>`.
- Change internal `selectedAlbum: string` → `selectedAlbums: string[]`.
- Update `triggerSearch` to build `StructuredSearchCriteria` with `albums: selectedAlbums`.
- Update `onSearch`/`onAdvancedSearch` parent signature: `(query, albums[], filters) => void`.
- Add **contextual help text** below the input that swaps by mode:
  - keyword: `"Tip: search by title, pinyin, or composer — e.g. '奇异恩典', 'Amazing Grace', '约瑟夫'"`
  - describe: `"Tip: describe the song by theme or feeling — e.g. '关于神的恩典与怜悯的赞美', 'upbeat praise songs about grace'"`
- Keep debounce logic; only fire when query length > 0 for `describe` mode (avoid empty searches).

### Phase 4 — Frontend: consolidate SongSearchSheet

**4.1 Remove two-tab structure** in `SongSearchSheet.tsx:312-337`.
- Replace the `Browse`/`Describe` tab buttons with a single segmented control of the same style that toggles `mode`. The control lives above the (now shared) search panel.
- Keep `SearchMode = "keyword" | "describe"` type and the existing `mode` state.
- Render `<SongSearch mode={mode} ... />` once (no more conditional `mode === "keyword"` / `mode === "describe"` blocks).

**4.2 Hoist shared state** into `SongSearchSheet`:
- Lift `selectedAlbums: string[]`, `selectedKeys: string[]`, `selectedBpm` from `SongSearch` into `SongSearchSheet` (or pass through callbacks — preferred: keep `SongSearch` controlled, lift state up so switching modes preserves filters).
- Decide: lift state to `SongSearchSheet` to ensure filters survive mode switches. `SongSearch` becomes a controlled child.

**4.3 Unify `handleSearch`** — `SongSearchSheet.tsx:82`
- New signature: `handleSearch(query, albums[], advanced?)`.
- When `mode === "keyword"`: existing `/api/songs` or `/api/songs/search` path with `albumName=A,B`.
- When `mode === "describe"`: POST to `/api/songs/search/semantic` with `{ query, albums, keys: advanced?.keys, bpmRange: advanced?.bpmRange, limit: 20 }`; fetch semantic results into the shared results grid.
- Merge the rendering logic: a single `results` array + `SongCard` grid (semantic results may still render the `similarity` / `matchingSnippet` / `whyThisMatch` badges — the existing `SemanticSearch.tsx` rendering for those can be retained as an optional overlay inside the shared grid).

**4.4 Remove or repurpose `SemanticSearch.tsx`** — `delivery/webapp/src/components/search/SemanticSearch.tsx`
- Extract its result-overlay rendering (`similarity` badge, `matchingSnippet`, `whyThisMatch` expand) into a small `<SemanticResultExtras>` subcomponent reusable inside the shared grid.
- The fetch logic moves into `SongSearchSheet.handleSearch` (described in 4.3).
- Delete the standalone `SemanticSearch` usage from `SongSearchSheet` (its current `<SemanticSearch .../>` render block at `SongSearchSheet.tsx:433-443`).
- Keep `SemanticSearch.tsx` file if other callers exist (search first); otherwise it can be deleted after migration.

**4.5 Footer update** — `SongSearchSheet.tsx:446-457`
- Counter line currently shows `${totalCount} songs` only in keyword mode; update to show for both modes (semantic endpoint returns `total`).

### Phase 5 — Tests

**5.1 Update** `delivery/webapp/src/test/components/songset/SongSearchSheet.test.tsx` (renamed from `BrowseSheet.test.tsx`)
- Tab toggle tests: rename / repurpose to segmented control assertions.
- Add test: switching mode preserves selected albums/keys/bpm.
- Add test: describe mode sends `albums`, `keys`, `bpmRange` in the semantic POST body.
- Add test: keyword mode sends `albumName=A,B` query param.

**5.2 Update** `delivery/webapp/src/test/components/songset/SongSearch.test.tsx`
- Update album filter tests from single-select to multi-select (chip add/remove, dropdown toggle).
- Verify `StructuredSearchCriteria.albums` is now an array.

**5.3 Update** `delivery/webapp/src/test/components/search/SemanticSearch.test.tsx`
- If `SemanticSearch` is removed as a standalone component, delete or convert tests to cover the new `<SemanticResultExtras>` overlay.

**5.4 Update** `delivery/webapp/src/test/accessibility/accessibility.test.tsx`
- Update import path + component name to `SongSearchSheet`.
- Update `describe("BrowseSheet", ...)` → `describe("SongSearchSheet", ...)`.

**5.5 (Optional) New** `delivery/webapp/src/test/api/songs/search-semantic.filter.test.ts`
- Unit test the route handler with `albums`, `keys`, `bpmRange` in the body — assert they reach `semanticSearchSongs`.

## 5. Risks & Edge Cases

- **Empty album array** treated identically on server and client: no album filter (`WHERE` clause skipped). Both endpoints must guard against empty arrays (don't emit `inArray([])`).
- **Album name with comma**: comma is the separator. Mitigate by URL-encoding values; document that album names must not contain commas (verify none exist via a quick catalog query before merge — `delivery/webapp/src/lib/db/songs.ts:362` `getAlbums` returns distinct album names).
- **URL-encoding of commas inside `albumName` query param**: server should `decodeURIComponent` each token; consume via `searchParams.get` (already decoded by `URLSearchParams`).
- **Backward compatibility**: existing single-album callers (Android app, if any) sending `albumName=Single` keep working because comma-split of a single value yields `[Single]`.
- **Semantic recall degradation with strict album filter**: if a song has `album_name IS NULL` it will be excluded by an album filter. Acceptable (matches keyword behaviour). Document in help text.
- **Performance**: extra `EXISTS` subqueries on the semantic path mirror the keyword path; both rely on the existing `recordings` indexes. No new index needed.
- **Free-text `describe` debounce**: must not auto-fire on empty; existing `if (!trimmed) return;` in `SemanticSearch.handleSearch` is preserved in the unified handler.
- **Rename churn**: `BrowseSheet` is referenced in 3 external files (`SongsetEditorClient.tsx`, `BrowseSheet.test.tsx`, `accessibility.test.tsx`) — all must be updated atomically with the rename to avoid broken imports.

## 6. Verification

After implementation:
```bash
# Lint + typecheck
pnpm --filter sow-webapp lint
pnpm --filter sow-webapp build

# Tests
pnpm --filter sow-webapp test -- src/test/components/songset
pnpm --filter sow-webapp test -- src/test/components/search
pnpm --filter sow-webapp test -- src/test/accessibility
```

Manual smoke (dev server `pnpm --filter sow-webapp dev`):
1. Open SongSearchSheet → only one panel visible; segmented control toggles Keyword/Describe.
2. Keyword + multi-album + key + bpm selected → `/api/songs/search?albumName=A,B&keys=C&bpmRange=fast`.
3. Describe + same filters → request body contains `albums`, `keys`, `bpmRange`.
4. Switching mode preserves filter selections.
5. Help text swaps per mode (visual check).
6. Album dropdown chips removable; "All" selection clears.

## 7. Out of Scope

- Adding `cmdk` / shadcn `command` + `popover` combo (deferred — existing components suffice).
- Server-side searchable album combobox (album count is small).
- Persisting filter state across SongSearchSheet open/close (current behaviour resets on close — keep).
- Mobile design pass beyond what existing components already provide.
- Android client `albumName` updates (its search is single-album; the server remains backward compatible so no client change required).
