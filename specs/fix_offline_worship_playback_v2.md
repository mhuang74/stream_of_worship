# Offline Worship Playback Remediation Plan v2

> Supersedes `specs/fix_offline_worship_playback.md` (v1). v1's architecture stands; this
> revision folds in the six P0 defects found in review, plus the P1/P2 corrections. The
> corresponding spec issue is [mhuang74/stream_of_worship#202], with tracer-bullet tickets
> [#203]–[#208] carrying native blocking edges.

## Context

Offline Worship Playback currently has a working download half (PrePlayCard → `OfflineStatus`
→ `GET /api/offline/cache` → `cacheArtifacts()` stores mp3/mp4/chapters in Cache Storage
`"sow-artifacts"` under stable keys `/sow-artifact-cache/<renderJobId>/{mp3,mp4,chapters}`)
and a broken playback half: the controller page requires four live, auth-gated API fetches
(`/api/songsets/:id`, `/api/render-jobs/:id`, `GET /api/signed-url?...cast=true`,
`/api/r2/artifact/:id/chapters.json`) and a 4-hour R2 presigned URL as `<video src>`. Nothing
reads the cached artifacts at playback time; the service worker (`public/sw.js`) is never
registered (`registerServiceWorker` has zero production callsites); and the SW routes mark
`/api/signed-url` and `/api/r2/` NetworkOnly by design.

User-visible break: starting worship with network down or dropping mid-stream → controller
page spins, then errors, or video plays a few seconds then stalls (no `error`/`stalled`
listener on the media element).

End state: with artifacts downloaded for offline and the network unavailable, Worship
Playback boots the controller and plays the cached MP4 (or audio-only MP3) with chapters and
full controls; online behavior is unchanged. Plus: real offline badge, supersede eviction,
deletion cleanup, auto-cache at render completion, `offlineEnabled` removal, cold-start and
warm-start offline entry.

Design decision (fixed): SW range-serving over proxy URLs. A full worship MP4 is hundreds of
MB (HARD_LIMIT_BYTES = 1 GB); blob URLs must hold the entire file in memory. SW cache-serving
+ manual Range keeps the existing `<video src>` shape, enables seeking in large MP4s, and
preserves the current architecture. Cache keys stay
`/sow-artifact-cache/<renderJobId>/{mp3,mp4,chapters}` (stable, non-expiring, already tested).

The share (`/share/[token]`) flow is explicitly OUT of scope — it is an anonymous one-off
link and cannot own an offline cache.

### Verified source anchors (v1 claims re-checked against the tree)

- Controller 4-fetch chain at the controller page; `signed-url` GET supports `cast=true`
  (shared-handler mints the 4-hour expiry for video); SW NetworkOnly for `/api/signed-url`
  and `/api/r2/`; dead `precacheAndRoute` (manifest always `[]`).
- Cache keys `/sow-artifact-cache/<jobId>/{mp3,mp4,chapters}` (artifact-cache constants);
  `ARTIFACT_CACHE_NAME = "sow-artifacts"`; 1 GB hard limit in `cacheArtifacts`.
- `offlineEnabled` is genuinely dead: checkbox exists in the render form but the render POST
  body omits it; defaults carry it; settings-side `offlineAutoCache` is the real switch
  (DB column, settings API, SettingsForm all round-trip it).
- Songset list plumbing partially exists: `Songset` interface already has
  `isOfflineAvailable` / `isArtifactsStale`; `transformSongsets` stubs `isOfflineAvailable`
  to `false`; the offline badge + WifiOff icon already render in SongsetRow.
- `registerServiceWorker` (workbox-window wrapper with skip-waiting on "waiting") exists,
  tested, uncalled. `OfflineIndicator` exists, tested, unmounted.
- The R2 proxy route forwards Range to R2 and returns 206 — confirming the SW network branch
  must never store partial responses.
- Lyrics Feedback hashes are built in the controller from `songsetData.items` (issue #194) —
  the offline path must persist them or the affordance silently dies.

## What changed from v1 (review findings)

The v1 plan contained six P0 defects. This revision's Steps fold them in:

1. **Step 2 of v1 contradicted itself** — it registered both a `CacheFirst` + RangeRequestsPlugin
   route AND the custom `artifactHandler`. Workbox matches routes in registration order, so
   the first route would win and the handler would never run; worse, CacheFirst stores under
   the request URL, not the mapped keys, so offline lookups would miss everything. v2 has
   exactly one artifact route (Step 2).
2. **No SW-control guarantee** — layout-mount registration leaves the download session's own
   document uncontrolled; `router.push` never reloads the document, so every page fetch
   bypasses the SW in the very session that downloaded. Fix: `clientsClaim()` in the rewrite
   plus a first-class blob-URL fallback (promoted from v1's footnote contingency).
3. **Cache-first regardless of connectivity** — v1's Step 5 booted from cache whenever an
   index record existed, online or not, silently playing stale artifacts after a re-render
   and hiding Cast for the downloaded-but-online leader. Fix: connectivity-aware boot
   (Step 5).
4. **206 cache-poisoning / Cache.put throw** — v1's network fallback fetched with the Range
   header and stored the (partial) 206. Cache.put rejects 206s (TypeError per the Cache API
   spec), so the put throws and the seek dead-ends in the SW catch handler; even if stored,
   a partial body would poison the full-file key. Fix: put only full 200s; miss-with-Range
   refetches sans Range (Step 2).
5. **No document route / RSC coverage → cold-start offline dead-ends** — with the SW
   controlling documents, an offline navigation matched no route and fell into the catch
   handler's static "You are offline" HTML; the controller's cache-first boot never ran. And
   SPA navigation to a never-visited controller URL needs RSC payload fetches that
   `request.mode === "navigate"` doesn't cover. Fix: document route (navigate + RSC-aware) +
   download-time document pre-cache + full-document navigation for the offline Start Worship
   tap (Step 4).
6. **Supersede eviction missing** — after re-render + re-download, the index record holds the
   new jobId, so the old job's artifacts were unreachable AND undeletable through any UI (the
   list menu deletes via the record's renderJobId). Fix: `putOfflineRecord` invalidates the
   prior renderJobId before writing (Step 4 of v1's Step-4 region, now in Step 1).

P1/P2 corrections also folded in: `stalled` overlay only after a 15-second timer (transient
stalls on healthy networks must not flash overlays); offline-appropriate stall copy (the
existing buffering copy is TV/Cast-specific); Lyrics Feedback hashes persisted in the index
record; songset-deletion cleanup; per-artifact error isolation in offline boot (a chapters
parse failure never blocks media); auto-cache documented as tab-contingent; `render.toast.completed`
lands in the render bundle (not core); the list reuses the existing staleness field rather
than adding a parallel one; range-serving memory framing corrected (Chromium Cache Storage
bodies are disk-backed Blobs — `slice()` is a lazy view streaming only requested bytes, so
verify seek latency in e2e rather than worry about OOM).

## Approach

### Step 1 — Offline index: download-time writes, supersede eviction, deletion cleanup

New file `src/lib/offline/offline-index.ts` — plain IndexedDB wrapper, promise-wrapped:

```
DB: "sow-offline-index" (version 1)
  objectStore: "songsets" (keyPath: "songsetId")
  record: {
    songsetId: string,
    renderJobId: string,        // the job whose artifacts are cached
    songsetName: string,        // so the offline path renders the title without the API
    mp3Cached: boolean,
    mp4Cached: boolean,
    chaptersCached: boolean,
    cachedAt: number,           // epoch ms
    chapterRecordingHashes: (string | null)[]  // Lyrics Feedback, position-aligned
  }
```

Exports: `putOfflineRecord`, `getOfflineRecord`, `deleteOfflineRecord`,
`listOfflineRecords` — all resolve gracefully (return null/[], no-throw) when IndexedDB is
unavailable, mirroring the silent-failure convention in artifact-cache/OfflineStatus.

**Supersede eviction (P0-6):** `putOfflineRecord` reads any prior record for the songset;
if its renderJobId differs from the incoming one, call `invalidateArtifactCache(prior.renderJobId)`
before writing the new record. Without this, superseded artifacts are unreachable and
undeletable by user action.

**Deletion cleanup:** hook the songset-delete flow so deleting a songset also calls
`deleteOfflineRecord(songsetId)` + `invalidateArtifactCache(record.renderJobId)`.

**Wire into download:** in `OfflineStatus`, after `cacheArtifacts` succeeds and before the
cached badge state, write the index (songsetId becomes a new required prop; the single
callsite in PrePlayCard already has it). Refactor the download body onto a shared helper
(`downloadAndCacheArtifacts(renderJobId, onProgress?)`) so auto-cache (Step 6) reuses it.

**Mount-time reconcile:** the existing cleanup effect extends to reconcile the index — for
every record, verify a primary artifact still cache-matches; if not, delete the record.
Legacy `/songsets/`+`/renders/` key cleanup stays.

### Step 2 — Service worker rewrite: artifact range-serving + registration (tracer bullet)

Register the existing Workbox SW at app boot (new client component in the root layout, mounted
inside `<body>`; `registerServiceWorker` unchanged). Rewrite `public/sw.js` entirely:

- Keep the Workbox CDN import (`setConfig({debug:false})`); **delete** `precacheAndRoute` —
  it is dead code (manifest always `[]`).
- **`clientsClaim()`** so the download session's own document is SW-controlled (P0-2).

Routes, in order:

1. **Artifact route — exactly ONE** (P0-1: do not register a second CacheFirst route; do NOT
   register RangeRequestsPlugin anywhere — it matches full request URLs, but our stored keys
   are the mapped `/sow-artifact-cache/...` keys, so it would miss):

   ```js
   const ARTIFACT_TYPE = { "output.mp3": "mp3", "output.mp4": "mp4", "chapters.json": "chapters" };
   async function artifactHandler({ request, event }) {
     const url = new URL(request.url);
     const [, , , , jobId, file] = url.pathname.split("/"); // /api/r2/artifact/<jobId>/<file>
     const type = ARTIFACT_TYPE[file];
     const cache = await caches.open("sow-artifacts");
     const mappedKey = `/sow-artifact-cache/${jobId}/${type}`;
     const cached = await cache.match(mappedKey, { ignoreSearch: true });
     if (cached) return rangeResponseFrom(cached, request.headers.get("range"));

     // Miss: refetch WITHOUT Range so we store a full 200 (P0-4 — a 206 must never be
     // stored: Cache.put throws on 206, and a partial body would poison the key).
     const range = request.headers.get("range");
     const storeRequest = range ? new Request(request, { headers: sansRange(request.headers) }) : request;
     const response = await fetch(storeRequest);
     if (response && response.status === 200 && request.method === "GET") {
       await cache.put(mappedKey, response.clone());
       // Serve the requested slice from the fresh full body.
       return range ? rangeResponseFrom(response, range) : response;
     }
     return response;
   }
   ```

2. **`rangeResponseFrom(cached, rangeHeader)`**: no header → return the cached response
   as-is; else parse `bytes=start-end` / `bytes=-N` suffix, `await cached.clone().blob()`,
   `blob.slice(start, end+1)`, return a 206 with `Content-Range`, `Content-Length`,
   `Accept-Ranges: bytes`, and the stored Content-Type. Unsatisfiable/malformed → return the
   cached 200 (graceful degrade, never error). Memory note: Chromium Cache Storage bodies are
   disk-backed Blobs; `slice()` is lazy and streams only the requested bytes — verify seek
   latency in the e2e, memory is not the risk.

3. **Document route (P0-5):** `request.mode === "navigate"` → NetworkFirst, cacheName
   `sow-pages`, `CacheableResponsePlugin({ statuses: [0, 200] })`, expiration plugin. Extend
   the match to RSC payload fetches (`request.headers.get("RSC") === "1"`, destination
   "empty") so offline warm navigations of already-visited routes hit cache. Do NOT
   pre-cache RSC payloads at download time (runtime `_rsc` hashes won't match; warmed
   sessions cover repeat navigation).

4. **Render-jobs offline fallback:** NetworkFirst for `/api/render-jobs`,
   `networkTimeoutSeconds: 4`, cacheName `sow-api-render-jobs`, statuses [0,200],
   expiration (50 entries, 7 days). Lets an online-then-offline session still read job
   metadata if the page reloads mid-session. Existing `/api/songs` SWR and `/api/songsets`
   NetworkFirst stay byte-identical.

5. **Keep NetworkOnly for `/api/signed-url`** (presigned URLs are ephemeral).

6. **Catch handler:** keep the JSON 503 + offline document fallbacks; add: video destination
   → `Response.error()` so the element fires `error` and the UI handles it (Step 3).

Registration is only valid over HTTPS or localhost; the dev server's `--experimental-https`
and production both satisfy this. Registration failure returns are swallowed (existing
behavior).

### Step 3 — Controller offline boot + media failure UX + audio-only fallback

Rework the controller page's `loadData()` into a **connectivity-aware three-branch state
machine** (P0-3, merging v1's stale-boot + lost-Cast findings):

1. **Online (`navigator.onLine === true`)** → run the existing 4-fetch chain verbatim:
   songset → render-jobs → signed-url (`cast=true`) → chapters proxy. Freshness and Cast
   preserved for the downloaded-but-online leader; no silent stale playback, ever.
2. **Chain fetch fails while nominally online** (mid-session drop; TypeError from a dead
   fetch) → fall back to the offline index: read the record, verify cache hits
   (`chapters`/`mp4`/`mp3` keys), boot cache-first with the proxy URL as media source,
   `isOfflineMedia: true` (Cast hidden — genuinely offline), offline hint rendered.
3. **Offline at boot (`navigator.onLine === false`)** → cache-first directly, no hanging
   fetches.

Because `isOfflineMedia` is only ever true in branches 2/3, Cast gating on it is correct as
written (a downloaded-but-online leader keeps Cast).

**Blob fallback (P0-2, first-class):** when `!navigator.serviceWorker.controller` (or any
proxy-URL media fetch fails), fall back to `URL.createObjectURL(await cache.match(mp4Key).blob())`
— one-line shape change in the controller, no other edits.

**Offline boot specifics:** skip ALL four API fetches; `setVideoUrl(proxyUrl)` (SW intercepts);
chapters from the cached manifest; songset stub `{ id, name: record.songsetName, renderState:
"fresh", latestRenderJobId: record.renderJobId }`; `chapterRecordingHashes` from
`record.chapterRecordingHashes` (Lyrics Feedback survives offline). Chapters boot
independently of media — a chapters parse failure must never block media boot (per-artifact
error isolation).

**Media failure UX:** extend the media event effect with `error`/`stalled` handlers:
`error` → overlay + toast; `stalled` starts a **15-second timer** (cleared on
`playing`/`progress`) — transient stalls on healthy networks must not flash overlays. Overlay
render: centered amber panel in the same visual family as the tap-to-resume button, with
offline-appropriate copy (the existing buffering copy is TV/Cast-specific — new key) and a
Retry button (`video.load(); video.play().catch(...)` mirroring the existing catch path). No
Cast-path changes; handlers early-return under `isPresentationActive` like the others.

**Audio-only renders:** in the offline branches, if `mp4` misses but `mp3` hits: audio-only
boot — new optional `audioSrc` prop rendering `<audio>` instead of `<video>` (same ref
handling; duration/time flow through the same handlers). Chapters render as before;
projection stays local-only in this mode. Online mp3-only sets keep the existing error
(unchanged).

### Step 4 — Offline entry: cold start, navigation, PrePlay offline variant

The full offline journey from airplane mode (P0-5):

1. Download-time document pre-cache: during the Step 1 download flow, fetch
   `/songsets/<id>/play/controller` once and `cache.put` it into `sow-pages` — the offline
   Start Worship target pre-exists in the SW document cache.
2. Play page offline variant: wrap the initial songset fetch — on network failure
   (TypeError) when an offline index record exists, render a minimal "offline available"
   variant of the card (songset name from the record, Start Worship button, offline hint)
   instead of the error screen.
3. **Offline Start Worship performs a full document navigation**
   (`window.location.assign`) rather than SPA `router.push` — the deterministic tap path:
   SPA navigation to a never-visited controller URL needs RSC fetches that cannot be
   pre-cached reliably; a full navigation is served directly by the SW document route.
4. Mount `<OfflineIndicator />` in the root layout (inside the locale provider, before the
   header) — the offline banner.
5. Offline boot hint (`control.offlineBooting`) rendered as the controller's offline hint or
   dropped — don't define unused keys.

### Step 5 — List offline badge + remove-from-offline

1. Compute offline availability client-side in `SongsetsClient`: after the songsets fetch
   resolves, `await listOfflineRecords()` and build a `Map<songsetId, {renderJobId, cachedAt}>`;
   merge into the transformed list as `isOfflineAvailable` + staleness. Re-run the merge
   whenever the songsets state changes (single effect after set). `transformSongsets` itself
   stays sync.
2. Staleness: a row is stale when `songset.latestRenderJobId != null &&
   offlineRecord.renderJobId !== songset.latestRenderJobId`. Render the existing offline badge
   when available, amber-tinted when stale — **reuse the existing `isOfflineAvailable` /
   `isArtifactsStale` fields; no parallel `offlineStale` field**.
3. Management: extend the row's existing dropdown menu with a conditional "Remove from
   offline" item (only when offline-available). Handler: `invalidateArtifactCache(record.renderJobId)`
   + `deleteOfflineRecord(songsetId)` + local state update + toast.
4. i18n: `songsets.menu.removeOffline` (EN + zh-Hant) in the songsets bundle.

### Step 6 — Auto-cache at render completion + offlineEnabled removal

The render pipeline has no server-side completion hook touching the webapp (browser POST
creates the job; Lambda worker writes DB directly); the only reliable client hook is the
render page seeing completion.

1. `RenderSubmitted` gains a poll: every 10s fetch the job; on `completed` → completion
   callback + cleanup; on `failed` → failure callback; clean up on unmount/terminal.
2. On completion: render-completed toast; read the settings once (`GET /api/settings`) on
   mount of the submitted screen; if `offlineAutoCache` and the job has artifacts and the
   device supports offline: call `downloadAndCacheArtifacts(renderJobId)` (Step 1 helper)
   fire-and-forget with a then/toast — never block the UI on a 500 MB download. Also write
   the index record (songsetId is available in the render page scope). Auto-cache is
   **tab-contingent** by design (no server hook); accepted and documented; reopening the
   render page with a still-running job recovers polling.
3. The PrePlay path keeps manual download as the fallback; no auto-download there (avoids
   surprise 500 MB downloads on page visits).
4. **offlineEnabled removal:** delete the "Offline Availability" card from the render form
   (Card, Checkbox, Label, Tooltip, Info block, cacheHint paragraph), the field from form
   data, its useState init, and the now-unused local iOS helpers if no other consumer.
   Delete `offlineEnabled: false` from render defaults. Delete the
   `offlineEnabled` assertions/fields in the render-form/render-page test fixtures (do NOT
   re-pin to new text). Delete the `render.offline.*` i18n keys (EN + zh); keep
   `settings.offline.*`. `render.toast.completed` lands in the **render bundle** (the
   `render.toast.*` namespace lives there; not core).

### Step 7 — i18n keys (ride their feature tickets)

- `control.*` (control bundle): `offlineBooting` (offline hint; render it in Step 3 or drop
  the key), `offlineUnavailable` (PrePlay offline variant body, Step 4).
- `controller.*` (play bundle — this namespace exists there): `mediaFailed`, `retry`, plus a
  distinct offline-appropriate stall message.
- `render.toast.completed` (render bundle, Step 6).
- `songsets.menu.removeOffline` (songsets bundle, Step 5).
- Both EN and zh-Hant for every key. The offline badge tint has no text (no new keys).

## Critical files & anchors

- `public/sw.js` — full rewrite; single artifact route + rangeResponseFrom + document route
  (navigate + RSC-aware) + clientsClaim; delete precacheAndRoute.
- Controller page `loadData` — connectivity-aware three-branch rework; `isOfflineMedia`
  state; blob fallback; mp3 fallback wiring.
- `ControllerPlayer` media event effect (~330-382), media element (~948-961), tap-to-resume
  overlay pattern (~1102-1118) — error/stalled UX, `audioSrc` prop.
- `OfflineStatus` download handler (~85-131) — refactor onto the shared helper; index writes;
  new `songsetId` prop; mount reconcile.
- `src/lib/offline/artifact-cache.ts` — constants reused (`ARTIFACT_CACHE_NAME`, key format);
  `invalidateArtifactCache` reused for supersede/deletion.
- `src/lib/offline/offline-index.ts` — new; IndexedDB wrapper with supersede eviction.
- `RenderPageClient` + `RenderSubmitted` — completion polling + auto-cache trigger.
- Root layout — SW registrar + OfflineIndicator mounts.
- `SongsetsClient` / `SongsetList` / `SongsetRow` — list merge, badge tint, menu item.

## Verification

Prerequisites: `cd delivery/webapp && pnpm dev` (dev server on 8080, already possibly running
— reuse, do not start a second). Test user creds via `SOW_WEBAPP_TESTUSER_LOGIN`/
`SOW_WEBAPP_TESTUSER_PASSWORD` env vars; headless Chrome via hub with
`--ignore-certificate-errors --remote-debugging-port=9222` per AGENTS.md browser recipe.

1. **Unit tests** (existing suites, must stay green): `pnpm test` — OfflineStatus (update for
   the extracted helper + new prop), artifact-cache (unchanged behavior), controller-page
   (new offline-path tests), RenderForm (offlineEnabled removal), settings tests untouched.
2. **New unit tests to add** (behavior, not plumbing):
   - offline-index suite: put→get→list→delete round-trip, supersede eviction (old job's keys
     deleted), graceful degradation on a mock mirroring artifact-cache.test's style (no new
     dependency).
   - controller-page suite: (a) with a populated offline index + caches mock holding
     mp4/chapters while `navigator.onLine === false` — renders the player with the proxy URL
     and zero fetch calls; (b) online with no record — existing 4-fetch chain asserted
     (already covered — keep); (c) chain-failure-falls-back-to-offline branch; (d) mp3-only
     record → audioSrc prop set, no error.
   - `sw.js` cannot be unit-tested with the Workbox CDN import; verify via the browser recipe.
3. **Download proof (browser)**: sign in → songset with a completed render → PrePlayCard →
   Download for offline → wait for the cached badge → verify `caches.open("sow-artifacts")`
   keys for the jobId (`/sow-artifact-cache/<jobId>/mp4` present) and the IndexedDB
   `sow-offline-index` record (including `chapterRecordingHashes` and `songsetName`). Also
   verify the controller document landed in `sow-pages`.
4. **Range proof (highest-risk piece — verify first)**: SW-controlled page, `tab.run` fetch of
   `/api/r2/artifact/<jobId>/output.mp4` with `Range: bytes=0-100` → expect 206 + correct
   Content-Range from cache; and a miss-with-Range case (fresh jobId) → full fetch stored,
   slice served, cache now holds the full 200.
5. **Cold-start offline proof**: `page.setOfflineMode(true)` → navigate to the controller URL
   → player boots with the proxy URL, readyState >= 2, currentTime advances over ~5s; seek to
   a later chapter → currentTime moves (range serving from cache).
6. **Warm-start tap proof**: offline, from the play page tap Start Worship → full document
   navigation boots the controller (the window.location.assign path).
7. **Mid-stream offline proof** (downloaded fixture only): play online ~5s, `setOfflineMode(true)`,
   seek forward 30s → playback continues from cache (no overlay within 5s). For a set that was
   never downloaded, a mid-stream drop degrades to the failure overlay — acceptable, scope the
   fixture accordingly.
8. **Auto-cache proof**: render with the setting default-on → after completion the new jobId's
   keys appear; toggle the setting off → a fresh render completion gains no new keys.
9. **Regression**: online path unchanged — signed-url still minted when no offline record;
   Cast still connects (manual check via diagnostic sheet presence); downloaded-but-online
   keeps Cast available.
10. Push gate per AGENTS.md session completion (git pull --rebase, push).

## Assumptions & contingencies

- `workbox-cdn` (runtime CDN import in sw.js) stays — it already works for the routes pattern
  and a build-time Workbox pipeline is a larger change with no benefit here. If the CDN import
  is blocked in some environment, the SW fails to import and behaves as no-SW — the
  controller's explicit cache-first path (Step 3) still boots without the SW for media via the
  blob fallback; only SW-mediated serving degrades.
- Cache Storage availability on the test device is the same constraint OfflineStatus already
  enforces (iOS 17.4+); no new gating introduced for desktop.
- The artifact cache holds large files fetched via SW — the existing 1 GB hard-limit check
  governs writes made through the helper; SW-cached writes during playback (the network
  branch) bypass the limit by design (bounded by the same R2 files already counted).
- Double-put on downloads (SW network branch stores under the mapped key AND cacheArtifacts
  puts the same key) is harmless duplication — acceptable; skip if trivial.
- Auto-cache is tab-contingent (no server-side completion hook); revisit-with-running-job
  recovers it. Documented, accepted.
- Offline cold start for routes never visited online depends on the pre-cached controller
  document; other pages fall back to the catch handler's offline HTML — acceptable, the
  worship journey is the controller.