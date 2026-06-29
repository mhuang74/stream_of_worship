# Fix: Shared Link Worship Playback Parity with Songsets Worship Playback

**Date:** 2026-06-29
**Status:** Ready for Implementation
**Branch:** `lyrics_editor_fixes_0628`
**Version:** 2.0 (post-interview)

---

## 0. Problem Statement

The product decision is that the Shared Link Worship Playback screen
(`/share/[token]/play/controller`) should be **basically the same** as the
regular Songsets Worship Playback screen (`/songsets/[id]/play/controller`),
except that shared-link viewers cannot navigate back to Songsets or Settings.

The current branch already applied several fixes symmetrically to both
surfaces (commit `125665a` added `handleStopPresentation` + the
`presentationMediaStatus` prop to both controller pages; the `LyricJumpList`
expand-vs-seek behavior in `d2e5691` and the top-bar overlap fix in `abdef9f`
live inside the shared `<ControllerPlayer>` component). However, two
structural divergences remain on the shared-link side, and they prevent the
two surfaces from being "basically the same."

---

## 1. Scope

**In scope:**

- Shared-link controller page: `delivery/webapp/src/app/share/[token]/play/controller/page.tsx`
- Shared-link projection receiver page: `delivery/webapp/src/app/share/[token]/play/projection/page.tsx`
- Share-token API presigned URL TTL: API route serving `/api/share/{token}`

**Out of scope (confirmed):**

- `/share/[token]/play/audio` (mp3-only fallback page) — keep native `<audio controls>`. The songsets flow has no audio-only path; aligning it fully would expand scope into a code path songsets doesn't have.
- `/share/[token]/page.tsx` (share landing card) — not refactored to use `PrePlayCard`. Intentionally different from the logged-in "Play" page (no re-render/share-dialog buttons, public unauthenticated context).
- Mobile fullscreen degradation — out of scope for this branch. The Fullscreen API behavior on mobile browsers (iOS Safari, Android Chrome) is accepted as-is; if unavailable, the browser ignores the fullscreen request and the normal layout renders.

---

## 2. Decisions (confirmed by user)

| Decision | Choice |
|---|---|
| `autoFullscreen` on shared-link controller | Match songsets (`true`) — remove the explicit `false`. |
| Projection receiver URL on shared-link | Pass `?v=&t=` query params, mirroring the songsets pattern (commit `3f08a9f`). |
| Presigned URL TTL for share tokens | **Extend to 4 hours** (matching `/api/signed-url?cast=true`). |
| Back navigation from external referrer | **Always land on `/share/[token]`** — use history replacement on controller entry. |

---

## 3. User-Named Concerns — Already in Sync (No Changes Needed)

All four user-named concerns are already in sync because **both controllers
render the same shared `<ControllerPlayer>` component**, which composes:

| Concern | Implementation Site | Already Shared? |
|---|---|---|
| Lyrics pull-up sheet navigation | `LyricJumpList.tsx` (composed inside `ControllerPlayer`) | Yes — share inherits the `d2e5691` "expand instead of seek" fix |
| Back vs. fullscreen icons | `ControllerPlayer.tsx:889-918` top bar | Yes — share inherits the `abdef9f` overlap fix |
| Keyboard shortcuts for playback control | `useKeyboardShortcuts.ts` (wired inside `ControllerPlayer`) | Yes — Space / `←` / `→` / `[` / `]` |
| On-screen playback controller | `PlaybackControls.tsx` (composed inside `ControllerPlayer`) | Yes — prev / play / pause / next / volume / presentation chip |

The "no navigation back to Songsets or Settings" requirement is also already
honored:

- `share/[token]/play/controller/page.tsx:213` sets `exitRoute={`/share/${token}`}`.
- `ControllerPlayer.handleExit` (`ControllerPlayer.tsx:536-558`) honors `exitRoute` (falls back to `/songsets/${playerId}/play` only when the prop is omitted).
- Global chrome suppression on projection routes via `isProjectionRoute` (`lib/routes.ts:1-6`) already matches `/share/.../play/projection`.

---

## 4. Implementation

### Change 1 — Remove `autoFullscreen={false}` from share controller

**File:** `delivery/webapp/src/app/share/[token]/play/controller/page.tsx`

**Location:** Line 214, inside the `<ControllerPlayer>` JSX.

**Before:**

```tsx
return (
  <ControllerPlayer
    playerId={token}
    videoSrc={videoUrl}
    chapters={chapters}
    exitRoute={`/share/${token}`}
    autoFullscreen={false}
    isPresentationActive={isPresentationActive}
    ...
```

**After:**

```tsx
return (
  <ControllerPlayer
    playerId={token}
    videoSrc={videoUrl}
    chapters={chapters}
    exitRoute={`/share/${token}`}
    isPresentationActive={isPresentationActive}
    ...
```

**Rationale:** `ControllerPlayer` defaults `autoFullscreen` to `true`
(`ControllerPlayer.tsx:105`). Removing the explicit `false` lets the share
controller auto-enter fullscreen on mount and register the `fullscreenchange`
effect (`ControllerPlayer.tsx:637-666`) that re-shows controls on Esc. Back
button and Esc both continue to exit cleanly. This mirrors the songsets
controller, which omits the prop.

---

### Change 2 — Pass `?v=&t=` from share controller to projection receiver

Mirrors the songsets pattern adopted in commit `3f08a9f`
(`songsets/[id]/play/controller/page.tsx:153-161` +
`songsets/[id]/play/projection/page.tsx:31-40`).

#### 2A. Controller side

**File:** `delivery/webapp/src/app/share/[token]/play/controller/page.tsx`

**Location:** Line 103 — replace the bare `presentationUrl` string with a
`useMemo` (already imported at line 3).

**Before (line 103):**

```tsx
const presentationUrl = `/share/${token}/play/projection`;
```

**After:**

```tsx
const presentationUrl = useMemo(() => {
  const params = new URLSearchParams();
  if (videoUrl) params.set("v", videoUrl);
  if (shareName) params.set("t", shareName);
  const qs = params.toString();
  return qs
    ? `/share/${token}/play/projection?${qs}`
    : `/share/${token}/play/projection`;
}, [token, videoUrl, shareName]);
```

The share controller already has:

- `videoUrl` (set at line 59 — from `data.playback.mp4Url`, the presigned R2 URL minted by `/api/share/{token}`).
- `shareName` (set at lines 60-62 — from `data.songset.name`; defaults to `"Shared Worship Set"`).

The bare-path fallback keeps `handleSendToTV` from firing before data is ready
(same guard pattern songsets uses).

#### 2B. Receiver side

**File:** `delivery/webapp/src/app/share/[token]/play/projection/page.tsx`

**Location:** Lines 16-58 — insert a `?v=`/`?t=` fast-path before the existing
`/api/share/${token}` fetch.

**Before (lines 16-49, the `loadShare` body):**

```tsx
useEffect(() => {
  let cancelled = false;

  async function loadShare() {
    try {
      setIsLoading(true);
      setError(null);

      const res = await fetch(`/api/share/${token}`);
      ...
    } catch (err) {
      ...
    } finally {
      if (!cancelled) setIsLoading(false);
    }
  }

  if (token) loadShare();
  return () => { cancelled = true; };
}, [token]);
```

**After:**

```tsx
useEffect(() => {
  let cancelled = false;

  async function loadShare() {
    try {
      setIsLoading(true);
      setError(null);

      // Receiver context (opened via PresentationRequest) does not share the
      // sender's session cookies. The sender (controller) passes the
      // presigned R2 URL via the `v` query param so the receiver can boot
      // without calling any API. `t` carries the songset name for the title
      // overlay. Fall back to the public /api/share/{token} fetch only when
      // the params are absent (direct navigation).
      const searchParams = new URLSearchParams(window.location.search);
      const passedVideoUrl = searchParams.get("v");
      const passedTitle = searchParams.get("t") ?? undefined;

      if (passedVideoUrl) {
        if (cancelled) return;
        setVideoUrl(passedVideoUrl);
        setSongTitle(passedTitle);
        return;
      }

      const res = await fetch(`/api/share/${token}`);
      ...
    } catch (err) {
      ...
    } finally {
      if (!cancelled) setIsLoading(false);
    }
  }

  if (token) loadShare();
  return () => { cancelled = true; };
}, [token]);
```

**Notes:**

- The early `return` inside `try` does run `finally` (JS semantics: `return` in `try` runs `finally` before unwinding), so `setIsLoading(false)` will fire — but the songsets receiver does the same thing and it's idempotent. Matches the songsets receiver behavior exactly.
- The `/api/share/{token}` fallback still works for direct navigation because the share API is public (no cookies required).
- Existing variable names reused: `setVideoUrl`, `setSongTitle`.

---

### Change 3 — Extend presigned URL TTL to 4 hours (NEW)

**File:** API route serving `/api/share/{token}` (to be identified; likely
`delivery/webapp/src/app/api/share/[token]/route.ts` or a helper used by it)

**Requirement:** The presigned `mp4Url` returned in the share-token response
must have a **minimum TTL of 4 hours** (14,400 seconds), matching the behavior
of `/api/signed-url?cast=true` used by the songsets controller.

**Rationale:** The receiver consumes this URL via `?v=` and may play for an
entire worship service. A short TTL (e.g., 15 minutes) would cause hard
playback failure mid-service. The songsets flow already solved this with a
4-hour TTL for casting; the share flow must match.

**Implementation note:** No API contract changes. The `mp4Url` field in the
response remains a string. Only the expiry parameter in the presigned URL
generation call changes.

---

### Change 4 — Ensure Back always lands on `/share/[token]` (NEW)

**File:** `delivery/webapp/src/app/share/[token]/play/controller/page.tsx`

**Requirement:** When navigating *to* the controller page (e.g., from the share
landing card or an external referrer), use **history replacement**
(`router.replace` or equivalent) so that pressing the controller's Back button
(or browser Back after exiting fullscreen) always lands on `/share/[token]`,
even for users who arrived from a text message or other external source.

**Rationale:** The `exitRoute` prop is already set to `/share/${token}`. The
gap is on the *entry* side: if the user arrives via `router.push` from an
external page, the browser Back button would exit the site entirely instead of
returning to the share landing card. History replacement keeps the navigation
bounded within the share flow.

**Implementation note:** This likely involves changing the navigation call on
the share landing card (`/share/[token]/page.tsx`) or within the controller
page's own entry logic, depending on where the navigation originates.

---

## 5. Files Affected

1. `delivery/webapp/src/app/share/[token]/play/controller/page.tsx` — three edits:
   - Remove `autoFullscreen={false}` prop on `<ControllerPlayer>`.
   - Convert `presentationUrl` from bare string (line 103) to `useMemo` with `?v=&t=` query params. `useMemo` is already imported from React at line 3.
   - (Investigate) Add or ensure history replacement on entry navigation.
2. `delivery/webapp/src/app/share/[token]/play/projection/page.tsx` — insert `?v=`/`?t=` fast-path before `/api/share/${token}` fetch in `loadShare`.
3. API route for `/api/share/{token}` — extend presigned URL TTL to 4 hours.

No new files. No new dependencies. No API contract changes.

---

## 6. Test Updates Required

Check these existing tests for assertions that may break:

1. `delivery/webapp/src/test/app/controller-page.test.tsx`
   - Any assertion that `ShareControllerPage` passes `autoFullscreen={false}` to `ControllerPlayer`. **After change:** assert `autoFullscreen` is **not** passed (or is `undefined`/defaults to `true`).
   - Any assertion on `presentationUrl` being the bare `/share/{token}/play/projection`. **After change:** assert it includes `?v={videoUrl}&t={shareName}` when those are set (and bare path when not).
   - (New) Assert that navigation to the controller uses `router.replace` (or equivalent) rather than `router.push`.

2. `delivery/webapp/src/test/app/projection-page.test.tsx`
   - Any assertion that the share projection receiver always calls `/api/share/{token}`. **After change:** add a case where `?v=` and `?t=` are present in `window.location.search` and assert **no fetch is made** (use `vi.spyOn(global, "fetch")` like the songsets projection test does).

3. API route tests (if existing)
   - Assert that the presigned URL returned by `/api/share/{token}` has a TTL of at least 4 hours (14,400 seconds).

**Run after edits:**

```bash
pnpm --filter sow-webapp lint
pnpm --filter sow-webapp test
pnpm --filter sow-webapp build
```

Optionally update the graphify knowledge graph after committing:

```bash
graphify update .
```

---

## 7. Manual Verification

1. **Auto-fullscreen on share controller** — Open `/share/{token}/play/controller` in a browser.
   - On mount the page enters browser fullscreen automatically (same as songsets controller).
   - Press Esc — controls re-appear (Maximize button visible top-left, alongside Back).
   - Back button → `/share/{token}` (not `/login`, not `/songsets`, not `/settings`).

2. **Projection receiver boots from query params** — From the share controller, press the Cast / Send-to-TV button.
   - The receiver page loads video from the `?v=` URL **without** calling `/api/share/{token}` (verify in the receiver's network tab: zero requests to `/api/share`).
   - The title overlay shows the share name (from `?t=`).
   - Cast + Presentation API fallback both work.

3. **Projection direct-navigation fallback** — Navigate directly to `/share/{token}/play/projection` (no `?v=`).
   - Receiver fetches `/api/share/{token}` and loads the video. (Public API, no cookies — no "Authentication required" error.)

4. **Presigned URL TTL** — Inspect the `mp4Url` returned by `/api/share/{token}`.
   - Verify the presigned expiry parameter is ≥4 hours from the current time.

5. **Back behavior from external referrer** — Open `/share/{token}/play/controller` from a fresh tab (simulating text message link).
   - Press Esc to exit fullscreen.
   - Press the Back button (top-left `ArrowLeft`) or browser Back.
   - Lands on `/share/{token}` (share landing), not the previous external page or `about:blank`.

6. **Lyrics pull-up, keyboard, on-screen controls** (shared via `<ControllerPlayer>`, smoke-test only) —
   - Tap handle bar to open lyrics list.
   - Tap a song title to expand its lines.
   - Tap a line to jump to that moment.
   - Press Space to play/pause.
   - Press `[` / `]` to change songs.
   - Press `←` / `→` to seek 10s.

7. **Exit behavior** — Inside the share controller, press the Back button (top-left `ArrowLeft`).
   - Exits to `/share/{token}` (share landing), not `/songsets` or `/settings`.

---

## 8. Risk Assessment

- **`autoFullscreen={false}` was previously deliberate.** Removing it changes UX for shared-link viewers: they'll auto-enter browser fullscreen on entry. This is the intended "basically the same" behavior per the product decision. Shared-link viewers coming from arbitrary referrers (text messages, etc.) will still see the standard browser fullscreen gate and can press Esc to exit. The Maximize button re-enters fullscreen.

- **Presigned URL lifetime.** Extending the TTL to 4 hours eliminates the risk of mid-service playback failure. This is a safe, backward-compatible change — existing share links continue to work, and longer-lived URLs do not break any consumer.

- **History replacement on entry.** Using `router.replace` (or equivalent) prevents the browser Back button from exiting the site when the user arrived from an external referrer. This is bounded to the share controller entry point and does not affect other navigation flows.

- **No structural refactor.** This change does **not** extract a shared controller wrapper or generalize the transport-wiring block. The two controller pages remain near-duplicate (~230 lines vs ~288 lines). That's an accepted trade-off for this branch; a future refactor could extract a `useControllerTransport` hook (see the exploration report for that future direction).

- **Backward compatibility.** Old share links without `?v=` will continue to work — the receiver falls back to `/api/share/{token}`. Streaming receivers already casting when this code deploys will continue to use the bare `presentationUrl` until the sender reloads.

---

## 9. Out-of-Scope Future Direction

A higher-leverage refactor (deferred to a future branch) would extract a
`useControllerTransport({ media, presentationUrlBuilder, tokenOrId })` hook
returning `{ cast, sender, presentationMediaStatus, isPresentationActive,
handleSendToTV, handleStopPresentation, handleSendTransportCommand,
castPropsBundle }`. The two controller page components would then collapse to
data-load + `<ControllerPlayer {...transportBundle} playerId videoSrc chapters
exitRoute />`. This would eliminate the ~80 lines of duplicated transport
wiring between the two controller pages and prevent future drift (the
existing drift was the bug that motivated this branch's audit).
