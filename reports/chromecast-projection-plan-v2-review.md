# Review: Consolidated Chromecast Projection Plan v2

**Date:** 2026-06-25  
**Reviewed plan:** `specs/consolidated-chromecast-projection-plan-v2.md`  
**Scope:** robust and smooth large-TV playback, smooth UX, runtime issues, operational risks, and tech stack maturity.  
**User-confirmed review bar:** live-service gate, current-generation Google TV/Chromecast hardware, typical service under 2 hours.

## Executive Summary

The v2 plan is a substantial improvement over v1. It resolves the previous critical gaps around disconnect resume, mute semantics, buffering feedback, dangling sessions, seek debouncing, Cast SDK loader cleanup, singleton Cast initialization, transport error feedback, and auth-free signed URL validation.

The core direction is sound: Google Cast Web Sender SDK on Android Chrome, Default Media Receiver for baked-in MP4 lyric videos, receiver status as the source of truth, and Presentation API only as a dev/fallback path. For modern Google TV hardware and services under 2 hours, this is a mature enough architecture to pursue.

For live worship readiness, I would still treat the plan as **not yet production-gated** until the items below are addressed. The remaining risks are less about the React transport shape and more about TV media compatibility, stale-state recovery under network loss, mobile UX diagnostics, and runbook discipline.

## Key Concerns

### P0 / Release Gate: Receiver Choice and Docs Must Be Unambiguous

The v2 plan chooses Google Default Media Receiver (`specs/consolidated-chromecast-projection-plan-v2.md`, decisions table), which is the right pragmatic choice for baked-in lyric MP4s. However, the current webapp README still describes registering a **Custom Receiver** pointed at `/songsets/<id>/play/projection`.

This is operationally dangerous because these are different deployment models:

- Default Media Receiver loads the signed MP4 URL directly and does not run the app's projection route.
- Custom Receiver requires a registered receiver web app, review/whitelisting flow, and route availability.
- Mixing the two in docs can lead to a service-day setup where the sender app ID, receiver behavior, and debugging expectations do not match.

Recommendation: before live use, make the implementation and docs converge on one mode. If v2 keeps Default Media Receiver, remove or clearly quarantine Custom Receiver registration guidance as old/future context.

### P0 / Release Gate: Validate Rendered MP4 Compatibility, Not Just Cast Transport

The plan covers signed URL expiry and R2 reachability, but smooth large-TV playback depends on the actual media file being Cast-friendly. Google Cast supports MP4 containers and device-specific codec limits, but device capability still varies.

Recommendations:

- Add a pre-release validation check for rendered MP4s: H.264 video, AAC audio, 1080p target unless there is a known 4K requirement, and no exotic codec/profile.
- Ensure the MP4 is optimized for progressive playback (`moov` atom at the front / faststart) so the TV can start and seek quickly.
- Extend the manual validation to explicitly test first-frame startup time, 10s forward/back range seek, chapter jump seek, and lyric-line jump seek on the actual Google TV hardware.
- Keep the existing `Content-Type: video/mp4` and byte-range runbook requirement, but make it a live-service gate rather than a documentation note.

Without this, the Cast SDK may work perfectly while the TV still buffers, starts slowly, or seeks poorly.

### P1: Disconnect Resume Still Depends on Potentially Stale `currentTime`

v2 correctly says local playback should seek to `transport.currentTime` before resuming after disconnect. That fixes the stale paused local video problem from v1.

Remaining issue: after a phone sleep, network blip, or background throttling, the last receiver status event can itself be stale. If the receiver continues playing for 30 seconds after the phone stops receiving updates, resuming local playback at the last observed `currentTime` is still visibly wrong.

Recommendation: track `lastStatusAtMs` alongside `currentTime` and `playerState`. On disconnect, if the last known receiver state was `"playing"`, extrapolate:

```text
resumeTime = lastCurrentTime + (Date.now() - lastStatusAtMs) / 1000
```

Clamp to duration and cap extrapolation to a conservative maximum, such as 60 seconds. If the status is too old or unknown, show a clear "Resume from TV position may be stale" prompt instead of silently resuming.

### P1: Local Autoplay Recovery Should Not Fail Silently

Phase 6 says the local video should call `video.play()` on disconnect and catch rejections silently. For live worship, a silent rejection is risky. Mobile browsers can reject playback when user activation requirements are not met, especially after backgrounding or route changes.

Recommendation: if local resume `play()` rejects, show a prominent tap-to-resume control with the target resume time already applied. The user should never be left thinking audio has resumed when it has not.

### P1: Buffering Commands Need a Deterministic Policy

v2 intentionally keeps controls enabled while buffering and says commands queue. That UX is good, but the command policy is underspecified.

Recommendation: define "queued" as deterministic latest-intent behavior:

- Seek commands are latest-wins during buffering.
- Play/pause is latest-wins, not every tap replayed in order.
- Volume/mute applies immediately if possible, otherwise latest-wins.
- Show one visible pending state, not repeated toasts.
- If the receiver remains buffering beyond a threshold, show actionable copy: check Wi-Fi / MP4 reachability / retry Cast.

This matters because rapid worship-leader taps during a TV stall are predictable.

### P1: Cast Unavailable UX Needs Mobile-Friendly Diagnostics

The plan mentions a disabled button plus tooltip when Cast is unavailable. Tooltips are weak on touch devices, and "unavailable" can mean several different things: unsupported browser, not HTTPS, no receiver on LAN, receiver not whitelisted, network isolation, or SDK load failure.

Recommendation: replace tooltip-only feedback with a tap-accessible diagnostic panel or bottom sheet:

- "Use Android Chrome on HTTPS."
- "Phone and TV must be on the same Wi-Fi/VLAN."
- "Receiver must be powered on and whitelisted for dev/staging."
- "Try opening the MP4 URL from this network."

This is a smooth-UX requirement because discovery failures will otherwise look like a broken app.

### P1: Error Telemetry Needs Production-Ready Rate Limiting

The proposed `/api/log-client-error` endpoint is useful, but an unauthenticated endpoint with an in-memory rate limiter is fragile on serverless infrastructure and weak against abuse. The plan correctly strips PII and signed URLs; that should remain.

Recommendations:

- Prefer a durable/distributed rate limiter for production, or keep telemetry behind a feature flag until that exists.
- Include structured fields that help debug without leaking secrets: browser, platform, cast app ID mode, transport kind, error kind, media source kind, and whether the URL had expired.
- Never log full signed URLs; host + path + expiry age is enough.

### P2: Presentation API Should Stay Dev-Only

The v2 scope already treats Presentation API as secondary/fallback. That is the right level. MDN marks Presentation API as limited availability, secure-context-only, and experimental. It should not be sold as a production fallback for church users.

Recommendation: label Presentation fallback in UX/docs as "developer/browser projection fallback" rather than as an equivalent TV path. Production guidance should be Cast on Android/Chrome or AirPlay to Apple TV for iPhone.

### P2: Test Plan Is Strong But Could Be Over-Brittle

The unit and component test list is thorough. The risk is that extensive JSDOM mocks of `window.cast.framework` can become brittle while still missing real device behavior.

Recommendation: keep the planned Vitest coverage for state transitions and command semantics, but do not treat it as sufficient for release. Add a small real-device acceptance checklist as the final gate, and require a rehearsal with the same TV/network class used in service.

## Tech Stack Maturity Assessment

| Component | Assessment | Notes |
|---|---|---|
| Google Cast Web Sender SDK | Mature | Correct primary choice for Android Chrome. Official docs require HTTPS and note iOS Chrome does not support casting. |
| Default Media Receiver | Mature / constrained | Good fit for a single baked lyric MP4. Limited error UI and no app-specific overlay, so operational validation matters. |
| MP4 direct playback from R2 | Mature if validated | Main risk is not R2 signing itself; it is TV network reachability, byte-range support, headers, and codec/faststart compatibility. |
| Presentation API | Declining / limited | Acceptable as dev fallback only. Do not rely on it for production service operation. |
| Next.js / React / Vitest stack | Mature | Good testability for app logic, weak substitute for real Cast device validation. |
| Client telemetry endpoint | Useful but needs hardening | Good for post-incident visibility; production rate limiting and careful redaction are required. |

## Recommendations Summary

| Priority | Recommendation |
|---|---|
| P0 | Resolve Default Media Receiver vs Custom Receiver documentation mismatch before implementation/release. |
| P0 | Add rendered MP4 compatibility and faststart/range-seek validation as a release gate. |
| P1 | Extrapolate receiver `currentTime` on disconnect when last known state was playing, with conservative caps. |
| P1 | Show tap-to-resume if local `video.play()` fails after disconnect. |
| P1 | Define buffering command queue semantics as latest-wins, especially for seek and play/pause. |
| P1 | Replace mobile tooltip-only unavailable state with actionable diagnostics. |
| P1 | Harden `/api/log-client-error` rate limiting for serverless production or feature-flag it. |
| P2 | Keep Presentation API explicitly dev-only / non-production fallback. |
| P2 | Keep the broad test plan, but require real-device rehearsal before service use. |

## Live-Service Go / No-Go Checklist

Before first live use:

- Android Chrome phone and Google TV/Chromecast are on the same Wi-Fi/VLAN.
- Receiver device is discoverable from the sender and whitelisted where needed.
- Signed MP4 URL opens from the same network and supports range seek.
- Rendered MP4 starts quickly on the TV and supports seek/jump without long stalls.
- Play/pause, volume, mute, chapter jump, and lyric-line jump work on the real TV.
- Disconnect resumes local playback from a reasonable TV position.
- Failed local resume shows tap-to-resume instead of failing silently.
- A service-length rehearsal runs for at least 60 minutes without URL expiry or receiver stalls.

## Positive Aspects To Preserve

- Receiver-as-truth design is correct.
- No silent reconnect-induced seek to the TV is the right safety choice.
- Dedicated mute command is the right cross-transport model.
- `loadMedia` failure cleanup and retry behavior is necessary and well captured.
- 4-hour Cast signed URL policy is acceptable for the confirmed under-2-hour service profile.
- iPhone web limitation is handled honestly instead of offering a broken Chromecast path.

## References

- Google Cast Web Sender setup: https://developers.google.com/cast/docs/web_sender
- Google Cast supported media: https://developers.google.com/cast/docs/media
- MDN Presentation API compatibility/maturity notes: https://developer.mozilla.org/en-US/docs/Web/API/Presentation_API

## Final Assessment

Approve v2 as the right implementation direction, but do not treat it as live-service-ready until the receiver/docs mismatch, MP4 playback validation, stale-disconnect recovery, mobile diagnostics, and real-device rehearsal gate are addressed.

