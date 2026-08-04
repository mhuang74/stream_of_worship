---
# Songset Editor: Player-Integrated Lyrics Display — v4

[Supersedes v3 (`songset-editor-inline-lyrics-v3.md`). The inline SongList expansion approach is replaced by lyrics shown in the global AudioPlayerBar.]

## Goal

Replace the vague-looking arrow (ChevronDown) that expands lyrics below each Song Row in the Songset Management screen. Instead, show lyrics inside the global AudioPlayerBar. A new "Lyrics" button in the Player expands the Player *upward* to reveal lyrics. Clicking again collapses the Player back to its original height and hides lyrics.

This avoids polluting the Song Row layout with expand/collapse state, eliminates the drag-v-expand interaction conflicts, and dedicates a consistent, persistent lyrics surface tied to the currently playing song.

## What Changed from v3

| Aspect | v3 (Inline) | v4 (Player-Integrated) |
|--------|-------------|------------------------|
| Write location | This file | This file |
| Lyrics display | Below each Song Row in `SongList.tsx`, via per-row `ChevronDown` | Inside global `AudioPlayerBar`, via "Lyrics" toggle button that grows the player upward |
| Lyrics trigger | Each Song Row's expand chevron | "Lyrics" button in the Player Bar |
| Lyrics target song | The row the user expanded (not necessarily the playing song) | The currently playing track (the song the user is listening to) |
| Accordion behavior | One row expanded at a time | Single panel; toggled by button |
| Drag interaction | `onDragStart` auto-collapse hack needed | Not needed; no Song Row lyrics |
| `AudioTrack.recordingContentHash` | Not present | Added (required for lyrics lookup) |
| `useSongLyrics` hook | Created in v3 | Reused as-is (no changes) |
| `/api/lyrics/[recordingContentHash]` endpoint | Created in v3 | Reused as-is (no changes) |

## Product Decisions

- **Lyrics button visible on**: Tracks of type `song` and `lyrics-loop` (both originate from a recording with a `contentHash`). **Hidden** on `transition` type — transitions span two recordings and have no meaningful per-song lyrics.
- **Lyrics button always clickable**: When a qualifying track is loaded, the Lyrics button is always enabled. The panel shows: a loading spinner during fetch, an error message on failure, or an empty-state when lyrics are null. The button is never greyed out based on lyrics availability.
- **Panel persists across track changes**: If the lyrics panel is open and a new song starts, the panel **stays open** and automatically swaps to the new track's lyrics. If the new track's lyrics are cached (module-scoped cache from v3), the swap is instant with no spinner.
- **Panel auto-collapses on stop**: When the user closes the player (X button) or the track is cleared, `showLyrics` resets to `false`. The next track starts with the panel collapsed by default.
- **Synchronized auto-scroll is out of scope**: No line highlighting or auto-scroll based on playback `currentTime`. This is a static display (same as v3). Future enhancement candidates: per-line highlighting via `findCurrentLyricIndex()`, click-to-seek on timestamp.
- **Animated expand/collapse**: CSS `max-height` transition for smooth upward growth. The lyrics panel appears above the existing seek bar + track info + controls row, within the same `fixed bottom-0` container. Since the container is bottom-anchored, growing its height naturally extends it upward.
- **Module-scoped cache reuse**: v3's `useSongLyrics` hook has a module-scoped `Map<contentHash, Result>`. This is reused unchanged. A song whose lyrics were fetched while in the Songset Editor (or any other screen) will be instantly available when played in the Player.
- **LRC rendering**: Same as v3 — `parseLRC()` + `isValidLRC()`. Timestamps side-by-side on desktop (`md:`+), stacked vertically on mobile (`< md`). Plain `lyricsRaw` and `lyricsLines` rendered as `<pre>` blocks.
- **No `ChevronDown` removal yet**: `ChevronDown` is still imported in `SongList.tsx` for use elsewhere if needed; only the chevron *button* (the lyrics expand toggle) is removed. The import can be cleaned up if it becomes unused.

## Context

### Relevant Files

| File | Role |
|------|------|
| `delivery/webapp/src/components/audio/AudioPlayerBar.tsx` | The Player bar (fixed bottom-0). Will gain the Lyrics toggle button + the lyrics panel. Currently: 236 lines. |
| `delivery/webapp/src/components/audio/GlobalAudioPlayer.tsx` | Wraps `AudioPlayerProvider` + renders `<AudioPlayerBar />` (except on `/play/controller`). No changes needed. |
| `delivery/webapp/src/contexts/AudioPlayerContext.tsx` | Defines `AudioTrack` interface (`id`, `title`, `artist`, `src`, `type`, `duration?`, `loopStart?`, `loopEnd?`). Will gain `recordingContentHash?: string`. |
| `delivery/webapp/src/hooks/useAudioPlayer.ts` | Exposes `playSong`, `playTransition`, `playLyricsLoop`. `PlaySongOptions` and `PlayLyricsLoopOptions` will gain an optional `recordingContentHash` field; it is forwarded to `AudioTrack`. |
| `delivery/webapp/src/hooks/useSongLyrics.ts` | v3 hook: fetches `GET /api/lyrics/{contentHash}`, caches in module-scoped `Map`. **No changes.** |
| `delivery/webapp/src/app/api/lyrics/[recordingContentHash]/route.ts` | v3 API endpoint: resolves user override → R2 → `lyrics_lines` → `lyrics_raw`. **No changes.** |
| `delivery/webapp/src/lib/render/lrc-parser.ts` | `parseLRC()`, `isValidLRC()`, `LRCLine` interface. Import site for the new lyrics panel. |
| `delivery/webapp/src/components/songset/SongList.tsx` | Song row rendering. v3 added: `LyricsPanel` component, `expandedItemId` accordion state, `handleDragStart` auto-collapse, chevron button in `SortableSongItem`, `handleToggleExpand`. v4 removes all of these. The `formatTimestamp()` local function at line 91 is moved to the new `PlayerLyricsPanel` component (or a shared util). |
| `delivery/webapp/src/components/songset/SongsetEditor.tsx` | Renders `<SongList>` and has `pb-24` bottom padding (line 396) to clear the fixed player bar. No direct changes, but if the lyrics panel is very tall on mobile, the `pb-24` may be insufficient — acceptable since the panel is overlay/fixed. |
| `delivery/webapp/src/components/songset/BrowseSheet.tsx` | Calls `play()` at lines 270 + 300. Has `recording.contentHash` available. Needs to pass it. |
| `delivery/webapp/src/components/search/SemanticSearch.tsx` | Calls `play()` at lines 213 + 243. Has `recording.contentHash` available. Needs to pass it. |
| `delivery/webapp/src/components/transition/TransitionSheet.tsx` | Calls `play()` at line 63 with `type: "transition"`. No `recordingContentHash` passed — transitions don't show lyrics. **No changes.** |

### Songs Already Carry recording.contentHash

All song-starting call sites (`SongList`, `BrowseSheet`, `SemanticSearch`) already have `item.recording.contentHash` or `recording.contentHash` available in scope. They just don't currently pass it to `play()`. The `useAudioPlayer` hook's `playSong` wrapper also accepts `songId` and looks up the song — it needs to add the field to `PlaySongOptions`.

---

## Implementation Plan

### Step 1: Add `recordingContentHash` to `AudioTrack`

**Modify**: `delivery/webapp/src/contexts/AudioPlayerContext.tsx`

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
  recordingContentHash?: string;  // NEW — used by AudioPlayerBar to fetch lyrics
}
```

- **Optional**: Transitions and any future track types without lyrics simply omit it. The Lyrics button checks `currentTrack.recordingContentHash` before rendering.
- **No context logic changes**: The `AudioPlayerProvider` doesn't need to know about this field; it's carried on the track object and consumed by the bar.

### Step 2: Propagate `recordingContentHash` from Call Sites

**Modify**: `delivery/webapp/src/hooks/useAudioPlayer.ts`

Add `recordingContentHash?: string` to three option interfaces and forward to `AudioTrack`:

1. `PlaySongOptions`: add `recordingContentHash?: string`. In `playSong`, set `recordingContentHash: options.recordingContentHash` on the track.
2. `PlayLyricsLoopOptions`: add `recordingContentHash?: string`. In `playLyricsLoop`, set `recordingContentHash: options.recordingContentHash` on the track.
3. `PlayTransitionOptions`: **no change** — transitions don't show lyrics.

**Modify**: `delivery/webapp/src/components/songset/SongList.tsx`

In `handlePlaySong` (lines 415 + 443), add `recordingContentHash: recording.contentHash` to both `play()` calls. The `recording` variable is already in scope at both call sites.

**Modify**: `delivery/webapp/src/components/songset/BrowseSheet.tsx`

In `handlePlaySong` (lines 270 + 300), add `recordingContentHash: recording.contentHash` to both `play()` calls. The `recording` variable is already in scope.

**Modify**: `delivery/webapp/src/components/search/SemanticSearch.tsx`

In `handlePlaySong` (lines 213 + 243), add `recordingContentHash: recording.contentHash` to both `play()` calls. The `recording` variable is already in scope.

**No changes to**: `TransitionSheet.tsx` (transition type, no lyrics lookup).

### Step 3: Extract `formatTimestamp` to a Shared Util

**New file**: `delivery/webapp/src/lib/render/lyrics-display.ts`

Move the `formatTimestamp()` function from `SongList.tsx:91-96` to this shared module. Both `PlayerLyricsPanel` and (if retained elsewhere) `SongList` can import it.

```typescript
export function formatTimestamp(timeSeconds: number): string {
  const minutes = Math.floor(timeSeconds / 60);
  const seconds = Math.floor(timeSeconds % 60);
  const hundredths = Math.floor((timeSeconds % 1) * 100);
  return `[${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}.${hundredths.toString().padStart(2, "0")}]`;
}
```

### Step 4: New `PlayerLyricsPanel` Component

**New file**: `delivery/webapp/src/components/audio/PlayerLyricsPanel.tsx`

A self-contained component that renders the lyrics for the currently playing track:

```typescript
"use client";

import { useId } from "react";
import { Loader2 } from "lucide-react";
import { useSongLyrics } from "@/hooks/useSongLyrics";
import { parseLRC, isValidLRC, type LRCLine } from "@/lib/render/lrc-parser";
import { formatTimestamp } from "@/lib/render/lyrics-display";
```

Behavior:
- Takes a single prop: `recordingContentHash: string` (guaranteed non-undefined because the parent only renders this panel when `currentTrack.recordingContentHash` exists).
- Calls `useSongLyrics(recordingContentHash)`.
- Renders the same branching logic as v3's `LyricsPanel`:
  - `loading` → `<Loader2 className="animate-spin" />` + "Loading lyrics…"
  - `error` → muted "Lyrics unavailable"
  - `lrcContent !== null && isValidLRC(lrcContent)` → parse with `parseLRC()`, render each `LRCLine`:
    - Desktop (`md:`+): side-by-side flex row — `<span className="font-mono text-xs text-muted-foreground block md:w-16 md:shrink-0">{formatTimestamp(line.timeSeconds)}</span>` + `<span className="text-sm break-words block">{line.text}</span>`
    - Mobile (`< md`): stacked vertically — same elements with `flex-col md:flex-row md:items-baseline md:gap-2`
  - `lines !== null && lines.length > 0` → `<pre className="text-sm whitespace-pre-wrap break-words">{lines.join('\n')}</pre>`
  - `lrcContent !== null` (plain text that failed `isValidLRC()`) → `<pre className="text-sm whitespace-pre-wrap break-words">{lrcContent}</pre>`
  - Both null → `<p className="text-sm text-muted-foreground">No lyrics available for this recording.</p>`
- Container: `max-h-[40vh] md:max-h-[400px] overflow-y-auto px-3 lg:px-4 py-2`
- `role="region"` + `aria-label="Lyrics for {track title}"`
- **No `scrollIntoView`**: The v3 `LyricsPanel` called `scrollIntoView` on mount. This is not needed here — the panel is already visible when toggled (it grows upward from the bottom of the viewport).

**Key difference from v3's LyricsPanel**: No `item.recording === null` check. This component is only rendered when the player has a track with `recordingContentHash`, so the null-recording case is handled by the parent (the Lyrics button simply isn't shown).

### Step 5: Lyrics Toggle Button + Expandable Panel in AudioPlayerBar

**Modify**: `delivery/webapp/src/components/audio/AudioPlayerBar.tsx`

#### 5a. New State

```typescript
const [showLyrics, setShowLyrics] = useState(false);
```

#### 5b. Auto-collapse on track change / stop

```typescript
useEffect(() => {
  if (!currentTrack?.recordingContentHash) {
    setShowLyrics(false);
  }
}, [currentTrack?.recordingContentHash]);
```

When the track changes or is cleared, and the new track has no `recordingContentHash` (e.g., a transition or no track), the panel collapses.

**Note**: We intentionally do *not* collapse when switching between two songs. If `showLyrics` is `true` and the user starts a new song, the panel stays open and lyrics swap instantly (via cache) or show a brief loading spinner.

#### 5c. Lyrics Toggle Button

Add a `Mic2` icon button (from `lucide-react`) in the player bar's control row. Recommended placement: between the track info area and the main playback controls (or adjacent to the loop toggle). The button:

- **Visible when**: `currentTrack.recordingContentHash` is defined (song or lyrics-loop type). Hidden entirely for transition tracks.
- **Active state**: `variant={showLyrics ? "secondary" : "ghost"}` — visually indicates when lyrics panel is open.
- **`aria-expanded={showLyrics}`**, `aria-controls="player-lyrics-panel"`, `aria-label={showLyrics ? "Hide lyrics" : "Show lyrics"}`
- **`data-testid="lyrics-toggle-button"`**

Import: `Mic2` from `lucide-react` (add to the existing `lucide-react` import destructure on line 6).

#### 5d. Render the Lyrics Panel

Within the player bar's `fixed bottom-0` container, **before** the seek bar `<div>` (so it appears above the existing content):

```jsx
{showLyrics && currentTrack.recordingContentHash && (
  <div
    id="player-lyrics-panel"
    role="region"
    aria-label={`Lyrics for ${currentTrack.title}`}
    className="max-h-[40vh] md:max-h-[400px] overflow-y-auto border-t bg-background/95 backdrop-blur-sm"
  >
    <PlayerLyricsPanel recordingContentHash={currentTrack.recordingContentHash} />
  </div>
)}
```

Because the container is `fixed bottom-0`, adding content **above** the existing rows naturally pushes the player's total height upward. The seek bar and controls remain at the bottom; the lyrics panel grows from the top of the player.

#### 5e. Animated Expand/Collapse

For smooth height animation, wrap the lyrics panel in an animated container:

- Use a CSS `transition-all duration-300` on the panel wrapper.
- When `showLyrics` is `false`, render the panel with `max-h-0 overflow-hidden opacity-0` (collapsed, invisible).
- When `showLyrics` is `true`, render with `max-h-[40vh] md:max-h-[400px] opacity-100` (expanded, visible).
- **Always render the panel DOM** (even when collapsed) so the transition animates. Guard the `PlayerLyricsPanel` (which invokes the hook) with `showLyrics && currentTrack.recordingContentHash` to avoid unnecessary fetches.

```jsx
{/* Animated lyrics panel — always in DOM for transition */}
<div
  className={cn(
    "overflow-hidden transition-all duration-300 ease-in-out border-t bg-background/95 backdrop-blur-sm",
    showLyrics && currentTrack.recordingContentHash
      ? "max-h-[40vh] md:max-h-[400px] opacity-100"
      : "max-h-0 opacity-0 border-t-0"
  )}
>
  {showLyrics && currentTrack.recordingContentHash && (
    <div
      id="player-lyrics-panel"
      role="region"
      aria-label={`Lyrics for ${currentTrack.title}`}
      className="overflow-y-auto h-full"
    >
      <PlayerLyricsPanel recordingContentHash={currentTrack.recordingContentHash} />
    </div>
  )}
</div>
```

#### 5f. Import the New Component

Add to the top of `AudioPlayerBar.tsx`:

```typescript
import { PlayerLyricsPanel } from "./PlayerLyricsPanel";
```

#### 5g. Destructure `useState`, `useEffect` 

The current `AudioPlayerBar` destructures only `useAudioPlayer`. Add React hooks:

```typescript
import { useState, useEffect } from "react";
```

(Add this at the top of the file, above the existing imports.)

### Step 6: Remove v3 Inline Lyrics from SongList

**Modify**: `delivery/webapp/src/components/songset/SongList.tsx`

Remove the following v3 additions:

1. **`LyricsPanel` component** (lines 98-171): Delete entirely. Its rendering logic is now in `PlayerLyricsPanel`.
2. **`formatTimestamp` function** (lines 91-96): Delete. It's been moved to `delivery/webapp/src/lib/render/lyrics-display.ts`.
3. **`expandedItemId` state** (line 381): Delete.
4. **`handleDragStart` callback** (lines 492-494): Delete. Was only used for auto-collapse of the accordion. No longer needed.
5. **`handleToggleExpand` callback** (lines 496-498): Delete.
6. **`DndContext`'s `onDragStart` prop** (line 536): Remove `onDragStart={handleDragStart}`.
7. **`SortableSongItemProps`**: Remove `isExpanded: boolean` and `onToggleExpand: () => void`.
8. **`SortableSongItem` chevron button** (lines 298-314): Delete the entire `<Button>` block with `ChevronDown`.
9. **`expandedItemId === item.id` / `onToggleExpand` props** on `<SortableSongItem>` (lines 551-552): Delete both props.
10. **`{isExpanded && <LyricsPanel item={item} />}`** (lines 360-362): Delete.
11. **Imports**: Remove `useSongLyrics` (line 29), `parseLRC`, `isValidLRC`, `LRCLine` (line 30), `ChevronDown` (line 24 — check if still used elsewhere; remove from the destructure if not).
12. **`useId`**: Check if still needed (was used for `lyricsPanelId` in `LyricsPanel`). Retain if used by `dndContextId`.

### Step 7: Update `SongsetEditor.tsx` Bottom Padding

No code changes required. The existing `pb-24` (96px) on `<main>` (line 396) is enough to clear the collapsed player bar. When the lyrics panel is expanded:
- On desktop: The player grows upward but is `fixed`. Content behind it is simply covered. The user can collapse lyrics to interact with the song list.
- On mobile: The Add Songs FAB (`fixed bottom-20 right-4`, line 418) may be partially overlapped by the expanded lyrics panel. This is acceptable — the user is reading lyrics, not adding songs. When they collapse lyrics, the FAB is fully visible again. The player's `z-[60]` ensures it renders above the FAB.
- No adjustment to `pb-24` is made because:
- The collapsed player height (~80-100px) is already cleared by `pb-24` (96px).
- The expanded lyrics panel is an overlay; users collapse it when they want to interact with the list.

---

## Files to Create

| File | Purpose |
|------|---------|
| `delivery/webapp/src/components/audio/PlayerLyricsPanel.tsx` | Lyrics rendering component for the AudioPlayerBar. Uses `useSongLyrics` + LRC parser. |
| `delivery/webapp/src/lib/render/lyrics-display.ts` | Shared `formatTimestamp()` utility (extracted from SongList). |

## Files to Modify

| File | Changes |
|------|---------|
| `delivery/webapp/src/contexts/AudioPlayerContext.tsx` | Add `recordingContentHash?: string` to `AudioTrack` interface. |
| `delivery/webapp/src/hooks/useAudioPlayer.ts` | Add `recordingContentHash?: string` to `PlaySongOptions` + `PlayLyricsLoopOptions`; forward to `AudioTrack`. |
| `delivery/webapp/src/components/audio/AudioPlayerBar.tsx` | Add `showLyrics` state + auto-collapse effect + Lyrics toggle button + animated lyrics panel wrapper + import `PlayerLyricsPanel`. |
| `delivery/webapp/src/components/songset/SongList.tsx` | Remove all v3 lyrics code: `LyricsPanel` component, `formatTimestamp`, `expandedItemId`, `handleDragStart`, `handleToggleExpand`, chevron button, `SortableSongItemProps` lyric fields, lyrics imports. |
| `delivery/webapp/src/components/songset/BrowseSheet.tsx` | Pass `recordingContentHash: recording.contentHash` to both `play()` calls. |
| `delivery/webapp/src/components/search/SemanticSearch.tsx` | Pass `recordingContentHash: recording.contentHash` to both `play()` calls. |

## Files to Delete

| File | Reason |
|------|--------|
| `delivery/webapp/src/test/components/songset/SongList-lyrics.test.tsx` | Tests the v3 inline chevron/TyricsPanel behavior. Entirely superseded by new AudioPlayerBar lyrics tests. |

## Files NOT Modified (Reused as-is from v3)

| File | Reason |
|------|--------|
| `delivery/webapp/src/hooks/useSongLyrics.ts` | Hook + module cache are reused unchanged. |
| `delivery/webapp/src/app/api/lyrics/[recordingContentHash]/route.ts` | API endpoint reused unchanged. |
| `delivery/webapp/src/test/api/lyrics/recordingContentHash.test.ts` | API tests still valid. |
| `delivery/webapp/src/components/transition/TransitionSheet.tsx` | No `recordingContentHash` needed for transitions. |
| `delivery/webapp/src/components/audio/GlobalAudioPlayer.tsx` | Rendering wrapper unchanged. |

---

## Imports Summary

### AudioPlayerBar.tsx (new imports)
```typescript
import { useState, useEffect } from "react";
import { Mic2 } from "lucide-react";  // add to existing destructure
import { PlayerLyricsPanel } from "./PlayerLyricsPanel";
```

### PlayerLyricsPanel.tsx (all imports)
```typescript
import { useSongLyrics } from "@/hooks/useSongLyrics";
import { parseLRC, isValidLRC, type LRCLine } from "@/lib/render/lrc-parser";
import { formatTimestamp } from "@/lib/render/lyrics-display";
import { Loader2 } from "lucide-react";
```

### SongList.tsx (imports removed)
```typescript
// REMOVE these lines:
import { useSongLyrics } from "@/hooks/useSongLyrics";
import { parseLRC, isValidLRC, type LRCLine } from "@/lib/render/lrc-parser";
// Remove ChevronDown from the lucide-react destructure (if unused elsewhere)
```

---

## Edge Cases

1. **Transition track plays while lyrics panel is open**: The panel auto-collapses via the `useEffect` (transition has no `recordingContentHash`). When the user switches back to a song, they must re-click the Lyrics button (panel does not auto-reopen).
2. **User stops playback (X button)**: `currentTrack` becomes `null`. `showLyrics` resets to `false` via effect. The entire player (including lyrics) disappears.
3. **Lyrics button clicked on a song with no lyrics**: Button is always clickable. The panel expands, shows a brief loading state (or instant if previously cached), then displays "No lyrics available for this recording."
4. **Rapid track switching while panel is open**: Each track change fires `useSongLyrics` with a new `recordingContentHash`. The hook's `AbortController` aborts the previous fetch. Cached songs show instantly. The panel remains open throughout.
5. **Lyrics loaded, then track changes to a cached song**: The panel stays open. `useSongLyrics` returns the cached result synchronously. No spinner; lyrics swap instantly.
6. **Lyrics loaded, then track changes to an uncached song**: The panel stays open. A loading spinner appears inside the panel until the new lyrics are fetched.
7. **Mobile: lyrics panel is very tall**: `max-h-[40vh]` caps the height. The panel scrolls internally. The seek bar and controls remain accessible below.
8. **Lyrics panel overlaps the Add Songs FAB on mobile**: Acceptable. The FAB is behind the player (`z-[60]` > FAB's z-index). User collapses lyrics to access the FAB.
9. **User navigates to `/play/controller`**: `GlobalAudioPlayer` already hides `AudioPlayerBar` on controller pages. No lyrics shown. No issue.
10. **User navigates away from the songset page**: The player is global and persists across navigations (within the app). If a song is playing and lyrics are open, both the player and lyrics remain visible on other pages.
11. **`recordingContentHash` is present but the API returns an error**: Panel shows "Lyrics unavailable" text. The user can collapse and try again (re-expand would re-trigger fetch unless cached as failed — but v3's cache only stores successful results, errors are not cached).

---

## Testing

### Unit Tests

- **`delivery/webapp/src/test/hooks/useAudioPlayer.test.tsx`**: Update existing tests to include `recordingContentHash` in `playSong` and `playLyricsLoop` calls. Verify it's forwarded to `currentTrack`.

### Component Tests

- **`delivery/webapp/src/test/components/audio/AudioPlayerBar.test.tsx`** (extend existing):
  - (a) Lyrics button is hidden when no track is loaded.
  - (b) Lyrics button is hidden for `type: "transition"` tracks.
  - (c) Lyrics button is visible for `type: "song"` tracks with `recordingContentHash`.
  - (d) Lyrics button is visible for `type: "lyrics-loop"` tracks with `recordingContentHash`.
  - (e) Click Lyrics button → `aria-expanded="true"` + panel appears (mock `useSongLyrics` to return `lines: ["Test line"]` → verify `<pre>` text).
  - (f) Click Lyrics button again → `aria-expanded="false"` + panel disappears.
  - (g) Panel auto-collapses when track changes to one without `recordingContentHash` (transition).
  - (h) Panel stays open when switching between two songs with `recordingContentHash`.

- **`delivery/webapp/src/test/components/audio/PlayerLyricsPanel.test.tsx`** (new):
  - (a) `loading: true` → renders spinner + "Loading lyrics…"
  - (b) `error` → renders "Lyrics unavailable"
  - (c) `lrcContent` with valid LRC → renders timestamped lines via `parseLRC()`
  - (d) `lines` non-empty → renders `<pre>` block with joined lines
  - (e) `lrcContent` plain text (fails `isValidLRC()`) → renders `<pre>` block
  - (f) Both null → renders "No lyrics available for this recording."

### Accessibility Tests

- **`delivery/webapp/src/test/accessibility/accessibility.test.tsx`** (update):
  - Remove the 3 tests referencing "expand lyrics for amazing grace" chevron in SongList (lines 401-437): these test v3's inline chevron which is being deleted.
  - Add new tests under an `AudioPlayerBar` describe block:
    - Lyrics toggle button has `aria-expanded` attribute.
    - Lyrics toggle button toggles `aria-expanded` on click.
    - Expanded lyrics panel has `role="region"`.

### Deleted Tests

- **`delivery/webapp/src/test/components/songset/SongList-lyrics.test.tsx`**: Delete entirely. All v3 inline lyrics behavior is removed. The accordion expansion, chevron toggle, and `LyricsPanel` rendering tests are obsolete.

### Manual Testing

1. Play a song with an LRC file in R2 → click Lyrics → see timed lyrics with timestamps.
2. Play a song with only `lyrics_lines` → click Lyrics → see plain-text formatted by line.
3. Play a song with only `lyrics_raw` → click Lyrics → see plain text with preserved formatting.
4. Play a song with a user LRC override → click Lyrics → see override content.
5. Play a song with neither → click Lyrics → see "No lyrics available for this recording."
6. Play a transition preview → verify Lyrics button is **not visible**.
7. Open lyrics panel, then play a different song → verify panel stays open and lyrics swap (instant if cached, spinner if not).
8. Open lyrics panel, then stop playback (X button) → verify panel + player disappear together.
9. Open lyrics panel, then play a transition → verify panel auto-collapses (transition has no lyrics).
10. Verify on mobile (`< 768px`): timestamps stack above lyric text for LRC content; panel has `max-h-[40vh]` with internal scroll.
11. Verify smooth expand/collapse animation (CSS `transition-all duration-300`).
12. Navigate between songs via Browse Sheet or Semantic Search → if the new song has `recordingContentHash`, lyrics button appears and panel (if open) swaps.
13. Navigate away from the songset page to another page while music plays → verify player + lyrics panel persist globally.
14. Verify `pb-24` bottom padding on songset editor still clears the collapsed player bar when no lyrics are open.

---

## Out of Scope

- **Synchronized auto-scroll**: Highlighting the current lyric line based on `currentTime` and auto-scrolling to it. The existing `findCurrentLyricIndex()` in `lrc-parser.ts:138` operates on `GlobalLRCLine[]` (global timeline, not per-song `LRCLine[]`); a per-song variant would be needed. Future enhancement.
- **Click-to-seek on LRC timestamps**: Clicking a timestamp in the lyrics panel to seek to that time. Future enhancement.
- **Editing lyrics from this panel** (use the existing LRC editor / admin CLI).
- **Cross-session cache persistence** (module-scope Map is cleared on page reload; v3 behavior retained).
- **Server-side caching of the API response** (could be a future enhancement with `Cache-Control` headers).
- **Lyrics for transition previews** (transitions span two recordings; no meaningful single-song lyrics).

---

## Migration from v3

The v3 implementation is already live in the codebase. This v4 spec replaces it. The migration consists of:

1. **Backend**: No changes. The API endpoint and hook are reused.
2. **Data model**: `AudioTrack` gains an optional field. Non-breaking.
3. **Call sites**: 3 files updated to pass `recordingContentHash` (additive, non-breaking).
4. **SongList cleanup**: Remove v3's inline lyrics code. This **is** breaking for the referenced tests, which must be updated/deleted in the same change.
5. **Player enrichment**: Add the Lyrics button + panel. This is purely additive — if `showLyrics` is never set to `true`, the player behaves identically to before.

No database migration. No API contract change. No breaking change to any externally-visible API.
---
