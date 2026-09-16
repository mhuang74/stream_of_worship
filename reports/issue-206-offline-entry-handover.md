# Issue #206 — Offline entry: cold start, navigation, PrePlay offline variant

**Date:** 2026-09-16
**Branch:** `fix_offline_worship_playback`
**Status:** Implemented + production-build e2e verified. Handover keeps the environment traps that cost the session (they will recur).

## What landed

| File | Change |
|---|---|
| `delivery/webapp/public/sw.js` | **Document route** (issue #206): `request.mode === "navigate" || RSC: 1` → `NetworkFirst` over `sow-pages` (10s timeout, statuses [0,200], 50 entries / 7 days). `cacheWillUpdate` drops `response.redirected` responses so an auth 307→login navigation never stores login HTML under the asked URL. |
| `delivery/webapp/src/lib/offline/document-cache.ts` | **New.** `SOW_PAGES_CACHE_NAME`, `controllerDocumentPath`, `cacheControllerDocument(songsetId)`: fetches the controller document, `cache.put`s it into `sow-pages`, then warms the same-origin scripts/styles/fonts the HTML references as `<link rel=preload>`s (a page `fetch()` has destination `""`, so no SW static route would cache them — a real browser subresource fetch is required). 15s cap; best-effort (returns false, never throws). |
| `delivery/webapp/src/lib/offline/download-offline.ts` | Calls `cacheControllerDocument(songsetId)` after the index write. Best-effort — a download whose document pre-cache fails still succeeds (degraded cold start to the offline fallback page, never a failed download). |
| `delivery/webapp/src/app/songsets/[id]/play/page.tsx` | **Offline-available card**: `offerOfflineEntry()` (reads offline index) on a non-401/404 fetch failure OR a thrown fetch → renders `OfflineAvailableCard` instead of the error screen. `handleStartWorship` is connectivity-aware: `navigator.onLine === false` → `window.location.assign(controller)` (full document navigation), else `router.push` (unchanged). The card's own tap always uses the document navigation. 404 is distinct (`SongsetNotFoundError`); 401 still redirects to login. |
| `delivery/webapp/src/components/play/OfflineAvailableCard.tsx` | **New.** Songset name from the index + Start Worship + offline hint. i18n `play.offline.heading`/`play.offline.hint` (EN + zh-Hant). |
| `delivery/webapp/src/components/offline/OfflineIndicator.tsx` | Converted to `useSyncExternalStore` (server snapshot `false`) so an offline cold start hydrates without a mismatch — same fix #205 applied to the controller boot hint. |
| `delivery/webapp/src/app/layout.tsx` | Mounts `<OfflineIndicator />` before `<Toaster />`. |
| i18n `play.ts` | `play.offline.heading` / `play.offline.hint` in both locales. |

## Acceptance criteria → evidence (production build, headless Chromium)

Fixture: songset `WBn5Kd0RMD214FECGX4Ud` ("Thursday Worship"), render job `0Uv-yKD9ojmDWV31JnTL8`, real R2 artifacts (mp3 56 MB, mp4 50.8 MB, chapters).

- **AC-1 (SW document route serves cached documents, network-first; controller HTML pre-cached at download):** warm-up session's real download wrote `sow-pages` entry `/songsets/WBn5Kd0RMD214FECGX4Ud/play/controller` (26 279 B prod HTML; 32/32 referenced scripts/styles present in `sow-static-assets` after the preload wire-up). sw.js route is `NetworkFirst`; online regression (below) served the **network** document — zero visible change.
- **AC-2 (offline cold start → app loads, controller boots and plays, seek works):** prod server stopped, fresh Chrome process, same profile, `Navigator.prototype.onLine` → false injected via `Page.addScriptToEvaluateOnNewDocument`. Navigated to `/songsets/WBn5Kd0RMD214FECGX4Ud/play/controller`:
  - not the "You are offline. Please reconnect." fallback — real app shell;
  - `<video>` with `currentSrc = http://localhost:8080/api/r2/artifact/0Uv-yKD9ojmDWV31JnTL8/output.mp4` (proxy URL ⇒ SW-served), `readyState 4`, `duration` 1405.652, "Offline playback" hint, **zero `/api/songsets|render-jobs|signed-url` fetches** (resource timeline: only `/api/auth/get-session` + artifact proxy, all `transferSize 0`);
  - seek: `video.currentTime = 600` → `seeked`, `currentTime` 600;
  - `fetch(Range: bytes=100-199)` on the proxy URL → **206 with `Content-Range: bytes 100-199/50851316`** (range served from Cache Storage by the SW);
  - `OfflineIndicator` banner (`You are offline`) rendered.
- **AC-3 (offline Start Worship tap → full document navigation):** same offline browser, play page showed the offline card (below); tapping Start Worship navigated `.../play` → `.../play/controller` and booted the controller (`readyState 4`, proxy URL, "Offline playback").
- **AC-4 (play page offline-available card):** with the server stopped and the songsets API answer unreachable, the play page rendered "Ready for offline playback / You are offline. This worship set was downloaded and can start without a network." + songset name + Start Worship — from the index record, not the error screen.
- **AC-5 (OfflineIndicator in root layout):** banner renders on network loss (`role=status aria-label="You are offline"`), absent online.
- **AC-6 (online regression):** prod server up, fresh session cookie, fresh Chrome: play page signed-in, no offline card; Start Worship → controller with the **presigned R2 URL** (`r2.cloudflarestorage.com/...`), `readyState 4`, **no** offline hint, no banner. Identical online path.

Component/module coverage: `document-cache.test.ts` (8), `download-offline.test.ts` (+2), `artifact-cache-sw-parity.test.ts` (+1 sow-pages contract), `play-page.test.tsx` (8 offline-entry cases), `OfflineIndicator.test.tsx` (updated 2 event tests to flip `navigator.onLine` atomically with the event, matching the real browser contract). Full webapp suite 2397 passed / 5 skipped / 10 todo; `tsc --noEmit` clean; `pnpm lint` clean (4 pre-existing warnings).

## Environment traps (the expensive part — will recur)

1. **Root FS 100% full.** `/` had 783 MB free; Chrome's Cache Storage (profile in `/tmp/sow-chrome-206`) fails mid-write with `Cache.put() ... encountered a network error` for the 50–56 MB artifacts. Every earlier "download failed" symptom traced to this. Fix: profile dir on `/home` (`/home/mhuang/.cache/sow206/profile`). Synthetic 56 MB puts succeeded, which is why profile-dir tests passed while the real download didn't.
2. **`Page.addScriptToEvaluateOnNewDocument` survives the referencing CDP session's detach and leaks the `Navigator.prototype.onLine` override into every later page of that browser process** (and bleeding into new tabs). For online checks, restart Chrome or go to a fresh process. Prefer a new Chrome process per behavioural phase (cold vs online).
3. **Dev-mode hydration confound:** `next dev` (Turbopack) html carries dev-only chunks; offline cold start with the cached dev html + dev chunks stalled at "Loading player..." (flight payload never consumed; `__next_f.length === 0`) — dev-only machinery, not a product regression. All offline/online proofs in this sign-off ran against `next build` + `next start`, the shipped shape.
4. **`page.evaluate` runs in an isolated world** on CDP-attached Chromium (globals set by page scripts invisible to `page.evaluate`); assert via `Runtime.evaluate` (main world) and read page-script results from the DOM. Page-originated probes via a real `<script>`/page fetch are also required for SW-intercepted requests (DevTools-eval fetch bypasses the SW).
5. **Production server auth:** `next start` rejects the default Better Auth secret and needs `BETTER_AUTH_SECRET` + `BETTER_AUTH_URL=http://localhost:8080` + `TRUSTED_ORIGINS` (matches the #204 handover's dev-origin guidance). The prod cookie is `__Secure-better-auth.session_token` (Secure); use `Network.setCookie` with `secure: true`.
6. **`Network.offline:true` emulation detaches with its CDP session** and did not cleanly apply to SW-initiated fetches; the trustworthy airplane-mode proxy is: stop the upstream + override the `onLine` signal (`Navigator.prototype.onLine` getter) in the main world.

## Out of scope (unchanged from the issue)

Share flow `/share/[token]`; PWA manifest; RSC payload pre-caching at download time; list-page badge (sibling).

## Review outcome (two-axis)

Spec axis: all six ACs MET, no scope creep. One P2 (SEV-2) — the offline card could surface for a genuine *online* 5xx (a reachable server error) whenever an index record exists, showing misleading "You are offline" copy. Fixed: the non-ok branch now offers the card only when the response is exactly the SW's offline `503 {"error":"offline"}`, and handles other non-OK statuses inline (`setError` + `return`) so the outer catch's `offerOfflineEntry()` only ever runs for genuine network rejections. Regression test added (`keeps the error screen for a genuine server 503`).

Standards axis: no P0/P1/P2. P3s triaged: offlineRecord reset at effect start (played: stale-card guard) and preload settle-timer cleared (played: `clearTimeout` in the settle handler). P3 skipped (test `window.location`/`navigator.onLine` defineProperty not restored — matches the existing repo test pattern; isolated describes).

## Repro quick-start (prod-build e2e)

```bash
cd delivery/webapp
node_modules/.bin/env-cmd -f /opt/sow/.env node_modules/.bin/next build
# then start with BETTER_AUTH_SECRET/BETTER_AUTH_URL/TRUSTED_ORIGINS as above
```
Warm-up: sign in (curl POST `/api/auth/sign-in/email` with `Origin`), visit `/songsets` + the play page, click "Download for offline" (real 50–100 MB download), assert `sow-pages` holds the controller route. Stop the server, launch a fresh Chrome process with the **same** profile dir, inject the `onLine=false` override, navigate to the controller URL, then assert boot + seek + the 206 range fetch.
