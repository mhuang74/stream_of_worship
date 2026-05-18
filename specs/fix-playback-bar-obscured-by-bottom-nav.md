# Fix: Playback Bar Obscured by BottomNav in Worship Mode

> **Status:** Plan only — not yet implemented.
> **Date:** 2026-05-18

---

## Problem

On the `/songsets/[id]/play/controller` page (Worship mode), the `ControllerPlayer` uses `fixed inset-0` with **no z-index**, while the `BottomNav` (`fixed bottom-0 z-50`) is always rendered by the root layout. The BottomNav paints on top of the ControllerPlayer, obscuring the bottom portion of the `PlaybackControls`.

**Measured impact:** The Play/Pause button (64px tall, at y=752–816) is overlapped by the BottomNav (at y=767–832). Approximately **48 of 64 pixels** (3/4) of the Play button are obscured and untappable.

The `AudioPlayerBar` (`fixed bottom-0 z-[60]`) has the same potential issue but is not currently visible on the controller page (no track loaded in the global audio player during worship playback).

---

## Root Cause

Three fixed-position elements compete for the bottom of the screen:

| Element | Position | Z-Index | Height |
|---------|----------|---------|--------|
| `BottomNav` | `fixed bottom-0` | `z-50` | 64px (`h-16`) |
| `AudioPlayerBar` | `fixed bottom-0` | `z-[60]` | ~80–100px |
| `ControllerPlayer` | `fixed inset-0` | none (auto) | full viewport |

The `ControllerPlayer` has no z-index, so both `BottomNav` and `AudioPlayerBar` paint above it. The root layout (`layout.tsx`) unconditionally renders both `BottomNav` and `GlobalAudioPlayer` on every page, including the controller.

**Key files:**

- `webapp/src/app/layout.tsx` — root layout, unconditionally renders `BottomNav` and `GlobalAudioPlayer`
- `webapp/src/components/layout/BottomNav.tsx` — `fixed bottom-0 z-50`, always visible on mobile (`lg:hidden`)
- `webapp/src/components/audio/AudioPlayerBar.tsx` — `fixed bottom-0 z-[60]`, visible when a track is loaded
- `webapp/src/components/audio/GlobalAudioPlayer.tsx` — wrapper that renders `AudioPlayerBar`
- `webapp/src/components/play/ControllerPlayer.tsx` — `fixed inset-0` with no z-index

---

## Proposed Fix: Hybrid Approach

Apply **both** changes for defense-in-depth:

### Change 1: Add `z-[70]` to ControllerPlayer (safety net)

**File:** `webapp/src/components/play/ControllerPlayer.tsx`

**Current (line 348–351):**
```tsx
className={cn(
  "fixed inset-0 bg-black flex flex-col",
  className
)}
```

**Proposed:**
```tsx
className={cn(
  "fixed inset-0 z-[70] bg-black flex flex-col",
  className
)}
```

**Rationale:** The ControllerPlayer intends to be the topmost layer (full-screen immersive playback). Giving it a z-index above both `BottomNav` (`z-50`) and `AudioPlayerBar` (`z-[60]`) ensures it always paints above app-shell chrome, regardless of whether those elements are hidden. This is a one-line change that prevents any future z-index conflict.

### Change 2: Hide BottomNav on controller routes (semantic correctness)

**File:** `webapp/src/components/layout/BottomNav.tsx`

**Current (line 11–13):**
```tsx
export function BottomNav() {
  const pathname = usePathname();

  return (
```

**Proposed:**
```tsx
export function BottomNav() {
  const pathname = usePathname();

  if (pathname.includes("/play/controller")) {
    return null;
  }

  return (
```

**Rationale:** The BottomNav has no purpose during immersive worship playback. Hiding it removes unnecessary DOM nodes and eliminates invisible touch targets that could intercept taps. The `usePathname` hook is already imported and used in this component for active-state highlighting, so no new imports are needed.

### Change 3: Hide AudioPlayerBar on controller routes (semantic correctness)

**File:** `webapp/src/components/audio/GlobalAudioPlayer.tsx`

Add a route-aware check to suppress the `AudioPlayerBar` when on the controller page. Since `GlobalAudioPlayer` is a client component that wraps children and renders `AudioPlayerBar`, we need to either:

- **Option A:** Pass a `hidden` prop or use a context to tell `AudioPlayerBar` not to render
- **Option B:** Use `usePathname()` inside `GlobalAudioPlayer` to conditionally render `AudioPlayerBar`

**Preferred: Option B** — add `usePathname()` to `GlobalAudioPlayer` and conditionally render `AudioPlayerBar`:

```tsx
const pathname = usePathname();
const isControllerPage = pathname.includes("/play/controller");

// ... existing provider logic ...

return (
  <AudioPlayerContextProvider>
    {children}
    {!isControllerPage && <AudioPlayerBar />}
  </AudioPlayerContextProvider>
);
```

**Rationale:** The global audio player has no role during worship playback (the ControllerPlayer manages its own video/audio). Hiding it avoids confusion and prevents the `z-[60]` overlay from ever competing with the controller.

---

## Why Both Changes

| Scenario | Change 1 only (z-index) | Change 1 + Change 2 + Change 3 |
|----------|------------------------|-------------------------------|
| BottomNav overlaps Play button | Fixed (z-index wins) | Fixed (element removed) |
| AudioPlayerBar overlaps Play button | Fixed (z-index wins) | Fixed (element removed) |
| Invisible touch targets on edges | Still present (BottomNav/AudioPlayerBar intercept taps) | Eliminated |
| Future fixed element with z-[80] | Bug recurs | Bug recurs (but less likely) |
| DOM waste during playback | BottomNav/AudioPlayerBar still rendered | Clean DOM |
| Semantic clarity | ControllerPlayer "wins" by force | App-shell correctly hidden |

---

## What NOT to Change

- **`layout.tsx`**: Do not add route-awareness to the root layout. It's a server component and cannot use `usePathname()`. The conditional hiding belongs in the client components themselves.
- **`PlaybackControls.tsx`**: No changes needed. The controls are correctly positioned within the ControllerPlayer; the issue is the overlay, not the controls themselves.
- **`LyricJumpList.tsx`**: No changes needed. It's rendered inside the ControllerPlayer and will benefit from the z-index fix.

---

## Verification

1. Navigate to `/songsets/[id]/play/controller` on a mobile viewport
2. Confirm the Play/Pause button is fully visible and tappable
3. Confirm the BottomNav ("Songsets" / "Settings") is not visible
4. Confirm the AudioPlayerBar is not visible
5. Navigate back to `/songsets` and confirm the BottomNav reappears
6. Navigate to a non-controller page with audio playing and confirm the AudioPlayerBar is visible

---

## Out of Scope

- Refactoring the z-index system into a shared constant file (could be a future improvement)
- Adding a "full-screen mode" context/provider to coordinate hiding app-shell elements (over-engineering for current needs)
- Adjusting `pb-16` / `pb-24` padding values on other pages (unrelated to this bug)
