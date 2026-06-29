# Chromecast Local-Dev Login-Prompt Fix — Plan

Status: **Approved (pending implementation).**
Approved variant: **A1 + Fix B + revert `androidReceiverCompatible` change.**

## Problem

When the webapp is run locally (`http://localhost:8080`) and the user invokes the
in-page Cast button (or any browser-level Cast mechanism), the Android TV renders
the Stream of Worship web login page instead of starting media playback.

## Root cause

The root cause is **not** the earlier `androidReceiverCompatible` hypothesis.
The earlier change to `useCast.ts` is unrelated to this local-dev symptom and
should be reverted.

The real chain that produces the SOW login screen on the TV when running
locally is:

1. **Local dev server is HTTP-only.** `pnpm dev` runs Next.js at
   `http://localhost:8080`. Verified on the live page (Chrome DevTools
   `evaluate_script`, 20-second poll):
   - `cast_sender.js` injected ✓
   - `window.chrome.cast` defined ✓
   - `window.cast.framework` **never initializes** ✗

   The Google Cast Web Sender SDK framework refuses to bind on non-HTTPS
   origins — only the low-level `chrome.cast.*` namespace loads. This is
   documented Cast SDK behavior.

2. **`isCastSdkSupported()` returns `false`.**
   - `delivery/webapp/src/lib/cast/loader.ts:231-234` requires both
     `window.chrome.cast` **and** `window.cast.framework`.

3. **`useCastTransport` marks itself unavailable.**
   - `delivery/webapp/src/hooks/useCast.ts:522-528` sets
     `availability = "unavailable"` and `isSupported = false`.

4. **The Cast button is not a real Cast session trigger.**
   - `delivery/webapp/src/components/play/ControllerPlayer.tsx:823-829`
     shows the diagnostic sheet on click (when `castUnavailable`).
   - Before `availability` flips to `"unavailable"`, the button routes
     through `handleSendToTV` → `usePresentationSender.start()` (W3C
     Presentation API) in `delivery/webapp/src/app/songsets/[id]/play/controller/page.tsx:188-196`.

5. **Whichever fallback runs, it loads a webapp URL on the TV.**
   - Presentation API 2-UA mode sends `presentationUrl =
     "/songsets/[id]/play/projection"` (relative, resolved against sender
     origin `http://localhost:8080`) to the TV's Chrome.
   - Alternative Chrome browser-level Cast (tab mirroring) sends the
     controller page URL to the TV's Chrome.

6. **`proxy.ts` redirects any unauthenticated request to `/login`.**
   - `delivery/webapp/src/proxy.ts:10-26` is Next.js 16's renamed
     middleware (previously `middleware.ts`).
   - Every non-public path — including `/songsets/[id]/play/projection`
     and `/songsets/[id]/play/controller` — calls
     `auth.api.getSession`, sees no session on the TV, and returns
     `NextResponse.redirect("/login?callbackUrl=...")`.
   - The TV's Chrome follows the redirect and renders `/login`. **This is
     the SOW login screen observed on the TV.**

The production deployment worked because Vercel serves HTTPS, so
`cast.framework.*` fully loads, `cast.start()` opens a real Cast session,
the Default Media Receiver runs on the TV, and the receiver fetches the
presigned R2 MP4 from `MediaInfo.contentId` directly — never loading any
SOW webapp page on the TV.

## Evidence

- Live page (`http://localhost:8080/.../play/controller`), 20-second poll:
  `cast_sender.js` injected ✓, `window.chrome.cast` ✓,
  `window.cast.framework` ✗.
- `delivery/webapp/src/lib/cast/loader.ts:231-234` — `isCastSdkSupported()`
  requires both `window.chrome.cast` and `window.cast.framework`.
- `delivery/webapp/src/hooks/useCast.ts:522-528` — on
  `!isCastSdkSupported()` the hook sets `availability = "unavailable"` and
  returns early.
- `delivery/webapp/src/app/songsets/[id]/play/controller/page.tsx:188-196`
  — `handleSendToTV` calls `sender.start()` (Presentation API) when
  `cast.isSupported` is `false`.
- `delivery/webapp/src/hooks/usePresentation.ts:309-311` —
  `new PresentationRequest([presentationUrl])` with
  `presentationUrl = "/songsets/[id]/play/projection"` (relative, resolves
  to sender origin).
- `delivery/webapp/src/proxy.ts:17-23` — every non-public path, including
  `/songsets/.../play/projection`, redirected to `/login?callbackUrl=...`
  when no session.
- Cast SDK Developer Console (https://cast.google.com/publish): only one
  registered Cast device ("Ant Theater", serial `2F6A523EB8`). No
  registered applications. So the receiver fallbacks in the SDK are
  Google's Default Media Receiver, which does not load the webapp.

## Plan

### Approved variant

**A1 + Fix B + revert `androidReceiverCompatible` change.**

### Change 1 (A1): Add HTTPS dev script

**File:** `delivery/webapp/package.json`

**Action:** Add a new npm script `dev:https` that runs Next.js dev with
`--experimental-https`. Next.js 16 auto-generates a self-signed cert when
the flag is passed.

```jsonc
{
  "scripts": {
    "dev": "next dev --turbopack --port 8080",
    "dev:https": "next dev --turbopack --port 8080 --experimental-https",
    // ...rest unchanged
  }
}
```

**Effect:** Running `pnpm dev:https` starts the dev server at
`https://localhost:8080` with a self-signed cert (accept the cert warning
once in Chrome). On HTTPS, `cast.framework.*` initializes, the in-page
Cast button routes through `cast.start()` → real Cast SDK flow → Default
Media Receiver on the TV → MP4 playback from R2 directly. The SOW
webapp is never loaded by the TV.

**No code change required** beyond `package.json`. The Cast SDK's
behavior path on HTTPS already exists and works in production.

### Change 2 (Fix B, defense-in-depth): Public projection routes

**File:** `delivery/webapp/src/proxy.ts`

**Action:** Add `/songsets/[id]/play/projection` and
`/share/[token]/play/projection` to `PUBLIC_PATHS` so that any future
Cast mechanism that routes the TV through one of these SOW pages does
not bounce to `/login`.

Update line 4:

```ts
const PUBLIC_PATHS = [
  "/login",
  "/register",
  "/api/auth",
  "/share",
  "/api/share",
];
```

To:

```ts
const PUBLIC_PATHS = [
  "/login",
  "/register",
  "/api/auth",
  "/share",
  "/api/share",
];
// Allow projection pages to load on Cast receivers without a session cookie
// (the pinned-down MP4 URL from /api/signed-url is the security boundary).
// Routes must be matched by prefix; the play-page and API gates remain
// auth-protected.
const PUBLIC_PROJECTION_SEGMENTS = ["/play/projection"];
```

And update `isPublicPath` to also match `PUBLIC_PROJECTION_SEGMENTS` by
suffix (a request whose pathname ends with `/play/projection` is public).

**Alternative (simpler):** append explicit suffixes to `PUBLIC_PATHS`:

```ts
const PUBLIC_PATHS = ["/login", "/register", "/api/auth", "/share", "/api/share"];
// Allow projection pages — matched by suffix to cover both songset and
// share projection routes.
function isPublicPath(pathname: string) {
  if (pathname.endsWith("/play/projection")) return true;
  return PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(p + "/"));
}
```

The simpler alternative is preferred — it is one check, broadcast across
both `/songsets/[id]/play/projection` and `/share/[token]/play/projection`.

**Why this is safe even though it weakens the page-load gate:**
- The projection page (`delivery/webapp/src/app/songsets/[id]/play/projection/page.tsx:24-30`)
  gracefully handles a 401 by setting `error: "Authentication required"`
  and showing a non-blocking message — it does NOT redirect.
- `/api/songsets/:id` and `/api/signed-url` remain auth-gated by their
  own route handlers — the API layer is the real security boundary, not
  the page-load layer.
- The share path is already public — `/api/share/[token]` is rate-limited
  and token-gated.

This change is **defense-in-depth**: it doesn't fix the current symptom
(the root cause is Cast framework not loading on HTTP) but prevents future
regressions where any Cast mechanism loads the projection page on the TV
and bounces to `/login`.

### Change 3 (revert): Restore `androidReceiverCompatible: true`

**File:** `delivery/webapp/src/hooks/useCast.ts`

**Action:** Revert the earlier `androidReceiverCompatible: false` change
back to `true` (the v3 production intent). Restore the original
comment-block (no explanatory note about the Android TV login).

Restore this section to:

```ts
if (!castContextInitDone || castContextInitDone.receiverAppId !== receiverAppId) {
  castContextInitDone = { receiverAppId };
  ctx.setOptions({
    receiverApplicationId: receiverAppId,
    autoJoinPolicy: chrome.cast.AutoJoinPolicy.TAB_AND_ORIGIN_SCOPED,
    androidReceiverCompatible: true,
  });
}
```

### Change 4 (test cleanup): Drop the regression assertion

**File:** `delivery/webapp/src/test/hooks/useCastTransport.test.ts`

**Action:** Remove the `expect(opts.androidReceiverCompatible).toBeFalsy()`
assertion added in the earlier (incorrect) fix at line ~248. The existing
`receiverApplicationId` assertion remains.

### Change 5 (documentation): README update

**File:** `delivery/webapp/README.md`

**Action:** Add a sub-section under the "Development Commands" /
"Google Cast SDK Setup" area explaining:
- Local dev Cast testing MUST use `pnpm dev:https` (HTTPS required by
  the Cast SDK framework).
- HTTP localhost (`pnpm dev`) won't break the dev server but the
  in-page Cast button will fall back to the Presentation API / browser
  Cast — neither of which works for production-equivalent testing on a
  real Android TV.

## Verification

1. Run `pnpm --filter sow-webapp test` (especially `useCastTransport.test.ts`)
   — all tests pass after Change 4.
2. Run `pnpm --filter sow-webapp lint` — clean.
3. `pnpm --filter sow-webapp dev:https` →
   `https://localhost:8080/songsets/NeT2dphTDdeN4xKeJWHcX/play/controller`
4. Accept the self-signed cert warning once.
5. Chrome DevTools → `evaluate_script` poll: `window.cast.framework`
   should be defined within ~1 second of page load (no 20-second hang).
6. Verify the in-page Cast button (`data-testid="cast-button"`, the
   Monitor icon) → opens Chrome's device picker, NOT the diagnostic
   sheet.
7. Pick "Ant Theater". The TV launches Google's Default Media Receiver
   (not the SOW webapp).
8. Within a few seconds, the MP4 plays on the TV from R2 directly. No
   SOW login page should ever appear on the TV.

## Out of scope

- Custom Receiver registration (the README documents this only as a
  future option if lyrics stop being baked into the MP4). Not needed
  for v3.
- Cast SDK Developer Console review — the user has one whitelisted
  device ("Ant Theater") and no registered applications; this is correct
  for the Default Media Receiver flow used by v3.
- Better Auth session sharing with the TV — not required because the
  Default Media Receiver only fetches the presigned R2 URL, never the
  webapp.
