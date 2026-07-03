# Consolidated Song Search Sheet V2

## Summary

Build one unified search sheet with shared filters across Keyword and Describe modes, biased toward a simple UX and lower runtime risk. Keep public behavior backward compatible for Android and existing web APIs. Do not rename internal files/components in this feature pass; update user-facing copy from "Browse" to "Search" where visible.

This plan replaces the broader `consolidate-browse-describe-sheet.md` approach with a smaller, safer implementation: unify the user experience without forcing a full component rename, a monolithic result-grid refactor, or fragile comma-encoded album filters.

## Review Notes On The Previous Plan

- UX experience: consolidating the modes is right, but the prior plan risks making the sheet feel busy by combining mode switching, contextual help, multi-select chips, advanced filters, semantic match details, counters, and result rendering changes at once.
- Ease of use: multi-album selection should be compact. A dropdown trigger with a selected count is easier to scan than a large chip area that grows with selections.
- Over-engineering: renaming `BrowseSheet` to `SongSearchSheet` creates broad import/test churn without being required for the user-visible feature. A full extraction of semantic result rendering is also avoidable in the first pass.
- Runtime concerns: semantic search must stay explicit. Debouncing semantic queries would generate unnecessary embedding calls and create stale-response races.
- Operational issues: comma-separated album query params are fragile for real album names. Repeated `albumName` params are safer and still preserve single-album backward compatibility.

## Key Changes

- Replace the two visible Browse/Describe tabs with a compact mode switch labeled `Keyword` and `Describe` inside the existing `BrowseSheet`.
- Use separate local inputs: `keywordQuery` and `describeQuery`, with shared `selectedAlbums`, `selectedKeys`, and `selectedBpm`.
- Keyword mode keeps debounced search and supports empty-query filtered listing through `/api/songs`.
- Describe mode uses an explicit `Search` button or Ctrl/Cmd+Enter only; do not debounce semantic embedding calls.
- Show concise contextual examples near the input, but keep them short enough not to crowd the sheet.
- Add album multi-select as a compact control: trigger shows `Albums` plus selected count; selected names are shown only in a limited summary/chip row to avoid layout bloat.
- Preserve the current semantic fallback: if semantic search returns `503`, switch to Keyword mode using the same text.

## API And Data Model

- Change shared filter type from `album?: string` to `albums?: string[]`.
- For GET endpoints, support repeated query params: `albumName=A&albumName=B`.
- Preserve backward compatibility with existing single `albumName=Hymns`; optionally parse comma-separated values defensively, but do not make comma encoding the primary client format.
- Extend `/api/songs` to pass `albumNames` into `listSongs`.
- Extend `/api/songs/search` to pass `albums`, `keys`, and `bpmRange` into `fullTextSearchSongs`.
- Extend `/api/songs/search/semantic` body to accept optional `albums`, `keys`, and `bpmRange`.
- Add bounded parsing: trim, de-dupe, drop empty values, and cap album filters to a reasonable limit such as 25.
- In DB helpers, use `inArray(songs.albumName, albumNames)` for list/full-text paths.
- In semantic SQL, apply album/key/BPM filters before reranking, but return `total` as returned-result count only. Do not present it as a full catalog total.

## Frontend Implementation

- Keep `BrowseSheet.tsx` file/component name for now; change visible title to `Search Songs`.
- Lift shared filter state into `BrowseSheet` so mode switching preserves filters.
- Keep `SongSearch` as the keyword/filter input component, but make it controlled enough to accept shared album/key/BPM state.
- Add a small `AlbumMultiSelect` component using existing UI primitives; no new dependency.
- Keep `SemanticSearch` as the describe-mode implementation initially, but add props for shared filters and result/add/play state as needed rather than moving all rendering into `BrowseSheet`.
- Avoid a full shared result-grid refactor in this pass. That can be a later cleanup after behavior is stable.
- In the footer, show exact total only for keyword/list results. For describe mode, show `${results.length} matches` or no count until searched.

## Runtime And Operations

- Semantic search must not fire on empty input or while the user types.
- Add stale-request protection with `AbortController` or request IDs so slower responses cannot overwrite newer results.
- No database migration required.
- No Android change required; existing single-album list calls and semantic calls continue to work.
- Avoid file rename churn in this pass to reduce import/test breakage and review size.

## Test Plan

- Unit/API tests:
  - `/api/songs` accepts single and repeated `albumName`.
  - `/api/songs/search` forwards albums, keys, and BPM.
  - `/api/songs/search/semantic` validates and forwards albums, keys, and BPM.
  - Empty/invalid album values are ignored and large album arrays are capped.
- Component tests:
  - Mode switch preserves selected albums/key/BPM filters.
  - Keyword mode sends repeated `albumName` params.
  - Describe mode sends `{ albums, keys, bpmRange }` in POST body only when explicit search is triggered.
  - Semantic `503` fallback switches to Keyword mode with the describe text.
  - Album multi-select can select, deselect, clear all, and display selected count.
- Verification:
  - `pnpm --filter sow-webapp test -- src/test/api/songs src/test/components/songset src/test/components/search`
  - `pnpm --filter sow-webapp lint`
  - `pnpm --filter sow-webapp build`

## Assumptions

- The requested "single tab" means one search sheet surface, not necessarily one monolithic component.
- "Advanced filters apply for both modes" means filters are sent to both keyword/list and semantic endpoints when relevant.
- Simplicity takes priority over preserving every semantic-result decoration in the first pass; existing semantic match details may remain where already implemented.
