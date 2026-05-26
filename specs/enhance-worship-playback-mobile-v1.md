# Enhance Worship Playback Mobile Experience (v1)

> **Status:** Planning
> **Date:** 2026-05-26

---

## Summary

Three mobile UX improvements to the Worship screen (`/songsets/[id]/play/controller`):

| # | Change | Rationale |
|---|--------|-----------|
| 1 | Remove tap-to-pause/play on video area; tapping only shows Playback Controls | Accidental pausing during worship is disruptive. Pause/play is only via Playback Controls. |
| 2 | Remove Skip ±10s buttons and scrub bar from Playback Controls; simplify to Prev \| Play/Pause \| Next | Controls too wide on mobile. Song navigation + pause/play is sufficient. Fine seeking via Lyrics sheet. |
| 3 | Remove swipe gestures from Lyrics sheet; click-only toggle with ghost-click protection | Swipe-down triggers page reload on Android Vivaldi; ghost clicks cause unintended playback jumps. |

---

## Change 1: Remove Tap-to-Pause on Video Area

### Problem

Tapping the video element toggles play/pause (`ControllerPlayer.tsx:400-403`). During worship, accidental taps pause playback — disruptive and frustrating.

### Current Code

**File:** `webapp/src/components/play/ControllerPlayer.tsx:394-407`

```tsx
<video
  ref={videoRef}
  src={videoSrc}
  className="w-full h-full object-contain"
  playsInline
  muted={isPresentationActive}
  onClick={(e) => {
    e.stopPropagation();
    handlePlayPause();       // <-- THIS toggles play/pause on tap
  }}
  onDoubleClick={(e) => {
    e.preventDefault();
  }}
/>
```

### Proposed Fix

Replace `handlePlayPause()` with `handleInteraction()` (shows controls + starts auto-hide timer). Keep `e.stopPropagation()` so the click doesn't double-trigger the container's `handleInteraction`.

**File:** `webapp/src/components/play/ControllerPlayer.tsx:394-407`

```tsx
<video
  ref={videoRef}
  src={videoSrc}
  className="w-full h-full object-contain"
  playsInline
  muted={isPresentationActive}
  onClick={(e) => {
    e.stopPropagation();
    handleInteraction();
  }}
  onDoubleClick={(e) => {
    e.preventDefault();
  }}
/>
```

### Side Effects

- **iOS info toast** (`ControllerPlayer.tsx:463-465`): Currently says "Tap the screen to show controls." — still accurate after this change. No update needed.
- **Desktop behavior**: Clicking the video on desktop now shows controls instead of toggling play/pause. This is consistent with the mobile behavior. Desktop users can still use Space bar or the Playback Controls button for play/pause.

---

## Change 2: Simplify Playback Controls

### Problem

Playback Controls are too wide on mobile. The Skip ±10s buttons and scrub bar add width without essential value — song navigation and play/pause are sufficient for worship. Fine-grained seeking can be done via the Lyrics sheet.

### Current Layout

```
[Prev Song] [1/3] [Next Song]  |  [Skip-10] [Play/Pause] [Skip+10]  |  [Mute] [Volume] [Connected]
──────────────────────────────── scrub bar ──────────────────────────────────────────────────────────
```

Total mobile width: ~288px (after v3 responsive fixes). Still crowded on 375px screens.

### Proposed Layout

**Mobile (default):**
```
[Prev Song] [1/3] [Next Song]  |  [Play/Pause]  |  [Connected?]
```

**Desktop (md+):**
```
[Prev Song] [1/3] [Next Song]  |  [Play/Pause]  |  [Mute] [Volume] [Connected?]
```

No scrub bar on any screen size. No Skip ±10s buttons on any screen size.

### Changes to PlaybackControls.tsx

#### 2a: Remove scrub bar and time display

Remove the entire scrub bar section (lines 77-109) including the `handleScrubClick` function (lines 63-68), `progress` calculation (line 61), and `formatTime` function (lines 54-59).

Remove `currentTime`, `duration`, `onSeek` from props interface.

#### 2b: Remove Skip ±10s buttons

Remove the Skip Back 10s and Skip Forward 10s buttons (lines 142-184). Remove `onSkipBack` and `onSkipForward` from props interface.

#### 2c: Simplify center section to just Play/Pause

The center section becomes a single Play/Pause button:

```tsx
<div className="flex items-center justify-center">
  <Button
    variant="default"
    size="icon"
    className="size-14 sm:size-16 rounded-full bg-white text-black hover:bg-white/90"
    onClick={onPlayPause}
    aria-label={isPlaying ? "Pause" : "Play"}
  >
    {isPlaying ? (
      <Pause className="size-7 sm:size-8" />
    ) : (
      <Play className="size-7 sm:size-8 ml-1" />
    )}
  </Button>
</div>
```

#### 2d: Hide volume/mute on mobile, show on md+

Change the mute button from `hidden sm:flex` to `hidden md:flex` and the volume slider from `hidden sm:block` to `hidden md:block`. This actually hides them on mobile phones (the `sm=0px` breakpoint means `sm:` always applies, so the current code never hides them).

#### 2e: Updated props interface

```tsx
export interface PlaybackControlsProps {
  isPlaying: boolean;
  currentSongIndex: number;
  totalSongs: number;
  isPresentationActive: boolean;
  onPlayPause: () => void;
  onPrevSong: () => void;
  onNextSong: () => void;
  onVolumeChange: (volume: number) => void;
  onToggleMute: () => void;
  volume: number;
  isMuted: boolean;
  className?: string;
}
```

Removed: `currentTime`, `duration`, `onSeek`, `onSkipBack`, `onSkipForward`.

### Changes to ControllerPlayer.tsx

#### 2f: Remove skip handlers and scrub-related props

Remove `handleSkipBack` (lines 195-197) and `handleSkipForward` (lines 199-201).

Update `<PlaybackControls>` props (lines 517-534):

```tsx
<PlaybackControls
  isPlaying={isPlaying}
  currentSongIndex={currentSongIndex}
  totalSongs={chapters.length}
  isPresentationActive={isPresentationActive}
  onPlayPause={handlePlayPause}
  onPrevSong={handlePrevSong}
  onNextSong={handleNextSong}
  onVolumeChange={handleVolumeChange}
  onToggleMute={handleToggleMute}
  volume={volume}
  isMuted={isMuted}
/>
```

### Changes to useKeyboardShortcuts.ts

#### 2g: Remove seek shortcuts

Remove `onSeekBack` and `onSeekForward` from the `KeyboardShortcutActions` interface (lines 7-8) and the `switch` handler (lines 40-47 for ArrowLeft/ArrowRight).

Updated interface:

```tsx
export interface KeyboardShortcutActions {
  onTogglePlayback: () => void;
  onPrevSong: () => void;
  onNextSong: () => void;
}
```

Updated `ControllerPlayer.tsx` call:

```tsx
useKeyboardShortcuts({
  onTogglePlayback: handlePlayPause,
  onPrevSong: handlePrevSong,
  onNextSong: handleNextSong,
});
```

### Changes to useMediaSession.ts

#### 2h: Remove seekbackward/seekforward handlers

Remove `onSeekBack` and `onSeekForward` from `MediaSessionActions` interface (lines 17-18) and the `setActionHandler` calls (lines 66-67, 76-77).

Updated interface:

```tsx
export interface MediaSessionActions {
  onPlay?: () => void;
  onPause?: () => void;
  onPrevSong?: () => void;
  onNextSong?: () => void;
}
```

Updated `ControllerPlayer.tsx` call:

```tsx
const { updatePlaybackState, updatePositionState } = useMediaSession(
  mediaSessionMetadata,
  {
    onPlay: handlePlayPause,
    onPause: handlePlayPause,
    onPrevSong: handlePrevSong,
    onNextSong: handleNextSong,
  }
);
```

### Changes to keyboard shortcuts hint

#### 2i: Update hint text

**File:** `webapp/src/components/play/ControllerPlayer.tsx:490-497`

Current:
```tsx
<div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
  <span><kbd>Space</kbd> Play/Pause</span>
  <span><kbd>←</kbd>/<kbd>→</kbd> Seek 10s</span>
  <span><kbd>[</kbd> Prev song</span>
  <span><kbd>]</kbd> Next song</span>
</div>
```

Proposed:
```tsx
<div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
  <span><kbd>Space</kbd> Play/Pause</span>
  <span><kbd>[</kbd> Prev song</span>
  <span><kbd>]</kbd> Next song</span>
</div>
```

### Width Calculation After Fix

| Group | Mobile | Desktop (md+) |
|-------|--------|---------------|
| Song nav | 2×40 + 40 + gaps ≈ 128px | 2×48 + 48 + gaps ≈ 160px |
| Play/Pause | 56px | 64px |
| Volume | 0px (hidden) | 40 + 80 = 120px |
| Gaps | 2×4 = 8px | 2×16 = 32px |
| **Total** | **~192px** | **~376px** |

Mobile total (~192px) fits comfortably within 375px viewport with ~183px to spare.

---

## Change 3: Remove Swipe from Lyrics Sheet, Click-Only

### Problem

1. **Ghost click**: Tapping the handle bar to open the sheet causes a synthetic click event that lands on a lyrics line button, jumping playback to that line's timestamp.
2. **Swipe-down page reload**: On Android Vivaldi, swiping down on the lyrics sheet triggers a browser page reload instead of closing the sheet.
3. **Swipe complexity**: The current swipe implementation (touch drag with threshold logic, debounce, mouse fallback) is complex and fragile.

### Current Code

**File:** `webapp/src/components/play/LyricJumpList.tsx`

The handle bar (lines 128-156) has six gesture handlers:

```tsx
<div
  onTouchStart={handleTouchStart}
  onTouchMove={handleTouchMove}
  onTouchEnd={handleTouchEnd}
  onMouseDown={handleTouchStart}
  onMouseMove={handleTouchMove}
  onMouseUp={handleTouchEnd}
  onMouseLeave={handleTouchEnd}
>
```

Plus drag state: `isDragging`, `startY`, `currentY`, `lastToggleTimeRef`.

Plus inline transform style during drag (lines 117-123).

### Proposed Fix

#### 3a: Remove all swipe/drag handlers and state

Remove:
- `isDragging` state (line 28)
- `startY` state (line 29)
- `currentY` state (line 30)
- `lastToggleTimeRef` ref (line 33)
- `handleTouchStart` callback (lines 35-44)
- `handleTouchMove` callback (lines 46-62)
- `handleTouchEnd` callback (lines 64-88)
- Inline `style` prop on sheet container (lines 117-123)
- All six gesture handlers on the handle bar (lines 130-136)

#### 3b: Add click-only toggle with ghost-click protection

Add a `contentInteractive` state that prevents lyrics line clicks for 350ms after opening (matching the 300ms CSS transition + 50ms buffer):

```tsx
const [isOpen, setIsOpen] = useState(false);
const [contentInteractive, setContentInteractive] = useState(false);

const handleToggle = useCallback(() => {
  setIsOpen((prev) => {
    const next = !prev;
    if (next) {
      setContentInteractive(false);
      setTimeout(() => setContentInteractive(true), 350);
    } else {
      setContentInteractive(false);
    }
    return next;
  });
}, []);
```

Handle bar becomes:

```tsx
<div
  className="flex flex-col items-center justify-center h-12 bg-black/90 backdrop-blur-sm rounded-t-2xl cursor-pointer"
  onClick={handleToggle}
  role="button"
  tabIndex={0}
  aria-label={isOpen ? "Close lyric jump list" : "Open lyric jump list"}
  onKeyDown={(e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      handleToggle();
    }
  }}
>
  <div className="w-12 h-1 bg-white/30 rounded-full mb-1" />
  <div className="flex items-center gap-2 text-white/70 text-sm">
    <ChevronUp
      className={cn(
        "size-4 transition-transform",
        isOpen ? "rotate-180" : ""
      )}
    />
    <span>{isOpen ? "Tap to close" : "Lyrics"}</span>
  </div>
</div>
```

Content area gets `pointer-events-none` during the protection window:

```tsx
<div
  ref={contentRef}
  className={cn(
    "bg-black/90 backdrop-blur-sm max-h-[60vh] overflow-y-auto",
    !contentInteractive && "pointer-events-none"
  )}
>
```

#### 3c: Remove `onTouchStart` stopPropagation on sheet container

The sheet container currently has `onTouchStart={(e) => e.stopPropagation()}` (line 125). This was needed to prevent the swipe from triggering the ControllerPlayer's `handleInteraction`. With swipe removed, we only need `onClick={(e) => e.stopPropagation()}` to prevent clicks inside the sheet from showing/hiding the main controls.

#### 3d: Update handle bar text

Change "Swipe down to close" → "Tap to close" since swipe is removed.

#### 3e: Keep backdrop overlay

The backdrop (lines 247-261) remains unchanged. Clicking the backdrop still closes the sheet.

---

## Summary of All File Changes

| File | Changes |
|------|---------|
| `ControllerPlayer.tsx` | Video `onClick` → `handleInteraction()` instead of `handlePlayPause()`. Remove `handleSkipBack`, `handleSkipForward`. Remove `onSkipBack`, `onSkipForward`, `onSeek`, `currentTime`, `duration` from `<PlaybackControls>` props. Remove `onSeekBack`, `onSeekForward` from `useKeyboardShortcuts` and `useMediaSession` calls. Update keyboard hint text. |
| `PlaybackControls.tsx` | Remove scrub bar, time display, Skip ±10s buttons, `formatTime`, `handleScrubClick`, `progress`. Simplify center to Play/Pause only. Change volume visibility from `sm:` to `md:`. Remove `currentTime`, `duration`, `onSeek`, `onSkipBack`, `onSkipForward` from props. |
| `LyricJumpList.tsx` | Remove all swipe/drag handlers and state (`isDragging`, `startY`, `currentY`, `lastToggleTimeRef`, `handleTouchStart`, `handleTouchMove`, `handleTouchEnd`). Remove inline drag transform style. Add `handleToggle` click handler. Add `contentInteractive` state with 350ms delay for ghost-click protection. Update handle bar text. Remove `onTouchStart` stopPropagation on container. |
| `useKeyboardShortcuts.ts` | Remove `onSeekBack`, `onSeekForward` from interface and handler. |
| `useMediaSession.ts` | Remove `onSeekBack`, `onSeekForward` from interface and `setActionHandler` calls. |

---

## Test Changes

### PlaybackControls.test.tsx

- **Remove**: Tests for skip back/forward buttons, scrub bar, time display rendering
- **Remove**: `mockSkipBack`, `mockSkipForward`, `mockSeek` mocks
- **Remove**: `currentTime`, `duration`, `onSeek`, `onSkipBack`, `onSkipForward` from `defaultProps`
- **Update**: Volume button/slider tests to account for `md:` visibility (may need to mock `window.matchMedia` for breakpoint testing)
- **Keep**: Play/pause, prev/next song, presentation mode, disabled states tests

### LyricJumpList.test.tsx

- **Remove**: Any swipe-related tests (if any exist beyond the click-based tests)
- **Update**: Handle bar text assertions ("Swipe down to close" → "Tap to close")
- **Add**: Test that lyrics line clicks are ignored within 350ms of opening (ghost-click protection)
- **Add**: Test that lyrics line clicks work after 350ms delay
- **Keep**: Click-to-open, backdrop-to-close, chapter/line jump, keyboard navigation, current line highlighting tests

### ControllerPlayer.test.tsx

- **Remove**: Tests for skip back/forward buttons, scrub bar, video click-to-pause
- **Add**: Test that clicking the video shows controls (does NOT toggle play/pause)
- **Update**: Keyboard shortcut tests (remove ArrowLeft/ArrowRight)
- **Remove**: Volume tests that depend on `sm:` breakpoint (update to `md:`)

---

## Verification

### Change 1: No Tap-to-Pause

1. Open Worship screen on mobile browser
2. Start playback
3. Tap the video area
4. Confirm: Controls appear, playback does NOT pause
5. Tap the Play/Pause button in Playback Controls
6. Confirm: Playback pauses
7. Tap again
8. Confirm: Playback resumes

### Change 2: Simplified Playback Controls

1. Open Worship screen on mobile browser (375px viewport)
2. Confirm: Only Prev Song, Play/Pause, Next Song buttons visible
3. Confirm: No Skip ±10s buttons
4. Confirm: No scrub/seek bar
5. Confirm: No time display
6. Confirm: No volume/mute controls on mobile
7. Open on desktop (1024px+ viewport)
8. Confirm: Volume/mute controls visible
9. Confirm: No scrub bar, no Skip ±10s buttons
10. Test keyboard: Space = play/pause, `[` = prev song, `]` = next song
11. Confirm: Arrow keys no longer seek

### Change 3: Click-Only Lyrics Sheet

1. Open Worship screen on mobile browser
2. Tap the "Lyrics" handle bar
3. Confirm: Sheet opens, playback does NOT jump to any lyrics line
4. Wait 0.5s, then tap a lyrics line
5. Confirm: Playback jumps to that line's timestamp
6. Tap the "Tap to close" handle bar
7. Confirm: Sheet closes
8. Open the sheet, then tap the backdrop
9. Confirm: Sheet closes
10. On Android Vivaldi: open the sheet, then try swiping down
11. Confirm: No page reload occurs (swipe has no effect)
12. On desktop: click the handle bar
13. Confirm: Sheet toggles open/closed

---

## Out of Scope

- Adding a progress indicator (e.g., thin progress bar without seek functionality) — could be a future enhancement
- Auto-scrolling the lyrics sheet to the current line when opened
- Restructuring the lyrics sheet as a modal dialog instead of a bottom sheet
- Adding haptic feedback on button presses
