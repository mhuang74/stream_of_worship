# Implementation Plan: Projection Transport API Only

## Goal

Complete the projection transport API boundary for the existing W3C Presentation API approach. This plan is limited to connection lifecycle, message send/receive, typed wire contracts, cleanup, and minimal page/component integration needed to use the transport.

This plan intentionally does **not** implement broader playback-smoothness work, UX redesign, Google Cast SDK support, drift correction, or render/data changes.

## Background

The current webapp already has the receiver-side projection route and `ProjectionPlayer`, plus a `usePresentationReceiver` hook that receives JSON commands over `PresentationConnection`. The missing piece is the sender bridge: `PrePlayCard` launches a presentation but does not retain the `PresentationConnection`, while the controller pages listen for dead `window.postMessage` events that the projection page never sends.

The transport connection must be created and owned by the controller document, because that is where playback commands originate.

## Scope

### In Scope

- Add minimal TypeScript declarations for the Presentation API used by this app.
- Add a sender-side hook for Presentation API connection lifecycle and message sending.
- Tighten the receiver-side hook as the matching transport endpoint.
- Move Presentation API launch ownership from `PrePlayCard` to controller pages.
- Remove dead `window.postMessage` presentation plumbing.
- Preserve the existing command protocol with only a small receiver-to-sender status channel.
- Add focused tests for transport behavior and minimal page/component integration.

### Out of Scope

- Google Cast SDK sender/receiver integration.
- `NEXT_PUBLIC_CAST_RECEIVER_APP_ID` behavior changes.
- Playback drift correction, periodic resync, retries, sequence IDs, acknowledgements, or durable reconnect IDs.
- Full state snapshot protocol.
- Controller/projection visual redesign.
- Database, auth, render, artifact, or storage changes.
- Any implementation beyond transport API wiring.

## Transport Contract

Keep the existing controller-to-projection command shape as the public v1 transport command:

```ts
export type PresentationCommand =
  | { type: "play" }
  | { type: "pause" }
  | { type: "seek"; positionSeconds: number }
  | { type: "volume"; level: number }
  | { type: "songTitle"; title: string };
```

Add a small projection-to-controller status shape:

```ts
export type PresentationStatus =
  | { type: "ready" }
  | { type: "disconnected" }
  | { type: "error"; message: string };
```

Validation rules:

- Ignore malformed JSON.
- Ignore unknown `type` values.
- `seek.positionSeconds` must be finite and `>= 0`.
- `volume.level` must be finite; clamp to `[0, 1]` before invoking callbacks.
- `songTitle.title` and `error.message` must be strings.
- `send()` should no-op when no connected transport exists.

## Implementation Changes

### Types

Create `delivery/webapp/src/types/presentation-api.d.ts` with minimal ambient declarations for the Presentation API surface the app uses:

- `PresentationRequest`
- `PresentationConnection`
- `PresentationConnectionList`
- `PresentationAvailability`
- `PresentationConnectionState`
- `PresentationConnectionAvailableEvent`
- `navigator.presentation.receiver`

Keep these declarations narrow. Do not model unused W3C fields.

### Hook: `usePresentationSender`

Add `usePresentationSender` to `delivery/webapp/src/hooks/usePresentation.ts`.

Inputs:

```ts
export interface UsePresentationSenderOptions {
  presentationUrl: string;
  onReady?: () => void;
  onError?: (message: string) => void;
  onStartError?: (error: unknown) => void;
  onConnected?: () => void;
  onDisconnected?: () => void;
}
```

Return value:

```ts
export interface UsePresentationSenderResult {
  isSupported: boolean;
  availability: "unknown" | "available" | "unavailable";
  isConnecting: boolean;
  isConnected: boolean;
  start: () => Promise<void>;
  close: () => void;
  terminate: () => void;
  send: (command: PresentationCommand) => void;
}
```

Behavior:

- Treat the API as supported only when both `navigator.presentation` and global `PresentationRequest` are present.
- Create a `PresentationRequest` for the current `presentationUrl`.
- Use `getAvailability()` when available; set `availability` to `"unknown"` if probing fails or is unsupported.
- Attach and remove availability `change` listeners.
- `start()` must be called from the controller UI click path. It starts the request, stores the returned connection, attaches connection listeners, and updates connection state.
- Connection `connect` sets `isConnected=true` and calls `onConnected`.
- Connection `close` and `terminate` set `isConnected=false` and call `onDisconnected`.
- Incoming messages from the receiver are parsed as `PresentationStatus`.
- `send()` serializes valid `PresentationCommand` objects with `JSON.stringify` and sends only when the connection state is `"connected"`.
- Cleanup removes listeners and closes the active connection without throwing.
- Use an `optionsRef` pattern so callbacks stay fresh without re-creating listeners unnecessarily.

### Hook: `usePresentationReceiver`

Keep the existing receiver hook in `delivery/webapp/src/hooks/usePresentation.ts`, but make it the explicit receiving half of the transport API.

Changes:

- Use the new ambient types instead of `@ts-expect-error`.
- Parse inbound data through a small validator before invoking callbacks.
- Clamp volume values to `[0, 1]`.
- Track active receiver connections in a ref.
- Return a `sendStatus(status: PresentationStatus) => void` helper so receiver components can send status messages back to the controller.
- Remove listeners for connection `message`, `close`, `terminate`, and `connectionavailable` during cleanup.

Receiver options remain command-oriented:

```ts
export interface UsePresentationReceiverOptions {
  onPlay?: () => void;
  onPause?: () => void;
  onSeek?: (positionSeconds: number) => void;
  onVolume?: (level: number) => void;
  onSongTitle?: (title: string) => void;
  onConnected?: () => void;
  onDisconnected?: () => void;
}
```

### Controller Pages

Update both controller routes to own the sender hook:

- `delivery/webapp/src/app/songsets/[id]/play/controller/page.tsx`
  - Use `presentationUrl = /songsets/${songsetId}/play/projection`.
- `delivery/webapp/src/app/share/[token]/play/controller/page.tsx`
  - Use `presentationUrl = /share/${token}/play/projection`.

For both pages:

- Remove `window.addEventListener("message", ...)` presentation logic.
- Remove local `handlePresentationConnect` and `handlePresentationDisconnect` callbacks.
- Use `usePresentationSender`.
- Pass `sender.isConnected` as `isPresentationActive`.
- Pass sender availability/connection state and sender actions into `ControllerPlayer`.
- Show toast notifications only from sender lifecycle callbacks.

### `ControllerPlayer`

Make only transport-boundary prop changes:

```ts
export interface ControllerPlayerProps {
  playerId: string;
  videoSrc: string;
  chapters: Chapter[];
  isPresentationActive?: boolean;
  isCastSupported?: boolean;
  castAvailability?: "unknown" | "available" | "unavailable";
  isCastConnecting?: boolean;
  onSendToTV?: () => void;
  onSendPresentationCommand?: (command: PresentationCommand) => void;
  exitRoute?: string;
  autoFullscreen?: boolean;
  className?: string;
}
```

Changes:

- Remove `onPresentationConnect` and `onPresentationDisconnect`.
- Add a top-bar `Send to TV` control that calls `onSendToTV`.
- Show the control when `isCastSupported` is true and `isPresentationActive` is false.
- Disable the control while `isCastConnecting` is true.
- If `castAvailability === "unavailable"`, disable the control and show copy indicating cast is unavailable.
- If `castAvailability === "unknown"`, allow the click; user gesture should still call `start()`.
- Forward existing user-initiated controller actions through `onSendPresentationCommand` only when `isPresentationActive` is true:
  - play/pause
  - seek
  - volume
  - song title when current chapter changes
- Do not add drift correction, periodic snapshots, full state sync, or new playback policy.

### `ProjectionPlayer`

Keep projection behavior unchanged except for minimal transport status integration:

- Consume the `sendStatus` helper returned by `usePresentationReceiver`.
- Send `{ type: "ready" }` once the projection video can receive commands, preferably after `loadedmetadata` or `canplay`.
- Send `{ type: "error", message }` only for transport-relevant playback failures, such as `video.play()` rejection.
- Do not add visual controls, drift correction, or state snapshot handling.

### `PrePlayCard`

Remove Presentation API ownership from `PrePlayCard`:

- Delete Presentation API availability state.
- Delete `PresentationRequest` construction.
- Delete `handleSendToTV`.
- Delete the Send-to-TV button block.
- Keep Start Worship, Share, render status, song list, and offline status behavior unchanged.

## UX Notes

- The transport action belongs on the controller screen, not pre-play, because the controller document must retain the `PresentationConnection`.
- Unsupported browsers simply do not show the transport action.
- Unknown availability should not block the user; `PresentationRequest.start()` is still the authoritative user-gesture operation.
- The connected indicator remains the existing projection-active signal.
- Playback-smoothness problems after transport wiring should be measured and addressed in a separate plan.

## Operational Notes

- This plan uses the W3C Presentation API only. It does not activate or depend on the Google Cast SDK.
- Existing Google Cast SDK environment documentation can remain as future-work context, but should not be wired to this feature in this pass.
- Real device validation still requires a browser/device combination that supports the Presentation API and an environment that satisfies secure-context requirements.
- Share-mode casting should use the share projection URL so unauthenticated projection displays do not need a logged-in session.

## Test Plan

Run tests from `delivery/webapp`.

### Hook Tests

Add or update tests under `delivery/webapp/src/test/hooks/usePresentation.test.ts` or a focused sender test file.

Sender cases:

- Unsupported API returns `isSupported=false` and no-ops.
- Missing global `PresentationRequest` returns `isSupported=false`.
- Availability resolves available/unavailable.
- Availability probe failure maps to `"unknown"`.
- Availability `change` listener updates state.
- Cleanup removes availability listener.
- `start()` success stores connection and sets connected state.
- `start()` rejection clears connecting state and calls `onStartError`.
- `send()` serializes valid commands only while connected.
- `send()` no-ops while disconnected.
- Incoming `ready` status calls `onReady`.
- Incoming `error` status calls `onError`.
- Close and terminate events mark disconnected and call `onDisconnected`.
- Unmount removes connection listeners and closes the active connection.

Receiver cases:

- Existing connections are attached.
- New `connectionavailable` connections are attached.
- Valid `play`, `pause`, `seek`, `volume`, and `songTitle` commands dispatch callbacks.
- Invalid seek/volume/title payloads are ignored or clamped as specified.
- Malformed JSON and unknown command types are ignored.
- `sendStatus` serializes status to all connected receiver connections.
- Cleanup removes connection listeners and `connectionavailable` listener.

### Page Tests

Update controller page tests:

- Authenticated controller passes `/songsets/{id}/play/projection` to `usePresentationSender`.
- Share controller passes `/share/{token}/play/projection` to `usePresentationSender`.
- `sender.isConnected` drives `ControllerPlayer.isPresentationActive`.
- Remove tests for old `window.postMessage` presentation events.

### Component Tests

Update `ControllerPlayer` tests:

- Send-to-TV control renders only when supported and disconnected.
- Control calls `onSendToTV`.
- Connecting state disables the control.
- Unavailable state disables the control.
- Unknown availability still allows the control.
- Existing play/pause/seek/volume user actions forward transport commands when presentation is active.
- No transport commands are sent when presentation is inactive.
- Song title command is emitted when active and current chapter changes.

Update `ProjectionPlayer` tests:

- Sends `ready` after video readiness event.
- Sends `error` on `play()` rejection.
- Existing command callbacks still control the video.

Update `PrePlayCard` tests:

- Remove obsolete Send-to-TV and availability tests.
- Verify existing Start Worship and Share behavior still works.

### Verification Commands

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

## Acceptance Criteria

- The existing plan file is not edited.
- Transport API types compile without `@ts-expect-error`.
- Controller pages own sender connections.
- Receiver hook and sender hook communicate with typed JSON messages.
- Dead `window.postMessage` presentation plumbing is removed.
- PrePlayCard no longer owns Presentation API launch or availability logic.
- Existing playback behavior remains unchanged except for transport command forwarding.
- Tests cover unsupported API, lifecycle, send/receive, cleanup, and page wiring.
