# Implementation Plan: Consolidated Chromecast Projection v2 (Cast SDK + AirPlay + Presentation API Fallback)

> **v2 revision.** This file is a new version of `consolidated-chromecast-projection-plan.md`, rewritten to incorporate review feedback in `reports/chromecast-projection-plan-review.md`. The original plan file is left unmodified. The three prior standalone plans (`complete-chromecast-projection-transport-api.md`, `complete-chromecast-projection-sender-bridge.md`, `complete-chromecast-projection-cast-sdk-media-transport.md`) also remain untouched.

## What Changed From v1 (Summary)

| Review issue | P | Resolution in v2 |
|---|---|---|
| #1 Disconnect → local resume time sync | P0 | On disconnect, seek the local `<video>` to `transport.currentTime` (last known receiver position) before unpausing. New effect + acceptance criterion. |
| #2 Mute toggle cross-transport semantics | P0 | Add `{ type: "mute"; muted: boolean }` to `PresentationCommand`. `dispatchCast` routes mute to `cast.setMuted()`. `ControllerPlayer.handleToggleMute` emits mute command. |
| #3 Buffering UI | P1 | Non-blocking "TV is loading…" indicator when `playerState === "buffering"`. Controls stay enabled (queued). |
| #4 Dangling session on `loadMedia` failure | P1 | Call `CastContext.endCurrentSession()` on `loadMedia` failure; reset to clean disconnected state; allow retry. |
| #5 Rapid-fire seeks | P1 | 150–250ms trailing debounce on `seek()` inside `useCastTransport`. |
| #6 Global `__onGCastApiAvailable` cleanup | P1 | Loader tracks mounted/cancelled state; rejects resolve silently after unmount; no throwing into dead tree. |
| #7 `CastContext.setOptions` singleton | P1 | Module-level singleton guard so `setOptions` runs at most once per page load. |
| #8 / #13 Failed command feedback + telemetry | P1 | Pipe transport errors into toast system AND add a lightweight `/api/log-client-error` endpoint so receiver-side failures are observable post-incident. |
| #9 `songTitle` Cast no-op | P2 | `dispatchCast` explicitly no-ops `songTitle` with a documented invariant. |
| #10 `isCastSdkSupported()` clarity | P2 | Remove the confusing `navigator.presentation` "test path" condition; standard check is `!!window.chrome?.cast && !!window.cast?.framework`. |
| #11 Chapter timestamp drift | P2 | Manual validation criterion: verify songset chapter/line timestamps match rendered MP4 seek points. |
| #12 R2 reachability | Ops | Add pre-service network test to runbook; document ~3.5h URL-expiry limit. |
| #14 Share auth-free path | Ops | Add automated Vitest confirming `/api/signed-url?cast=true` works with no session cookie. |

Additional v2 additions: **Phase 12 — Client Error Telemetry Endpoint**, expanded acceptance criteria, new test files.

## Goal

Smooth and responsive playback and jump-to-chapter/jump-to-lyrics navigation from a phone casted to a large TV, with easy connect and robust stay-connected behavior during worship. Must work from Android phones to a Chromecast/Google TV; iPhone web casting to non-Apple TV is not feasible and is deferred to a native iOS Cast sender app (future work).

## Decisions (confirmed)

| Decision | Choice |
|---|---|
| Primary transport (Android) | Google Cast Web Sender SDK with `chrome.cast` media APIs (`loadMedia`, `RemotePlayer`, `RemotePlayerController`) |
| Receiver app | Google **Default Media Receiver** (lyrics are baked into the MP4; no custom Cast receiver UI needed) |
| Receiver app ID | Single env var `NEXT_PUBLIC_CAST_RECEIVER_APP_ID`; default receiver constant when unset in dev |
| Sync source of truth | **Cast receiver media status is the source of truth** for position, playerState, volume, mute. Phone UI reconciles to receiver status events. Phone commands intent (play/pause/seek/volume/mute/song). No silent reconnect-induced seeks hit the TV. |
| Disconnect → local resume | **[P0 fix]** On Cast disconnect, the local `<video>` is first seeked to `transport.currentTime` (last known receiver position) and only then unpause/unmute. Prevents the leader being thrown back to a stale position mid-set. |
| Mute command model | **[P0 fix]** `PresentationCommand` gains `{ type: "mute"; muted: boolean }`. Cast dispatch routes this to `cast.setMuted()`; Presentation fallback simulates mute via volume level. |
| Seek guard | **[P1 fix]** 200ms trailing debounce on `useCastTransport.seek()`. |
| Buffering UX | **[P1 fix]** Non-blocking "TV is loading…" indicator when `playerState === "buffering"`. Transport controls remain enabled (commands queue). |
| Failed-command feedback | **[P1 fix]** transport errors / `loadMedia` failures surface as toasts; an anonymized telemetry endpoint (`/api/log-client-error`) records them server-side for post-incident debugging. |
| iPhone | Web UI shows "Chromecast not supported on iPhone web — use AirPlay to an Apple TV, or wait for native iOS app." Native iOS Cast sender app = documented future work. No broken iPhone Chromecast flow. |
| Presentation API fallback | **Retained as secondary/fallback transport** for laptop-to-laptop dev/direct browser projection when Cast is unavailable. Try Cast first; fall back to Presentation API. |
| R2 signed MP4 URL expiry | **4 hours (14400s)** for Cast playback URLs (and share-mp4 URLs) so playback survives long rehearsals/services. Services longer than ~3.5h require a deliberate stop/re-cast with a freshly minted URL. |
| Future work | Native iOS Cast sender app; periodic drift correction only if real-world drift observed; custom Cast receiver overlay only if lyrics stop being baked into MP4; automated chapter-timestamp drift check in the render pipeline. |

## Scope

### In Scope

- Add ambient TypeScript declarations for the Google Cast Web Sender SDK surface used by this app.
- Add ambient `.d.ts` for the W3C Presentation API surface (clean up existing `@ts-expect-error`).
- Add a client-only Cast SDK loader that injects `https://www.gstatic.com/cv/js/sender/v1/cast_sender.js` once, honors `window.__onGCastApiAvailable`, reports clear unsupported state, and is unmount-safe.
- Add `useCastTransport({ media })` sender hook wrapping `cast.framework.CastContext` + `RemotePlayer` + `RemotePlayerController`, with a module-level `setOptions` singleton guard, `loadMedia`-failure session teardown, and a 200ms seek debounce.
- Extend `usePresentationReceiver` to return a `sendStatus` helper and use the new ambient types (remove `@ts-expect-error`).
- Plumb both controller pages (`songsets/[id]/play/controller`, `share/[token]/play/controller`) to own the Cast transport (and Presentation fallback).
- Replace dead `window.postMessage` presentation plumbing.
- Update `ControllerPlayer` to: render Cast button + connection state, route all transport commands (including a dedicated mute command) through the active transport, reconcile the on-phone UI from the Cast receiver status stream, surface a non-blocking buffering indicator, and **seek the local video to the receiver's last position on disconnect before resuming**.
- Remove Presentation API launch/availability ownership from `PrePlayCard`.
- Extend R2 signed URL expiry to 14400s for Cast/share playback URLs; verify the `cast=true` auth-free path.
- Add iPhone fallback copy in `ControllerPlayer`.
- **[New, P1]** Add a lightweight `/api/log-client-error` endpoint that accepts anonymized transport errors and persists them (rate-limited, best-effort) so server-side visibility exists for `loadMedia` failures, expired-URL 403s, and receiver errors.
- Update Cast docs: stable receiver app ID per env; whitelisted dev/test devices in Google Cast SDK Developer Console; default receiver option documented; 4-hour URL expiry limit; pre-service network test.
- Add focused tests for transport behaviors.

### Out of Scope

- Native iOS Cast sender app (future milestone).
- Custom Cast receiver HTML/JS app (Default Media Receiver suffices for baked-in lyric MP4s).
- Periodic drift heartbeat (rely on Cast SDK's status stream).
- Custom ack protocol, sequence IDs, or durable session state stored in DB.
- Database schema changes other than a single telemetry log table (see Phase 12).
- Render pipeline changes, storage changes.
- Controller/projection visual redesign beyond the new top-bar Cast button + buffering chip.
- Automated chapter-timestamp drift validation inside the render pipeline (manual QA step only in this milestone; future work).
- Google Cast production approval (this is a dev/test-plan gate, not a prerequisite).

## Transport Architecture

```
                  ┌─────────────────────────┐
   Worship leader │  Controller page (web)  │
   phone (Android│  - useCastTransport      │   PRIMARY  (Android Chrome)
   Chrome)        │    (Cast SDK + loadMedia)│ ────────────────────────────▶ TV (Chromecast/Google TV)
                  │  - usePresentationSender │   FALLBACK (laptop-to-laptop
                  │    (Presentation API)    │   when Cast unsupported) ──▶ Projection route in second browser
                  └────────────┬─────────────┘
                               │ reconcile on-phone UI from
                               │ RemotePlayerController events
                               ▼
                  Phone-local video element is paused+muted while cast active
                  **[P0] On disconnect: seek local to transport.currentTime, then unmute/resume**
```

- **Phone → TV (intent):** Cast: `CastSession.loadMedia`, `RemotePlayerController.play/pause/seek/setVolumeLevel/muteOrUnmute`. Presentation fallback: `PresentationConnection.send(JSON)`.
- **TV → Phone (status, source of truth):** Cast: `RemotePlayerController` event listeners (`currentTime`, `playerState`, `duration`, `volume`, `isMuted`, `displayName`). The on-phone UI slider/time/playing state always reflects the receiver's actual state.
- **TV on phone reconnect (Wi-Fi blip):** TV keeps playing; phone re-subscribes to status events and re-syncs its local UI to the TV's actual position. No seek is forced on the TV unless the worship leader commands one.
- **TV when worship leader taps seek/prev-song/next-song/lyric-line-jump:** That is commanded intent -> TV seeks (correct and expected). Seek is debounced 200ms client-side.
- **On disconnect (P0):** before the local video resumes audio, seek it to `transport.currentTime`; only then unmute + resume the phone-local `<video>`.
- **On `loadMedia` failure (P1):** `CastContext.endCurrentSession()` is called so the user is returned to a clean disconnected state and can retry.

## Contract Types

In `delivery/webapp/src/types/presentation-api.d.ts`:

```ts
export type PresentationCommand =
  | { type: "play" }
  | { type: "pause" }
  | { type: "seek"; positionSeconds: number }
  | { type: "volume"; level: number }
  | { type: "mute"; muted: boolean }      // [P0 fix] dedicated mute command
  | { type: "songTitle"; title: string };  // Presentation-only; Cast dispatch no-ops

export type PresentationStatus =
  | { type: "ready" }
  | { type: "disconnected" }
  | { type: "error"; message: string };
```

In `delivery/webapp/src/types/cast-sdk.d.ts`:

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

- Listen only to event types the app uses (`CURRENT_TIME_CHANGED`, `PLAYER_STATE_CHANGED`, `IS_MEDIA_LOADED_CHANGED`, `VOLUME_LEVEL_CHANGED`, `IS_MUTED_CHANGED`, `IS_CONNECTED_CHANGED`).
- Keep declarations narrow — do not model unused Cast SDK fields.

## Cast Command Dispatch (`dispatchCast`)

Create `delivery/webapp/src/lib/cast/dispatch.ts` exporting `dispatchCast(cast: CastTransportResult, cmd: PresentationCommand): void`. Invariant:

- `play` -> `cast.play()`
- `pause` -> `cast.pause()`
- `seek` -> `cast.seek(cmd.positionSeconds)` (debounced inside the hook)
- `volume` -> `cast.setVolume(cmd.level)`
- `mute` -> `cast.setMuted(cmd.muted)`  // **[P0]** routes to the mute bit, not volume 0
- `songTitle` -> **no-op** (Cast title set via `MediaInfo` metadata at `loadMedia`; documented invariant)
- unknown type -> **no-op** (defensive)

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

- Create `delivery/webapp/src/types/presentation-api.d.ts` (Presentation API surface the receiver uses today, plus `PresentationStatus`, plus the new `mute` command).
- Create `delivery/webapp/src/types/cast-sdk.d.ts` (narrow Cast SDK surface above).
- Remove all 6 `@ts-expect-error` annotations across `usePresentation.ts` (4) and `PrePlayCard.tsx` (2). The 2 in PrePlayCard go away in Phase 7 when the Presentation API code is removed from that file.

### Phase 2 — Cast SDK Loader

Create `delivery/webapp/src/lib/cast/loader.ts`:

- `loadCastSdk(): Promise<void>` injects `https://www.gstatic.com/cv/js/sender/v1/cast_sender.js` once (script-tag ref-counted singleton).
- Sets `window.__onGCastApiAvailable = (loaded) => { ... }` before injection; resolves on `loaded=true`, rejects on `loaded=false`.
- SSR-safe: bail when `typeof window === "undefined"`.
- **[P1 fix #6]** Unmount-safe: the loader tracks a module-level `cancelled` set keyed by request id; if the requesting component unmounts before the callback fires (detected via an `AbortSignal` / cancel token passed in by the hook), the promise resolves silently and never schedules React state updates on a dead tree.
- Exposes `isCastSdkSupported()` that returns true only when `!!window.chrome?.cast && !!window.cast?.framework`. **[P2 fix #10]** The previous `"navigator.presentation is NOT the test path"` condition is removed.

### Phase 3 — `useCastTransport` Hook

Add `useCastTransport` in `delivery/webapp/src/hooks/useCast.ts`.

```ts
interface CastTransportMedia {
  videoUrl: string;
  title: string;
  startSeconds?: number;
  autoplay?: boolean;
  source: { kind: "songset" | "share"; idOrToken: string };
}

interface CastTransportResult {
  isSupported: boolean;
  isAvailable: boolean;       // device availability via CastContext
  isConnecting: boolean;
  isConnected: boolean;
  deviceName: string | null;
  playerState: "idle" | "buffering" | "playing" | "paused" | "unknown";
  currentTime: number;        // from RemotePlayer, the source of truth
  duration: number;
  volume: number;
  isMuted: boolean;
  lastError: string | null;   // [P1] surfaces loadMedia / transport failures for toasts
  start: () => Promise<void>; // requestSession + loadMedia
  stop: () => Promise<void>;
  play: () => void;
  pause: () => void;
  seek: (seconds: number) => void;  // internally debounced 200ms
  setVolume: (level: number) => void;
  setMuted: (muted: boolean) => void;
}
```

Behavior:

- Disabled when no `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` AND no dev default.
- **[P1 fix #7]** Module-level singleton guard: `CastContext.getInstance()` is called once per page load; `setOptions({ androidReceiverCompatible: true, autoJoinPolicy: "tab_and_origin_scoped", receiverApplicationId })` runs at most once. A module-level `let castContextInitDone = false` guards it.
- Create one `RemotePlayer` + one `RemotePlayerController`; attach all status event listeners once.
- **[P1 fix #6]** On unmount, remove all listeners and mark the loader request cancelled; never schedule state updates post-unmount.
- On `start()`:
  1. `requestSession()` (user gesture from the controller Cast button).
  2. Build `chrome.cast.media.MediaInfo` (content type `video/mp4`, metadata title, stream type `BUFFERED`).
  3. Set `currentTime = startSeconds` in `LoadRequest`.
  4. `session.loadMedia(loadRequest)`.
  5. **[P1 fix #4]** On `loadMedia` rejection: call `CastContext.endCurrentSession(true)` to return the receiver to a clean state; set `isConnected=false`, `isConnecting=false`, `lastError=<message>`; emit a toast via the hook's `onError` callback; do NOT leave a dangling session. User can retry.
- On cast `IS_CONNECTED_CHANGED -> false`: emit `isConnected=false`, clear device name, preserve the **last known `currentTime`** in state so `ControllerPlayer` can use it for the local-resume seek (Phase 6).
- On `PLAYER_STATE_CHANGED` / `CURRENT_TIME_CHANGED` / `VOLUME_LEVEL_CHANGED` / `IS_MUTED_CHANGED`: update state fields (this drives phone UI reconciliation — no seeks issued back to TV).
- **[P1 fix #5]** `seek(seconds)`: clamp to `[0, duration]`; apply a **200ms trailing debounce** so rapid taps (jump-to-chapter, jump-to-lyric-line) collapse to a single receiver command. The latest argument wins.
- `setVolume(level)`: clamp `[0, 1]`; call `controller.setVolumeLevel()`.
- `setMuted(muted)` (P0): call `controller.muteOrUnmute()` — never `setVolume(0)`.
- `stop()`: `CastContext.endCurrentSession()`.
- **[P1 fix #8/#13]** On any transport error path (`loadMedia` failure, `requestSession` exception, receiver error event): set `lastError`, fire `onError(message)` callback (the controller page wires this to a toast), and POST the error to `/api/log-client-error` (see Phase 12).
- Cleanup: remove all listeners, release references, never throw.
- Do **not** persist signed URLs, Cast session IDs, or device names to the database.

### Phase 4 — Presentation API Sender Refactor (Fallback Path)

Add `usePresentationSender` to `delivery/webapp/src/hooks/usePresentation.ts` (per the transport-api plan), used only when `useCastTransport` reports `isSupported=false` (Cast unavailable) or when explicitly operating in browser-to-browser dev mode.

Inputs/returns per `transport-api.md` spec (lines 91-128). Wire:

- `sender.isConnected` -> `isPresentationActive`
- `sender.send(command)` -> issued commands flow through the Presentation fallback transport only when Cast is inactive.
- **[P0 fix #2]** `sender.send({ type: "mute", muted })` simulates mute via volume level on the Presentation receiver (acceptable per review; Cast uses the real mute bit).

Extend `usePresentationReceiver` (per the transport-api plan):

- Use ambient types; drop `@ts-expect-error`.
- Add small validator; clamp volume to `[0, 1]`; coerce `mute.muted` to boolean.
- Return `sendStatus(status: PresentationStatus) => void` so `ProjectionPlayer` can post `ready` and playback-error statuses back to the controller.

`ProjectionPlayer` changes:

- Call `sendStatus({ type: "ready" })` on `loadedmetadata`/`canplay`.
- Call `sendStatus({ type: "error", message })` only on transport-relevant `video.play()` rejections.
- **[P1 fix #8]** On `error` status, the controller shows a toast "TV projection failed — check connection".
- Otherwise unchanged (no visual controls, no drift correction).

### Phase 5 — Controller Pages

Both `delivery/webapp/src/app/songsets/[id]/play/controller/page.tsx` and `delivery/webapp/src/app/share/[token]/play/controller/page.tsx`:

- Remove the dead `window.addEventListener("message", ...)` block (lines 133-151 / 90-108 respectively).
- Remove stub `handlePresentationConnect` / `handlePresentationDisconnect` callbacks.
- Compute media payload:
  - `presentationUrl = /songsets/${songsetId}/play/projection` (or share token equivalent) for the Presentation fallback.
  - `media = { videoUrl, title, source: { kind, idOrToken }, startSeconds: 0 }` for the Cast transport.
- Mount `const cast = useCastTransport({ media, onError: (m) => toast(...) })`.
- Mount `const sender = usePresentationSender({ presentationUrl, onConnected, onDisconnected })` as fallback; `sender.send` is only invoked when `!cast.isSupported`.
- Pass unified transport props to `ControllerPlayer`:

  ```tsx
  <ControllerPlayer
    playerId={playerId}
    videoSrc={videoSrc}
    chapters={chapters}
    transport={cast}
    presentationFallback={sender}
    isPresentationActive={cast.isConnected || sender.isConnected}
    isCastSupported={cast.isSupported}
    castAvailability={cast.isAvailable ? "available" : "unavailable"}
    isCastConnecting={cast.isConnecting}
    onSendToTV={() => (cast.isSupported ? cast.start() : sender.start())}
    onSendTransportCommand={(cmd) =>
      cast.isConnected ? dispatchCast(cast, cmd) : sender.send(cmd)
    }
    exitRoute={exitRoute}
    className={...}
  />
  ```

- Toast notifications only from transport lifecycle callbacks (`cast.onConnected`, `cast.onDisconnected`, `cast.onError`, `sender.onStartError`).
- For share mode: ensure `presentationUrl = /share/${token}/play/projection` (no auth on TV).
- **[Ops fix #14]** The Cast `media.videoUrl` must be the signed R2 URL returned by `/api/signed-url?cast=true` (no session cookie required). The controller fetches this URL via an auth-free fetch path (token/songset ID validation only).

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
- Top bar: replace dead `isPresentationActive` badge path with:
  - "Connected to {transport.deviceName ?? 'TV'}" badge when `isPresentationActive=true`.
  - **[P1 fix #3]** When `transport.playerState === "buffering"` AND `isPresentationActive`, show a non-blocking "TV is loading…" chip/spinner next to the badge. Controls remain enabled (commands queue).
  - Cast/Send-to-TV button (`Monitor` icon from `lucide-react`) visible when `isCastSupported=true` and `!isPresentationActive`.
  - Spinner state when `isCastConnecting=true`.
  - Disabled + tooltip "Cast unavailable" when `castAvailability="unavailable"`.
  - Hidden when `isCastSupported=false` (iPhone): show a small AirPlay hint with link to docs explaining iPhone uses AirPlay to an Apple TV, native iOS app pending.
- **Reconcile on-phone UI from Cast status** when `transport.isConnected`:
  - The slider/time/playing indicator reflects `transport.currentTime` / `transport.playerState` (source of truth from the receiver). Phone-local video element is paused and muted.
- **Forward user intent** via `onSendTransportCommand?.(...)` guarded by `isPresentationActive`:
  - `handlePlayPause` -> `send({type: isPlaying ? "pause" : "play"})` (intent state, not pre-toggle state)
  - `handleSeek(t)` -> `send({type:"seek", positionSeconds: clamp(t, 0, duration)})`
  - `handleVolumeChange(v)` -> `send({type:"volume", level: clamp(v, 0, 1)})`
  - **[P0 fix #2]** `handleToggleMute` -> `send({type:"mute", muted: !currentlyMuted})` (NOT a volume-level command). Cast dispatch routes this to `cast.setMuted()`; Presentation fallback simulates via volume level.
  - `handleSkipBack/Forward/PrevSong/NextSong/JumpToChapter/JumpToLine` all funnel through `handleSeek`, so they automatically become seeks on the TV. This satisfies "jump-to-chapter" and "jump-to-lyrics navigation" — they will seek the receiver to the chapter/lyric-line start. Debounced 200ms client-side.
- **[P0 fix #1] Disconnect → local resume effect:** An effect keyed on `isPresentationActive` falsification. On the `isPresentationActive` false edge (where `transport` was previously connected):
  1. Read `transport.currentTime` (last known receiver position).
  2. Seek the local `<video>` to that value (clamped to `[0, video.duration]`).
  3. Then unmute and resume playback (use `video.play()`; catch rejections silently).
  This replaces the previous "local video resumes audio (un-muted)" behavior with a position-aware resume.
- **Song-change effect** keyed on `currentSongIndex` while `isPresentationActive`: `send({type:"songTitle", title: currentChapter?.songTitle})`. (For Cast, song title is already set via media metadata at `loadMedia`; this is a no-op for Cast and informational for Presentation-fallback.)
- Keep existing "mute local video when presentation active" effect (already present at lines 374-386) — it now composes with the disconnect-resume effect above.
- Keep existing "hide LyricJumpList when presentation active" behavior — operator still sees transport controls and a lyric-position indicator on the controller; projection shows the baked-in lyrics video.
- For iPhone (`!isCastSupported` AND `presentationFallback.isSupported=false`): show copy "Chromecast not supported on iPhone web. Use AirPlay to an Apple TV. Native iOS app coming."

### Phase 7 — `PrePlayCard` Cleanup

- Delete: `isPresentationAvailable` / `isCastAvailable` state, `checkPresentationAvailability` effect, `handleSendToTV`, the `<Monitor>` Send-to-TV button block (lines 334-345), both `new PresentationRequest([...])` calls (lines 89-92, 157-159), and the `Monitor` import.
- Delete the 2 `@ts-expect-error` annotations (they go with the code).
- Keep Start Worship, Share, render status, song list, offline status behavior unchanged.
- PrePlayCard no longer does any Presentation/Cast API detection or launch — the controller page owns all of that.

### Phase 8 — R2 Signed URL Expiry

- `src/lib/r2/client.ts:77` — keep the default at 3600 for non-cast artefacts; expose an explicit `CAST_PLAYBACK_EXPIRES_IN_SECONDS = 14400` constant.
- `src/app/api/share/[token]/route.ts:87-97` — change share MP4 mint to `expiresInSeconds: CAST_PLAYBACK_EXPIRES_IN_SECONDS` (14400). Preserve share revocation + expiry checks before returning any URL.
- Ensure Cast-targeted URLs go through a path that picks the 4-hour expiry (e.g., add `cast=true` query param on `/api/signed-url`, validated with zod; only that path uses 14400). Non-cast signed URLs stay at 1 hour.
- **[Ops fix #14]** Verify `/api/signed-url?cast=true` does **not** require a session cookie — only token/songset ID validation. The TV has no session cookies. (Automated test in Phase 10.)
- Document: signed URL must cover full set + setup time; if a service runs longer than ~3h40m, callers must re-mint (deliberate stop/re-cast).

### Phase 9 — Docs / Operational

- Update `delivery/webapp/README.md` Cast section:
  - One stable `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` per environment (dev, staging, prod), OR omit and use Google's Default Media Receiver constant.
  - Whitelisted dev/test devices in Google Cast SDK Developer Console.
  - Production Cast approval = later launch gate (not a prerequisite for this dev/test plan).
  - iPhone web does not support Chromecast; use AirPlay to an Apple TV; native iOS sender app future work.
  - Lyrics are baked into the MP4 already; no custom Cast receiver UI needed.
  - 4-hour signed URL expiry during Cast playback.
  - **[Ops fix #12]** Pre-service network test: open the signed MP4 URL directly in a laptop browser on the same Wi-Fi/VLAN as the Chromecast and verify range-seek works (seek forward/back 10s, reload). If it fails, R2 is unreachable from that network and Cast will show a black screen.
  - **[Ops fix #12]** Document that phone and Chromecast must be on the same LAN; guest/captive-portal Wi-Fi may block Cast discovery.
  - **[Ops fix #12]** Document the ~3.5h URL-expiry ceiling: services longer than that require a deliberate stop/re-cast with a freshly minted URL.
- Update `docs/deployment-plan-webapp*.md` to reflect receiver registration guidance, env var responsibility, and the 4-hour signed-URL convention for Cast.
- Add a runbook note: TV must be on the same Wi-Fi as the phone; receiver fetches MP4 directly from R2 (so R2 responses must be reachable from the TV's network and respond with `Content-Type: video/mp4` and standard range headers).

### Phase 10 — Tests

Run from `delivery/webapp/`:

```bash
pnpm lint
pnpm typecheck   # if a typecheck script exists; otherwise pnpm build covers typecheck
pnpm test
pnpm build
```

#### New: `src/test/lib/cast/loader.test.ts`

- Script injected once across multiple callers.
- `__onGCastApiAvailable` resolves on `true`, rejects on `false`.
- SSR does not touch `window`.
- Unsupported browser reports `isCastSdkSupported()=false`.
- **[P1 fix #6]** Unmount-before-load: if the cancel signal aborts before the global callback fires, the promise resolves silently and no state update is scheduled.

#### New: `src/test/lib/cast/dispatch.test.ts`

- **[P0 fix #2]** `dispatchCast({ type:"mute", muted:true })` calls `cast.setMuted(true)` — NOT `cast.setVolume(0)`.
- **[P2 fix #9]** `dispatchCast({ type:"songTitle", title:"..." })` is a no-op (does not call `cast.*`).
- Unknown `type` is a no-op.
- `seek`/`volume`/`play`/`pause` route correctly.

#### New: `src/test/hooks/useCastTransport.test.ts`

- Missing env app ID -> `isSupported=false`, `start()` no-ops.
- SDK unavailable -> `isSupported=false`.
- **[P1 fix #7]** Creating two hook instances calls `CastContext.setOptions` exactly once (singleton guard).
- `requestSession()` success + `loadMedia` success -> `isConnected=true`, `deviceName` set.
- `requestSession()` rejection (user cancel) -> `isConnecting=false`, no session leak.
- **[P1 fix #4]** `loadMedia` failure -> `endCurrentSession()` called, `isConnected=false`, `lastError` set, `onError` callback fired; retry path emits a fresh `requestSession`.
- `RemotePlayerController` event listeners update `currentTime`/`playerState`/`volume`/`isMuted`.
- **[P1 fix #5]** `seek()` debounced: three rapid `seek()` calls within 200ms produce a single `controller.seek()` invocation with the last argument.
- `seek()`, `setVolume()` clamp out-of-range inputs.
- **[P0 fix #2]** `setMuted(true)` calls `controller.muteOrUnmute()` and leaves `volume` untouched.
- **[P0 fix #1]** On `IS_CONNECTED_CHANGED -> false`, `currentTime` retains its last value (used by ControllerPlayer for local-resume seek).
- Cleanup removes all listeners without throwing.
- Reconnect (status event after disconnect) does NOT cause a seek command to be issued to the receiver.

#### Existing: `src/test/hooks/usePresentation.test.ts`

- Drop `@ts-expect-error` references.
- Add receiver `sendStatus` tests + validator/clamp tests per the transport-api plan.
- **[P0 fix #2]** Add validator test: `{ type:"mute", muted:true }` coerced to boolean; non-boolean input rejected or coerced.

#### New: `src/test/hooks/usePresentationSender.test.ts`

- Per the transport-api plan, lines 251-268.

#### Existing: `src/test/components/play/ControllerPlayer.test.tsx`

- Replace `onPresentationConnect`/`onPresentationDisconnect` default props with the new `transport` / `presentationFallback` / `isCastSupported` / `castAvailability` / `isCastConnecting` / `onSendToTV` / `onSendTransportCommand` props.
- Add: on-phone UI reconciles from `transport.currentTime`/`playerState` when `isPresentationActive=true`.
- Cast button shows in correct states (hidden when unsupported, visible when supported + disconnected, disabled when connecting/unavailable).
- Command forwarding tests: `handlePlayPause`, `handleSeek`, `handleVolumeChange`, `handleToggleMute` each call `onSendTransportCommand` with expected payload (and JSON-serialized for Presentation path).
- **[P0 fix #2]** `handleToggleMute` emits `{type:"mute", muted:<negated>}` — NOT a `volume` command.
- Jump-to-chapter and jump-to-lyrics tests: `handleJumpToChapter`/`handleJumpToLine` while `isPresentationActive=true` cause `onSendTransportCommand` with `seek` payload matching the chapter/line-start seconds.
- Song-change effect emits `songTitle` command when `isPresentationActive=true`.
- **[P1 fix #3]** Buffering chip renders when `transport.playerState === "buffering"` and `isPresentationActive=true`; controls remain enabled.
- **[P0 fix #1]** On `isPresentationActive` transitioning true -> false (formerly connected), the local `<video>` is seeked to `transport.currentTime` and then play() is called (mocked).
- iPhone fallback copy test: when `isCastSupported=false` and `presentationFallback.isSupported=false`, fallback copy renders.
- Reconnect test: when `transport` reconnects after disconnect, no `seek` command is auto-issued (only status reconciliation on phone side).

#### Existing: `src/test/components/play/PrePlayCard.test.tsx`

- Remove Send-to-TV / availability tests.
- Verify Start Worship, Share, render status, song list, offline status still pass.

#### Existing: `src/test/app/controller-page.test.tsx`

- Remove `window.postMessage({type:"presentation", action})` tests.
- Add: songset controller passes correct `presentationUrl` and `media` payload to hooks.
- Add: share controller passes token-derived `presentationUrl` and `media` payload.
- Add: `caster.isConnected` drives `ControllerPlayer.isPresentationActive`.
- Add: Cast path is preferred when `cast.isSupported=true`; Presentation fallback used only when `!cast.isSupported`.
- **[P1 fix #8]** `cast.onError` triggers a toast.

#### New: `src/test/api/signed-url-cast-expiry.test.ts`

- `cast=true` query param to `/api/signed-url` yields a URL minted with 14400s expiry.
- Without `cast=true`, default 3600s is preserved.
- `expiresInSeconds` clamped to allowed bounds per existing zod schema.
- **[Ops fix #14]** `cast=true` path returns a signed URL with **no session cookie / no auth header required** (token/songset ID validation only). A request with no `Authorization`/cookie succeeds.

#### New: `src/test/api/share-token-cast-expiry.test.ts`

- `/api/share/[token]` mints the MP4 URL with 14400s expiry.
- Revoked/expired shares still return 404/410 before minting.

#### New: `src/test/api/log-client-error.test.ts`  ([P1 Phase 12])

- POST malformed JSON -> 400.
- POST well-formed `{ message, kind, meta }` -> 202; persisted row exists.
- Rate limit: > N requests/min from one client returns 429.
- GET (non-POST) -> 405.

### Phase 11 — Manual Validation

- **Android Chrome phone + whitelisted Chromecast/Google TV on same Wi-Fi.**
  - Start cast from controller -> MP4 loads on TV.
  - Play, pause, seek (slider) on phone -> TV follows.
  - Prev/next song, chapter jump, lyric-line jump on phone -> TV seeks to the correct time.
  - Volume slider + mute toggle on phone -> TV follows. **Mute toggles the mute bit, not volume.**
  - **[P1 fix #3]** While TV buffers, a "TV is loading…" chip shows; controls remain enabled.
  - Disconnect TV -> phone badge clears; **local video resumes from the TV's last known position (not a stale position); audio un-mutes.**
  - Reconnect -> phone UI re-syncs to TV's current position; TV does NOT seek.
  - **[P1 fix #5]** Tap "Next Song" 3x rapidly -> TV seeks once to the last-tapped song (debounced), no out-of-order seeks.
  - **[P1 fix #4]** Simulate `loadMedia` failure (e.g., 403 R2 URL) -> user returned to clean disconnected state, can retry without manual cleanup.
  - Long-set validation: play past 60 minutes; verify 4-hour signed URL survives.
- **Laptop-to-laptop (Presentation API fallback):** verify on Chrome desktop that "Send to TV" still works as a fallback when Cast is unavailable, projecting to the existing `/play/projection` route. Verify mute toggle via the Presentation fallback (simulated via volume).
- **iPhone Safari:** confirm the UI does not offer broken Chromecast flow and falls back to AirPlay-to-Apple-TV copy.
- **[Ops fix #12] Pre-service network test:** open the signed MP4 URL directly in a laptop browser on the same Wi-Fi/VLAN as the Chromecast and verify range-seek works (seek forward/back 10s, reload). If it fails, R2 is unreachable from that network and Cast will show a black screen.
- **[P2 fix #11] Chapter timestamp drift check:** verify songset chapter/line timestamps exactly match rendered MP4 seek points by manual seek-and-eyeball for at least one full set.

### Phase 12 — Client Error Telemetry Endpoint (NEW, P1)

Add `delivery/webapp/src/app/api/log-client-error/route.ts`:

- `POST` only; others -> 405.
- Body: `{ message: string (<=1024), kind: "cast_load" | "cast_transport" | "presentation" | "other", meta?: Record<string, string|number|boolean> }` validated with zod.
- Auth: best-effort — accepts optional session, but works without one so the TV-less phone path is unrestricted. Rate-limit per client IP via an in-memory or `@upstash/ratelimit` token bucket (e.g., 20 req/min).
- Persistence: append to a small `client_error_log` table (.id, created_at, ip_hash, message, kind, meta_json). If DB write fails, swallow — this is best-effort telemetry, never user-facing.
- Returns 202 Accepted on success, 429 when rate-limited, 400 on validation error.
- Strip PII: never log signed URLs (replace with host + path only), never log user IDs, hash the IP.
- The hook (Phase 3) calls this endpoint from `cast.onError` and the transport error paths.

Add a minimal Drizzle migration for `client_error_log` (this is the one allowed schema addition).

## Files Touched Summary

| File | Action |
|---|---|
| `src/types/presentation-api.d.ts` | Create — ambient types + shared `PresentationCommand` (incl. `mute`) / `PresentationStatus` |
| `src/types/cast-sdk.d.ts` | Create — ambient Cast SDK types |
| `src/lib/cast/loader.ts` | Create — Cast SDK script loader + `isCastSdkSupported` (unmount-safe, clarified check) |
| `src/lib/cast/dispatch.ts` | Create — `dispatchCast` (mute -> setMuted, songTitle -> no-op) |
| `src/hooks/useCast.ts` | Create — `useCastTransport` (singleton setOptions, loadMedia-failure teardown, 200ms seek debounce, onError, lastError, last-known currentTime on disconnect) |
| `src/hooks/usePresentation.ts` | Edit — add `usePresentationSender`; add validator/clamp incl. `mute`; add `sendStatus`; remove 4 `@ts-expect-error` |
| `src/app/songsets/[id]/play/controller/page.tsx` | Edit — wire both transports; remove dead `window.message` plumbing; pass `onError` toasts |
| `src/app/share/[token]/play/controller/page.tsx` | Edit — same with token URL |
| `src/components/play/ControllerPlayer.tsx` | Edit — transport props; Cast button + reconciliation; buffering chip; mute command; **disconnect-resume seek**; iPhone copy |
| `src/components/play/PrePlayCard.tsx` | Edit — delete Presentation/Cast launch code, Send-to-TV button, 2 `@ts-expect-error` |
| `src/components/play/ProjectionPlayer.tsx` | Edit — `sendStatus({type:"ready"})` on `canplay`; `sendStatus({type:"error"})` on play rejection |
| `src/lib/r2/client.ts` | Edit — add `CAST_PLAYBACK_EXPIRES_IN_SECONDS=14400` constant |
| `src/app/api/share/[token]/route.ts` | Edit — mint MP4 with 14400s expiry |
| `src/app/api/signed-url/route.ts` + `shared-handler.ts` | Edit — accept `cast=true` -> use 14400s; no session-cookie requirement |
| `src/app/api/log-client-error/route.ts` | **Create (P1)** — best-effort anonymized telemetry endpoint |
| `src/db/schema.ts` (or equivalent) | **Edit (P1)** — add `client_error_log` table |
| `drizzle` migration | **Create (P1)** — for the new table |
| `src/test/lib/cast/loader.test.ts` | Create |
| `src/test/lib/cast/dispatch.test.ts` | Create (mute + songTitle no-op) |
| `src/test/hooks/useCastTransport.test.ts` | Create |
| `src/test/hooks/usePresentationSender.test.ts` | Create |
| `src/test/hooks/usePresentation.test.ts` | Edit — drop `@ts-expect-error`; add `sendStatus`/validator/`mute` tests |
| `src/test/components/play/ControllerPlayer.test.tsx` | Edit — new props, command forwarding (incl. mute), buffering chip, **disconnect-resume seek**, reconnect, iPhone copy |
| `src/test/components/play/PrePlayCard.test.tsx` | Edit — remove Send-to-TV tests |
| `src/test/app/controller-page.test.tsx` | Edit — remove postMessage tests; add transport wiring + onError toast tests |
| `src/test/api/signed-url-cast-expiry.test.ts` | Create (+ auth-free path test) |
| `src/test/api/share-token-cast-expiry.test.ts` | Create |
| `src/test/api/log-client-error.test.ts` | Create (P1) |
| `delivery/webapp/README.md` | Edit — Cast receiver registration, iPhone fallback, long-URL policy, pre-service network test |
| `docs/deployment-plan-webapp*.md` | Edit — Cast receiver registration + 4h URL policy |

## Risks / Open Items

- **Receiver readiness:** If the worship leader hits play before the TV has loaded the MP4, the receiver's first frames may be missed. **Mitigation (now required):** surface a non-blocking "TV is loading…" state derived from `playerState="buffering"`. Controls stay enabled; commands queue.
- **Receiver-as-truth caveats:** Phone-local `<video>` time may briefly disagree with the receiver during connect — the on-phone slider reflects receiver time, not local time, while casting. On disconnect, the local video is seeked to the last known receiver time before resuming (P0 fix).
- **LoadMedia-failure dangling session (resolved):** `endCurrentSession()` is called on `loadMedia` failure.
- **Rapid seeks (resolved):** 200ms debounce collapses rapid jump-to-chapter/jump-to-lyric taps.
- **Global callback / singleton races (resolved):** loader is unmount-safe; `CastContext.setOptions` is singleton-guarded.
- **Cast SDK production approval:** Required before public launch to non-whitelisted devices. Not a blocker for dev/staging.
- **iPhone -> non-Apple-TV:** Not supported by any web-only path; requires native iOS Cast sender. Documented future work.
- **Operator Wi-Fi:** Phone and Chromecast must be on the same LAN; corporate/captive-portal Wi-Fi can block Cast discovery. Pre-service network test in runbook.
- **R2 reachability from the TV network:** Cast receiver fetches the MP4 directly; R2 must be reachable from the TV's network with `Content-Type: video/mp4` and range support. Pre-service network test in runbook.
- **Telemetry best-effort:** `/api/log-client-error` is fire-and-forget; DB write failures are swallowed. Not a substitute for receiver-side observability.
- **Chapter timestamp drift:** Songset timestamps may diverge from the rendered MP4. Manual validation step in Phase 11; automated render-pipeline check = future work.
- **URL-expiry ceiling:** Services longer than ~3.5h require a deliberate stop/re-cast with a freshly minted URL.

## Acceptance Criteria

- The existing plan files (v1 + the three prior standalone plans) are not edited.
- Cast SDK types compile without `@ts-expect-error`.
- Controller pages own the Cast transport and Presentation fallback.
- Receiver-as-truth: phone UI reconciles to `RemotePlayerController` events; no silent reconnect-induced seeks hit the TV.
- **[P0]** On Cast disconnect, the local `<video>` seeks to `transport.currentTime` before unmuting + resuming playback.
- **[P0]** Mute toggle emits `{type:"mute", muted}`; Cast dispatch routes it to `cast.setMuted()`; it does NOT zero volume.
- **[P1]** Non-blocking "TV is loading…" chip shows when `playerState === "buffering"`; controls remain enabled.
- **[P1]** `loadMedia` failure calls `endCurrentSession()`; user can retry without manual cleanup.
- **[P1]** `seek()` is debounced 200ms; rapid taps collapse to one receiver command.
- **[P1]** Loader is unmount-safe (no state updates on a dead tree).
- **[P1]** `CastContext.setOptions` runs at most once per page load.
- **[P1]** Transport errors surface as toasts AND are POSTed to `/api/log-client-error` (rate-limited, anonymized).
- **[P2]** `dispatchCast` ignores `songTitle` and unknown command types (documented).
- **[P2]** `isCastSdkSupported()` uses the standard `window.chrome?.cast && window.cast?.framework` check (no `navigator.presentation` artifact).
- Dead `window.postMessage` presentation plumbing is removed from both controller pages.
- `PrePlayCard` no longer owns Presentation API launch, Cast detection, or Send-to-TV UI.
- Cast-targeted signed URLs use 14400s expiry; non-cast signed URLs remain at 3600s.
- `/api/signed-url?cast=true` works with no session cookie (token/songset ID validation only).
- iPhone web shows a clear fallback (AirPlay to Apple TV) instead of a broken Chromecast button.
- Existing playback behavior remains unchanged on the phone-local video element except for pause/mute while casting (and the new disconnect-resume seek).
- Tests cover: Cast loader (incl. unmount-safe), `dispatchCast` (mute, songTitle no-op), `useCastTransport` lifecycle/send/receive/cleanup/reconnect/loadMedia-failure-teardown/seek-debounce/setMuted/singleton, Presentation sender/receiver, command forwarding (incl. mute) in `ControllerPlayer`, buffering chip, jump-to-chapter/jump-to-lyrics transport, disconnect-resume seek, share-mp4 4h expiry, signed-url `cast=true` expiry + auth-free, client-error endpoint.
- `pnpm lint && pnpm typecheck && pnpm test && pnpm build` all pass from `delivery/webapp/`.
