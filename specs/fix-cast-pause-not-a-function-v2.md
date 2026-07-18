# Fix: Cast Pause "A.current?.pause is not a function"

## Status
Planning — implementation plan ready.

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

## Additional Risks Identified During Review

Beyond the phantom method bug, the following issues were identified and are
addressed in this plan:

### Risk 1: Race Condition — play/pause Before First Status Event

**Location:** `src/hooks/useCast.ts` (proposed fix)

**Issue:** After connecting, `playerRef.current` is seeded with default
values (`isPaused: false`, `playerState: ""`) at line 622-625. If the user
taps play/pause before the first `PLAYER_STATE_CHANGED` event fires,
`normalizePlayerState("")` returns `""`, and the guard logic may make the
wrong decision.

**Impact:** For `pause()`: `isPaused === false` (default) and `state === ""`
→ guard does NOT short-circuit → `playOrPause()` is called. This could
incorrectly toggle playback if the receiver happens to already be paused.

**Mitigation:** Add `state === ""` (unknown state) as a short-circuit
condition for BOTH `play()` and `pause()`. When the state hasn't been
received yet, we should not issue a toggle — the user can tap again after
the state syncs.

### Risk 2: Buffering State UX Quirk

**Location:** `src/components/play/ControllerPlayer.tsx:371-393`

**Issue:** When the receiver is buffering, `effectiveIsPlaying` is `false`
(because `playerState === "buffering"`). The play/pause button shows "play"
and tapping it sends a `play` command. But the media is loading and will
auto-play when ready.

**Impact:** Low — this is a UX quirk, not a functional bug. The toggle
behavior is correct once the state stabilizes.

**Mitigation:** Out of scope for this fix. Consider future enhancement to
treat `"buffering"` as a playing-like state for UI purposes.

### Risk 3: Test Mock Clarity

**Location:** `src/test/app/controller-page.test.tsx:57-58`

**Issue:** `makeTransport` mocks `play: vi.fn()` and `pause: vi.fn()`. These
are OUR adapter methods (not SDK methods), so they're correct. But the
distinction could be confusing.

**Impact:** Low — test-only, no production impact.

**Mitigation:** Add a clarifying comment in `makeTransport`.

## Implementation Plan

### Phase 1: Fix Type Declaration (`src/types/cast-sdk.d.ts`)

**File:** `src/types/cast-sdk.d.ts` (lines 252-253)

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

### Phase 2: Rewrite `play()` and `pause()` Callbacks (`src/hooks/useCast.ts`)

**File:** `src/hooks/useCast.ts` (lines 780-796)

Currently:

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
- **NEW:** Skips the call when `playerState` is empty (`""`) — the state
  hasn't been synced from the receiver yet. Prevents race-condition
  mis-toggles in the window between connection and first status event.
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
  // If state hasn't synced yet (empty string), don't issue a toggle —
  // the user can tap again after the first status event arrives.
  if (state === "" || p?.isPaused === false || state === "playing") return;
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
  // If state hasn't synced yet (empty string), don't issue a toggle —
  // the user can tap again after the first status event arrives.
  if (state === "" || p?.isPaused === true || state === "paused" || state === "idle") return;
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

### Phase 3: Update Tests (`src/test/hooks/useCastTransport.test.ts`)

#### 3a. Update SDK mock (lines 70-71)

Remove `pause: vi.fn()` and `play: vi.fn()` from the controller mock. Keep
`playOrPause: vi.fn()` (already at line 75).

#### 3b. Update existing telemetry tests (lines 886-974)

The existing telemetry tests mock `controller.play` to throw, but after the
fix the implementation calls `controller.playOrPause`. These tests must be
updated to mock `playOrPause` instead:

- **Line 890** (`play() throw` test): Change `controller.play.mockImplementation` → `controller.playOrPause.mockImplementation`
- **Line 927** (`castAppIdMode='default'` test): Change `controller.play.mockImplementation` → `controller.playOrPause.mockImplementation`
- **Line 952** (`urlExpired` test): Change `controller.play.mockImplementation` → `controller.playOrPause.mockImplementation`

The error message strings in these tests (e.g., `"receiver play blew up"`) can
stay the same — they are arbitrary test strings.

#### 3c. Add new test suite: `play/pause state guards`

Add a new `describe` block after the existing `setMuted idempotency` suite
(or within `transport error telemetry`):

```ts
describe("play/pause state guards", () => {
  it("pause() no-ops when already paused", async () => {
    const { result, controller } = await mountHook(undefined, {
      player: { playerState: "paused", isPaused: true },
    });
    await act(async () => {
      result.current.pause();
    });
    expect(controller.playOrPause).not.toHaveBeenCalled();
  });

  it("pause() calls playOrPause when playing", async () => {
    const { result, controller } = await mountHook(undefined, {
      player: { playerState: "playing", isPaused: false },
    });
    await act(async () => {
      result.current.pause();
    });
    expect(controller.playOrPause).toHaveBeenCalledTimes(1);
  });

  it("play() no-ops when already playing", async () => {
    const { result, controller } = await mountHook(undefined, {
      player: { playerState: "playing", isPaused: false },
    });
    await act(async () => {
      result.current.play();
    });
    expect(controller.playOrPause).not.toHaveBeenCalled();
  });

  it("play() calls playOrPause when paused", async () => {
    const { result, controller } = await mountHook(undefined, {
      player: { playerState: "paused", isPaused: true },
    });
    await act(async () => {
      result.current.play();
    });
    expect(controller.playOrPause).toHaveBeenCalledTimes(1);
  });

  it("pause() no-ops when state hasn't synced yet (empty playerState)", async () => {
    const { result, controller } = await mountHook(undefined, {
      player: { playerState: "", isPaused: false },
    });
    await act(async () => {
      result.current.pause();
    });
    expect(controller.playOrPause).not.toHaveBeenCalled();
  });

  it("play() no-ops when state hasn't synced yet (empty playerState)", async () => {
    const { result, controller } = await mountHook(undefined, {
      player: { playerState: "", isPaused: false },
    });
    await act(async () => {
      result.current.play();
    });
    expect(controller.playOrPause).not.toHaveBeenCalled();
  });

  it("pause() surfaces SDK sync throw via reportTransportError", async () => {
    const onError = vi.fn();
    const { result, controller } = await mountHook({ media: MEDIA, onError });
    controller.playOrPause.mockImplementation(() => {
      throw new Error("receiver pause blew up");
    });
    fetchSpy.mockClear();
    await act(async () => {
      result.current.pause();
    });
    expect(result.current.lastError).toBe("receiver pause blew up");
    expect(onError).toHaveBeenCalledWith("receiver pause blew up");
    const post = fetchSpy.mock.calls.find(
      (c) => typeof c[0] === "string" && c[0].endsWith("/api/log-client-error"),
    );
    expect(post).toBeTruthy();
    const body = JSON.parse(String(post?.[1]?.body));
    expect(body.kind).toBe("cast_transport");
  });

  it("play() mirrors the throw funnel", async () => {
    const onError = vi.fn();
    const { result, controller } = await mountHook({ media: MEDIA, onError });
    controller.playOrPause.mockImplementation(() => {
      throw new Error("receiver play blew up");
    });
    fetchSpy.mockClear();
    await act(async () => {
      result.current.play();
    });
    expect(result.current.lastError).toBe("receiver play blew up");
    expect(onError).toHaveBeenCalledWith("receiver play blew up");
  });

  it("pause() handles UPPERCASE PLAYING from real receiver", async () => {
    const { result, controller } = await mountHook(undefined, {
      player: { playerState: "PLAYING", isPaused: false },
    });
    await act(async () => {
      result.current.pause();
    });
    expect(controller.playOrPause).toHaveBeenCalledTimes(1);
  });

  it("catches the original bug: pause() does not reference controller.pause (removed method)", async () => {
    const { result, controller } = await mountHook(undefined, {
      player: { playerState: "playing", isPaused: false },
    });
    // The controller mock must NOT have a pause property after the fix.
    expect(controller).not.toHaveProperty("pause");
    expect(controller).not.toHaveProperty("play");
    // Calling pause() must not throw "not a function".
    await act(async () => {
      expect(() => result.current.pause()).not.toThrow();
    });
  });
});
```

### Phase 4: Add Clarifying Comment to Controller Page Test Mock

**File:** `src/test/app/controller-page.test.tsx` (around line 55)

Add a comment to clarify that `play`/`pause` are adapter methods:

```ts
function makeTransport(overrides: Partial<CastTransportResult> = {}): CastTransportResult {
  return {
    // ... other fields ...
    // play/pause are OUR adapter methods (not SDK methods).
    // The SDK uses playOrPause(); our transport exposes separate play/pause
    // for a cleaner dispatch contract.
    play: vi.fn(),
    pause: vi.fn(),
    // ...
  };
}
```

### Phase 5: Verify No Other Files Need Changes

- **`src/test/lib/cast/dispatch.test.ts`** — NO changes. `CastCommandTarget`
  (the interface in `src/lib/cast/dispatch.ts:25-31`) still declares
  `pause(): void` and `play(): void` (these are OUR adapter methods, not
  the SDK's), so the dispatch test stays unchanged. Only the
  *implementation* of those adapter methods inside `useCastTransport` changes
  (from `controller.pause()` → `controller.playOrPause()` with a guard).

- **Controller pages** (`/songsets/[id]/play/controller`, `/share/[token]/play/controller`)
  — NO changes. They call `play()`/`pause()` on the transport result, which
  keeps the same signature.

- **`src/lib/cast/dispatch.ts`** — NO changes. The dispatch contract is
  unchanged.

- **`src/components/play/ControllerPlayer.tsx`** — NO changes. The
  `handlePlayPause` function already correctly forwards play/pause commands
  via `onSendTransportCommandRef`. The `effectiveIsPlaying` derivation from
  `transport?.playerState === "playing"` is correct; the buffering UX quirk
  is noted as a future enhancement, not a bug.

## Verification Steps

```bash
cd delivery/webapp
pnpm test src/test/hooks/useCastTransport.test.ts    # updated mock + new tests
pnpm test src/test/lib/cast/dispatch.test.ts         # unchanged contract — keep green
pnpm test src/test/app/controller-page.test.tsx       # transport contract unchanged
pnpm lint
pnpm build
```

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `playOrPause()` called when already in target state (spurious toggle) | Low | State guards check `isPaused` + normalized `playerState` before calling |
| `playOrPause()` called before first status event (race condition) | Low | `state === ""` guard prevents toggle when state hasn't synced |
| `playerRef.current` is null when play/pause called | Low | Controller existence is checked (`if (!c) return`), and `p?.isPaused` is safe |
| Existing telemetry tests break due to mock target change | Medium | All three `controller.play` mocks must be switched to `controller.playOrPause` |
| Type errors from removing `play()`/`pause()` from type | Low | Only `useCast.ts` referenced these methods; no other files call them |
| `normalizePlayerState` not available in scope | None | It's defined at line 255 in the same file |
| Buffering state UX quirk | Low | Documented as future enhancement; not a functional bug |

## Out of Scope (Deliberately)

- Not adding `canPause`/`isMediaLoaded` guards — the original diagnosis
  around those was wrong; can revisit separately if needed.
- Not touching `seek()`, `setMuted`, `setVolumeLevel`, or `stop()` — those
  methods all exist on the real SDK and tests pass.
- Not changing the Android app or render-worker — they don't touch this
  code path.
- Not editing the existing spec `complete-chromecast-projection-transport-api.md`.
- Not changing buffering UX (`playerState === "buffering"` showing play
  button) — this is a UX enhancement, not a bug. The toggle behavior is
  correct once state stabilizes.
