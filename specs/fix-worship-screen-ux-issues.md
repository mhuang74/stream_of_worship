# Fix: Worship Screen UX Issues

> **Status:** Plan only — not yet implemented.
> **Date:** 2026-05-26

---

## Summary

Five UX bugs on the Worship screen (`/songsets/[id]/play/controller`) affecting both Desktop Chrome and Mobile browsers:

| # | Bug | Platform | Severity |
|---|-----|----------|----------|
| 1 | Lyrics slide-up panel only slides up a tiny bit, no lyrics visible | Desktop | High |
| 2 | Timestamps shown as "nn:nn" (NaN:NaN) | Mobile | High |
| 3 | Playback Controls only visible on click, which pauses playback | Desktop | High |
| 4 | Clicking toggles fullscreen unexpectedly | Desktop | Medium |
| 5 | Playback Controls too wide, Play/Pause button cut off | Mobile | High |

---

## Bug 1: Desktop — Lyrics Panel Only Slides Up a Tiny Bit

### Problem

On Desktop Chrome, clicking the "Lyrics" handle bar at the bottom of the Worship screen causes the panel to slide up only ~10-40px, then immediately snap back down. No lyrics are visible.

### Root Cause

The handle bar (`LyricJumpList.tsx:118-146`) has **no `onClick` handler**. The only toggle mechanisms are:

1. **Drag gesture** with a 100px minimum threshold (`line 68`)
2. **Keyboard** Enter/Space (`line 130-134`)

On desktop, a mouse click fires `onMouseDown` → `onMouseUp` without meaningful `currentY` accumulation. Since `currentY` stays at 0 and the 100px threshold isn't met, the panel never toggles. A slight mouse jitter during click causes a tiny drag (~10-38px), making the panel shift up briefly then snap back.

**Trace of a desktop click:**

1. `onMouseDown` fires `handleTouchStart` (`line 34-43`): sets `isDragging = true`, `startY = clientY`, `currentY = 0`
2. `onMouseUp` fires `handleTouchEnd` (`line 63-78`): checks `currentY > threshold` (0 > 100) = **false**; `currentY < -threshold` (0 < -100) = **false** → **NO toggle occurs**
3. After handler: `isDragging = false`, `currentY = 0`, panel snaps back

### Proposed Fix

**File:** `webapp/src/components/play/LyricJumpList.tsx`

**Location:** Line 118-146 (handle bar `<div>`)

**Current:**
```tsx
<div
  className="flex flex-col items-center justify-center h-12 bg-black/90 backdrop-blur-sm rounded-t-2xl cursor-grab active:cursor-grabbing"
  onTouchStart={handleTouchStart}
  onTouchMove={handleTouchMove}
  onTouchEnd={handleTouchEnd}
  onMouseDown={handleTouchStart}
  onMouseMove={handleTouchMove}
  onMouseUp={handleTouchEnd}
  onMouseLeave={handleTouchEnd}
  role="button"
  tabIndex={0}
  aria-label={isOpen ? "Close lyric jump list" : "Open lyric jump list"}
  onKeyDown={(e) => {
    if (e.key === "Enter" || e.key === " ") {
      setIsOpen(!isOpen);
    }
  }}
>
```

**Proposed:**
```tsx
<div
  className="flex flex-col items-center justify-center h-12 bg-black/90 backdrop-blur-sm rounded-t-2xl cursor-grab active:cursor-grabbing"
  onClick={() => setIsOpen(!isOpen)}
  onTouchStart={handleTouchStart}
  onTouchMove={handleTouchMove}
  onTouchEnd={handleTouchEnd}
  onMouseDown={handleTouchStart}
  onMouseMove={handleTouchMove}
  onMouseUp={handleTouchEnd}
  onMouseLeave={handleTouchEnd}
  role="button"
  tabIndex={0}
  aria-label={isOpen ? "Close lyric jump list" : "Open lyric jump list"}
  onKeyDown={(e) => {
    if (e.key === "Enter" || e.key === " ") {
      setIsOpen(!isOpen);
    }
  }}
>
```

**Rationale:** Adding `onClick={() => setIsOpen(!isOpen)}` allows desktop users to click the handle to toggle the panel. The existing drag gesture handlers remain for touch/swipe interactions. The `onClick` fires after `onMouseUp`, so the drag gesture still works — if the user drags past the threshold, `setIsOpen` is called in `handleTouchEnd`, and the subsequent `onClick` toggles it back, but since the state is already correct, it's a no-op. Actually, we should prevent the `onClick` from firing if a drag occurred.

**Refined approach:** Add a `didDrag` ref that tracks if significant drag occurred, and only toggle on `onClick` if `!didDrag`:

```tsx
const didDragRef = useRef(false);

const handleTouchStart = useCallback(
  (e: React.TouchEvent | React.MouseEvent) => {
    e.stopPropagation();
    const clientY =
      "touches" in e ? e.touches[0].clientY : (e as React.MouseEvent).clientY;
    setStartY(clientY);
    setIsDragging(true);
    didDragRef.current = false;
  },
  []
);

const handleTouchMove = useCallback(
  (e: React.TouchEvent | React.MouseEvent) => {
    if (!isDragging) return;
    e.stopPropagation();

    const clientY =
      "touches" in e ? e.touches[0].clientY : (e as React.MouseEvent).clientY;
    const deltaY = startY - clientY;

    if (Math.abs(deltaY) > 10) {
      didDragRef.current = true;
    }

    if (!isOpen && deltaY > 0) {
      setCurrentY(Math.min(deltaY, 300));
    } else if (isOpen && deltaY < 0) {
      setCurrentY(Math.max(deltaY, -300));
    }
  },
  [isDragging, startY, isOpen]
);

// In handle bar div:
onClick={() => {
  if (!didDragRef.current) {
    setIsOpen(!isOpen);
  }
}}
```

This ensures:
- Simple click → `didDragRef.current` stays `false` → toggle occurs
- Drag gesture → `didDragRef.current` becomes `true` → `onClick` does nothing, `handleTouchEnd` handles the toggle

---

## Bug 2: Mobile — Timestamps Shown as "nn:nn"

### Problem

On Mobile browsers, lyrics timestamps display as "nn:nn" (actually "NaN:NaN") or are missing entirely.

### Root Cause

The `formatTime` function in `LyricJumpList.tsx:81-85` lacks input validation that exists in every other `formatTime` in the codebase:

```tsx
// LyricJumpList.tsx (BUGGY — no guard)
const formatTime = (seconds: number): string => {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
};
```

When `seconds` is `NaN` or `undefined`, `Math.floor(NaN/60)` → `NaN`, producing `"NaN:NaN"`.

**Contrast with correct implementations:**

```tsx
// PlaybackControls.tsx:54-58 (CORRECT — has guard)
const formatTime = (seconds: number): string => {
  if (!isFinite(seconds) || seconds < 0) return "0:00";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
};
```

**How NaN enters the data:**

The `normalizeChaptersManifest` function (`chapters.ts:335`) checks `typeof lineStartSeconds === "number"`, but `typeof NaN === "number"` is `true`, so NaN values pass validation. This can happen if the JSON data contains `null` values that get cast to `NaN` via `as number`.

### Proposed Fix

#### Fix 2a: Add guard to `formatTime` in LyricJumpList

**File:** `webapp/src/components/play/LyricJumpList.tsx`

**Location:** Lines 81-85

**Current:**
```tsx
const formatTime = (seconds: number): string => {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
};
```

**Proposed:**
```tsx
const formatTime = (seconds: number): string => {
  if (!isFinite(seconds) || seconds < 0) return "0:00";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
};
```

#### Fix 2b: Harden `normalizeChaptersManifest` (defense-in-depth)

**File:** `webapp/src/lib/render/chapters.ts`

**Location:** Lines 314-318 (chapter start/end validation) and 335-338 (line startSeconds validation)

**Current (line 314-318):**
```tsx
if (typeof startSeconds !== "number" || typeof endSeconds !== "number") {
  throw new Error(
    `Invalid chapter at index ${index}: missing or invalid startSeconds/endSeconds`
  );
}
```

**Proposed:**
```tsx
if (
  typeof startSeconds !== "number" ||
  typeof endSeconds !== "number" ||
  !Number.isFinite(startSeconds) ||
  !Number.isFinite(endSeconds)
) {
  throw new Error(
    `Invalid chapter at index ${index}: missing or invalid startSeconds/endSeconds`
  );
}
```

**Current (line 335-338):**
```tsx
if (typeof text !== "string" || typeof lineStartSeconds !== "number") {
  throw new Error(
    `Invalid line at index ${lineIndex} in chapter ${index}: missing text or startSeconds`
  );
}
```

**Proposed:**
```tsx
if (
  typeof text !== "string" ||
  typeof lineStartSeconds !== "number" ||
  !Number.isFinite(lineStartSeconds)
) {
  throw new Error(
    `Invalid line at index ${lineIndex} in chapter ${index}: missing text or startSeconds`
  );
}
```

**Rationale:** Adding `Number.isFinite()` rejects `NaN` and `Infinity` values at the data normalization layer, preventing them from reaching the UI. This is defense-in-depth; the `formatTime` guard handles any edge cases that slip through.

---

## Bug 3: Desktop — Playback Controls Only Visible on Click (Which Pauses)

### Problem

On Desktop Chrome, the Playback Controls auto-hide after 2 seconds of playing. To reveal them, the user must click somewhere. However, clicking the video (the largest clickable area) toggles play/pause, which is not what the user wants — they just want to see the controls to scrub to a new position.

### Root Cause

Controls visibility is driven only by `onClick` and `onTouchStart` on the outer container (`ControllerPlayer.tsx:368-369`). There is **no `onMouseMove` handler**. On desktop, the standard video player pattern is: mouse movement reveals controls, click on video toggles play/pause.

**Current flow on desktop:**

1. Controls auto-hide after 2s of playing (`line 121-125`)
2. User moves mouse → **nothing happens** (no `onMouseMove` handler)
3. User clicks video → `handlePlayPause()` fires → **playback pauses** (`line 379-382`)
4. User clicks background → `handleInteraction()` → controls appear, but background click area is tiny

### Proposed Fix

#### Fix 3a: Add `onMouseMove` to reveal controls

**File:** `webapp/src/components/play/ControllerPlayer.tsx`

**Location:** Lines 363-370 (outer container `<div>`)

**Current:**
```tsx
<div
  className={cn(
    "fixed inset-0 z-[70] bg-black flex flex-col",
    className
  )}
  onClick={handleInteraction}
  onTouchStart={handleInteraction}
>
```

**Proposed:**
```tsx
<div
  className={cn(
    "fixed inset-0 z-[70] bg-black flex flex-col",
    className
  )}
  onClick={handleInteraction}
  onTouchStart={handleInteraction}
  onMouseMove={handleInteraction}
>
```

**Rationale:** Adding `onMouseMove` allows desktop users to reveal controls by simply moving the mouse, without clicking. The existing `onClick` on the video for play/pause remains standard behavior.

#### Fix 3b: Keep controls visible while hovering over them

**File:** `webapp/src/components/play/ControllerPlayer.tsx`

**Location:** Lines 466-471 (controls container `<div>`)

**Current:**
```tsx
<div
  ref={controlsRef}
  className={cn(
    "transition-opacity duration-300 pb-12",
    controlsVisible || isPresentationActive ? "opacity-100" : "opacity-0"
  )}
>
```

**Proposed:**
```tsx
<div
  ref={controlsRef}
  className={cn(
    "transition-opacity duration-300 pb-12",
    controlsVisible || isPresentationActive ? "opacity-100" : "opacity-0"
  )}
  onMouseEnter={() => {
    if (hideTimeoutRef.current) {
      clearTimeout(hideTimeoutRef.current);
    }
  }}
  onMouseLeave={startHideTimer}
>
```

**Rationale:** When the mouse enters the controls area, cancel the auto-hide timer so controls stay visible while the user is interacting with them. When the mouse leaves, restart the timer.

#### Fix 3c: Add `pointer-events-none` when controls are hidden

**File:** `webapp/src/components/play/ControllerPlayer.tsx`

**Location:** Lines 466-471 (controls container `<div>`)

**Proposed (add to className):**
```tsx
className={cn(
  "transition-opacity duration-300 pb-12",
  controlsVisible || isPresentationActive
    ? "opacity-100"
    : "opacity-0 pointer-events-none",
  className
)}
```

**Rationale:** When controls are hidden (`opacity-0`), they should not intercept mouse events. This prevents accidental clicks on invisible buttons.

---

## Bug 4: Desktop — Clicking Toggles Fullscreen Unexpectedly

### Problem

On Desktop Chrome, clicking somewhere causes fullscreen to exit and re-enter, creating a visible flicker. This is annoying and disorienting.

### Root Cause

The fullscreen `useEffect` depends on `[showControls]` (`line 346`). The callback chain is:

```
startHideTimer → depends on [isPresentationActive, isPlaying] (line 126)
showControls → depends on [startHideTimer] (line 131)
handleInteraction → depends on [showControls] (line 136)
```

So every time `isPlaying` changes (e.g., user clicks video → play/pause toggles), `showControls` gets a new reference, the fullscreen effect's cleanup runs `document.exitFullscreen()`, then the effect re-runs and calls `requestFullscreen()` — causing a fullscreen flicker on every play/pause.

**Additionally:** Chrome natively toggles fullscreen on **double-click** on `<video>` elements, which would exit fullscreen even after we fix the dependency issue.

### Proposed Fix

#### Fix 4a: Use a ref for `showControls` in fullscreen effect

**File:** `webapp/src/components/play/ControllerPlayer.tsx`

**Location:** Lines 318-346 (fullscreen `useEffect`)

**Current:**
```tsx
// Request fullscreen on mount
useEffect(() => {
  const requestFullscreen = async () => {
    try {
      if (document.documentElement.requestFullscreen) {
        await document.documentElement.requestFullscreen();
      }
    } catch {
      // Fullscreen not supported or blocked
    }
  };

  requestFullscreen();

  const handleFullscreenChange = () => {
    if (!document.fullscreenElement) {
      showControls();
    }
  };

  document.addEventListener("fullscreenchange", handleFullscreenChange);

  return () => {
    document.removeEventListener("fullscreenchange", handleFullscreenChange);
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    }
  };
}, [showControls]);
```

**Proposed:**
```tsx
// Ref to access showControls without triggering re-runs
const showControlsRef = useRef(showControls);
showControlsRef.current = showControls;

// Request fullscreen on mount
useEffect(() => {
  const requestFullscreen = async () => {
    try {
      if (document.documentElement.requestFullscreen) {
        await document.documentElement.requestFullscreen();
      }
    } catch {
      // Fullscreen not supported or blocked
    }
  };

  requestFullscreen();

  const handleFullscreenChange = () => {
    if (!document.fullscreenElement) {
      showControlsRef.current();
    }
  };

  document.addEventListener("fullscreenchange", handleFullscreenChange);

  return () => {
    document.removeEventListener("fullscreenchange", handleFullscreenChange);
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    }
  };
}, []); // Empty dependency array — only run on mount/unmount
```

**Rationale:** Using a ref for `showControls` allows the `handleFullscreenChange` callback to access the latest `showControls` function without causing the effect to re-run. The effect now only runs on mount (enter fullscreen) and unmount (exit fullscreen).

#### Fix 4b: Prevent Chrome's native double-click-to-fullscreen on video

**File:** `webapp/src/components/play/ControllerPlayer.tsx`

**Location:** Lines 373-383 (`<video>` element)

**Current:**
```tsx
<video
  ref={videoRef}
  src={videoSrc}
  className="w-full h-full object-contain"
  playsInline
  muted={isPresentationActive}
  onClick={(e) => {
    e.stopPropagation();
    handlePlayPause();
  }}
/>
```

**Proposed:**
```tsx
<video
  ref={videoRef}
  src={videoSrc}
  className="w-full h-full object-contain"
  playsInline
  muted={isPresentationActive}
  onClick={(e) => {
    e.stopPropagation();
    handlePlayPause();
  }}
  onDoubleClick={(e) => {
    e.preventDefault();
  }}
/>
```

**Rationale:** Preventing the default behavior on double-click stops Chrome from toggling fullscreen when the user double-clicks the video. The Worship screen should always be in fullscreen mode; users can exit by pressing the Back button.

---

## Bug 5: Mobile — Playback Controls Too Wide, Play/Pause Button Cut Off

### Problem

On Mobile browsers, the Playback Controls are too wide for the viewport. The Play/Pause button is partially cut off and not fully visible.

### Root Cause

The main controls row (`PlaybackControls.tsx:112`) uses `flex justify-between gap-4` with three groups of fixed-size buttons:

| Group | Buttons | Size | Total Width |
|-------|---------|------|-------------|
| Song nav | 2× SkipBack/SkipForward + counter | `size-12` (48px) × 2 + `min-w-[3rem]` (48px) | ~160px |
| Playback | 2× Skip + 1× Play/Pause | `size-14` (56px) × 2 + `size-16` (64px) | ~188px |
| Volume | 1× Mute button | `size-10` (40px) | ~40px |

**Total minimum:** ~160 + ~188 + ~40 + 2× `gap-4` (32px) = **~420px**

On a 375px iPhone viewport, this overflows by ~45px. The Play/Pause button (`size-16` = 64px) is the largest single element and gets cut off.

### Proposed Fix

Make the layout responsive using Tailwind's `sm:` breakpoint (640px). On mobile (below `sm:`), use smaller buttons and tighter gaps.

**File:** `webapp/src/components/play/PlaybackControls.tsx`

**Location:** Lines 112-225 (main controls)

#### Fix 5a: Reduce button sizes on mobile

**Song navigation (lines 114-138):**

**Current:**
```tsx
<div className="flex items-center gap-2">
  <Button
    variant="ghost"
    size="icon"
    className="size-12 text-white hover:bg-white/20"
    onClick={onPrevSong}
    disabled={currentSongIndex <= 0}
    aria-label="Previous song"
  >
    <SkipBack className="size-6" />
  </Button>
  <span className="text-sm text-white/70 min-w-[3rem] text-center">
    {currentSongIndex + 1}/{totalSongs}
  </span>
  <Button
    variant="ghost"
    size="icon"
    className="size-12 text-white hover:bg-white/20"
    onClick={onNextSong}
    disabled={currentSongIndex >= totalSongs - 1}
    aria-label="Next song"
  >
    <SkipForward className="size-6" />
  </Button>
</div>
```

**Proposed:**
```tsx
<div className="flex items-center gap-1 sm:gap-2">
  <Button
    variant="ghost"
    size="icon"
    className="size-10 sm:size-12 text-white hover:bg-white/20"
    onClick={onPrevSong}
    disabled={currentSongIndex <= 0}
    aria-label="Previous song"
  >
    <SkipBack className="size-5 sm:size-6" />
  </Button>
  <span className="text-xs sm:text-sm text-white/70 min-w-[2.5rem] sm:min-w-[3rem] text-center">
    {currentSongIndex + 1}/{totalSongs}
  </span>
  <Button
    variant="ghost"
    size="icon"
    className="size-10 sm:size-12 text-white hover:bg-white/20"
    onClick={onNextSong}
    disabled={currentSongIndex >= totalSongs - 1}
    aria-label="Next song"
  >
    <SkipForward className="size-5 sm:size-6" />
  </Button>
</div>
```

#### Fix 5b: Reduce playback button sizes on mobile

**Playback controls (lines 140-185):**

**Current:**
```tsx
<div className="flex items-center gap-2">
  <Button
    variant="ghost"
    size="icon"
    className="size-14 text-white hover:bg-white/20"
    onClick={onSkipBack}
    aria-label="Skip back 10 seconds"
  >
    <div className="relative">
      <SkipBack className="size-6" />
      <span className="absolute -bottom-1 left-1/2 -translate-x-1/2 text-[8px] font-bold">
        10
      </span>
    </div>
  </Button>

  <Button
    variant="default"
    size="icon"
    className="size-16 rounded-full bg-white text-black hover:bg-white/90"
    onClick={onPlayPause}
    aria-label={isPlaying ? "Pause" : "Play"}
  >
    {isPlaying ? (
      <Pause className="size-8" />
    ) : (
      <Play className="size-8 ml-1" />
    )}
  </Button>

  <Button
    variant="ghost"
    size="icon"
    className="size-14 text-white hover:bg-white/20"
    onClick={onSkipForward}
    aria-label="Skip forward 10 seconds"
  >
    <div className="relative">
      <SkipForward className="size-6" />
      <span className="absolute -bottom-1 left-1/2 -translate-x-1/2 text-[8px] font-bold">
        10
      </span>
    </div>
  </Button>
</div>
```

**Proposed:**
```tsx
<div className="flex items-center gap-1 sm:gap-2">
  <Button
    variant="ghost"
    size="icon"
    className="size-10 sm:size-14 text-white hover:bg-white/20"
    onClick={onSkipBack}
    aria-label="Skip back 10 seconds"
  >
    <div className="relative">
      <SkipBack className="size-5 sm:size-6" />
      <span className="absolute -bottom-1 left-1/2 -translate-x-1/2 text-[7px] sm:text-[8px] font-bold">
        10
      </span>
    </div>
  </Button>

  <Button
    variant="default"
    size="icon"
    className="size-12 sm:size-16 rounded-full bg-white text-black hover:bg-white/90"
    onClick={onPlayPause}
    aria-label={isPlaying ? "Pause" : "Play"}
  >
    {isPlaying ? (
      <Pause className="size-6 sm:size-8" />
    ) : (
      <Play className="size-6 sm:size-8 ml-1" />
    )}
  </Button>

  <Button
    variant="ghost"
    size="icon"
    className="size-10 sm:size-14 text-white hover:bg-white/20"
    onClick={onSkipForward}
    aria-label="Skip forward 10 seconds"
  >
    <div className="relative">
      <SkipForward className="size-5 sm:size-6" />
      <span className="absolute -bottom-1 left-1/2 -translate-x-1/2 text-[7px] sm:text-[8px] font-bold">
        10
      </span>
    </div>
  </Button>
</div>
```

#### Fix 5c: Reduce volume button size on mobile

**Volume controls (lines 187-224):**

**Current:**
```tsx
<div className="flex items-center gap-2">
  <Button
    variant="ghost"
    size="icon"
    className="size-10 text-white hover:bg-white/20"
    onClick={onToggleMute}
    aria-label={isMuted ? "Unmute" : "Mute"}
  >
    {isMuted || volume === 0 ? (
      <VolumeX className="size-5" />
    ) : (
      <Volume2 className="size-5" />
    )}
  </Button>
  {/* ... volume slider ... */}
</div>
```

**Proposed:**
```tsx
<div className="flex items-center gap-1 sm:gap-2">
  <Button
    variant="ghost"
    size="icon"
    className="size-8 sm:size-10 text-white hover:bg-white/20"
    onClick={onToggleMute}
    aria-label={isMuted ? "Unmute" : "Mute"}
  >
    {isMuted || volume === 0 ? (
      <VolumeX className="size-4 sm:size-5" />
    ) : (
      <Volume2 className="size-4 sm:size-5" />
    )}
  </Button>
  {/* ... volume slider (already hidden on mobile via hidden sm:block) ... */}
</div>
```

#### Fix 5d: Reduce main container gap on mobile

**Location:** Line 112

**Current:**
```tsx
<div className="flex items-center justify-between gap-4">
```

**Proposed:**
```tsx
<div className="flex items-center justify-between gap-1 sm:gap-4">
```

### Width Calculation After Fix

| Group | Mobile Size | Desktop Size |
|-------|-------------|--------------|
| Song nav | 2×40 + 40 + gaps = ~130px | 2×48 + 48 + gaps = ~160px |
| Playback | 2×40 + 48 + gaps = ~140px | 2×56 + 64 + gaps = ~188px |
| Volume | 32px | 40px |
| Gaps | 2×4 = 8px | 2×16 = 32px |
| **Total** | **~310px** | **~420px** |

Mobile total (~310px) fits comfortably within 375px viewport.

---

## Summary of Changes

| File | Change | Bug(s) Fixed |
|------|--------|--------------|
| `LyricJumpList.tsx` | Add `onClick` toggle + `didDragRef` | 1 |
| `LyricJumpList.tsx` | Add `isFinite` guard to `formatTime` | 2 |
| `chapters.ts` | Add `Number.isFinite()` checks | 2 (defense-in-depth) |
| `ControllerPlayer.tsx` | Add `onMouseMove` to container | 3 |
| `ControllerPlayer.tsx` | Add `onMouseEnter`/`onMouseLeave` to controls | 3 |
| `ControllerPlayer.tsx` | Add `pointer-events-none` when hidden | 3 |
| `ControllerPlayer.tsx` | Use ref for `showControls` in fullscreen effect | 4 |
| `ControllerPlayer.tsx` | Add `onDoubleClick` preventDefault on video | 4 |
| `PlaybackControls.tsx` | Responsive button sizes with `sm:` variants | 5 |

---

## Verification

### Bug 1: Lyrics Panel

1. Open Worship screen on Desktop Chrome
2. Click the "Lyrics" handle bar at the bottom
3. Confirm the panel slides up fully and lyrics are visible
4. Click the handle bar again
5. Confirm the panel slides down and closes

### Bug 2: Timestamps

1. Open Worship screen on Mobile browser
2. Open the Lyrics panel
3. Confirm all timestamps display correctly (e.g., "0:10", "3:00")
4. Confirm no "nn:nn" or "NaN:NaN" appears

### Bug 3: Controls Visibility

1. Open Worship screen on Desktop Chrome
2. Start playback
3. Wait 2+ seconds for controls to auto-hide
4. Move the mouse (without clicking)
5. Confirm controls appear without pausing playback
6. Hover over the controls area
7. Confirm controls stay visible while hovering
8. Move mouse away from controls
9. Confirm controls auto-hide after 2 seconds

### Bug 4: Fullscreen

1. Open Worship screen on Desktop Chrome
2. Confirm fullscreen is entered automatically
3. Click the video to pause/play
4. Confirm fullscreen does NOT flicker or exit
5. Double-click the video
6. Confirm fullscreen does NOT exit
7. Press the Back button
8. Confirm fullscreen exits and navigation occurs

### Bug 5: Mobile Layout

1. Open Worship screen on Mobile browser (375px viewport)
2. Confirm all Playback Controls are fully visible
3. Confirm the Play/Pause button is not cut off
4. Confirm all buttons are tappable
5. Test on tablet viewport (768px)
6. Confirm controls use larger sizes appropriate for tablet

---

## Out of Scope

- Refactoring `formatTime` into a shared utility (could be future improvement)
- Adding a dedicated fullscreen toggle button (user wants always-fullscreen behavior)
- Using `dvh` units for the lyrics panel `max-h-[60vh]` (minor mobile improvement, not critical)
- Restructuring the controls layout for landscape mobile orientation
