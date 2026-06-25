# Implementation Plan: Complete the Chromecast / Projection Feature

## Goal
Wire the missing sender bridge so that when the operator plays/pauses/seeks/changes volume/changes songs on the controller screen, the projection screen follows in real time. Restructure the cast launch so the `PresentationConnection` lives in the controller page (where commands originate), replacing the current dead `window.postMessage` plumbing.

## Architectural Decisions (confirmed via clarifications)
- New hook: `usePresentationSender` — lives next to `usePresentationReceiver` in `src/hooks/usePresentation.ts`.
- Cast launch moves **out** of `PrePlayCard` and **into** the controller page; the controller page's hook calls `PresentationRequest.start()` so the connection lives in the same document that sends commands. (PresentationConnection objects are tied to the document that created them and cannot be passed across navigation, so this solves the "PrePlayCard creates the connection but ControllerPlayer can't reach it" gap.)
- Token URL support: controller pages compute `/share/{token}/play/projection` when in share mode, so casting works on TVs that aren't logged in.
- Local ambient `.d.ts` file removes all `@ts-expect-error` annotations.
- Sync strategy: event-driven only (forward user-initiated play/pause/seek/volume/songTitle changes; no periodic heartbeat).

---

## Phase 1 — TypeScript Types & Shared Exports

### 1.1 Add `src/types/presentation-api.d.ts`
Ambient declarations for: `PresentationRequest`, `PresentationConnection`, `PresentationConnectionList`, `PresentationAvailability`, `PresentationConnectionStateEvent`, `navigator.presentation` (sender side: `defaultRequest` + receiver side: `receiver`). Mirror the W3C Presentation API surface the app uses; keep types minimal but accurate.

### 1.2 Update `src/hooks/usePresentation.ts`
- Keep `PresentationCommand` and `usePresentationReceiver` as-is (export `PresentationCommand` remains the shared wire-format type — already exported).
- Remove all 4 `@ts-expect-error` annotations in the receiver hook now that types land via `.d.ts`.

---

## Phase 2 — `usePresentationSender` Hook

### 2.1 New hook in `src/hooks/usePresentation.ts`
Add a `usePresentationSender(options)` hook mirroring the receiver's structure (`optionsRef` + `useLayoutEffect` for stale-closure safety, single `useEffect([])` for setup).

**Options:**
```ts
export interface UsePresentationSenderOptions {
  presentationUrl: string;                 // computed by caller
  onStartError?: (err: unknown) => void;   // e.g. user cancels picker
  onConnected?: () => void;                // connection.connect
  onDisconnected?: () => void;             // connection.close / terminate
}
```

**Returns:**
```ts
{
  isSupported: boolean;        // "presentation" in navigator
  isCastAvailable: boolean;    // from getAvailability() + change listener
  isConnecting: boolean;       // start() pending
  isConnected: boolean;        // connection in "connected" state
  start: () => Promise<void>;  // new PresentationRequest([url]).start()
  terminate: () => void;       // connection.terminate()
  send: (cmd: PresentationCommand) => void;  // JSON.stringify + connection.send
}
```

**Implementation notes:**
- Hoist `PresentationRequest` into a `useRef`, recreate only when `presentationUrl` changes; dispose previous `availability` `change` listener on recreation (fixes the documented leak).
- On `start()`: build `PresentationRequest([presentationUrl])`, store connection ref returned by `request.start()`, attach listeners (`connect` → `isConnected=true` + `onConnected`, `close`/`terminate` → `isConnected=false` + `onDisconnected`).
- `send(cmd)`: bail silently if `!connectionRef.current` or `state !== "connected"`.
- `useEffect` cleanup: remove `availability.change` listener, call `connection.close()`.
- SSR guard: `typeof navigator === "undefined"` returns.

### 2.2 Sender tests at `src/test/hooks/usePresentationSender.test.ts`
Mirror the receiver test patterns exactly (`Object.defineProperty(navigator, "presentation", { value, writable:true, configurable:true })` in `beforeEach`, reset to `undefined` in `afterEach`, capture handlers via spy-in-`addEventListener`, flush promises with `await act(async () => { await Promise.resolve(); })`).

**Cases:**
- SSR / no `navigator.presentation` → hook no-ops, `isSupported=false`
- `presentation` present but no global `PresentationRequest` constructor → `isSupported=false`
- `getAvailability()` resolves `true`/`false` → `isCastAvailable` toggles; `change` event updates state
- `start()` resolves → connection listeners attached, `isConnected=true`, `onConnected` fired
- `start()` rejects (user cancels) → `isConnecting=false`, `onStartError` fired
- `send({type:"play"})` → `mockConnection.send` called with `JSON.stringify({type:"play"})`
- `send()` while `isConnected=false` is a no-op (no throw)
- `connection.close` event → `isConnected=false`, `onDisconnected` fired
- `connection.terminate` event → `isConnected=false`, `onDisconnected` fired
- unmount → `connection.close()` called, `availability.change` listener removed

---

## Phase 3 — Plumb the Controller Page (Sender Side)

### 3.1 `src/app/songsets/[id]/play/controller/page.tsx`
- Compute `presentationUrl = \`/songsets/${songsetId}/play/projection\``.
- Remove the dead `window.addEventListener("message", ...)` block entirely.
- Call `const sender = usePresentationSender({ presentationUrl, onConnected: () => toast.success("Connected to projection"), onDisconnected: () => toast.info("Disconnected from projection") })`.
- Render `<ControllerPlayer ... isPresentationActive={sender.isConnected} isCastSupported={sender.isSupported} isCastAvailable={sender.isCastAvailable} isCastConnecting={sender.isConnecting} onSendToTV={sender.start} onSendPresentationCommand={sender.send} />`.
- Delete the now-unused `handlePresentationConnect`/`handlePresentationDisconnect` callbacks.

### 3.2 `src/app/share/[token]/play/controller/page.tsx`
Same as 3.1 with `presentationUrl = \`/share/${token}/play/projection\``. This closes the auth-gap finding — when an unauthenticated TV opens the share-projection URL, it works without login.

### 3.3 `src/components/play/ControllerPlayer.tsx`
- **New props on `ControllerPlayerProps`** (replace the dead `onPresentationConnect`/`onPresentationDisconnect` pair):
  ```ts
  isCastSupported?: boolean;
  isCastAvailable?: boolean;
  isCastConnecting?: boolean;
  onSendToTV?: () => void;
  onSendPresentationCommand?: (cmd: PresentationCommand) => void;
  ```
- Top bar: keep the existing "Connected to TV" badge when `isPresentationActive`. **Add** a "Send to TV" button in the top bar (visible when `isCastSupported`), with three states: available (`onSendToTV`), `isCastConnecting` (spinner), `!isCastAvailable` (disabled with tooltip "Cast unavailable"). Hide the button once `isPresentationActive` is true (already connected).
- **Wire command forwarding** in the existing handlers — call `onSendPresentationCommand?.(...)` at the end of each (guarded by `isPresentationActive`):
  - `handlePlayPause` → `send({type: isPlaying ? "pause" : "play"})` (note: state update order — send the *intended* new state, not the pre-toggle state)
  - `handleSeek(time)` → `send({type:"seek", positionSeconds: clampedTime})`
  - `handleVolumeChange(newVolume)` → `send({type:"volume", level: newVolume})`
  - `handleToggleMute` → `send({type:"volume", level: video.muted ? 0 : video.volume})`
  - `handleSkipBack/Forward/PrevSong/NextSong/JumpToChapter/JumpToLine` → all funnel through `handleSeek`, so no additional wiring needed.
- **Add an effect** that watches `currentSongIndex` and emits `send({type:"songTitle", title: currentChapter?.songTitle})` when (a) `isPresentationActive` and (b) index changes. Also send on initial connection (covered by Phase 5 below).
- Keep the existing "mute local video when presentation active" effect untouched.
- Keep the existing "hide LyricJumpList when presentation active" behavior (operator can still see transport controls and the lyric indicator on the controller; projection shows lyrics video).

### 3.4 `src/components/play/PrePlayCard.tsx`
- Delete: `isPresentationAvailable`/`isCastAvailable` state, the `checkPresentationAvailability` effect, `handleSendToTV`, the `<Monitor>` Send to TV button block, and the `Monitor` import.
- Delete: both `new PresentationRequest([...])` calls (lines 90 and 158).
- Delete: the `@ts-expect-error` annotations removed naturally with the code.
- Note that Presentation API detection now lives entirely in the controller page via the hook — PrePlayCard is pure presentation again.

### 3.5 Update existing tests

#### `src/test/components/play/ControllerPlayer.test.tsx`
- Replace the `onPresentationConnect`/`onPresentationDisconnect` default props with the new `isCastSupported`/`isCastAvailable`/`isCastConnecting`/`onSendToTV`/`onSendPresentationCommand` props (also fix the `songsetId` → `playerId` mismatch that's currently hidden by mocking).
- New tests: send-command forwarding (assert `onSendPresentationCommand` called with `JSON.stringify({type:"play"})` when `handlePlayPause` clicked while `isPresentationActive=true`; same for seek/volume).
- New test: "Send to TV" button renders when `isCastSupported=true`, hidden when `isPresentationActive=true`, shows spinner when `isCastConnecting=true`.
- New test: song-change effect emits `songTitle` command when `isPresentationActive=true`.

#### `src/test/components/play/PrePlayCard.test.tsx`
- Delete the `Send to TV` button-presence/absence tests entirely (button is gone).
- Delete the `test.skip` availability-positive tests — they're obsolete now.

#### `src/test/app/controller-page.test.tsx`
- Delete the `window.postMessage({type:"presentation", action:"connected"|"disconnected"})` tests — that plumbing is removed.
- New test: after `usePresentationSender` returns `isConnected=true`, the rendered `ControllerPlayer` receives `isPresentationActive=true` (mock the hook with `vi.mock("@/hooks/usePresentation", () => ({ usePresentationSender: () => ({...}) }))`).

#### `src/test/hooks/usePresentation.test.ts`
- Remove the `@ts-expect-error` references — receiver behavior unchanged, but TS now knows the types.

---

## Phase 4 — Remove Dead Code & Rename the "presentation message" plumbing
- Delete `onPresentationConnect`/`onPresentationDisconnect` from `ControllerPlayerProps` (Phase 3.3 already does this).
- Delete the corresponding unused callbacks in both controller pages (Phase 3.1/3.2).
- Keep `usePresentationReceiver`'s `onConnected`/`onDisconnected` (still meaningful for future receiver-side integrations if needed).

---

## Phase 5 — Capability Re-sync on Connect (lightweight)
When the sender's connection transitions to `connected`:
- After `onConnected` fires, the sender hook itself **does not** send commands (it doesn't know player state).
- ControllerPlayer gains a one-shot effect keyed on `isPresentationActive` flipping `false → true` that posts full state once: `play` or `pause` (based on `isPlaying`), `seek` to `currentTime`, `volume` of current `volume`, `songTitle` of current chapter. This brings a newly-connected projection into sync without a continuous resync heartbeat (event-driven per the chosen sync strategy).

---

## Phase 6 — Lint, Typecheck, Tests, Build
Run in order from `delivery/webapp/`:
```bash
pnpm lint
pnpm test
pnpm build
```
Fix everything red before declaring done.

---

## Phase 7 — Manual Validation Notes (no automation)
- Chrome desktop → cast to a second Chrome window (1 Avail+Connect via `getAvailability` ext)
- Verify: play on controller → projection plays; pause → projection pauses; seek via slider → projection seeks; prev/next song jumps → projection seeks to chapter start; volume slider → projection volume; mute toggle → projection muted; disconnect TV → controller badge disappears, audio returns to controller; reconnect → resync effect fires.
- Verify share-path: open `/share/{token}/play/controller`, "Send to TV" targets `/share/{token}/play/projection` (no auth required on TV).

---

## Files Touched Summary

| File | Action |
|---|---|
| `src/types/presentation-api.d.ts` | **Create** — ambient types |
| `src/hooks/usePresentation.ts` | **Edit** — remove `@ts-expect-error` (4×); add `usePresentationSender` |
| `src/app/songsets/[id]/play/controller/page.tsx` | **Edit** — wire `usePresentationSender`, remove `window.message` plumbing |
| `src/app/share/[token]/play/controller/page.tsx` | **Edit** — same, with token URL |
| `src/components/play/ControllerPlayer.tsx` | **Edit** — new props, "Send to TV" button in top bar, send-command forwarding in handlers, song-change effect, on-connect resync effect |
| `src/components/play/PrePlayCard.tsx` | **Edit** — remove Presentation API code and Send to TV button |
| `src/test/hooks/usePresentationSender.test.ts` | **Create** — sender hook tests |
| `src/test/hooks/usePresentation.test.ts` | **Edit** — drop `@ts-expect-error` references |
| `src/test/components/play/ControllerPlayer.test.tsx` | **Edit** — new prop signatures, send-command tests |
| `src/test/components/play/PrePlayCard.test.tsx` | **Edit** — remove Send to TV tests |
| `src/test/app/controller-page.test.tsx` | **Edit** — remove window-message tests, add hook wiring tests |

---

## Risks / Open Items
- **Receiver readiness:** if the operator hits `play` on the controller before the projection video has loaded, the receiver's `video.play()` is lost. v1 ships with this limitation; operator should wait for the "Connected to TV" badge before starting playback. Future: add a `ready` handshake in the protocol.
- **Operator's playback position vs. receiver position:** event-driven sync means independent video clocks may drift over long sets. v1 event-driven only (per the chosen strategy); observe real-world drift before adding periodic resync.
- **`PresentationRequest.start()` requires user gesture** — the new "Send to TV" button in the controller top bar satisfies this since it's a click handler. Cannot auto-start on mount.
