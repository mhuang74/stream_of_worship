# Webapp Controller: Overlay Player Model — v2 (Fullscreen-First, Tap-Toggle Chrome)

Supersedes `specs/webapp-controller-overlay-fullscreen-v1.md`. v1's grilling decisions (Q1–Q18) stand unless explicitly revised below. This v2 incorporates a second review round (2026-09-23) focused on: (a) uncluttered normal playback — only lyrics on screen until the user acts, and (b) landscape-maximized video as the primary worship mode.

## What changed vs v1

| # | v1 decision | v2 decision | Why |
|---|-------------|-------------|-----|
| D1 | Tap only *summons* chrome; dismissal waits for the 3s timer | **Tap toggles chrome** — tap when visible hides immediately (Q19) | Fastest path back to lyrics-only screen; standard video-player convention |
| D2 | Fullscreen button → `videoEl.requestFullscreen()` (element fullscreen) | **Document-root fullscreen** (`document.documentElement.requestFullscreen()`) (Q20) | Element fullscreen hands the video to the browser's native player UI — custom controls, lyrics sheet, and progress bar all vanish while active. Document fullscreen hides the URL bar/toolbars while our `fixed inset-0` overlay player (with all custom UI) keeps working. Strictly better fit for the overlay model. |
| D3 | Fullscreen only via manual button (auto effect deleted as impossible) | **Auto-request document fullscreen once on the user's first tap, touch devices only** (Q21) | Browsers mandate a gesture, so page-load fullscreen is impossible — but first-tap fullscreen makes fullscreen the *default mode* for phones. Desktop excluded (windowed context; intrusive). Failure (iOS Safari: no Fullscreen API) → never retried, never nagged; the in-browser overlay is the whole UX there. |
| D4 | Nothing about orientation | **Rotate hint in portrait** (Q22): dismissible, portrait+touch-only chip that fades with chrome | v1 accepted a small letterboxed portrait video silently; "maximized in landscape" needs an explicit nudge. Orientation *lock* rejected: Android Chrome requires fullscreen for `screen.orientation.lock`, iOS Safari doesn't support it at all. |
| D5 | Unstated | **Paused state pins chrome visible** (Q23) — auto-hide only runs while playing (existing behavior, now explicit). Manual tap-off still hides chrome even when paused (the pin governs *auto*-hide only). | Paused = user is deciding; controls stay. Explicit user action always wins. |
| D6 | No safe-area handling (none exists in codebase) | **`env(safe-area-inset-*)` padding** on top bar and bottom controls overlay | Notch/home-indicator phones: without this the back button and control bar collide with system areas in landscape. |

## Confirmed carried-over decisions (v1, unchanged)

Overlay model with `object-contain` (letterbox accepted, never crop — Q2/Q3); one unified layout all platforms (Q4); visual size is the acceptance bar (Q6); peek handle deleted, lyrics via control-bar button (Q7); 3000ms hide timer (Q8); wake lock unchanged (Q9); audio-only same layout (Q10); tests + real-browser visual check (Q11); two-step lyrics access (Q12); chrome pinned while lyrics sheet open (Q13); swipe gestures deleted, close via in-sheet chip / backdrop / Escape / bar toggle (Q14, Q18); top bar fades with bottom controls, presentation-active pins chrome (Q15); sheet docks above the bar with a `100dvh` height budget (Q17).

---

## Revised Implementation Plan

### Phase 1: ControllerPlayer — overlay layout (v1 §1a–1d, 1f unchanged)

v1 Phase 1a (media div `absolute inset-0`), 1b (controls wrapper `absolute bottom-0 … z-[80]`, delete `pb-12`), 1c (rename `controlsVisible` → `chromeVisible`, shared by top bar / keyboard hint / controls), 1d (delete auto-fullscreen effect + `autoFullscreen` prop), 1f (timer 2000ms → 3000ms) apply as written, with these modifications:

#### 1b′. Safe-area padding on chrome overlays (D6)

- Controls wrapper: add `pb-[env(safe-area-inset-bottom)]` (replaces the deleted `pb-12`'s only legitimate purpose — gesture-area clearance — with the correct mechanism). The runtime bar-height measurement for the sheet dock (Phase 2c, `--sow-controller-bar-height`) automatically includes it.
- Top bar: add `pt-[env(safe-area-inset-top)] pl-[env(safe-area-inset-left)] pr-[env(safe-area-inset-right)]` — in landscape the notch sits on a side edge and would otherwise overlap the back/cast buttons.

#### 1e′. Fullscreen button → document root (D2, replaces v1 §1e)

```tsx
const handleEnterFullscreen = useCallback(() => {
  // Document fullscreen: hides browser chrome (incl. Android URL bar) while
  // our fixed inset-0 overlay player — custom controls, lyrics sheet — keeps
  // rendering. Element fullscreen would hand the video to the browser's
  // native player UI and hide everything we built.
  if (typeof document.documentElement.requestFullscreen === "function") {
    document.documentElement.requestFullscreen().catch(() => {});
    return;
  }
  // iOS WKWebView / iPhone Safari: no Fullscreen API. Native WebKit video
  // fullscreen is the only escape from browser chrome there.
  try {
    (mediaRef.current as VideoElementWithIOSFullscreen | null)?.webkitEnterFullscreen?.();
  } catch {
    // Best-effort; capability detection hides this button when absent.
  }
}, []);
```

- `canDocumentFullscreen` gates the primary path; `canVideoFullscreen` (WebKit) gates the fallback. Button visibility logic unchanged otherwise (hidden while `isFullscreen`).
- The existing `fullscreenchange` listener (`:836-846`) works unchanged for document fullscreen.
- **Element fullscreen of the video is removed as a path entirely** (it was v1's primary). The only video-level fullscreen left is the iOS WebKit fallback, where no alternative exists.

#### 1g. Tap toggles chrome (D1/Q19)

Split the current single `handleInteraction` (root `onClick`/`onTouchStart`/`onMouseMove` → `showControls()`, `:1154-1156`) into two handlers:

```tsx
// Toggle: fires on click only (a tap synthesizes click on mobile; handling
// touchstart AND click would double-toggle and cancel out).
const handleTapToggle = useCallback(() => {
  setChromeVisible((v) => {
    const next = !v;
    if (next) startHideTimer();
    else if (hideTimeoutRef.current) clearTimeout(hideTimeoutRef.current);
    return next;
  });
}, [startHideTimer]);

// Activity wake: MOUSE hover only — re-summons, never toggles.
// MUST be pointer-type-gated: a tap dispatches compat mouse events
// (touchstart → touchend → mouseover → mousemove → mousedown → mouseup)
// BEFORE click, so a plain onMouseMove wake would set chrome visible and
// the immediately following click-toggle would read that state and hide
// it again — tap-to-reveal would become tap-to-hide on phones.
const handleActivity = useCallback((e: React.PointerEvent) => {
  if (e.pointerType !== "mouse") return;
  setChromeVisible(true);
  startHideTimer();
}, [startHideTimer]);
```

Wiring:
- Root div: `onClick={handleTapToggle}`, `onPointerMove={handleActivity}`; **`onTouchStart` and `onMouseMove` both removed** — touchstart would double-toggle against click (a tap synthesizes click), and ungated mousemove has the compat-event ordering bug above. Click covers taps with no 300ms delay in modern browsers; pointermove with the `pointerType === "mouse"` gate covers desktop hover.
- Video element's own `onClick` (`:1180-1183`) calls `handleTapToggle` (keeps its `stopPropagation`).
- **Propagation discipline (the core risk of toggle semantics):** chrome containers must not let their button clicks bubble to the root toggle. Add `onClick={(e) => e.stopPropagation()}` to: top bar container, controls wrapper, pendingResume overlay, media-failure overlay, iOS info card, diagnostic sheet, and the LyricJumpList backdrop (its tap = close sheet, not toggle chrome).
- Q13 interaction: while the lyrics sheet is open, the backdrop covers the video — backdrop taps close the sheet (stopPropagation), so chrome can't be toggled off underneath the pinned-open sheet. Consistent.
- Paused state (D5/Q23): `startHideTimer` keeps its `if (isPlaying)` guard for *auto*-hide; manual toggle-off works regardless of play state.

#### 1h. First-tap document fullscreen, touch only (D3/Q21)

```tsx
const fullscreenDoneRef = useRef(false);

const maybeEnterFullscreenOnce = useCallback(() => {
  if (fullscreenDoneRef.current) return;
  if (!isTouchDevice || !canDocumentFullscreen) return;
  // MUST be called from an activation-triggering event (click / pointerup /
  // touchend). NEVER from touchstart: touchstart is NOT an activation-
  // triggering event for the Fullscreen API, so a request fired there is
  // rejected — the same silent-failure mode as the deleted auto effect.
  document.documentElement.requestFullscreen().then(
    () => {
      fullscreenDoneRef.current = true; // success: never auto-request again
    },
    () => {
      // Rejected (transient focus/race, embed policy): leave the flag UNSET
      // so the next tap may silently retry. Bounded by user taps; no UI nag.
    }
  );
}, [isTouchDevice, canDocumentFullscreen]);
```

- `isTouchDevice` via the existing `useSyncExternalStore` capability-detection pattern (`matchMedia("(pointer: coarse)")`), alongside `canDocumentFullscreen` (`:106-134`).
- Called from `handleTapToggle` (the `click` path — activation-triggering) and from the play button's `click` handler (covers users whose first gesture is the play button — the button's stopPropagation means the root toggle never fires there). **Never wired to touchstart** (see comment in code).
- Not called from `handleActivity` (desktop mousemove must never trigger it; also redundant given the pointer-coarse gate).
- The one-shot flag latches on **promise resolution**, not on the call — a rejection doesn't consume the attempt, so a transiently-failing first tap isn't a permanent loss of auto-entry.
- Once latched, it stays latched even if the user later exits fullscreen (Esc / Android back gesture / browser UI): auto-entry never fights an explicit exit. **The manual top-bar re-enter button is permanent, not transitional** — system exit paths can't be intercepted, and iPhone Safari has no Fullscreen API at all (`canDocumentFullscreen` false), so without the button those users are stranded with browser chrome.
- The 1h request and 1g's toggle both fire on the same first tap — that's intended: the user sees chrome appear *as* the browser chrome disappears, once.

#### 1i. Rotate hint in portrait (D4/Q22)

A chip rendered inside the media overlay area, sharing the chrome fade:

```tsx
{isPortrait && isTouchDevice && !rotateHintDismissed && (
  <div
    className={cn(
      "absolute bottom-[calc(var(--sow-controller-bar-height)+1rem)] left-1/2 -translate-x-1/2",
      "z-[80] flex items-center gap-2 rounded-full bg-black/60 px-4 py-2 text-sm text-white/90",
      "transition-opacity duration-300",
      chromeVisible || isPresentationActive ? "opacity-100" : "opacity-0 pointer-events-none"
    )}
    onClick={(e) => e.stopPropagation()}
    data-testid="rotate-hint"
  >
    <RotateCw className="size-4" />
    <span>{t("controller.rotateHint")}</span>
    <button
      type="button"
      onClick={() => setRotateHintDismissed(true)}
      aria-label={t("controller.dismissInfo")}
      className="size-6 flex items-center justify-center text-white/60 hover:text-white"
    >
      <X className="size-3.5" />
    </button>
  </div>
)}
```

- `isPortrait` via `useSyncExternalStore` on `matchMedia("(orientation: portrait)")` — same pattern as capability detection.
- Fades **with** chrome: zero added clutter during normal playback (chrome hidden → hint hidden). Users see it at mount (chrome starts visible for 3s) and on every chrome summon while in portrait — a persistent but never-obstructive nudge. Session-scoped dismiss (`useState`) for those who intentionally stay portrait.
- Sits above the control bar via the same `--sow-controller-bar-height` variable used for the sheet dock; never overlaps the bar or the video's letterboxed image center.
- **Measurement timing fix (v1 §234 bug):** v1 measured the bar height only "while `isLyricsOpen`" — but the hint renders at mount, before the sheet is ever opened, so the variable would be unset and `bottom-[calc(var(--sow-controller-bar-height)+1rem)]` is an invalid declaration the browser drops, leaving the chip unbounded at the top of the overlay. Two-part fix: (a) the root sets a default `--sow-controller-bar-height: 0px` so the declaration is always valid, and (b) the measurement moves from "while sheet open" to **whenever chrome is visible** (on `chromeVisible` transitions and window resize) — the sheet dock consumes the same variable, so earlier measurement is compatible with Phase 2c.
- No orientation lock attempted anywhere (rejected: requires fullscreen on Android, unsupported on iOS).

### Phase 2: LyricJumpList — unchanged from v1

Controlled `isOpen`/`onOpenChange`, peek handle + all drag-tracking deleted, close via in-sheet chip / backdrop / Escape / bar toggle, docked above the bar with the `max-h-[calc(100dvh-var(--sow-controller-bar-height))]` flex-column budget, `rounded-t-2xl`. One addition: the backdrop's `onClick` gains `stopPropagation` (Phase 1g discipline).

### Phase 3: Lyrics toggle in the control bar — unchanged from v1

`isLyricsOpen` lifted into ControllerPlayer, chrome pinned while open (Phase 1g note: the pin blocks *auto*-hide; the sheet backdrop blocks tap-toggle), `ListMusic` button in the right column of `PlaybackControls`, i18n per v1 §3c, plus one new key:

| Key | en | zh-Hant |
|-----|----|----|
| `controller.rotateHint` | `Rotate your device for a larger view` | `旋轉裝置以放大畫面` |

### Phase 4: Tests

v1 §4a–4c apply, with these changes:

**ControllerPlayer.test.tsx**
- Delete (v1): auto-fullscreen-on-mount assertion.
- Update (D2): fullscreen-button test now asserts `document.documentElement.requestFullscreen` is called; the `webkitEnterFullscreen` iOS fallback test (`:1937-1956`) is kept by deleting `document.documentElement.requestFullscreen` in that test. Any v1-planned element-fullscreen assertions are dropped — that path no longer exists.
- New (D1): tap on the video toggles — `opacity-100` → tap → `opacity-0 pointer-events-none` immediately (no timer wait) → tap → `opacity-100`. Clicking the play button (child of the controls wrapper) does **not** toggle chrome. Manual toggle-off while paused works; the 3s auto-hide still never fires while paused (D5).
- New (D1, compat-mouse-event regression): with chrome hidden, fire `pointerMove` with `pointerType: "touch"` followed by `click` on the video (the real mobile tap sequence) → chrome is visible **and stays visible** (the pointermove must not wake chrome into the toggle's path). Fire `pointerMove` with `pointerType: "mouse"` → chrome wakes without toggling.
- New (D3): with coarse-pointer + `canDocumentFullscreen` mocks, the first tap requests document fullscreen exactly once; on mocked promise **resolution**, a second tap does not re-request; on mocked **rejection**, a second tap retries (flag latches on settle, not on call); after a mocked `fullscreenchange` exit following success, further taps do NOT re-request (manual button only). With fine-pointer mock, no request ever fires; with `canDocumentFullscreen` false, no request and no throw. Assert the request is only ever triggered from click handlers (no touchstart wiring).
- New (D4): rotate hint rendered when portrait + touch + chrome visible; absent in landscape; absent after dismiss click; gains `opacity-0` when chrome hides. At mount with the sheet never opened, the root carries a valid `--sow-controller-bar-height` (default `0px`, updated on chrome-visible transitions) so the hint's `bottom` calc always resolves — jsdom class/variable assertion; the browser pass measures the rect.

**LyricJumpList.test.tsx** — v1 changes apply; add: backdrop click closes the sheet without invoking any parent toggle (assert `onOpenChange(false)` called, and if rendered inside a toggle harness, chrome state unchanged).

**Real-browser verification (acceptance, per AGENTS.md recipe)** — v1's geometry proofs plus:
- Portrait 390×844: rotate hint visible at mount, gone after chrome fade; video rect fills viewport.
- Landscape 844×390: video fills viewport height; after first tap, `document.fullscreenElement === document.documentElement` (headless Chromium supports the Fullscreen API — if the CDP environment rejects it, verify the request was *attempted* via a spy injected with `addInitScript`, and verify real fullscreen manually on a device); URL-bar-free viewport height gain confirmed via `window.innerHeight` before/after.
- Tap-toggle: tap video → controls appear **and stay** (real touch events exercise the compat-mouse-event ordering — the regression the pointer-gate fixes); tap again → gone; screenshot pair. At mount in portrait, measure the rotate hint's rect: fully inside the viewport, bottom edge above the control bar's top edge (catches the unset-CSS-var bug jsdom can't).
- Sheet geometry proofs (Q17) unchanged.
- Safe-area: can't be measured on a notched emulator via plain headless Chrome — class-contract assertion in jsdom (`env(safe-area-inset-*)` classes present) suffices; eyeball on a real device when available.

### Phase 5: Out of scope (unchanged from v1, plus)

- Screen orientation lock (rejected, D4).
- Element video fullscreen as any kind of primary path (removed, D2).
- Desktop auto-fullscreen (manual button only, D3).

## Risks & mitigations (v2 deltas)

| Risk | Mitigation |
|------|-----------|
| Tap-toggle: missed tap on a small button hides chrome, disorienting the user | stopPropagation on every chrome container (1g); covered by the "play button doesn't toggle" test |
| Tap-toggle double-fires from touchstart+click synthesis | Toggle on `click` only; `touchstart` handler removed (1g) |
| Compat mouse events (mouseover/mousemove before click) wake chrome into the toggle's path — tap-to-reveal becomes tap-to-hide on phones | Activity wake is `pointermove` gated to `pointerType === "mouse"` (1g); dedicated regression test + real-touch browser check |
| Fullscreen request fired from touchstart is rejected (touchstart is not an activation-triggering event) — same silent-failure mode as the deleted auto effect | Request wired only to `click` handlers (1h); test asserts no touchstart wiring |
| First-tap fullscreen surprises users who only wanted to summon controls | Requested once per session, touch-only; the fullscreen transition coincides with chrome appearing; back gesture/Esc exits; the overlay player looks identical in and out of fullscreen except for browser chrome |
| User exits fullscreen via Esc/Android back — system path the page can't intercept — and iPhone Safari has no Fullscreen API at all | Manual re-enter button is permanent (1h); auto-entry never re-fires after an explicit exit (flag stays latched) |
| `requestFullscreen` promise rejection (transient focus/race, embed policy) | Flag latches on promise **resolution**, not on the call — the next tap silently retries; bounded by user taps, no UI nag (1h) |
| Rotate-hint `calc()` uses `--sow-controller-bar-height` before it's ever measured (v1 measured only while the sheet is open) → invalid declaration dropped, chip unbounded | Root default `--sow-controller-bar-height: 0px` + measurement on every chrome-visible transition (1i); mount-time geometry assertions in both jsdom and browser passes |
| Rotate hint becomes noise for intentional portrait users | Session dismiss chip; fades with chrome so it never sits over playback |
| Document fullscreen + landscape: Android gesture bar still overlays bottom edge | Safe-area padding (1b′); chrome auto-hides anyway during playback |

v1 risks not superseded (translucent gradient, discoverability of the lyrics button, etc.) still apply.
