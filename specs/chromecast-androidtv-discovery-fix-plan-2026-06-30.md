# Chromecast → AndroidTV `session_request_failed` — Fix Plan (2026-06-30)

Status: **Plan only. Not implemented.**

Scope: `delivery/webapp` (Cast sender path), local HTTPS dev server at `https://localhost:8080`,
diagnosing against an AndroidTV receiver. This plan is the execution companion to the
investigation note `specs/chromecast-androidtv-discovery-investigation-2026-06-30.md` and does
not modify it.

## Request

Chromecast to AndroidTV is not working. Investigate with Chrome DevTools and write a detailed
plan only. Do not implement.

Known starting context (from the user):

- Chrome is pointed at local webapp `https://localhost:8080`.
- `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` is not defined for the webapp.
- Chromecast discovery was working yesterday, but was blocked by the login screen.
- Today cast discovery is broken, possibly due to changes from PR #119.
- User tested in **both** a regular Chrome and the chrome-devtools-mcp-launched Chrome.
- Symptom on screen: `error: cast session request failed`.
- Preferred investigation approach: diagnose in a normal Chrome (the MCP automation Chrome
  cannot run Cast).

## Summary of findings (read-only investigation)

### The dev server and code are NOT the regression

- The `:8080` dev server (node PID 66177) started today at **13:13**, *after* both Cast
  fixes, so the running bundle includes them:
  - `f1305d8` "Fix Cast framework initialization" (12:55) — added `?loadCastFramework=1` to
    the sender URL (`delivery/webapp/src/lib/cast/loader.ts:18`) so `window.cast.framework`
    actually loads; without it `isCastSdkSupported()` returns false → `availability:"unavailable"`
    → no real Cast path.
  - `1fd9c0f` "Improve Cast session failure diagnostics" (13:09) — added
    `resolveCastReceiverAppId()` (with `.trim()`), `formatCastRequestError()`, and logs
    `castErrorCode` / `castState` / `sessionState` to `POST /api/log-client-error`.
- `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` being unset is **by design** (`.env.example`,
  `delivery/webapp/README.md`): the v3 default path falls back to
  `chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID` (`"CC1AD845"`) after SDK load
  (`delivery/webapp/src/hooks/useCast.ts:582`). There is no `.env.local` / `.env.development`
  setting it.

### PR #119 fixed yesterday's symptom, it did not cause today's

PR #119 (merged Jun 29 15:08 UTC) "Fix TV projection, playback controls, and lyric editor
polish" made projection routes public in `delivery/webapp/src/proxy.ts` and changed
unauthenticated API requests to return JSON `401` instead of an HTML `/login` redirect —
exactly the fix for the "blocked by login screen" / `invalid token '<'` receiver failure seen
yesterday. It also omitted `androidReceiverCompatible` from `setOptions` so AndroidTV stays on
the Default Media Receiver path instead of the unsupported Cast Connect path
(`delivery/webapp/src/hooks/useCast.ts:596-605`).

### The decisive clue: the symptom string identifies the failure point

`error: cast session request failed` is the exact fallback string emitted by
`formatCastRequestError()` (`delivery/webapp/src/hooks/useCast.ts:175`, added in `1fd9c0f`).
That means:

- The Cast framework **loaded** (`?loadCastFramework=1` worked).
- `setOptions` **ran** with a resolved `receiverApplicationId`.
- `cast.framework.CastContext.requestSession()` was **called and rejected**.

This is NOT "no devices found" (`RECEIVER_UNAVAILABLE`). The failure is downstream of
discovery, at session request time. `1fd9c0f` only changed error *formatting/logging*; it
cannot cause the failure, only make it visible. So PR #119 is not the regression — it surfaced a
pre-existing AndroidTV receiver failure that was previously masked by the framework never
loading (pre-`f1305d8`) and the app silently falling back to the Presentation API (which is what
hit the login wall yesterday).

### Why the MCP automation Chrome cannot be used for this investigation

The chrome-devtools-mcp browser (PID 66834) was launched with:

```
--disable-features=Translate,AcceptCHFrame,MediaRouter,OptimizationHints,WebUIReloadButton,...
--enable-automation --user-data-dir=<ephemeral>
```

`MediaRouter` is the Chrome feature that powers Chromecast device discovery and the Cast
extension surface. With it disabled (plus `--enable-automation` and an ephemeral profile),
Cast cannot run in that browser regardless of webapp code. Cast must be diagnosed in a normal
Chrome (per the user's choice).

### Secondary blocker: DevTools MCP connectivity is currently wedged

`list_pages` fails with `Failed to fetch browser webSocket URL from http://127.0.0.1:9222`
because the MCP Chrome uses `--remote-debugging-pipe` (not port 9222), and there are two
competing `chrome-devtools-mcp` processes (PIDs `62014`/`62347` @12:51 and `66485` @1:15). The
investigation tooling itself must be restored before any DevTools-driven capture.

## Root-cause hypothesis (to confirm empirically)

`cast.framework.requestSession()` rejects against the AndroidTV when using the Default Media
Receiver app id from `https://localhost:8080`. Candidate causes, in priority order:

1. **Origin / secure-context eligibility.** Cast on `localhost` HTTPS is supported, but a
   self-signed cert that is only "proceeded past" (not trusted) can weaken Cast eligibility for
   the AndroidTV receiver. Confirm the dev cert is trusted (e.g. via `mkcert`), and confirm
   `pnpm dev:https` wiring in `delivery/webapp/package.json`.
2. **AndroidTV Cast service state.** TV's Cast receiver service stale, TV not on the same LAN
   as the laptop, or SSDP/mDNS blocked by VPN/firewall. (Yesterday devices were seen, so this
   is more likely a transient TV-side state than a network topology change.)
3. **Default Media Receiver not resolving on this AndroidTV.** `CC1AD845` should launch the
   TV's built-in media receiver; verify by casting a non-SOW URL (e.g. a public MP4) to the TV
   from the same Chrome.
4. **`setOptions` shape / `autoJoinPolicy` interaction with AndroidTV.** Confirm
   `androidReceiverCompatible` is absent and `autoJoinPolicy` is `TAB_AND_ORIGIN_SCOPED`
   (`delivery/webapp/src/hooks/useCast.ts:595`).
5. **SDK timing.** If `castAppIdMode` ever reports `"unset"`, the SDK did not expose
   `chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID` at init time and the fallback did not
   resolve — would require deferring `setOptions` until `chrome.cast.isAvailable` is true.

## Execution plan

### Phase 0 — Restore DevTools MCP connectivity

1. Quit the duplicate/stale MCP processes and the pipe Chrome:
   - Kill `chrome-devtools-mcp` PIDs `62014`, `62347`, `66485` and watchdog `62348`.
   - Kill the MCP-managed Chrome PID `66834` (the one with `MediaRouter` disabled).
2. Restart a single `chrome-devtools-mcp` cleanly so the DevTools tools can attach to a browser
   again. (This browser will still be automation-only and Cast-incapable — it is used only for
   the non-Cast DevTools steps; Phase 1 launches the Cast-capable browser.)

### Phase 1 — Launch a Cast-capable Chrome (normal Chrome, per user choice)

The automation Chrome has `MediaRouter` disabled and `--enable-automation` set, so Cast cannot
run there. Launch a normal Chrome instead:

```
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/sow-cast-debug-profile \
  --no-first-run --no-default-browser-check
```

- Do **not** pass `--disable-features=MediaRouter` or `--enable-automation` (keeps Cast
  discovery and the Cast surface alive).
- Do **not** reuse the user's live profile (Chrome disallows two instances per profile). A
  fresh dir is fine; modern Chrome has Cast built in via MediaRouter, so a new profile can
  still discover devices.
- Navigate to `https://localhost:8080` and **accept/trust the self-signed cert** — Cast requires
  a secure context; `localhost` over HTTPS qualifies only if the cert is trusted.

### Phase 2 — Confirm the framework/boot path is healthy (console evaluate)

Before reproducing, verify SDK state in the DevTools console:

- `window.chrome?.cast && window.cast?.framework` → both truthy (i.e. `isCastSdkSupported()`
  true).
- `chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID` → `"CC1AD845"`; `chrome.cast.isAvailable`
  → `true`.
- `cast.framework.CastContext.getInstance().getCastState()` → expect `"NO_DEVICES_AVAILABLE"`
  or `"NOT_CONNECTED"`.
- `cast.framework.CastContext.getInstance().getOptions()?.receiverApplicationId` → `"CC1AD845"`;
  confirm `androidReceiverCompatible` is **absent**.
- Network panel: `cast_sender.js?loadCastFramework=1` returned 200 and was not blocked by CSP
  or an ad blocker.

### Phase 3 — Reproduce and capture the real SDK rejection

1. Open DevTools **Network** panel with "Preserve log" on. Trigger Cast from the controller UI
   (`/songsets/[id]/play/controller` or `/share/[token]/play/controller`).
2. Capture the `POST /api/log-client-error` request body. It now contains `castErrorCode`,
   `castState`, `sessionState`, `castAppIdMode`, `browser`, `platform`. **This payload is the
   primary diagnostic artifact.**
3. In the console, wrap `requestSession` to log the raw rejection shape:
   ```js
   const ctx = cast.framework.CastContext.getInstance();
   const _r = ctx.requestSession.bind(ctx);
   ctx.requestSession = () =>
     _r().then(v => (console.log("requestSession OK", v), v),
               e => (console.log("requestSession REJECT", JSON.stringify(e)), Promise.reject(e)));
   ```
4. Capture the full reject object: `code`, `description`, `details`, `errorCode`, and all
   enumerable keys. Map `chrome.cast.ErrorCode.*`:
   - `RECEIVER_UNAVAILABLE` → no devices / MediaRouter off / TV offline.
   - `SESSION_ERROR` / `INVALID_PARAMETER` → receiver app id problem or origin not eligible.
   - `CANCEL` / `TIMEOUT` → user dismissed the picker / picker hung.
   - No code (generic "Cast session request failed") → SDK returned an unrecognized shape; dump
     the full object.

### Phase 4 — Resolve the AndroidTV-specific failure

Based on the captured code:

- **`RECEIVER_UNAVAILABLE` / no devices:** confirm AndroidTV + laptop on the same LAN; reboot
  the AndroidTV; in `chrome://flags` ensure `#load-media-router-component-extension` is enabled;
  verify SSDP/mDNS is not blocked by VPN/firewall.
- **Session error on the Default Media Receiver:** test casting a non-SOW URL to the TV from the
  same Chrome to confirm the TV casts at all from this machine. If only SOW fails, the receiver
  id path is the issue.
- **Origin / HTTPS:** confirm the dev cert is trusted (not just "proceed"). If trust is the
  issue, generate a trusted cert with `mkcert` and wire it into `pnpm dev:https` via
  `NEXT_DEV_HTTPS_KEY` / `NEXT_DEV_HTTPS_CERT` (verify the script in
  `delivery/webapp/package.json`).
- **If `castAppIdMode === "unset"` ever appears:** the SDK did not expose
  `chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID` at init time and the fallback did not
  resolve. Mitigation: defer `setOptions` until `chrome.cast.isAvailable === true` (or until the
  `cast.framework` `CAST_STATE_CHANGED` first fires), rather than resolving the id immediately
  in the `loadCastSdk().then()` callback.

### Phase 5 — Verify the PR #119 receiver-auth fix (once a session establishes)

- Confirm `/songsets/.../play/projection` and `/share/.../play/projection` are not redirected to
  `/login` (public-route list in `delivery/webapp/src/proxy.ts`).
- Confirm an unauthenticated API call returns JSON `401`, not an HTML `/login` redirect (the old
  `invalid token '<'` failure).
- Cast the MP4; the Default Media Receiver on AndroidTV should play the R2 signed URL (4-hour
  TTL, `cast=true`). Inspect the `loadMedia` `MediaInfo` content URL to confirm it is the
  presigned R2 URL, not an `/api/...` path.

### Phase 6 — Hardening / options (only after the root cause is known)

- If origin trust is the issue: extend the Cast HTTPS workflow section in
  `delivery/webapp/README.md` (PR #119 already added a section) to document `mkcert` +
  `pnpm dev:https`.
- Consider a clearer user-facing message in `formatCastRequestError`
  (`delivery/webapp/src/hooks/useCast.ts:175`) that maps `RECEIVER_UNAVAILABLE` vs
  `SESSION_ERROR` distinctly, instead of the generic "Cast session request failed".
- Optionally surface `castState` / `sessionState` in the existing diagnostic sheet for live
  triage.

## Files that may be touched during implementation (NOT in this plan)

- `delivery/webapp/src/hooks/useCast.ts` — error mapping; possibly deferred `setOptions`.
- `delivery/webapp/src/lib/cast/loader.ts` — only if SDK timing is implicated.
- `delivery/webapp/README.md` + `delivery/webapp/package.json` (`dev:https`) — cert workflow.
- `delivery/webapp/src/proxy.ts` — verify projection routes are public (read-only check).

## Verification

- `pnpm --filter sow-webapp lint && pnpm --filter sow-webapp test` after any code change.
- Re-run the Phase 3 capture: `castErrorCode` should move from the failing code to a successful
  `castState === "CONNECTED"`, and `loadMedia` should play the MP4 on the AndroidTV.

## Open questions to resolve during execution

- What is the exact `castErrorCode` in the `POST /api/log-client-error` body? (Determines which
  Phase 4 branch applies.)
- Is the `https://localhost:8080` dev cert trusted by the system keychain, or only "proceeded
  past" in Chrome?
- Can the same Chrome cast a non-SOW URL to the AndroidTV? (Isolates SOW vs TV/network.)
