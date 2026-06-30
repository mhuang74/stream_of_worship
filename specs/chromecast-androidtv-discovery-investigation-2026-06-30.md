# Chromecast AndroidTV Discovery Investigation — 2026-06-30

Status: **Investigation complete; implementation not started.**

Scope: `delivery/webapp`, local HTTPS dev server at `https://localhost:8080`.

## Request

Chromecast to AndroidTV is not working. Investigate with Chrome DevTools and write
a detailed plan only. Do not implement.

Known starting context:

- Chrome is pointed at local webapp `https://localhost:8080`.
- `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` is not defined for the webapp.
- Chromecast discovery worked yesterday, but was blocked by the login screen.
- Today Cast discovery is broken, possibly due to changes from PR #119.

## Chrome DevTools Findings

DevTools initially exposed only `about:blank`, so the local app was opened in the
DevTools-controlled tab at `https://localhost:8080`.

The app loaded and redirected to `/songsets` with an authenticated session.
Clicking Play on a rendered songset opened:

```text
https://localhost:8080/songsets/Jxyu78jKqWfw6Ot-5NSG6/play/controller
```

Runtime Cast probe from the controller page:

```json
{
  "href": "https://localhost:8080/songsets/Jxyu78jKqWfw6Ot-5NSG6/play/controller",
  "isSecureContext": true,
  "hasChromeCast": true,
  "hasCastFramework": true,
  "hasGCastCallback": "function",
  "defaultReceiverAppId": "CC1AD845",
  "castState": "NO_DEVICES_AVAILABLE",
  "sessionState": "NO_SESSION",
  "castScripts": [
    "https://www.gstatic.com/cv/js/sender/v1/cast_sender.js?loadCastFramework=1",
    "https://www.gstatic.com/eureka/clank/149/cast_sender.js"
  ],
  "presentationRequest": "function",
  "userAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
}
```

Network evidence:

- `cast_sender.js?loadCastFramework=1` loaded with HTTP 200.
- `cast_framework.js` loaded with HTTP 304.
- `gstatic.com/eureka/clank/149/cast_sender.js` loaded with HTTP 200.
- `/api/signed-url?renderJobId=...&fileType=video&cast=true` returned HTTP 200.
- The generated R2 MP4 URL returned HTTP 206 range responses in the local video
  element.
- `POST /api/log-client-error` returned HTTP 400 twice.

Visible page state after pressing the focused Cast button:

```text
Toast: "Cast session request failed"
Cast button remains visible as "Send to TV"
```

Telemetry POST body:

```json
{
  "message": "Cast session request failed",
  "kind": "cast_load",
  "meta": {
    "platform": "macos",
    "browser": "chrome",
    "castAppIdMode": "default",
    "castState": "NO_DEVICES_AVAILABLE",
    "sessionState": "NO_SESSION",
    "transportKind": "cast",
    "mediaSourceKind": "songset"
  }
}
```

Telemetry response:

```json
{
  "error": "Invalid request body",
  "details": [
    {
      "code": "unrecognized_keys",
      "keys": ["castState", "sessionState"],
      "path": ["meta"],
      "message": "Unrecognized keys: \"castState\", \"sessionState\""
    }
  ]
}
```

## Interpretation

The current failure is **not** that the Cast Web Sender SDK cannot initialize.
On local HTTPS it initializes correctly:

- secure context is true,
- `window.chrome.cast` exists,
- `window.cast.framework` exists,
- Default Media Receiver app ID resolves to `CC1AD845`,
- CastContext is available.

The current failure is that CastContext reports:

```text
NO_DEVICES_AVAILABLE
```

That means the sender framework has no currently discoverable Cast devices for
the selected receiver app ID. Since `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` is unset,
the app is using Google's Default Media Receiver, which should not need a custom
receiver registration.

The app now also has a diagnostics regression: the hook posts newly-added
`castState` and `sessionState` fields, but `/api/log-client-error` rejects those
fields because its Zod schema is strict and does not include them. This does not
cause discovery to fail, but it discards the exact diagnostics needed to
differentiate `NO_DEVICES_AVAILABLE`, `NOT_CONNECTED`, framework init failure,
and session request errors.

## PR #119 Assessment

The local git history shows:

```text
1abcbc8 fix(webapp): address PR #119 review feedback
3b768f6 fix(webapp): update share landing test
f1305d8 Fix Cast framework initialization
1fd9c0f Improve Cast session failure diagnostics
```

Commit `1abcbc8` touched only:

- `delivery/webapp/src/app/songsets/[id]/play/controller/page.tsx`
- `delivery/webapp/src/components/play/LyricJumpList.tsx`
- `delivery/webapp/src/components/play/ProjectionPlayer.tsx`

It did **not** touch:

- `delivery/webapp/src/hooks/useCast.ts`
- `delivery/webapp/src/lib/cast/loader.ts`
- `delivery/webapp/src/app/api/log-client-error/route.ts`

Based on the inspected files and runtime evidence, PR #119 review feedback is
unlikely to be the direct cause of the current `NO_DEVICES_AVAILABLE` discovery
state.

The Cast-specific changes after that range are more relevant:

1. `f1305d8 Fix Cast framework initialization`
   - changed the sender script URL to include `?loadCastFramework=1`.
   - changed default receiver lookup toward `chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID`.
   - This appears necessary and is working in the live DevTools probe.

2. `1fd9c0f Improve Cast session failure diagnostics`
   - added `castState`, `sessionState`, and `castErrorCode` to client telemetry.
   - did not update the strict server telemetry schema to accept those fields.
   - This explains the observed HTTP 400 diagnostics failure.

## Working Hypotheses

### H1: Browser/device discovery is broken outside the app

Most likely based on DevTools: the SDK is initialized, but Chrome reports
`NO_DEVICES_AVAILABLE`. This can happen when:

- AndroidTV is powered off, asleep, or on a different Wi-Fi/VLAN.
- AP/client isolation, guest Wi-Fi, VPN, firewall, or mDNS/SSDP restrictions
  block discovery.
- Chrome's built-in Cast menu cannot see the TV either.
- The currently controlled Chrome profile/session has Cast discovery disabled or
  blocked.

This is an environment/device condition, not a webapp code path.

### H2: The app is using a different receiver app ID mode than yesterday

Current runtime mode is `default` with receiver app ID `CC1AD845` because
`NEXT_PUBLIC_CAST_RECEIVER_APP_ID` is unset. This should launch the Default Media
Receiver, not a SOW custom receiver.

If yesterday used a custom app ID, discovery may have appeared differently, but
the prompt states the env var is unset today. The plan should verify there is no
stale custom app ID in:

- `.env.local`
- shell env used to start `pnpm dev:https`
- browser bundle output
- Vercel preview/prod envs, if comparing with deployed behavior

### H3: Diagnostics are currently masking the actionable failure detail

Confirmed. The client sends `castState` and `sessionState`, but the API rejects
them. This should be fixed before relying on telemetry to debug more Cast cases.

### H4: The old login-screen blocker is a separate issue

Yesterday's symptom was "discovery worked, but TV hit the login screen." That
matches the earlier local-dev projection fallback/login issue, where non-media
Cast/Presentation paths could load SOW webapp URLs on the receiver without a
session.

Today's observed path is different: the real Cast SDK is loaded and uses Default
Media Receiver mode, but no device is discoverable. The receiver never gets far
enough to load a SOW page or an MP4.

## Clarification Needed

The key external check is:

```text
Can Chrome's built-in toolbar Cast menu see the AndroidTV from this same browser
session right now?
```

Interpretation:

- If **No**, the current failure is almost certainly LAN/device/Chrome discovery
  state outside the webapp.
- If **Yes**, the app's CastContext setup/requestSession path needs deeper
  inspection, because Chrome itself can see receivers while this app cannot.

I attempted to use the structured Question tool for this, but the current Codex
session is not in the mode where that tool is available.

## Remediation Plan

### Phase 1 — Establish External Discovery Baseline

No code changes.

1. From the same Chrome profile used by DevTools, open Chrome's built-in Cast
   menu.
2. Record whether the AndroidTV appears.
3. If it does not appear:
   - confirm AndroidTV is awake and on the same Wi-Fi/VLAN as the Mac,
   - disable VPNs or network filters temporarily,
   - confirm no guest-network/client-isolation mode is active,
   - reboot AndroidTV/Chromecast service if necessary,
   - retry Chrome built-in Cast menu before testing the app.
4. If it appears in Chrome's menu, return to the controller page and run the
   page-context probe again:

   ```js
   ({
     castState: cast.framework.CastContext.getInstance().getCastState(),
     sessionState: cast.framework.CastContext.getInstance().getSessionState(),
     defaultReceiverAppId: chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID,
   })
   ```

Expected app result when the TV is discoverable:

```text
castState: NOT_CONNECTED
sessionState: NO_SESSION
```

`NO_DEVICES_AVAILABLE` after Chrome's own Cast menu sees the AndroidTV would be
an app-level or receiver-app-ID-level discrepancy.

### Phase 2 — Fix Telemetry Contract

Implement after approval.

Files:

- `delivery/webapp/src/app/api/log-client-error/route.ts`
- `delivery/webapp/src/test/api/log-client-error.test.ts`
- optionally `delivery/webapp/src/db/schema.ts` comments/docs if field meaning is
  documented there

Changes:

1. Extend `metaSchema` to accept:
   - `castErrorCode?: string`
   - `castState?: string`
   - `sessionState?: string`
2. Persist those fields in `redactedMeta`.
3. Keep the schema strict after adding the known keys.
4. Add API tests proving:
   - Cast diagnostics with `castState/sessionState/castErrorCode` return 202.
   - Unknown meta keys are still rejected.
   - Existing PII redaction contract remains unchanged.

This does not fix discovery, but it makes the new diagnostics actually usable.

### Phase 3 — Improve Sender-Side Discovery Diagnostics

Implement after approval.

Files:

- `delivery/webapp/src/hooks/useCast.ts`
- `delivery/webapp/src/components/play/ControllerPlayer.tsx`
- `delivery/webapp/src/test/hooks/useCastTransport.test.ts`
- relevant controller/player tests

Changes:

1. Subscribe to `cast.framework.CastContextEventType.CAST_STATE_CHANGED`.
2. Store the latest Cast state separately from generic `availability`.
3. Treat states distinctly:
   - `NO_DEVICES_AVAILABLE`: SDK initialized, but no receivers discovered.
   - `NOT_CONNECTED`: receiver available, no active session.
   - `CONNECTING`: session request in progress.
   - `CONNECTED`: active session.
4. Update user-facing diagnostic copy:
   - for `NO_DEVICES_AVAILABLE`: same Wi-Fi/VLAN, TV awake, Chrome toolbar Cast
     menu comparison, VPN/firewall/client isolation.
   - for SDK unavailable: HTTPS, browser support, gstatic script loading.
   - for session request failure: include sanitized Cast error code.
5. Keep `availability` for coarse button rendering, but do not collapse all
   initialized/no-device states into an opaque "available" or generic failure.

Expected UX:

- If no devices exist, the Cast button should be tappable and open a diagnostic
  sheet that explicitly says no Cast devices were discovered.
- If devices exist, tapping should open the receiver picker/session flow.

### Phase 4 — Verify Default Media Receiver Path

No custom receiver app ID should be required for the current v3 flow.

Verification steps:

1. Ensure `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` is unset or blank.
2. Confirm runtime resolves:

   ```text
   chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID = CC1AD845
   castAppIdMode = default
   ```

3. Start Cast from a rendered songset controller page.
4. Confirm `requestSession()` succeeds after selecting AndroidTV.
5. Confirm `session.loadMedia()` receives:
   - content ID: signed R2 MP4 URL,
   - content type: `video/mp4`,
   - stream type: `BUFFERED`,
   - title metadata.
6. Confirm the TV plays the MP4 directly from R2 and does not navigate to:
   - `/login`,
   - `/songsets/.../play/projection`,
   - `/share/.../play/projection`.

### Phase 5 — Regression Tests

Implement after approval.

Add or update tests for:

1. SDK loader uses `?loadCastFramework=1`.
2. blank `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` uses
   `chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID`.
3. `requestSession` errors include safe Cast state/session diagnostics.
4. `/api/log-client-error` accepts the full Cast diagnostics payload.
5. `NO_DEVICES_AVAILABLE` state produces actionable diagnostic UI.
6. PR #119 touched controller/lyric/projection code does not regress Cast
   initialization or button visibility.

Recommended commands:

```bash
pnpm --filter sow-webapp test -- src/test/hooks/useCastTransport.test.ts
pnpm --filter sow-webapp test -- src/test/api/log-client-error.test.ts
pnpm --filter sow-webapp test
```

### Phase 6 — Manual End-to-End Checklist

Run from `delivery/webapp` or project root:

```bash
pnpm --filter sow-webapp dev:https
```

Checklist:

1. Open `https://localhost:8080` and accept the self-signed certificate.
2. Log in.
3. Open a rendered songset controller page.
4. Confirm page probe:

   ```text
   isSecureContext = true
   hasChromeCast = true
   hasCastFramework = true
   defaultReceiverAppId = CC1AD845
   ```

5. Confirm Chrome's built-in Cast menu sees AndroidTV.
6. Confirm app Cast button either:
   - opens a device picker when devices are available, or
   - opens a clear diagnostic sheet when no devices are discoverable.
7. Select AndroidTV.
8. Confirm TV plays the rendered MP4.
9. Confirm no `/login` page appears on TV.
10. Confirm local telemetry accepts the Cast diagnostics payload with HTTP 202.

## Proposed Implementation Order

1. Fix `/api/log-client-error` schema/persistence for new Cast diagnostic fields.
2. Add CastContext state listener and explicit `NO_DEVICES_AVAILABLE` UI path.
3. Add focused tests for both.
4. Re-run manual DevTools probe with AndroidTV visible from Chrome toolbar.
5. Only if Chrome toolbar sees the TV but the app still reports
   `NO_DEVICES_AVAILABLE`, investigate CastContext options or receiver app ID
   setup further.

## Non-Goals

- Do not reintroduce `androidReceiverCompatible`; the app does not have a native
  Android TV Cast Connect receiver.
- Do not require a custom `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` for the v3 default
  MP4 playback path.
- Do not route Chromecast playback through authenticated SOW projection pages.
- Do not persist raw signed R2 URLs, Cast session IDs, or user IDs in telemetry.

