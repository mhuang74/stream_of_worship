---
# Songset Editor: Inline Lyrics Display — v3

## Goal

Allow users to view song lyrics directly in the Songset Editor by expanding any song row via a dedicated expand button. This provides a quick way to evaluate songs via lyrics prior to rendering, without needing to wait for or play back the rendered output.

## Product Decisions (from v2 review)

- **Expand interaction**: Dedicated `ChevronDown` button per row, separate from the drag handle. Prevents drag-vs-click conflicts in the `@dnd-kit` sortable list.
- **`onSelectSong`**: Removed entirely. The dedicated chevron is the sole expand toggle; the no-op `onSelectSong` prop and its click handlers on the song row are removed from both `SongList` and `SongsetEditor`.
- **Accordion (one-at-a-time)**: Only one row expanded at a time. Expanding a new row collapses the previous. Allows lyric comparison only by re-expanding — acceptable trade-off for layout stability on long set lists.
- **Drag-start auto-collapse**: When `@dnd-kit` fires `onDragStart`, the expanded row collapses immediately (`expandedItemId = null`). The panel does not re-open automatically after drop; user re-clicks the chevron if desired. Prevents visual confusion as the lyrics panel moves with the dragged card.
- **R2 failure handling**: Both 404 and network errors from R2 silently fall through to `songs.lyrics_lines`. No error is shown to the user; we always attempt to show something.
- **`lrcStatus` optimization**: The API checks `recordings.lrcStatus` before attempting an R2 fetch. If `lrcStatus === 'missing'`, R2 is skipped entirely and the API falls through to DB.
- **`lyricsLines` is JSON `string[]`**: Stored as TEXT column, JSON-encoded `List[str]` (per `ops/admin-cli/src/stream_of_worship/admin/db/models.py:149-151`). Server JSON-parses it before returning in the API response; client receives a typed `string[] | null`.
- **No `source` field**: Response shape uses two nullable fields instead. Rendering branches on `isValidLRC()` client-side; no discriminator needed.
- **No size guard**: LRC files are tiny plain-text (typically 2–10 KB). R2's own object limits suffice. The 413 size-cap code path is removed entirely.
- **Module-level cache**: The `useSongLyrics` hook caches results in a module-scoped `Map<contentHash, Result>` that persists across component mounts within the session. Re-expansion after navigation away and back is instant. Memory footprint is negligible (lyrics are small text).
- **Plain-text rendering**: For both `lyricsRaw` (string) and `lyricsLines` (joined with `\n`), render as a single `<pre className="text-sm whitespace-pre-wrap break-words">` block. Preserves author formatting; avoids per-line `<div>` proliferation.
- **Mobile layout**: Timestamps and lyrics text stacked vertically on mobile (`< 768px`), side-by-side on desktop. Timestamps are never hidden.
- **Distinct `recording === null` message**: When `item.recording` is null, the panel immediately shows "No lyrics available — recording missing." without invoking the hook.

## Context

### Relevant Files

| File | Role |
|------|------|
| `delivery/webapp/src/components/songset/SongList.tsx` | Song row rendering (`SortableSongItem`), `SongListItem` interface. `onSelectSong` prop wired to a no-op in SongsetEditor. |
| `delivery/webapp/src/components/songset/SongsetEditor.tsx` | Parent editor component; passes `onSelectSong={() => {}}` to SongList (line ~410). |
| `delivery/webapp/src/lib/render/lrc-parser.ts` | `parseLRC()`, `isValidLRC()` for parsing/validating LRC content. |
| `delivery/webapp/src/lib/r2/client.ts` | `getLrcSignedUrl(hashPrefix)` → presigned R2 URL for `{hashPrefix}/lyrics.lrc`. Returns a `SignedUrlResult` (URL string + expiresAt + cacheControl); does NOT fetch object contents. |
| `delivery/webapp/src/app/api/lyrics/overrides/route.ts` | Existing lyrics override API; GET returns `{ lrcContent: string \| null }` by `recordingContentHash`. Used for CRUD on user overrides; not used for primary lyrics retrieval. |
| `delivery/webapp/src/lib/db/songsets.ts` | `getSongsetEditorData()`: DB query feeding the editor. |
| `delivery/webapp/src/db/schema.ts` | `songs.lyricsRaw` (TEXT), `songs.lyricsLines` (TEXT, JSON `string[]`), `recordings.r2LrcUrl`, `recordings.lrcStatus`, `userLrcOverrides.lrcContent`. |

### Data Already Available in SongListItem

The `SongListItem` interface already carries:

- `recording.contentHash` — used to look up user LRC override + as R2 hash prefix
- `recording.hashPrefix` — used for R2 LRC path

No new fields need to be added to the songset editor's inline data model.

## Implementation Plan

### Step 1: New API Endpoint — `GET /api/lyrics/[recordingContentHash]`

**New file**: `delivery/webapp/src/app/api/lyrics/[recordingContentHash]/route.ts`

- **Route**: `GET /api/lyrics/{recordingContentHash}`
- **Auth**: require session (same pattern as existing lyrics/overrides route).
- **Resolution order**:
  1. Look up `userLrcOverrides` table by `(userId, recordingContentHash)`. If `lrcContent` is non-null, return `{ lrcContent, lines: null }`.
  2. Else, look up `recordings` by `contentHash`. **Check `lrcStatus`**: if `lrcStatus === "missing"`, skip R2 and fall through to step 3.
  3. Else, call `r2Client.getLrcSignedUrl(hashPrefix)` and `fetch` the LRC text from the presigned URL (server-side `fetch`). A 404 or any network error (timeout, 5xx, DNS failure) is treated as "no R2 file" — fall through silently to step 4.
  4. Look up `songs.lyricsLines` via `recordings.songId → songs.id` join. If non-null, JSON-parse it. On parse failure or non-array result, treat as null and fall through. If parsed to a non-empty `string[]`, return `{ lrcContent: null, lines }`.
  5. Look up `songs.lyricsRaw` via the same join. If non-null, return `{ lrcContent: lyricsRaw, lines: null }`.
  6. If `lyricsRaw` is also null, return `{ lrcContent: null, lines: null }`.
- **Response shape**: `{ "lrcContent": string | null, "lines": string[] | null }`
  - `lrcContent`: LRC string (from override or R2) OR plain text (from `lyricsRaw`). Client uses `isValidLRC()` to distinguish.
  - `lines`: structured line array (from `lyricsLines`). Mutually exclusive with `lrcContent` in the response — exactly one (or neither) is non-null.

### Step 2: Client-Side Lyrics Hook — `useSongLyrics`

**New file**: `delivery/webapp/src/hooks/useSongLyrics.ts`

A React hook that:

- Takes `recordingContentHash: string | undefined`
- Returns early with `{ lrcContent: null, lines: null, loading: false, error: null }` when `recordingContentHash` is `undefined`.
- Calls `GET /api/lyrics/{recordingContentHash}` on demand (triggered by row expansion).
- Uses an `AbortController` per fetch. If the component unmounts or the `recordingContentHash` changes before the fetch completes, the previous request is aborted and its result discarded.
- Caches results in a **module-scoped** `Map<string, { lrcContent: string | null; lines: string[] | null }>`, keyed by `contentHash`. Persists across component mounts within the session. On cache hit, returns cached value synchronously without refetching.
- Returns `{ lrcContent: string | null, lines: string[] | null, loading: boolean, error: string | null }`.
- Only fetches when the row is expanded; does not pre-fetch all on mount.

### Step 3: Expandable Song Row in SongList.tsx

**Modify**: `delivery/webapp/src/components/songset/SongList.tsx`

Changes in `SongList` component:

- Add `expandedItemId: string | null` state (lifted to SongList level for accordion behavior).
- Pass `isExpanded`, `onToggleExpand` down to each `SortableSongItem`.
- **Drag-start auto-collapse**: subscribe to `@dnd-kit`'s `onDragStart` event. On drag start, set `expandedItemId = null`. The currently expanded row collapses immediately; the lyrics panel does not fly with the dragged card. Do not auto-restore after drop.
- Accordion logic: when `onToggleExpand` is called for an item, if that item is already expanded, collapse it (`expandedItemId = null`). Otherwise, set it as expanded and collapse any previously expanded row.

Changes in `SortableSongItem`:

- New props: `isExpanded: boolean`, `onToggleExpand: () => void`.
- Add a `ChevronDown` icon immediately to the right of the song info area (before the transition/edit buttons). It rotates 180° via `transition-transform duration-200` when `isExpanded === true`.
- **Accessibility**: the chevron button has `aria-expanded={isExpanded}`, `aria-controls={lyricsPanelId}`, and the lyrics panel has `role="region"` + matching `id`.
- When `isExpanded === true`, render a second `<div>` below the song info (inside the same `<Card>`, after the existing `CardContent`'s flex row) containing:
  - If `item.recording === null`: muted "No lyrics available — recording missing." do NOT invoke the hook.
  - Else if `loading`: `<Loader2 className="animate-spin" />` + "Loading lyrics…"
  - Else if `error`: muted "Lyrics unavailable" text
  - Else if `lrcContent !== null && isValidLRC(lrcContent)`: parse with `parseLRC()` → render each `LRCLine`:
    - **Desktop (`md:` breakpoint)**: side-by-side flex row:
      ```
      [00:12.34]  赞美耶和华
      ```
      Timestamp: `font-mono text-xs text-muted-foreground w-16 shrink-0`
      Text: `text-sm break-words`
    - **Mobile (`< md`)**: stacked vertically:
      ```
      [00:12.34]
      赞美耶和华
      ```
      Timestamp: `font-mono text-xs text-muted-foreground block`
      Text: `text-sm break-words block`
  - Else if `lines !== null && lines.length > 0`: render as a single `<pre className="text-sm whitespace-pre-wrap break-words">{lines.join('\n')}</pre>` block.
  - Else if `lrcContent !== null` (plain text that failed `isValidLRC()`): `<pre className="text-sm whitespace-pre-wrap break-words">{lrcContent}</pre>`.
  - Else (both null): "No lyrics available for this recording."
- Container: `max-h-[40vh] md:max-h-[400px] overflow-y-auto` with vertical padding.
- **Scroll behavior**: when a row expands, call `scrollIntoView({ behavior: 'smooth', block: 'nearest' })` on the expanded panel so the lyrics are fully visible without the user manually scrolling.

### Step 4: Remove `onSelectSong` Prop

**Modify**: `delivery/webapp/src/components/songset/SongList.tsx`

- Remove `onSelectSong` from `SongListProps` interface.
- Remove `onSelectSong` from `SortableSongItemProps` interface.
- Remove the `onSelectSong` click/keydown handlers from the song info `<div>` in `SortableSongItem`.
- The song info `<div>` is no longer clickable for expansion; the chevron is the sole toggle.

**Modify**: `delivery/webapp/src/components/songset/SongsetEditor.tsx`

- Remove `onSelectSong={() => {}}` from the `<SongList>` JSX invocation.

## Files to Create

| File | Purpose |
|------|---------|
| `delivery/webapp/src/app/api/lyrics/[recordingContentHash]/route.ts` | Server-side lyrics resolution endpoint (override → R2 → DB lyrics_lines → DB lyrics_raw) |
| `delivery/webapp/src/hooks/useSongLyrics.ts` | React hook: fetch + cache lyrics by recordingContentHash |

## Files to Modify

| File | Changes |
|------|---------|
| `delivery/webapp/src/components/songset/SongList.tsx` | Add accordion expand state + drag-start auto-collapse + lyrics panel rendering + chevron toggle; remove `onSelectSong` prop and click handlers |
| `delivery/webapp/src/components/songset/SongsetEditor.tsx` | Remove `onSelectSong` prop from `<SongList>` invocation |

## Imports

- `parseLRC`, `isValidLRC`, `LRCLine` from `@/lib/render/lrc-parser` (existing, no new dep)
- `Loader2`, `ChevronDown` from `lucide-react` (already imported Loader2; add ChevronDown)
- `useSongLyrics` from `@/hooks/useSongLyrics` (new)

## Edge Cases

1. **No recording**: Song row has `item.recording === null`. Lyrics expand shows "No lyrics available — recording missing." without invoking the hook.
2. **No LRC in R2 (404)**: API falls through to `songs.lyrics_lines` (JSON-parsed string array). If `lyrics_lines` is null or fails JSON parse, falls through to `songs.lyrics_raw`. If that's also null, returns `{ lrcContent: null, lines: null }`.
3. **`lyrics_lines` is invalid JSON or not an array**: Treated as null; API silently falls through to `lyrics_raw`. Logged server-side at `warn` level for diagnosis.
4. **R2 not configured / network down**: Falls through to DB silently. Graceful degradation.
5. **`lrcStatus === 'missing'`**: R2 fetch is skipped entirely; API queries DB directly.
6. **User toggles expansion mid-fetch**: Hook aborts the previous request via `AbortController`. Stale responses are discarded.
7. **Read-only mode**: Expansion still works in read-only mode. The chevron is always visible. No change needed.
8. **Rapid accordion toggling**: Because of the module-scoped cache, toggling back to a recently-viewed song shows lyrics instantly without refetching.
9. **Drag with expanded row**: The `onDragStart` handler collapses any expanded row before the drag preview is rendered. The user sees only the song info card during drag.
10. **`lyrics_lines` is an empty array `[]`**: Treated as null (no content); falls through to `lyrics_raw`.

## Testing

### Unit Tests

- `delivery/webapp/src/test/api/lyrics/recordingContentHash.test.ts`: mock DB + R2 client; test (a) override path returns `lrcContent`, (b) lrcStatus='missing' skips R2 and returns `lines` from `lyrics_lines`, (c) R2 200 returns `lrcContent`, (d) R2 404 falls through to `lyrics_lines`, (e) `lyrics_lines` invalid JSON falls through to `lyrics_raw` returning `lrcContent` as plain text, (f) all-null returns `{ lrcContent: null, lines: null }`, (g) auth required (401 without session), (h) `lyrics_lines` empty array falls through to `lyrics_raw`.
- `delivery/webapp/src/test/hooks/useSongLyrics.test.ts`: mock fetch; test (a) loading → success transitions, (b) error state, (c) module-scoped cache: second expansion of the same contentHash within same session does not refetch (verify `fetch` not called), (d) cache survives unmount+remount within same session, (e) abort on unmount or hash change (verify `AbortController.abort()` called), (f) `recordingContentHash === undefined` returns nulls with no fetch.

### Component Tests

- `delivery/webapp/src/test/components/SongList-lyrics.test.tsx`: render SongList with mock items; (a) click chevron → lyrics panel appears, (b) click another chevron → first collapses, second expands (accordion), (c) verify LRC parsing → timestamped display, (d) verify mobile stacked layout below `md` breakpoint via container width assertion, (e) `item.recording === null` → distinct "recording missing" message without hook invocation, (f) drag start collapses the expanded row (simulate `onDragStart` from `@dnd-kit`).

### Manual Testing

1. Open a songset with songs that have LRC files in R2 → expand → see timed lyrics.
2. Open a songset with songs that only have `lyrics_lines` (structured) → expand → see plain-text formatted by line.
3. Open a songset with songs that only have `lyrics_raw` → expand → see plain text with preserved formatting.
4. Open a songset with songs that have a user LRC override → expand → see override content.
5. Open a songset with a song that has neither → expand → see "No lyrics available".
6. Open a songset with a song whose `recording === null` → expand → see "No lyrics available — recording missing."
7. Verify drag-to-reorder still works smoothly (no accidental expansion while dragging the chevron area).
8. Expand a row, then drag a different row → the expanded row collapses on drag start.
9. Verify on mobile (`< 768px`) timestamps stack above lyric text for LRC content.
10. Navigate away from the editor and back → previously expanded lyrics are cached in memory; re-expansion is instant (no spinner).

## Out of Scope

- Editing lyrics from this panel (use the existing LRC editor / admin CLI).
- Synchronized auto-scroll during playback (future enhancement using `findCurrentLyricIndex()`).
- Cross-session cache persistence (module-scope Map is cleared on page reload).
- Server-side caching of the API response (could be a future enhancement with `Cache-Control` headers if profiling shows the resolution chain is slow).
---
