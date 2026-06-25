// Pure command dispatcher for the Cast transport.
//
// `dispatchCast` routes a shared `PresentationCommand` (the same wire contract
// used by the dev-only Presentation API fallback) to a narrow
// `CastCommandTarget` adapter backed by a `RemotePlayerController`.
//
// Invariants enforced here are the ones the v2 review hardened:
//   - `mute` calls `setMuted(muted)` — never `setVolume(0)` (the receiver mute
//     bit is distinct from volume level, and routing mute through volume would
//     lose the user's prior level on unmute).
//   - `songTitle` is a no-op: the title is delivered via `MediaInfo.metadata`
//     at `loadMedia` time, not as a transport command.
//   - Unknown command types are no-ops (forward-compatible).
//
// This module is deliberately stateless and side-effect free aside from the
// delegated `cast.*` calls, so it is trivially unit-testable with a stub target.

import type { PresentationCommand } from "@/types/presentation-api";

/**
 * Narrow adapter surface that `dispatchCast` drives. Implemented by
 * `useCastTransport`'s transport methods (clamped + debounced) on the Cast
 * path, and by `usePresentationSender`'s `send()` for the dev-only fallback.
 */
export interface CastCommandTarget {
  play(): void;
  pause(): void;
  seek(positionSeconds: number): void;
  setVolume(level: number): void;
  setMuted(muted: boolean): void;
}

/**
 * Routes a `PresentationCommand` to the given Cast command target.
 *
 * - `play`    → `cast.play()`
 * - `pause`   → `cast.pause()`
 * - `seek`    → `cast.seek(positionSeconds)`
 * - `volume`  → `cast.setVolume(level)` (clamped `[0,1]` by the target)
 * - `mute`    → `cast.setMuted(muted)` (NOT `setVolume(0)`)
 * - `songTitle`→ no-op (title set via media metadata at `loadMedia`)
 * - unknown   → no-op
 */
export function dispatchCast(cast: CastCommandTarget, cmd: PresentationCommand): void {
  switch (cmd.type) {
    case "play":
      cast.play();
      break;
    case "pause":
      cast.pause();
      break;
    case "seek":
      cast.seek(cmd.positionSeconds);
      break;
    case "volume":
      cast.setVolume(cmd.level);
      break;
    case "mute":
      cast.setMuted(cmd.muted);
      break;
    case "songTitle":
      // No transport command — the title is set via MediaInfo.metadata at
      // loadMedia time, so there is nothing to dispatch here.
      break;
    default:
      // Unknown command: forward-compatible no-op.
      break;
  }
}
