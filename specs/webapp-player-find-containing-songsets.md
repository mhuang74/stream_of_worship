---

# Webapp Player: Find Songsets Containing Currently Playing Song

## Problem

When a user plays a song from Songset A and then navigates away (e.g., to browse Songset C), they lose track of which songset contains the currently playing song. There is no way to discover which songsets contain the song currently loaded in the audio player.

## User Decisions

- **Scope**: Both — show the originating songset first (for quick return), then list ALL other songsets containing the song
- **UI Entry Point**: A "locate" icon button in the track info area of `AudioPlayerBar`, opening a popover listing containing songsets
- **Navigation Behavior**: Navigate to the songset detail page AND scroll to / highlight the specific song within it
- **Track Type Scope**: Songs only (type `"song"`); not shown for `"transition"` or `"lyrics-loop"` track types

## Current State

### What Exists

| Component | File | Status |
|-----------|------|--------|
| `AudioPlayerProvider` | `webapp/src/contexts/AudioPlayerContext.tsx` | Maintains `currentTrack` state, wraps entire app |
| `AudioTrack` type | `webapp/src/contexts/AudioPlayerContext.tsx:14-24` | Has `id` (e.g., `song-${songId}`), `title`, `artist`, `type`, but NO `songId` or `songsetId` field |
| `useAudioPlayer` hook | `webapp/src/hooks/useAudioPlayer.ts` | Exposes `playSong()` with `songId` in options, but does NOT pass `songId` into the `AudioTrack` |
| `AudioPlayerBar` | `webapp/src/components/audio/AudioPlayerBar.tsx` | Fixed bottom bar with track info, controls, lyrics, close button |
| `SongList` `handlePlaySong` | `webapp/src/components/songset/SongList.tsx:287-355` | Plays song via `play()` from `useAudioPlayerContext`, passes `song-${songId}` as track id |
| `SongsetEditorClient` | `webapp/src/app/songsets/[id]/SongsetEditorClient.tsx` | Handles `?new=true` and `?share=true` query params; no `?highlightSong=` support |
| `SortableSongItem` | `webapp/src/components/songset/SongList.tsx:87-100` | Renders individual songs; has `isPlaying` highlight but no `data-song-id` attribute |
| `songsets` table | `webapp/src/db/schema.ts:180-196` | Stores songset metadata with `id`, `userId`, `name`, `description` |
| `songsetItems` table | `webapp/src/db/schema.ts:198-220` | Junction table with `songsetId`, `songId`, `position` — allows reverse lookup |
| `listSongsetSummaries` | `webapp/src/lib/db/songsets.ts:337-422` | Lists songsets by user with search; no "by song" variant |
| `getSongsetEditorData` | `webapp/src/lib/db/songsets.ts:424+` | Loads songset detail with items; no reverse lookup |
| Popover component | shadcn/ui (already in project) | `Popover`, `PopoverContent`, `PopoverTrigger` available |

### What's Missing (The Gap)

1. **`AudioTrack` lacks `songId`** — The song ID is embedded inside the `id` string as `song-${songId}` but not stored as a structured field. Need to extract `songId` or add it to the type.
2. **No `songsetId` on `AudioTrack`** — The originating songset is not tracked when playback starts.
3. **No reverse lookup query** — No DB function or API endpoint to find all songsets containing a specific song ID.
4. **No `data-song-id` attribute** — `SortableSongItem` cards don't expose a `data-song-id` DOM attribute for scroll-to / highlight targeting.
5. **No `?highlightSong=` support** — `SongsetEditorClient` doesn't handle a query param to auto-highlight and scroll to a specific song on page load.

## Implementation Plan

### Phase 1: Enrich `AudioTrack` with Song Origin Metadata

#### 1a. Update `AudioTrack` interface

**File**: `webapp/src/contexts/AudioPlayerContext.tsx`

Add optional `songId` and `originSongsetId` fields to `AudioTrack`:

```typescript
export interface AudioTrack {
  id: string;
  title: string;
  artist: string;
  src: string;
  type: AudioTrackType;
  duration?: number;
  loopStart?: number;
  loopEnd?: number;
  recordingContentHash?: string;
  songId?: string;            // ADD — the song ID for type "song" tracks
  originSongsetId?: string;  // ADD — the songset the song was played from
}
```

#### 1b. Update `PlaySongOptions` interface

**File**: `webapp/src/hooks/useAudioPlayer.ts`

Add `originSongsetId` to `PlaySongOptions`:

```typescript
interface PlaySongOptions {
  songId: string;
  title: string;
  artist: string;
  src: string;
  duration?: number;
  recordingContentHash?: string;
  originSongsetId?: string;  // ADD
}
```

Update the `playSong` callback to pass `songId` and `originSongsetId` into the `AudioTrack`:

```typescript
const playSong = useCallback(
  (options: PlaySongOptions) => {
    const track: AudioTrack = {
      id: \`song-\${options.songId}\`,
      title: options.title,
      artist: options.artist,
      src: options.src,
      type: "song" as AudioTrackType,
      duration: options.duration,
      recordingContentHash: options.recordingContentHash,
      songId: options.songId,                      // ADD
      originSongsetId: options.originSongsetId,    // ADD
    };
    play(track);
  },
  [play]
);
```

#### 1c. Update `SongList.handlePlaySong` to pass `originSongsetId`

**File**: `webapp/src/components/songset/SongList.tsx`

The `SongList` component receives `songsetId` as a prop. When calling `play()`, pass `songId` and `originSongsetId`:

```typescript
play({
  id: \`song-\${songId}\`,
  title: item.song?.title || "Unknown Song",
  artist,
  src: publicUrl,
  type: "song",
  duration: recording.durationSeconds ?? undefined,
  recordingContentHash: recording.contentHash,
  songId: songId,                    // ADD
  originSongsetId: songsetId,        // ADD — passed from SongsetEditor → SongList
});
```

**Note**: If `SongList` does not currently receive `songsetId` as a prop, thread it down from `SongsetEditorClient` → `SongsetEditor` → `SongList`. Check the `SongsetEditor` wrapper component for the prop chain.

**Other call sites**: The `BrowseSheet` and `SemanticSearch` components also call `play()` for songs. These don't have a songset context, so `originSongsetId` should be omitted (it's optional). The `songId` field should still be added so the feature works for songs played from the browse sheet.

---

### Phase 2: Backend — Reverse Lookup Query & API

#### 2a. Add `findSongsetsContainingSong` DB function

**File**: `webapp/src/lib/db/songsets.ts`

Add a new exported function that queries all songsets containing a given song ID for a given user:

```typescript
export interface SongsetContainingSong {
  id: string;
  name: string;
  description: string | null;
  updatedAt: Date;
  itemCount: number;
  songPosition: number;  // position of the song in this songset
  isOrigin: boolean;      // whether this is the originating songset
}

export async function findSongsetsContainingSong(
  songId: string,
  userId: number,
  originSongsetId?: string | null
): Promise<SongsetContainingSong[]> {
  return timePageLoad("findSongsetsContainingSong", async () => {
    const rows = await db
      .select({
        id: songsets.id,
        name: songsets.name,
        description: songsets.description,
        updatedAt: songsets.updatedAt,
        itemCount: sql<number>\`count(\${songsetItems.id})::int\`,
        songPosition: songsetItems.position,
      })
      .from(songsets)
      .innerJoin(
        songsetItems,
        and(
          eq(songsetItems.songsetId, songsets.id),
          eq(songsetItems.songId, songId)
        )
      )
      .leftJoin(
        songsetItems as any,  // self-join for item count
        eq(songsetItems.songsetId, songsets.id)
      )
      .where(eq(songsets.userId, userId))
      .groupBy(songsets.id, songsets.name, songsets.description, songsets.updatedAt, songsetItems.position)
      .orderBy(desc(songsets.updatedAt));

    // Sort: origin songset first, then by updatedAt desc
    const sorted = rows.sort((a, b) => {
      if (originSongsetId) {
        if (a.id === originSongsetId) return -1;
        if (b.id === originSongsetId) return 1;
      }
      return b.updatedAt.getTime() - a.updatedAt.getTime();
    });

    return sorted.map((row) => ({
      id: row.id,
      name: row.name,
      description: row.description,
      updatedAt: row.updatedAt,
      itemCount: Number(row.itemCount ?? 0),
      songPosition: row.songPosition,
      isOrigin: row.id === originSongsetId,
    }));
  });
}
```

**Query note**: The `itemCount` sub-query needs careful implementation. A simpler alternative is to use a subquery or CTE. The key join is: `songset_items` where `song_id = $songId`, joined to `songsets` where `user_id = $userId`.

#### 2b. Add API route

**File**: `webapp/src/app/api/songs/[id]/songsets/route.ts` (NEW FILE)

```typescript
import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { findSongsetsContainingSong } from "@/lib/db/songsets";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  const session = await auth.api.getSession({ headers: request.headers });
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const { id: songId } = await params;
  const originSongsetId = request.nextUrl.searchParams.get("origin");

  const songsets = await findSongsetsContainingSong(
    songId,
    Number(session.user.id),
    originSongsetId ?? undefined
  );

  return NextResponse.json({ songsets });
}
```

**API**: `GET /api/songs/:songId/songsets?origin=<songsetId>`

Returns:
```json
{
  "songsets": [
    {
      "id": "ss-abc",
      "name": "Sunday Worship Set",
      "description": "...",
      "updatedAt": "2026-01-15T...",
      "itemCount": 5,
      "songPosition": 2,
      "isOrigin": true
    },
    ...
  ]
}
```

---

### Phase 3: Player Bar UI — Locate Button & Popover

#### 3a. Create `LocateSongsetsPopover` component

**File**: `webapp/src/components/audio/LocateSongsetsPopover.tsx` (NEW FILE)

A popover triggered by a "locate" icon button. It:
1. Reads `currentTrack` from `useAudioPlayer()`
2. Only shows for tracks with `type === "song"` and a valid `songId`
3. On open, fetches `GET /api/songs/\${songId}/songsets?origin=\${originSongsetId}\` 
4. Renders a list of songsets; clicking one navigates to \`/songsets/\${id}?highlightSong=\${songId}\`
5. Shows a loading spinner while fetching
6. Shows "No songsets found" if empty
7. The originating songset is visually distinguished (badge: "Origin")

```tsx
"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAudioPlayer } from "@/hooks/useAudioPlayer";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { MapPin, Loader2, ListMusic, ArrowUpRight } from "lucide-react";
import { cn } from "@/lib/utils";

interface ContainingSongset {
  id: string;
  name: string;
  description: string | null;
  updatedAt: string;
  itemCount: number;
  songPosition: number;
  isOrigin: boolean;
}

export function LocateSongsetsPopover() {
  const { currentTrack } = useAudioPlayer();
  const router = useRouter();
  const [isOpen, setIsOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [songsets, setSongsets] = useState<ContainingSongset[]>([]);
  const [error, setError] = useState<string | null>(null);

  const songId = currentTrack?.type === "song" ? currentTrack.songId : undefined;
  const originSongsetId = currentTrack?.originSongsetId;

  const handleOpenChange = useCallback(async (open: boolean) => {
    setIsOpen(open);
    if (!open || !songId) return;

    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (originSongsetId) params.set("origin", originSongsetId);
      const res = await fetch(\`/api/songs/\${songId}/songsets?\${params}\`);
      if (!res.ok) throw new Error("Failed to load songsets");
      const data = await res.json();
      setSongsets(data.songsets);
    } catch {
      setError("Failed to load songsets");
    } finally {
      setLoading(false);
    }
  }, [songId, originSongsetId]);

  const handleSongsetClick = useCallback(
    (songsetId: string) => {
      router.push(\`/songsets/\${songsetId}?highlightSong=\${songId}\`);
      setIsOpen(false);
    },
    [router, songId]
  );

  if (!songId) return null;

  return (
    <Popover open={isOpen} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          className="shrink-0"
          aria-label="Find containing songsets"
          title="Find in songsets"
        >
          <MapPin className="size-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        className="w-72 p-0"
        align="start"
      >
        {loading ? (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <div className="px-3 py-4 text-sm text-destructive">{error}</div>
        ) : songsets.length === 0 ? (
          <div className="px-3 py-4 text-sm text-muted-foreground">
            This song is not in any of your songsets.
          </div>
        ) : (
          <div className="flex flex-col max-h-64 overflow-y-auto">
            {songsets.map((ss) => (
              <button
                key={ss.id}
                onClick={() => handleSongsetClick(ss.id)}
                className="flex items-center gap-3 px-3 py-2 text-left hover:bg-accent transition-colors border-b last:border-b-0"
              >
                <ListMusic className="size-4 text-muted-foreground shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{ss.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {ss.itemCount} songs • Position {ss.songPosition + 1}
                  </p>
                </div>
                {ss.isOrigin && (
                  <span className="text-xs text-primary shrink-0 font-medium">
                    Origin
                  </span>
                )}
                <ArrowUpRight className="size-3.5 text-muted-foreground shrink-0" />
              </button>
            ))}
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
```

#### 3b. Add `LocateSongsetsPopover` to `AudioPlayerBar`

**File**: `webapp/src/components/audio/AudioPlayerBar.tsx`

Import and render the popover in the track info section, next to the title/artist. Place it after the title/artist block, before the controls:

```tsx
import { LocateSongsetsPopover } from "./LocateSongsetsPopover";
```

In the track info area (after the title/artist `div`, within the `flex items-center gap-3 min-w-0 flex-1` container):

```tsx
<div className="flex items-center gap-3 min-w-0 flex-1 lg:flex-none">
  {/* Album art placeholder */}
  <div className="shrink-0 w-10 h-10 lg:w-12 lg:h-12 rounded-md bg-muted flex items-center justify-center">
    <Music className="size-5 lg:size-6 text-muted-foreground" />
  </div>

  {/* Title and artist */}
  <div className="min-w-0 flex-1">
    <p className="font-medium text-sm truncate" data-testid="track-title">
      {currentTrack.title}
    </p>
    <p className="text-xs text-muted-foreground truncate" data-testid="track-artist">
      {currentTrack.artist}
      {/* ... existing type badges ... */}
    </p>
  </div>

  {/* ADD: Locate containing songsets */}
  <LocateSongsetsPopover />
</div>
```

**Visibility**: The popover trigger only renders for `type === "song"` tracks (handled inside `LocateSongsetsPopover` via the `if (!songId) return null` guard). For `"transition"` and `"lyrics-loop"` tracks, it won't render.

---

### Phase 4: Songset Detail Page — Highlight & Scroll-to Song

#### 4a. Add `data-song-id` attribute to `SortableSongItem`

**File**: `webapp/src/components/songset/SongList.tsx`

On the outermost `div` of `SortableSongItem` (line ~132), add `data-song-id`:

```tsx
<div
  ref={setNodeRef}
  style={style}
  data-song-id={item.songId}          // ADD
  className={cn(
    "group",
    isDragging && "opacity-50"
  )}
>
```

#### 4b. Handle `?highlightSong=` query param in `SongsetEditorClient`

**File**: `webapp/src/app/songsets/[id]/SongsetEditorClient.tsx`

Read the `highlightSong` param and pass it to `SongsetEditor` → `SongList`:

```typescript
const highlightSongId = searchParams.get("highlightSong");
```

Pass down through the component chain:

```tsx
<SongsetEditor
  songset={songset}
  items={items}
  highlightSongId={highlightSongId}  // ADD prop
  // ... existing props
/>
```

Clean the URL after consuming:

```typescript
useEffect(() => {
  if (highlightSongId) {
    router.replace(\`/songsets/\${songsetId}\`);
  }
}, [highlightSongId, songsetId, router]);
```

#### 4c. Thread `highlightSongId` through `SongsetEditor` → `SongList`

**File**: `webapp/src/components/songset/SongsetEditor.tsx`

Accept `highlightSongId` prop and pass it to `SongList`.

**File**: `webapp/src/components/songset/SongList.tsx`

Accept `highlightSongId` prop in `SongList`.

#### 4d. Scroll to & highlight the target song in `SongList`

**File**: `webapp/src/components/songset/SongList.tsx`

Add a `useEffect` in the `SongList` component body that scrolls to the matching song and applies a temporary highlight:

```typescript
const highlightSongIdRef = useRef<string | null>(null);
highlightSongIdRef.current = highlightSongId ?? null;

const [highlightedSongId, setHighlightedSongId] = useState<string | null>(null);

useEffect(() => {
  if (!highlightSongId) return;
  
  // Wait for DOM to settle after render
  const timer = setTimeout(() => {
    const el = document.querySelector(\`[data-song-id="\${highlightSongId}"]\`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      setHighlightedSongId(highlightSongId);
      // Remove highlight after 3 seconds
      setTimeout(() => setHighlightedSongId(null), 3000);
    }
  }, 300); // small delay for layout/drag-and-drop to settle

  return () => clearTimeout(timer);
}, [highlightSongId]);
```

Pass `highlightedSongId` to `SortableSongItem` as a new prop:

```tsx
<SortableSongItem
  // ... existing props
  isHighlighted={highlightedSongId === item.songId}
/>
```

#### 4e. Apply highlight style in `SortableSongItem`

**File**: `webapp/src/components/songset/SongList.tsx`

Add `isHighlighted` to `SortableSongItemProps`:

```typescript
interface SortableSongItemProps {
  // ... existing props
  isHighlighted?: boolean;  // ADD
}
```

Apply highlight styling on the `<Card>`:

```tsx
<Card className={cn(
  "border-border/50 hover:border-border transition-colors",
  isPlaying && "border-primary/30 bg-primary/5",
  confirmRemove && "border-destructive/40 bg-destructive/5",
  isHighlighted && "ring-2 ring-primary border-primary animate-pulse"
)}>
```

---

## Implementation Order

1. **Phase 1**: Enrich `AudioTrack` with `songId` and `originSongsetId`
   - Update `AudioTrack` interface in `AudioPlayerContext.tsx`
   - Update `PlaySongOptions` in `useAudioPlayer.ts`
   - Update `SongList.handlePlaySong` to pass `songId` and `originSongsetId`
   - Update `BrowseSheet` / `SemanticSearch` play calls to include `songId` (no `originSongsetId`)

2. **Phase 2**: Backend reverse lookup
   - Add `findSongsetsContainingSong` in `lib/db/songsets.ts`
   - Create API route `app/api/songs/[id]/songsets/route.ts`

3. **Phase 3**: Player bar UI
   - Create `LocateSongsetsPopover` component
   - Add it to `AudioPlayerBar` track info section

4. **Phase 4**: Songset detail page highlight
   - Add `data-song-id` to `SortableSongItem`
   - Handle `?highlightSong=` param in `SongsetEditorClient`
   - Thread `highlightSongId` prop through `SongsetEditor` → `SongList`
   - Add scroll-to + highlight effect in `SongList`
   - Apply ring/pulse highlight style on target `SortableSongItem`

## Files to Create

| File | Description |
|------|-------------|
| `webapp/src/components/audio/LocateSongsetsPopover.tsx` | Popover component triggered from player bar |
| `webapp/src/app/api/songs/[id]/songsets/route.ts` | API route for reverse songset lookup |

## Files to Modify

| File | Changes |
|------|---------|
| `webapp/src/contexts/AudioPlayerContext.tsx` | Add `songId`, `originSongsetId` to `AudioTrack` |
| `webapp/src/hooks/useAudioPlayer.ts` | Add `originSongsetId` to `PlaySongOptions`, pass `songId` in track |
| `webapp/src/components/songset/SongList.tsx` | Pass `songId`/`originSongsetId` in `play()` call; add `data-song-id`; scroll + highlight effect |
| `webapp/src/components/audio/AudioPlayerBar.tsx` | Render `LocateSongsetsPopover` in track info |
| `webapp/src/app/songsets/[id]/SongsetEditorClient.tsx` | Handle `?highlightSong=` param; clean URL |
| `webapp/src/components/songset/SongsetEditor.tsx` | Thread `highlightSongId` prop to `SongList` |
| `webapp/src/lib/db/songsets.ts` | Add `findSongsetsContainingSong` function |
| Any other `play()` call site for songs | Add `songId` field (BrowseSheet, SemanticSearch, etc.) |

## Testing

### Unit Tests

1. **`findSongsetsContainingSong`** — mock DB, verify correct join/query, origin sorting, empty results
2. **API route** — auth check, correct response shape, `origin` param handling
3. **`LocateSongsetsPopover`** — renders only for `type === "song"` tracks, fetches on open, handles loading/error/empty states

### Integration Tests

4. **E2E flow**: Play a song from Songset A → click locate → see Songset A in list with "Origin" badge → click it → navigate to Songset A detail page → song is scrolled into view and highlighted
5. **Multi-songset scenario**: Song appears in Songsets A, B, and C → play from A → locate shows all three with A first
6. **No origin**: Play from BrowseSheet (no songset context) → locate shows all containing songsets, none marked as "Origin"
7. **Non-song tracks**: Play a transition preview or lyrics loop → locate button is not visible
8. **Empty results**: Play a song not in any songset → locate shows "This song is not in any of your songsets"

### Manual Verification

9. Verify highlight auto-dismisses after ~3 seconds
10. Verify "smooth" scroll puts the target song in the center of the viewport
11. Verify the URL is cleaned (`?highlightSong=` removed) after scroll/highlight completes
12. Verify mobile responsiveness — popover fits within viewport
13. Verify existing `?new=true` and `?share=true` param handling still works alongside `?highlightSong=`

## Edge Cases & Considerations

- **Deleted songs**: If a song was removed from all songsets after playback started, the API returns empty list. Show "not in any songsets" message.
- **Multiple recordings of same song**: The lookup is by `songId`, not `recordingHashPrefix`. All songsets containing the same song ID will match regardless of which recording was used.
- **Shared songsets from other users**: Only the current user's songsets are returned (`userId` filter). Songsets shared by other users are not included in this phase.
- **Auto-play policy**: Opening the popover triggers an API call but no audio playback, so no autoplay restriction concerns.
- **Popover dismissal**: Popover should close on route navigation (handled by existing `AudioPlayerBar` route change effect or Next.js behavior).

