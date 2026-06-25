# Implementation Plan: Consolidated Chromecast Projection v3 (Cast SDK + AirPlay + Presentation API Fallback)

> **v3 revision.** This file is a new version of `consolidated-chromecast-projection-plan-v2.md`, rewritten to incorporate the reviewer feedback in `reports/chromecast-projection-plan-v2-review.md`. The v1/v2 plan files and the three prior standalone plans are left unmodified. v3 is non-destructive; do not edit the prior plan files.

## What Changed From v2 (Summary)

| Review concern | P | Resolution in v3 |
|---|---|---|
| #R1 Receiver choice / docs mismatch (Custom vs Default) | P0 | Converge on **Default Media Receiver only**. Rewrite README + deploy docs + `.env.production.example`. Quarantine existing Custom Receiver registration guidance under a clearly-labeled "Legacy / future custom receiver" section. Projection route remains for the Presentation API dev fallback only — it is NOT loaded by the Cast receiver. |
| #R2 MP4 compatibility as release gate (codec, faststart, range-seek) | P0 | Add `-movflags +faststart` to `video_engine.get_video_codec_args()`. Add an automated ffprobe-based render-pipeline test asserting H.264 video / AAC audio / `moov` atom at the front. Extend Phase 11 manual validation with first-frame startup, 10s range seek, chapter jump, and lyric-line jump on real Google TV hardware. |
| #R3 Disconnect resume still stale (last `currentTime`) | P1 | Track `lastStatusAtMs` alongside `currentTime`/`playerState`. On disconnect, if last known state was `"playing"`, extrapolate `resumeTime = lastCurrentTime + (Date.now() - lastStatusAtMs)/1000`, clamped to `[0, duration]` and capped at `+60s`. If status older than 60s, show a "Resume from TV position may be stale" prompt instead of silently resuming. |
| #R4 Local autoplay recovery fails silently | P1 | On `video.play()` rejection during disconnect resume, render a prominent inline "Tap to resume at \<time\>" control with the seek already applied. Never silent. |
| #R5 Buffering command queue underspecified | P1 | Define queued = latest-wins deterministically: seek latest-wins; play/pause latest-wins; volume/mute immediate-if-possible else latest-wins; one visible pending state (not per-tap toasts). If receiver buffering > 15s, show actionable copy (Wi-Fi / MP4 reachability / retry Cast). |
| #R6 Cast-unavailable UX tooltip-only | P1 | Replace tooltip with a tap-to-open diagnostic bottom sheet listing: use Android Chrome on HTTPS; phone + TV same Wi-Fi/VLAN; receiver powered on + whitelisted for dev/staging; "try opening the MP4 URL from this network". |
| #R7 Telemetry rate limiting fragile on serverless | P1 | Use `@upstash/ratelimit` + `@upstash/redis` distributed token bucket (20 req/min/IP hash). Add structured fields: browser, platform, cast app ID mode, transport kind, error kind, media source kind, URL-expired flag. Never log signed URLs (host + path + expiry age only). |
| #R8 Presentation API should stay dev-only | P2 | Label Presentation API as dev-only / browser-projection fallback **in docs only** (README + deploy docs). The on-screen Send-to-TV button copy stays neutral; no UI label change. Production guidance = Cast on Android/Chrome, or AirPlay to Apple TV for iPhone. |
| #R9 Test plan may be over-brittle; needs real-device gate | P2 | Keep the planned Vitest coverage for state transitions / command semantics. Add a new **Live-Service Go/No-Go Checklist** as the hard pre-service gate (separate section). Require a service-length rehearsal on the same TV/network class used in service. |

All v2 fixes (#1–#14 from the v2 table) are carried forward unchanged into v3. This document supersedes v2 where the two conflict; where v3 is silent, v2 applies.

## Goal

Smooth and responsive playback and jump-to-chapter/jump-to-lyrics navigation from a phone casted to a large TV, with easy connect and robust stay-connected behavior during worship, on current-generation Google TV / Chromecast hardware, for services under 2 hours. Android Chrome → Chromecast/Google TV is the supported path. iPhone web casting to non-Apple TV is not feasible and is deferred to a native iOS Cast sender app (future work).

## Decisions (confirmed)

| Decision | Choice |
|---|---|
| Primary transport (Android) | Google Cast Web Sender SDK with `chrome.cast` media APIs (`loadMedia`, `RemotePlayer`, `RemotePlayerController`). |
| Receiver app | Google **Default Media Receiver only**. Lyrics are baked into the MP4; no custom Cast receiver UI. The `/play/projection` route is **not** used by the Cast receiver — it is the Presentation API dev fallback target only. |
| Receiver app ID | Single env var `NEXT_PUBLIC_CAST_RECEIVER_APP_ID`; when unset in dev, fall back to Google's Default Media Receiver constant. Custom Receiver registration is documented as **Legacy / future** and is not required for this milestone. |
| Sync source of truth | Cast receiver media status is the source of truth for position, playerState, volume, mute. Phone UI reconciles to receiver status events. Phone commands intent (play/pause/seek/volume/mute/song). No silent reconnect-induced seeks hit the TV. |
| Disconnect → local resume (P0, hardened in v3) | On Cast disconnect: (a) compute `resumeTime` from last known `currentTime` + `lastStatusAtMs` extrapolation when last state was `playing`, capped at `+60s` and clamped to duration; (b) seek the local `<video>` to that time; (c) attempt `video.play()`; (d) **if `play()` rejects, show an inline "Tap to resume at \<time\>" control**. If status is older than 60s, do not silently resume — show a "Resume from TV position may be stale" prompt. |
| Mute command model | `PresentationCommand` gains `{ type: "mute"; muted: boolean }`. Cast dispatch routes this to `cast.setMuted()`; Presentation fallback simulates mute via volume level. |
| Seek guard | 200ms trailing debounce on `useCastTransport.seek()`. |
| Buffering UX + command policy | Non-blocking "TV is loading…" chip when `playerState === "buffering"`. **Queued commands are deterministic latest-wins**: seek latest-wins; play/pause latest-wins; volume/mute immediate-if-possible else latest-wins; one visible pending state, no per-tap toasts. If receiver buffering > 15s, show actionable copy. |
| Cast-unavailable UX | Tapping the disabled Cast button opens a diagnostic bottom sheet (not a tooltip). |
| Failed-command feedback | Transport errors / `loadMedia` failures surface as toasts AND are POSTed to `/api/log-client-error` (Upstash distributed rate-limit, structured fields, PII redacted). |
| iPhone | Web UI shows "Chromecast not supported on iPhone web — use AirPlay to an Apple TV, or wait for the native iOS app." No broken iPhone Chromecast flow. |
| Presentation API fallback | Retained as a **dev-only / browser-projection fallback** (docs-labeled). Used for laptop-to-laptop dev/direct browser projection when Cast is unavailable. Try Cast first; fall back to Presentation API. |
| R2 signed MP4 URL expiry | 4 hours (14400s) for Cast playback URLs and share-mp4 URLs. |
| Rendered MP4 compatibility | Render worker emits H.264 (`libx264`) video + AAC audio + `-movflags +faststart`. Automated ffprobe-based pipeline test. |
| Telemetry rate limiting | `@upstash/ratelimit` + `@upstash/redis` distributed token bucket (20 req/min per IP hash). Structured fields. |
| Real-device release gate | New Live-Service Go/No-Go Checklist section; required before first live use. |
| Future work | Native iOS Cast sender app; periodic drift correction only if real-world drift observed; custom Cast receiver overlay only if lyrics stop being baked into MP4; automated chapter-timestamp drift check in the render pipeline. |

## Scope

### In Scope

- Ambient TypeScript declarations for the Google Cast Web Sender SDK surface.
- Ambient `.d.ts` for the W3C Presentation API surface (clean up existing `@ts-expect-error`).
- A client-only Cast SDK loader (`loader.ts`) that injects the sender script once, honors `window.__onGCastApiAvailable`, reports clear unsupported state, and is unmount-safe.
- `useCastTransport({ media })` sender hook wrapping `cast.framework.CastContext` + `RemotePlayer` + `RemotePlayerController`, with: module-level `setOptions` singleton guard, `loadMedia`-failure session teardown, 200ms seek debounce, `lastStatusAtMs` tracking for disconnect extrapolation, `onError` callback, and caller-facing `resumeProposal` (time + isStale flag) for the controller to act on.
- Extend `usePresentationReceiver` to return `sendStatus`; use ambient types; remove `@ts-expect-error`.
- Plumb both controller pages (`songsets/[id]/play/controller`, `share/[token]/play/controller`) to own the Cast transport and Presentation fallback.
- Replace the dead `window.postMessage` presentation plumbing.
- Update `ControllerPlayer`: transport props; Cast button; reconciliation from receiver status; buffering chip with latest-wins queue semantics; **disconnect-resume seek with extrapolation + tap-to-resume fallback + stale prompt**; diagnostic bottom sheet for unavailable state; mute command; iPhone copy.
- Remove Presentation API launch/availability ownership from `PrePlayCard`.
- Extend R2 signed URL expiry to 14400s for Cast/share playback URLs; verify the `cast=true` auth-free path.
- iPhone fallback copy in `ControllerPlayer`.
- `/api/log-client-error` endpoint with Upstash distributed rate-limit + structured fields + PII redaction; minimal Drizzle migration for `client_error_log`.
- **Render worker change:** add `-movkeys +faststart`/`-movflags +faststart` to `video_engine.get_video_codec_args()`; add an ffprobe-based pipeline test asserting H.264/AAC/moov-at-front.
- Docs: Default Media Receiver only; whitelisted dev/test devices; 4-hour URL expiry; pre-service network test; Presentation API labeled dev-only.
- Focused tests for transport behaviors.
- New **Live-Service Go/No-Go Checklist** section.

### Out of Scope

- Native iOS Cast sender app (future milestone).
- Custom Cast receiver HTML/JS app (Default Media Receiver suffices).
- Custom ack protocol, sequence IDs, or durable session state in DB other than `client_error_log`.
- Storage / render pipeline changes beyond the faststart flag + ffprobe test.
- Controller/projection visual redesign beyond the top-bar Cast button, buffering chip, tap-to-resume control, and diagnostic bottom sheet.
- Automated chapter-timestamp drift validation inside the render pipeline (manual QA step only; future work).
- Google Cast production approval (dev/test-plan gate, not a prerequisite).
- Rewriting the on-screen Send-to-TV button label (Presentation API stays dev-only in docs only, not in UI copy).

## Transport Architecture

```
                  ┌─────────────────────────┐
   Worship leader │  Controller page (web)  │
   phone (Android │  - useCastTransport      │   PRIMARY  (Android Chrome)
   Chrome)        │    (Cast SDK + loadMedia)│ ────────────────────────────▶ TV (Chromecast/Google TV)
                  │  - usePresentationSender │   FALLBACK (dev-only browser
                  │    (Presentation API)    │   projection when Cast unsupported) ──▶ projection route
                  └────────────┬─────────────┘
                               │ reconcile on-phone UI from
                               │ RemotePlayerController events
                               ▼
               Phone-local video element is paused+muted while cast active
               [P0, hardened] On disconnect:
                 resumeTime = lastCurrentTime + (now - lastStatusAtMs)/1000
                              (only when last state was "playing",
                               capped at +60s, clamped to duration)
                 seek local video to resumeTime
                 try video.play()
                   └─ on rejection: show inline "Tap to resume at <time>"
                 if status older than 60s: show "may be stale" prompt, do not auto-resume
```

- **Phone → TV (intent):** Cast: `CastSession.loadMedia`, `RemotePlayerController.play/pause/seek/setVolumeLevel/muteOrUnmute`. Presentation fallback: `PresentationConnection.send(JSON)`.
- **TV → Phone (status, source of truth):** `RemotePlayerController` event listeners (`currentTime`, `playerState`, `duration`, `volume`, `isMuted`, `displayName`). The phone UI slider/time/playing state always reflects the receiver's actual state. Each event also refreshes `lastStatusAtMs = Date.now()`.
- **TV on phone reconnect (Wi-Fi blip):** TV keeps playing; phone re-subscribes and re-syncs its UI to the TV's current position. No seek is forced on the TV unless the worship leader commands one.
- **TV when worship leader taps seek/prev-song/next-song/lyric-line-jump:** commanded intent → TV seeks (correct and expected). Seek debounced 200ms client-side.
- **On disconnect (P0, hardened):** extrapolate, seek local, attempt `play()`, surface tap-to-resume on rejection, surface stale prompt when status is too old.
- **On `loadMedia` failure (P1):** `CastContext.endCurrentSession()` returns the user to a clean disconnected state; retry is possible.

## Contract Types

In `delivery/webapp/src/types/presentation-api.d.ts`:

```ts
export type PresentationCommand =
  | { type: "play" }
  | { type: "pause" }
  | { type: "seek"; positionSeconds: number }
  | { type: "volume"; level: number }
  | { type: "mute"; muted: boolean }       // [P0] dedicated mute command
  | { type: "songTitle"; title: string }; // Presentation-only; Cast dispatch no-ops

export type PresentationStatus =
  | { type: "ready" }
  | { type: "disconnected" }
  | { type: "error"; message: string };
```

In `delivery/webapp/src/types/cast-sdk.d.ts` (narrow surface):

```ts
declare namespace chrome.cast { /* media + session surface used */ }
declare namespace cast.framework {
  class CastContext { /* getInstance, setOptions, addEventListener, endCurrentSession, requestSession */ }
  class RemotePlayer { /* currentTime, duration, volume, isMuted, playerState, displayName, isMediaLoaded, canPause, canSeek */ }
  class RemotePlayerController {
    constructor(player: RemotePlayer);
    addEventListener(/* CastEventType */, handler);
    removeEventListener(/* CastEventType */, handler);
    play(); pause(); seek(); setVolumeLevel(); playOrPause(); muteOrUnmute();
  }
}
```

Listen only to event types the app uses: `CURRENT_TIME_CHANGED`, `PLAYER_STATE_CHANGED`, `IS_MEDIA_LOADED_CHANGED`, `VOLUME_LEVEL_CHANGED`, `IS_MUTED_CHANGED`, `IS_CONNECTED_CHANGED`. Keep declarations narrow.

## Cast Command Dispatch (`dispatchCast`)

`delivery/webapp/src/lib/cast/dispatch.ts` exports `dispatchCast(cast: CastTransportResult, cmd: PresentationCommand): void`. Invariant:

- `play` → `cast.play()`
- `pause` → `cast.pause()`
- `seek` → `cast.seek(cmd.positionSeconds)` (debounced inside the hook)
- `volume` → `cast.setVolume(cmd.level)`
- `mute` → `cast.setMuted(cmd.muted)` // routes to the mute bit, NOT volume 0
- `songTitle` → **no-op** (Cast title set via `MediaInfo` metadata at `loadMedia`; documented invariant)
- unknown → **no-op** (defensive)

## Validation Rules (shared by both transports)

- Ignore malformed JSON.
- Ignore unknown `type` values.
- `seek.positionSeconds` must be finite and `>= 0`.
- `volume.level` must be finite; clamp to `[0, 1]` before invoking callbacks.
- `mute.muted` must be a boolean; coerce non-booleans via `Boolean(...)`.
- `songTitle.title` and `error.message` must be strings.
- `send()` should no-op when no connected transport exists.

## Implementation Phases

### Phase 1 — Ambient Types

- Create `delivery/webapp/src/types/presentation-api.d.ts` (Presentation API surface, `PresentationStatus`, new `mute` command).
- Create `delivery/webapp/src/types/cast-sdk.d.ts` (narrow Cast SDK surface).
- Remove all 6 `@ts-expect-error` annotations across `usePresentation.ts` (4) and `PrePlayCard.tsx` (2). The 2 in PrePlayCard go away in Phase 7 when Presentation API code is removed from that file.

### Phase 2 — Cast SDK Loader

Create `delivery/webapp/src/lib/cast/loader.ts`:

- `loadCastSdk(opts?: { signal?: AbortSignal }): Promise<void>` injects `https://www.gstatic.com/cv/js/sender/v1/cast_sender.js` once (script-tag ref-counted singleton).
- Sets `window.__onGCastApiAvailable = (loaded) => { ... }` before injection; resolves on `loaded=true`, rejects on `loaded=false`.
- SSR-safe: bail when `typeof window === "undefined"`.
- **Unmount-safe:** module-level `cancelled` set keyed by request id; if the `AbortSignal` aborts before the global callback fires, the promise resolves silently and never schedules React state updates on a dead tree.
- Exposes `isCastSdkSupported()` returning true only when `!!window.chrome?.cast && !!window.cast?.framework` (the `navigator.presentation` artifact condition is removed).

### Phase 3 — `useCastTransport` Hook

`delivery/webapp/src/hooks/useCast.ts`. Returns:

```ts
interface CastTransportMedia {
  videoUrl: string;
  title: string;
  startSeconds?: number;
  autoplay?: boolean;
  source: { kind: "songset" | "share"; idOrToken: string };
}

interface ResumeProposal {
  time: number;       // extrapolated resume time in seconds
  isStale: boolean;   // true when lastStatusAtMs older than 60s threshold
  lastState: "playing" | "paused" | "unknown";
}

interface CastTransportResult {
  isSupported: boolean;
  isAvailable: boolean;       // device availability via CastContext
  isConnecting: boolean;
  isConnected: boolean;
  deviceName: string | null;
  playerState: "idle" | "buffering" | "playing" | "paused" | "unknown";
  currentTime: number;        // from RemotePlayer, source of truth
  lastStatusAtMs: number | null;   // [v3] for disconnect extrapolation
  duration: number;
  volume: number;
  isMuted: boolean;
  bufferingSinceMs: number | null;   // [v3] for >15s actionable copy
  lastError: string | null;
  resumeProposal: ResumeProposal | null;  // [v3] populated on disconnect
  start: () => Promise<void>;
  stop: () => Promise<void>;
  play: () => void;
  pause: () => void;
  seek: (seconds: number) => void;       // debounced 200ms, latest-wins
  setVolume: (level: number) => void;
  setMuted: (muted: boolean) => void;
}
```

Behavior:

- Disabled when no `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` AND no dev default.
- **Module-level singleton guard:** `CastContext.getInstance()` called once per page load; `setOptions({ androidReceiverCompatible: true, autoJoinPolicy: "tab_and_origin_scoped", receiverApplicationId })` runs at most once via module-level `let castContextInitDone = false`.
- Create one `RemotePlayer` + one `RemotePlayerController`; attach all status event listeners once.
- **Unmount-safe:** on unmount, remove all listeners and mark the loader request cancelled; never schedule state updates post-unmount.
- On `start()`:
  1. `requestSession()` (user gesture from the controller Cast button).
  2. Build `chrome.cast.media.MediaInfo` (content type `video/mp4`, metadata title, stream type `BUFFERED`).
  3. Set `currentTime = startSeconds` in `LoadRequest`.
  4. `session.loadMedia(loadRequest)`.
  5. **On `loadMedia` rejection:** call `CastContext.endCurrentSession(true)`; set `isConnected=false`, `isConnecting=false`, `lastError=<message>`; emit a toast via `onError`; do NOT leave a dangling session. Retry is possible.
- On cast `IS_CONNECTED_CHANGED -> false`: emit `isConnected=false`, clear device name; **compute and populate `resumeProposal`** using the extrapolation rule below, preserving the last known `currentTime` and `lastStatusAtMs` so `ControllerPlayer` can act on it (Phase 6).
- On `PLAYER_STATE_CHANGED` / `CURRENT_TIME_CHANGED` / `VOLUME_LEVEL_CHANGED` / `IS_MUTED_CHANGED`: update state fields AND `lastStatusAtMs = Date.now()` (drives phone UI reconciliation — no seeks issued back to TV).
- **v3 extrapolation rule (on disconnect):** compute `resumeProposal` as follows and expose it on the result:
  - If `lastStatusAtMs == null` or last `playerState` was not `"playing"`: `resumeProposal = { time: currentTime, isStale: false, lastState: <actual> }`.
  - Else: `elapsed = (Date.now() - lastStatusAtMs) / 1000`. If `elapsed > 60`: `isStale = true`, `time = currentTime + 60` (clamped to duration). Else: `isStale = false`, `time = clamp(currentTime + elapsed, 0, duration)`.
- `seek(seconds)`: clamp to `[0, duration]`; apply a **200ms trailing debounce**, latest-wins.
- `setVolume(level)`: clamp `[0, 1]`; call `controller.setVolumeLevel()`.
- `setMuted(muted)`: call `controller.muteOrUnmute()` — never `setVolume(0)`.
- **Buffering tracking:** on `PLAYER_STATE_CHANGED -> "buffering"`, set `bufferingSinceMs = Date.now()`; on transition out, clear it. The controller reads this for the >15s actionable copy.
- `stop()`: `CastContext.endCurrentSession()`.
- On any transport error path (`loadMedia` failure, `requestSession` exception, receiver error event): set `lastError`, fire `onError(message)` (controller wires a toast), and POST the error to `/api/log-client-error` with structured fields (Phase 12).
- Cleanup: remove all listeners, release references, never throw.
- Do not persist signed URLs, Cast session IDs, or device names to the database.

### Phase 4 — Presentation API Sender Refactor (Dev-Only Fallback)

Add `usePresentationSender` to `delivery/webapp/src/hooks/usePresentation.ts`, used only when `useCastTransport` reports `isSupported=false` (Cast unavailable) or when explicitly operating in browser-to-browser dev mode.

Wire:

- `sender.isConnected` → `isPresentationActive`
- `sender.send(command)` → issued commands flow through the Presentation fallback transport only when Cast is inactive.
- `sender.send({ type: "mute", muted })` simulates mute via volume level on the Presentation receiver (acceptable per review; Cast uses the real mute bit).

Extend `usePresentationReceiver`:

- Use ambient types; drop `@ts-expect-error`.
- Add small validator; clamp volume to `[0, 1]`; coerce `mute.muted` to boolean.
- Return `sendStatus(status: PresentationStatus) => void`.

`ProjectionPlayer` changes:

- Call `sendStatus({ type: "ready" })` on `loadedmetadata`/`canplay`.
- Call `sendStatus({ type: "error", message })` only on transport-relevant `video.play()` rejections.
- On `error` status, the controller shows a toast "TV projection failed — check connection".
- Otherwise unchanged.

### Phase 5 — Controller Pages

Both `delivery/webapp/src/app/songsets/[id]/play/controller/page.tsx` and `delivery/webapp/src/app/share/[token]/play/controller/page.tsx`:

- Remove the dead `window.addEventListener("message", ...)` block.
- Remove stub `handlePresentationConnect` / `handlePresentationDisconnect` callbacks.
- Compute media payload:
  - `presentationUrl = /songsets/${songsetId}/play/projection` (or share token equivalent) for the Presentation fallback.
  - `media = { videoUrl, title, source: { kind, idOrToken }, startSeconds: 0 }` for the Cast transport.
- Mount `const cast = useCastTransport({ media, onError: (m) => toast(...) })`.
- Mount `const sender = usePresentationSender({ presentationUrl, onConnected, onDisconnected })` as fallback; `sender.send` is only invoked when `!cast.isSupported`.
- Pass unified transport props to `ControllerPlayer` (see Phase 6 interface).
- Toast notifications only from transport lifecycle callbacks (`cast.onConnected`, `cast.onDisconnected`, `cast.onError`, `sender.onStartError`).
- For share mode: ensure `presentationUrl = /share/${token}/play/projection` (no auth on TV).
- The Cast `media.videoUrl` must be the signed R2 URL returned by `/api/signed-url?cast=true` (no session cookie required). The controller fetches this URL via an auth-free fetch path (token/songset ID validation only).

### Phase 6 — `ControllerPlayer` Updates

```ts
interface ControllerPlayerProps {
  playerId: string;
  videoSrc: string;
  chapters: Chapter[];
  transport?: CastTransportResult;
  presentationFallback?: UsePresentationSenderResult;
  isPresentationActive?: boolean;
  isCastSupported?: boolean;
  castAvailability?: "unknown" | "available" | "unavailable";
  isCastConnecting?: boolean;
  onSendToTV?: () => Promise<void> | void;
  onSendTransportCommand?: (command: PresentationCommand) => void;
  exitRoute?: string;
  autoFullscreen?: boolean;
  className?: string;
}
```

Changes:

- Remove `onPresentationConnect` / `onPresentationDisconnect` props.
- Top bar:
  - "Connected to {transport.deviceName ?? 'TV'}" badge when `isPresentationActive=true`.
  - **Buffering chip:** when `transport.playerState === "buffering"` AND `isPresentationActive`, show a non-blocking "TV is loading…" chip. Controls remain enabled (commands queue latest-wins). If `transport.bufferingSinceMs` is more than 15s ago, show actionable copy: "TV is still loading — check Wi-Fi / MP4 reachability / retry Cast."
  - Cast/Send-to-TV button (`Monitor` icon) visible when `isCastSupported=true` and `!isPresentationActive`.
  - Spinner state when `isCastConnecting=true`.
  - **[v3] Cast-unavailable diagnostics:** when `castAvailability="unavailable"`, the button is disabled but tappable; tapping opens a diagnostic bottom sheet listing:
    - Use Android Chrome on HTTPS.
    - Phone and TV must be on the same Wi-Fi/VLAN.
    - Receiver must be powered on and whitelisted for dev/staging.
    - "Try opening the MP4 URL from this network."
  - Hidden when `isCastSupported=false` (iPhone): show a small AirPlay hint linking to docs explaining iPhone uses AirPlay to an Apple TV; native iOS app pending.
- **Reconcile on-phone UI from Cast status** when `transport.isConnected`:
  - The slider/time/playing indicator reflects `transport.currentTime` / `transport.playerState` (source of truth from the receiver). The phone-local video element is paused and muted.
- **Forward user intent** via `onSendTransportCommand?.(...)` guarded by `isPresentationActive`, with latest-wins queueing during buffering:
  - `handlePlayPause` → `send({type: isPlaying ? "pause" : "play"})`
  - `handleSeek(t)` → `send({type:"seek", positionSeconds: clamp(t, 0, duration)})`
  - `handleVolumeChange(v)` → `send({type:"volume", level: clamp(v, 0, 1)})`
  - `handleToggleMute` → `send({type:"mute", muted: !currentlyMuted})` (NOT a volume-level command).
  - `handleSkipBack/Forward/PrevSong/NextSong/JumpToChapter/JumpToLine` all funnel through `handleSeek`, so they become seeks on the TV. Debounced 200ms client-side.
- **Disconnect → local resume (P0, hardened):** an effect keyed on `isPresentationActive` falsification (where `transport` was previously connected):
  1. Read `transport.resumeProposal`.
  2. If `resumeProposal.isStale`: show a "Resume from TV position may be stale — tap to resume at \<time\>" prompt; do NOT auto-resume.
  3. Otherwise: seek the local `<video>` to `resumeProposal.time` (clamped to `[0, video.duration]`).
  4. Attempt `video.play()`.
  5. **On `play()` rejection:** render a prominent inline "Tap to resume at \<resumeProposal.time\>" control with the seek already applied. Never silent.
- **Song-change effect** keyed on `currentSongIndex` while `isPresentationActive`: `send({type:"songTitle", title: currentChapter?.songTitle})`. (For Cast, song title is already set via media metadata at `loadMedia`; this is a no-op for Cast.)
- Keep existing "mute local video when presentation active" effect — it composes with the disconnect-resume effect above.
- Keep existing "hide LyricJumpList when presentation active" behavior.
- For iPhone (`!isCastSupported` AND `presentationFallback.isSupported=false`): show fallback copy.

### Phase 7 — `PrePlayCard` Cleanup

- Delete: `isPresentationAvailable` / `isCastAvailable` state, `checkPresentationAvailability` effect, `handleSendToTV`, the `<Monitor>` Send-to-TV button block, both `new PresentationRequest([...])` calls, and the `Monitor` import.
- Delete the 2 `@ts-expect-error` annotations.
- Keep Start Worship, Share, render status, song list, offline status behavior unchanged.
- PrePlayCard no longer does any Presentation/Cast API detection or launch — the controller page owns all of that.

### Phase 8 — R2 Signed URL Expiry + Auth-Free Cast Path

- `src/lib/r2/client.ts` — keep default at 3600 for non-cast artefacts; expose explicit `CAST_PLAYBACK_EXPIRES_IN_SECONDS = 14400` constant.
- `src/app/api/share/[token]/route.ts` — change share MP4 mint to `expiresInSeconds: CAST_PLAYBACK_EXPIRES_IN_SECONDS` (14400). Preserve share revocation + expiry checks before returning any URL.
- Ensure Cast-targeted URLs go through a path that picks the 4-hour expiry (e.g., add `cast=true` query param on `/api/signed-url`, validated with zod; only that path uses 14400). Non-cast signed URLs stay at 1 hour.
- Verify `/api/signed-url?cast=true` does **not** require a session cookie — only token/songset ID validation. The TV has no session cookies. (Automated test in Phase 10.)
- Document: signed URL must cover full set + setup time; if a service runs longer than ~3h40m, callers must re-mint (deliberate stop/re-cast).

### Phase 9 — Render Worker MP4 Compatibility (NEW in v3)

- `delivery/render-worker/src/sow_render_worker/video_engine.py:129` — extend `get_video_codec_args()` to include `-movflags +faststart` (places the `moov` atom at the front for progressive playback / fast startup / fast seeking on TV hardware).
- Re-run existing render-worker tests to ensure the new flag does not break the pipeline command construction (`tests/test_video_engine.py`).
- Add an ffprobe-based pipeline test (NEW): `delivery/render-worker/tests/test_mp4_cast_compatibility.py` that asserts on a sample render output:
  - Video codec is H.264 (`codec_name == "h264"`).
  - Audio codec is AAC (`codec_name == "aac"`).
  - `moov` atom is at the front (faststart present): probe with `ffprobe -v error -show_entries format=tags` or check atom order via a lightweight parser. Acceptance: `moov` appears before `mdat`.
  - `Content-Type`/upload metadata remains `video/mp4`.
- Keep resolution at the current render target (1080p unless a separate 4K requirement is filed). No change to audio engine.

### Phase 10 — Docs / Operational

- Update `delivery/webapp/README.md` Cast section:
  - **[v3 P0]** Default Media Receiver is the only supported mode in this milestone. Remove the "Register a Custom Receiver" step from the main setup flow. Move existing Custom Receiver registration instructions under a clearly-labeled "Legacy / future custom receiver" subsection with a note that it is not required for v3 and is only relevant if/when lyrics stop being baked into the MP4.
  - One stable `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` per environment (dev, staging, prod), OR omit and use Google's Default Media Receiver constant.
  - Whitelisted dev/test devices in Google Cast SDK Developer Console.
  - Production Cast approval = later launch gate, not a prerequisite for this dev/test plan.
  - iPhone web does not support Chromecast; use AirPlay to an Apple TV; native iOS sender app future work.
  - Lyrics are baked into the MP4 already; no custom Cast receiver UI needed.
  - 4-hour signed URL expiry during Cast playback.
  - **[v3 P0]** Rendered MP4 must be H.264 video + AAC audio + faststart. This is enforced by the render worker (`-movflags +faststart`) and verified by the ffprobe pipeline test.
  - **[v3]** Pre-service network test: open the signed MP4 URL directly in a laptop browser on the same Wi-Fi/VLAN as the Chromecast and verify range-seek works (seek forward/back 10s, reload). If it fails, R2 is unreachable from that network and Cast will show a black screen.
  - **[v3]** Document that phone and Chromecast must be on the same LAN; guest/captive-portal Wi-Fi may block Cast discovery.
  - **[v3]** Document the ~3.5h URL-expiry ceiling: services longer than that require a deliberate stop/re-cast with a freshly minted URL.
  - **[v3 P2]** Label Presentation API explicitly as a developer / browser-projection fallback, not a production TV path. Production guidance = Cast on Android/Chrome, or AirPlay to Apple TV for iPhone.
- Update `.env.production.example` to reflect Default Media Receiver as the default and demote Custom Receiver instructions to a "Legacy / future" comment block.
- Update `docs/deployment-plan-webapp*.md` to reflect receiver registration guidance, env var responsibility, the 4-hour signed-URL convention for Cast, and the faststart requirement.
- Add a runbook note: TV must be on the same Wi-Fi as the phone; receiver fetches the MP4 directly from R2 (so R2 responses must be reachable from the TV's network and respond with `Content-Type: video/mp4` and standard range headers).

### Phase 11 — Tests

Run from `delivery/webapp/`:

```bash
pnpm lint
pnpm typecheck   # if a typecheck script exists; otherwise pnpm build covers typecheck
pnpm test
pnpm build
```

Run from `delivery/render-worker/`:

```bash
PYTHONPATH=src pytest tests/test_video_engine.py tests/test_mp4_cast_compatibility.py -v
```

#### New: `src/test/lib/cast/loader.test.ts`

- Script injected once across multiple callers.
- `__onGCastApiAvailable` resolves on `true`, rejects on `false`.
- SSR does not touch `window`.
- Unsupported browser reports `isCastSdkSupported()=false`.
- Unmount-before-load: if the `AbortSignal` aborts before the global callback fires, the promise resolves silently and no state update is scheduled.

#### New: `src/test/lib/cast/dispatch.test.ts`

- `dispatchCast({ type:"mute", muted:true })` calls `cast.setMuted(true)` — NOT `cast.setVolume(0)`.
- `dispatchCast({ type:"songTitle", title:"..." })` is a no-op.
- Unknown `type` is a no-op.
- `seek`/`volume`/`play`/`pause` route correctly.

#### New: `src/test/hooks/useCastTransport.test.ts`

- Missing env app ID → `isSupported=false`, `start()` no-ops.
- SDK unavailable → `isSupported=false`.
- Creating two hook instances calls `CastContext.setOptions` exactly once (singleton guard).
- `requestSession()` success + `loadMedia` success → `isConnected=true`, `deviceName` set.
- `requestSession()` rejection (user cancel) → `isConnecting=false`, no session leak.
- `loadMedia` failure → `endCurrentSession()` called, `isConnected=false`, `lastError` set, `onError` fired; retry path emits a fresh `requestSession`.
- `RemotePlayerController` event listeners update `currentTime`/`playerState`/`volume`/`isMuted` AND `lastStatusAtMs`.
- `seek()` debounced: three rapid `seek()` calls within 200ms produce a single `controller.seek()` invocation with the last argument.
- `seek()`, `setVolume()` clamp out-of-range inputs.
- `setMuted(true)` calls `controller.muteOrUnmute()` and leaves `volume` untouched.
- On `IS_CONNECTED_CHANGED -> false`: `currentTime` retains its last value; `resumeProposal` is populated.
- **[v3] Extrapolation tests:**
  - last state `"playing"`, `lastStatusAtMs` 10s ago → `resumeProposal.time == currentTime + 10`, `isStale=false`.
  - last state `"playing"`, `lastStatusAtMs` 90s ago → `resumeProposal.isStale=true`, `time == currentTime + 60` (capped), clamped to duration.
  - last state `"paused"` → `resumeProposal.time == currentTime`, `isStale=false`.
  - `lastStatusAtMs == null` → `resumeProposal.time == currentTime`, `isStale=false`, `lastState="unknown"`.
- **[v3] Buffering tracking:** `PLAYER_STATE_CHANGED -> "buffering"` sets `bufferingSinceMs`; transition out clears it.
- Cleanup removes all listeners without throwing.
- Reconnect (status event after disconnect) does NOT cause a seek command to be issued to the receiver.

#### Existing: `src/test/hooks/usePresentation.test.ts`

- Drop `@ts-expect-error` references.
- Add receiver `sendStatus` tests + validator/clamp tests.
- Add validator test: `{ type:"mute", muted:true }` coerced to boolean; non-boolean input rejected or coerced.

#### New: `src/test/hooks/usePresentationSender.test.ts`

- Per the transport-api plan.

#### Existing: `src/test/components/play/ControllerPlayer.test.tsx`

- Replace `onPresentationConnect`/`onPresentationDisconnect` default props with the new `transport` / `presentationFallback` / `isCastSupported` / `castAvailability` / `isCastConnecting` / `onSendToTV` / `onSendTransportCommand` props.
- On-phone UI reconciles from `transport.currentTime`/`playerState` when `isPresentationActive=true`.
- Cast button shows in correct states.
- Command forwarding tests: `handlePlayPause`, `handleSeek`, `handleVolumeChange`, `handleToggleMute` each call `onSendTransportCommand` with expected payload.
- `handleToggleMute` emits `{type:"mute", muted:<negated>}` — NOT a `volume` command.
- Jump-to-chapter and jump-to-lyrics tests cause `onSendTransportCommand` with `seek` payload matching the chapter/line-start seconds.
- Song-change effect emits `songTitle` command when `isPresentationActive=true`.
- Buffering chip renders when `transport.playerState === "buffering"` and `isPresentationActive=true`; controls remain enabled.
- **[v3] Actionable buffering copy** appears when `transport.bufferingSinceMs` is more than 15s in the past.
- **[v3] Tap-to-resume:** on `isPresentationActive` true→false with `transport.resumeProposal.isStale=false` and mocked `video.play()` rejecting, an inline "Tap to resume at \<time\>" control renders.
- **[v3] Stale prompt:** on `isPresentationActive` true→false with `transport.resumeProposal.isStale=true`, the "may be stale" prompt renders and `video.play()` is NOT auto-invoked.
- **[v3] Diagnostic bottom sheet:** tapping the disabled Cast button when `castAvailability="unavailable"` opens the bottom sheet with the 4 diagnostic lines.
- iPhone fallback copy test: when `isCastSupported=false` and `presentationFallback.isSupported=false`, fallback copy renders.
- Reconnect test: when `transport` reconnects after disconnect, no `seek` command is auto-issued (only status reconciliation).

#### Existing: `src/test/components/play/PrePlayCard.test.tsx`

- Remove Send-to-TV / availability tests.
- Verify Start Worship, Share, render status, song list, offline status still pass.

#### Existing: `src/test/app/controller-page.test.tsx`

- Remove `window.postMessage({type:"presentation", action})` tests.
- Add: songset controller passes correct `presentationUrl` and `media` payload to hooks.
- Add: share controller passes token-derived `presentationUrl` and `media` payload.
- Add: `caster.isConnected` drives `ControllerPlayer.isPresentationActive`.
- Add: Cast path is preferred when `cast.isSupported=true`; Presentation fallback used only when `!cast.isSupported`.
- `cast.onError` triggers a toast.

#### New: `src/test/api/signed-url-cast-expiry.test.ts`

- `cast=true` query param to `/api/signed-url` yields a URL minted with 14400s expiry.
- Without `cast=true`, default 3600s is preserved.
- `expiresInSeconds` clamped to allowed bounds per existing zod schema.
- `cast=true` path returns a signed URL with **no session cookie / no auth header required** (token/songset ID validation only).

#### New: `src/test/api/share-token-cast-expiry.test.ts`

- `/api/share/[token]` mints the MP4 URL with 14400s expiry.
- Revoked/expired shares still return 404/410 before minting.

#### New: `src/test/api/log-client-error.test.ts`

- POST malformed JSON → 400.
- POST well-formed `{ message, kind, meta }` → 202; persisted row exists.
- Rate limit: > 20 requests/min from one client IP → 429 (using the Upstash ratelimit mock).
- GET (non-POST) → 405.
- **[v3] Structured fields:** persisted row includes `browser`, `platform`, `castAppIdMode`, `transportKind`, `errorKind`, `mediaSourceKind`, `urlExpired` when provided.
- **[v3] PII redaction:** a submitted `meta.url` containing a signed URL is reduced to host + path + expiry age before persistence.

#### New: `delivery/render-worker/tests/test_mp4_cast_compatibility.py` (v3 P0)

- `ffprobe` on a rendered sample → video codec H.264, audio codec AAC.
- `moov` atom appears before `mdat` (faststart verified).
- Upload `content_type` remains `video/mp4`.

### Phase 12 — Manual Validation

- **Android Chrome phone + whitelisted Chromecast/Google TV on same Wi-Fi.**
  - Start cast from controller → MP4 loads on TV.
  - Play, pause, seek (slider) on phone → TV follows.
  - Prev/next song, chapter jump, lyric-line jump on phone → TV seeks to the correct time.
  - Volume slider + mute toggle on phone → TV follows. Mute toggles the mute bit, not volume.
  - While TV buffers, a "TV is loading…" chip shows; controls remain enabled. Simulate a >15s stall and confirm actionable copy appears.
  - Disconnect TV → phone badge clears; local video resumes from the extrapolated TV position (not a stale position); audio un-mutes.
  - **[v3] Tap-to-resume:** background the phone during cast, then disconnect → if `play()` rejects, the inline "Tap to resume at \<time\>" control renders and works.
  - **[v3] Stale prompt:** simulate a disconnect after the receiver status has been silent for >60s → the "Resume from TV position may be stale" prompt renders; no silent resume.
  - **[v3] Diagnostic bottom sheet:** on a network with no Cast devices, tap the disabled Cast button → bottom sheet opens with the 4 diagnostic lines.
  - Reconnect → phone UI re-syncs to TV's current position; TV does NOT seek.
  - Tap "Next Song" 3x rapidly → TV seeks once to the last-tapped song (debounced), no out-of-order seeks.
  - Simulate `loadMedia` failure (e.g., 403 R2 URL) → user returned to a clean disconnected state, can retry without manual cleanup.
  - Long-set validation: play past 60 minutes; verify 4-hour signed URL survives.
- **Laptop-to-laptop (Presentation API dev fallback):** verify on Chrome desktop that "Send to TV" still works as a dev fallback when Cast is unavailable, projecting to the existing `/play/projection` route. Verify mute toggle via the Presentation fallback (simulated via volume).
- **iPhone Safari:** confirm the UI does not offer broken Chromecast flow and falls back to AirPlay-to-Apple-TV copy.
- **Pre-service network test:** open the signed MP4 URL directly in a laptop browser on the same Wi-Fi/VLAN as the Chromecast and verify range-seek works (seek forward/back 10s, reload). If it fails, R2 is unreachable from that network and Cast will show a black screen.
- **[v3 P0] MP4 compatibility on real TV:** with a freshly rendered MP4 (post-faststart), verify on the actual Google TV hardware:
  - First-frame startup time is fast (no long stalls).
  - 10s forward/back range seek works.
  - Chapter jump seek is accurate.
  - Lyric-line jump seek is accurate.
- **Chapter timestamp drift check:** verify songset chapter/line timestamps exactly match rendered MP4 seek points by manual seek-and-eyeball for at least one full set.

### Phase 13 — Client Error Telemetry Endpoint (v3 hardened)

Add `delivery/webapp/src/app/api/log-client-error/route.ts`:

- `POST` only; others → 405.
- Body schema (zod):
  ```ts
  {
    message: string (<=1024),
    kind: "cast_load" | "cast_transport" | "presentation" | "other",
    meta?: {
      browser?: string;
      platform?: string;
      castAppIdMode?: "set" | "default" | "unset";
      transportKind?: "cast" | "presentation" | "none";
      mediaSourceKind?: "songset" | "share";
      urlExpired?: boolean;
      url?: string;   // will be redacted to host + path + expiry age before persistence
    }
  }
  ```
- Auth: best-effort — accepts optional session, but works without one so the TV-less phone path is unrestricted.
- **[v3] Rate limiting:** `@upstash/ratelimit` + `@upstash/redis` distributed token bucket, 20 req/min per hashed client IP. This survives Vercel serverless cold-starts and multi-instance execution. Add `@upstash/ratelimit` and `@upstash/redis` via the project's package manager.
- Persistence: append to `client_error_log` table (`id`, `created_at`, `ip_hash`, `message`, `kind`, `meta_json`). If DB write fails, swallow — best-effort telemetry, never user-facing.
- Returns 202 Accepted on success, 429 when rate-limited, 400 on validation error.
- **PII redaction (v3):** never log full signed URLs. A submitted `meta.url` is parsed and only `host + path + (expired | fresh)` is persisted. Never log user IDs. Hash the IP with a rotating salt.
- The hook (Phase 3) calls this endpoint from `cast.onError` and the transport error paths, populating the structured fields.

Add a minimal Drizzle migration for `client_error_log` (this is the one allowed schema addition).

## Files Touched Summary

| File | Action |
|---|---|
| `src/types/presentation-api.d.ts` | Create — ambient types + shared `PresentationCommand` (incl. `mute`) / `PresentationStatus` |
| `src/types/cast-sdk.d.ts` | Create — ambient Cast SDK types |
| `src/lib/cast/loader.ts` | Create — Cast SDK script loader + `isCastSdkSupported` (unmount-safe, `AbortSignal`, clarified check) |
| `src/lib/cast/dispatch.ts` | Create — `dispatchCast` (mute → setMuted, songTitle → no-op) |
| `src/hooks/useCast.ts` | Create — `useCastTransport` (singleton setOptions, loadMedia-failure teardown, 200ms seek debounce, `lastStatusAtMs`, `resumeProposal`, `bufferingSinceMs`, `onError`, `lastError`) |
| `src/hooks/usePresentation.ts` | Edit — add `usePresentationSender`; validator/clamp incl. `mute`; `sendStatus`; remove 4 `@ts-expect-error` |
| `src/app/songsets/[id]/play/controller/page.tsx` | Edit — wire both transports; remove dead `window.message`; pass `onError` toasts |
| `src/app/share/[token]/play/controller/page.tsx` | Edit — same with token URL |
| `src/components/play/ControllerPlayer.tsx` | Edit — transport props; Cast button; reconciliation; buffering chip + >15s actionable copy; mute command; **disconnect-resume seek + extrapolation + tap-to-resume + stale prompt**; **diagnostic bottom sheet**; iPhone copy |
| `src/components/play/PrePlayCard.tsx` | Edit — delete Presentation/Cast launch code, Send-to-TV button, 2 `@ts-expect-error` |
| `src/components/play/ProjectionPlayer.tsx` | Edit — `sendStatus({type:"ready"})` on `canplay`; `sendStatus({type:"error"})` on play rejection |
| `src/lib/r2/client.ts` | Edit — add `CAST_PLAYBACK_EXPIRES_IN_SECONDS=14400` constant |
| `src/app/api/share/[token]/route.ts` | Edit — mint MP4 with 14400s expiry |
| `src/app/api/signed-url/route.ts` + `shared-handler.ts` | Edit — accept `cast=true` → use 14400s; no session-cookie requirement |
| `src/app/api/log-client-error/route.ts` | **Create (v3 hardened)** — Upstash distributed rate-limit + structured fields + PII redaction |
| `src/db/schema.ts` (or equivalent) | **Edit** — add `client_error_log` table |
| `drizzle` migration | **Create** — for the new table |
| `delivery/render-worker/src/sow_render_worker/video_engine.py` | **Edit (v3 P0)** — add `-movflags +faststart` to `get_video_codec_args()` |
| `delivery/render-worker/tests/test_video_engine.py` | **Edit (v3)** — assert faststart flag in command args |
| `delivery/render-worker/tests/test_mp4_cast_compatibility.py` | **Create (v3 P0)** — ffprobe-based H.264/AAC/moov-at-front assertions |
| `src/test/lib/cast/loader.test.ts` | Create |
| `src/test/lib/cast/dispatch.test.ts` | Create (mute + songTitle no-op) |
| `src/test/hooks/useCastTransport.test.ts` | Create (incl. `lastStatusAtMs`, `resumeProposal` extrapolation, `bufferingSinceMs`) |
| `src/test/hooks/usePresentationSender.test.ts` | Create |
| `src/test/hooks/usePresentation.test.ts` | Edit — drop `@ts-expect-error`; add `sendStatus`/validator/`mute` tests |
| `src/test/components/play/ControllerPlayer.test.tsx` | Edit — new props, command forwarding (incl. mute), buffering chip + >15s copy, **disconnect-resume + tap-to-resume + stale prompt**, **diagnostic bottom sheet**, reconnect, iPhone copy |
| `src/test/components/play/PrePlayCard.test.tsx` | Edit — remove Send-to-TV tests |
| `src/test/app/controller-page.test.tsx` | Edit — remove postMessage tests; add transport wiring + onError toast tests |
| `src/test/api/signed-url-cast-expiry.test.ts` | Create (+ auth-free path test) |
| `src/test/api/share-token-cast-expiry.test.ts` | Create |
| `src/test/api/log-client-error.test.ts` | Create (Upstash rate-limit, structured fields, PII redaction) |
| `delivery/webapp/README.md` | Edit — Default Media Receiver only (Custom Receiver demoted to Legacy/future); iPhone fallback; long-URL policy; pre-service network test; Presentation API dev-only label; faststart requirement |
| `delivery/webapp/.env.production.example` | Edit — Default Media Receiver default; Custom Receiver demoted to commented Legacy block |
| `delivery/webapp/DEPLOY-VERCEL.md` | Edit — reflect Default Media Receiver guidance |
| `docs/deployment-plan-webapp*.md` | Edit — Cast receiver registration (default only), 4h URL policy, faststart requirement |

## Live-Service Go / No-Go Checklist (NEW in v3)

Required before first live use. All items must pass on the same TV + network class that will be used in service.

1. **Network topology:** Android Chrome phone and Google TV/Chromecast are on the same Wi-Fi/VLAN; no captive portal / guest isolation.
2. **Receiver discoverability:** the receiver device is discoverable from the sender and whitelisted in the Google Cast SDK Developer Console for dev/staging.
3. **Signed URL range-seek:** the signed MP4 URL opens from the same network in a laptop browser and supports seek (forward/back 10s) and reload.
4. **MP4 compatibility on real TV:** with a freshly rendered MP4 (post `-movflags +faststart`), the TV starts playback quickly, supports 10s range seek, chapter jump seek, and lyric-line jump seek without long stalls. ffprobe pipeline test passes (H.264/AAC/moov-at-front).
5. **Transport on real TV:** play/pause, volume, mute (mutes the bit, not volume), chapter jump, and lyric-line jump all work on the real TV from the phone.
6. **Disconnect resume:** disconnect resumes local playback from the extrapolated TV position; the audio un-mutes. Tap-to-resume renders if `play()` rejects (verify by backgrounding the phone during cast, then disconnecting).
7. **Stale signaling:** when the receiver status was silent >60s before disconnect, the "Resume from TV position may be stale" prompt renders instead of silently resuming.
8. **Diagnostic UX:** on a network with no Cast devices, tapping the disabled Cast button opens the bottom sheet with the 4 diagnostic lines.
9. **Rehearsal:** a service-length rehearsal on the same TV/network class runs for at least 60 minutes without URL expiry or receiver stalls. (4-hour signed URL covers this; longer services require deliberate stop/re-cast.)
10. **Telemetry:** `/api/log-client-error` is reachable from the phone, rate-limited, and persists structured anonymized rows for at least one simulated `loadMedia` failure.

## Risks / Open Items

- **Receiver readiness:** Cast SDK may work perfectly while the TV still buffers. **Mitigation:** "TV is loading…" chip from `playerState="buffering"`; >15s actionable copy.
- **Receiver-as-truth caveats:** Phone-local `<video>` time may briefly disagree with the receiver during connect. On disconnect, the local video is seeked to the extrapolated receiver time (P0 hardened); if the extrapolation window exceeds 60s, a stale prompt is shown instead of silent resume.
- **Local autoplay rejection (v3):** mobile browsers may reject `play()` after backgrounding. **Mitigation:** inline tap-to-resume control with the seek already applied.
- **LoadMedia-failure dangling session (resolved):** `endCurrentSession()` on `loadMedia` failure.
- **Rapid seeks (resolved):** 200ms debounce.
- **Global callback / singleton races (resolved):** loader unmount-safe; `CastContext.setOptions` singleton-guarded.
- **Cast SDK production approval:** Required before public launch to non-whitelisted devices. Not a blocker for dev/staging.
- **iPhone → non-Apple-TV:** Not supported by any web-only path; requires native iOS Cast sender. Documented future work.
- **Operator Wi-Fi:** Same-LAN requirement; corporate/captive-portal Wi-Fi may block discovery. Pre-service network test in runbook + diagnostic bottom sheet.
- **R2 reachability from the TV network:** receiver fetches MP4 directly; R2 must be reachable with `Content-Type: video/mp4` and range support. Pre-service network test in runbook.
- **MP4 codec/faststart (v3):** enforced by render worker + ffprobe pipeline test; real-TV validation in Phase 12.
- **Telemetry best-effort:** `/api/log-client-error` is fire-and-forget; DB write failures swallowed; Upstash rate-limit prevents abuse. Not a substitute for receiver-side observability.
- **Chapter timestamp drift:** Songset timestamps may diverge from the rendered MP4. Manual Phase 12 step; automated render-pipeline check = future work.
- **URL-expiry ceiling:** Services longer than ~3.5h require a deliberate stop/re-cast with a freshly minted URL.
- **Presentation API maturity:** MDN marks it limited/experimental; retained as dev-only browser fallback; never sold as a production TV path.

## Acceptance Criteria

- The existing v1/v2 plan files and the three prior standalone plans are not edited.
- Cast SDK types compile without `@ts-expect-error`.
- Controller pages own the Cast transport and Presentation fallback.
- Receiver-as-truth: phone UI reconciles to `RemotePlayerController` events; no silent reconnect-induced seeks hit the TV.
- **[P0]** Default Media Receiver is the only documented Cast receiver mode; Custom Receiver instructions are demoted to a Legacy/future section in README and `.env.production.example`.
- **[P0]** Rendered MP4 carries `-movflags +faststart`; ffprobe pipeline test asserts H.264/AAC/moov-at-front.
- **[P0]** On Cast disconnect, the local `<video>` seeks to the extrapolated `transport.resumeProposal.time` (with 60s cap) before unmuting + resuming playback; if `resumeProposal.isStale`, the stale prompt shows instead of silent resume; if `play()` rejects, the tap-to-resume control renders.
- **[P0]** Mute toggle emits `{type:"mute", muted}`; Cast dispatch routes it to `cast.setMuted()`; it does NOT zero volume.
- **[P1]** Non-blocking "TV is loading…" chip shows when `playerState === "buffering"`; controls remain enabled; queue semantics are latest-wins; >15s actionable copy appears.
- **[P1]** `loadMedia` failure calls `endCurrentSession()`; user can retry without manual cleanup.
- **[P1]** `seek()` is debounced 200ms; rapid taps collapse to one receiver command; latest argument wins.
- **[P1]** Loader is unmount-safe (no state updates on a dead tree).
- **[P1]** `CastContext.setOptions` runs at most once per page load.
- **[P1]** Transport errors surface as toasts AND are POSTed to `/api/log-client-error` (Upstash distributed rate-limit, structured fields, PII redaction).
- **[P1]** Tapping the disabled Cast button opens the diagnostic bottom sheet with the 4 diagnostic lines.
- **[P2]** `dispatchCast` ignores `songTitle` and unknown command types (documented).
- **[P2]** `isCastSdkSupported()` uses the standard `window.chrome?.cast && window.cast?.framework` check (no `navigator.presentation` artifact).
- **[P2]** Presentation API is labeled dev-only in docs; Send-to-TV UI copy is otherwise unchanged.
- Dead `window.postMessage` presentation plumbing is removed from both controller pages.
- `PrePlayCard` no longer owns Presentation API launch, Cast detection, or Send-to-TV UI.
- Cast-targeted signed URLs use 14400s expiry; non-cast signed URLs remain at 3600s.
- `/api/signed-url?cast=true` works with no session cookie (token/songset ID validation only).
- iPhone web shows a clear fallback (AirPlay to Apple TV) instead of a broken Chromecast button.
- Existing playback behavior remains unchanged on the phone-local video element except for pause/mute while casting (and the new disconnect-resume seek + tap-to-resume).
- Tests cover: Cast loader (incl. unmount-safe), `dispatchCast` (mute, songTitle no-op), `useCastTransport` lifecycle/send/receive/cleanup/reconnect/loadMedia-failure-teardown/seek-debounce/setMuted/singleton/`lastStatusAtMs`/`resumeProposal` extrapolation/`bufferingSinceMs`, Presentation sender/receiver, command forwarding (incl. mute) in `ControllerPlayer`, buffering chip + >15s actionable copy, jump-to-chapter/jump-to-lyrics transport, disconnect-resume seek + tap-to-resume + stale prompt, diagnostic bottom sheet, share-mp4 4h expiry, signed-url `cast=true` expiry + auth-free, client-error endpoint (Upstash rate-limit, structured fields, PII redaction), render-worker faststart flag + ffprobe compatibility test.
- **Live-Service Go/No-Go Checklist** is a separate, required pre-service gate (see above).
- `pnpm lint && pnpm typecheck && pnpm test && pnpm build` all pass from `delivery/webapp/`.
- `PYTHONPATH=src pytest tests/test_video_engine.py tests/test_mp4_cast_compatibility.py -v` passes from `delivery/render-worker/`.
