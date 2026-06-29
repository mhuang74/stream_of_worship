# Fix: Songset Projection Page Shows "Authentication Required" When Cast to Second Screen

**Date:** 2026-06-29
**Status:** Ready for Implementation
**Bug:** When casting the songset worship playback controller to a second screen via the Presentation API (dev fallback transport), the receiver (projection) page shows "Authentication required" instead of playing the video.

---

## 0. Problem Statement

The Web App has two screen modes for worship playback:

1. **Controller page** (`/songsets/[id]/play/controller`) — the sender, opened on the primary laptop by an authenticated user.
2. **Projection page** (`/songsets/[id]/play/projection`) — the receiver, opened on the second display (TV / 2nd monitor) via the Presentation API.

When the user clicks "Send to TV" on the controller, `usePresentationSender` calls:

```ts
new PresentationRequest([presentationUrl]).start();
```

where `presentationUrl = /songsets/${songsetId}/play/projection`.

The Presentation API opens the projection page in a **separate receiving browsing context** that does **not** share the sender's Better Auth session cookies — even though both pages are on the same origin (`https://localhost:8080`).

### Confirmed via Chrome DevTools

- **Controller page (sender, page id 4):** `document.cookie` only shows `__next_hmr_refresh_hash__=153`. The Better Auth session cookies are `HttpOnly` so they are not in `document.cookie`, but they **are** sent with `fetch` — so the controller's `GET /api/songsets/...` succeeds.
- **Projection page (receiver, page 6):** `document.cookie` is `""` (empty). `navigator.presentation.receiver` is `true`, confirming this is a receiver context. Its `GET /api/songsets/NeT2dphTDdeN4xKeJWHcX` returns `401 {"error":"Unauthorized"}` because **no session cookie is sent**.
- Only one network request fires on the receiver: `GET /api/songsets/...` → 401. The page then sets `error = "Authentication required"` (`projection/page.tsx:27`) and renders the error instead of the `<video>`.

### Root cause

The receiver (`ProjectionPage`) independently re-fetches the songset, render job, and signed URL via authenticated APIs — but the receiver browsing context has no session. The sender (controller) already has all this data, including a 4-hour signed R2 URL minted with `cast=true` (see `controller/page.tsx:85-91`). The data simply never gets handed to the receiver.

The comment at `controller/page.tsx:85-88` even documents the intent:

> The logged-in phone mints the presigned R2 URL with its own session and hands it to the TV receiver (the TV only hits R2, never the webapp). `cast=true` mints the 4-hour Cast-playback expiry so the URL survives a full service + setup.

But the projection page never receives the handoff — it insists on fetching everything itself.

### Evidence (from the live Chrome session)

```
reqid=3 GET https://localhost:8080/api/songsets/NeT2dphTDdeN4xKeJWHcX [401]
Response Body: {"error":"Unauthorized"}
Request Headers: (no Cookie header — HttpOnly session cookie absent from receiver context)
```

```
console: [error] Failed to load resource: the server responded with a status of 401 (Unauthorized)
```

```
ProjectionPage receiver context:
  cookie: ""
  origin: "https://localhost:8080"
  presentationReceiver: true
```

---

## 1. Design Decision

| Decision | Choice | Rationale |
|----------|--------|-----------|
| How to pass data sender → receiver | **Query string on the projection URL** (`?v=<signedUrl>&t=<title>`) | Simpler than a new `PresentationCommand`, lets the receiver page self-boot without waiting for the `PresentationConnection` to establish, and survives receiver reloads. The signed R2 URL is already minted with 4-hour `cast=true` expiry on the sender, so it can be embedded directly in the URL. |
| Receiver-side read mechanism | `window.location.search` parsed inside the existing `useEffect` | Avoids `useSearchParams()` which — per the Next.js 16 docs in `node_modules/next/dist/docs/01-app/03-api-reference/04-functions/use-search-params.md` lines 79–86 — forces client-side rendering of the subtree up to the nearest `<Suspense>` boundary during prerendering and fails the production build if no `<Suspense>` wraps it. The projection page is a `"use client"` component that already reads `useParams`; reading `window.location.search` inside the existing `useEffect` keeps the static-prerender story unchanged and adds no Suspense requirement. |
| What to do when params are absent | Fall back to the current authenticated fetch path | Preserves the direct-navigation UX (an authenticated user pasting the projection URL into their own browser) and keeps every existing test passing unchanged — the tests don't set `window.location.search`, so they exercise the fallback path. |
| Should the receiver still use `usePresentationReceiver` for commands? | Yes — unchanged | `ProjectionPlayer` already wires `usePresentationReceiver` for play/pause/seek/volume/mute/songTitle commands. Once `videoSrc` is set (from the query param), the receiver is ready to accept transport commands from the sender's `usePresentationSender.send()` over the established `PresentationConnection`. |
| Scope of auth on receiver | Remove all authenticated API calls from the receiver code path when params are present | Mirrors the share-token projection page (`/share/[token]/play/projection`) which already receives `data.playback.mp4Url` from a public endpoint with no session requirement. The receiver should be cookie-independent. |
| Should `songTitle` command still be sent over the connection? | Unchanged — still supported | When the sender re-renders with a different song name, it can send the existing `songTitle` `PresentationCommand` over the connection to update the overlay. The `?t=` param is just the initial value, matching how `initialSongTitle` flows to `ProjectionPlayer`. |
| URL length concern | Acceptable | Presigned R2 URLs are ~500–700 chars; browser URL limits are ~2k+ minimum; the Presentation API accepts long receiver URLs. This is a dev-only fallback transport (Cast SDK is production), so exposing a short-TTL presigned URL in the query string is on par with the share-token JSON path which returns the same kind of presigned URL. |

---

## 2. Implementation Phases

### Phase 1: Pass signed video URL + title via query string from controller

**File:** `delivery/webapp/src/app/songsets/[id]/play/controller/page.tsx` (line 144)

**Problem:** `presentationUrl` is a bare path with no data:

```ts
const presentationUrl = `/songsets/${songsetId}/play/projection`;
```

The receiver opens this URL inside a cookie-less browsing context and can't fetch anything.

**Change:** Make `presentationUrl` a `useMemo` that appends `v` (signed video URL) and `t` (songset name) query params when the data is ready:

```tsx
import { useMemo } from "react"; // already imported at top of file

const presentationUrl = useMemo(() => {
  const params = new URLSearchParams();
  if (videoUrl) params.set("v", videoUrl);
  if (songset?.name) params.set("t", songset.name);
  const qs = params.toString();
  return qs
    ? `/songsets/${songsetId}/play/projection?${qs}`
    : `/songsets/${songsetId}/play/projection`;
}, [songsetId, videoUrl, songset?.name]);
```

**Why `useMemo` and not a plain string:** `usePresentationSender`'s `start` callback is `useCallback`-memoized on `[presentationUrl]` (`usePresentation.ts:343`). When `videoUrl` loads and the memo recomputes, `start` is recreated with the current URL. The user clicks "Send to TV" only after `loadData()` completes and the player renders (the controller's `if (isLoading)` / `if (error || !songset || !videoUrl)` guards at lines 209–236 prevent the button from rendering before data is ready), so `videoUrl` and `songset.name` are guaranteed populated by the time `handleSendToTV` runs.

**Edge case — `videoUrl` not yet loaded:** The `presentationUrl` falls back to the bare path (no query string), which means the receiver would try the authenticated fallback. This state is unreachable in practice because the controller's UI only renders the "Send to TV" button after loading completes, but the defensive behavior is harmless.

---

### Phase 2: Read query params on the projection page, skip authenticated fetches

**File:** `delivery/webapp/src/app/songsets/[id]/play/projection/page.tsx` (lines 19–76 inside `loadData`)

**Problem:** `loadData()` unconditionally fetches three authenticated endpoints:

1. `GET /api/songsets/${songsetId}` → 401 in the receiver context
2. `GET /api/render-jobs/${songsetData.latestRenderJobId}`
3. `GET /api/signed-url?renderJobId=...&fileType=video`

All three require a session cookie the receiver doesn't have.

**Change:** At the top of `loadData()`, read `window.location.search`. If a `v` param is present, use it as `videoUrl` and the `t` param as `initialTitle`, then `return` early without making any fetch calls. Otherwise, keep the existing fetch flow as a fallback (so direct navigation by an authenticated user continues to work).

```tsx
async function loadData() {
  try {
    setIsLoading(true);
    setError(null);

    // Receiver context (opened via PresentationRequest) does not share the
    // sender's session cookies, so it cannot call the authenticated songset /
    // render-job / signed-url APIs. The sender (controller) mints a 4-hour
    // signed R2 URL and passes it via the `v` query param; `t` carries the
    // songset name for the title overlay. Fall back to the authenticated
    // fetch path only when the params are absent (direct navigation).
    const searchParams = new URLSearchParams(window.location.search);
    const passedVideoUrl = searchParams.get("v");
    const passedTitle = searchParams.get("t") ?? undefined;

    if (passedVideoUrl) {
      if (cancelled) return;
      setVideoUrl(passedVideoUrl);
      setInitialTitle(passedTitle);
      return;
    }

    // --- existing authenticated fallback (unchanged) ---
    const songsetResponse = await fetch(`/api/songsets/${songsetId}`);
    if (!songsetResponse.ok) {
      if (songsetResponse.status === 401) {
        setError("Authentication required");
        return;
      }
      throw new Error("Failed to load songset");
    }

    const songsetData = await songsetResponse.json();
    if (cancelled) return;

    setInitialTitle(songsetData.name as string);

    if (!songsetData.latestRenderJobId) {
      throw new Error("No render artifacts available");
    }

    const jobResponse = await fetch(
      `/api/render-jobs/${songsetData.latestRenderJobId}`
    );
    if (!jobResponse.ok) {
      throw new Error("Failed to load render job");
    }

    const jobData = await jobResponse.json();
    if (cancelled) return;

    if (!jobData.mp4R2Key) {
      throw new Error("No video available for this songset");
    }

    const signedUrlResponse = await fetch(
      `/api/signed-url?renderJobId=${encodeURIComponent(songsetData.latestRenderJobId)}&fileType=video`
    );
    if (!signedUrlResponse.ok) {
      throw new Error("Failed to get video URL");
    }

    const { url } = await signedUrlResponse.json();
    if (cancelled) return;

    setVideoUrl(url as string);
  } catch (err) {
    if (!cancelled) {
      setError(err instanceof Error ? err.message : "Failed to load projection");
    }
  } finally {
    if (!cancelled) {
      setIsLoading(false);
    }
  }
}
```

**Why `window.location.search` and not `useSearchParams()`:** The Next.js 16 docs warn that `useSearchParams()` forces client-side rendering of the subtree up to the nearest `<Suspense>` boundary, and that a production build **fails** with `Missing Suspense boundary with useSearchParams` if none exists (see `node_modules/next/dist/docs/01-app/03-api-reference/04-functions/use-search-params.md` lines 79–180). Reading `window.location.search` inside the existing `useEffect` sidesteps that entirely and keeps the build green without restructuring the page into a server-component wrapper + `<Suspense>` + client child, which would be a much larger, unrelated refactor.

---

## 3. Files Modified

| File | Change Type | Phase |
|------|-------------|-------|
| `delivery/webapp/src/app/songsets/[id]/play/controller/page.tsx` | Edit: make `presentationUrl` a `useMemo` appending `v` + `t` query params | 1 |
| `delivery/webapp/src/app/songsets/[id]/play/projection/page.tsx` | Edit: read `window.location.search` at top of `loadData()`, use `v`/`t` params when present, return early without fetches | 2 |

**Total: 2 files edited, 0 new files**

---

## 4. Implementation Order

1. **Phase 2 first** (projection page reads query params) — making the receiver accept the handoff.
2. **Phase 1** (controller appends query params) — making the sender provide the handoff.

Either order works in isolation (each is independently correct), but doing Phase 2 first means the receiver is ready to consume params before the sender starts emitting them, which is safer if the two changes land in separate commits.

---

## 5. What This Does NOT Change

- **`ProjectionPlayer`** (`delivery/webapp/src/components/play/ProjectionPlayer.tsx`) — no changes. It already accepts `videoSrc` + `initialSongTitle` props and wires `usePresentationReceiver` for transport commands. Once the projection page sets `videoSrc` from the `v` param, the player is fully functional and ready to receive play/pause/seek/volume/mute commands over the `PresentationConnection`.
- **`usePresentationSender` / `usePresentationReceiver`** (`delivery/webapp/src/hooks/usePresentation.ts`) — no changes. The sender hook already uses `presentationUrl` in `start()`; a longer URL with query string is transparent to it. The receiver hook processes `PresentationCommand` messages, which is orthogonal to how the video URL is obtained.
- **Share-token projection page** (`/share/[token]/play/projection`) — no changes. It already works without auth (fetches from the public `/api/share/:token` endpoint).
- **Authenticated APIs** (`/api/songsets/:id`, `/api/render-jobs/:id`, `/api/signed-url`) — no changes. They remain session-gated; the receiver simply stops calling them when params are present.
- **Cast SDK (production transport)** — not affected. The Cast path mints its own 4-hour signed URL (line 89–91) and passes it via `CastMedia.videoUrl` → `chrome.cast.framework` `LoadRequest`. This fix only touches the Presentation-API fallback path used in browser-to-browser dev mode.
- **Better Auth config** (`src/lib/auth.ts`) — no cookie/SameSite/`HttpOnly` changes. The receiver cookie isolation is a Presentation-API spec behavior, not a misconfiguration.

---

## 6. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Presigned R2 URL exposed in URL (query string logged in browser history, server access logs) | The Presentation fallback is **dev-only** (Cast SDK is production). The signed URL has a 4-hour expiry (`cast=true`). This is comparable to the share-token path which returns the same kind of presigned URL in JSON. Acceptable for dev/testing; production uses Cast which never puts the URL in a browser-visible query string. |
| `videoUrl` not ready when user clicks "Send to TV" | Unreachable: the controller's render guards (`if (isLoading)` / `if (error || !songset || !videoUrl)` at lines 209–236) prevent the `ControllerPlayer` (and its "Send to TV" button) from rendering until `loadData()` completes. Defensively, if `videoUrl` is still null, `presentationUrl` falls back to the bare path and the receiver tries the authenticated fallback (which would 401 and show "Authentication required" — the same pre-fix behavior, no regression). |
| Receiver navigates directly (no query params) and has no session | Falls through to the existing authenticated fetch path. If the user is authenticated, it works as before. If not, it shows "Authentication required" — same as today. No regression. |
| `window.location.search` is empty in jsdom (tests) | Existing tests in `projection-page.test.tsx` don't set `window.location.search`, so they exercise the authenticated fallback (including the 401 → "Authentication required" assertion at line 157). These remain valid and pass unchanged. A new test will be added (Phase 3) that sets `window.location.search` to exercise the new param path. |
| Long presigned URL exceeds some Presentation-API URL length limit | Presentation API accepts the full receiver URL; presigned R2 URLs are typically 500–700 chars, well under browser URL limits (~2k+). No mitigation needed. |

---

## 7. Test Plan

### Existing tests (must remain green)

`delivery/webapp/src/test/app/projection-page.test.tsx` — all existing tests exercise the authenticated fallback path (jsdom `window.location.search` is `""`), so they're unaffected:

- `shows loading spinner while fetching data` ✅
- `shows error when songset fetch fails` ✅
- `shows error when no render artifacts available` ✅
- `shows error when render job fetch fails` ✅
- `shows error when no video available` ✅
- `shows error when signed URL fetch fails` ✅
- `shows error message on 401` → asserts "authentication required" ✅
- `renders ProjectionPlayer when data loads` ✅
- `passes signed video URL to ProjectionPlayer` ✅
- `passes songset name as initial title to ProjectionPlayer` ✅
- `fetches signed URL with renderJobId and fileType` ✅
- `does not render app header` ✅
- `does not render navigation` ✅

### New test to add (Phase 3)

Append to `projection-page.test.tsx`:

```tsx
describe("query-param handoff (receiver context)", () => {
  beforeEach(() => {
    // Simulate the URL the controller builds: /songsets/.../projection?v=<signedUrl>&t=<title>
    const url = new URL("https://localhost:8080/songsets/test-songset/play/projection");
    url.searchParams.set("v", "https://cdn.example.com/video.mp4?signature=abc");
    url.searchParams.set("t", "Morning Worship");
    Object.defineProperty(window, "location", {
      value: { ...window.location, search: url.search },
      writable: true,
    });
  });

  it("uses the v param as the video URL without fetching", async () => {
    const fetchSpy = vi.fn();
    global.fetch = fetchSpy;

    render(<ProjectionPage />);

    await waitFor(() => {
      expect(screen.getByTestId("video-src")).toHaveTextContent(
        "https://cdn.example.com/video.mp4?signature=abc"
      );
    });
    // No authenticated API calls should fire
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("uses the t param as the initial song title", async () => {
    global.fetch = vi.fn();

    render(<ProjectionPage />);

    await waitFor(() => {
      expect(screen.getByTestId("initial-title")).toHaveTextContent("Morning Worship");
    });
  });
});
```

### Lint / typecheck / build

```bash
cd delivery/webapp && pnpm lint
cd delivery/webapp && pnpm test
cd delivery/webapp && pnpm build   # confirms no Suspense/useSearchParams pitfall
```

---

## 8. Manual Verification (Chrome DevTools)

After implementing the fix:

1. **Reload the controller page** (page id 4) at `https://localhost:8080/songsets/NeT2dphTDdeN4xKeJWHcX/play/controller` — confirm it still loads authenticated data (the controller is unaffected).
2. **Reload the projection page** (page id 6) but first navigate it to the controller-built URL with query params, e.g. `https://localhost:8080/songsets/NeT2dphTDdeN4xKeJWHcX/play/projection?v=<signedUrl>&t=Morning%20Worship` — confirm the video element renders and `navigator.presentation.receiver` is still `true`.
3. **Verify no 401 fires** on the projection page: list network requests, confirm `GET /api/songsets/...` is **not** in the list.
4. **Drive end-to-end:** On the controller page, click "Send to TV" (Cast unavailable in this browser → Presentation fallback). Confirm the projection page receives the video URL and becomes ready. Then send a `play` command from the controller and confirm the receiver `<video>` starts playing.
5. **Reverse check:** Reload the projection page with a bare URL (no query params) while authenticated — confirm it still loads via the authenticated fallback (no regression for direct navigation).
