# Webapp Controller: Overlay Player Model — Fix Tiny Lyrics Video / Broken Fullscreen on Android Chrome (v1)

## Summary

On Android Chrome, the worship playback controller (`/songsets/[id]/play/controller`) shows a tiny lyrics video: `PlaybackControls` is a layout **sibling below** the video, permanently consuming the bottom ~200–250px of a `fixed inset-0` flex column, so the 16:9 lyrics video letterboxes into the remaining strip. The "fullscreen" affordance cannot fix this:

1. The **auto-fullscreen effect** (`ControllerPlayer.tsx:848-877`) calls `document.documentElement.requestFullscreen()` on mount **without a user gesture** — Android Chrome rejects gesture-less fullscreen every time (silent no-op, caught and swallowed).
2. The **manual fullscreen button** (`handleReenterFullscreen`, `ControllerPlayer.tsx:746-759`) fullscreens the **document root** — `PlaybackControls` lives inside that document, so even when it succeeds, the video/controls split is unchanged.

**Fix: overlay player model.** Video always fills the viewport; top bar + bottom controls become translucent overlays that fade together after ~3s idle and reappear on tap. The broken auto-fullscreen effect is deleted; the manual button is repurposed to element-level fullscreen (`videoEl.requestFullscreen()`), which is the only way to also dismiss Android Chrome's URL bar. The LyricJumpList's always-visible 48px peek handle is removed and merged into the control bar as a button (user decision).

Not implemented yet — this spec is the agreed plan. All decisions were confirmed with the user in a grilling session (2026-09-23).

---

## User Decisions

| Topic | Decision |
|-------|----------|
| Q1. Which surface | Controller page (`/songsets/[id]/play/controller`) used standalone on the phone — no TV/projection involved. ProjectionPlayer (`fixed inset-0`, `object-cover`) is not affected. |
| Q2. Fix model | **Overlay model** — video fills viewport; controls float on top, auto-hide. NOT native `<video>` fullscreen (which would replace our custom controls with the browser's AVKong/AVPlayer-style UI). |
| Q3. Portrait fit | **Accept letterboxing** — `object-contain`, video as large as aspect ratio allows. Never crop a lyrics video (`object-cover` rejected: lyrics at edges would be cut off). |
| Q4. Platform scope | **All platforms unified** — one layout for desktop and mobile. No `md:` breakpoint split (two layouts = maintenance weight; desktop already auto-hides controls). |
| Q5. Fullscreen API | **Remove auto-fullscreen effect; keep manual button**, repurposed to `videoEl.requestFullscreen()` (hides Android Chrome URL bar) with document-root fallback retained for browsers without element fullscreen. Capability detection (`canDocumentFullscreen` / `canVideoFullscreen` via `useSyncExternalStore`) stays. |
| Q6. Acceptance | Visual size is the fix (controls gone when not in use, video at max area). URL bar removal is a bonus via the repurposed button, not the acceptance bar. |
| Q7. Lyrics sheet | **Merge peek handle into controls** — delete the always-visible 48px handle; lyrics open via a button in the control bar. |
| Q8. Hide delay | **~3s idle** (existing timer is 2000ms at `ControllerPlayer.tsx:480` — retune to 3000ms). |
| Q9. Wake lock | **Unchanged** — held for the whole session, never tied to controls visibility. |
| Q10. Audio-only boots | **Same layout** — overlay controls over black background; no special casing. |
| Q11. Verification | **Update tests + real-browser visual check** at phone viewport (AGENTS.md headless-Chrome recipe, `getBoundingClientRect` proof). |
| Q12. Lyrics access when hidden | **Two-step accepted** — tap to summon controls, then tap the lyrics button. |
| Q13. Sheet-open state | **Pin controls visible while the lyrics sheet is open**; on close, resume the idle timer. |
| Q14. Swipe gestures | **Drop swipe-up-to-open** (its handle target is gone); keep swipe-down-to-close and tap-to-close inside the open sheet. |
| Q15. Top bar | **Fades together with bottom controls** — one shared chrome-visibility state. Cast/presentation-active still pins chrome visible (existing behavior). |
| Q17. Open-sheet geometry | **Dock sheet above the control bar** — while open, the sheet's bottom edge sits above the pinned bar; no occlusion of the scroll area or LyricsFeedbackRow footer. |
| Q18. In-sheet close | **Add a floating close button** (chevron-down/X chip) at the sheet's top-right, always in-sheet. Backdrop tap, Escape, and the bar toggle remain additional close paths. |

---

## Current State

### Files involved

| File | Role |
|------|------|
| `delivery/webapp/src/components/play/ControllerPlayer.tsx` (1504 lines) | Controller surface. `fixed inset-0 z-[70] bg-black flex flex-col` root. Children: media div (`flex-1 min-h-0 relative`, `ControllerPlayer.tsx:1158-1181`), top bar (absolute, inside media div, `:1184-1400`), controls wrapper (`:1436-1475`, `pb-12`), `LyricJumpList` (`:1477-1486`), diagnostic Sheet. Owns `controlsVisible` state, 2000ms hide timer (`:472-516`), auto-fullscreen effect (`:848-877`), `handleReenterFullscreen` (`:746-759`), fullscreen capability detection (`:106-134`, `:200-214`). |
| `delivery/webapp/src/components/play/PlaybackControls.tsx` | Bottom control bar: mobile thin progress bar, time display, song-context row (`grid grid-cols-[1fr_auto_1fr]`, `text-lg` remaining span), 3-column main grid (prev/next + counter, size-14 play button, volume [hidden on mobile] + presentation chip). Pure presentational component — no own chrome-visibility state. |
| `delivery/webapp/src/components/play/LyricJumpList.tsx` (379 lines) | Fixed bottom sheet (`fixed bottom-0 left-0 right-0 z-50`), peek handle via `translate-y-[calc(100%-48px)]` (`:172-177`), handle bar `h-12` with chevron + label (`:185-238`), swipe open/close via `isSwipeEnabled = isIOS()` drag tracking (`:96-153`), backdrop (`:357-377`), `LyricsFeedbackRow` footer. Rendered by ControllerPlayer as a **fixed sibling** — it does not participate in the flex column layout. |
| `delivery/webapp/src/lib/i18n/messages/play.ts` | Keys: `lyrics.openAriaLabel` ("Open lyric jump list"), `lyrics.closeAriaLabel`, `lyrics.swipeDownToClose`, `lyrics.tapToClose`, `lyrics.lyrics` ("Lyrics"/"歌詞"), `controller.reenterFullscreen` ("Re-enter fullscreen"), `controller.enterFullscreen` ("Enter fullscreen"). Both `en` (`:38-47`) and `zh-Hant` (`:116-125`). |
| `delivery/webapp/src/test/components/play/ControllerPlayer.test.tsx` | Pins current structure: auto-fullscreen assertions (`:1931-1933` requests fullscreen on mount), iOS `webkitEnterFullscreen` fallback (`:1937-1956`), fullscreen-button visibility when no capability (`:1957-…`), mocks `requestFullscreen` on `document.documentElement` (`:114-117`). |
| `delivery/webapp/src/test/components/play/LyricJumpList.test.tsx` | Pins the peek handle / open-close behavior (swipe on iOS, click toggle). |
| `delivery/webapp/src/test/components/play/PlaybackControls.test.tsx` | Presentational tests — mostly unaffected (no layout-position assertions). |

### Current layout (portrait phone, no TV)

```
┌─────────────────┐
│ ← ⛶        📺  │ ← top bar (absolute over video area)
│  ┌───────────┐  │
│  │  video    │  │ ← flex-1 min-h-0, object-contain
│  │ (tiny     │  │   16:9 video letterboxed into
│  │ letterbox)│  │   (screen − controls) strip
│  └───────────┘  │
├─────────────────┤ ← flex column sibling boundary
│ ▁▁▁▁▁▁▁▁▁▁▁▁▁ │ ← PlaybackControls (opaque section)
│ 0:12      45:00 │    pb-12 = 48px dead padding
│ ♫ -3:21 (1/45)  │    size-14 play button + context row
│ ⏮ 1/12   (▶)  ⏭│    ≈ 200–250px total
└─────────────────┘
  ▁▁ lyrics ▁▁ ▲  ← LyricJumpList 48px fixed peek handle
```

### Existing mechanisms reused (not re-invented)

- **`controlsVisible` state + hide timer** (`ControllerPlayer.tsx:472-516`): auto-hide only while `isPlaying` and not presentation-active; `showControls()` re-summons and restarts the timer; timer restarted on play state change. Only the delay (2000ms → 3000ms) and the *layout semantics* change (fade-over-video instead of column sibling).
- **Chrome visibility classes**: top bar (`:1193-1195`), keyboard hint (`:1418-1420`), and controls wrapper (`:1439-1443`) already toggle `opacity-0`/`opacity-100` on `controlsVisible || isPresentationActive` — the overlay model reuses exactly this condition on all three.
- **Capability detection** (`:119-134`): `canDocumentFullscreenSnapshot` / `canVideoFullscreenSnapshot` (WebKit `webkitEnterFullscreen`) — kept as-is; only the *action* of the fullscreen button changes.
- **Wake lock** (`useWakeLock` in ProjectionPlayer; controller equivalent unchanged) — untouched per Q9.

---

## Implementation Plan

### Phase 1: ControllerPlayer — overlay layout

File: `delivery/webapp/src/components/play/ControllerPlayer.tsx`

#### 1a. Root stays `fixed inset-0`; media div becomes full-bleed

The root `div` keeps `fixed inset-0 z-[70] bg-black` but drops `flex flex-col` — stacking is now z-index/absolute, not flex children. The media container changes from:

```tsx
<div className="flex-1 min-h-0 relative">
```

to:

```tsx
<div className="absolute inset-0">
```

The `<video>` keeps `w-full h-full object-contain` (Q3: letterbox accepted, never crop) and its `playsInline`, mute-on-presentation, tap/double-click handlers unchanged. Audio-only boots (`isAudioOnly`) are unaffected — the `<audio>` is hidden and the background is black (Q10).

#### 1b. Controls wrapper becomes a bottom overlay

Change the controls wrapper from a flex-column sibling to an absolute overlay pinned above the lyrics-sheet handle area:

```tsx
<div
  ref={controlsRef}
  className={cn(
    "absolute bottom-0 left-0 right-0 z-[80] transition-opacity duration-300",
    chromeVisible || isPresentationActive
      ? "opacity-100"
      : "opacity-0 pointer-events-none"
  )}
  onMouseEnter={/* unchanged */}
  onMouseLeave={startHideTimer}
>
  <PlaybackControls … />
</div>
```

Key points:
- **`pb-12` is deleted** (48px dead padding was compensating for the old peek handle overlap; with the handle gone and the bar translucent, the bar sits at the true bottom edge).
- `PlaybackControls` already renders its own translucent gradient background (`bg-gradient-to-t from-black/80 via-black/50 to-transparent`) — as an overlay this is exactly right; no background change needed.
- z-order: media (z-auto) < controls (`z-[80]`) < pendingResume/media-failure overlays (`z-[85]`, existing). The LyricJumpList sheet (`z-50`, fixed) sits **below** the control bar, so the visible control bar covers the sheet's top edge — consistent with the pinned-open rule in Phase 3.

#### 1c. Shared chrome visibility — top bar fades with controls

One state (renamed `controlsVisible` → `chromeVisible` for accuracy) drives top bar, keyboard hint, and bottom controls. All three already key off `controlsVisible || isPresentationActive` — rename and keep. The `isPresentationActive` pin (chrome stays visible during Cast, `startHideTimer` early-returns) is preserved per Q15.

#### 1d. Delete the auto-fullscreen effect

Remove `ControllerPlayer.tsx:848-877` entirely (the `autoFullscreen` effect: gesture-less `documentElement.requestFullscreen()` + its cleanup/exit). Remove the now-unused `autoFullscreen = true` prop from `ControllerPlayerProps` and from any caller passing it (grep `autoFullscreen` — currently no caller overrides it; the prop exists only for tests).

Rationale: Android Chrome rejects gesture-less fullscreen unconditionally; the effect can never succeed there, and on desktop it auto-entered fullscreen without consent. The page is already `fixed inset-0` — nothing is gained.

#### 1e. Repurpose the manual fullscreen button to element fullscreen

`handleReenterFullscreen` (`:746-759`) becomes:

```tsx
const handleEnterFullscreen = useCallback(() => {
  const video = mediaRef.current;
  // Element fullscreen first: on Android Chrome this also dismisses the
  // URL bar and shows the video edge-to-edge with system UI hidden.
  if (video instanceof HTMLVideoElement && typeof video.requestFullscreen === "function") {
    video.requestFullscreen().catch(() => {});
    return;
  }
  if (typeof document.documentElement.requestFullscreen === "function") {
    document.documentElement.requestFullscreen().catch(() => {});
    return;
  }
  // iOS WKWebView (Chrome iOS etc.): native WebKit video fullscreen.
  try {
    (mediaRef.current as VideoElementWithIOSFullscreen | null)?.webkitEnterFullscreen?.();
  } catch {
    // Best-effort; capability detection hides this button when absent.
  }
}, []);
```

Notes:
- Runs from a button tap → user-gesture requirement satisfied.
- Element fullscreen of the video shows only the video (custom controls hidden while active — accepted, Q2: this is the bonus path, not the primary UX). Exiting (back gesture / browser UI) returns to the overlay player.
- The button stays hidden while `isFullscreen` (`:1209` gating) and when no capability exists — unchanged.
- The `fullscreenchange` listener (`:836-846`) syncing `isFullscreen` continues to work for both document- and element-fullscreen (element fullscreen fires `fullscreenchange` too).
- The `autoFullscreen` effect's exit-cleanup `showControlsRef` behavior (`:863-867`) is deleted with the effect; the main `fullscreenchange` listener at `:836-846` already re-shows controls via `setIsFullscreen` re-render + existing show logic.

#### 1f. Retune the hide timer to 3000ms

`ControllerPlayer.tsx:480`: `}, 2000);` → `}, 3000);` (Q8). Comment "Auto-hide controls in mirror mode" updated to describe the overlay model.

### Phase 2: LyricJumpList — remove peek handle, open from control bar

File: `delivery/webapp/src/components/play/LyricJumpList.tsx`

#### 2a. New controlled open state

`isOpen` becomes externally controlled (Q7/Q13 — ControllerPlayer must pin chrome while the sheet is open, so it owns the state):

```tsx
export interface LyricJumpListProps {
  …
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  /** True while playback is paused — disables auto-hide of the sheet. */
  className?: string;
}
```

- Delete internal `const [isOpen, setIsOpen] = useState(false)`; replace `setIsOpen(x)` with `onOpenChange(x)`.
- `explicitExpandedChapterIndex` reset logic in `handleToggle` moves into `onOpenChange(false)` handling or stays as a local wrapper around the prop callback.
- The backdrop (`:357-377`) still calls `onOpenChange(false)` + resets expansion.

#### 2b. Delete the peek handle bar and the swipe-open gesture

- Remove the `h-12` handle bar block (`:185-238`) — the sheet, when closed, renders nothing (no `translate-y-[calc(100%-48px)]` state; return `null` when `!isOpen`). In-sheet close affordance moves to a floating close chip (Phase 2c, Q18).
- Delete `handleTouchStart`/`handleTouchMove`/`handleTouchEnd` **open** paths and `isDragging`/`startY`/`currentY`/`lastToggleTimeRef`/`sheetRef` state that exists only for drag-to-open (Q14). Keep swipe-**down**-to-close? — **No**: with the handle gone, the swipe target inside the open sheet is the scrollable content area itself, where vertical swipe is scrolling, not closing. Closing is by the in-sheet close chip (Phase 2c, Q18), the backdrop tap, Escape, and the pinned control-bar toggle — matching the existing `lyrics.tapToClose` affordance. Delete all drag-tracking code and `isIOS()` swipe gating. (This supersedes the round-3 sketch "keep swipe-close": once the handle is gone there is no non-scrolling surface to host the gesture.)
- `contentInteractive` delay logic stays (350ms settle before row taps register).

#### 2c. Open-sheet geometry — docked above the control bar (Q17)

The pinned control bar (Q13, `z-[80]`, `bottom-0`) would otherwise cover the bottom ~200px of an open bottom-docked sheet — occluding the trailing lyric rows and the `LyricsFeedbackRow` footer (which is the **last child of the fixed sheet container, outside the scroll div**, so it can never scroll clear of the bar). While open, the sheet therefore docks **above** the bar:

```tsx
{/* Sheet container (open state) */}
<div
  className="fixed left-0 right-0 bottom-[var(--sow-controller-bar-height)] z-50
             bg-black/90 backdrop-blur-sm rounded-t-2xl"
  data-testid="lyric-jump-sheet"
>
  {/* Floating in-sheet close chip (Q18) */}
  <button
    type="button"
    onClick={() => onOpenChange(false)}
    className="absolute top-2 right-2 z-10 size-8 rounded-full bg-white/10
               text-white/80 hover:bg-white/20 flex items-center justify-center"
    aria-label={t("lyrics.closeAriaLabel")}
    data-testid="lyric-sheet-close"
  >
    <ChevronDown className="size-4" />
  </button>

  {/* Scrollable content (unchanged inner structure) */}
  <div ref={contentRef} className="relative max-h-[60vh] overflow-y-auto …">…</div>

  {/* LyricsFeedbackRow footer — now fully visible above the bar */}
</div>
```

- The bar height varies (song-context row, presentation chip). Measure it once per open: ControllerPlayer passes `--sow-controller-bar-height` (or a numeric `barHeightPx` prop) from `controlsRef.current.getBoundingClientRect().height`, updated while `isLyricsOpen`. Fallback `bottom-0` if the ref is not yet measured.
- **Height budget, not just offset (landscape!)**: the old `max-h-[60vh]` on the scroll div assumed the sheet could extend past the bar. Docked above a ~200px bar in landscape (e.g. 844×390 — the controller's *target* orientation via the projection landscape lock), 60vh ≈ 234px of scroll + the outside footer + close chip exceeds the ~190px available and pushes the sheet off the top of the viewport. The sheet container therefore gets a hard budget and an internal flex column, replacing the `max-h-[60vh]` cap:

  ```tsx
  <div
    className="fixed left-0 right-0 z-50 flex flex-col
               bottom-[var(--sow-controller-bar-height)]
               max-h-[calc(100dvh-var(--sow-controller-bar-height))]
               bg-black/90 backdrop-blur-sm rounded-t-2xl"
  >
    {/* close chip (absolute, out of flow) */}
    <div ref={contentRef} className="flex-1 min-h-0 overflow-y-auto …">…</div>
    {/* LyricsFeedbackRow footer — shrink-0, always visible */}
  </div>
  ```

  The scroll div shrinks (`flex-1 min-h-0`), the footer never does (`shrink-0`); `100dvh` (dynamic viewport height) avoids the mobile URL-bar `vh` inflation. In portrait the budget is generous; in landscape the scroll area compresses instead of the sheet overflowing.
- The closed sheet renders `null` (Phase 2b), so the variable only matters while open — and Q13 pins the bar visible for the whole open duration, so the height cannot change mid-open except via the presentation chip (already pinned-visible → static height).
- The backdrop (`z-40`, fixed) stays below sheet (`z-50`) and bar (`z-[80]`), so all three close paths remain live: backdrop tap, Escape, bar toggle, plus the new in-sheet chip (Q18).
- Rounded top corners return (`rounded-t-2xl`) since the sheet is now a floating panel, not an edge-docked strip.

### Phase 3: ControllerPlayer — lyrics toggle in the control bar

Files: `delivery/webapp/src/components/play/ControllerPlayer.tsx`, `PlaybackControls.tsx`, `play.ts` (i18n)

#### 3a. Lift sheet state into ControllerPlayer

```tsx
const [isLyricsOpen, setIsLyricsOpen] = useState(false);

// Q13: pin chrome while the lyrics sheet is open; resume idle timer on close.
useEffect(() => {
  if (isLyricsOpen) {
    if (hideTimeoutRef.current) clearTimeout(hideTimeoutRef.current);
    setChromeVisible(true);
  } else if (isPlaying && !isPresentationActive) {
    startHideTimer();
  }
}, [isLyricsOpen, isPlaying, isPresentationActive, startHideTimer]);
```

While open: `chromeVisible` pinned true (the `startHideTimer` early-return for presentation is joined by an `isLyricsOpen` early-return). On close: normal idle timer resumes.

#### 3b. Lyrics button in PlaybackControls

New props on `PlaybackControls`:

```tsx
isLyricsOpen?: boolean;
onToggleLyrics?: () => void;
```

Rendered in the right column of the main 3-column grid, **before** the volume controls (which are already `hidden md:flex`/`hidden md:block`):

```tsx
{onToggleLyrics && (
  <Button
    variant="ghost"
    size="icon"
    className={cn("size-10 text-white hover:bg-white/20", isLyricsOpen && "bg-white/20")}
    onClick={onToggleLyrics}
    aria-label={t("controls.lyrics")}
    aria-expanded={isLyricsOpen}
    data-testid="lyrics-toggle"
  >
    <ListMusic className="size-5" />
  </Button>
)}
```

- Visible on **all** screen sizes (on mobile the volume control is hidden, so this is the right column's only control — good reachability for the two-step access, Q12).
- `ListMusic` icon (lucide, already a dependency); active state highlighted while open.

#### 3c. i18n keys

`delivery/webapp/src/lib/i18n/messages/play.ts`:

| Key | en | zh-Hant |
|-----|----|----|
| `controls.lyrics` | `Lyrics` | `歌詞` |
| `lyrics.closeAriaLabel` | already exists (`Close lyric jump list`) — reused for the in-sheet close chip (Q18); review wording later if the chip becomes the primary close affordance | 已存在（`關閉歌詞清單`） |

Reuse of `lyrics.lyrics` is tempting but its namespace belongs to the sheet; a `controls.*` key keeps the button's aria-label with the other control labels. Delete now-dead keys: `lyrics.openAriaLabel`, `lyrics.swipeDownToClose` (verify each with grep before deleting — `lyrics.closeAriaLabel` and `lyrics.tapToClose` remain in use by the backdrop; `lyrics.lyrics` remains if the sheet header still needs a label).

### Phase 4: Tests

#### 4a. ControllerPlayer.test.tsx

- **Delete**: the auto-fullscreen-on-mount assertion (`:1931-1933` "requests fullscreen on mount") and its `autoFullscreen` prop usages — behavior removed.
- **Update**: fullscreen-button test now asserts `video.requestFullscreen` (element) is called on button click, with document-root fallback tested by deleting `HTMLVideoElement.prototype.requestFullscreen`, and the existing `webkitEnterFullscreen` iOS test (`:1937-1956`) kept.
- **New (behavior)**:
  - controls wrapper has `opacity-0 pointer-events-none` after the 3s timer fires while playing, and `opacity-100` after a simulated tap (the core overlay contract).
  - top bar and controls wrapper share visibility (same `chromeVisible` state flips both classes).
  - lyrics toggle click → `LyricJumpList` sheet rendered (`data-testid` from sheet content), controls pinned visible while open; backdrop click closes and restarts hide behavior.
  - open-sheet geometry (Q17): sheet container's bottom offset equals the measured control-bar height (`--sow-controller-bar-height` applied), i.e. the sheet's rect does not intersect the bar's rect while open. Height budget: scroll div is `flex-1 min-h-0` inside a `max-h-[calc(100dvh-…)]` container (assert classes — jsdom can't measure; the landscape browser pass is the real check).
  - in-sheet close chip (Q18): `data-testid="lyric-sheet-close"` click calls `onOpenChange(false)`.
  - layout contract: controls wrapper is `absolute bottom-0` overlay — assert class, and that the media container is `absolute inset-0` (fails on regression to flex-column).
- jsdom cannot measure real pixel geometry; the class-contract assertions above + the browser visual check (4c) cover it.

#### 4b. LyricJumpList.test.tsx

- Update to controlled `isOpen`/`onOpenChange` (render with `isOpen: true`).
- Delete swipe-open tests (drag-to-open removed); keep row-jump, expansion, feedback-row, auto-scroll behavior tests (all internal to the open sheet, unchanged).
- Delete peek-handle/toggle-click tests (handle removed).

#### 4c. Real-browser visual verification (the actual acceptance test)

Per AGENTS.md recipe: headless Chrome via `hub` (`--ignore-certificate-errors --remote-debugging-port=9222`), `browser.open({ app: { cdp_url } })` against the dev server (`--experimental-https`), sign in with `SOW_WEBAPP_TESTUSER_*` env creds, navigate to a rendered songset's controller page, then:

1. Set a phone viewport inside `tab.run` (`page.setViewport({ width: 390, height: 844 })`) — **and repeat the geometry proofs in landscape (`844×390`)**, the controller's target orientation (landscape lock on the projection side). Landscape is where the Q17 height budget actually bites: sheet rect + footer must stay inside the viewport above the bar.
2. `getBoundingClientRect()` proof:
   - video element: `width ≈ 390` and `height ≈ 844` (fills viewport; `object-contain` letterboxing shows as the video's painted aspect inside the full-bleed box).
   - controls wrapper: after 3s idle, computed `opacity` ≈ 0 and `pointer-events: none`.
   - after tap: controls `opacity` ≈ 1.
   - screenshot with controls visible + hidden for eyeball confirmation.
   - lyrics sheet open: `getBoundingClientRect()` on the sheet vs the control bar — no vertical intersection; the LyricsFeedbackRow footer's rect is fully above the bar's top edge (Q17 acceptance). Screenshot for eyeball confirmation.

### Phase 5: Out of scope (explicitly non-goals)

- ProjectionPlayer (receiver) — already full-bleed; untouched.
- Cast/presentation flows — logic unchanged; only the chrome layout around them.
- Audio-only boot special visuals (Q10).
- Wake lock behavior (Q9).
- Native video fullscreen *as the primary* playback UX (rejected in Q2 — we keep custom controls).

---

## Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Element fullscreen on Android shows the browser's own video UI with no custom controls while active | Accepted (Q5): it's the bonus path from an explicit button; exiting returns to the overlay player with all controls. Documented in the button's aria-label semantics (`controller.enterFullscreen` key reused). |
| `requestFullscreen` on `<video>` unsupported on some browsers | Document-root fallback (1e) covers desktop Chrome/Firefox/Safari 16.4+; WebKit fallback covers iOS. Capability detection already gates button visibility. |
| Lyrics sheet without a peek handle is undiscoverable | The toggle is in the control bar visible whenever controls are visible; two-step access (Q12) matches standard video-player conventions. |
| Controls overlaying the video obscures lyrics at the bottom edge | Translucent gradient (existing) + auto-hide after 3s; bottom of the 16:9 letterboxed video rarely intersects the bar in landscape; in portrait the letterbox bands put the video clear of the bar. |
| Removing swipe-open breaks iOS muscle memory | Deliberate user decision (Q14); the sheet is 2 taps away and the control bar button is a larger, more reliable target. |
