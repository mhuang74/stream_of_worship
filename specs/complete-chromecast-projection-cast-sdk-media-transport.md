# Implementation Plan: Complete Chromecast Projection with Google Cast Media Transport

## Summary

Replace the Presentation API sender-bridge direction with a Google Cast SDK media
transport. The Presentation API is useful for two-browser projection prototypes, but
it is not the right primary transport for phone-to-Chromecast-to-TV worship playback.

Primary v1 target: Android Chrome web sender plus desktop/ChromeOS Chrome where the
Google Cast Web Sender SDK is available. iPhone web Chromecast is not treated as
supported; iPhone support is a separate future native sender app using the Google
Cast iOS SDK.

References:

- Google Cast Web Sender SDK: https://developers.google.com/cast/docs/web_sender/integrate
- Google Cast media/CORS guidance: https://developers.google.com/cast/docs/media
- Google Cast iOS Sender SDK: https://developers.google.com/cast/docs/ios_sender
- MDN Presentation API: https://developer.mozilla.org/en-US/docs/Web/API/Presentation_API

## Key Changes

- Add a `CastTransport` sender abstraction instead of `usePresentationSender`.
- Load the Google Cast Web Sender SDK client-side only and initialize
  `cast.framework.CastContext` with `NEXT_PUBLIC_CAST_RECEIVER_APP_ID`.
- Use Cast media APIs for playback: `CastSession.loadMedia`, `RemotePlayer`, and
  `RemotePlayerController`.
- Register one stable Cast receiver app ID per environment, not dynamic
  `/songsets/{id}/play/projection` or `/share/{token}/play/projection` URLs.
- Load the selected rendered MP4 into the Cast session at cast start with title,
  content type `video/mp4`, and start time.
- Keep existing projection pages for direct browser/prototype projection, but do not
  use Presentation API as the primary Chromecast path.
- Remove the `Send to TV` button and Presentation API launch logic from
  `PrePlayCard`.

## Implementation Plan

### Cast SDK Types and Loader

- Create narrow ambient types for only the Cast SDK surface the app uses:
  `chrome.cast`, `cast.framework.CastContext`, `CastSession`, `RemotePlayer`,
  `RemotePlayerController`, relevant event types, `MediaInfo`, and `LoadRequest`.
- Add a client-only SDK loader that injects the Cast sender script once, handles
  `__onGCastApiAvailable`, and reports a clear unsupported state when the SDK is not
  available.
- Guard all SDK access behind `typeof window !== "undefined"` and never reference
  Cast globals during SSR.

### `useCastTransport`

Add `useCastTransport({ media })` in the webapp hooks layer.

Inputs:

```ts
interface CastTransportMedia {
  videoUrl: string;
  title: string;
  startSeconds?: number;
  autoplay?: boolean;
  source: { kind: "songset" | "share"; idOrToken: string };
}
```

Return value:

```ts
interface CastTransportResult {
  isSupported: boolean;
  isAvailable: boolean;
  isConnecting: boolean;
  isConnected: boolean;
  deviceName: string | null;
  playerState: "idle" | "buffering" | "playing" | "paused" | "unknown";
  currentTime: number;
  duration: number;
  volume: number;
  isMuted: boolean;
  start: () => Promise<void>;
  stop: () => Promise<void>;
  play: () => void;
  pause: () => void;
  seek: (seconds: number) => void;
  setVolume: (level: number) => void;
  setMuted: (muted: boolean) => void;
}
```

Behavior:

- Treat missing `NEXT_PUBLIC_CAST_RECEIVER_APP_ID`, missing SDK, or unsupported
  browser as disabled.
- Initialize `CastContext` once with the configured receiver app ID and default
  Cast media options.
- On `start()`, request a Cast session from the user's click path, create a
  `chrome.cast.media.MediaInfo` for the rendered MP4, and call `loadMedia`.
- Use `RemotePlayerController` event listeners as the source of truth for playback
  state, time, duration, volume, mute, device name, and connected status.
- Clamp seeks to valid media bounds and clamp volume to `[0, 1]`.
- Cleanup must remove all Cast event listeners and detach local references without
  throwing.
- Do not persist signed URLs, Cast session IDs, or device names to the database.

### Controller Pages

Update both controller routes to own the Cast transport:

- `delivery/webapp/src/app/songsets/[id]/play/controller/page.tsx`
- `delivery/webapp/src/app/share/[token]/play/controller/page.tsx`

For both pages:

- Load the existing rendered MP4 and chapters as today.
- Request Cast-safe MP4 URLs whose expiry covers the full worship set plus setup
  time, e.g. `expiresInSeconds=14400`.
- Pass `{ videoUrl, title, source }` into `useCastTransport`.
- Remove dead `window.postMessage` presentation plumbing and
  `onPresentationConnect` / `onPresentationDisconnect` callbacks.
- Pass Cast state and Cast actions into `ControllerPlayer`.

For share mode:

- Ensure the public share endpoint can provide Cast-safe MP4 URLs with the longer
  expiry without requiring TV authentication.
- Preserve share revocation and expiry checks before returning any playback URL.

### `ControllerPlayer`

Replace Presentation props with Cast transport props:

```ts
interface ControllerPlayerProps {
  playerId: string;
  videoSrc: string;
  chapters: Chapter[];
  cast?: CastTransportResult;
  exitRoute?: string;
  autoFullscreen?: boolean;
  className?: string;
}
```

Behavior:

- Add a Cast button in the controller top bar, visible only when Cast is supported
  and disconnected.
- Disable the Cast button while connecting or when no Cast devices are available.
- Show connected state as `Connected to {deviceName}` when available, otherwise
  `Connected to TV`.
- While cast is active, route play, pause, seek, previous/next song, chapter jump,
  lyric-line jump, volume, and mute commands to the Cast transport.
- Pause and mute the local video while cast is active to avoid duplicate playback,
  duplicate network load, and local/remote clock drift.
- Keep lyric/song jump controls available in cast mode so the worship leader can
  navigate the set from the phone.
- Update iPhone copy: web Chromecast control is not supported from iPhone in this
  milestone; use AirPlay/HDMI operationally, with native iOS Cast sender as future
  work.

### `PrePlayCard`

- Delete Presentation API availability state.
- Delete `PresentationRequest` construction.
- Delete `handleSendToTV`.
- Delete the pre-play `Send to TV` button block.
- Keep Start Worship, Share, render status, song list, and offline status behavior
  unchanged.

### Docs and Operational Setup

- Correct existing Cast docs that imply dynamic projection URLs should be registered
  as receiver URLs.
- Document one stable receiver app ID per environment:
  development, staging/preview, and production.
- Keep `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` as the feature gate.
- Document that dev/test requires Cast devices whitelisted in the Google Cast SDK
  Developer Console.
- Document production Cast approval as a later launch gate, not a prerequisite for
  this dev/test implementation.

## Robustness and Runtime Concerns

- Cast receivers fetch MP4 files directly, so R2 responses must be reachable by the
  receiver, have `Content-Type: video/mp4`, and support the access pattern required
  by Chromecast playback.
- Signed URLs must remain valid for the complete worship set plus setup time. The
  current one-hour default is risky for long rehearsals or services.
- Fail closed when Cast is unavailable: no broken button, no global-reference crash,
  and no misleading Presentation API fallback labeled as Chromecast.
- Avoid periodic heartbeat, custom ack protocols, or bespoke drift correction in v1;
  Cast media status events are the playback source of truth.
- Do not add database writes, schema changes, render mutations, storage mutations, or
  durable session state.

## Test Plan

Run from `delivery/webapp/`:

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

Add or update focused tests:

- Cast SDK loader: script injection happens once, success callback resolves, failure
  reports unsupported, and SSR does not touch `window`.
- `useCastTransport`: missing app ID, unsupported SDK, no devices, session start
  success/failure, `loadMedia` success/failure, remote play/pause/seek/volume/mute,
  connected/disconnected events, event-listener cleanup, and clamped invalid inputs.
- Controller pages: songset and share pages pass the correct media payload and
  Cast-safe URL expiry into the transport hook.
- `ControllerPlayer`: Cast button states, connected device label, iPhone fallback
  copy, local video paused/muted while casting, remote commands for all controller
  actions, and lyric jump controls available in cast mode.
- `PrePlayCard`: obsolete Send-to-TV and Presentation API tests removed; Start
  Worship and Share behavior still covered.
- Deployment/docs tests: stable Cast receiver registration and whitelisted dev/test
  devices are documented.

Manual validation:

- Android Chrome phone and a whitelisted Chromecast/Google TV on the same Wi-Fi.
- Start cast from controller, verify MP4 loads on TV.
- Verify play, pause, seek, previous/next song, chapter jump, lyric-line jump,
  volume, mute, disconnect, and reconnect.
- Verify playback remains smooth past one hour using a long-expiry URL.
- Verify iPhone web UI does not offer a broken Chromecast flow.

## Assumptions

- v1 uses Google Cast Web Sender SDK for web.
- Native iOS Cast sender is future work and should be planned separately.
- Rendered lyrics are already baked into the MP4, so no custom receiver lyric overlay
  is required for v1.
- The existing plan files are left unchanged.
