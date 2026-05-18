# Songset Editor: Song Preview/Playback

## Goal

Add inline song preview playback to the Songset Editor's song list, matching the Browse Songs screen's playback UX. Users should be able to play/pause any song directly from the editor without navigating away.

## User Decisions

- **Play button placement**: Replace the song number (#1, #2, etc.) with a play/pause icon button (same pattern as SongCard's album-art-as-play-button)
- **Playing indicator**: Highlight the currently playing song row with a subtle background color + swap icon to pause
- **Playback persistence**: Audio continues playing regardless of user interaction with the editor (same as Browse Songs). Only stops if the playing song is removed from the songset.

## Files to Modify

### 1. `webapp/src/app/songsets/[id]/page.tsx`

**Problem**: The `SongListItem.recording` type is missing `hashPrefix`, which is needed to construct the audio URL. The API returns `recordingHashPrefix` as a top-level field on each item, but the page mapping drops it.

**Changes**:

- In the `setItems()` call inside `loadSongset()` (line ~117-131), map `item.recordingHashPrefix` into `recording.hashPrefix`:

```typescript
recording: item.recording
  ? {
      ...item.recording,
      hashPrefix: item.recordingHashPrefix ?? "",
    }
  : null,
```

- In the `handleAddSong` callback (line ~344-361), same mapping for the newly added item:

```typescript
recording: item.recording
  ? {
      ...item.recording,
      hashPrefix: item.recordingHashPrefix ?? "",
    }
  : null,
```

### 2. `webapp/src/components/songset/SongList.tsx`

This is the main file. Changes:

#### 2a. Update `SongListItem.recording` type

Add `hashPrefix: string` to the recording sub-object:

```typescript
recording: {
  contentHash: string;
  hashPrefix: string;               // ADD
  durationSeconds: number | null;
  tempoBpm: number | null;
  musicalKey: string | null;
} | null;
```

#### 2b. Add new imports

```typescript
import { useAudioPlayerContext } from "@/contexts/AudioPlayerContext";
import { getPublicAudioUrl } from "@/lib/r2/public-url";
import { toast } from "sonner";
import { Play, Pause, Loader2 } from "lucide-react";
```

#### 2c. Add playback state and logic to `SongList` component

Add inside `SongList` function body (before the `if (items.length === 0)` check):

```typescript
const { currentTrack, state: playerState, play } = useAudioPlayerContext();
const [playingSongId, setPlayingSongId] = useState<string | null>(null);
const [previewLoadingSongId, setPreviewLoadingSongId] = useState<string | null>(null);

const handlePlaySong = useCallback(
  async (songId: string) => {
    const item = localItems.find((i) => i.songId === songId);
    if (!item?.recording) {
      toast.error("No audio available for this song");
      return;
    }

    // Toggle pause if clicking the currently playing song
    if (playingSongId === songId && currentTrack?.id === `song-${songId}`) {
      if (playerState.isPlaying) {
        setPlayingSongId(null);
        return;
      }
    }

    const recording = item.recording;
    const artist = item.song?.composer || item.song?.lyricist || "Unknown Artist";
    const publicUrl = getPublicAudioUrl(recording.hashPrefix);

    if (publicUrl) {
      play({
        id: `song-${songId}`,
        title: item.song?.title || "Unknown Song",
        artist,
        src: publicUrl,
        type: "song",
        duration: recording.durationSeconds ?? undefined,
      });
      setPlayingSongId(songId);
      return;
    }

    // Fallback to signed URL
    setPreviewLoadingSongId(songId);

    try {
      const res = await fetch("/api/signed-url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          hashPrefix: recording.hashPrefix,
          fileType: "audio",
        }),
      });

      if (!res.ok) throw new Error("Failed to get audio URL");

      const data = await res.json();

      play({
        id: `song-${songId}`,
        title: item.song?.title || "Unknown Song",
        artist,
        src: data.url,
        type: "song",
        duration: recording.durationSeconds ?? undefined,
      });

      setPlayingSongId(songId);
    } catch {
      toast.error("Failed to load audio preview");
    } finally {
      setPreviewLoadingSongId(null);
    }
  },
  [localItems, playingSongId, currentTrack, playerState.isPlaying, play]
);

// Clear playing state when audio stops
useEffect(() => {
  if (!currentTrack || !playerState.isPlaying) {
    const timeout = setTimeout(() => {
      if (!currentTrack || !playerState.isPlaying) {
        setPlayingSongId(null);
      }
    }, 200);
    return () => clearTimeout(timeout);
  }
}, [currentTrack, playerState.isPlaying]);
```

#### 2d. Pass playback props to `SortableSongItem`

Update the `SortableSongItem` rendering in the map to pass:

```typescript
<SortableSongItem
  key={item.id}
  item={item}
  index={index}
  onRemove={onRemove}
  onEditTransition={onEditTransition}
  onSelectSong={onSelectSong}
  readOnly={readOnly}
  isPlaying={playingSongId === item.songId}
  isPreviewLoading={previewLoadingSongId === item.songId}
  onPlaySong={handlePlaySong}
/>
```

#### 2e. Update `SortableSongItemProps` interface

Add new props:

```typescript
interface SortableSongItemProps {
  item: SongListItem;
  index: number;
  onRemove: (itemId: string) => void;
  onEditTransition?: (itemId: string) => void;
  onSelectSong?: (itemId: string) => void;
  readOnly?: boolean;
  isPlaying?: boolean;              // ADD
  isPreviewLoading?: boolean;      // ADD
  onPlaySong?: (songId: string) => void;  // ADD
}
```

#### 2f. Replace song number with play/pause button in `SortableSongItem`

**Current** (line 129-132):
```tsx
<span className="text-sm font-medium text-muted-foreground w-6 text-center shrink-0">
  {index + 1}
</span>
```

**Replace with**:
```tsx
<Button
  variant="ghost"
  size="icon-sm"
  className={cn(
    "shrink-0 rounded-full",
    isPlaying && "bg-primary/10 text-primary"
  )}
  onClick={() => onPlaySong?.(item.songId)}
  aria-label={isPlaying ? `Pause ${item.song?.title || "song"}` : `Play ${item.song?.title || "song"}`}
  disabled={!item.recording}
>
  {isPreviewLoading ? (
    <Loader2 className="size-4 animate-spin" />
  ) : isPlaying ? (
    <Pause className="size-4" />
  ) : (
    <Play className="size-4 ml-0.5" />
  )}
</Button>
```

#### 2g. Add row highlight for currently playing song

On the `<Card>` element inside `SortableSongItem`, add conditional highlight:

**Current** (line 112):
```tsx
<Card className="border-border/50 hover:border-border transition-colors">
```

**Replace with**:
```tsx
<Card className={cn(
  "border-border/50 hover:border-border transition-colors",
  isPlaying && "border-primary/30 bg-primary/5"
)}>
```

### 3. `webapp/src/components/songset/SongsetEditor.tsx` — NO CHANGES NEEDED

Play logic is fully self-contained within `SongList`, following the same pattern as `BrowseSheet` and `SemanticSearch`.

## Implementation Order

1. Update `SongListItem.recording` type in `SongList.tsx` (add `hashPrefix`)
2. Update `page.tsx` to map `recordingHashPrefix` into `recording.hashPrefix`
3. Add imports to `SongList.tsx`
4. Add playback state + `handlePlaySong` + cleanup effect to `SongList` component
5. Update `SortableSongItemProps` interface
6. Replace song number with play/pause button in `SortableSongItem`
7. Add row highlight for playing song
8. Pass new props from `SongList` to `SortableSongItem`

## Testing

- Verify play/pause works for each song in the editor
- Verify the song number is replaced by play icon, which becomes pause when playing
- Verify the currently playing row has a subtle highlight
- Verify loading spinner shows while fetching signed URL
- Verify audio continues playing when interacting with other editor controls
- Verify the global `AudioPlayerBar` at the bottom reflects the playing song
- Verify playback from BrowseSheet still works (no regressions)
- Verify `readOnly` mode still works (play button should still function since it's read-only, not the audio)
