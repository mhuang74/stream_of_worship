# Songset Editor: Inline Lyrics Display

## Goal

Allow users to view song lyrics directly in the Songset Editor by expanding any song row. This provides a quick way to evaluate songs via lyrics prior to rendering, without needing to wait for or play back the rendered output.

## User Decisions

- **Source priority**: User LRC override (DB) → R2 LRC file (`{hashPrefix}/lyrics.lrc`) → `songs.lyrics_raw` (DB fallback)
- **Display format**: Timestamped lines — show `[mm:ss.xx]` timestamp alongside each lyric line, parsed from LRC content. For `lyrics_raw` fallback (raw text without timestamps), display plain line-by-line text.
- **Expansion behavior**: Single accordion — only one song's lyrics visible at a time. Expanding a new row collapses the previous.
- **LRC fetch strategy**: New API endpoint returning LRC text as JSON (server resolves override vs R2 vs song lyrics).

## Context

### Relevant Files

| File | Role |
|------|------|
| `delivery/webapp/src/components/songset/SongList.tsx` | Song row rendering (`SortableSongItem`), `SongListItem` interface. Already has `onSelectSong` prop wired to a no-op in SongsetEditor. |
| `delivery/webapp/src/components/songset/SongsetEditor.tsx` | Parent editor component; passes `onSelectSong={() => {}}` to SongList (line ~410). |
| `delivery/webapp/src/lib/render/lrc-parser.ts` | Existing `parseLRC()` function for parsing LRC content into `LRCLine[]` with `timeSeconds` + `text`. |
| `delivery/webapp/src/lib/r2/client.ts` | `getLrcSignedUrl(hashPrefix)` → presigned R2 URL for `{hashPrefix}/lyrics.lrc` (line 169). |
| `delivery/webapp/src/app/api/signed-url/route.ts` | Existing signed-url API; accepts `fileType: "lrc"` + `hashPrefix`. |
| `delivery/webapp/src/app/api/lyrics/overrides/route.ts` | Existing lyrics override API; GET returns `{ lrcContent: string | null }` by `recordingContentHash`. |
| `delivery/webapp/src/lib/db/songsets.ts` | `getSongsetEditorData()`: the DB query feeding the editor. Currently selects from `songsets`, `songs`, `recordings`, `lyricMarks`. Does NOT select `songs.lyrics_raw`. |
| `delivery/webapp/src/db/schema.ts` | `songs.lyricsRaw` (line 53), `songs.lyricsLines` (line 54). `recordings.r2LrcUrl` (line 83), `recordings.lrcStatus` (line 103). `userLrcOverrides.lrcContent` (line 387). |

### Data Already Available in SongListItem

The `SongListItem` interface (SongList.tsx:30) already carries what we need:

- `recording.contentHash` — used to look up user LRC override + as R2 hash prefix
- `recording.hashPrefix` — used for R2 LRC path

No new fields need to be added to the songset editor's inline data model; the new API endpoint will resolve the three source tiers server-side.

## Implementation Plan

### Step 1: New API Endpoint — `GET /api/lyrics/[recordingContentHash]`

**New file**: `delivery/webapp/src/app/api/lyrics/[recordingContentHash]/route.ts`

- **Route**: `GET /api/lyrics/{recordingContentHash}`
- **Auth**: require session (same pattern as existing lyrics/overrides route).
- **Query params**: none required (recordingContentHash is in the path).
- **Resolution order**:
  1. Look up `userLrcOverrides` table by `(userId, recordingContentHash)`. If `lrcContent` is non-null, return it.
  2. Else, resolve `hashPrefix` from `recordings` by `contentHash`. Call `r2Client.getLrcSignedUrl(hashPrefix)`. Fetch the LRC text from the presigned URL (server-side fetch). Return the text.
  3. Else (R2 fetch fails or no LRC file): look up `songs.lyricsRaw` via `recordings.songId → songs.id` join. Return `lyricsRaw` as plain text (no LRC timestamps).
- **Response**: `{ "lrcContent": string | null, "source": "override" | "r2" | "db-raw" }`
- **Reuse**: Use `r2Client.getLrcSignedUrl()` (existing) + server-side `fetch()` to GET the presigned URL content. Reuse `parseLRC()` for client-side parsing/validation.

** Why a path parameter instead of query param**: `recordingContentHash` is already unique per recording and the existing `/api/lyrics/overrides` route uses query params — but using a path param makes this a cleaner REST resource. Either works; follow REST convention here.

### Step 2: Client-Side Lyrics Hook — `useSongLyrics`

**New file**: `delivery/webapp/src/hooks/useSongLyrics.ts`

A small React hook that:
- Takes `recordingContentHash: string | undefined`
- Calls `GET /api/lyrics/{recordingContentHash}` on demand (triggered by expansion)
- Memoizes results in an internal cache (Map keyed by contentHash) so re-expansion is instant
- Returns `{ data: string | null, source: string | null, loading: boolean, error: string | null }`
- Only fetches when the row is expanded; does not pre-fetch all on mount

### Step 3: Expandable Song Row in SongList.tsx

**Modify**: `delivery/webapp/src/components/songset/SongList.tsx`

Changes in `SongList` component:
- Add `expandedItemId: string | null` state (lifted to SongList level for accordion behavior).
- Pass `isExpanded`, `onToggleExpand` down to each `SortableSongItem`.

Changes in `SortableSongItem`:
- New props: `isExpanded: boolean`, `onToggleExpand: () => void`.
- Wire `onSelectSong` (existing click handler on the song info area) to also toggle the expand state. Currently `onSelectSong` is called on click; we'll repurpose this or add a dedicated expand toggle.
- Add a `ChevronDown` icon that rotates 180° when expanded (visual affordance).
- When `isExpanded === true`, render a second `<div>` below the song info (inside the same `<Card>`, after the existing `CardContent`'s flex row) containing:
  - If loading: `<Loader2 className="animate-spin" />` + "Loading lyrics…"
  - If error: muted "Lyrics unavailable" text
  - If `lrcContent` is valid LRC (use `isValidLRC()` from lrc-parser): parse with `parseLRC()` → render each `LRCLine` as a row showing `[mm:ss.xx]` timestamp (monospace, muted) + text.
  - If `lrcContent` is plain text (source === "db-raw" or fails LRC validation): render line-by-line as plain text.
  - If `lrcContent` is null: show "No lyrics available for this recording."

**Formatting of timed lyrics**:
```
[00:12.34]  赞美耶和华
[00:18.50]  从日出之地到日落之处
```
Timestamp in `font-mono text-xs text-muted-foreground`, lyric text in `text-sm`. Line spacing for readability. Container should be `max-h-[400px] overflow-y-auto` so very long lyrics don't overwhelm the screen.

### Step 4: Wire Accordion State in SongsetEditor.tsx

**Modify**: `delivery/webapp/src/components/songset/SongsetEditor.tsx`

- The existing `onSelectSong={() => {}}` (line ~410) is a no-op. Either:
  - **(A) Lift accordion state here**: SongsetEditor manages `expandedItemId` state and passes it + setter down to SongList.
  - **(B) Keep state in SongList**: Simpler — SongList manages its own expand state internally. `onSelectSong` callback can be removed or kept for future use.

**Recommendation: (B)** — Keep expansion state internal to SongList. The SongsetEditor does not need to know which row is expanded. `onSelectSong` stays as a no-op (or gets removed). This minimizes blast radius.

## Files to Create

| File | Purpose |
|------|---------|
| `delivery/webapp/src/app/api/lyrics/[recordingContentHash]/route.ts` | Server-side lyrics resolution endpoint (override → R2 → DB lyrics_raw) |
| `delivery/webapp/src/hooks/useSongLyrics.ts` | React hook: fetch + cache lyrics by recordingContentHash |

## Files to Modify

| File | Changes |
|------|---------|
| `delivery/webapp/src/components/songset/SongList.tsx` | Add accordion expand state + lyrics panel rendering in SortableSongItem |
| `delivery/webapp/src/components/songset/SongsetEditor.tsx` | No change needed if state stays internal to SongList (option B) |

## Imports

- `parseLRC`, `isValidLRC`, `LRCLine` from `@/lib/render/lrc-parser` (existing, no new dep)
- `Loader2`, `ChevronDown` from `lucide-react` (already imported Loader2; add ChevronDown)
- `useSongLyrics` from `@/hooks/useSongLyrics` (new)

## Edge Cases

1. **No recording**: Song row has `item.recording === null`. Lyrics expand shows "No lyrics available — recording missing."
2. **No LRC in R2 (404)**: API falls through to `songs.lyrics_raw`. If that's also null, returns `{ lrcContent: null, source: null }`.
3. **LRC file too large**: Cap response server-side at 1MB (matches render worker's `MAX_LRC_SIZE_BYTES`). Return 413 if exceeded.
4. **R2 not configured**: Endpoint returns `lyrics_raw` from DB if available; otherwise null. Graceful degradation.
5. **User toggles expansion mid-fetch**: Hook should handle stale responses (use AbortController or ignore if contentHash changed).
6. **Read-only mode**: Expansion still works in read-only mode (read-only disables drag/reorder, not viewing lyrics). No change needed.

## Testing

### Unit Tests
- `delivery/webapp/src/test/api/lyrics/[recordingContentHash].test.ts`: mock DB + R2 client; test all three resolution paths + fallback when R2 returns 404 + auth required.
- `delivery/webapp/src/test/hooks/useSongLyrics.test.ts`: mock fetch; test loading → success, error, caching behavior (second expansion doesn't refetch).

### Component Tests
- `delivery/webapp/src/test/components/SongList-lyrics.test.tsx`: render SongList with mock items; click song row → lyrics panel appears; click another row → first collapses, second expands (accordion); verify LRC parsing → timestamped display.

### Manual Testing
1. Open a songset with songs that have LRC files in R2 → expand → see timed lyrics.
2. Open a songset with songs that only have `lyrics_raw` → expand → see plain text lyrics.
3. Open a songset with songs that have a user LRC override → expand → see override content.
4. Open a songset with a song that has neither → expand → see "No lyrics available".

## Out of Scope

- Editing lyrics from this panel (use the existing LRC editor / admin CLI).
- Synchronized auto-scroll during playback (could be a future enhancement using the existing `findCurrentLyricIndex()` helper).
- Mobile lyrics formatting differences (same responsive container works for both).
