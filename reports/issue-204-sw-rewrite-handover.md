# Issue #204 — Service worker rewrite: artifact range-serving + registration

**Date:** 2026-09-16
**Branch:** `fix_offline_worship_playback`
**Commit:** `feat(webapp): service worker rewrite — artifact range serving + registration (issue #204)`
**Status:** SHIPPED — all acceptance criteria verified, including in-browser proofs. This document keeps what is worth reusing: the two real bugs the browser caught, and the environment traps that cost most of the session.

---

## 1. What landed

| File | Change |
|---|---|
| `delivery/webapp/public/sw-artifact-serving.js` | **New.** Testable seam: `artifactCacheKeyForUrl`, `parseRangeHeader`, `rangeResponseFrom`, `artifactHandler`. CJS export for vitest; publishes `self.artifactRangeServing` under `importScripts`. |
| `delivery/webapp/public/sw.js` | Registers the module as the single `/api/r2/artifact/*` route; dead precache route removed; `skipWaiting()` + `clientsClaim()`; catch handler gains the `video` destination branch. |
| `delivery/webapp/src/components/system/ServiceWorkerRegistrar.tsx` | **New.** Registers `/sw.js` on mount, renders null. Mounted in `layout.tsx`. |
| `delivery/webapp/src/lib/offline/precaching.ts` | workbox-window wrapper dropped (SW owns activation); plain `navigator.serviceWorker.register("/sw.js")`. |
| `delivery/webapp/src/proxy.ts` | Matcher no longer auth-redirects `sw.js` / `sw-artifact-serving.js`. |
| `delivery/webapp/src/test/lib/offline/sw-artifact-serving.test.ts` | 25 cases. |
| `delivery/webapp/package.json`, `pnpm-lock.yaml` | `workbox-window` removed. |

Cache keys are the ones `artifact-cache.ts` writes: `/api/r2/artifact/<jobId>/<file>` → `/sow-artifact-cache/<jobId>/{mp3,mp4,chapters}` in the `sow-artifacts` cache. They are not request URLs, so no Workbox strategy or URL-matching range plugin can serve them — hence the custom handler and **no `RangeRequestsPlugin` anywhere** (AC 2).

---

## 2. Two real bugs the browser caught (both unit-green before it)

1. **Handler signature vs. router contract.** `workbox` invokes route handlers as `handle({url, request, event, params})` — verified in `workbox-routing.prod.js` 7.0.0 (`o.handle({url:s,request:t,event:e,params:n})`). Destructuring `caches` without a default left `cachesRef` undefined inside the SW, so every real artifact request threw at `cachesRef.open(...)` and the router's catch handler turned it into `Response.error()`. Unit tests injected `caches` explicitly and never saw it. Fixed with `caches: cachesRef = caches`; regression test calls the handler with exactly the router's params and stubs the globals.
2. **Drained body on the degradation path.** `rangeResponseFrom` did `await cachedResponse.blob()` *before* deciding whether to slice, then returned the same response for unsatisfiable ranges — a body-used `Response` fails the consumer's fetch with `TypeError: Failed to fetch` instead of streaming the file. Fixed by reading through `clone()`; the test now asserts the returned body is readable (it previously asserted status only, which is why it passed).

---

## 3. Environment traps (the expensive part)

- **Requests issued from the DevTools/evaluation context bypass the service worker** in this Chrome build. `page.evaluate(() => fetch("/__probe204"))` reached the network while a SW raw listener was standing by; the same fetch from the page's own script hit the SW. Every "the SW isn't intercepting" symptom this session traced back to this. **Drive e2e SW checks from a script the page itself loads**, then read results out of the DOM (globals set by that script are invisible to `page.evaluate`, which runs in an isolated world).
- **`Network.emulateNetworkConditions({offline:true})` does not stop SW-originated fetches.** The SW kept fetching successfully "offline". A trustworthy offline test: stop the upstream (`hub stop sow-webapp-dev`) and have the page detect it by probing a `NetworkOnly` route (never cached, never SW-served) until the fetch rejects.
- **`importScripts` modules need a `sw.js` byte change to update.** Editing `sw-artifact-serving.js` alone leaves the installed worker running the old module — the browser only re-fetches `sw.js`. Unregister + `caches.delete` for a clean reinstall.
- **`/opt/sow/.env` has no `TRUSTED_ORIGINS`**, and `auth.ts` `trustedOrigins` only whitelists private LAN IPs — so `http://localhost:8080` logins fail origin validation once cookies exist. Dev-only workaround used: start the dev server with `TRUSTED_ORIGINS=http://localhost:8080,http://127.0.0.1:8080` (`node_modules/.bin/env-cmd -f /opt/sow/.env node_modules/.bin/next dev -p 8080 -H 0.0.0.0`; `pnpm dev` strips the override). Not committed. Curl sign-in can mint a session for a browser run: POST `/api/auth/sign-in/email` with `Origin: http://localhost:8080`, then `page.setCookie({name:"better-auth.session_token", …})`.
- Form login via Puppeteer needs the React-native value setter + `input`/`change` dispatch; `page.fill`/`type` silently no-op.

---

## 4. Verification evidence (headless Chrome, `http://localhost:8080`, SW controlling the page)

Online, seeded cache key (synthetic job → a 206 can only come from the SW cache):

| Check | Result |
|---|---|
| Boot document controlled without reload | `controller.scriptURL = /sw.js` (AC 1) |
| Hit + `Range: bytes=3-6` | `206`, `Content-Range: bytes 3-6/10`, `Content-Length: 4`, `Content-Type: video/mp4`, body `3456` (AC 3) |
| Hit, no Range / suffix `bytes=-3` | `200` full body / `206 bytes 7-9/10` |
| Hit + unsatisfiable `bytes=999-1200` | `200` full body (regression, was `TypeError`) |
| Miss + Range, real 200 upstream | `206 bytes 2-5/12` `CDEF`; stored key holds the **full** `200` body `ABCDEFGHIJ#1`; upstream saw no Range (a forwarded Range returns a 400 marker) (AC 4) |
| Same key afterwards | `206 ABCD` and `200 …#1` — still the first stored body (AC 5) |
| Miss without Range | `200 …#2`, stored under the mapped key (AC 5) |
| `?download=1` | network, not cache (counter advanced) |
| Non-200 upstream (`404`) with Range | passthrough `404`, no `Content-Range`, nothing stored |
| Offline (dev server stopped): hit + Range | `206 bytes 3-6/10` `3456` |
| Offline: uncached `<video>` | `error` event (AC 7 video branch) |
| Offline: uncached JSON API / plain | `503 {"error":"offline"}` / `TypeError` (AC 7) |

`/api/songs`, `/api/songsets`, `/api/signed-url` route bodies are byte-identical to pre-fix; no precache route remains (AC 6). Full webapp suite 2343 passed / 1 file skipped; `tsc --noEmit` exit 0; `pnpm lint` exit 0 with only the 4 pre-existing warnings.

A two-axis review ran over `610df41b..HEAD`; its fixes are in `93c882c2`. The two that are easiest to reintroduce:

- Cache Storage is best effort (handler degrades on `open`/`match`/`put`/body-read failure and serves from the network). Before that guard, a blocked or over-quota cache turned an *online* artifact request into `Response.error()` — a media-failure overlay for bytes already in hand.
- The `importScripts` token is the module's content hash and `artifact-cache-sw-parity.test.ts` asserts it. It is load-bearing, not cosmetic: a browser re-runs `importScripts` only when `sw.js`'s own bytes change, so a module-only edit with a stale token never reaches an installed client.

---

## 5. Known limitations (deliberately not addressed here)

- **Ranged serving reads the cached body through `blob()` before slicing** — the approach the parent spec's own snippet prescribes, and the same one workbox's `RangeRequestsPlugin` uses. It keeps the artifact out of the *page*; whether Chrome's blob backing keeps it off the SW's heap is **unmeasured**, so the issue's "seeks without holding the file in memory" motivation is not demonstrated, and each ranged request re-materializes the body (disk I/O, not necessarily memory). Measure before optimising; the fallback worth benchmarking is streaming the cached body through a `TransformStream` that discards to `start` and ends at `end`, taking the total from the cached response's `Content-Length`. Do **not** add a per-key Blob cache in the SW global (pins the body across requests, dies with the worker), and note `URL.createObjectURL` is unavailable in service workers.
- **The catch handler's `document` branch is unreachable.** No route in this SW matches a navigation, and workbox-routing 7 applies the catch handler only to a matched handler that rejects, so an offline navigation gets the browser's offline page, not our HTML fallback. Pre-existing, unchanged by #204; AC 7's "keeps" is satisfied syntactically only.

---

## 6. Follow-ups (not done here)

- `auth.ts` could trust `http://localhost:<port>` / `http://127.0.0.1:<port>` when `NODE_ENV !== "production"` — removes the dev-login landmine above.
- The SW scripts stay reachable unauthenticated through `PUBLIC_PATHS` (`/sw.js`, `/sw-artifact-serving.js`), not the matcher. A **new** `importScripts()` target must be added there too, or registration dies on a 307 — verify with a cookie-less `curl -sI` (`200`, no `location:`) rather than from a signed-in browser, whose session branch returns `next()` and hides the failure.

---

## 7. Verification environment, condensed

Headless Chrome through the hub with a throwaway profile, dev server on `http://localhost:8080`, session obtained by `curl` sign-in and injected with `page.setCookie`. The full battery was driven from a **page-served script** writing results into the DOM, read back by polling `textContent`; the upstream was stopped with `hub stop sow-webapp-dev` for the offline half. Details of why that shape is required are in §3.
