# Share-link viewers get their own offline copies, token-scoped and frozen

The Share URL play controller gains Offline Copy support alongside the songset controller, under different cache semantics than the owner's songset downloads.

**Share copies are token-scoped.** A share viewer's downloaded copy lives in its own namespace keyed by the share token, never in the songset-keyed offline index the owner uses. This prevents a collision where opening your own share link on a device that already holds your Download for Offline copy would overwrite (or be overwritten by, with stale-render invalidation of the artifacts) the owner record. The cost is accepted: a logged-in owner who downloads both stores the media twice.

**Share copies are always allowed.** Any viewer of a valid share link may download, regardless of the share's dormant `allowDownload` flag, which stays unused. The Download affordance lives on the share landing page — explicit user action, consistent with ADR-0003's principle that the caching affordance lives on the playback surface and downloads are never automatic (full lyrics videos are large; auto-download on open surprises recipients on metered data).

**A share copy is a frozen snapshot.** The cached copy pins the renderJobId resolved at download time. A songset-type share otherwise resolves to whatever render is current at view time; a cached copy does not. Revocation or expiry of the token does not wipe an already-cached copy — it keeps playing. Staleness surfaces only on the landing page, which calls the share API on every visit and can compare the current renderJobId against the cached copy (Re-download for Offline). The controller boots cache-first with zero API calls when a copy exists, so it deliberately cannot detect staleness; a boot probe would defeat offline playback.

## Considered options

- Auto-download on every share open — rejected: large media, metered data, contradicts ADR-0003's explicit-affordance principle.
- Reuse the songsetId-keyed offline index for share copies — rejected: owner-record collision and artifact invalidation when the two copies pin different renders.
- Owner-aware download routing (detect the owner's session on the landing page and download into the songset namespace) — rejected: reintroduces the collision to save disk on a rare path.
- Controller-side staleness probing on boot — rejected: defeats the zero-API cache-first boot that makes offline playback work.
- Gate share downloads on `allowDownload` — rejected this round: the flag was never exposed in the UI; every share is downloadable.

## Consequences

- The share controller plays a revoked/expired link's cached copy offline; the landing page is the only surface that reflects revocation.
- Share-viewer feedback is session-gated (anonymous viewers never see the Lyrics Feedback row); anonymous submissions remain out of scope.
