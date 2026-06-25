# Review: Consolidated Chromecast Projection Plan

**Date:** 2026-06-25
**Scope:** UX experience, playback robustness, runtime issues, operational concerns, tech stack maturity
**Plan:** `specs/consolidated-chromecast-projection-plan.md`

---

## Executive Summary

The plan is architecturally sound. The "receiver-as-truth" design is correct, the scope is well-bounded, and the fallback strategy (Cast primary, Presentation API secondary) is pragmatic. However, there are **two critical gaps** that could cause visible failures during live worship services, plus several high-priority runtime and operational issues that should be addressed before the feature is considered production-ready.

**Key finding:** The plan specifies what happens *while* casting correctly, but underspecifies the **disconnect-to-local-resume transition** and the **command-dispatch semantics** for mute/toggle across transports.

---

## Critical Issues (P0)

### 1. Disconnect → Local Resume Time Sync Gap

**Problem:** When casting stops (user disconnect, Wi-Fi blip, or `endCurrentSession`), the local `<video>` element resumes from its **stale paused position** — not from the TV's current playback position. If the worship leader has navigated through multiple songs or lyric lines while casting, the local video could be minutes behind.

**Impact:** Worship leader is thrown back to an earlier point in the set. Re-syncing manually under pressure is error-prone.

**Where plan is silent:** Phase 6 (`ControllerPlayer` updates) mentions "local video resumes audio (un-muted)" in the manual validation section, but never specifies *from what time*.

**Recommendation:**
- Before unpausing the local video on disconnect, seek it to `transport.currentTime` (the last known receiver position).
- Capture this in `useCastTransport`'s disconnect handler or in `ControllerPlayer`'s `isPresentationActive` effect cleanup.
- Add an acceptance criterion: *"On disconnect, local video seeks to receiver's last known position before resuming playback."*

---

### 2. Mute Toggle Lacks Cross-Transport Semantics

**Problem:** The unified `PresentationCommand` type only supports `{ type: "volume"; level: number }`. There is no mute/unmute command. For Cast, the hook exposes `setMuted(boolean)`, but `ControllerPlayer`'s `handleToggleMute` currently emits a volume-level command. This means:
- On Cast: "unmute" would set volume to 0 rather than toggling the mute bit
- On Presentation fallback: mute state is simulated via volume level (acceptable)

**Impact:** Cast mute toggle is broken or semantically incorrect.

**Recommendation:**
- Add `{ type: "mute"; muted: boolean }` to `PresentationCommand`.
- In `dispatchCast`, intercept mute commands and call `cast.setMuted()` rather than `cast.setVolume(0)`.
- Update `ControllerPlayer` to emit the mute command type on toggle.

---

## High Priority Issues (P1)

### 3. No Explicit Buffering UI Requirement

**Problem:** The plan lists "surface a 'Loading on TV...' state derived from `playerState='buffering'`" as a risk mitigation, but **no phase requires it** and it's absent from acceptance criteria.

**Impact:** Worship leader may tap play/pause/seek repeatedly while the TV is buffering, causing command queue conflicts or confusing receiver state.

**Clarification received:** Queue commands during buffering (do not disable controls).

**Recommendation:**
- Add a visual "TV is loading..." indicator (spinner or toast) when `playerState === "buffering"`.
- Do not disable controls (per clarified preference), but provide clear feedback so the leader knows commands are queued.
- Add to Phase 6: *"Render a non-blocking buffering indicator when receiver reports buffering state."*

---

### 4. `loadMedia` Failure Leaves Dangling CastSession

**Problem:** If `requestSession()` succeeds but `loadMedia()` fails, the spec sets `isConnected=false` but does not tear down the Cast session. The user remains connected to a receiver with no media loaded.

**Impact:** User cannot retry without manually disconnecting. Confusing state.

**Recommendation:**
- On `loadMedia` failure, call `CastContext.endCurrentSession()` (or equivalent) to return to a clean disconnected state.
- Add test: *"loadMedia failure -> session ended, isConnected=false, can retry."*

---

### 5. Rapid-Fire Seek Guard Missing

**Problem:** Jump-to-chapter and jump-to-lyric-line navigation can generate rapid `seek()` calls. Cast's `RemotePlayerController.seek()` is fire-and-forget; there is no guard against overlapping or out-of-order seeks.

**Impact:** Tapping "Next Song" or lyric lines rapidly may queue conflicting seeks that arrive out of order at the receiver.

**Recommendation:**
- Add a 150-250ms debounce or an `isSeekInFlight` guard in `useCastTransport`.
- Block new seeks until the previous seek's status event confirms the receiver has processed it, or simply debounce.

---

### 6. `window.__onGCastApiAvailable` Never Cleaned Up

**Problem:** The loader assigns a global callback. If the component/page unmounts before the script finishes loading, the callback fires and resolves/rejects a promise whose consumer may be gone.

**Impact:** On navigation or rapid unmount, this could throw into a dead React tree or leak closure references.

**Recommendation:**
- Add cancellation/ignore logic in `loadCastSdk()` so that if the requesting component unmounts before the callback fires, it no-ops safely.
- Use an `AbortController`-like pattern or a mounted-ref check.

---

### 7. `CastContext.setOptions` Called on Every Mount

**Problem:** `CastContext.getInstance()` is global. Rapidly mounting/unmounting controller pages could race on `setOptions`.

**Impact:** Potential race conditions or redundant SDK re-initialization.

**Recommendation:**
- Wrap initialization in a module-level singleton guard so `setOptions` is called at most once per page load.

---

### 8. No Visual Feedback for Failed Commands

**Problem:** If a seek or play command fails at the receiver (network issue, receiver error), the leader only sees that the TV didn't move. No toast or error state is surfaced.

**Impact:** Leader doesn't know whether the command was lost, delayed, or rejected.

**Recommendation:**
- Pipe `loadMedia` errors and transport-level failures into the existing toast system via lifecycle callbacks.
- At minimum, show a toast: *"TV did not respond. Check connection."*

---

## Quick Wins (P2)

### 9. `songTitle` Command Has No Cast Dispatch Path

**Problem:** The song-change effect emits `{ type: "songTitle", title }`, which is valid for Presentation but unknown to Cast. `dispatchCast` must silently drop it.

**Impact:** Minor — Cast already sets title via `MediaInfo` metadata at `loadMedia`. But the unified command path should be explicit.

**Recommendation:**
- Document that `dispatchCast` ignores `songTitle` and any unhandled `PresentationCommand` types.
- Or, map `songTitle` to a no-op in the Cast dispatch function with a comment explaining why.

---

### 10. `isCastSdkSupported()` Cryptic Check

**Problem:** The spec says `isCastSdkSupported()` returns true only when `"navigator.presentation is NOT the test path"`. This condition is unclear and looks like a copy-paste artifact.

**Impact:** Confusing code, potential false negatives/positives.

**Recommendation:**
- Clarify or remove this condition. If it's meant to exclude a specific test environment, document it explicitly.
- The standard check should be: `!!window.chrome?.cast && !!window.cast?.framework`.

---

### 11. Missing Chapter Timestamp Validation

**Problem:** `handleJumpToChapter` and `handleJumpToLine` use timestamps from the songset definition. If these drift from the baked MP4 timeline (due to re-encoding, transition padding, or manual edits), the TV seeks to the wrong frame.

**Impact:** Lyric-line jumps land on wrong lyrics; chapter jumps land mid-transition.

**Recommendation:**
- Add a manual validation criterion: *"Verify chapter/line timestamps in songset exactly match rendered MP4 seek points."*
- Consider a future automated check during render pipeline validation.

---

## Operational Concerns

### 12. R2 Reachability from TV Network

**Problem:** The Cast receiver fetches the MP4 directly from R2. Church Wi-Fi networks often block Cloudflare IPs, large direct downloads, or non-standard ports. Corporate/captive-portal networks can block Cast discovery entirely.

**Impact:** Service fails silently — TV shows black screen or buffering indefinitely.

**Clarification received:** Document the limitation for long services; no automatic refresh.

**Recommendation:**
- Add a pre-service network test to the runbook: *"Open the signed MP4 URL directly in a laptop browser on the same Wi-Fi/VLAN as the Chromecast and verify range-seek works."*
- Document that phone and Chromecast must be on the same LAN; note that guest/captive-portal Wi-Fi may block Cast discovery.
- For URL expiry: add an operational note that services longer than ~3.5 hours must plan a deliberate stop/re-cast with a freshly minted URL.

---

### 13. No Server-Side Telemetry for Cast Failures

**Problem:** If a service is interrupted, the only evidence is on the worship leader's phone. Server logs won't show receiver errors, expired URL 403s, or loadMedia failures.

**Impact:** Difficult to debug post-incident.

**Recommendation:**
- Consider (future work) posting anonymized transport errors to a lightweight `/api/log-client-error` endpoint.
- For now, document that Cast failures are client-side only and require phone-side debugging.

---

### 14. Share Controller Auth-Free Path

**Problem:** The spec correctly notes that `presentationUrl = /share/${token}/play/projection` must have no auth. However, verify that the Cast `media.videoUrl` (signed R2 URL) also doesn't inadvertently require auth/cookies, since the TV has no session cookies.

**Impact:** If the signed URL endpoint requires auth, the TV cannot fetch the MP4.

**Recommendation:**
- Explicitly verify that `/api/signed-url` with `cast=true` does not require session auth (only the token/songset ID validation).

---

## Tech Stack Maturity Assessment

| Component | Maturity | Notes |
|---|---|---|
| Google Cast Web Sender SDK | **Mature** | `gstatic.com` CDN is reliable. `RemotePlayerController` is stable but offers less granular control than low-level API. Acceptable for this use case. |
| Default Media Receiver | **Mature / Limited** | Correct pragmatic choice for baked-in lyrics. Tradeoff: no custom error UI, no dynamic overlay, no mid-stream URL refresh. |
| Presentation API | **Declining** | Chrome-only, declining mindshare. Appropriate as dev fallback only. Do not rely on it for production. |
| TypeScript ambient declarations | **Pragmatic** | Narrow declarations are good. Expand only if needed for future features. |
| Testability | **Challenging** | Mocking `window.cast.framework` in Vitest/JSDOM is nontrivial. Expect brittleness around event listener timing. Consider one integration-style manual test. |

---

## Recommendations Summary

| Priority | Issue | Recommended Action |
|---|---|---|
| **P0** | Disconnect resume time sync | Seek local video to `transport.currentTime` before resuming on disconnect. Add acceptance criterion. |
| **P0** | Mute toggle semantics | Add `{ type: "mute" }` to `PresentationCommand`; use `cast.setMuted()` in dispatch. |
| **P1** | Buffering indicator | Add non-blocking "TV is loading" visual feedback when `playerState === "buffering"`. |
| **P1** | Dangling session on loadMedia failure | Call `endCurrentSession()` on `loadMedia` failure. Add test. |
| **P1** | Rapid-fire seeks | Add 150-250ms debounce or `isSeekInFlight` guard in `useCastTransport`. |
| **P1** | Global callback cleanup | Add cancellation logic for `__onGCastApiAvailable` on unmount/navigation. |
| **P1** | CastContext singleton guard | Wrap `setOptions` in module-level singleton to prevent re-initialization races. |
| **P1** | Failed command feedback | Pipe transport errors into toast system. |
| **P2** | songTitle no-op documentation | Explicitly document that Cast dispatch ignores `songTitle` commands. |
| **P2** | `isCastSdkSupported` clarity | Remove or clarify the `navigator.presentation` "test path" condition. |
| **P2** | Chapter timestamp drift | Add manual validation that songset timestamps match rendered MP4 seek points. |
| **Ops** | R2 reachability | Add pre-service network test to runbook. Document 3.5h URL expiry limit. |
| **Ops** | Telemetry gap | Document client-side-only debugging. Consider future `/api/log-client-error` endpoint. |
| **Ops** | Share auth-free path | Verify `/api/signed-url?cast=true` does not require session cookies. |

---

## Positive Aspects (Preserve)

1. **Receiver-as-truth design is correct.** Phone UI reconciling from `RemotePlayerController` events prevents sync drift.
2. **No silent reconnect-induced seeks.** This is the right call for live worship.
3. **4-hour signed URL expiry split** (`cast=true` → 14400s, default → 3600s) is well-designed.
4. **iPhone fallback copy** is clear and user-friendly.
5. **Scope discipline is strong.** Custom receiver, drift correction, and iOS native app are correctly deferred.
6. **Presentation API fallback** is appropriately scoped as dev-only secondary transport.

---

*Report generated from review of `specs/consolidated-chromecast-projection-plan.md`. No code changes or plan modifications were made.*
