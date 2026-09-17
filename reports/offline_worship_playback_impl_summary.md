# Offline Worship Playback — Implementation Summary

**Date:** 2026-08-23
**Branch:** `fix_offline_worship_playback`
**Base:** `2f52d355` · **HEAD:** `c05f5af4`
**Spec:** [`specs/fix_offline_worship_playback_v2.md`](../specs/fix_offline_worship_playback_v2.md) (spec issue [#202](https://github.com/mhuang74/stream_of_worship/issues/202))
**Issues:** #203, #204, #205, #206, #207, #208 (committed) · #210 (review fixes, committed in `a369abc3`)

> **Developer explanation:** [docs/offline_worship_design_explained.md](../docs/offline_worship_design_explained.md) — how the service worker serves the cached page, artifacts, and Range seeks.

---

## Problem

The download half of offline worship playback already worked: a leader could cache a rendered
songset's artifacts. The playback half did not. Opening the controller page required four
live, auth-gated fetches (songset, song list, signed URL, chapters) plus a 4-hour presigned R2
URL, and **nothing ever read the cache** — so a cached songset was unusable the moment the
network dropped. Compounding it, the service worker was never registered: `public/sw.js` was
inert, so no route, no offline navigation, and no cached artifact could ever be served. The
branch closes the loop end to end: cache the artifacts, cache the controller document, register
the worker, serve artifacts from cache (including HTTP Range seeks), boot the controller with no
network at all, and surface the offline state in the play page, the songset list, and the render
flow.

## Architecture as implemented

### #203 — Offline index (`src/lib/offline/offline-index.ts`)

- IndexedDB `sow-offline-index`, object store `songsets`, `keyPath: "songsetId"`, version 1.
- Record shape: `songsetId, renderJobId, songsetName, cachedMp3, cachedMp4, cachedChapters,
  cachedAt, chapterContentHashes`. `chapterContentHashes[i]` is the position-aligned
  contentHash of songset item `i` (`null` when the item has no recording) — this is what detects
  a changed set behind an unchanged render.
- `putOfflineRecord` reads the prior record and, when `renderJobId` differs, invalidates the
  prior artifacts and deletes the prior controller document **before** opening the readwrite
  transaction (supersede eviction; a re-rendered set never leaves unreachable, undeletable cache
  entries).
- `removeOfflineSongset` invalidates artifacts, deletes the controller document, then deletes the
  record. Used by remove-from-offline **and** songset deletion (the record must not outlive the
  songset).
- All IndexedDB access goes through `withIndexDb`, which resolves `null`/empty on unavailability
  or any IDB failure (silent-failure convention).
- Shared download helper `downloadOfflineArtifacts` (`src/lib/offline/download-offline.ts`):
  `GET /api/offline/cache?renderJobId=…` → `cacheArtifacts` → `putOfflineRecord` →
  `cacheControllerDocument`, reporting percentage progress through `DownloadProgressCallback`.

### #204 — Service worker rewrite (`public/sw.js` + `public/sw-artifact-serving.js`)

- Single artifact route, extracted into `public/sw-artifact-serving.js`, mapping
  `/api/r2/artifact/<jobId>/<file>` onto cache keys
  `/sow-artifact-cache/<jobId>/{mp3,mp4,chapters}` inside cache `sow-artifacts`.
- Range serving: a `Range` request is answered by slicing the cached body into a 206 with correct
  `Content-Range`/`Accept-Ranges`. Cache **writes are full 200s only** — a `cache.put` of a 206
  would throw and poison the key. On a miss with a `Range` header, the handler refetches
  **without** the header so the stored response is a full 200.
- `clientsClaim()` so a new worker takes over without a reload; the module is cache-busted so
  module-only edits land.
- Document route: NetworkFirst over cache `sow-pages`, covering navigations and RSC payload
  fetches. `/api/songsets` NetworkFirst via `sow-api-songsets`; other API reads via
  `sow-api-songs`; static assets via `sow-static-assets`; `/api/signed-url` NetworkOnly (never
  cache a presigned URL).
- Catch handler: JSON 503 `{"error":"offline"}` for API reads, `Response.error()` for video
  requests, and a static offline HTML page ("You are offline. Please reconnect.") for uncached
  document navigations.
- Registration: `ServiceWorkerRegistrar` mounted in `src/app/layout.tsx`, calling
  `registerServiceWorker` (`src/lib/offline/precaching.ts`) with `updateViaCache: "none"` and
  skip-waiting.

### #205 — Controller offline boot (`src/app/songsets/[id]/play/controller/page.tsx`)

- `loadData` is now three-branch: (1) online → the unchanged four-fetch chain; (2) the chain
  fails while online → offline boot plus the `control.offlineFallback` toast ("The live version
  could not be loaded — playing the downloaded copy."); (3) offline at boot → silent offline
  boot.
- `resolveOfflinePlayback` (`src/lib/offline/offline-playback.ts`) returns the service-worker
  proxy URL for the cached artifact, falling back to a blob URL (`createOfflineBlobUrl`, revoked
  via `revokeOfflineBlobUrl`) when the proxy path cannot be used.
- Chapters boot independently of media, so the chapter list is usable even when the media source
  itself is failing.
- Media `error`/`stalled` handlers with a 15-second stall timer; the failure overlay
  (`controller.mediaFailed` / `controller.mediaStalled` plus `controller.mediaFailedOfflineDesc`)
  exposes `controller.retry`.
- Audio-only boot when the render has no video: playback falls back to the `audioSrc` prop
  (`OfflineMediaKind = "video" | "audio"`).
- Cast and second-screen Presentation are hidden whenever `isOfflineMedia` — the downloaded copy
  is local-only and cannot be projected.

### #206 — Offline entry (`src/components/play/*`, `src/app/layout.tsx`)

- `cacheControllerDocument` pre-caches the controller document into `sow-pages` and warms the
  hashed script/style subresources (best-effort, bounded by `WARM_TIMEOUT_MS = 15_000`).
- `OfflineAvailableCard` on the play page when the songset fetch fails offline — never on a 404,
  so a genuinely missing set still reads as missing.
- Offline Start Worship uses a full document navigation (`window.location.assign`) rather than a
  client-side transition, so the service worker's document route serves the cached page.
- `<OfflineIndicator />` mounted in the root layout renders the red "You are offline" banner.

### #207 — Songset list badge and removal (`src/app/songsets/SongsetsClient.tsx`, `SongsetRow.tsx`)

- `transformSongsetsWithOffline` merges `listOfflineRecords()` into every fetch, inside the fetch
  choke point, so refetch, search, and pagination all re-merge. Rows without an index record keep
  the plain transform's defaults.
- Staleness = the row's existing `isArtifactsStale` (renderState out of date) **or** the cached
  `renderJobId` no longer matching the songset's `latestRenderJobId`; stale rows tint the
  **Offline** badge amber and the row border amber.
- The row's ⋯ menu gains **Remove from offline** → `removeOfflineSongset` + local state update +
  `songsets.toast.offlineRemoved`.

### #208 — Auto-cache at render completion, and dead-option removal

- `RenderSubmitted` polls the render job every 10 s (via the DOM timer handle, per `ab398651`),
  and `RenderPageClient.handleRenderComplete` reads the `offlineAutoCache` setting once per
  submitted screen, then fire-and-forgets `downloadOfflineArtifacts`. Tab-contingent by design:
  there is no server-side completion hook, so auto-cache only fires if the render page stays open
  until the render finishes.
- Removed the dead `offlineEnabled` render-form card, field, defaults, and fixtures, along with
  the `render.offline.*` i18n keys.

### #210 — Review fixes (**uncommitted in the working tree**)

- `isLoginPage` guard in `cacheControllerDocument`: a redirected response that is actually the
  login page is never stored under the controller document path — an expired session can no
  longer cache login HTML as the offline controller.
- `deleteControllerDocument` invoked on both remove-from-offline and supersede, so a pre-cached
  controller document never outlives its record.
- A dedicated **unexpiring** service-worker route for `/songsets/<id>/play/controller`
  navigations, registered before the generic document route with redirect-drop, so conditions
  that evict the generic `sow-pages` entries do not take the offline controller with them.
- 416 with `Content-Range: bytes */<size>` for unsatisfiable ranges (media elements recover from
  416 instead of failing the fetch); unparsable Range headers degrade to the cached 200.
- Auth-expiry poll signal: `onAuthExpired` on `RenderSubmitted`, surfaced as
  `render.submitted.authExpired`.
- Branch-2 fallback toast fixed (the offline-fallback path now actually announces itself).
- Retry listener teardown with position restore (stall state cleared on progress; the playhead is
  preserved across Retry; blob URLs released).

## Cache and storage inventory

| Item | Name / value |
|---|---|
| Artifact cache | `sow-artifacts` (`ARTIFACT_CACHE_NAME`) |
| Artifact cache key | `/sow-artifact-cache/<renderJobId>/{mp3,mp4,chapters}` |
| Document cache | `sow-pages` (`SOW_PAGES_CACHE_NAME`) |
| Static assets | `sow-static-assets` |
| API reads | `sow-api-songs`, `sow-api-songsets` |
| Offline index DB | `sow-offline-index`, store `songsets`, keyPath `songsetId` |
| Storage warning | `WARN_STORAGE_BYTES` = 500 MB |
| Storage hard limit | `HARD_LIMIT_BYTES` = 1 GB |

## Verification state, honestly

- **Unit suites (green as of `ab398651` + the working tree):** `artifact-cache`,
  `artifact-cache-sw-parity`, `sw-artifact-serving`, `download-offline`, `document-cache`,
  `offline-index`, `offline-playback`, `precaching` (all under `src/test/lib/offline/`);
  `controller-page`, `play-page`, `songsets-offline-merge` (`src/test/app/`); `OfflineStatus`,
  `ControllerPlayer` (`src/test/components/play/`); `RenderSubmitted`, `RenderForm`
  (`src/test/components/render/`); plus the `ServiceWorkerRegistrar` coverage.
- **Browser proofs: not yet green.** `delivery/webapp/scripts/e2e/offline-playback.mjs` is
  **tracked but not yet green** (`a369abc3`, documented by `delivery/webapp/scripts/e2e/README.md`;
  the working tree carries further uncommitted changes to it). Its intended scenarios are (a)
  download producing artifact keys + index record + controller document, (b) offline cold start
  with a 206 seek, (c) mid-stream network drop, (d)
  the auto-cache toggle being honored, (e) an online regression pass (signed URL + Cast connect),
  (f) the expired-session login-HTML guard, (g) a 416 seek past EOF, (h) the pre-cached
  controller document surviving generic document-cache eviction. None of these has completed a
  green run, so the end-to-end offline behavior is **verified by unit tests plus code
  inspection, not yet by a browser run**.

## Known limitations

- Offline playback is **local-only**: Cast and second-screen projection are unavailable while
  playing a downloaded copy.
- Auto-cache is **tab-contingent** — the render page must stay open until the render completes.
- The share flow is out of scope for offline playback.
- iOS below 17.4 does not support offline caching.
- A downloaded copy is **per device and per browser**, never synced, capped at 1 GB total
  (500 MB warning).
- Routes never opened online on this device fall back to the static offline HTML page.

## Working-tree notice

The #210 review fixes are **committed** (`a369abc3`), together with this task's real-browser e2e
harness (`delivery/webapp/scripts/e2e/`, documented by its own `README.md`) and
`reports/handover-issue-210.md`. The committed branch tip is `c05f5af4` (a graphify chore commit),
so the branch as committed contains #203–#210.

The working tree still carries **uncommitted** changes from other in-flight webapp work — eight
files at the time of writing (`git status --porcelain`): `delivery/webapp/scripts/e2e/offline-playback.mjs`,
`src/app/page/HomePageClient.tsx`, `src/app/songsets/SongsetsClient.tsx`,
`src/app/songsets/[id]/SongsetEditorClient.tsx`, `src/app/songsets/[id]/play/controller/page.tsx`,
`src/components/play/ControllerPlayer.tsx`, `src/test/app/controller-page.test.tsx`, and
`src/test/components/play/ControllerPlayer.test.tsx`. Those changes belong to other work in
progress and are not described by this summary.