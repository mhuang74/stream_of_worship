# Songset Editor: Inline Lyrics Display — v2

## Goal

Allow users to view song lyrics directly in the Songset Editor by expanding any song row via a dedicated expand button. This provides a quick way to evaluate songs via lyrics prior to rendering, without needing to wait for or play back the rendered output.

## Product Decisions (from review clarifications)

- **Expand interaction**: Dedicated `ChevronDown` button per row, separate from the drag handle. Prevents drag-vs-click conflicts in the `@dnd-kit` sortable list.
- **`onSelectSong`**: Removed entirely. The dedicated chevron is the sole expand toggle; the no-op `onSelectSong` prop and its click handlers on the song row are removed from both `SongList` and `SongsetEditor`.
- **R2 failure handling**: Both 404 and network errors from R2 silently fall through to `songs.lyrics_raw`. No error is shown to the user; we always attempt to show something.
- **`lrcStatus` optimization**: The API checks `recordings.lrcStatus` before attempting an R2 fetch. If `lrcStatus === 'missing'`, R2 is skipped entirely and the API falls through to DB `lyrics_raw`.
- **Mobile layout**: Timestamps and lyrics text are stacked vertically on mobile (`< 768px`), side-by-side on desktop. Timestamps are never hidden.

---

## Context

### Relevant Files

| File | Role |
|------|------|
| `delivery/webapp/src/components/songset/SongList.tsx` | Song row rendering (`SortableSongItem`), `SongListItem` interface. `onSelectSong` prop wired to a no-op in SongsetEditor. |
| `delivery/webapp/src/components/songset/SongsetEditor.tsx` | Parent editor component; passes `onSelectSong={() => {}}` to SongList (line ~410). |
| `delivery/webapp/src/lib/render/lrc-parser.ts` | `parseLRC()`, `isValidLRC()` for parsing/validating LRC content. |
| `delivery/webapp/src/lib/r2/client.ts` | `getLrcSignedUrl(hashPrefix)` → presigned R2 URL for `{hashPrefix}/lyrics.lrc`. |
| `delivery/webapp/src/app/api/lyrics/overrides/route.ts` | Existing lyrics override API; GET returns `{ lrcContent: string \| null }` by `recordingContentHash`. |
| `delivery/webapp/src/lib/db/songsets.ts` | `getSongsetEditorData()`: DB query feeding the editor. |
| `delivery/webapp/src/db/schema.ts` | `songs.lyricsRaw`, `songs.lyricsLines`, `recordings.r2LrcUrl`, `recordings.lrcStatus`, `userLrcOverrides.lrcContent`. |

### Data Already Available in SongListItem

The `SongListItem` interface already carries:

- `recording.contentHash` — used to look up user LRC override + as R2 hash prefix
- `recording.hashPrefix` — used for R2 LRC path

No new fields need to be added to the songset editor's inline data model.

---

## Implementation Plan

### Step 1: New API Endpoint — `GET /api/lyrics/[recordingContentHash]`

**New file**: `delivery/webapp/src/app/api/lyrics/[recordingContentHash]/route.ts`

- **Route**: `GET /api/lyrics/{recordingContentHash}`
- **Auth**: require session (same pattern as existing lyrics/overrides route).
- **Resolution order**:
  1. Look up `userLrcOverrides` table by `(userId, recordingContentHash)`. If `lrcContent` is non-null, return `{ lrcContent, source: "override" }`.
  2. Else, look up `recordings` by `contentHash`. **Check `lrcStatus`**: if `lrcStatus === "missing"`, skip R2 and fall through to step 3.
  3. Else, call `r2Client.getLrcSignedUrl(hashPrefix)`. Before reading the response body, check the `Content-Length` header: if present and > 1MB (1,048,576 bytes), return `413 Payload Too Large`.
  4. Fetch the LRC text from the presigned URL (server-side `fetch`). A 404 or any network error (timeout, 5xx, DNS failure) is treated as "no R2 file" — fall through silently to step 5.
  5. Look up `songs.lyricsRaw` via `recordings.songId → songs.id` join. Return `lyricsRaw` as plain text with `source: "db-raw"`.
  6. If `lyricsRaw` is also null, return `{ lrcContent: null, source: null }`.
- **Response shape**: `{ "lrcContent": string | null, "source": "override" | "r2" | "db-raw" | null }`

### Step 2: Client-Side Lyrics Hook — `useSongLyrics`

**New file**: `delivery/webapp/src/hooks/useSongLyrics.ts`

A React hook that:
- Takes `recordingContentHash: string | undefined`
- Calls `GET /api/lyrics/{recordingContentHash}` on demand (triggered by expansion)
- Uses an `AbortController` per fetch. If the component unmounts or the `recordingContentHash` changes before the fetch completes, the previous request is aborted and its result discarded.
- Memoizes results in an internal `Map` cache keyed by `contentHash` so re-expansion is instant
- Returns `{ data: string | null, source: string | null, loading: boolean, error: string | null }`
- Only fetches when the row is expanded; does not pre-fetch all on mount

### Step 3: Expandable Song Row in SongList.tsx

**Modify**: `delivery/webapp/src/components/songset/SongList.tsx`

Changes in `SongList` component:
- Add `expandedItemId: string | null` state (lifted to SongList level for accordion behavior).
- Pass `isExpanded`, `onToggleExpand` down to each `SortableSongItem`.
- Accordion logic: when `onToggleExpand` is called for an item, if that item is already expanded, collapse it (`expandedItemId = null`). Otherwise, set it as expanded and collapse any previously expanded row.

Changes in `SortableSongItem`:
- New props: `isExpanded: boolean`, `onToggleExpand: () => void`.
- Add a `ChevronDown` icon immediately to the right of the song info area (before the transition/edit buttons). It rotates 180° via `transition-transform duration-200` when `isExpanded === true`.
- **Accessibility**: the chevron button (or the song info hit area) must have `aria-expanded={isExpanded}`, `aria-controls={lyricsPanelId}`, and the lyrics panel must have `role="region"` + matching `id`.
- When `isExpanded === true`, render a second `<div>` below the song info (inside the same `<Card>`, after the existing `CardContent`'s flex row) containing:
  - If loading: `<Loader2 className="animate-spin" />` + "Loading lyrics…"
  - If error: muted "Lyrics unavailable" text
  - If `lrcContent` is valid LRC (`isValidLRC()` returns true): parse with `parseLRC()` → render each `LRCLine`:
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
  - If `lrcContent` is plain text (`source === "db-raw"` or fails LRC validation): render line-by-line as plain text (`text-sm break-words`).
  - If `lrcContent` is null: show "No lyrics available for this recording."
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

---

## Files to Create

| File | Purpose |
|------|---------|
| `delivery/webapp/src/app/api/lyrics/[recordingContentHash]/route.ts` | Server-side lyrics resolution endpoint (override → R2 → DB lyrics_raw) |
| `delivery/webapp/src/hooks/useSongLyrics.ts` | React hook: fetch + cache lyrics by recordingContentHash |

## Files to Modify

| File | Changes |
|------|---------|
| `delivery/webapp/src/components/songset/SongList.tsx` | Add accordion expand state + lyrics panel rendering + chevron toggle; remove `onSelectSong` prop and click handlers |
| `delivery/webapp/src/components/songset/SongsetEditor.tsx` | Remove `onSelectSong` prop from `<SongList>` invocation |

## Imports

- `parseLRC`, `isValidLRC`, `LRCLine` from `@/lib/render/lrc-parser` (existing, no new dep)
- `Loader2`, `ChevronDown` from `lucide-react` (already imported Loader2; add ChevronDown)
- `useSongLyrics` from `@/hooks/useSongLyrics` (new)

## Edge Cases

1. **No recording**: Song row has `item.recording === null`. Lyrics expand shows "No lyrics available — recording missing."
2. **No LRC in R2 (404)**: API falls through to `songs.lyrics_raw`. If that's also null, returns `{ lrcContent: null, source: null }`.
3. **LRC file too large**: Checked via `Content-Length` header before reading body. Return 413 if exceeded.
4. **R2 not configured / network down**: Falls through to DB `lyrics_raw` silently. Graceful degradation.
5. **`lrcStatus === 'missing'`**: R2 fetch is skipped entirely; API queries DB directly.
6. **User toggles expansion mid-fetch**: Hook aborts the previous request via `AbortController`. Stale responses are discarded.
7. **Read-only mode**: Expansion still works in read-only mode. The chevron is always visible. No change needed.
8. **Rapid accordion toggling**: Because of client-side caching, toggling back to a recently-viewed song shows lyrics instantly without refetching.

## Testing

### Unit Tests
- `delivery/webapp/src/test/api/lyrics/recordingContentHash.test.ts`: mock DB + R2 client; test all three resolution paths + `lrcStatus === 'missing'` short-circuit + fallback when R2 returns 404 + 413 size cap + auth required.
- `delivery/webapp/src/test/hooks/useSongLyrics.test.ts`: mock fetch; test loading → success, error, caching behavior (second expansion doesn't refetch), abort on unmount/hash change.

### Component Tests
- `delivery/webapp/src/test/components/SongList-lyrics.test.tsx`: render SongList with mock items; click chevron → lyrics panel appears; click another chevron → first collapses, second expands (accordion); verify LRC parsing → timestamped display; verify mobile stacked layout below `md` breakpoint.

### Manual Testing
1. Open a songset with songs that have LRC files in R2 → expand → see timed lyrics.
2. Open a songset with songs that only have `lyrics_raw` → expand → see plain text lyrics.
3. Open a songset with songs that have a user LRC override → expand → see override content.
4. Open a songset with a song that has neither → expand → see "No lyrics available".
5. Verify drag-to-reorder still works smoothly (no accidental expansion while dragging).
6. Verify on mobile (`< 768px`) timestamps stack above lyric text.

## Out of Scope

- Editing lyrics from this panel (use the existing LRC editor / admin CLI).
- Synchronized auto-scroll during playback (future enhancement using `findCurrentLyricIndex()`).
