# Fix: Cast Pause "A.current?.pause is not a function"

## Status
Planning — not yet implemented.

## Symptom

User taps **Pause** while Cast is playing on the worship controller page
(`/songsets/[id]/play/controller` or `/share/[token]/play/controller`). The
error surfaced is:

```
A.current?.pause is not a function
```

The minified identifier `A` corresponds to `controllerRef` in
`src/hooks/useCast.ts`. The optional chaining `?.` only guards against
`controllerRef.current` being `null`/`undefined`; it does NOT guard against
the `pause` property being absent on a truthy controller object.

## Root Cause

The Google Cast Web Sender SDK's `cast.framework.RemotePlayerController`
class **does not expose `play()` or `pause()` methods**. Per the official
reference
(https://developers.google.com/cast/docs/reference/web_sender/cast.framework.RemotePlayerController),
the only playback-control methods on `RemotePlayerController` are:

- `playOrPause()` — toggle play/pause
- `seek()` — seek to `player.currentTime`
- `stop()` — stop the media player
- `setVolumeLevel(volume)` — set volume
- `muteOrUnmute()` — toggle mute
- `skipAd()` — skip current ad

Our ambient type declaration at `src/types/cast-sdk.d.ts:252-253` declares
phantom `play(): void` and `pause(): void` methods that do not exist on the
real SDK class. The hook calls them at:

- `src/hooks/useCast.ts:782` — `controllerRef.current?.play();`
- `src/hooks/useCast.ts:791` — `controllerRef.current?.pause();`

In production, `controller.pause === undefined`, so
`controllerRef.current?.pause()` evaluates to `undefined()` and throws
`TypeError: A.current?.pause is not a function` (after minification:
`controllerRef` → `A`).

The test mock at `src/test/hooks/useCastTransport.test.ts:70-71` fakes
`play` and `pause` to satisfy the wrong type declaration, so unit tests
never caught this. The mock does not reflect the real SDK surface.

The existing `try/catch` in the `pause()` callback (`useCast.ts:789-796`)
DOES catch the synchronous throw and funnel it to `reportTransportError`,
which surfaces the message via the `onError` toast at the controller page
(`(m) => toast.error(m)`). So the user-visible error text is the toast
message, not an uncaught crash. But the underlying transport function is
broken: **pause never reaches the receiver**.

`play()` has the same bug; it just didn't trip the error because the user
happened to be at "playing" when they tapped the button, which sent a
"pause" command first.

## Hypotheses Ruled Out

During diagnosis, the following hypotheses were investigated and ruled out:

1. **React StrictMode double-mount race** — Traced the exact sequence in
   `useCast.ts:560-673` and `lib/cast/loader.ts:172-225`. The loader's
   `cancelled` Set + `pending` Map + `settled` singleton correctly handle
   the mount → unmount → mount cycle. Mount#1's abort handler resolves its
   own promise and removes its entry from `pending` before
   `dispatchSettlement` ever runs. Mount#1's cleanup runs BEFORE mount#2's
   effect, not after. No code path assigns a non-controller value to
   `controllerRef.current`.

2. **`playerRef` / `controllerRef` out of sync** — Traced every assignment
   site. Both refs are always paired: both set to values at `:609-610`, or
   both nulled at `:670-671`. No alternate assignment paths exist.

3. **SDK internal minified throw** — Initially misdiagnosed as
   `"a.Pause is not a function"` (capital P) from inside the Cast SDK. The
   user corrected the actual error to `"A.current?.pause is not a function"`,
   which is a minified reference to OUR `controllerRef.current?.pause()`.

4. **`canPause` / `isMediaLoaded` guards needed** — These would be
   defensive hardening but are NOT the root cause. The root cause is the
   phantom method on the type declaration.

## Files to Modify

### 1. `src/types/cast-sdk.d.ts` (lines 252-253)

Remove the phantom `play()` and `pause()` declarations from
`RemotePlayerController`. Keep `playOrPause()` (which IS in the real SDK,
line 257). Final `RemotePlayerController` method surface matches the
official reference:

```ts
export class RemotePlayerController {
  constructor(player: RemotePlayer);
  addEventListener(
    type: RemotePlayerEventType,
    handler: (event: RemotePlayerChangedEvent) => void,
  ): void;
  removeEventListener(
    type: RemotePlayerEventType,
    handler: (event: RemotePlayerChangedEvent) => void,
  ): void;
  seek(): void;
  stop(): void;
  setVolumeLevel(volume: number): void;
  playOrPause(): void;
  muteOrUnmute(): void;
  getFormattedTime(timeInSec: number): string;
  getSeekPosition(currentTime: number, duration: number): number;
  getSeekTime(currentPosition: number, duration: number): number;
}
```

Note: `skipAd` is also in the real SDK reference but not currently used —
leave it out (narrow-to-usage convention from the file's existing comment
at lines 21-22).

### 2. `src/hooks/useCast.ts` — rewrite `play()` and `pause()` callbacks

Currently (lines 780-796):

```ts
const play = useCallback(() => {
  try {
    controllerRef.current?.play();
  } catch (e) {
    const msg = e instanceof Error ? e.message : "Cast play failed";
    reportTransportError(msg, "cast_transport");
  }
}, [reportTransportError]);

const pause = useCallback(() => {
  try {
    controllerRef.current?.pause();
  } catch (e) {
    const msg = e instanceof Error ? e.message : "Cast pause failed";
    reportTransportError(msg, "cast_transport");
  }
}, [reportTransportError]);
```

Replace with logic that:

- Reads current state from `playerRef.current` (`isPaused` field, exposed
  in our `RemotePlayer` type at `cast-sdk.d.ts:233`, and the normalized
  `playerState` in `snapshotRef`).
- Skips the call when the player is already in the target state (avoids
  spurious toggles — `playOrPause()` is a stateless toggle, so calling it
  twice is a no-op-toggling bug).
- Calls `controllerRef?.current?.playOrPause()`.
- Keeps the existing try/catch + `reportTransportError` funnel.

Proposed:

```ts
const play = useCallback(() => {
  const p = playerRef.current;
  const c = controllerRef.current;
  if (!c) return;
  // playOrPause() is a stateless toggle. Skip if already playing so we
  // don't accidentally pause. isPaused is the SDK-mirrored receiver field;
  // back it up with the normalized playerState for resilience.
  const state = p ? normalizePlayerState(p.playerState) : "";
  if (p?.isPaused === false || state === "playing") return;
  try {
    c.playOrPause();
  } catch (e) {
    const msg = e instanceof Error ? e.message : "Cast play failed";
    reportTransportError(msg, "cast_transport");
  }
}, [reportTransportError]);

const pause = useCallback(() => {
  const p = playerRef.current;
  const c = controllerRef.current;
  if (!c) return;
  // Skip if already paused — playOrPause() is a toggle.
  const state = p ? normalizePlayerState(p.playerState) : "";
  if (p?.isPaused === true || state === "paused" || state === "idle") return;
  try {
    c.playOrPause();
  } catch (e) {
    const msg = e instanceof Error ? e.message : "Cast pause failed";
    reportTransportError(msg, "cast_transport");
  }
}, [reportTransportError]);
```

Public `CastTransportResult` interface (`useCast.ts:66-72`) stays unchanged
— `play()` and `pause()` keep `() => void` signatures. `dispatchCast` at
`src/lib/cast/dispatch.ts:44-68` and both controller pages need NO changes.

### 3. `src/test/hooks/useCastTransport.test.ts`

- **Update SDK mock** at lines 70-71: remove `pause: vi.fn()` and
  `play: vi.fn()` from the controller mock. Keep `playOrPause: vi.fn()`
  (already at line 75).
- **Update existing play/pause test(s)** — find any test that asserts
  `controller.pause` was called and rewrite it to assert
  `controller.playOrPause` was called instead.
- **Add new tests:**
  - `pause() no-ops when already paused` — set
    `{ playerState: "paused", isPaused: true }`, call
    `result.current.pause()`, assert `playOrPause` not called.
  - `pause() calls playOrPause when playing` — set
    `{ playerState: "playing", isPaused: false }`, call `pause()`, assert
    `controller.playOrPause` called exactly once.
  - `play() no-ops when already playing` — set
    `{ playerState: "playing", isPaused: false }`, call `play()`, assert
    `playOrPause` not called.
  - `play() calls playOrPause when paused` — set
    `{ playerState: "paused", isPaused: true }`, call `play()`, assert
    `controller.playOrPause` called exactly once.
  - `pause() surfaces SDK sync throw via reportTransportError` — make
    `playOrPause` throw, call `pause()`, assert `lastError` set, `onError`
    called, telemetry POST body `kind: "cast_transport"`.
  - `play() mirrors the throw funnel`.
  - `pause() handles UPPERCASE PLAYING from real receiver` — set
    `{ playerState: "PLAYING" }`, assert `pause()` calls `playOrPause`
    (verifying normalization).
  - **Catches the original bug:** `pause() does not reference
    controller.pause (removed method)` — assert the controller mock has no
    `pause` property after the fix, and `pause()` does not throw
    "not a function".

### 4. `src/test/lib/cast/dispatch.test.ts` — NO changes

`CastCommandTarget` (the interface in `src/lib/cast/dispatch.ts:25-31`)
still declares `pause(): void` and `play(): void` (these are OUR adapter
methods, not the SDK's), so the dispatch test stays unchanged. Only the
*implementation* of those adapter methods inside `useCastTransport` changes
(from `controller.pause()` → `controller.playOrPause()` with a guard).

## Verification

```bash
cd delivery/webapp
pnpm test src/test/hooks/useCastTransport.test.ts    # updated mock + new tests
pnpm test src/test/lib/cast/dispatch.test.ts         # unchanged contract — keep green
pnpm test src/test/app/controller-page.test.tsx       # transport contract unchanged
pnpm lint
pnpm build
```

## Out of Scope (Deliberately)

- Not adding `canPause`/`isMediaLoaded` guards — the original diagnosis
  around those was wrong; can revisit separately if needed.
- Not touching `seek()`, `setMuted`, `setVolumeLevel`, or `stop()` — those
  methods all exist on the real SDK and tests pass.
- Not changing the Android app or render-worker — they don't touch this
  code path.
- Not editing the existing spec `complete-chromecast-projection-transport-api.md`.
