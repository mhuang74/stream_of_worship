# Chromecast Testing & Deployment Guide

Applies to the v3 Default Media Receiver Cast integration in
`delivery/webapp`. Covers how the feature is wired, how to set up the
devices, how to test on real hardware (laptop/phone → Android TV), and the
production deployment gate.

> Supersedes `docs/research-chromecast-projection.md`, which described the
> pre-v3 W3C Presentation API design. The Presentation API path is now a
> dev-only fallback (`src/hooks/usePresentation.ts`); production guidance is
> Cast on Android/Chrome or AirPlay to Apple TV.

---

## 1. How the feature works

### Architecture (one picture)

```
Pixel 6 (sender)                  Android TV (receiver)        Cloudflare R2
┌─────────────────────────┐        ┌──────────────────┐         ┌──────────┐
│ Chrome                  │        │ "Chromecast       │         │  MP4     │
│  └─ Vercel webapp JS    │        │  built-in" svc    │         │  (H.264  │
│  └─ Cast Web Sender SDK │──mdNS─│                   │──HTTPS──│   +AAC   │
│     (gstatic.com)       │  +Google│ Default Media  │         │   +fst)  │
│                         │  backend│  Receiver       │         └──────────┘
└──────────┬──────────────┘  lookup │                  │                ▲
           │                          └──────────────────┘                │
           │  remote control (play/pause/seek/volume)                    │
           └────────────────────────────────────────────► (session) ─────┘
                                                                  (TV fetches MP4)
```

The webapp (on Vercel) **does not** participate in discovery or in the
media transport. It only:

1. Serves the JS bundle containing the `useCastTransport` hook.
2. Mints a 4-hour presigned R2 URL for the MP4 (so the TV can fetch it).

After that, the Cast Web Sender SDK running in Chrome on the sender device
owns discovery, session management, and the remote-control channel.

### Key code paths

| Concern | File | Notes |
|---|---|---|
| Sender hook | `src/hooks/useCast.ts` | Public surface: `isSupported`, `availability`, `isConnecting`, `isConnected`, `deviceName`, `playerState`, `currentTime`, `duration`, `volume`, `isMuted`, `start`, `stop`, `play`, `pause`, `seek`, `setVolume`, `setMuted`, `onError`, `resumeProposal` |
| SDK loader (injects `cast_sender.js` once) | `src/lib/cast/loader.ts` | Ref-counted singleton, `LOAD_TIMEOUT_MS = 15_000` |
| Cast-vs-Presentation dispatcher | `src/lib/cast/dispatch.ts` | Routes `play`/`pause`/`seek`/`volume`/`mute` (mute never routed via `setVolume(0)`) |
| Controller UI (Cast button + diagnostic sheet) | `src/components/play/ControllerPlayer.tsx` | `cast-button` `data-testid`, 4-line diagnostic bottom sheet, "tap to resume" prompt, AirPlay fallback for iOS |
| Entry: logged-in songset | `src/app/songsets/[id]/play/controller/page.tsx` | Mints `/api/signed-url?...&cast=true` |
| Entry: public share | `src/app/share/[token]/play/controller/page.tsx` | Reads `playback.mp4Url` (already minted with Cast expiry by `/api/share/[token]`) |
| Signed-URL endpoint | `src/app/api/signed-url/route.ts` + `shared-handler.ts` | `cast=true` ⇒ 14400s expiry for video; non-cast 3600s |
| Share-token MP4 minting | `src/app/api/share/[token]/route.ts` | MP4 at `CAST_PLAYBACK_EXPIRES_IN_SECONDS` (14400s) |
| Client error logging | `src/app/api/log-client-error/route.ts` | Anonymous, 20/min per IP, `meta.castAppIdMode = "set"\|"default"\|"unset"`, `transportKind = "cast"`, URL redacted client-side before upload |

### The one Cast env var

```
NEXT_PUBLIC_CAST_RECEIVER_APP_ID
```

- Client-baked (must be set at build time per environment).
- When **blank** (the v3 default), the SDK falls back to
  `chrome.cast.DEFAULT_MEDIA_RECEIVER_APP_ID` — Google's built-in Default
  Media Receiver. No custom receiver HTML exists in this repo and none is
  required for v3 (lyrics are baked into the MP4).
- When set, points to a Custom Receiver app registered in the Cast SDK
  Developer Console. Only needed if a custom on-receiver UI is ever
  reintroduced (legacy/future, see `README.md:245-262`).

The flag that enables targeting Android TV as a Cast Connect receiver is
hard-coded:

```ts
// src/hooks/useCast.ts:549
ctx.setOptions({
  receiverApplicationId: receiverAppId,
  autoJoinPolicy: chrome.cast.AutoJoinPolicy.TAB_AND_ORIGIN_SCOPED,
  androidReceiverCompatible: true,
});
```

No other config is required to support Android TV.

---

## 2. Prerequisites (one-time setup)

### 2.1 Cast SDK Developer Console (https://cast.google.com/publish)

- Sign in with a normal Google account. There is **no fee** — the old $5
  registration was discontinued in 2017.
- Under **Device registration**, **Add a new device** and enter the
  Android TV's **hardware serial number**:
  - On the TV: Settings → About → Status
  - Or via ADB: `adb shell getprop ro.boot.serialno`
- Wait for Google's confirmation email (typically minutes; worst case
  hours). The TV must then be **power-cycled** (unplug power, not just
  standby) so its "Chromecast built-in" service re-fetches the whitelist.

> **Whitelist ≠ Google-account-on-TV.** The whitelist is hardware
> serial-bound to your developer account on the Console side. The TV
> itself may be signed into *any* Google account — it just needs to be
> signed into one so the "Chromecast built-in" service can register with
> Google's backend. The TV's account does not need to match the
> developer account. Recommendation: signing the TV into the same
> developer account is the lowest-friction path, but it is not a hard
> requirement.

### 2.2 Sender devices

| Device | Browser | Supported? |
|---|---|---|
| Pixel 6 (or any Android phone) | **Chrome** | ✅ Required for phone testing |
| Laptop | **Chrome** | ✅ Easiest for debugging |
| Laptop | Vivaldi / Edge / Brave / Opera | ❌ Google's SDK allow-list typically excludes these; the SDK is documented as Android-Chrome-only (`README.md:230-232`, `ControllerPlayer.tsx:1097`) |
| iPhone | Any | ❌ No Web Sender SDK on iOS → AirPlay hint rendered instead (`ControllerPlayer.tsx:951-963`) |
| Laptop | Firefox / Safari | ❌ Not Chromium |

### 2.3 Network topology

- **Sender and TV must be on the same Wi-Fi/VLAN** — discovery happens via
  local mDNS plus Google's backend lookup; isolation breaks the mDNS leg.
- No captive portal, no guest network isolation.
- The TV's network must be able to reach R2 (the public Cloudflare edge)
  to fetch the MP4 — usually trivial but worth verifying.

### 2.4 Rendered MP4 requirements

Enforced by the render worker and verified by `test_mp4_cast_compatibility.py`:

- **H.264 + AAC + `+faststart`** (`video_engine.get_video_codec_args()`
  appends `-movflags +faststart`).
- `moov` atom must precede `mdat` (so the receiver can start playback
  before the full file is buffered).
- Must have a render job with `status === "completed"` in the DB; the
  controller page reads this and refuses to load otherwise.

---

## 3. Discovery: how the phone finds the TV

The webapp itself implements no discovery — it is fully delegated to the
Cast Web Sender SDK after `useCast.ts:546-550` calls `setOptions`. The SDK
uses three mechanisms in parallel:

1. **mDNS / Cast discovery on the LAN** — the SDK multicasts on the local
   network; the "Chromecast built-in" service on Android TV answers with
   friendly name, IP, capabilities.
2. **Google backend lookup over HTTPS** — because Chrome on the sender is
   signed into a Google account, the SDK also queries Google's
   device-discovery endpoint for Cast devices associated with the
   household.
3. **Cast Connect path for Android TV** — `androidReceiverCompatible:
   true` tells the SDK this launch may target a native Android TV
   receiver (rather than only a generic Cast dongle). The TV registers
   itself with Google's Cast backend at boot via the "Chromecast built-in"
   service — which is why the TV being signed into a Google account
   matters for discovery, even though the whitelist itself is serial-based.

The webapp surfaces only the resulting state via `castAvailability`:

| State | What it means | UI |
|---|---|---|
| `"unknown"` | SDK still loading, first discovery scan in progress (~2–5s) | No Cast button rendered (`ControllerPlayer.tsx:803`) |
| `"available"` | ≥1 device on the network that speaks the resolved receiver app id | Cast button renders bright |
| `"unavailable"` | SDK loaded but found zero devices | Cast button renders dimmed; tapping opens the diagnostic sheet |

---

## 4. Where to host the webapp during testing

The webapp can run anywhere — Vercel production, a Vercel preview branch,
or local `pnpm dev`. None of these affect Cast discovery.

| Option | Cast works? | Setup cost | Best for |
|---|---|---|---|
| **Production Vercel URL** | ✅ HTTPS by default | None | First end-to-end test; verifying real R2 path |
| **Vercel preview branch** | ✅ HTTPS by default | Push a branch — auto-deploys via `vercel.json` | Iterating on code; preview is HTTPS, falls back to Default Media Receiver when no per-branch receiver ID is set (`README.md:181-187`) |
| **Local `pnpm dev` on `localhost:8080`** | ✅ localhost is exempt from the HTTPS requirement | `pnpm --filter sow-webapp dev` | Laptop debugging with DevTools breakpoints in `useCast.ts` |
| Local dev accessed via LAN IP | ⚠️ Only with HTTPS | Needs `mkcert` (trusted root) or a `cloudflared` tunnel — the Cast SDK rejects self-signed certs | Avoid unless necessary |
| Local dev reached from the Pixel 6 | ⚠️ Phone can't see `localhost` on the laptop | Required: `cloudflared tunnel --url http://localhost:8080` or pushing a Vercel preview | For phone testing of code changes |

> **The HTTPS requirement is real for non-localhost origins.** The Cast
> Web Sender SDK refuses to init on plain HTTP for non-localhost origins
> (the first line of the diagnostic sheet says exactly this,
> `ControllerPlayer.tsx:1097`). Localhost is exempt. Any LAN IP / Vercel
> preview / production URL needs HTTPS — Vercel already does this.

### Recommendation

1. **First test: production Vercel URL from laptop Chrome** — the
   lowest-friction path. Confirms the whole flow end-to-end against the
   real R2 bucket, real render job, real 4-hour signed URL.
2. **To iterate on code: `pnpm dev` locally on the laptop** — instant
   feedback, DevTools breakpoints in the hook, no deploy needed. Cast
   works on `http://localhost:8080` without HTTPS.
3. **For phone end-to-end testing of code changes: push a Vercel preview
   branch** — gets HTTPS for free; avoids the `cloudflared` dance.

---

## 5. The two test entry points

### 5.1 Logged-in songset controller

```
/songsets/[id]/play/controller
```

- Signed in with a Better Auth session that owns the songset.
- Requires a render job with `status === "completed"`.
- On mount, the page calls
  `GET /api/signed-url?renderJobId=...&fileType=video&cast=true`
  (`src/app/songsets/[id]/play/controller/page.tsx:89-91`) to mint the
  4-hour (14400s) presigned R2 URL.

### 5.2 Public share-token controller

```
/share/[token]/play/controller
```

- No login required — best for testing on someone else's phone.
- `GET /api/share/[token]` mints MP4 at 14400s, MP3 at 3600s, chapters
  JSON at 3600s server-side. The controller reads
  `data.playback.mp4Url` directly
  (`src/app/share/[token]/play/controller/page.tsx:51`).
- Revoked / expired tokens return 410 **before** any URL is minted
  (verified in `src/test/api/share-token-cast-expiry.test.ts`).

Both pages wire up the same `useCastTransport` hook, the same
`ControllerPlayer`, and the same Cast-vs-Presentation fallback rule
(`cast.isSupported` wins; Presentation API runs only when Cast is
unsupported — i.e. iOS or non-Chrome browsers).

---

## 6. Step-by-step manual test (laptop Chrome → Android TV)

The fastest end-to-end test:

1. **Confirm the Android TV is whitelisted** (Section 2.1) and
   power-cycled after the registration email arrived.
2. **Confirm the TV's "Chromecast built-in" / "Google Cast" service is
   enabled** in TV settings and the TV is signed into *some* Google
   account.
3. **Verify same Wi-Fi**: laptop and TV on the same SSID, no guest mode,
   no AP isolation. (You can confirm mDNS works by attempting to
   `ping <TV-IP>` or by opening the TV's IP in a browser, though neither
   is strictly required.)
4. **Pre-check R2 reachability from this network**: in Chrome on the
   laptop, open DevTools → Network tab. Navigate to a songset
   controller page on the production Vercel URL. Watch the
   `/api/signed-url?...&cast=true` call return a JSON `{ url, expiresAt }`
   payload. Paste the `url` field into a new tab and verify:
   - Video plays.
   - Forward/back 10s seeks work (range request support).
   - Reload resumes from the same position.
   If any of these fail, Cast will connect but show a black screen on
   the TV — R2 unreachable or range-seek broken on this network.
5. **Open a controller page** from a completed songset or share token.
   Wait ~2–5 seconds for the Cast button (`data-testid="cast-button"`
   at `ControllerPlayer.tsx:925`) to appear and brighten — this means
   `castAvailability === "available"`, discovery succeeded.
6. **Tap the Cast button → pick the Android TV** in the device picker.
   - Spinner shows while `isCastConnecting` is true.
   - TV launches the Default Media Receiver and starts fetching the MP4
     from R2.
   - Phone shows toast `Connected to <device name>`.
7. **Walk the functional matrix** (Section 8).

### If the Cast button never appears

`castAvailability` stuck at `"unknown"` → the Cast Web Sender SDK failed
to load or initialize. Open DevTools console; common causes:

- Browser not Chrome (Vivaldi/Edge/etc. silently rejected by the SDK
  allow-list).
- Network blocks `https://www.gstatic.com/cv/js/sender/v1/cast_sender.js`.
- `cast_sender.js` loaded but `__onGCastApiAvailable` never fired within
  the 15s `LOAD_TIMEOUT_MS` (`src/lib/cast/loader.ts:27`).

In all cases, the hook POSTs telemetry to `/api/log-client-error` with
`meta.castAppIdMode = "set"|"default"|"unset"` and
`transportKind = "cast"`. Query the `clientErrorLog` table to see what
was actually reported:

```sql
SELECT created_at, kind, message, meta
FROM client_error_log
WHERE transport_kind = 'cast'
ORDER BY created_at DESC
LIMIT 20;
```

### If the Cast button is dimmed

`castAvailability === "unavailable"` → SDK loaded but found zero matching
devices. Tapping the dimmed button opens the diagnostic bottom sheet
(`ControllerPlayer.tsx:1085-1103`) with the 4-check list:

1. Use Android Chrome over HTTPS.
2. Phone and TV on the same Wi-Fi/VLAN.
3. Receiver powered on + whitelisted in the Cast SDK Developer Console.
4. Try opening the MP4 URL from this network in a laptop browser.

---

## 7. Sender device tests

### 7.1 Pixel 6 (Android Chrome) — canonical end-to-end target

Use this for final validation of the `androidReceiverCompatible: true`
path on a real Android sender. Either the production Vercel URL or a
Vercel preview deploy — `localhost` is not reachable from the phone.

### 7.2 Laptop Chrome — easiest debugging

Use this for almost everything else. Full DevTools:

- Breakpoints inside `useCast.ts`.
- Network tab to watch `/api/signed-url?cast=true` and verify the 4-hour
  expiry.
- Application tab to inspect `cast.framework.CastContext` state.
- `console` for hook `console.log` output during the session lifecycle.

`androidReceiverCompatible` is essentially a no-op on a desktop sender
(you're not exercising the Cast-Connect native-receiver path), so for
final sign-off on a *real* Android-→-Android-TV target, the Pixel test
remains the canonical one.

---

## 8. Functional test matrix

Per entry point (songset controller and share-token controller), verify:

| Action | Expected | Code path |
|---|---|---|
| Cast button tap → pick TV | Spinner while `isCastConnecting`; toast `Connected to <device name>`. TV loads MP4. | `useCast.ts:620-707`; controller pages call `toast.success(...)` at `share/[token]/.../page.tsx:135-138` |
| Play | TV resumes playback | `dispatchCast` → `cast.play()` (`src/lib/cast/dispatch.ts:45-50`) |
| Pause | TV pauses | `cast.pause()` |
| Seek 10s forward / back | TV seeks; phone's `currentTime` reflects receiver position | `cast.seek()` with 200ms trailing debounce (`useCast.ts:743-762`) |
| Volume slider | TV volume changes | `cast.setVolume()`, clamped `[0,1]` |
| Mute toggle | TV mutes via `setMuted(true)` — *not* via `setVolume(0)` | `dispatch.ts:58-60` (anti-pattern enforced) |
| Chapter / lyric-line jump | TV seeks to new position | Transport command → `cast.seek()` |
| Background the phone (session pausing) | Cast keeps playing; on return, UI shows `isConnected=true` and current receiver time | `RemotePlayerController.IS_CONNECTED_CHANGED` |
| Disconnect / tap Disconnect | TV stops; phone shows "Tap to resume" with extrapolated TV position | `resumeProposal` populated (`useCast.ts:39-46`) |
| Disconnect after >60s of receiver silence | Phone shows stale variant: "Resume from TV position may be stale — tap to resume at \<time\>" | `STALE_THRESHOLD_SECONDS = 60` (`useCast.ts:149`) |
| Tap Cast button on a no-Cast network | Diagnostic sheet opens with 4 lines | `ControllerPlayer.tsx:1085-1103` |
| iPhone (no Cast support) | AirPlay hint rendered, links to `/docs#airplay` | `ControllerPlayer.tsx:951-963` |

---

## 9. Automated tests (no hardware needed)

Run before each hardware-test round to catch regressions in the wiring:

```bash
# Hooks + dispatcher + loader + signed-URL expiry + controller-pages wiring + UI (Vitest)
cd delivery/webapp
pnpm test \
  src/test/hooks/useCastTransport.test.ts \
  src/test/lib/cast \
  src/test/api/share-token-cast-expiry.test.ts \
  src/test/app/controller-page.test.tsx \
  src/test/components/play/ControllerPlayer.test.tsx \
  src/test/api/log-client-error.test.ts \
  src/test/deployment/deployment.test.ts \
  -v
```

What they cover:

| Test file | Covers |
|---|---|
| `src/test/hooks/useCastTransport.test.ts` | Full hook lifecycle: SDK support detection, Default Media Receiver fallback, `requestSession` resolve/reject/cancel, `loadMedia` success/error, transport dispatch, 200ms seek debounce, disconnect-resume proposals (stale vs fresh), telemetry POST shape + URL redaction, unmount safety |
| `src/test/lib/cast/dispatch.test.ts` | `dispatchCast` routing; mute-vs-`setVolume(0)` anti-pattern; `songTitle` no-op; unknown command no-op |
| `src/test/lib/cast/loader.test.ts` | `loadCastSdk` ref-counted singleton injection, `__onGCastApiAvailable` dispatcher, abort/cancel paths, `isCastSdkSupported` |
| `src/test/api/share-token-cast-expiry.test.ts` | `GET /api/share/[token]` mints MP4 at 14400s (Cast); MP3 + chapters JSON at 3600s default; revoked → 410 before minting; expired → 410 before minting |
| `src/test/api/log-client-error.test.ts` | POST endpoint: zod body schema, anonymized persistence, URL redaction enforcement, 20/min rate limit + 429, optional auth, 202 on DB-write failure |
| `src/test/app/controller-page.test.tsx` | Both controller pages wiring: asserts `cast=true` query sent (`ControllerPlayer` test line 306-331); `cast.isConnected` drives `isPresentationActive`; prefers `cast.start` when supported; Presentation fallback when `cast.isSupported === false` |
| `src/test/components/play/ControllerPlayer.test.tsx` | Renders Cast button when `castAvailability !== "unknown"`; diagnostic bottom sheet content; buffering chip; tap-to-resume; AirPlay fallback |
| `src/test/deployment/deployment.test.ts` | Documentation tests: `.env.production.example` mentions `cast.google.com/publish`, dev/staging/prod app IDs, approval process |

Render-side MP4 ffprobe check:

```bash
cd delivery/render-worker
PYTHONPATH=src pytest tests/ -v
# Specifically: tests/test_video_engine.py (asserts H.264, AAC, moov-at-front)
```

---

## 10. Live-service Go/No-Go checklist

Required before the first live use, on the same TV + network class used in
service. All items must pass. (Tracked as `README.md:266-299`.)

1. **Network topology** — phone + TV on the same Wi-Fi/VLAN; no captive
   portal / guest isolation.
2. **Receiver discoverability** — TV/Chromecast discoverable from the
   phone on the same network + whitelisted in the Cast SDK Developer
   Console for dev/staging.
3. **Signed URL range-seek** — open the MP4 URL from a laptop browser on
   the same network and verify seek (forward/back 10s) + reload succeed.
   Failure ⇒ R2 unreachable from that network ⇒ Cast black screen.
4. **MP4 compatibility on real TV** — freshly rendered MP4
   (post-faststart) starts quickly on the TV, supports 10s range seek,
   chapter jump, lyric-line jump; `ffprobe` pipeline test passes
   (H.264 / AAC / moov-at-front).
5. **Transport on real TV** — play/pause, volume, mute (the mute bit, not
   volume-zero), chapter jump, lyric-line jump all driven from the phone.
6. **Disconnect resume** — resumes local playback from the extrapolated
   TV position; audio un-mutes; tap-to-resume renders if `video.play()`
   rejects. Verify by backgrounding the phone during Cast, then
   disconnecting.
7. **Stale signaling** — when receiver status was silent for >60s before
   disconnect, the "Resume from TV position may be stale — tap to resume
   at \<time\>" prompt renders instead of silent auto-resume.
8. **Diagnostic UX** — on a no-Cast-devices network, tapping the disabled
   Cast button opens the bottom sheet with the 4 diagnostic lines.
9. **Rehearsal** — service-length rehearsal (≥60 min) on the same TV/network
   class with no URL expiry or receiver stalls.
10. **Telemetry** — `POST /api/log-client-error` is reachable from the
    phone, rate-limited (20 req/min per IP), and persists structured
    anonymized rows for one simulated `loadMedia` failure.

---

## 11. Production deployment gate

For dev/staging: **only** the device whitelist is required (Section 2.1).
No Google review. The Default Media Receiver works with whitelisted
devices.

For public production launch: the Cast app must be **submitted for
approval** via the Cast SDK Developer Console → your app → **Submit for
Approval**. Approval typically takes 2–4 weeks (Google's current SLA is
closer to 1–2 weeks; budget conservatively). Until approved, only
whitelisted devices work.

Env vars per environment (one app ID per environment when a custom
receiver is used; blank to use the Default Media Receiver):

```
# .env.production.example
NEXT_PUBLIC_CAST_RECEIVER_APP_ID=         # set per environment
# NEXT_PUBLIC_CAST_RECEIVER_APP_ID=ABCD1234   (dev)
# NEXT_PUBLIC_CAST_RECEIVER_APP_ID=EFGH5678   (staging/preview)
# NEXT_PUBLIC_CAST_RECEIVER_APP_ID=IJKL9012   (production)
```

### Custom Receiver (only if reintroducing custom on-TV UI)

Not required for v3 — the lyrics are baked into the rendered MP4. If a
custom on-receiver UI is ever reintroduced, register it via:

1. Cast SDK Developer Console → **Add New Application** → **Custom
   Receiver**.
2. Receiver URL per environment:
   - Dev: `http://localhost:8080/songsets/<songset-id>/play/projection`
   - Staging/Preview: `https://<preview>.vercel.app/songsets/<songset-id>/play/projection`
   - Production: `https://<prod>.vercel.app/songsets/<songset-id>/play/projection`
3. Save the generated 8-character App ID and set it as
   `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` for that environment.

(See `README.md:245-262` and `.env.production.example:74-95`.)

---

## 12. Common failure modes

| Symptom | Likely cause | Quick check |
|---|---|---|
| Cast button never renders | Sender browser not Chrome, or SDK blocked from loading | DevTools console; check `cast_sender.js` fetch; check `clientErrorLog` rows |
| Cast button renders dimmed | SDK loaded but no devices found | Tap the dimmed button → diagnostic sheet; verify TV power / Wi-Fi / whitelist |
| Cast connects, TV shows black screen | MP4 unreachable from TV's network, or codec incompatible | Open MP4 URL in laptop browser on same Wi-Fi; run `ffprobe` on the render |
| Cast session disconnects silently after a minute | TV firmware quirk, or receiver crash on the MP4 | Try a different MP4; check Android TV system logs via `adb logcat \| grep -i cast` |
| URL expiry mid-service (>4h) | `CAST_PLAYBACK_EXPIRES_IN_SECONDS = 14400` exceeded | Stop + re-cast to mint a fresh URL; consider raising the constant only if deliberate |
| iPhone shows AirPlay hint instead of Cast button | Web Sender SDK is Android-Chrome-only | Expected — use AirPlay to Apple TV; native iOS sender app is future work |
| "Tap to resume" shows stale message | Receiver silent for >60s before disconnect | Expected (`STALE_THRESHOLD_SECONDS = 60`, `useCast.ts:149`); tap to resume at the extrapolated position |

---

## 13. References

- Sender hook: `delivery/webapp/src/hooks/useCast.ts`
- Cast dispatcher: `delivery/webapp/src/lib/cast/dispatch.ts`
- SDK loader: `delivery/webapp/src/lib/cast/loader.ts`
- SDK type declarations: `delivery/webapp/src/types/cast-sdk.d.ts`
- R2 client (signed-URL minting): `delivery/webapp/src/lib/r2/client.ts`
- Controller UI: `delivery/webapp/src/components/play/ControllerPlayer.tsx`
- Songset controller page: `delivery/webapp/src/app/songsets/[id]/play/controller/page.tsx`
- Share-token controller page: `delivery/webapp/src/app/share/[token]/play/controller/page.tsx`
- Signed-URL endpoint: `delivery/webapp/src/app/api/signed-url/route.ts` + `shared-handler.ts`
- Share-token route: `delivery/webapp/src/app/api/share/[token]/route.ts`
- Client error logging: `delivery/webapp/src/app/api/log-client-error/route.ts`
- Webapp README Cast section: `delivery/webapp/README.md:189-299`
- Vercel deploy notes: `delivery/webapp/DEPLOY-VERCEL.md:156-195`
- Env var reference: `delivery/webapp/.env.production.example:44-95`
- Render worker MP4 compatibility test: `delivery/render-worker/tests/` (ffprobe asserts H.264 / AAC / moov-at-front)
- Cast SDK Developer Console: https://cast.google.com/publish
- Google Cast SDK documentation: https://developers.google.com/cast
