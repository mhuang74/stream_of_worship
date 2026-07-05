# Webapp Search Songs — Album Display Format v1

## Status
Planning (not yet implemented)

## Summary
Update the album display name in the Search Songs (Browse Sheet) screen so that the
`albumSeries` value has any pre-existing parentheses stripped before being wrapped in the
format `"<Album Title> (<Album Series>) [<song count>]"`. Both ASCII `()` and full-width
`（）` parens are stripped; inner content is preserved with parens replaced by spaces.

## Motivation
The catalog increasingly uses Chinese album series values like `敬拜讚美（22）`. The current
`formatAlbumLabel` blindly interpolates the raw `albumSeries` into a wrapping pair of ASCII
parens, producing confusing double/nested parens such as
`<Title> (敬拜讚美（22）) [N]`. We want a clean single pair of wrapping parens with the
album series content preserved.

## Scope
Applies to **display only**. Underlying `albumSeries` filter values sent to the API and the
DB column remain untouched. Only the presentation layer in
`delivery/webapp/src/lib/search/album-filter.ts` changes.

## Out of scope
- Data migration / normalization of stored `albumSeries` values.
- Changes to `BrowseSheet.tsx`, `AlbumMultiSelect.tsx`, API routes, or DB schema
  (they already consume `formatAlbumLabel` / `formatAlbumOptionLabel`, so they need no edits).
- Android app, admin CLI, render worker.

## Format rule
Final display format for a single album is:

```
<Album Title> (<stripped Album Series>) [<song count>]
```

When `albumSeries` is null/empty after stripping, fall back to just `<Album Title>`
(without parens, without song count repetition) for `formatAlbumLabel`, and
`<Album Title> [<song count>]` for `formatAlbumOptionLabel`.

### Strip algorithm
1. Take the raw `albumSeries` string (already trimmed upstream).
2. Replace every occurrence of the four paren characters with a single space:
   - `(` -> ` `
   - `)` -> ` `
   - `（` -> ` ` (U+FF08 FULLWIDTH LEFT PARENTHESIS)
   - `）` -> ` ` (U+FF09 FULLWIDTH RIGHT PARENTHESIS)
3. Collapse all runs of whitespace into a single space.
4. Trim leading/trailing whitespace.
5. If the result is the empty string, treat `albumSeries` as absent (null behavior).

This is **fully aggressive** — every paren character is stripped, regardless of nesting
or balance. Example transforms of the albumSeries portion:

| Raw albumSeries          | After strip       |
| ------------------------ | ----------------- |
| `敬拜讚美（22）`         | `敬拜讚美 22`     |
| `Series (Vol. 1)`        | `Series Vol. 1`   |
| `Series (A) (B)`         | `Series A B`      |
| `A（B（C））`            | `A B C`           |
| `(Parens Only)`          | `Parens Only`     |
| `（）`                   | `""` (treated as null) |

### Outer wrapping parens
Always ASCII `(` `)` per the format spec, regardless of CJK content. Final example:

```
My Title (敬拜讚美 22) [12]
```

## Surfaces
Apply to both display functions in `delivery/webapp/src/lib/search/album-filter.ts`:

1. `formatAlbumLabel(album: AlbumFilter): string` — used by `AlbumMultiSelect` trigger
   text when exactly one album is selected (file
   `delivery/webapp/src/components/songset/AlbumMultiSelect.tsx:58`).
2. `formatAlbumOptionLabel(album: AlbumOption): string` — used by `AlbumMultiSelect`
   for each dropdown list item (file `AlbumMultiSelect.tsx:101`).

Both must use the stripped albumSeries. `formatAlbumOptionLabel` should keep delegating
to `formatAlbumLabel` and appending ` [${songCount}]` so the strip logic lives in exactly
one place.

## Implementation plan

### File: `delivery/webapp/src/lib/search/album-filter.ts`

1. Add a private helper `stripAlbumSeriesParens(raw: string): string`:
   - Input: the raw (already-trimmed) album series string.
   - Behavior: replace the four paren characters with single spaces, collapse
     whitespace runs, and trim. Implementation sketch:
     ```ts
     const PAREN_CHARS = /[()（）]/g;
     function stripAlbumSeriesParens(raw: string): string {
       return raw.replace(PAREN_CHARS, " ").replace(/\s+/g, " ").trim();
     }
     ```
2. Modify `formatAlbumLabel(album: AlbumFilter): string`:
   - If `album.albumSeries` is falsy, return `album.albumName` unchanged (current behavior).
   - Otherwise compute `const series = stripAlbumSeriesParens(album.albumSeries);`
   - If `series` is empty string, return `album.albumName` (no parens appended).
   - Otherwise return `` `${album.albumName} (${series})` ``.
3. `formatAlbumOptionLabel(album: AlbumOption): string` — no logic change. It already
   delegates to `formatAlbumLabel` and appends ` [${album.songCount}]`. Verify after edit.
4. `albumFilterKey` is left UNCHANGED so filter matching still uses the raw `albumSeries`
   value (consistent with the API query sent to `/api/songs`).
5. `normalizeAlbumFilters` is left UNCHANGED so the selected filter persisted/sent to the
   backend keeps the original `albumSeries`. This is intentional — display transformation
   is a view-layer concern.

### No other code changes required
- `AlbumMultiSelect.tsx` calls `formatAlbumLabel` / `formatAlbumOptionLabel` — picked
  up automatically.
- `BrowseSheet.tsx` `normalizeAlbumOptions` trims values; doesn't itself format labels.
- API route `/api/songs/albums` returns raw rows from `getAlbums()` — no change.

## Tests

Add unit tests to the existing webapp Vitest suite (run via `pnpm --filter sow-webapp test`).
If no test file exists for `album-filter.ts` yet, create
`delivery/webapp/src/lib/search/album-filter.test.ts`. Otherwise extend it.

### Test cases for `stripAlbumSeriesParens` (via the public `formatAlbumLabel`/`formatAlbumOptionLabel` API)

| Album option                                                    | Expected output                                       |
| --------------------------------------------------------------- | ----------------------------------------------------- |
| `{ albumName: "My Title", albumSeries: null, songCount: 12 }`   | `My Title [12]` (option label); `My Title` (trigger)  |
| `{ albumName: "My Title", albumSeries: "  ", songCount: 12 }`  | `My Title [12]` (whitespace-only collapse to empty)   |
| `{ albumName: "詩歌", albumSeries: "敬拜讚美（22）", songCount: 5 }` | `詩歌 (敬拜讚美 22) [5]`                              |
| `{ albumName: "Hymns", albumSeries: "Series (Vol. 1)", songCount: 3 }` | `Hymns (Series Vol. 1) [3]`                       |
| `{ albumName: "M", albumSeries: "Series (A) (B)", songCount: 1 }` | `M (Series A B) [1]`                                |
| `{ albumName: "M", albumSeries: "A（B（C））", songCount: 1 }`   | `M (A B C) [1]`                                       |
| `{ albumName: "M", albumSeries: "（）", songCount: 1 }`          | `M [1]` (trigger: `M`)                                 |
| `{ albumName: "M", albumSeries: "Clean Series", songCount: 1 }` | `M (Clean Series) [1]`                                |

### Behavioral assertions
- `albumFilterKey` still returns a key built from the RAW `albumSeries` (not the stripped
  one) — assert that wrapping `敬拜讚美（22）` produces a key ending in `\u0000敬拜讚美（22）`.
- `normalizeAlbumFilters` does NOT transform parens — assert the input `albumSeries`
  round-trips through `normalizeAlbumFilters` unchanged.

## Verification steps
After implementation:

```bash
# From project root
pnpm --filter sow-webapp test -- album-filter

# Lint + typecheck the changed file
pnpm --filter sow-webapp lint
pnpm --filter sow-webapp build

# Manual smoke
pnpm --filter sow-webapp dev
# Open http://localhost:8080/songsets/<some-id>, click "Add Songs" / open Browse Sheet,
# open the album multi-select, confirm the dropdown labels and trigger label render
# correctly for a CJK example (e.g. 敬拜讚美（22）).
```

## Risks / notes
- The strip is intentionally destructive (no nesting awareness). This matches the
  user's stated intent. Edge cases like album series that legitimately contain
  parentheses as part of the name (rare in this catalog) will lose the parens from
  the display name only — the underlying value is preserved for filtering.
- Because transformation is display-only, no DB migration is needed and existing
  applied filters continue to match the same rows.
- `graphify update .` should be run after the code edit (per AGENTS.md graphify rule).
