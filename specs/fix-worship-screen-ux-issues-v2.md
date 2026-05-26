# Fix: Worship Screen UX Issues (v2)

> **Status:** Plan only — not yet implemented.
> **Date:** 2026-05-26
> **Supersedes:** `fix-worship-screen-ux-issues.md` (v1)

---

## Changes from v1

| Bug | v1 Approach | v2 Change | Reason |
|-----|-------------|-----------|--------|
| 1 | `didDragRef` threshold 10px | Raise to 30px | 10px threshold created a dead zone: 15px drag sets `didDrag=true` but doesn't meet 100px toggle threshold, silently swallowing the interaction |
| 3 | Fixes 3a + 3b + 3c as separate items | Keep all three, document they are compatible | `pointer-events-none` only applies when controls are hidden, so `onMouseEnter`/`onMouseLeave` work when controls are visible — no conflict |
| 3 | No throttle on `onMouseMove` | No throttle (confirmed) | React batches state updates; `clearTimeout`/`setTimeout` is cheap |
| 4 | No fullscreen re-entry mechanism | Add "Re-enter Fullscreen" button | If user accidentally exits fullscreen (Escape), there's no way back without navigating away |
| 5 | Shrink mute button on mobile | Remove mute button on mobile entirely | Hardware volume buttons make mute toggle unnecessary on mobile; saves ~32px width |
| 2 | Defense-in-depth in `normalizeChaptersManifest` only | Also harden `parseChaptersManifest` | Same `typeof`-only check exists in `parseChaptersManifest` (lines 222-232) |

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

#### Fix 1a: Add `didDragRef` with 30px threshold

Add a `didDrag` ref that tracks whether a significant drag occurred. Only toggle on `onClick` if the user did **not** drag past 30px. This threshold is high enough to avoid the dead zone between 10px (v1) and 100px (toggle threshold), while still distinguishing intentional drags from click jitter.

**New code (add after line 30):**

```tsx
const didDragRef = useRef(false);
```

**Modified `handleTouchStart` (lines 34-43):**

```tsx
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
```

**Modified `handleTouchMove` (lines 45-61):**

```tsx
const handleTouchMove = useCallback(
  (e: React.TouchEvent | React.MouseEvent) => {
    if (!isDragging) return;
    e.stopPropagation();

    const clientY =
      "touches" in e ? e.touches[0].clientY : (e as React.MouseEvent).clientY;
    const deltaY = startY - clientY;

    if (Math.abs(deltaY) > 30) {
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
```

#### Fix 1b: Add `onClick` handler to handle bar

**Location:** Lines 118-146 (handle bar `<div>`)

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
  onClick={() => {
    if (!didDragRef.current) {
      setIsOpen(!isOpen);
    }
  }}
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

**Interaction matrix:**

| User action | `didDragRef.current` | `handleTouchEnd` | `onClick` | Result |
|-------------|---------------------|-------------------|-----------|--------|
| Simple click (no movement) | `false` | No toggle (currentY=0) | Toggles | Panel opens/closes |
| Small drag < 30px | `false` | No toggle (currentY < threshold) | Toggles | Panel opens/closes |
| Medium drag 30-99px | `true` | No toggle (currentY < threshold) | Skipped | Panel snaps back (user didn't commit) |
| Full drag ≥ 100px | `true` | Toggles | Skipped | Panel opens/closes via drag |

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

**Contrast with correct implementation:**

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

**Location:** Lines 314-318 (chapter start/end validation)

**Current:**
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

**Location:** Lines 335-338 (line startSeconds validation)

**Current:**
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

#### Fix 2c: Harden `parseChaptersManifest` (defense-in-depth)

**File:** `webapp/src/lib/render/chapters.ts`

**Location:** Lines 222-232 (chapter validation in `parseChaptersManifest`)

**Current:**
```tsx
for (const chapter of parsed.chapters) {
  if (
    typeof chapter.position !== "number" ||
    typeof chapter.songTitle !== "string" ||
    typeof chapter.startSeconds !== "number" ||
    typeof chapter.endSeconds !== "number" ||
    !Array.isArray(chapter.lines)
  ) {
    throw new Error("Invalid chapter structure");
  }
}
```

**Proposed:**
```tsx
for (const chapter of parsed.chapters) {
  if (
    typeof chapter.position !== "number" ||
    typeof chapter.songTitle !== "string" ||
    typeof chapter.startSeconds !== "number" ||
    typeof chapter.endSeconds !== "number" ||
    !Number.isFinite(chapter.startSeconds) ||
    !Number.isFinite(chapter.endSeconds) ||
    !Array.isArray(chapter.lines)
  ) {
    throw new Error("Invalid chapter structure");
  }
}
```

**Rationale:** Adding `Number.isFinite()` rejects `NaN` and `Infinity` values at both normalization and parsing layers, preventing them from reaching the UI. The `formatTime` guard handles any edge cases that slip through.

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

All three sub-fixes are compatible and should be applied together:

- **Fix 3b** (`onMouseEnter`/`onMouseLeave`) only fires when controls are **visible** (no `pointer-events-none`)
- **Fix 3c** (`pointer-events-none`) only applies when controls are **hidden** (no `onMouseEnter` possible)
- These are complementary, not contradictory

**Edge case:** During the 300ms fade-out transition, `pointer-events-none` is applied immediately (CSS doesn't wait for the transition). If the mouse re-enters the controls during this window, `onMouseEnter` won't fire. However, the container's `onMouseMove` (Fix 3a) would still detect movement and re-show controls. This is a very narrow window and unlikely to cause issues.

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

**Rationale:** Adding `onMouseMove` allows desktop users to reveal controls by simply moving the mouse, without clicking. No throttle needed — React batches state updates and `clearTimeout`/`setTimeout` is cheap.

#### Fix 3b: Keep controls visible while hovering over them

**File:** `webapp/src/components/play/ControllerPlayer.tsx`

**Location:** Lines 466-472 (controls container `<div>`)

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

**Rationale:** When the mouse enters the controls area, cancel the auto-hide timer so controls stay visible while the user is interacting with them. When the mouse leaves, restart the timer. On mobile (touch-only), these events never fire — no impact.

#### Fix 3c: Add `pointer-events-none` when controls are hidden

**File:** `webapp/src/components/play/ControllerPlayer.tsx`

**Location:** Lines 466-472 (controls container `<div>`)

**Proposed (modify className):**
```tsx
className={cn(
  "transition-opacity duration-300 pb-12",
  controlsVisible || isPresentationActive
    ? "opacity-100"
    : "opacity-0 pointer-events-none"
)}
```

**Rationale:** When controls are hidden (`opacity-0`), they should not intercept mouse events. This prevents accidental clicks on invisible buttons. Since `pointer-events-none` is only applied when controls are hidden, it does not conflict with Fix 3b's `onMouseEnter`/`onMouseLeave` (which only need to fire when controls are visible).

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

**Location:** Lines 319-346 (fullscreen `useEffect`)

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

**Rationale:** Preventing the default behavior on double-click stops Chrome from toggling fullscreen when the user double-clicks the video.

#### Fix 4c: Add "Re-enter Fullscreen" button

**File:** `webapp/src/components/play/ControllerPlayer.tsx`

When the user accidentally exits fullscreen (e.g., pressing Escape), there is currently no way to re-enter fullscreen without navigating away and back. Add a button that appears when not in fullscreen mode.

**New state:**
```tsx
const [isFullscreen, setIsFullscreen] = useState(false);
```

**New effect (alongside existing fullscreen effect):**
```tsx
useEffect(() => {
  const handleFullscreenChange = () => {
    setIsFullscreen(!!document.fullscreenElement);
  };

  document.addEventListener("fullscreenchange", handleFullscreenChange);

  return () => {
    document.removeEventListener("fullscreenchange", handleFullscreenChange);
  };
}, []);
```

**New handler:**
```tsx
const handleReenterFullscreen = useCallback(() => {
  document.documentElement.requestFullscreen().catch(() => {});
}, []);
```

**New import:**
```tsx
import { ArrowLeft, X, Info, Maximize } from "lucide-react";
```

**Button placement:** Add to the top bar (lines 386-401), next to the existing Back button. Only visible when not in fullscreen:

```tsx
{!isFullscreen && (
  <Button
    variant="ghost"
    size="icon"
    className="size-10 text-white hover:bg-white/20"
    onClick={handleReenterFullscreen}
    aria-label="Re-enter fullscreen"
  >
    <Maximize className="size-5" />
  </Button>
)}
```

**Rationale:** This provides a user-initiated way to re-enter fullscreen after accidental exit. Browsers require fullscreen requests to be in response to a user gesture, so a button click satisfies this requirement. The button is only shown when not in fullscreen, so it doesn't clutter the normal UI.

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

Make the layout responsive using Tailwind's `sm:` breakpoint (640px). On mobile (below `sm:`), use smaller buttons, tighter gaps, and **remove the mute button entirely** (hardware volume buttons make it unnecessary on mobile).

**File:** `webapp/src/components/play/PlaybackControls.tsx`

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

**Playback controls (lines 141-185):**

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

#### Fix 5c: Remove mute button on mobile, keep on desktop

**Volume controls (lines 188-224):**

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

  {/* Volume slider */}
  <div className="w-20 hidden sm:block">
    <input
      type="range"
      min={0}
      max={1}
      step={0.01}
      value={isMuted ? 0 : volume}
      onChange={(e) => onVolumeChange(parseFloat(e.target.value))}
      className="w-full h-1 bg-white/30 rounded-full appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:rounded-full"
      aria-label="Volume"
    />
  </div>

  {/* Presentation status indicator */}
  {isPresentationActive && (
    <div className="flex items-center gap-1 px-2 py-1 bg-green-500/20 text-green-400 rounded-full text-xs">
      <Monitor className="size-3" />
      <span className="hidden sm:inline">Connected</span>
    </div>
  )}
</div>
```

**Proposed:**
```tsx
<div className="flex items-center gap-1 sm:gap-2">
  <Button
    variant="ghost"
    size="icon"
    className="hidden sm:flex size-10 text-white hover:bg-white/20"
    onClick={onToggleMute}
    aria-label={isMuted ? "Unmute" : "Mute"}
  >
    {isMuted || volume === 0 ? (
      <VolumeX className="size-5" />
    ) : (
      <Volume2 className="size-5" />
    )}
  </Button>

  {/* Volume slider */}
  <div className="w-20 hidden sm:block">
    <input
      type="range"
      min={0}
      max={1}
      step={0.01}
      value={isMuted ? 0 : volume}
      onChange={(e) => onVolumeChange(parseFloat(e.target.value))}
      className="w-full h-1 bg-white/30 rounded-full appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:rounded-full"
      aria-label="Volume"
    />
  </div>

  {/* Presentation status indicator */}
  {isPresentationActive && (
    <div className="flex items-center gap-1 px-2 py-1 bg-green-500/20 text-green-400 rounded-full text-xs">
      <Monitor className="size-3" />
      <span className="hidden sm:inline">Connected</span>
    </div>
  )}
</div>
```

**Rationale:** On mobile, hardware volume buttons control system audio — there is no use case for a mute toggle. Hiding the mute button on mobile (`hidden sm:flex`) saves ~40px of width. The volume slider was already hidden on mobile (`hidden sm:block`), so this makes the mute button consistent with the slider.

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
| Song nav | 2×40 + 40 + gaps = ~128px | 2×48 + 48 + gaps = ~160px |
| Playback | 2×40 + 48 + gaps = ~136px | 2×56 + 64 + gaps = ~188px |
| Volume | 0px (hidden) | 40px |
| Gaps | 2×4 = 8px | 2×16 = 32px |
| **Total** | **~272px** | **~420px** |

Mobile total (~272px) fits comfortably within 375px viewport with ~103px to spare.

---

## Summary of Changes

| File | Change | Bug(s) Fixed |
|------|--------|--------------|
| `LyricJumpList.tsx` | Add `didDragRef` (30px threshold) + `onClick` toggle | 1 |
| `LyricJumpList.tsx` | Add `isFinite` guard to `formatTime` | 2 |
| `chapters.ts` | Add `Number.isFinite()` checks in `normalizeChaptersManifest` | 2 (defense-in-depth) |
| `chapters.ts` | Add `Number.isFinite()` checks in `parseChaptersManifest` | 2 (defense-in-depth) |
| `ControllerPlayer.tsx` | Add `onMouseMove` to container | 3 |
| `ControllerPlayer.tsx` | Add `onMouseEnter`/`onMouseLeave` to controls | 3 |
| `ControllerPlayer.tsx` | Add `pointer-events-none` when hidden | 3 |
| `ControllerPlayer.tsx` | Use ref for `showControls` in fullscreen effect | 4 |
| `ControllerPlayer.tsx` | Add `onDoubleClick` preventDefault on video | 4 |
| `ControllerPlayer.tsx` | Add `isFullscreen` state + "Re-enter Fullscreen" button | 4 |
| `PlaybackControls.tsx` | Responsive button sizes with `sm:` variants | 5 |
| `PlaybackControls.tsx` | Hide mute button on mobile (`hidden sm:flex`) | 5 |

---

## Verification

### Bug 1: Lyrics Panel

1. Open Worship screen on Desktop Chrome
2. Click the "Lyrics" handle bar at the bottom
3. Confirm the panel slides up fully and lyrics are visible
4. Click the handle bar again
5. Confirm the panel slides down and closes
6. Drag the handle bar upward past 100px
7. Confirm the panel opens via drag gesture
8. Drag the handle bar slightly (< 30px) and release
9. Confirm the panel still toggles (onClick fires because didDragRef stays false)
10. Drag the handle bar 50px and release (without reaching 100px threshold)
11. Confirm the panel snaps back (intentional drag that didn't commit)

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
10. While controls are hidden, click in the area where controls would be
11. Confirm no accidental button clicks occur (pointer-events-none)

### Bug 4: Fullscreen

1. Open Worship screen on Desktop Chrome
2. Confirm fullscreen is entered automatically
3. Click the video to pause/play
4. Confirm fullscreen does NOT flicker or exit
5. Double-click the video
6. Confirm fullscreen does NOT exit
7. Press Escape to exit fullscreen
8. Confirm a "Re-enter Fullscreen" button appears
9. Click the "Re-enter Fullscreen" button
10. Confirm fullscreen is re-entered
11. Press the Back button
12. Confirm fullscreen exits and navigation occurs

### Bug 5: Mobile Layout

1. Open Worship screen on Mobile browser (375px viewport)
2. Confirm all Playback Controls are fully visible
3. Confirm the Play/Pause button is not cut off
4. Confirm the mute button is NOT visible on mobile
5. Confirm all buttons are tappable
6. Test on tablet viewport (768px)
7. Confirm controls use larger sizes appropriate for tablet
8. Confirm mute button IS visible on tablet/desktop

---

## Out of Scope

- Refactoring `formatTime` into a shared utility (could be future improvement)
- Using `dvh` units for the lyrics panel `max-h-[60vh]` (minor mobile improvement, not critical)
- Restructuring the controls layout for landscape mobile orientation
- Auto-re-request fullscreen on Escape (browsers block non-user-initiated fullscreen requests)
