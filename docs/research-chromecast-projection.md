# Research Note: Chromecast / Projection Feature

## Overview

The webapp implements a **two-screen worship projection** model using the native **W3C Presentation API** (not the Google Cast SDK). An operator controls playback on a "controller" screen while lyrics video plays chrome-free on a second "projection" screen (e.g., TV).

- **Protocol:** JSON `{type: "play"|"pause"|"seek"|"volume"|"songTitle", ...}` over `PresentationConnection`
- **Sender:** `PresentationRequest.start()` opens the projection page
- **Receiver:** `navigator.presentation.receiver` listens for commands and drives a `<video>` element
- No `chrome.cast` / `cast.framework` / RemotePlayback APIs are used anywhere

## Architecture

```
PrePlayCard (sender UI)
       │
       │ new PresentationRequest([/songsets/{id}/play/projection])
       │ request.start()
       ▼
ControllerPlayer ──(gap: connection never captured)── ProjectionPlayer (receiver)
       │                                                    │
       │ window.postMessage({type:"presentation",          │ usePresentationReceiver
       │   action:"connected"|"disconnected"})             │   ↳ onPlay/onPause/onSeek
       ▼                                                    │     /onVolume/onSongTitle
  Controller page wrapper                                  ▼
                                                   <video> on receiver page
```

## Components

| File | Role | State |
|---|---|---|
| `src/hooks/usePresentation.ts` | `usePresentationReceiver` — subscribes to receiver API, parses commands, dispatches to callbacks | Complete, tested |
| `src/hooks/useWakeLock.ts` | Keeps both screens awake; re-acquires on visibility change | Complete, tested |
| `src/components/play/ProjectionPlayer.tsx` | Receiver-side fullscreen chrome-free video with title overlay | Complete, tested |
| `src/components/play/PrePlayCard.tsx` | Detects availability, launches projection via `request.start()` | Partial — launches but doesn't capture connection |
| `src/components/play/ControllerPlayer.tsx` | Operator video player; shows "Connected to TV" badge, mutes local audio when projection active | Partial — indicator logic exists, no command sending |

Pages: `/songsets/[id]/play/projection`, `/songsets/[id]/play/controller`, `/share/[token]/play/{projection,controller}`.

## What Works (Receiver Side)

- `ProjectionPlayer` renders fullscreen black video, landscape-locks orientation, keeps screen awake
- `usePresentationReceiver` correctly subscribes to `receiver.connectionList`, handles new connections, dispatches all 5 command types
- Malformed JSON gracefully ignored; `close`/`terminate` events call `onDisconnected`
- PrePlayCard's `getAvailability()` reactively enables/disables the "Send to TV" button
- ControllerPlayer shows "Connected to TV" badge and mutes local audio when projection is active

## Critical Gap: Sender Bridge Not Implemented

The receiver listens for `play`/`pause`/`seek`/`volume`/`songTitle` commands, but **no code sends them**.

Evidence:
- No `connection.send(...)`, `presentationConnection`, or `JSON.stringify({type:"play"...})` calls anywhere (excluding tests)
- The `PresentationRequest` in `PrePlayCard.handleSendToTV` is local — never stored in ref/state
- `ControllerPlayer` accepts `onPresentationConnect`/`onPresentationDisconnect` props but never calls them — they're invoked indirectly by the page wrapper via `window.postMessage`
- Controller pages listen for `window.postMessage({type:"presentation", action:"connected"})`, but projection runs in a separate browsing context — the projection page never calls `window.opener.postMessage`, so that path is dead
- ControllerPlayer never observes its own `play`/`pause`/`seek`/`timeupdate` events to forward them

**Consequence:** Casting opens the video on a second screen, but operator play/pause/seek/volume/song changes are not mirrored.

## Secondary Issues

1. **Auth-required URL for casting** — `PrePlayCard` targets `/songsets/{id}/play/projection` (requires login). The token-based `/share/[token}/play/projection` exists but isn't wired in. TVs not logged in will 401.
2. **Availability listener leak** — `getAvailability()` `change` listener is registered but never cleaned up; `PresentationRequest` is recreated per `songset.id` change without disposal.
3. **TypeScript fragility** — 5+ `@ts-expect-error` annotations across `usePresentation.ts` and `PrePlayCard.tsx` because the Presentation API isn't in TS DOM lib types.
4. **Skipped tests** — PrePlayCard's availability-positive tests are `test.skip` ("requires complex async setup").
5. **Test prop mismatch** — `ControllerPlayer.test.tsx` default props use `songsetId`, but the component's prop is `playerId` (hidden by mocking).

## Recommended Next Steps

To complete the feature, the missing sender bridge needs:

1. **Hoist the PresentationConnection** — `PrePlayCard.handleSendToTV` must capture the `PresentationConnection` returned by `request.start()` and surface it (e.g., via context, ref, or a dedicated `usePresentationSender` hook)
2. **Forward ControllerPlayer events** — wire `onPlay`, `onPause`, `onSeek`, `volume` changes, and song-title changes to `connection.send(JSON.stringify(command))`
3. **Bridge page wrapper listeners** — replace `window.postMessage` plumbing with direct connection lifecycle callbacks (the projection page should emit `connected`/`disconnected` via actual Presentation API events, not window messaging)
4. **Support token URL** — allow `PrePlayCard` to cast the `/share/[token}/play/projection` URL when streaming via share link
5. **Cleanup listeners** — remove the `availability.change` listener on unmount and dispose prior `PresentationRequest` on `songset.id` change
