# Share play controller playback-feature parity with songset controller

GitHub issue: #218 (label: ready-for-agent) · ADR: docs/adr/0009-share-offline-copies-token-scoped-frozen.md

## Problem Statement

The Share URL play controller (the controller an anonymous recipient of a share link lands on) is a minimal sibling of the logged-in songset play controller. It plays the video and mirrors transport controls, but lacks the playback features the owner's controller has: audio-only playback for MP3-only renders, media-error recovery, Lyrics Feedback, and offline playback. The two controllers also duplicate their Cast/Presentation transport wiring, so every new playback feature must be manually remembered and ported to both — features drift silently.

## Solution

Extract the shared playback-core machinery (boot flow, media model, media-error recovery, transport wiring) into one shared hook consumed by two thin controller pages — the logged-in songset controller and the anonymous share controller. Port the full connectivity-aware boot structure and media-failure recovery to the share controller, and extend the share API so the share controller can support audio-only renders, session-gated Lyrics Feedback, and Offline Copies scoped to the share token. After this, playback features land on both controllers automatically.

## User Stories

1. As a share recipient, I want the share controller to play an MP3-only render as audio-only with lyrics and controls, so that shares of audio-only songsets work like my own songsets do.
2. As a share recipient, I want a failed media load to attempt recovery (swap to an available cached source) instead of a dead-end error, so that a dying presigned URL doesn't end the worship session.
3. As a share recipient with a downloaded copy, I want the controller to boot from the local cache with zero network calls, so that playback works with no connectivity.
4. As a share recipient whose network drops mid-session, I want the controller to fall back to my cached copy instead of an error screen, so that worship continues.
5. As a share recipient, I want an explicit Download affordance on the share landing page, so that I choose when to spend data on a full lyrics video.
6. As a share recipient, I want the landing page to tell me my downloaded copy is stale and offer Re-download, so that I don't lead worship from an outdated video.
7. As a logged-in user opening a share link, I want the Lyrics Feedback affordance available there too, so that I can flag lyric problems wherever I encounter them.
8. As an anonymous share viewer, I want no feedback icons shown, so that I'm never offered an action that can't work.
9. As a share recipient, I want the cached copy to keep playing even if the share link is later revoked or expires, so that a worship service isn't interrupted by link management.
10. As a logged-in owner who downloaded a songset and also opens its share link, I want both copies to coexist without one clobbering the other, so that my owner download stays intact.
11. As a share recipient, I want the same arrow-key lyric jumps, duration/time-remaining, and transport controls as the songset controller, so that both surfaces feel identical.
12. As a share recipient casting to a TV, I want Cast and the Presentation fallback to behave identically to the songset controller, so that my setup steps don't change per surface.
13. As a developer, I want the boot/recovery/transport core in one shared hook, so that new playback features land on both controllers without manual porting.
14. As a developer, I want the share API to declare mediaKind explicitly, so that the client never infers media type from URL shape.
15. As a share recipient, I want a stale-download hint only where the network is being used anyway (landing page), so that offline playback isn't burdened with freshness probes.
16. As a songset owner, I want the existing controller behavior preserved exactly (auth redirect, offline hint, recovery, feedback), so that parity work never regresses the original surface.

## Implementation Decisions

**Delivery is staged in two PRs:**
- PR 1 (parity core): share API contract extensions + shared hook extraction + port of audio-only boot, media-error recovery, and session-gated feedback hashes to the share controller.
- PR 2 (share offline): share-scoped download + cache-first boot + landing-page staleness/re-download.

**Shared playback core.** Extract a hook covering the connectivity-aware three-branch boot (definitive Offline at boot → cache-first, zero API calls; chain failure while not positively offline → offline fallback + connectivity probe + informational toast; otherwise → online chain), the media model (source kind, offline/via-proxy flags, render job id), media-error recovery (proxy→blob swap for offline sources; one per-boot swap to a downloaded copy for online sources), and the Cast + Presentation transport wiring (device toasts, unified send/stop handlers, presentation-media status). Both controller pages become thin: the songset page keeps its authenticated fetch chain and 401→login redirect; the share page keeps its anonymous token fetch. Exit routes differ per page. The songset controller's existing behavior is preserved verbatim.

**Share API contract (one bundle).** The anonymous share-token response gains: explicit `mediaKind` (video/audio, derived server-side — the client never infers from URL shape); per-position Recording content hashes (same ordering contract as the songset controller uses for Lyrics Feedback); a `viewerAuthenticated` flag (the route sees cookies; avoids a separate session round-trip); and the fields needed for share-scoped caching (current renderJobId and artifact availability). A share pinned to a specific render job continues to resolve to that job; a songset-type share resolves to the current completed render. No authenticated endpoints are introduced on the share path.

**Lyrics Feedback on share.** Hashes are passed to the player; the feedback row renders only when `viewerAuthenticated` is true. Anonymous viewers see no icons (consistent with feedback being per-user, session-required data). This respects ADR-0007: feedback stays advisory, per-user, and never mutates pipeline state.

**Share Offline Copies (ADR-0009).** Download lives on the share landing page as an explicit button — never automatic. Copies live in a separate namespace keyed by the share token, never in the songsetId-keyed owner index; owner and share copies coexist (duplication accepted). A copy is a frozen snapshot of the renderJobId at download time: token revocation/expiry does not wipe it, and staleness is detected only on the landing page (which calls the API on every visit and compares the current renderJobId against the cached copy), offering Re-download for Offline. The controller boots cache-first with zero API calls when a copy exists and cannot detect staleness by design. The dormant `allowDownload` share flag remains unused.

**Component layer unchanged.** The shared player component already accepts audio-only sources, media-error takeover, chapter recording hashes, and offline hints; both controllers simply pass them.

## Testing Decisions

Good tests assert external, observable behavior — what a user sees and what network/cache calls the page makes — never hook internals, state field names, or wiring details.

Three existing seams, no new ones:

1. **Share API route.** Response-contract tests at the route boundary: mediaKind, hash ordering, viewerAuthenticated truthiness, render-job selection per share variant. Prior art: the existing share-token route tests.
2. **Controller pages.** Both pages rendered with mocked fetch/connectivity/service worker; assert boot-branch behavior (offline-at-boot serves cache with no API calls, chain failure falls back with a toast, online chain happy path), media-error recovery swaps and terminal overlay, feedback-row presence/absence by auth state, audio-only element selection, exit routes. The songset page's existing page-level tests are the prior art and must keep passing unchanged; the share page gains equivalent coverage. The shared hook is deliberately not a separate test seam — it is tested through both pages, which directly proves parity.
3. **Offline plumbing.** Share-namespace record creation, artifact caching, landing-page staleness comparison, and service-worker serving of share-cached artifacts. Prior art: existing offline index/playback/download and SW artifact-serving tests.

## Out of Scope

- Anonymous Lyrics Feedback submissions (feedback stays per-user; schema unchanged per ADR-0007).
- Gating share downloads on the `allowDownload` flag or wiring it into the Share Dialog.
- Auto-download on share open.
- Controller-side staleness probing.
- Owner-aware download routing (detecting the owner's session to reuse the songset namespace).
- Changes to the shared player component, projection pages, or Cast/Presentation transport hooks.
- Freshness validation of cached share copies at controller boot time.

## Further Notes

- The three-branch boot and its deliberations (single boot decision at effect time, no re-run on connectivity flips, probe only after app-level fetch failure) are documented in the songset controller; the shared hook must carry those comments forward, since the semantics are subtle and regression-prone.
- The connectivity state machine, service-worker document routes, and artifact-cache machinery are unchanged; only a new consumer (the share controller) is added.
- Domain vocabulary: "Offline Copy", "Connectivity", "Projection", "Lyrics Feedback" per CONTEXT.md; share-scoped copies extend the Offline Copy concept under ADR-0009's distinct semantics.
- Translation keys: new UI strings (Download/Re-download on landing, any share-specific errors) need both en and zh-Hant entries.
