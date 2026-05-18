# Browse Songs: Song Preview/Play Implementation Plan

## Problem

Users browsing songs in the `BrowseSheet` (and `SemanticSearch` tab) cannot listen to songs before adding them to a songset. There is no play/preview button on `SongCard`. The global audio player infrastructure (`AudioPlayerContext`, `AudioPlayerBar`, `useAudioPlayer`) and signed URL API (`/api/signed-url`) already exist and work for transition previews, but are not wired to the browse UI.

## Current State

### What Exists (Infrastructure)

| Component | File | Status |
|-----------|------|--------|
| `AudioPlayerContext` | `webapp/src/contexts/AudioPlayerContext.tsx` | Fully functional, wraps entire app |
| `useAudioPlayer` hook | `webapp/src/hooks/useAudioPlayer.ts` | Exposes `playSong()`, not used in browse |
| `AudioPlayerBar` | `webapp/src/components/audio/AudioPlayerBar.tsx` | Fixed bottom bar, auto-shows when track loaded |
| `GlobalAudioPlayer` | `webapp/src/components/audio/GlobalAudioPlayer.tsx` | Provider + Bar, in root layout |
| `/api/signed-url` | `webapp/src/app/api/signed-url/route.ts` | GET/POST, accepts `hashPrefix` + `fileType: "audio"` |
| Transition preview pattern | `webapp/src/components/transition/TransitionSheet.tsx:44-75` | Reference: fetch signed URL → `play()` |

### What's Missing (The Gap)

1. **No play/preview button in `SongCard`** — only "Add to songset" button exists (`SongCard.tsx:114-134`)
2. **No `onPlay` prop on `SongCard`** — no callback mechanism for play actions
3. **No `isPlaying` state tracking** — no way to show which song is currently playing
4. **`useAudioPlayer` not imported** in `BrowseSheet`, `SongCard`, or `SemanticSearch`
5. **No audio fetch logic** in browse components — no code to call `/api/signed-url` with `hashPrefix`

### Key Data Already Available

`SongCardData.recordings[].hashPrefix` is already present in the data returned by `/api/songs` and `/api/songs/search`. This is exactly what `/api/signed-url` needs to generate a streaming URL.

## Implementation Plan

### Step 1: Add Play Props to `SongCard`

**File:** `webapp/src/components/songset/SongCard.tsx`

**Changes:**

1. Add `Play`, `Pause`, `Loader2` to lucide-react imports (line 6)
2. Add new props to `SongCardProps` interface:

```typescript
interface SongCardProps {
  song: SongCardData;
  onAdd?: (songId: string) => void | Promise<void>;
  onPlay?: (songId: string) => void;          // NEW
  isAdded?: boolean;
  isAdding?: boolean;
  isPlaying?: boolean;                         // NEW
  isPreviewLoading?: boolean;                  // NEW
  className?: string;
}
```

3. Add a play button to the card UI, positioned **before** the add button in the action area (between song info and add button). The play button replaces the album art placeholder's `Disc` icon when hovered/playing:

**Design approach — Play button on album art area:**

When `onPlay` is provided, the album art placeholder (the `Disc` icon in the 48x48 rounded box at `SongCard.tsx:75-77`) becomes interactive:
- **Default state:** `Disc` icon (unchanged)
- **Hover state:** `Play` icon appears over the disc
- **Playing state:** `Pause` icon with a subtle background highlight (e.g., `bg-primary/10`)
- **Loading state:** `Loader2` spinning icon

This follows the common pattern seen in Spotify, Apple Music, etc. where the album art area doubles as a play button.

**Implementation details:**

Replace the static album art div (lines 75-77) with:

```tsx
<div
  className={cn(
    "shrink-0 w-12 h-12 rounded-md bg-muted flex items-center justify-center relative",
    onPlay && "cursor-pointer hover:bg-muted/80 transition-colors",
    isPlaying && "bg-primary/10"
  )}
  onClick={onPlay ? () => onPlay(song.id) : undefined}
  data-testid={onPlay ? "song-play-button" : "song-art-placeholder"}
  aria-label={isPlaying ? "Pause preview" : "Play preview"}
  role={onPlay ? "button" : undefined}
>
  {isPreviewLoading ? (
    <Loader2 className="size-6 animate-spin text-muted-foreground" />
  ) : isPlaying ? (
    <Pause className="size-6 text-primary" />
  ) : isHovered && onPlay ? (
    <Play className="size-6 text-primary ml-0.5" />
  ) : (
    <Disc className="size-6 text-muted-foreground" />
  )}
</div>
```

4. Destructure new props in the component function signature (line 34):

```typescript
export function SongCard({
  song,
  onAdd,
  onPlay,
  isAdded = false,
  isAdding = false,
  isPlaying = false,
  isPreviewLoading = false,
  className,
}: SongCardProps) {
```

### Step 2: Wire Audio Player in `BrowseSheet`

**File:** `webapp/src/components/songset/BrowseSheet.tsx`

**Changes:**

1. Add imports:

```typescript
import { useAudioPlayerContext } from "@/contexts/AudioPlayerContext";
```

2. Add state for tracking which song is playing and loading:

```typescript
const [playingSongId, setPlayingSongId] = useState<string | null>(null);
const [previewLoadingSongId, setPreviewLoadingSongId] = useState<string | null>(null);
```

3. Get audio player context:

```typescript
const { play, currentTrack, state: playerState } = useAudioPlayerContext();
```

4. Add `handlePlaySong` callback:

```typescript
const handlePlaySong = useCallback(
  async (songId: string) => {
    const song = results.find((r) => r.id === songId);
    if (!song || song.recordings.length === 0) {
      toast.error("No audio available for this song");
      return;
    }

    // If clicking the currently playing song, toggle pause/play
    if (playingSongId === songId && currentTrack?.id === `song-${songId}`) {
      if (playerState.isPlaying) {
        // Pause handled by AudioPlayerBar — but we need pause from context
        // Actually, we should use togglePlay or pause from context
        // For simplicity, clicking play on playing song = stop preview
        setPlayingSongId(null);
        return;
      }
    }

    const recording = song.recordings[0];
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

      if (!res.ok) {
        throw new Error("Failed to get audio URL");
      }

      const data = await res.json();
      const artist = song.composer || song.lyricist || "Unknown Artist";

      play({
        id: `song-${songId}`,
        title: song.title,
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
  [results, playingSongId, currentTrack, playerState.isPlaying, play]
);
```

5. Add effect to clear `playingSongId` when audio stops (track ends or is closed):

```typescript
useEffect(() => {
  if (!currentTrack || !playerState.isPlaying) {
    // Small delay to avoid flicker during track switches
    const timeout = setTimeout(() => {
      if (!currentTrack || !playerState.isPlaying) {
        setPlayingSongId(null);
      }
    }, 200);
    return () => clearTimeout(timeout);
  }
}, [currentTrack, playerState.isPlaying]);
```

6. Pass play props to `SongCard` in the browse results (line 282-288):

```tsx
<SongCard
  key={song.id}
  song={song}
  onAdd={handleAddSong}
  onPlay={handlePlaySong}
  isAdded={isSongAdded(song.id)}
  isAdding={isSongAdding(song.id)}
  isPlaying={playingSongId === song.id}
  isPreviewLoading={previewLoadingSongId === song.id}
/>
```

7. Reset playing state when sheet closes (in the existing close effect, line 118-128):

Add to the cleanup:
```typescript
setPlayingSongId(null);
setPreviewLoadingSongId(null);
```

### Step 3: Wire Audio Player in `SemanticSearch`

**File:** `webapp/src/components/search/SemanticSearch.tsx`

**Changes:**

1. Add imports:

```typescript
import { useAudioPlayerContext } from "@/contexts/AudioPlayerContext";
import { toast } from "sonner";
```

2. Add state and context:

```typescript
const [playingSongId, setPlayingSongId] = useState<string | null>(null);
const [previewLoadingSongId, setPreviewLoadingSongId] = useState<string | null>(null);
const { play, currentTrack, state: playerState } = useAudioPlayerContext();
```

3. Add `handlePlaySong` callback (same logic as `BrowseSheet`, but using `results` from SemanticSearch's own state).

4. Add the same `useEffect` for clearing `playingSongId` when audio stops.

5. Pass play props to `SongCard` (line 152-157):

```tsx
<SongCard
  song={song}
  onAdd={onAddSong}
  onPlay={handlePlaySong}
  isAdded={isSongAdded(song.id)}
  isAdding={isSongAdding(song.id)}
  isPlaying={playingSongId === song.id}
  isPreviewLoading={previewLoadingSongId === song.id}
/>
```

### Step 4: Handle Songs Without Recordings

Some songs in the catalog may have no recordings (e.g., `mockSongNoRecording` in tests). The play button should gracefully handle this:

- If `song.recordings.length === 0`, the `onPlay` handler shows a toast: "No audio available for this song"
- The album art area remains a non-interactive `Disc` icon (no play-on-hover behavior)
- This is already handled by the `handlePlaySong` logic above

### Step 5: Update Tests

**File:** `webapp/src/test/components/songset/SongCard.test.tsx`

Add test cases:

1. **Rendering:**
   - Renders play button area when `onPlay` is provided
   - Does not render play interaction when `onPlay` is not provided
   - Shows `Play` icon on hover when `onPlay` is provided
   - Shows `Pause` icon when `isPlaying` is true
   - Shows `Loader2` spinner when `isPreviewLoading` is true
   - Shows `Disc` icon when not hovered and not playing

2. **Interaction:**
   - Calls `onPlay` with song ID when play area is clicked
   - Does not call `onPlay` when `onPlay` is not provided
   - Has correct `aria-label` for play/pause states
   - Has `role="button"` when `onPlay` is provided

3. **Accessibility:**
   - Play area has `data-testid="song-play-button"` when interactive
   - Play area has `data-testid="song-art-placeholder"` when not interactive

**File:** `webapp/src/test/components/songset/BrowseSheet.test.tsx`

Add test cases:

1. **Play functionality:**
   - Renders song cards with play buttons
   - Calls `/api/signed-url` when play button is clicked
   - Calls `play()` from audio context with correct track data
   - Shows loading state while fetching signed URL
   - Shows error toast when signed URL fetch fails
   - Shows error toast when song has no recordings
   - Clears playing state when sheet closes

2. **Mock setup:**
   - Mock `AudioPlayerContext` in test (follow pattern from `TransitionSheet.test.tsx:18`)
   - Mock `/api/signed-url` fetch response

**File:** `webapp/src/test/components/search/SemanticSearch.test.tsx` (if exists, or create)

Add similar play functionality tests as BrowseSheet.

## Files to Modify/Create

| File | Action | Purpose |
|------|--------|---------|
| `webapp/src/components/songset/SongCard.tsx` | Modify | Add `onPlay`, `isPlaying`, `isPreviewLoading` props; make album art interactive |
| `webapp/src/components/songset/BrowseSheet.tsx` | Modify | Wire `useAudioPlayerContext`, add `handlePlaySong`, pass play props to `SongCard` |
| `webapp/src/components/search/SemanticSearch.tsx` | Modify | Wire `useAudioPlayerContext`, add `handlePlaySong`, pass play props to `SongCard` |
| `webapp/src/test/components/songset/SongCard.test.tsx` | Modify | Add play button tests |
| `webapp/src/test/components/songset/BrowseSheet.test.tsx` | Modify | Add play functionality tests |

## Design Decisions

### 1. Play button placement: Album art overlay vs. separate button

**Chosen: Album art overlay** (Play/Pause icon replaces Disc on hover/playing)

Rationale:
- Follows established UX pattern (Spotify, Apple Music, YouTube Music)
- Doesn't add horizontal width to the card (important in a bottom sheet)
- The 48x48 album art area is already a natural touch target
- Keeps the "Add" button as the only right-side action, avoiding button clutter

### 2. Play state management: Component-local vs. global

**Chosen: Component-local (`playingSongId` state in BrowseSheet/SemanticSearch)**

Rationale:
- The global `AudioPlayerContext` already tracks `currentTrack` and `isPlaying`
- But we need to know which *specific song card* is playing (by song ID), not just the track ID
- Local state is simpler and avoids coupling the context to browse-specific concerns
- We sync local state with global state via `useEffect` on `currentTrack`/`isPlaying`

### 3. Signed URL fetching: In-component vs. dedicated hook

**Chosen: In-component (inline fetch in `handlePlaySong`)**

Rationale:
- The pattern is simple: one POST to `/api/signed-url`, then `play()`
- Only 2 components need it (BrowseSheet, SemanticSearch)
- If more components need it later, we can extract a `useSongPreview` hook
- Matches the existing pattern in `TransitionSheet.tsx:44-75`

### 4. Toggle behavior: Click playing song = stop vs. pause

**Chosen: Click playing song = stop (clear `playingSongId`)**

Rationale:
- The `AudioPlayerBar` at the bottom provides full play/pause/seek controls
- The card's play button is a "start preview" affordance, not a full transport control
- Stopping (rather than pausing) is simpler and avoids needing to import `pause` from context
- Users can pause/resume from the `AudioPlayerBar`

### 5. No new API endpoint needed

The existing `/api/signed-url` endpoint with `hashPrefix` + `fileType: "audio"` already serves this purpose. The transition preview uses a dedicated `/api/transitions/preview` endpoint because it needs to concatenate two recordings. For single-song preview, the generic signed URL endpoint is sufficient.

## Verification

### Manual Testing

1. Run the webapp: `cd webapp && pnpm dev`
2. Navigate to a songset editor page
3. Open "Browse Songs" sheet
4. Verify:
   - [ ] Song cards show `Disc` icon in album art area
   - [ ] Hovering over album art shows `Play` icon
   - [ ] Clicking play area fetches signed URL and starts playback
   - [ ] `AudioPlayerBar` appears at bottom with song title and artist
   - [ ] Currently playing song card shows `Pause` icon with highlighted background
   - [ ] Clicking play on a different song switches to that song
   - [ ] Clicking play on the currently playing song stops playback
   - [ ] Loading spinner shows while fetching signed URL
   - [ ] Error toast appears if signed URL fetch fails
   - [ ] Error toast appears if song has no recordings
   - [ ] Playing state clears when sheet is closed
5. Switch to "Describe" tab
6. Verify:
   - [ ] Same play functionality works in semantic search results
   - [ ] Playing state is independent between Browse and Describe tabs (or shared — either is acceptable)

### Automated Tests

```bash
cd webapp && pnpm test
```

All existing tests must continue to pass. New tests for play functionality should cover the cases listed in Step 5.

## Out of Scope

- Waveform visualization on the card
- Progress indicator on the card itself (the `AudioPlayerBar` handles this)
- Keyboard shortcut to play a song from the browse list
- Playing a specific recording when a song has multiple recordings (always uses `recordings[0]`)
- Pre-fetching/caching signed URLs for faster playback start
- Volume control from the card
