# Chromecast `loadMedia` throws `Cannot read properties of undefined (reading 'BUFFERED')` — Fix Plan (2026-06-30)

Status: **Plan only. Not implemented.**

Scope: `delivery/webapp` (Cast sender `loadMedia` path). This is the execution companion
to the prior investigation `specs/chromecast-androidtv-discovery-fix-plan-2026-06-30.md`
and supersedes its Phase 3/4 hypothesis: the failure is no longer at
`cast.framework.requestSession()` — discovery and session establishment now succeed. The
new symptom is a JavaScript `TypeError` thrown *after* the session is established, in the
`loadMedia` step.

## Request

Chrome can now find the AndroidTV and `requestSession()` succeeds (the prior
`session_request_failed` is resolved). On Cast, `loadMedia` throws:
`Cannot read properties of undefined (reading 'BUFFERED')`. Investigate and write a
detailed plan only. Do not implement.

## Summary of findings (read-only investigation)

### The symptom moved downstream of discovery and session request

The prior plan's hypothesis was that `cast.framework.requestSession()` rejects against
AndroidTV. The new error string — `Cannot read properties of undefined (reading 'BUFFERED')`
— is a runtime `TypeError`, not an SDK rejection. It is thrown by the app's own code at
`delivery/webapp/src/hooks/useCast.ts:728`, inside the `loadMedia` block that runs only
*after* `ctx.requestSession()` resolves and `getCurrentSession()` returns a live session:

```ts
// delivery/webapp/src/hooks/useCast.ts (excerpt)
const mediaInfo = new chrome.cast.media.MediaInfo(m.videoUrl, "video/mp4");
mediaInfo.metadata = {
  title: m.title,
  metadataType: chrome.cast.media.MetadataType.GENERIC,
};
mediaInfo.streamType = chrome.cast.StreamType.BUFFERED;   // <- THROWS
```

That `requestSession()` no longer rejects confirms:
- `loadCastFramework=1` is loading the framework (Phase 2 of the prior plan is satisfied).
- `setOptions` ran with a resolved `receiverApplicationId` (the Default Media Receiver
  `CC1AD845` fallback resolved).
- The device picker dialog was shown and the user selected the AndroidTV.
- A `chrome.cast.Session` object was returned.

The failure is therefore *not* a discovery, eligibility, origin, cert-trust,
`autoJoinPolicy`, `androidReceiverCompatible`, or `castAppIdMode === "unset"` problem.
Those branches of the prior plan's Phase 4 do not apply.

### Root cause: `StreamType` is declared and accessed at the wrong namespace

The Google Cast Web Sender SDK exposes `StreamType` at
**`chrome.cast.media.StreamType`**, *not* `chrome.cast.StreamType`
([reference](https://developers.google.com/cast/docs/reference/web_sender/chrome.cast.media.StreamType)).
The app's ambient type declaration declares it at the wrong spot, and the implementation
follows that wrong path:

- **Declaration bug** — `delivery/webapp/src/types/cast-sdk.d.ts:41` declares
  `export enum StreamType { ... }` directly inside `namespace chrome.cast` (top-level of
  the chrome.cast namespace). The SDK actually exposes it inside `chrome.cast.media`.
- **Call-site bug** — `delivery/webapp/src/hooks/useCast.ts:728` reads
  `chrome.cast.StreamType.BUFFERED`. At runtime `chrome.cast.StreamType` is `undefined`
  (only `chrome.cast.media.StreamType` exists on the SDK global), so accessing `.BUFFERED`
  throws `TypeError: Cannot read properties of undefined (reading 'BUFFERED')`.

Notably, the neighboring `MetadataType` is declared and used *correctly* under
`chrome.cast.media` (cast-sdk.d.ts:67, useCast.ts:726). `StreamType` is an inconsistent
outlier.

### Why tests did not catch it

`delivery/webapp/src/test/hooks/useCastTransport.test.ts:139` mocks `chrome.cast` with
`StreamType: { BUFFERED: "buffered" }` placed at the `chrome.cast` level (mirroring the
buggy code), not under `chrome.cast.media`. The test therefore passes against the buggy
shape because the mock matches the wrong namespace the code reads from. The test should
mirror the real SDK shape (`StreamType` nested under `chrome.cast.media`) so it guards
against the regression rather than encoding it.

The SDK is loaded at runtime via `delivery/webapp/src/lib/cast/loader.ts` as an external
script (`cast_sender.js?loadCastFramework=1`); its globals are not statically checked
against the ambient `.d.ts` in any test that actually constructs a `MediaInfo` and reaches
line 728 end-to-end.

### Why PR #119 / framework-loading commits are not implicated

Neither PR #119 ("Fix TV projection, playback controls...") nor the `f1305d8` / `1fd9c0f`
Cast commits touched the `MediaInfo` / `streamType` assignment or the `StreamType` enum
declaration. The assignment has been referencing the wrong namespace since the
`streamType` line was introduced; discovery failing earlier (per the prior plan) simply
masked it, because `requestSession()` rejecting short-circuited the function before line
728 was ever reached. The session now succeeding has *uncovered* a latent bug rather than
introducing one.

## Root cause (confirmed by static analysis)

`chrome.cast.StreamType` does not exist on the Cast SDK global; only
`chrome.cast.media.StreamType` does. `delivery/webapp/src/hooks/useCast.ts:728` reads the
non-existent path, throwing the exact `TypeError` the user reported.

## Execution plan

### Phase 1 — Move the `StreamType` declaration into the `chrome.cast.media` namespace

File: `delivery/webapp/src/types/cast-sdk.d.ts`

1. Remove the `export enum StreamType { ... }` block (lines 41–45) from the top of
   `namespace chrome.cast` (just below `VERSION` / `isAvailable` / `AutoJoinPolicy` /
   `Capability`).
2. Add the same `export enum StreamType { BUFFERED = "buffered", LIVE = "live", OTHER = "other" }`
   block inside `namespace chrome.cast.media`, adjacent to the existing
   `export enum MetadataType { ... }` block (cast-sdk.d.ts:67). Place it either immediately
   before or immediately after `MetadataType` for locality with the `MediaInfo` class that
   consumes it.
3. On line 82 (the `MediaInfo.streamType` field), the type reference
   `streamType?: StreamType | string;` must become `streamType?: media.StreamType | string;`
   — `StreamType` is no longer in scope from the outer `chrome.cast` namespace, so use the
   qualified path matching the rest of the namespace's type aliases.

### Phase 2 — Fix the call site in `useCast.ts`

File: `delivery/webapp/src/hooks/useCast.ts`

1. Line 728 — change `chrome.cast.StreamType.BUFFERED` → `chrome.cast.media.StreamType.BUFFERED`.

Note: `streamType` is optional on `MediaInfo`; an alternative would be to drop the
assignment entirely (the receiver defaults to buffered). But explicit assignment is the
documented best practice for VOD MP4 content, so keep it with the corrected namespace
rather than removing it.

### Phase 3 — Fix the test mock to mirror the real SDK shape

File: `delivery/webapp/src/test/hooks/useCastTransport.test.ts`

1. Move `StreamType: { BUFFERED: "buffered" }` from the `chrome.cast` mock object
   (currently around line 139, sibling of `AutoJoinPolicy`) into the `chrome.cast.media`
   mock object (around line 140, sibling of `MetadataType` / `DEFAULT_MEDIA_RECEIVER_APP_ID`).
2. Confirm no other test mocks `chrome.cast` with a top-level `StreamType` (rg surfaced
   only this one occurrence outside of `cast-sdk.d.ts` and `useCast.ts`).
3. Optionally add a post-`loadMedia` assertion that verifies `mediaInfo.streamType === "buffered"`
   on the mocked `MediaInfo` instance, so the guarantee becomes explicit.

### Phase 4 — Verify the Phase 5 receiver-auth/URL fix from the prior plan

Once the `TypeError` is resolved, `loadMedia` will actually fire against the AndroidTV
receiver. The prior plan's Phase 5 items then become runnable for the first time:

- Confirm `/songsets/.../play/projection` and `/share/.../play/projection` routes are public
  (`delivery/webapp/src/proxy.ts` public-route list) — no `/login` redirect for the
  receiver's projection fetch.
- Confirm an unauthenticated API call returns JSON `401`, not an HTML `/login` redirect
  (the old `invalid token '<'` failure surface).
- Inspect the `loadMedia` `MediaInfo.contentId` to confirm it is the presigned R2 URL
  with `cast=true` (4-hour TTL), *not* an `/api/...` path.
- Confirm playback starts on the AndroidTV (not merely `loadMedia` resolving). If
  `loadMedia` resolves but the receiver errors out (e.g. URL redirects to HTML login),
  that is a *new* bug distinct from this one and should be captured in a separate plan
  rather than scattering Phase 4 of this plan.

### Phase 5 — Verification gates

```bash
pnpm --filter sow-webapp lint
pnpm --filter sow-webapp test
```

Both must pass. The transport test (`useCastTransport.test.ts`) must still pass with the
corrected mock shape — if it breaks, the mock was over-coupled to the bug rather than to
the SDK, which is itself a finding worth a comment in the test file.

After lint+test pass, re-run the live Phase 3 capture from the prior plan: a fresh
`POST /api/log-client-error` body (if any error is still posted) should no longer mention
`BUFFERED`, and `castState` should reach `CONNECTED` with `loadMedia` playing the MP4 on
the AndroidTV.

## Files that will be touched during implementation

- `delivery/webapp/src/types/cast-sdk.d.ts` — move `StreamType` enum into
  `chrome.cast.media`; update `MediaInfo.streamType` type reference on line 82.
- `delivery/webapp/src/hooks/useCast.ts` — single-character-namespace fix on line 728.
- `delivery/webapp/src/test/hooks/useCastTransport.test.ts` — move `StreamType` mock into
  the `chrome.cast.media` block; optional post-`loadMedia` assertion.

## Files that will NOT be touched (explicit non-goals)

- `delivery/webapp/src/lib/cast/loader.ts` — SDK timing is not implicated; discovery and
  session establishment succeed, so `?loadCastFramework=1` and the `loadCastSdk().then()`
  flow are healthy.
- `delivery/webapp/src/proxy.ts`, cert/mkcert workflow, `package.json` `dev:https` —
  origin/secure-context is not implicated; discovery proves the secure context is
  eligible.
- `formatCastRequestError` (`useCast.ts:175`) — error formatting is not implicated; the
  thrown `TypeError` was surfacing as a catch-all, and once fixed, `formatCastRequestError`
  need not change unless Phase 4 surfaces a *new* SDK error code that warrants a clearer
  message (defer to a separate hardening plan).
- `androidReceiverCompatible` / `autoJoinPolicy` / `castAppIdMode` paths — previously
  covered, not implicated here.

## Risks / out-of-scope follow-ups

- If `loadMedia` resolves but the receiver hits the old `invalid token '<'` failure (i.e.
  the projection URL still returns HTML redirect), that is a *new* bug to capture in a
  separate plan — do not expand this fix's scope into PR #119's projection-route territory.
- If a *second* wrong-namespace enum lurks in `cast-sdk.d.ts` (we confirmed only
  `StreamType` was mis-declared; `MetadataType` is correct), a future audit pass could
  cross-check every `chrome.cast.*`/`chrome.cast.media.*` access site. Out of scope here.
- The Phase 6 hardening items from the prior plan (clearer `formatCastRequestError`
  messages mapping `RECEIVER_UNAVAILABLE` vs `SESSION_ERROR`; surfacing `castState` in
  the diagnostic sheet) remain unaddressed and are intentionally deferred.

## Verification (summary)

- `pnpm --filter sow-webapp lint && pnpm --filter sow-webapp test` passes with the
  corrected mock shape.
- Live re-cast against the AndroidTV: no `BUFFERED` error; `castState === "CONNECTED"`;
  the MP4 plays on the TV.
