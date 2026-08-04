---
# Songset Editor: Player-Integrated Lyrics Display — v6

[Supersedes v5 (`songset-editor-player-lyrics-v5.md`). Changes: auto-collapse lyrics on modal/sheet open, auto-collapse on route change, delayed unmount via `onTransitionEnd` to fix collapse animation, LRU eviction cap at 50 entries in module-scoped lyrics cache, Error Boundary wrapping `PlayerLyricsPanel`, modal-aware 'L' keyboard shortcut listener with no-op `preventDefault`, switch from `vh` to `dvh` units for mobile Safari, `isValidLRC()` threshold raised to require 2+ matching timestamp lines.]

## Goal

Same as v5: Replace the vague-looking arrow (ChevronDown) that expands lyrics below each Song Row in the Songset Management screen. Instead, show lyrics inside the global AudioPlayerBar. A new "Lyrics" button in the Player expands the Player *upward* to reveal lyrics. Clicking again collapses the Player back to its original height and hides lyrics.

v6 preserves all v5 product decisions and implementation steps, and adds production-hardening fixes for eight concerns identified during review.

## What Changed from v5

| Aspect | v5 | v6 |
|--------|-----|-----|
| Modal/sheet overlap | Documented as Edge Case 12 — "acceptable" | **Auto-collapse lyrics** when any modal/sheet/dialog mounts. Eliminates z-index conflict between `z-[60]` player and `z-50` modals |
| Cross-route content hidden | Documented as Edge Case 10 — "acceptable" | **Auto-collapse on route change** via `usePathname()` effect. Prevents lyrics panel from covering bottom content on non-songset pages |
| Collapse animation | `PlayerLyricsPanel` unmounts immediately; wrapper animates `max-h` → 0 over 300ms (empty container visible during transition) | **Delayed unmount** — keep `PlayerLyricsPanel` mounted during 300ms transition; remove on `onTransitionEnd`. Content stays visible throughout collapse |
| Module-scoped cache eviction | Never evicts; `clearLyricsCache()` export has no callers | **LRU cap at 50 entries** — evict oldest entry when cache exceeds 50 keys. Prevents unbounded memory growth in long sessions |
| Error protection | None — `PlayerLyricsPanel` render errors crash the global `AudioPlayerBar`, breaking audio playback app-wide | **Error Boundary** wrapping `PlayerLyricsPanel`. On error, falls back to "Lyrics unavailable" UI. Player bar keeps working |
| 'L' keyboard shortcut | `e.preventDefault()` fires unconditionally on every 'L' keypress; listener ignores open modals; re-attaches on every track change | **Modal-aware listener** — skip 'L' when a `[role=dialog]` or `[data-slot=sheet]` is present in the DOM. **No-op `preventDefault`** — only call `preventDefault()` when a toggle actually happens. **Stable listener** via `useRef` for `currentTrack` to avoid re-attaching on every track change |
| Mobile viewport units | `max-h-[40vh]` — reflows on mobile Safari as dynamic address bar expands/collapses | **`max-h-[40dvh]`** — dynamic viewport height updates with address bar. Desktop unchanged at `md:max-h-[400px]` |
| LRC misclassification | `isValidLRC()` returns `true` if ANY line matches `[mm:ss.xx]` pattern. Plain text with a single timestamp reference (e.g., "bridge at [01:00.00]") gets misclassified; most lines silently dropped | **Require 2+ matching timestamp lines** to classify as LRC. Single-match plain text renders as `<pre>` block instead |

## Product Decisions (carried from v5, unchanged)

- **Lyrics button visible on**: Tracks of type `song` and `lyrics-loop` (both originate from a recording with a `contentHash`). **Hidden** on `transition` type.
- **Lyrics button always clickable**: When a qualifying track is loaded, the Lyrics button is always enabled. The panel shows: a loading spinner during fetch, an error message on failure, or an empty-state when lyrics are null.
- **Panel persists across track changes**: If the lyrics panel is open and a new song starts, the panel **stays open** and automatically swaps to the new track's lyrics. If the new track's lyrics are cached, the swap is instant.
- **Panel auto-collapses on stop**: When the user closes the player (X button) or the track is cleared, `showLyrics` resets to `false`.
- **Synchronized auto-scroll is out of scope**: No line highlighting or auto-scroll based on playback `currentTime`. Static display only.
- **Animated expand/collapse**: CSS `max-height` transition for smooth upward growth. The lyrics panel appears above the existing seek bar + track info + controls row, within the same `fixed bottom-0` container.
- **Module-scoped cache reuse**: v3's `useSongLyrics` hook has a module-scoped `Map<contentHash, Result>`. Reused with v6 LRU eviction cap (see Step 8).
- **LRC rendering**: Same as v3/v5 — `parseLRC()` + `isValidLRC()`. Timestamps side-by-side on desktop (`md:`+), stacked vertically on mobile (`< md`). Plain `lyricsRaw` and `lyricsLines` rendered as `<pre>` blocks.
- **No `ChevronDown` removal yet**: `ChevronDown` is still imported in `SongList.tsx` for use elsewhere if needed; only the chevron *button* is removed.
- **Global layout padding is out of scope**: Only `SongsetEditor` has `pb-24` bottom padding; `BrowseSheet` has `pb-28 sm:pb-20`. v6 mitigates the hidden-content issue by auto-collapsing on route change (Step 9), but a future global layout refactor should introduce a `--player-height` CSS custom property.

## Context

### Relevant Files

| File | Role |
|------|------|
| `delivery/webapp/src/components/audio/AudioPlayerBar.tsx` | The Player bar (fixed bottom-0). Will gain the Lyrics toggle button + the lyrics panel + keyboard shortcut listener + Error Boundary wrapper + route-change collapse + modal-aware shortcut. Currently: 236 lines. |
| `delivery/webapp/src/components/audio/GlobalAudioPlayer.tsx` | Wraps `AudioPlayerProvider` + renders `<AudioPlayerBar />` (except on `/play/controller`). No changes needed. |
| `delivery/webapp/src/contexts/AudioPlayerContext.tsx` | Defines `AudioTrack` interface. Will gain `recordingContentHash?: string`. |
| `delivery/webapp/src/hooks/useAudioPlayer.ts` | Exposes `playSong`, `playTransition`, `playLyricsLoop`. `PlaySongOptions` and `PlayLyricsLoopOptions` will gain an optional `recordingContentHash` field. API-completeness only. |
| `delivery/webapp/src/hooks/useSongLyrics.ts` | v3 hook: fetches `GET /api/lyrics/{contentHash}`, caches in module-scoped `Map`. **v6 change**: LRU eviction cap at 50 entries (Step 8). |
| `delivery/webapp/src/app/api/lyrics/[recordingContentHash]/route.ts` | v3 API endpoint. **No changes.** |
| `delivery/webapp/src/lib/render/lrc-parser.ts` | `parseLRC()`, `isValidLRC()`, `LRCLine` interface. **v6 change**: `isValidLRC()` threshold raised to 2+ matching lines (Step 10). |
| `delivery/webapp/src/lib/render/lyrics-display.ts` | Shared `formatTimestamp()` utility (extracted from SongList). **New file** (Step 3). |
| `delivery/webapp/src/components/audio/PlayerLyricsPanel.tsx` | Lyrics rendering component for the AudioPlayerBar. **New file** (Step 4). |
| `delivery/webapp/src/components/audio/LyricsErrorBoundary.tsx` | Error Boundary wrapping `PlayerLyricsPanel`. **New file** (Step 11). |
| `delivery/webapp/src/components/songset/SongList.tsx` | Song row rendering. v3 added: `LyricsPanel` component, `expandedItemId` accordion state, `handleDragStart` auto-collapse, chevron button in `SortableSongItem`, `handleToggleExpand`. v5/v6 removes all of these. The `formatTimestamp()` local function at line 91 is moved to the new `PlayerLyricsPanel` component (or a shared util). |
| `delivery/webapp/src/components/songset/SongsetEditor.tsx` | Renders `<SongList>` and has `pb-24` bottom padding (line 396). No direct changes. |
| `delivery/webapp/src/components/songset/BrowseSheet.tsx` | Calls `play()` at lines 270 + 300. Has `recording.contentHash` available. Needs to pass it. |
| `delivery/webapp/src/components/search/SemanticSearch.tsx` | Calls `play()` at lines 213 + 243. Has `recording.contentHash` available. Needs to pass it. |
| `delivery/webapp/src/components/transition/TransitionSheet.tsx` | Calls `play()` at line 63 with `type: "transition"`. No `recordingContentHash` passed. **No changes.** |

### Songs Already Carry recording.contentHash

All song-starting call sites (`SongList`, `BrowseSheet`, `SemanticSearch`) already have `item.recording.contentHash` or `recording.contentHash` available in scope. They just don't currently pass it to `play()`.

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
- **No context logic changes**: The `AudioPlayerProvider` doesn't need to know about this field.

### Step 2: Propagate `recordingContentHash` from Call Sites

**Modify**: `delivery/webapp/src/hooks/useAudioPlayer.ts`

Add `recordingContentHash?: string` to two option interfaces and forward to `AudioTrack`:

1. `PlaySongOptions`: add `recordingContentHash?: string`. In `playSong`, set `recordingContentHash: options.recordingContentHash` on the track.
2. `PlayLyricsLoopOptions`: add `recordingContentHash?: string`. In `playLyricsLoop`, set `recordingContentHash: options.recordingContentHash` on the track.
3. `PlayTransitionOptions`: **no change** — transitions don't show lyrics.

> **Note:** This step is **API-completeness only**. All current call sites call `play()` directly from `useAudioPlayerContext`, not through these wrapper functions.

**Modify**: `delivery/webapp/src/components/songset/SongList.tsx`

In `handlePlaySong` (lines 415 + 443), add `recordingContentHash: recording.contentHash` to both `play()` calls.

**Modify**: `delivery/webapp/src/components/songset/BrowseSheet.tsx`

In `handlePlaySong` (lines 270 + 300), add `recordingContentHash: recording.contentHash` to both `play()` calls.

**Modify**: `delivery/webapp/src/components/search/SemanticSearch.tsx`

In `handlePlaySong` (lines 213 + 243), add `recordingContentHash: recording.contentHash` to both `play()` calls.

**No changes to**: `TransitionSheet.tsx`.

### Step 3: Extract `formatTimestamp` to a Shared Util

**New file**: `delivery/webapp/src/lib/render/lyrics-display.ts`

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

> **Note**: Remove `useId` from the import list if not used (v5 spec included it but the component description doesn't use it). ESLint will flag this.

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
- Container: `max-h-[40dvh] md:max-h-[400px] overflow-y-auto px-3 lg:px-4 py-2` (**v6: `dvh` instead of `vh`**)
- **`overscroll-y-contain`** on the scroll container to prevent body scroll chaining on mobile.
- `role="region"` + `aria-label="Lyrics for {track title}"`
- **No `scrollIntoView`**: The panel is already visible when toggled.

### Step 5: Lyrics Toggle Button + Expandable Panel + Keyboard Shortcut in AudioPlayerBar

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

When the track changes or is cleared, and the new track has no `recordingContentHash`, the panel collapses.

**Note**: We intentionally do *not* collapse when switching between two songs. If `showLyrics` is `true` and the user starts a new song, the panel stays open and lyrics swap instantly (via cache) or show a brief loading spinner.

#### 5c. Keyboard Shortcut ('L') — v6 Refined

**v6 changes from v5**:
1. **Modal-aware**: Skip 'L' when a `[role=dialog]` or `[data-slot=sheet]` is present in the DOM.
2. **No-op `preventDefault`**: Only call `e.preventDefault()` when a toggle actually happens.
3. **Stable listener**: Use `useRef` for `currentTrack` to avoid re-attaching the listener on every track change.

```typescript
// Ref to track currentTrack without re-attaching listener
const currentTrackRef = useRef(currentTrack);
useEffect(() => {
  currentTrackRef.current = currentTrack;
}, [currentTrack]);

useEffect(() => {
  const handleKeyDown = (e: KeyboardEvent) => {
    const track = currentTrackRef.current;
    if (!track) return;

    // v6: Skip when focus is inside an input/textarea/contenteditable
    const target = e.target as HTMLElement;
    if (
      target.tagName === "INPUT" ||
      target.tagName === "TEXTAREA" ||
      target.isContentEditable
    ) {
      return;
    }

    // v6: Skip when a modal/sheet/dialog is open
    if (
      document.querySelector('[role="dialog"]') ||
      document.querySelector('[data-slot="sheet"]')
    ) {
      return;
    }

    if (e.key === "l" || e.key === "L") {
      // v6: Only toggle if the current track supports lyrics
      if (track.recordingContentHash) {
        e.preventDefault(); // v6: Only preventDefault when we actually act
        setShowLyrics((prev) => !prev);
      }
    }
  };

  document.addEventListener("keydown", handleKeyDown);
  return () => document.removeEventListener("keydown", handleKeyDown);
}, []); // v6: Empty dependency array — listener attaches once
```

- `'L'` toggles lyrics **only** when a track with `recordingContentHash` is loaded.
- Ignored when focus is inside any `<input>`, `<textarea>`, or `contenteditable` element.
- **v6**: Ignored when any modal/sheet/dialog is open in the DOM.
- **v6**: `e.preventDefault()` only fires when a toggle actually happens.

#### 5d. Lyrics Toggle Button

Add an `AlignLeft` icon button (from `lucide-react`) in the player bar's control row. Recommended placement: between the track info area and the main playback controls (or adjacent to the loop toggle). The button:

- **Visible when**: `currentTrack.recordingContentHash` is defined (song or lyrics-loop type). Hidden entirely for transition tracks.
- **Active state**: `variant={showLyrics ? "secondary" : "ghost"}`.
- **`aria-expanded={showLyrics}`**, `aria-controls="player-lyrics-panel"`, `aria-label={showLyrics ? "Hide lyrics" : "Show lyrics"}`
- **`data-testid="lyrics-toggle-button"`**
- **`title="Lyrics (L)"`**

Import: `AlignLeft` from `lucide-react`. `FileText` is an acceptable alternative.

#### 5e. Render the Lyrics Panel — v6 with Delayed Unmount

**v6 change from v5**: The animated wrapper always renders, but `PlayerLyricsPanel` stays mounted during the collapse transition and is removed on `onTransitionEnd`. This fixes the v5 bug where the panel content vanished instantly while the wrapper animated for 300ms.

```typescript
// v6: Track whether the panel content should be mounted
const [isLyricsMounted, setIsLyricsMounted] = useState(false);

// Mount content when expanding; keep mounted during collapse transition
useEffect(() => {
  if (showLyrics && currentTrack?.recordingContentHash) {
    setIsLyricsMounted(true);
  }
}, [showLyrics, currentTrack?.recordingContentHash]);

// Unmount content after collapse transition completes
const handleTransitionEnd = () => {
  if (!showLyrics) {
    setIsLyricsMounted(false);
  }
};
```

```jsx
{/* Animated lyrics panel — always in DOM for transition */}
<div
  onTransitionEnd={handleTransitionEnd}
  className={cn(
    "overflow-hidden transition-[max-height,opacity] duration-300 ease-in-out border-t bg-background/95 backdrop-blur-sm",
    showLyrics && currentTrack?.recordingContentHash
      ? "max-h-[40dvh] md:max-h-[400px] opacity-100"
      : "max-h-0 opacity-0 border-t-0"
  )}
>
  {isLyricsMounted && currentTrack?.recordingContentHash && (
    <div
      id="player-lyrics-panel"
      role="region"
      aria-label={`Lyrics for ${currentTrack.title}`}
      className="overflow-y-auto overscroll-y-contain h-full"
    >
      <LyricsErrorBoundary fallback={<LyricsErrorFallback />}>
        <PlayerLyricsPanel recordingContentHash={currentTrack.recordingContentHash} />
      </LyricsErrorBoundary>
    </div>
  )}
</div>
```

**v6 changes from v5**:
1. **`dvh` instead of `vh`**: `max-h-[40dvh]` prevents mobile Safari reflow.
2. **`transition-[max-height,opacity]`** instead of `transition-all`: avoids animating unrelated CSS properties (border-color, background).
3. **Delayed unmount**: `isLyricsMounted` state + `onTransitionEnd` handler keeps content visible during collapse.
4. **Error Boundary**: `LyricsErrorBoundary` wraps `PlayerLyricsPanel` (Step 11).

#### 5f. Auto-collapse on Route Change — v6 New

**v6 new effect**: When the user navigates to a different page, collapse the lyrics panel to prevent content occlusion on pages without bottom padding.

```typescript
import { usePathname } from "next/navigation";

const pathname = usePathname();

useEffect(() => {
  setShowLyrics(false);
}, [pathname]);
```

This fires on every route change. The player itself persists (it's global), but the lyrics panel collapses. Users can re-expand lyrics on the new page if desired.

#### 5g. Auto-collapse on Modal/Sheet Open — v6 New

**v6 new effect**: When a modal/sheet/dialog mounts in the DOM, collapse the lyrics panel to prevent z-index overlap (player is `z-[60]`, modals are `z-50`).

Two implementation approaches (choose one during implementation):

**Approach A: MutationObserver** (recommended — catches all modals/sheets without requiring each modal to opt in):

```typescript
useEffect(() => {
  const observer = new MutationObserver(() => {
    const modalOpen =
      document.querySelector('[role="dialog"]') ||
      document.querySelector('[data-slot="sheet"]');
    if (modalOpen) {
      setShowLyrics(false);
    }
  });

  observer.observe(document.body, { childList: true, subtree: true });
  return () => observer.disconnect();
}, []);
```

**Approach B: Event-based** (requires each modal/sheet to dispatch a custom event on open):

```typescript
useEffect(() => {
  const handleModalOpen = () => setShowLyrics(false);
  document.addEventListener("modal-open", handleModalOpen);
  return () => document.removeEventListener("modal-open", handleModalOpen);
}, []);
```

Approach A is preferred because it requires no changes to existing modal/sheet components.

#### 5h. Import the New Components

Add to the top of `AudioPlayerBar.tsx`:

```typescript
import { useState, useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
import { AlignLeft } from "lucide-react";
import { PlayerLyricsPanel } from "./PlayerLyricsPanel";
import { LyricsErrorBoundary } from "./LyricsErrorBoundary";
```

### Step 6: Remove v3 Inline Lyrics from SongList

**Modify**: `delivery/webapp/src/components/songset/SongList.tsx`

Remove the following v3 additions (same as v5):

1. **`LyricsPanel` component** (lines 98-171): Delete entirely.
2. **`formatTimestamp` function** (lines 91-96): Delete. Moved to `delivery/webapp/src/lib/render/lyrics-display.ts`.
3. **`expandedItemId` state** (line 381): Delete.
4. **`handleDragStart` callback** (lines 492-494): Delete.
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
- On mobile: The Add Songs FAB (`fixed bottom-20 right-4`, line 418) may be partially overlapped by the expanded lyrics panel. This is acceptable — the user is reading lyrics, not adding songs.
- **v6**: On non-songset pages, the lyrics panel auto-collapses on route change (Step 5f), so hidden content is no longer an issue.

> **Out of scope (deferred):** A global `--player-height` CSS custom property that dynamically reflects the player's current height. Tackled in a future layout refactor.

### Step 8: LRU Eviction in `useSongLyrics` Cache — v6 New

**Modify**: `delivery/webapp/src/hooks/useSongLyrics.ts`

Add LRU eviction to the module-scoped `lyricsCache` Map. When the cache exceeds 50 entries, evict the oldest entry before inserting a new one. JavaScript `Map` preserves insertion order, so the first key is the oldest.

```typescript
const MAX_CACHE_SIZE = 50;

// Inside the fetch success handler, before lyricsCache.set():
if (lyricsCache.size >= MAX_CACHE_SIZE) {
  const oldestKey = lyricsCache.keys().next().value;
  if (oldestKey !== undefined) {
    lyricsCache.delete(oldestKey);
  }
}
lyricsCache.set(recordingContentHash, {
  lrcContent: data.lrcContent,
  lines: data.lines,
});
```

**Behavior**:
- Cache grows up to 50 entries.
- When the 51st unique song's lyrics are fetched, the oldest entry is evicted.
- If the user revisits an evicted song, the lyrics are re-fetched (brief spinner).
- The `clearLyricsCache()` export remains available for manual cache clearing.

### Step 9: Route-Change Auto-collapse — v6 New

Already documented in Step 5f. The `usePathname()` effect collapses `showLyrics` on every route change. No additional code beyond what's in Step 5f.

### Step 10: `isValidLRC()` Threshold Update — v6 New

**Modify**: `delivery/webapp/src/lib/render/lrc-parser.ts`

Raise the threshold for `isValidLRC()` from "any single line matches" to "2+ lines match the timestamp pattern". This prevents plain-text lyrics with a single timestamp reference (e.g., "bridge at [01:00.00]") from being misclassified as LRC and having most lines silently dropped.

```typescript
export function isValidLRC(lrcContent: string): boolean {
  const pattern = /\[\d{2}:\d{2}\.\d{2,3}\]/;
  let matchCount = 0;
  for (const line of lrcContent.split('\n')) {
    if (pattern.test(line)) {
      matchCount++;
      if (matchCount >= 2) {
        return true;
      }
    }
  }
  return false;
}
```

**Behavior**:
- LRC files with 2+ timestamped lines → classified as LRC (same as before).
- Plain text with 0 or 1 timestamp references → classified as plain text, rendered as `<pre>` block.
- Edge case: An LRC file with only 1 timestamped line (extremely rare) will now render as plain text. This is acceptable — a single-line LRC provides no synchronization value.

### Step 11: `LyricsErrorBoundary` Component — v6 New

**New file**: `delivery/webapp/src/components/audio/LyricsErrorBoundary.tsx`

A React Error Boundary that wraps `PlayerLyricsPanel`. If the panel throws during render (malformed LRC, unexpected API response shape, etc.), the boundary catches the error and renders a fallback UI instead of crashing the entire `AudioPlayerBar`.

```typescript
"use client";

import { Component, type ReactNode } from "react";

interface Props {
  children: ReactNode;
  fallback: ReactNode;
}

interface State {
  hasError: boolean;
}

export class LyricsErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(): State {
    return { hasError: true };
  }

  componentDidCatch(error: Error, errorInfo: React.ErrorInfo) {
    console.error("PlayerLyricsPanel render error:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return this.props.fallback;
    }
    return this.props.children;
  }
}
```

**Fallback UI** (rendered in `AudioPlayerBar.tsx`):

```jsx
function LyricsErrorFallback() {
  return (
    <div className="px-3 lg:px-4 py-2">
      <p className="text-sm text-muted-foreground">Lyrics unavailable</p>
    </div>
  );
}
```

**Reset behavior**: The error boundary resets when `PlayerLyricsPanel` unmounts and remounts (e.g., when the user collapses and re-expands the panel, or when the track changes). This allows the user to retry by collapsing and re-expanding.

**Important**: The boundary must be placed *inside* the `isLyricsMounted` conditional so it remounts when the panel is collapsed and re-expanded:

```jsx
{isLyricsMounted && currentTrack?.recordingContentHash && (
  <div id="player-lyrics-panel" ...>
    <LyricsErrorBoundary fallback={<LyricsErrorFallback />}>
      <PlayerLyricsPanel recordingContentHash={currentTrack.recordingContentHash} />
    </LyricsErrorBoundary>
  </div>
)}
```

---

## Files to Create

| File | Purpose |
|------|---------|
| `delivery/webapp/src/components/audio/PlayerLyricsPanel.tsx` | Lyrics rendering component for the AudioPlayerBar. Uses `useSongLyrics` + LRC parser. Adds `overscroll-y-contain`. Uses `dvh` units. |
| `delivery/webapp/src/components/audio/LyricsErrorBoundary.tsx` | Error Boundary wrapping `PlayerLyricsPanel`. Prevents panel render errors from crashing the global player. |
| `delivery/webapp/src/lib/render/lyrics-display.ts` | Shared `formatTimestamp()` utility (extracted from SongList). |

## Files to Modify

| File | Changes |
|------|---------|
| `delivery/webapp/src/contexts/AudioPlayerContext.tsx` | Add `recordingContentHash?: string` to `AudioTrack` interface. |
| `delivery/webapp/src/hooks/useAudioPlayer.ts` | Add `recordingContentHash?: string` to `PlaySongOptions` + `PlayLyricsLoopOptions`; forward to `AudioTrack`. (API-completeness only.) |
| `delivery/webapp/src/hooks/useSongLyrics.ts` | **v6**: Add LRU eviction cap at 50 entries in module-scoped `lyricsCache`. |
| `delivery/webapp/src/lib/render/lrc-parser.ts` | **v6**: Raise `isValidLRC()` threshold to require 2+ matching timestamp lines. |
| `delivery/webapp/src/components/audio/AudioPlayerBar.tsx` | Add `showLyrics` state + auto-collapse effect + 'L' keyboard shortcut (modal-aware, stable listener, no-op preventDefault) + Lyrics toggle button (`AlignLeft`) + animated lyrics panel wrapper (delayed unmount via `onTransitionEnd`, `dvh` units, `transition-[max-height,opacity]`) + route-change auto-collapse + modal-open auto-collapse + import `PlayerLyricsPanel` + `LyricsErrorBoundary` wrapper. |
| `delivery/webapp/src/components/songset/SongList.tsx` | Remove all v3 lyrics code: `LyricsPanel` component, `formatTimestamp`, `expandedItemId`, `handleDragStart`, `handleToggleExpand`, chevron button, `SortableSongItemProps` lyric fields, lyrics imports. Pass `recordingContentHash` in both `play()` calls. |
| `delivery/webapp/src/components/songset/BrowseSheet.tsx` | Pass `recordingContentHash: recording.contentHash` to both `play()` calls. |
| `delivery/webapp/src/components/search/SemanticSearch.tsx` | Pass `recordingContentHash: recording.contentHash` to both `play()` calls. |

## Files to Delete

| File | Reason |
|------|--------|
| `delivery/webapp/src/test/components/songset/SongList-lyrics.test.tsx` | Tests the v3 inline chevron/LyricsPanel behavior. Entirely superseded by new AudioPlayerBar lyrics tests. |

## Files NOT Modified (Reused as-is from v3/v4/v5)

| File | Reason |
|------|--------|
| `delivery/webapp/src/app/api/lyrics/[recordingContentHash]/route.ts` | API endpoint reused unchanged. |
| `delivery/webapp/src/test/api/lyrics/recordingContentHash.test.ts` | API tests still valid. |
| `delivery/webapp/src/components/transition/TransitionSheet.tsx` | No `recordingContentHash` needed for transitions. |
| `delivery/webapp/src/components/audio/GlobalAudioPlayer.tsx` | Rendering wrapper unchanged. |

---

## Imports Summary

### AudioPlayerBar.tsx (new imports)
```typescript
import { useState, useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
import { AlignLeft } from "lucide-react";
import { PlayerLyricsPanel } from "./PlayerLyricsPanel";
import { LyricsErrorBoundary } from "./LyricsErrorBoundary";
```

### PlayerLyricsPanel.tsx (all imports)
```typescript
import { useSongLyrics } from "@/hooks/useSongLyrics";
import { parseLRC, isValidLRC, type LRCLine } from "@/lib/render/lrc-parser";
import { formatTimestamp } from "@/lib/render/lyrics-display";
import { Loader2 } from "lucide-react";
```

### LyricsErrorBoundary.tsx (all imports)
```typescript
import { Component, type ReactNode } from "react";
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

1. **Transition track plays while lyrics panel is open**: The panel auto-collapses via the `useEffect` (transition has no `recordingContentHash`). When the user switches back to a song, they must re-click the Lyrics button.
2. **User stops playback (X button)**: `currentTrack` becomes `null`. `showLyrics` resets to `false` via effect. The entire player (including lyrics) disappears.
3. **Lyrics button clicked on a song with no lyrics**: Button is always clickable. The panel expands, shows a brief loading state (or instant if previously cached), then displays "No lyrics available for this recording."
4. **Rapid track switching while panel is open**: Each track change fires `useSongLyrics` with a new `recordingContentHash`. The hook's `AbortController` aborts the previous fetch. Cached songs show instantly. The panel remains open throughout.
5. **Lyrics loaded, then track changes to a cached song**: The panel stays open. `useSongLyrics` returns the cached result synchronously. No spinner; lyrics swap instantly.
6. **Lyrics loaded, then track changes to an uncached song**: The panel stays open. A loading spinner appears inside the panel until the new lyrics are fetched.
7. **Mobile: lyrics panel is very tall**: `max-h-[40dvh]` caps the height (**v6**: `dvh` instead of `vh` to prevent Safari reflow). The panel scrolls internally. The seek bar and controls remain accessible below. `overscroll-y-contain` prevents accidental body scroll.
8. **Lyrics panel overlaps the Add Songs FAB on mobile**: Acceptable. The FAB is behind the player (`z-[60]` > FAB's z-index). User collapses lyrics to access the FAB.
9. **User navigates to `/play/controller`**: `GlobalAudioPlayer` already hides `AudioPlayerBar` on controller pages. No lyrics shown. No issue.
10. **User navigates away from the songset page**: **v6**: The lyrics panel auto-collapses on route change (Step 5f). The player persists, but lyrics are hidden. Users can re-expand lyrics on the new page if desired. This prevents content occlusion on pages without bottom padding.
11. **`recordingContentHash` is present but the API returns an error**: Panel shows "Lyrics unavailable" text. The user can collapse and re-expand to retry (errors are not cached; remounting `PlayerLyricsPanel` re-triggers the fetch).
12. **Modal/sheet opened while lyrics panel is expanded**: **v6**: The lyrics panel auto-collapses via the `MutationObserver` effect (Step 5g). No z-index conflict occurs. When the modal closes, lyrics stay collapsed (user re-opens if desired).
13. **'L' shortcut conflict with focused input**: The keyboard shortcut explicitly checks `target.tagName` and `isContentEditable`, so typing 'L' inside a search box or text area will not toggle lyrics.
14. **'L' shortcut while a modal is open**: **v6**: The keyboard shortcut checks for `[role=dialog]` or `[data-slot=sheet]` in the DOM and skips the toggle if either is present.
15. **`PlayerLyricsPanel` throws during render**: **v6**: The `LyricsErrorBoundary` catches the error and renders the "Lyrics unavailable" fallback. The `AudioPlayerBar` continues to function normally. The user can collapse and re-expand to retry (remounting resets the boundary).
16. **Collapse animation with delayed unmount**: **v6**: When collapsing, `PlayerLyricsPanel` stays mounted during the 300ms transition. Content remains visible as the panel shrinks. On `onTransitionEnd`, `isLyricsMounted` is set to `false` and the panel content unmounts cleanly.
17. **Cache eviction during long session**: **v6**: When the 51st unique song's lyrics are fetched, the oldest cache entry is evicted. If the user revisits an evicted song, the lyrics are re-fetched (brief spinner). The cache never exceeds 50 entries.
18. **Plain-text lyrics with a single timestamp reference**: **v6**: `isValidLRC()` now requires 2+ matching timestamp lines. Plain text with 0 or 1 timestamp references is classified as plain text and rendered as a `<pre>` block. No lines are silently dropped.

---

## Testing

### Unit Tests

- **`delivery/webapp/src/test/hooks/useAudioPlayer.test.tsx`**: Update existing tests to include `recordingContentHash` in `playSong` and `playLyricsLoop` calls. Verify it's forwarded to `currentTrack`.

- **`delivery/webapp/src/test/hooks/useSongLyrics.test.ts`** (new or extend):
  - **v6**: Verify LRU eviction — insert 51 entries, verify the first entry is evicted and the cache size stays at 50.
  - **v6**: Verify evicted entry is re-fetched on next access.

- **`delivery/webapp/src/test/lib/lrc-parser.test.ts`** (extend):
  - **v6**: `isValidLRC()` returns `true` for content with 2+ timestamped lines.
  - **v6**: `isValidLRC()` returns `false` for content with 0 or 1 timestamped lines.
  - **v6**: `isValidLRC()` returns `false` for plain text with a single `[01:00.00]` reference.

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
  - (i) Press 'L' key toggles lyrics when player is active and focus is not inside an input.
  - (j) Press 'L' key does nothing when focus is inside an `<input>` or `<textarea>`.
  - (k) Press 'L' key does nothing when no track is loaded.
  - **v6 (l)**: Press 'L' key does nothing when a modal/sheet is open (mock `[role=dialog]` in DOM).
  - **v6 (m)**: `preventDefault` is NOT called when 'L' is pressed and no toggle happens (e.g., transition track).
  - **v6 (n)**: Keyboard listener does not re-attach on track change (verify `addEventListener` is called once).
  - **v6 (o)**: Panel auto-collapses on route change (mock `usePathname` to return a new value).
  - **v6 (p)**: Panel auto-collapses when a modal mounts (mock `MutationObserver` or `[role=dialog]` insertion).
  - **v6 (q)**: Collapse animation keeps content mounted during transition (verify `PlayerLyricsPanel` is still in DOM after `showLyrics` is set to `false`, before `onTransitionEnd`).
  - **v6 (r)**: After `onTransitionEnd` on collapse, `PlayerLyricsPanel` is unmounted.
  - **v6 (s)**: Panel uses `dvh` units (verify class contains `max-h-[40dvh]`).

- **`delivery/webapp/src/test/components/audio/PlayerLyricsPanel.test.tsx`** (new):
  - (a) `loading: true` → renders spinner + "Loading lyrics…"
  - (b) `error` → renders "Lyrics unavailable"
  - (c) `lrcContent` with valid LRC (2+ timestamped lines) → renders timestamped lines via `parseLRC()`
  - (d) `lines` non-empty → renders `<pre>` block with joined lines
  - (e) `lrcContent` plain text (fails `isValidLRC()`) → renders `<pre>` block
  - (f) Both null → renders "No lyrics available for this recording."
  - **v6 (g)**: `lrcContent` with only 1 timestamped line → renders as `<pre>` block (not parsed as LRC).

- **`delivery/webapp/src/test/components/audio/LyricsErrorBoundary.test.tsx`** (new, v6):
  - (a) When child throws during render, boundary renders fallback UI.
  - (b) When child renders normally, boundary renders children.
  - (c) Boundary resets when remounted (error state does not persist across unmount/remount).

### Accessibility Tests

- **`delivery/webapp/src/test/accessibility/accessibility.test.tsx`** (update):
  - Remove the 3 tests referencing "expand lyrics for amazing grace" chevron in SongList (lines 401-437): these test v3's inline chevron which is being deleted.
  - Add new tests under an `AudioPlayerBar` describe block:
    - Lyrics toggle button has `aria-expanded` attribute.
    - Lyrics toggle button toggles `aria-expanded` on click.
    - Expanded lyrics panel has `role="region"`.
    - Keyboard 'L' toggles `aria-expanded`.
    - **v6**: Keyboard 'L' does not toggle when a modal is open.

### Deleted Tests

- **`delivery/webapp/src/test/components/songset/SongList-lyrics.test.tsx`**: Delete entirely. All v3 inline lyrics behavior is removed.

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
10. Verify on mobile (`< 768px`): timestamps stack above lyric text for LRC content; panel has `max-h-[40dvh]` with internal scroll; scroll chaining does not propagate to body. **v6**: Verify panel height does not reflow when Safari address bar expands/collapses.
11. Verify smooth expand/collapse animation (CSS `transition-[max-height,opacity] duration-300`). **v6**: Verify content stays visible during collapse animation (no empty container).
12. Navigate between songs via Browse Sheet or Semantic Search → if the new song has `recordingContentHash`, lyrics button appears and panel (if open) swaps.
13. **v6**: Open lyrics panel, then navigate to a different page (e.g., home, settings) → verify panel auto-collapses. Player persists, lyrics hidden.
14. **v6**: Open lyrics panel, then open a modal/sheet (e.g., Browse Sheet) → verify panel auto-collapses. No z-index overlap.
15. Verify `pb-24` bottom padding on songset editor still clears the collapsed player bar when no lyrics are open.
16. Press 'L' while player is visible → verify lyrics toggle.
17. Focus a text input on the page, press 'L' → verify lyrics do NOT toggle.
18. **v6**: Open a modal, press 'L' → verify lyrics do NOT toggle.
19. **v6**: Play a song with malformed LRC data (e.g., truncated `[01:00.` without closing bracket) → verify panel shows "Lyrics unavailable" fallback, player bar continues to work.
20. **v6**: Play 51 unique songs in a long session → verify cache evicts the oldest entry and re-fetches on revisit.

---

## Out of Scope

- **Synchronized auto-scroll**: Highlighting the current lyric line based on `currentTime` and auto-scrolling to it. Future enhancement.
- **Click-to-seek on LRC timestamps**: Clicking a timestamp in the lyrics panel to seek to that time. Future enhancement.
- **Editing lyrics from this panel** (use the existing LRC editor / admin CLI).
- **Cross-session cache persistence** (module-scope Map is cleared on page reload; v3 behavior retained).
- **Server-side caching of the API response** (could be a future enhancement with `Cache-Control` headers).
- **Lyrics for transition previews** (transitions span two recordings; no meaningful single-song lyrics).
- **Global layout bottom padding / `--player-height` CSS variable**: A future refactor should make all page bottoms aware of the player's current height so content is never hidden. v6 mitigates this by auto-collapsing on route change, but the proper fix is deferred.
- **`prefers-reduced-motion` support**: The 300ms transition animates even for users with reduced-motion preferences. Future enhancement: add `motion-reduce:transition-none`.

---

## Migration from v5

1. **Backend**: No changes. The API endpoint and hook are reused (with v6 LRU eviction in the hook).
2. **Data model**: `AudioTrack` gains an optional field. Non-breaking.
3. **Call sites**: 3 files updated to pass `recordingContentHash` (additive, non-breaking).
4. **SongList cleanup**: Remove v3's inline lyrics code. Breaking for the referenced tests, which must be updated/deleted in the same change.
5. **Player enrichment**: Add the Lyrics button (`AlignLeft`) + panel + 'L' shortcut + `overscroll-y-contain` + Error Boundary + delayed unmount + route-change collapse + modal-open collapse. Purely additive — if `showLyrics` is never set to `true`, the player behaves identically to before.
6. **v6 production hardening**: LRU cache eviction, `isValidLRC()` threshold, `dvh` units, modal-aware shortcut, stable keyboard listener. All backward-compatible.

No database migration. No API contract change. No breaking change to any externally-visible API.

---

## v6 Production Hardening Summary

| # | Concern | Fix | Step |
|---|---------|-----|------|
| 1 | Modal/sheet overlap with lyrics panel | Auto-collapse on modal open via `MutationObserver` | 5g |
| 2 | Cross-route content hidden behind lyrics | Auto-collapse on `usePathname()` change | 5f |
| 3 | Collapse animation shows empty container | Delayed unmount via `onTransitionEnd` | 5e |
| 4 | Module cache grows unbounded | LRU cap at 50 entries | 8 |
| 5 | Panel render error crashes global player | `LyricsErrorBoundary` wrapping `PlayerLyricsPanel` | 11 |
| 6 | 'L' shortcut too broad | Modal-aware listener + no-op `preventDefault` + stable `useRef` | 5c |
| 7 | Mobile Safari `vh` reflow | Switch to `dvh` units | 5e |
| 8 | LRC misclassification on single-match plain text | `isValidLRC()` requires 2+ matching lines | 10 |
