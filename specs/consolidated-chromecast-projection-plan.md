# Implementation Plan: Consolidated Chromecast Projection (Cast SDK + AirPlay + Presentation API Fallback)

> This is a new, consolidated plan. The three prior plans (`complete-chromecast-projection-transport-api.md`, `complete-chromecast-projection-sender-bridge.md`, `complete-chromecast-projection-cast-sdk-media-transport.md`) are intentionally left unmodified.

## Goal

Smooth and responsive playback and jump-to-chapter/jump-to-lyrics navigation from a phone casted to a large TV, with easy connect and robust stay-connected behavior during worship. Must work from Android phones to a Chromecast/Google TV; iPhone web casting to non-Apple TV is not feasible and is deferred to a native iOS Cast sender app (future work).

## Decisions (confirmed)

| Decision | Choice |
|---|---|
| Primary transport (Android) | Google Cast Web Sender SDK with `chrome.cast` media APIs (`loadMedia`, `RemotePlayer`, `RemotePlayerController`) |
| Receiver app | Google **Default Media Receiver** (lyrics are baked into the MP4; no custom Cast receiver UI needed) |
| Receiver app ID | Single env var `NEXT_PUBLIC_CAST_RECEIVER_APP_ID`; default receiver constant when unset in dev |
| Sync source of truth | **Cast receiver media status is the source of truth** for position, playerState, volume, mute. Phone UI reconciles to receiver status events. Phone commands intent (play/pause/seek/volume/song). No silent reconnect-induced seeks hit the TV. |
| iPhone | Web UI shows "Chromecast not supported on iPhone web — use AirPlay to an Apple TV, or wait for native iOS app." Native iOS Cast sender app = documented future work. No broken iPhone Chromecast flow. |
| Presentation API fallback | **Retained as secondary/fallback transport** for laptop-to-laptop dev/direct browser projection when Cast is unavailable. Try Cast first; fall back to Presentation API. |
| R2 signed MP4 URL expiry | **4 hours (14400s)** for Cast playback URLs (and share-mp4 URLs) so playback survives long rehearsals/services |
| Future work | Native iOS Cast sender app; periodic drift correction only if real-world drift observed; custom Cast receiver overlay only if lyrics stop being baked into MP4 |

## Scope

### In Scope

- Add ambient TypeScript declarations for the Google Cast Web Sender SDK surface used by this app.
- Add ambient `.d.ts` for the W3C Presentation API surface (clean up existing `@ts-expect-error`).
- Add a client-only Cast SDK loader that injects `https://www.gstatic.com/cv/js/sender/v1/cast_sender.js` once, honors `window.__onGCastApiAvailable`, and reports clear unsupported state.
- Add `useCastTransport({ media })` sender hook wrapping `cast.framework.CastContext` + `RemotePlayer` + `RemotePlayerController`.
- Extend `usePresentationReceiver` to return a `sendStatus` helper and use the new ambient types (remove `@ts-expect-error`).
- Plumb both controller pages (`songsets/[id]/play/controller`, `share/[token]/play/controller`) to own the Cast transport (and Presentation fallback).
- Replace dead `window.postMessage` presentation plumbing.
- Update `ControllerPlayer` to: render Cast button + connection state, route all transport commands through the active transport (Cast primary, Presentation fallback), and reconcile the on-phone UI from the Cast receiver status stream.
- Remove Presentation API launch/availability ownership from `PrePlayCard`.
- Extend R2 signed URL expiry to 14400s for Cast/share playback URLs.
- Add iPhone fallback copy in `ControllerPlayer`.
- Update Cast docs: stable receiver app ID per env; whitelisted dev/test devices in Google Cast SDK Developer Console; default receiver option documented.
- Add focused tests for transport behaviors.

### Out of Scope

- Native iOS Cast sender app (future milestone).
- Custom Cast receiver HTML/JS app (Default Media Receiver suffices for baked-in lyric MP4s).
- Periodic drift heartbeat (rely on Cast SDK's status stream).
- Custom ack protocol, sequence IDs, or durable session state stored in DB.
- Database schema changes, render pipeline changes, storage changes.
- Controller/projection visual redesign beyond the new top-bar Cast button.
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
```

- **Phone → TV (intent):** Cast: `CastSession.loadMedia`, `RemotePlayerController.play/pause/seek/setVolume/setMutedLevel`. Presentation fallback: `PresentationConnection.send(JSON)`.
- **TV → Phone (status, source of truth):** Cast: `RemotePlayerController` event listeners (`currentTime`, `playerState`, `duration`, `volume`, `isMuted`, `displayName`). The on-phone UI slider/time/playing state always reflects the receiver's actual state.
- **TV on phone reconnect (Wi-Fi blip):** TV keeps playing; phone re-subscribes to status events and re-syncs its local UI to the TV's actual position. No seek is forced on the TV unless the worship leader commands one.
- **TV when worship leader taps seek/prev-song/next-song/lyric-line-jump:** That is commanded intent -> TV seeks (correct and expected).

## Contract Types

In `delivery/webapp/src/types/presentation-api.d.ts`:

```ts
export type PresentationCommand =
  | { type: "play" }
  | { type: "pause" }
  | { type: "seek"; positionSeconds: number }
  | { type: "volume"; level: number }
  | { type: "songTitle"; title: string };

export type PresentationStatus =
  | { type: "ready" }
  | { type: "disconnected" }
  | { type: "error"; message: string };
```

In `delivery/webapp/src/types/cast-sdk.d.ts`:

```ts
declare namespace chrome.cast { /* media + session surface used */ }
declare namespace cast.framework {
  class CastContext { /* getInstance, setOptions, addEventListener */ }
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

## Validation Rules (shared by both transports)

- Ignore malformed JSON.
- Ignore unknown `type` values.
- `seek.positionSeconds` must be finite and `>= 0`.
- `volume.level` must be finite; clamp to `[0, 1]` before invoking callbacks.
- `songTitle.title` and `error.message` must be strings.
- `send()` should no-op when no connected transport exists.

## Implementation Phases

### Phase 1 — Ambient Types

- Create `delivery/webapp/src/types/presentation-api.d.ts` (Presentation API surface the receiver uses today, plus `PresentationStatus`).
- Create `delivery/webapp/src/types/cast-sdk.d.ts` (narrow Cast SDK surface above).
- Remove all 6 `@ts-expect-error` annotations across `usePresentation.ts` (4) and `PrePlayCard.tsx` (2). The 2 in PrePlayCard go away in Phase 7 when the Presentation API code is removed from that file.

### Phase 2 — Cast SDK Loader

Create `delivery/webapp/src/lib/cast/loader.ts`:

- `loadCastSdk(): Promise<void>` injects `https://www.gstatic.com/cv/js/sender/v1/cast_sender.js` once (script-tag ref-counted singleton).
- Sets `window.__onGCastApiAvailable = (loaded) => { ... }` before injection; resolves on `loaded=true`, rejects on `loaded=false`.
- SSR-safe: bail when `typeof window === "undefined"`.
- Exposes `isCastSdkSupported()` that returns true only when both `navigator.presentation` is NOT the test path and `window.chrome?.cast` + `window.cast?.framework` are present.

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
  start: () => Promise<void>; // requestSession + loadMedia
  stop: () => Promise<void>;
  play: () => void;
  pause: () => void;
  seek: (seconds: number) => void;
  setVolume: (level: number) => void;
  setMuted: (muted: boolean) => void;
}
```

Behavior:

- Disabled when no `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` AND no dev default.
- Initialize `CastContext` once on mount with `androidReceiverCompatible: true`, `autoJoinPolicy: tab_and_origin_scoped`, receiver app ID from env.
- Create one `RemotePlayer` + one `RemotePlayerController`; attach all status event listeners once.
- On `start()`: `requestSession()` (user gesture from the controller Cast button), build `chrome.cast.media.MediaInfo` (content type `video/mp4`, metadata title, stream type `BUFFERED`), set `currentTime = startSeconds` in `LoadRequest`, call `session.loadMedia(loadRequest)`.
- On cast `IS_CONNECTED_CHANGED -> false`: emit `isConnected=false`, clear device name.
- On `PLAYER_STATE_CHANGED` / `CURRENT_TIME_CHANGED` / `VOLUME_LEVEL_CHANGED` / `IS_MUTED_CHANGED`: update state fields (this drives phone UI reconciliation — no seeks issued back to TV).
- `seek(seconds)`: clamp to `[0, duration]`; call `controller.seek()`.
- `setVolume(level)`: clamp `[0, 1]`; call `controller.setVolumeLevel()`.
- `stop()`: `CastContext.endCurrentSession()`.
- Cleanup: remove all listeners, release references, never throw.
- Do **not** persist signed URLs, Cast session IDs, or device names to the database.

### Phase 4 — Presentation API Sender Refactor (Fallback Path)

Add `usePresentationSender` to `delivery/webapp/src/hooks/usePresentation.ts` (per the transport-api plan), used only when `useCastTransport` reports `isSupported=false` (Cast unavailable) or when explicitly operating in browser-to-browser dev mode.

Inputs/returns per `transport-api.md` spec (lines 91-128). Wire:

- `sender.isConnected` -> `isPresentationActive`
- `sender.send(command)` -> issued commands flow through the Presentation fallback transport only when Cast is inactive.

Extend `usePresentationReceiver` (per the transport-api plan):

- Use ambient types; drop `@ts-expect-error`.
- Add small validator; clamp volume to `[0, 1]`.
- Return `sendStatus(status: PresentationStatus) => void` so `ProjectionPlayer` can post `ready` and playback-error statuses back to the controller.

`ProjectionPlayer` changes:

- Call `sendStatus({ type: "ready" })` on `loadedmetadata`/`canplay`.
- Call `sendStatus({ type: "error", message })` only on transport-relevant `video.play()` rejections.
- Otherwise unchanged (no visual controls, no drift correction).

### Phase 5 — Controller Pages

Both `delivery/webapp/src/app/songsets/[id]/play/controller/page.tsx` and `delivery/webapp/src/app/share/[token]/play/controller/page.tsx`:

- Remove the dead `window.addEventListener("message", ...)` block (lines 133-151 / 90-108 respectively).
- Remove stub `handlePresentationConnect` / `handlePresentationDisconnect` callbacks.
- Compute media payload:
  - `presentationUrl = /songsets/${songsetId}/play/projection` (or share token equivalent) for the Presentation fallback.
  - `media = { videoUrl, title, source: { kind, idOrToken }, startSeconds: 0 }` for the Cast transport.
- Mount `const cast = useCastTransport({ media })`.
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

- Toast notifications only from transport lifecycle callbacks (`cast.onConnected`, `cast.onDisconnected`, `sender.onStartError`).
- For share mode: ensure `presentationUrl = /share/${token}/play/projection` (no auth on TV).

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
  - `handleToggleMute` -> `send({type:"volume", level: video.muted ? 0 : video.volume})`
  - `handleSkipBack/Forward/PrevSong/NextSong/JumpToChapter/JumpToLine` all funnel through `handleSeek`, so they automatically become seeks on the TV. This satisfies "jump-to-chapter" and "jump-to-lyrics navigation" — they will seek the receiver to the chapter/lyric-line start.
- **Song-change effect** keyed on `currentSongIndex` while `isPresentationActive`: `send({type:"songTitle", title: currentChapter?.songTitle})`. (For Cast, song title is already set via media metadata at `loadMedia`; this is a no-op for Cast and informational for Presentation-fallback.)
- Keep existing "mute local video when presentation active" effect (already present at lines 374-386).
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
- Document: signed URL must cover full set + setup time; if a service runs longer than ~3h40m, callers must re-mint.

### Phase 9 — Docs / Operational

- Update `delivery/webapp/README.md` Cast section:
  - One stable `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` per environment (dev, staging, prod), OR omit and use Google's Default Media Receiver constant.
  - Whitelisted dev/test devices in Google Cast SDK Developer Console.
  - Production Cast approval = later launch gate (not a prerequisite for this dev/test plan).
  - iPhone web does not support Chromecast; use AirPlay to an Apple TV; native iOS sender app future work.
  - Lyrics are baked into the MP4 already; no custom Cast receiver UI needed.
  - 4-hour signed URL expiry during Cast playback.
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

#### New: `src/test/hooks/useCastTransport.test.ts`

- Missing env app ID -> `isSupported=false`, `start()` no-ops.
- SDK unavailable -> `isSupported=false`.
- `requestSession()` success + `loadMedia` success -> `isConnected=true`, `deviceName` set.
- `requestSession()` rejection (user cancel) -> `isConnecting=false`.
- `loadMedia` failure -> `isConnected=false`, error state.
- `RemotePlayerController` event listeners update `currentTime`/`playerState`/`volume`/`isMuted`.
- `seek()`, `setVolume()` clamp out-of-range inputs.
- Cleanup removes all listeners without throwing.
- Reconnect (status event after disconnect) does NOT cause a seek command to be issued to the receiver.

#### Existing: `src/test/hooks/usePresentation.test.ts`

- Drop `@ts-expect-error` references.
- Add receiver `sendStatus` tests + validator/clamp tests per the transport-api plan.

#### New: `src/test/hooks/usePresentationSender.test.ts`

- Per the transport-api plan, lines 251-268.

#### Existing: `src/test/components/play/ControllerPlayer.test.tsx`

- Replace `onPresentationConnect`/`onPresentationDisconnect` default props with the new `transport` / `presentationFallback` / `isCastSupported` / `castAvailability` / `isCastConnecting` / `onSendToTV` / `onSendTransportCommand` props.
- Add: on-phone UI reconciles from `transport.currentTime`/`playerState` when `isPresentationActive=true`.
- Cast button shows in correct states (hidden when unsupported, visible when supported + disconnected, disabled when connecting/unavailable).
- Command forwarding tests: `handlePlayPause`, `handleSeek`, `handleVolumeChange`, `handleToggleMute` each call `onSendTransportCommand` with expected payload (and JSON-serialized for Presentation path).
- Jump-to-chapter and jump-to-lyrics tests: `handleJumpToChapter`/`handleJumpToLine` while `isPresentationActive=true` cause `onSendTransportCommand` with `seek` payload matching the chapter/line-start seconds.
- Song-change effect emits `songTitle` command when `isPresentationActive=true`.
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

#### New: `src/test/api/signed-url-cast-expiry.test.ts`

- `cast=true` query param to `/api/signed-url` yields a URL minted with 14400s expiry.
- Without `cast=true`, default 3600s is preserved.
- `expiresInSeconds` clamped to allowed bounds per existing zod schema.

#### New: `src/test/api/share-token-cast-expiry.test.ts`

- `/api/share/[token]` mints the MP4 URL with 14400s expiry.
- Revoked/expired shares still return 404/410 before minting.

### Phase 11 — Manual Validation

- **Android Chrome phone + whitelisted Chromecast/Google TV on same Wi-Fi.**
  - Start cast from controller -> MP4 loads on TV.
  - Play, pause, seek (slider) on phone -> TV follows.
  - Prev/next song, chapter jump, lyric-line jump on phone -> TV seeks to the correct time.
  - Volume slider + mute toggle on phone -> TV follows.
  - Disconnect TV -> phone badge clears; local video resumes audio (un-muted).
  - Reconnect -> phone UI re-syncs to TV's current position; TV does NOT seek.
  - Long-set validation: play past 60 minutes; verify 4-hour signed URL survives.
- **Laptop-to-laptop (Presentation API fallback):** verify on Chrome desktop that "Send to TV" still works as a fallback when Cast is unavailable, projecting to the existing `/play/projection` route.
- **iPhone Safari:** confirm the UI does not offer broken Chromecast flow and falls back to AirPlay-to-Apple-TV copy.

## Files Touched Summary

| File | Action |
|---|---|
| `src/types/presentation-api.d.ts` | Create — ambient types + shared `PresentationCommand`/`PresentationStatus` |
| `src/types/cast-sdk.d.ts` | Create — ambient Cast SDK types |
| `src/lib/cast/loader.ts` | Create — Cast SDK script loader + `isCastSdkSupported` |
| `src/hooks/useCast.ts` | Create — `useCastTransport` |
| `src/hooks/usePresentation.ts` | Edit — add `usePresentationSender`; add validator/clamp; add `sendStatus`; remove 4 `@ts-expect-error` |
| `src/app/songsets/[id]/play/controller/page.tsx` | Edit — wire both transports; remove dead `window.message` plumbing |
| `src/app/share/[token]/play/controller/page.tsx` | Edit — same with token URL |
| `src/components/play/ControllerPlayer.tsx` | Edit — replace Presentation props with transport props; add Cast button + reconciliation; route intent through transport |
| `src/components/play/PrePlayCard.tsx` | Edit — delete Presentation/Cast launch code, Send-to-TV button, 2 `@ts-expect-error` |
| `src/components/play/ProjectionPlayer.tsx` | Edit — call `sendStatus({type:"ready"})` on `canplay`; `sendStatus({type:"error"})` on play rejection |
| `src/lib/r2/client.ts` | Edit — add `CAST_PLAYBACK_EXPIRES_IN_SECONDS=14400` constant |
| `src/app/api/share/[token]/route.ts` | Edit — mint MP4 with 14400s expiry |
| `src/app/api/signed-url/route.ts` + `shared-handler.ts` | Edit — accept `cast=true` -> use 14400s |
| `src/test/lib/cast/loader.test.ts` | Create |
| `src/test/hooks/useCastTransport.test.ts` | Create |
| `src/test/hooks/usePresentationSender.test.ts` | Create |
| `src/test/hooks/usePresentation.test.ts` | Edit — drop `@ts-expect-error`; add `sendStatus`/validator tests |
| `src/test/components/play/ControllerPlayer.test.tsx` | Edit — new prop shapes, transport-command forwarding, UI reconciliation, reconnect, iPhone copy |
| `src/test/components/play/PrePlayCard.test.tsx` | Edit — remove Send-to-TV tests |
| `src/test/app/controller-page.test.tsx` | Edit — remove postMessage tests; add transport wiring tests |
| `src/test/api/signed-url-cast-expiry.test.ts` | Create |
| `src/test/api/share-token-cast-expiry.test.ts` | Create |
| `delivery/webapp/README.md` | Edit — Cast receiver registration, iPhone fallback, long-URL policy |
| `docs/deployment-plan-webapp*.md` | Edit — Cast receiver registration + 4h URL policy |

## Risks / Open Items

- **Receiver readiness:** If the worship leader hits play on the controller before the TV has loaded the MP4, the receiver's first frames may be missed. Mitigation: surface a "Loading on TV..." state derived from `playerState="buffering"`; disable play/pause intent while buffering.
- **Receiver-as-truth caveats:** Phone-local `<video>` time may briefly disagree with the receiver during connect — the on-phone slider should reflect receiver time, not local time, while casting.
- **Cast SDK production approval:** Required before public launch to non-whitelisted devices. Not a blocker for dev/staging.
- **iPhone -> non-Apple-TV:** Not supported by any web-only path; requires native iOS Cast sender. Documented future work.
- **Operator Wi-Fi:** Phone and Chromecast must be on the same LAN; corporate/captive-portal Wi-Fi can block Cast discovery. Runbook note in README.
- **R2 reachability from the TV network:** Cast receiver fetches the MP4 directly; R2 must be reachable from the TV's network with `Content-Type: video/mp4` and range support.

## Acceptance Criteria

- The three existing plan files are not edited.
- Cast SDK types compile without `@ts-expect-error`.
- Controller pages own the Cast transport and Presentation fallback.
- Receiver-as-truth: phone UI reconciles to `RemotePlayerController` events; no silent reconnect-induced seeks hit the TV.
- Dead `window.postMessage` presentation plumbing is removed from both controller pages.
- `PrePlayCard` no longer owns Presentation API launch, Cast detection, or Send-to-TV UI.
- Cast-targeted signed URLs use 14400s expiry; non-cast signed URLs remain at 3600s.
- iPhone web shows a clear fallback (AirPlay to Apple TV) instead of a broken Chromecast button.
- Existing playback behavior remains unchanged on the phone-local video element except for pause/mute while casting.
- Tests cover: Cast loader, `useCastTransport` lifecycle/send/receive/cleanup/reconnect, Presentation sender/receiver, command forwarding in `ControllerPlayer`, jump-to-chapter/jump-to-lyrics transport, share-mp4 4h expiry, signed-url `cast=true` expiry.
- `pnpm lint && pnpm typecheck && pnpm test && pnpm build` all pass from `delivery/webapp/`.
