# Enhance Worship Playback Mobile Experience (v3)

> **Status:** Ready for Implementation
> **Date:** 2026-05-26
> **Supersedes:** `enhance-worship-playback-mobile-v2.md`

---

## Summary

Three mobile UX improvements to the Worship screen (`/songsets/[id]/play/controller`):

| # | Change | Rationale |
|---|--------|-----------|
| 1 | Remove tap-to-pause/play on video area; tapping only shows Playback Controls | Accidental pausing during worship is disruptive. Pause/play is only via Playback Controls. Consistent behavior across all devices. |
| 2 | Remove Skip ±10s buttons everywhere; mobile gets thin read-only progress bar + time display; desktop (md+) keeps interactive scrub bar + time display | Skip ±10s redundant with scrub bar on desktop. Mobile simplified to song navigation + pause/play. Thin progress bar provides position feedback. Time display kept for user awareness. |
| 3 | Platform-conditional swipe on Lyrics sheet: iOS = swipe+click, Android = click-only; ghost-click protection on both platforms; onTouchStart stopPropagation on container | Chrome for Android has mandatory pull-to-refresh that conflicts with swipe-down. iOS supports swipe-to-dismiss natively. Ghost-click protection prevents unintended playback jumps. stopPropagation prevents lyrics scrolling from triggering parent controls. |

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

## Change 2: Simplify Playback Controls with Responsive Progress Bar

### Problem

Playback Controls are too wide on mobile. The Skip ±10s buttons and scrub bar add width without essential value on mobile. However, desktop users benefit from the scrub bar for fine seeking.

### Solution

1. Remove Skip ±10s buttons on all screen sizes (redundant with scrub bar on desktop)
2. Mobile: thin (2-3px) read-only progress bar + time display
3. Desktop (md+): interactive scrub bar + time display (current behavior)
4. Keep `currentTime` and `duration` props for progress calculation
5. Keep `onSeek` prop for desktop scrub bar
6. Keep `formatTime` for time display

### Current Layout

```
[Prev Song] [1/3] [Next Song]  |  [Skip-10] [Play/Pause] [Skip+10]  |  [Mute] [Volume] [Connected]
─────────────────────────────── scrub bar ────────────────────────────────────────────────────────
```

Total mobile width: ~288px (after v3 responsive fixes). Still crowded on 375px screens.

### Proposed Layout

**Mobile (default):**
```
[Prev Song] [1/3] [Next Song]  |  [Play/Pause]  |  [Connected?]
────────────────── thin progress bar (2-3px, read-only) ──────────────────
1:23 / 4:56
```

**Desktop (md+):**
```
[Prev Song] [1/3] [Next Song]  |  [Play/Pause]  |  [Mute] [Volume] [Connected?]
────────────────── interactive scrub bar (md+ only) ──────────────────────
1:23 / 4:56
```

No Skip ±10s buttons on any screen size. Time display kept on all screen sizes.

### Changes to PlaybackControls.tsx

#### 2a: Responsive progress bar

Replace the single scrub bar with responsive variants:

```tsx
{/* Mobile: thin read-only progress bar */}
<div className="md:hidden h-0.5 bg-white/20 rounded-full overflow-hidden">
  <div
    className="h-full bg-primary transition-all duration-100"
    style={{ width: `${progress}%` }}
  />
</div>

{/* Desktop: interactive scrub bar */}
<div className="hidden md:block relative h-2 bg-white/20 rounded-full overflow-hidden cursor-pointer group">
  {/* existing scrub bar implementation */}
  <div
    className="absolute h-full bg-primary rounded-full"
    style={{ width: `${progress}%` }}
  />
  <div
    className="absolute top-1/2 -translate-y-1/2 w-4 h-4 bg-white rounded-full shadow-lg opacity-0 group-hover:opacity-100 transition-opacity"
    style={{ left: `calc(${progress}% - 8px)` }}
  />
</div>

{/* Time display - all screen sizes */}
<div className="flex justify-between text-xs text-white/60 mt-1">
  <span>{formatTime(currentTime)}</span>
  <span>{formatTime(duration)}</span>
</div>
```

Where `progress` is calculated as before:

```tsx
const progress = duration > 0 ? (currentTime / duration) * 100 : 0;
```

**Key differences for mobile progress bar:**
- No `onClick` handler (no seek)
- No thumb/drag handle
- No `role="slider"` or ARIA attributes
- No keyboard navigation
- Height is `h-0.5` (2px) instead of `h-2` (8px)

**Desktop scrub bar:**
- Keep existing `handleScrubClick` implementation
- Keep keyboard navigation (ArrowLeft/ArrowRight for 10s seek)
- Keep thumb/hover effects

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

Change the mute button from `hidden sm:flex` to `hidden md:flex` and the volume slider from `hidden sm:block` to `hidden md:block`.

**Note:** The project uses a custom Tailwind config (`globals.css:9`) where `--breakpoint-sm: 0px`. This means `sm:` variants always apply. The current `hidden sm:flex` never actually hides on mobile. Changing to `md:` (768px) correctly hides volume controls on phones.

#### 2e: Updated props interface

```tsx
export interface PlaybackControlsProps {
  isPlaying: boolean;
  currentTime: number;      // KEPT - needed for progress bar and time display
  duration: number;         // KEPT - needed for progress bar and time display
  currentSongIndex: number;
  totalSongs: number;
  isPresentationActive: boolean;
  onPlayPause: () => void;
  onSeek: (time: number) => void;  // KEPT - needed for desktop scrub bar
  onPrevSong: () => void;
  onNextSong: () => void;
  onVolumeChange: (volume: number) => void;
  onToggleMute: () => void;
  volume: number;
  isMuted: boolean;
  className?: string;
}
```

**Removed:** `onSkipBack`, `onSkipForward`.
**Kept:** `currentTime`, `duration`, `onSeek`, `formatTime`.

### Changes to ControllerPlayer.tsx

#### 2f: Remove skip handlers

Remove `handleSkipBack` (lines 195-197) and `handleSkipForward` (lines 199-201).

Keep `handleSeek` (needed for desktop scrub bar).

Update `<PlaybackControls>` props (lines 517-534):

```tsx
<PlaybackControls
  isPlaying={isPlaying}
  currentTime={currentTime}
  duration={duration}
  volume={volume}
  isMuted={isMuted}
  currentSongIndex={currentSongIndex}
  totalSongs={chapters.length}
  isPresentationActive={isPresentationActive}
  onPlayPause={handlePlayPause}
  onSeek={handleSeek}
  onPrevSong={handlePrevSong}
  onNextSong={handleNextSong}
  onVolumeChange={handleVolumeChange}
  onToggleMute={handleToggleMute}
/>
```

**Note:** `onSeek`, `currentTime`, and `duration` are still passed. `onSkipBack` and `onSkipForward` are removed.

### Changes to useKeyboardShortcuts.ts

#### 2g: Keep seek shortcuts (desktop only feature)

Keep `onSeekBack` and `onSeekForward` in the `KeyboardShortcutActions` interface and the `switch` handler (ArrowLeft/ArrowRight). These shortcuts naturally only work on desktop (mobile has no keyboard).

Interface remains:

```tsx
export interface KeyboardShortcutActions {
  onTogglePlayback: () => void;
  onSeekBack: () => void;
  onSeekForward: () => void;
  onPrevSong: () => void;
  onNextSong: () => void;
}
```

ControllerPlayer.tsx call remains:

```tsx
useKeyboardShortcuts({
  onTogglePlayback: handlePlayPause,
  onSeekBack: handleSkipBack,
  onSeekForward: handleSkipForward,
  onPrevSong: handlePrevSong,
  onNextSong: handleNextSong,
});
```

**Note:** `handleSkipBack` and `handleSkipForward` need to be kept for keyboard shortcuts, even though the UI buttons are removed. Alternatively, inline the seek logic:

```tsx
useKeyboardShortcuts({
  onTogglePlayback: handlePlayPause,
  onSeekBack: () => handleSeek(currentTime - 10),
  onSeekForward: () => handleSeek(currentTime + 10),
  onPrevSong: handlePrevSong,
  onNextSong: handleNextSong,
});
```

### Changes to useMediaSession.ts

#### 2h: Keep seekbackward/seekforward handlers

Keep `onSeekBack` and `onSeekForward` in `MediaSessionActions` interface and the `setActionHandler` calls. These are system-level controls (lock screen, notification shade) that users expect.

Interface remains:

```tsx
export interface MediaSessionActions {
  onPlay?: () => void;
  onPause?: () => void;
  onPrevSong?: () => void;
  onNextSong?: () => void;
  onSeekBack?: () => void;
  onSeekForward?: () => void;
}
```

ControllerPlayer.tsx call remains:

```tsx
const { updatePlaybackState, updatePositionState } = useMediaSession(
  mediaSessionMetadata,
  {
    onPlay: handlePlayPause,
    onPause: handlePlayPause,
    onPrevSong: handlePrevSong,
    onNextSong: handleNextSong,
    onSeekBack: handleSkipBack,
    onSeekForward: handleSkipForward,
  }
);
```

### Changes to keyboard shortcuts hint

#### 2i: Keep ArrowLeft/ArrowRight hint

**File:** `webapp/src/components/play/ControllerPlayer.tsx:482-498`

Keep current hint (desktop still has seek capability via keyboard):

```tsx
<div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
  <span><kbd>Space</kbd> Play/Pause</span>
  <span><kbd>←</kbd>/<kbd>→</kbd> Seek 10s</span>
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

## Change 3: Platform-Conditional Swipe on Lyrics Sheet

### Problem

1. **Ghost click**: Tapping the handle bar to open the sheet causes a synthetic click event that lands on a lyrics line button, jumping playback to that line's timestamp.
2. **Android pull-to-refresh conflict**: Chrome for Android has mandatory pull-to-refresh. Swiping down on the lyrics sheet triggers a browser page reload instead of closing the sheet.
3. **iOS swipe convention**: iOS users expect swipe-to-dismiss on bottom sheets (UISheetPresentationController pattern).
4. **Touch propagation**: Scrolling the lyrics content area can bubble up and trigger the parent's `handleInteraction`, causing controls to flash on/off.

### Solution

- **iOS**: Keep swipe-to-dismiss + click toggle. Add ghost-click protection and `overscroll-behavior` to prevent swipe propagation.
- **Android**: Remove swipe entirely. Click-only toggle with ghost-click protection.
- **Both platforms**: Add `onTouchStart` stopPropagation on container to prevent parent controls from flashing during lyrics scrolling.

### Platform Detection

Extract the iOS detection pattern from `ControllerPlayer.tsx:57-58` into a reusable utility:

**File:** `webapp/src/lib/platform.ts`

```tsx
export function isIOS(): boolean {
  if (typeof navigator === "undefined") return false;
  return (
    /iPad|iPhone|iPod/.test(navigator.userAgent) &&
    !(window as unknown as { MSStream: boolean }).MSStream
  );
}

export function isAndroid(): boolean {
  if (typeof navigator === "undefined") return false;
  return /Android/.test(navigator.userAgent);
}
```

### Changes to LyricJumpList.tsx

#### 3a: Add platform detection and ghost-click protection state

```tsx
import { isIOS } from "@/lib/platform";

// ... inside component:
const [isOpen, setIsOpen] = useState(false);
const [contentInteractive, setContentInteractive] = useState(false);
const [isDragging, setIsDragging] = useState(false);
const [startY, setStartY] = useState(0);
const [currentY, setCurrentY] = useState(0);
const sheetRef = useRef<HTMLDivElement>(null);
const contentRef = useRef<HTMLDivElement>(null);
const lastToggleTimeRef = useRef(0);

const isSwipeEnabled = isIOS();
```

#### 3b: Ghost-click protection on toggle

```tsx
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

#### 3c: Swipe handlers (iOS only)

Keep the existing swipe handlers but guard them with `isSwipeEnabled`:

```tsx
const handleTouchStart = useCallback(
  (e: React.TouchEvent | React.MouseEvent) => {
    if (!isSwipeEnabled) return;
    e.stopPropagation();
    const clientY =
      "touches" in e ? e.touches[0].clientY : (e as React.MouseEvent).clientY;
    setStartY(clientY);
    setIsDragging(true);
  },
  [isSwipeEnabled]
);

const handleTouchMove = useCallback(
  (e: React.TouchEvent | React.MouseEvent) => {
    if (!isSwipeEnabled || !isDragging) return;
    e.stopPropagation();

    const clientY =
      "touches" in e ? e.touches[0].clientY : (e as React.MouseEvent).clientY;
    const deltaY = startY - clientY;

    if (!isOpen && deltaY > 0) {
      setCurrentY(Math.min(deltaY, 300));
    } else if (isOpen && deltaY < 0) {
      setCurrentY(Math.max(deltaY, -300));
    }
  },
  [isSwipeEnabled, isDragging, startY, isOpen]
);

const handleTouchEnd = useCallback(
  (e: React.TouchEvent | React.MouseEvent) => {
    if (!isSwipeEnabled || !isDragging) return;
    e.stopPropagation();

    const now = Date.now();
    const threshold = 100;
    const absY = Math.abs(currentY);

    const shouldToggle =
      (currentY > threshold || absY < 30) && now - lastToggleTimeRef.current > 100;

    if (!isOpen && shouldToggle) {
      setIsOpen(true);
      lastToggleTimeRef.current = now;
    } else if (isOpen && shouldToggle) {
      setIsOpen(false);
      lastToggleTimeRef.current = now;
    }

    setIsDragging(false);
    setCurrentY(0);
  },
  [isSwipeEnabled, isDragging, currentY, isOpen]
);
```

#### 3d: Handle bar with conditional swipe handlers and text

```tsx
<div
  className="flex flex-col items-center justify-center h-12 bg-black/90 backdrop-blur-sm rounded-t-2xl cursor-pointer"
  onClick={handleToggle}
  onTouchStart={isSwipeEnabled ? handleTouchStart : undefined}
  onTouchMove={isSwipeEnabled ? handleTouchMove : undefined}
  onTouchEnd={isSwipeEnabled ? handleTouchEnd : undefined}
  onMouseDown={isSwipeEnabled ? handleTouchStart : undefined}
  onMouseMove={isSwipeEnabled ? handleTouchMove : undefined}
  onMouseUp={isSwipeEnabled ? handleTouchEnd : undefined}
  onMouseLeave={isSwipeEnabled ? handleTouchEnd : undefined}
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
    <span>
      {isOpen
        ? isSwipeEnabled
          ? "Swipe down to close"
          : "Tap to close"
        : "Lyrics"}
    </span>
  </div>
</div>
```

#### 3e: Content area with ghost-click protection and overscroll-behavior

```tsx
<div
  ref={contentRef}
  className={cn(
    "bg-black/90 backdrop-blur-sm max-h-[60vh] overflow-y-auto",
    !contentInteractive && "pointer-events-none",
    isSwipeEnabled && "overscroll-y-contain"
  )}
>
```

**Note:** `overscroll-y-contain` is a Tailwind utility for `overscroll-behavior-y: contain`. If not available, use inline style:

```tsx
style={isSwipeEnabled ? { overscrollBehaviorY: "contain" } : undefined}
```

#### 3f: Sheet container with onTouchStart stopPropagation

```tsx
<div
  ref={sheetRef}
  className={cn(
    "fixed bottom-0 left-0 right-0 z-50 transition-transform duration-300 ease-out",
    isOpen ? "translate-y-0" : "translate-y-[calc(100%-48px)]",
    className
  )}
  style={
    isSwipeEnabled && isDragging
      ? {
          transform: `translateY(${isOpen ? currentY : currentY - 48}px)`,
        }
      : undefined
  }
  onClick={(e) => e.stopPropagation()}
  onTouchStart={(e) => e.stopPropagation()}
>
```

**Note:** `onTouchStart` stopPropagation is kept to prevent touches inside the sheet (including scrolling the lyrics content) from triggering the parent's `handleInteraction` (which would show/hide the main controls).

#### 3g: Backdrop remains unchanged

The backdrop (lines 247-261) remains unchanged. Clicking the backdrop still closes the sheet.

---

## Summary of All File Changes

| File | Changes |
|------|---------|
| `ControllerPlayer.tsx` | Video `onClick` → `handleInteraction()` instead of `handlePlayPause()`. Remove `handleSkipBack`, `handleSkipForward` (or inline for keyboard shortcuts). Remove `onSkipBack`, `onSkipForward` from `<PlaybackControls>` props (keep `onSeek`, `currentTime`, `duration`). Keep keyboard shortcuts and media session seek handlers. |
| `PlaybackControls.tsx` | Responsive progress bar: mobile = thin read-only, desktop = interactive scrub bar. Keep time display on all sizes. Remove Skip ±10s buttons. Simplify center to Play/Pause only. Change volume visibility from `sm:` to `md:`. Remove `onSkipBack`, `onSkipForward` from props. Keep `onSeek`, `currentTime`, `duration`, `formatTime`. |
| `LyricJumpList.tsx` | Add `isSwipeEnabled` via `isIOS()` detection. Add `handleToggle` click handler. Add `contentInteractive` state with 350ms delay for ghost-click protection. Guard swipe handlers with `isSwipeEnabled`. Add `overscroll-behavior-y: contain` on iOS. Conditional handle bar text. Keep `onTouchStart` stopPropagation on container. |
| `lib/platform.ts` | **NEW FILE**. Export `isIOS()` and `isAndroid()` utility functions. |
| `useKeyboardShortcuts.ts` | Keep `onSeekBack`, `onSeekForward` in interface and handler (desktop keyboard seeking). |
| `useMediaSession.ts` | Keep `onSeekBack`, `onSeekForward` in interface and `setActionHandler` calls (system media controls). |

---

## Test Changes

### PlaybackControls.test.tsx

- **Remove**: Tests for skip back/forward buttons
- **Remove**: `mockSkipBack`, `mockSkipForward` mocks
- **Remove**: `onSkipBack`, `onSkipForward` from `defaultProps`
- **Keep**: `onSeek`, `currentTime`, `duration` in `defaultProps`
- **Add**: Test that thin progress bar renders on mobile with correct width percentage
- **Add**: Test that thin progress bar has no click handler (no seek on mobile)
- **Add**: Test that interactive scrub bar renders on desktop (md+)
- **Add**: Test that scrub bar click handler works on desktop
- **Keep**: Time display tests
- **Update**: Volume button/slider tests to account for `md:` visibility
- **Keep**: Play/pause, prev/next song, presentation mode, disabled states tests

### LyricJumpList.test.tsx

- **Add**: Mock `isIOS()` to return `true` for iOS tests, `false` for Android tests
- **Add**: iOS test: swipe down closes the sheet
- **Add**: iOS test: swipe handlers are attached
- **Add**: Android test: swipe handlers are NOT attached
- **Add**: Android test: click toggle opens/closes the sheet
- **Add**: Test that lyrics line clicks are ignored within 350ms of opening (ghost-click protection)
- **Add**: Test that lyrics line clicks work after 350ms delay
- **Add**: Test that `onTouchStart` stopPropagation is present on container
- **Update**: Handle bar text assertions (conditional: "Swipe down to close" vs "Tap to close")
- **Keep**: Backdrop-to-close, chapter/line jump, keyboard navigation, current line highlighting tests

### ControllerPlayer.test.tsx

- **Remove**: Tests for skip back/forward buttons
- **Keep**: Scrub bar tests (desktop)
- **Add**: Test that clicking the video shows controls (does NOT toggle play/pause)
- **Keep**: Keyboard shortcut tests for ArrowLeft/ArrowRight (desktop)
- **Update**: Volume tests that depend on `sm:` breakpoint (update to `md:`)

### lib/platform.test.ts

- **NEW FILE**. Tests for `isIOS()` and `isAndroid()` with various user agents.

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
9. Repeat on desktop browser
10. Confirm: Same behavior (tap shows controls, does not pause)

### Change 2: Responsive Playback Controls

**Mobile (375px viewport):**
1. Open Worship screen on mobile browser
2. Confirm: Only Prev Song, Play/Pause, Next Song buttons visible
3. Confirm: No Skip ±10s buttons
4. Confirm: No interactive scrub/seek bar
5. Confirm: Thin progress bar (2-3px) is visible
6. Confirm: Time display shows "X:XX / X:XX" format
7. Confirm: No volume/mute controls
8. Tap the progress bar
9. Confirm: No seek happens (read-only)
10. Progress bar updates as song plays

**Desktop (1024px+ viewport):**
1. Open Worship screen on desktop browser
2. Confirm: Prev Song, Play/Pause, Next Song buttons visible
3. Confirm: No Skip ±10s buttons
4. Confirm: Interactive scrub bar is visible
5. Confirm: Time display shows "X:XX / X:XX" format
6. Confirm: Volume/mute controls visible
7. Click on scrub bar
8. Confirm: Seek happens to clicked position
9. Test keyboard: Space = play/pause, `[` = prev song, `]` = next song
10. Test keyboard: ArrowLeft = seek back 10s, ArrowRight = seek forward 10s

### Change 3: Platform-Conditional Swipe

**iOS Safari:**
1. Open Worship screen on iOS Safari
2. Tap the "Lyrics" handle bar
3. Confirm: Sheet opens, playback does NOT jump to any lyrics line
4. Wait 0.5s, then tap a lyrics line
5. Confirm: Playback jumps to that line's timestamp
6. Swipe down on the handle bar
7. Confirm: Sheet closes
8. Open the sheet, then tap the backdrop
9. Confirm: Sheet closes
10. Open the sheet, then try swiping down on the content area
11. Confirm: No page scroll/refresh triggered (overscroll-behavior works)
12. Scroll the lyrics content
13. Confirm: Main controls do NOT flash on/off

**Android Chrome:**
1. Open Worship screen on Android Chrome
2. Tap the "Lyrics" handle bar
3. Confirm: Sheet opens, playback does NOT jump to any lyrics line
4. Wait 0.5s, then tap a lyrics line
5. Confirm: Playback jumps to that line's timestamp
6. Swipe down on the handle bar
7. Confirm: Sheet does NOT close (swipe disabled)
8. Tap the "Tap to close" handle bar
9. Confirm: Sheet closes
10. Open the sheet, then try swiping down
11. Confirm: No page reload occurs (swipe has no effect)
12. Open the sheet, then tap the backdrop
13. Confirm: Sheet closes
14. Scroll the lyrics content
15. Confirm: Main controls do NOT flash on/off

---

## Out of Scope

- Auto-scrolling the lyrics sheet to the current line when opened
- Restructuring the lyrics sheet as a modal dialog instead of a bottom sheet
- Adding haptic feedback on button presses
- Full-screen lyrics mode
