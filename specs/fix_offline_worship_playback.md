# Offline Worship Playback Remediation Plan

## Context

Offline Worship Playback currently has a working download half (PrePlayCard → `OfflineStatus` → `GET /api/offline/cache` → `cacheArtifacts()` stores mp3/mp4/chapters in Cache Storage `"sow-artifacts"` under stable keys `/sow-artifact-cache/<renderJobId>/{mp3,mp4,chapters}`) and a broken playback half: the controller (`src/app/songsets/[id]/play/controller/page.tsx:44-159`) requires four live, auth-gated API fetches (`/api/songsets/:id`, `/api/render-jobs/:id`, `/api/signed-url?cast=true`, `/api/r2/artifact/:id/chapters.json`) and a 4-hour R2 presigned URL as `<video src>`. Nothing reads the cached artifacts at playback time; the service worker (`public/sw.js`) is never registered (`registerServiceWorker` in `src/lib/offline/precaching.ts` has zero production callsites); and the SW routes mark `/api/signed-url` and `/api/r2/` NetworkOnly by design.

User-visible break: starting worship with network down or dropping mid-stream → controller page spins, then errors, or video plays a few seconds then stalls (no `error`/`stalled` listener on the `<video>` in `ControllerPlayer.tsx`).

End state: with artifacts downloaded for offline and the network unavailable, Worship Playback boots the controller, plays the cached MP4 with chapters and full controls; online behavior is unchanged. Plus the dead-wiring P1 fixes: auto-cache implemented, `offlineEnabled` removed, real offline badge, eviction/supersede, iOS gating made consistent.

Design decision (fixed): SW range-serving over blob URLs. A full worship MP4 is hundreds of MB (HARD_LIMIT_BYTES = 1 GB); blob URLs must hold the entire file in memory on phones. SW `CacheFirst` + Range keeps the existing `<video src>` shape, enables seeking in large MP4s, and preserves the current architecture. Cache keys stay `/sow-artifact-cache/<renderJobId>/{mp3,mp4,chapters}` (stable, non-expiring, already tested).

Approach in one paragraph: register the existing Workbox SW at app boot; rewrite `sw.js` to serve `/api/r2/artifact/*` from a client-managed CacheFirst strategy that maps proxy URLs → artifact cache keys and supports Range; make the controller page cache-first for boot data (an IndexedDB index `songsetId → {renderJobId, chapters}` written at download time) and for the video source (proxy URL when cached, presigned URL when online); keep all API paths byte-identical for the online path; remove/replace dead wiring per step. The share (`/share/[token]`) flow is explicitly OUT of scope — it is an anonymous one-off link and cannot own an offline cache.

### Architecture: how offline boot resolves renderJobId

The controller must learn `latestRenderJobId` + chapters + (for PrePlayCard) artifact keys without hitting `/api/songsets/:id` or `/api/render-jobs/:id`. A small IndexedDB store (simpler than localStorage for versioned object records; no existing IndexedDB wrapper exists in the repo — one-line justification: nothing equivalent exists) keyed by songset id is written at download time and read at controller boot:

```
DB: "sow-offline-index" (version 1)
  objectStore: "songsets" (keyPath: "songsetId")
  record: {
    songsetId: string,
    renderJobId: string,        // the job whose artifacts are cached
    mp3Cached: boolean,
    mp4Cached: boolean,
    chaptersCached: boolean,
    cachedAt: number            // epoch ms
  }
```

Read order at controller boot: IndexedDB index first; if present and cache matches exist → offline path. If absent → current online path unchanged.

### Cache key mapping

`OfflineStatus.checkCacheStatus` / `cacheArtifacts` already use stable keys `/sow-artifact-cache/<renderJobId>/{mp3,mp4,chapters}` (artifact-cache.ts:24-26). The SW route for `/api/r2/artifact/:jobId/:file` maps `request.url` → `${origin}/sow-artifact-cache/${jobId}/${{output.mp3:"mp3",output.mp4:"mp4",chapters.json:"chapters"}[file]}` and does `cache.match(mappedKey)`. Download fetches via `cacheArtifacts` go through the SW too; the SW must respond to its own downloads from network (CacheFirst with a "not yet cached" fallback to network).

## Approach

### Step 1 — Register the service worker at app boot

1. In `src/app/layout.tsx` add a client component `ServiceWorkerRegistrar` (new file `src/components/system/ServiceWorkerRegistrar.tsx`): `"use client"`, `useEffect(() => { void registerServiceWorker(); }, [])` on mount, renders `null`. Import `registerServiceWorker` from `@/lib/offline/precaching` (exists, tested, unchanged). Mount it inside `<body>` after `<Toaster />` (line 51), outside `LocaleProvider` — it needs no locale.

2. No manifest/PWA work — out of scope; SW alone provides the fetch interception the playback path needs.

### Step 2 — Rewrite `public/sw.js` for artifact range-serving + offline-fallback APIs

Replace the whole file. Keep Workbox CDN import (`workbox-cdn 7.0.0`, `setConfig({debug:false})`); drop `precacheAndRoute(self.__WB_MANIFEST || [])` entirely (it is always `[]` — dead code; no build-time injection exists).

Routes, in order:

1. **Artifact range-serving** (the core):
   ```js
   workbox.routing.registerRoute(
     ({ url }) => url.pathname.startsWith("/api/r2/artifact/"),
     new workbox.strategies.CacheFirst({
       cacheName: "sow-artifacts",
       plugins: [
         new workbox.rangeEx RangeRequestsPlugin(),  // actual name: workbox.rangeRequests.RangeRequestsPlugin
         new workbox.cacheableResponse.CacheableResponsePlugin({ statuses: [200, 206] }),
       ],
     })
   );
   ```
   Use `new workbox.rangeRequests.RangeRequestsPlugin()`. This strategy caches the full response on first fetch and serves 206 ranges from cache thereafter. Cache name MUST be `sow-artifacts` so the SW route and the client `ARTIFACT_CACHE_NAME` share one cache.

2. **URL→key mapping shim before the strategy runs**: Workbox routes cannot rewrite cache keys directly; instead register a custom handler (plain async function instead of a strategy object) for the artifact route:
   ```js
   const ARTIFACT_TYPE = { "output.mp3": "mp3", "output.mp4": "mp4", "chapters.json": "chapters" };
   async function artifactHandler({ request, event }) {
     const url = new URL(request.url);
     const [, , , , jobId, file] = url.pathname.split("/");  // /api/r2/artifact/<jobId>/<file>
     const type = ARTIFACT_TYPE[file];
     const cache = await caches.open("sow-artifacts");
     const mappedKey = `/sow-artifact-cache/${jobId}/${type}`;
     const cached = await cache.match(mappedKey, { ignoreSearch: true });
     if (cached) {
       // Range support: slice the cached body for 206 requests the way the
       // RangeRequestsPlugin would; simplest correct approach is to return
       // the cached 200 and let the video element seek via media fragments —
       // but Chrome requires 206 for range requests on <video>.
       // Implementation: read the full cached blob, apply the Range header
       // manually.
       return rangeResponseFrom(cached, request.headers.get("range"));
     }
     // Not cached → fetch from network and store under the mapped key.
     const response = await fetch(request);
     if (response && response.ok && request.method === "GET") {
       await cache.put(mappedKey, response.clone());
     }
     return response;
   }
   workbox.routing.registerRoute(
     ({ url }) => url.pathname.startsWith("/api/r2/artifact/"),
     artifactHandler
   );
   ```
   `rangeResponseFrom(response, rangeHeader)` (top-level function in sw.js): if no Range header → return the cached response as-is; else parse `bytes=start-end`, read `await response.clone().blob()`, slice with `blob.slice(start, end+1)`, return `new Response(slice, { status: 206, headers: { "Content-Type": response.headers.get("Content-Type"), "Content-Range": `bytes ${start}-${end}/${totalSize}`, "Content-Length": String(end-start+1), "Accept-Ranges": "bytes" } })`. When `end` is absent use `blob.size - 1`. Handle suffix ranges (`bytes=-N`) by slicing `blob.size-N` to end. Malformed/un satisfiable ranges → return cached 200 (degrades gracefully rather than erroring).

   Rationale for manual range instead of RangeRequestsPlugin: the plugin matches by `cache.match(request, {ignoreSearch:false})` on the full request URL with Range, but our stored keys are the mapped `/sow-artifact-cache/...` keys, not the request URLs — the plugin would miss. The manual shim keeps one canonical key space. Do NOT register RangeRequestsPlugin anywhere.

3. **Render-jobs offline fallback**: keep existing `NetworkFirst` pattern but for `/api/render-jobs` with `networkTimeoutSeconds: 4`, `cacheName: "sow-api-render-jobs"`, `CacheableResponsePlugin({ statuses: [0, 200] })`, `ExpirationPlugin({ maxEntries: 50, maxAgeSeconds: 7*24*60*60 })`. This lets an online-then-offline session still read job metadata (`mp4R2Key` presence, `chaptersR2Key`) mid-session if the page is reloaded. Existing `/api/songs` SWR and `/api/songsets` NetworkFirst routes stay byte-identical.

4. **Keep NetworkOnly for `/api/signed-url`** (unchanged; presigned URLs are ephemeral).

5. **Catch handler**: keep the existing JSON 503 + offline document fallbacks, and add: for `event.request.destination === "video"` return `Response.error()` (the `<video>` will fire `error` and the UI handles it).

Registration is only valid over HTTPS or localhost; the dev server's `--experimental-https` (AGENTS.md browser recipe) and production Vercel both satisfy this. No code path needed to special-case http; `registerServiceWorker` failure returns are swallowed (existing behavior).

### Step 3 — Offline boot index (IndexedDB) with typed helpers

New file `src/lib/offline/offline-index.ts`:

```ts
export interface OfflineIndexRecord {
  songsetId: string;
  renderJobId: string;
  mp3Cached: boolean;
  mp4Cached: boolean;
  chaptersCached: boolean;
  cachedAt: number;
}
export async function putOfflineRecord(record: OfflineIndexRecord): Promise<void>;
export async function getOfflineRecord(songsetId: string): Promise<OfflineIndexRecord | null>;
export async function deleteOfflineRecord(songsetId: string): Promise<void>;
export async function listOfflineRecords(): Promise<OfflineIndexRecord[]>;
```

Plain IndexedDB wrapper (promise-wrapped `indexedDB.open("sow-offline-index", 1)`, `onupgradeneeded` creates store `songsets` with `keyPath: "songsetId"`). All functions resolve gracefully (return `null` / `[]` / no-throw) when `indexedDB` is unavailable (private mode) — mirror the silent-failure convention used in `artifact-cache.ts` and `OfflineStatus` sessionStorage guards.

### Step 4 — Wire index writes into OfflineStatus download/cleanup

In `src/components/play/OfflineStatus.tsx`:

1. `handleDownloadOffline` (line 85): after `await cacheArtifacts(...)` succeeds (line 118-120) and before `setIsCached(true)`, write the index:
   ```ts
   await putOfflineRecord({
     songsetId, renderJobId, mp3Cached: !!proxyUrls.mp3Url, mp4Cached: !!proxyUrls.mp4Url, chaptersCached: !!proxyUrls.chaptersUrl, cachedAt: Date.now(),
   });
   ```
   Requires a new required prop `songsetId: string` on `OfflineStatusProps`. Update the single callsite `PrePlayCard.tsx:256-261` to pass `songsetId={songset.id}` (PrePlayCard already receives `songset` — verify prop exists at PrePlayCard.tsx:42-50, it does).

2. The mount-time `cleanupStaleEntries` effect (lines 46-60) currently deletes entries with `/songsets/` + `/renders/` in the URL (legacy keys, no longer produced). Change it to: also delete any legacy keys AND reconcile the index — for every index record, verify `cache.match` for the primary artifact still hits; if not, `deleteOfflineRecord(songsetId)`. Keep silent-catch semantics.

3. Freshness/supersede: add an effect keyed on `renderJobId` — when the download completes for a NEW renderJobId, no explicit old-job deletion here; instead, `SongsetsClient`/list handles superseding (Step 8). OfflineStatus itself remains single-job scoped. After a successful `putOfflineRecord`, if `invalidateArtifactCache` was previously used for this songset (Step 8), index is already consistent.

### Step 5 — Controller page cache-first boot

In `src/app/songsets/[id]/play/controller/page.tsx`, rework `loadData()` (lines 44-159) into three phases:

1. **Always: try offline first.**
   ```ts
   const offlineRecord = await getOfflineRecord(songsetId);
   if (offlineRecord) {
     const cache = await caches.open(ARTIFACT_CACHE_NAME); // import from "@/lib/offline/artifact-cache"
     const chaptersHit = offlineRecord.chaptersCached
       ? await cache.match(`/sow-artifact-cache/${offlineRecord.renderJobId}/chapters`)
       : null;
     const mediaHit = offlineRecord.mp4Cached
       ? await cache.match(`/sow-artifact-cache/${offlineRecord.renderJobId}/mp4`)
       : null;
     if (mediaHit) {
       // Offline/online cache-first: skip ALL four API fetches.
       setVideoUrl(`/api/r2/artifact/${offlineRecord.renderJobId}/output.mp4`);
       if (chaptersHit) { const manifest = normalizeChaptersManifest(await chaptersHit.json()); setChapters(manifest.chapters); }
       setSongset({ id: songsetId, name: offlineRecord.songsetName, renderState: "fresh", latestRenderJobId: offlineRecord.renderJobId });
       setIsLoading(false);
       return;
     }
   }
   ```
   Add `songsetName: string` to the index record (Step 3 schema gains `songsetName: string`) so the offline path can render the title without the songset API. Update the interface + all writes.
   Rationale for proxy URL as `videoSrc` when cached: the SW intercepts it and serves from `sow-artifacts` (Step 2). This keeps the `<video>` src shape identical in both modes and makes online-refresh of cache transparent.

2. **Online fallback (current logic, unchanged)**: if no offline record or no mp4 cache hit, run the existing 4-fetch chain verbatim (songset → render-jobs → signed-url → chapters proxy). No behavioral change when online.

3. **Error path**: keep as-is; a failed online chain with no offline record still shows the existing error screen.

Also gate the Cast/Presentation wiring on a new boolean `isOfflineMedia` (set true in the offline path): when true, skip `usePresentationSender` usage and render the player without Cast button (pass `castAvailability="unavailable"`, `isCastSupported={false}`) — offline, the TV cannot stream from R2 either; local playback only. `ControllerPlayer` already renders correctly for these props (diagnostic sheet branch, lines 908-926).

PrePlayCard (`src/app/songsets/[id]/play/page.tsx`) gains no offline boot: the Start Worship button still routes to the controller; the controller decides. But the page's `loadSongset` (line 63-125) should surface offline state: wrap the initial `fetch("/api/songsets/:id")` — on network failure (TypeError) when an offline index record exists for `songsetId`, render a minimal "offline available" variant of the card with a Start Worship button (new i18n key, Step 10) instead of the error screen. Implementation: catch in `loadSongset`, check `getOfflineRecord(songsetId)`, if hit `setOfflineStart(true)` and skip `setError`. Render: simple centered card with songset name from the record, `Start Worship` button → same `handleStartWorship` push, and the offline hint string.

### Step 6 — Video element failure UX

In `src/components/play/ControllerPlayer.tsx`:

1. Extend the video event effect (lines 330-382) with `error` and `stalled` handlers:
   - `handleError`: set new state `mediaError: "error"`; toast `t("controller.mediaFailed")`.
   - `handleStalled`: set `mediaError: "stalled"` (recovered on next `playing` event → clear both on `playing`).
2. Render an overlay when `mediaError` is set and `!isPresentationActive`: centered amber panel (same visual family as the `pendingResume` button, line 1102-1118) with `t("controller.mediaFailed")` (error) or `t("controller.bufferingActionable")` (stalled >15s reuses existing copy) and a `Retry` button that calls `video.load(); video.play().catch(...)` (mirrors `handlePlayPause` catch path).
3. No changes to Cast path: the effect's handlers early-return under `isPresentationActive` like the others.

### Step 7 — Implement offlineAutoCache at render completion

The render pipeline is: browser POST creates job → Lambda worker writes DB directly → no server-side "completed" hook touches the webapp client. The only reliable client hook is the render page seeing completion. Implement:

1. In `src/components/render/RenderSubmitted.tsx`, add a poll: `useEffect` when mounted with a new required prop `jobId: string`; every 10s `fetch("/api/render-jobs/" + jobId)`; on `status === "completed"` call `onComplete(job)` (new required prop); on `status === "failed"` call `onFail?.()`. Clean up interval on unmount and on terminal status.
2. In `RenderPageClient.tsx` pass `jobId={jobId}` and handlers: `onComplete` → `setScreenState` unchanged (RenderSubmitted is the only visible state today; there is no RenderComplete mount anywhere — verified). On completion:
   - `toast.success(t("render.toast.completed"))` (new key).
   - Read `settings.offlineAutoCache` — fetch `GET /api/settings` once on mount of the submitted screen (`jobId != null`), store in state.
   - If `offlineAutoCache` and the completed job has `mp3R2Key || mp4R2Key` and `isOfflineSupportedOnCurrentDevice()`: call the existing `cacheArtifacts` flow directly — reuse the logic by extracting `OfflineStatus`'s download body into a shared helper. New file `src/lib/offline/cache-download.ts`:
     ```ts
     export async function downloadAndCacheArtifacts(renderJobId: string): Promise<void> {
       const response = await fetch(`/api/offline/cache?renderJobId=${encodeURIComponent(renderJobId)}`);
       if (!response.ok) throw new Error(`offline cache URL fetch failed: ${response.status}`);
       const proxyUrls = await response.json();
       await cacheArtifacts(renderJobId, proxyUrls);
     }
     ```
     Refactor `OfflineStatus.handleDownloadOffline` (lines 85-131) to call it (keeps its toasts/progress by passing the same `onProgress` through — add optional `onProgress` param to the helper). Fire-and-forget the helper call with a `.then(toast success/failure)`; do not block the render-complete UI on a 500 MB download.
   - Also `putOfflineRecord(...)` after success (songsetId is available in RenderPageClient scope as the render page prop — pass it into `onComplete`).
3. The `offlineAutoCache` fetch result also applies to the PrePlay path: `OfflineStatus` remains the manual fallback; no auto-download there (avoids surprise 500 MB downloads on page visits).
4. iOS note in settings stays accurate (offlineAutoCache writes require the same Cache Storage support); no server change needed — settings API already round-trips the field (route.ts:53, 208-211).

### Step 8 — offlineEnabled checkbox: wire it in or remove it

Decision: remove the `offlineEnabled` checkbox from the render form. It is a no-op today (never submitted, no API field, no server column — the settings-side `offlineAutoCache` is the real switch). Keeping a second, non-functional offline toggle next to a real one is confusing.

1. `src/components/render/RenderForm.tsx`: delete the "Offline Availability" Card (lines ~400-440: Card, Checkbox, Label, Tooltip, Info icon block, cacheHint paragraph), the `offlineEnabled` field from `RenderFormData` (line 48), its `useState` init (line 140), and the now-unused `isIOS174OrLater` + `iosSupportsOffline` if no other consumer (grep within the file — the `showIOSNote` in SettingsForm is a separate implementation; remove only local uses).
2. `src/lib/render/render-defaults.ts`: delete `offlineEnabled: false` (line 14) from `DEFAULT_RENDER_CONFIG` and its `UserSettingsData`-like type if it is declared there (read the file; only remove the key).
3. `src/app/songsets/[id]/render/RenderPageClient.tsx`: no change (never referenced `offlineEnabled`).
4. Tests: delete the `offlineEnabled` assertions/fields in `src/test/components/render/RenderForm.test.tsx` (lines 165-167, 205-207, 224-226 fixtures) and `src/test/app/render-page.test.tsx` (lines 39-40, 74-75). Do NOT re-pin to new text — these fixtures only set a removed field.
5. i18n: delete the `render.offline.*` keys (render.ts lines 63-67 EN, 193-197 zh) — unused after removal. Keep `settings.offline.*` (still used).

### Step 9 — Real offline availability badge + management in the songset list

1. Compute offline availability client-side in `SongsetsClient.transformSongsets` cannot be async — instead: after the songsets fetch resolves (line 117), run `await listOfflineRecords()` and build a `Map<songsetId, {renderJobId, cachedAt}>`; merge into the transformed list as `isOfflineAvailable` + `offlineRenderJobId`. Re-run the merge whenever `songsets` state changes (single effect after `setSongsets`).
2. Staleness: a row's cached artifacts are stale when `songset.latestRenderJobId != null && offlineRecord.renderJobId !== songset.latestRenderJobId`. Render the existing "Offline" badge (`SongsetRow.tsx:242-247`) when `isOfflineAvailable` is true, and tint it amber (add `className={cn(..., isOfflineStale && "text-amber-600")}`) when stale.
3. Management affordance: extend `SongsetRow`'s existing dropdown menu (`isMenuOpen`, line 89) with a conditional "Remove from offline" item (only when `isOfflineAvailable`), new prop `onRemoveOffline?: () => void`, i18n key `songsets.menu.removeOffline`. Handler in `SongsetsClient`: `invalidateArtifactCache(offlineRecord.renderJobId)` + `deleteOfflineRecord(songsetId)` + local state update + `toast.info`.
4. `SongsetList` passes the two new optional fields through to `SongsetRow` (extend `Songset` interface at SongsetList.tsx:22-34 with `offlineStale?: boolean`; extend `SongsetRowProps` with `onRemoveOffline`).

### Step 10 — OfflineIndicator mount + i18n

1. Mount `<OfflineIndicator />` in `src/app/layout.tsx` inside `<body>`, first child (above `LocaleProvider` — it needs no locale context itself but `useLocale` inside requires the provider; place it inside the provider, before `Header`).
2. New i18n keys (both locales):
   - `control.ts` EN: `"control.offlineBooting": "Playing from offline cache"`, `"control.offlineUnavailable": "This worship set isn't downloaded for offline use"`, zh-Hant: `"control.offlineBooting": "正在從離線快取播放"`, `"control.offlineUnavailable": "這個敬拜歌單尚未下載離線內容"`.
   - `play.ts` EN/zh: `"controller.mediaFailed": "Playback failed — check your connection" / "播放失敗，請檢查網路"`, `"controller.retry": "Retry" / "重試"`.
   - `core.ts` EN/zh: `"render.toast.completed": "Render completed" / "渲染完成"`.
   - `songsets.ts` EN/zh: `"songsets.menu.removeOffline": "Remove from offline" / "從離線移除"`.
   - `audio.ts` (offline badge tint has no text; no new keys needed beyond existing).
3. PrePlay offline variant (Step 5) uses `control.offlineUnavailable` as the card body and reuses `preplay.startWorship` for the button.

### Step 11 — Audio-only renders

Controller still requires `mp4R2Key` (`control.noVideoForSongset`, line 104-106). Extend the offline path to fall back to mp3 when `mp4Cached` is false but `mp3Cached` is true:

1. In the offline branch (Step 5.1), if `mediaHit` (mp4) misses but `mp3Hit` exists: `setVideoUrl(null); setAudioUrl(`/api/r2/artifact/${renderJobId}/output.mp3`)` — new state + new optional prop `audioSrc` on `ControllerPlayer` that renders `<audio>` instead of `<video>` (same ref handling; PlaybackControls works unchanged — duration/time flow through the same handlers). Chapters render as before if `chaptersHit` exists; the projection screen stays local-only in this mode (no Cast for audio: same gating as Step 5's offline branch).
2. Online path unchanged: mp3-only jobs still error with `control.noVideoForSongset` (out of scope to redesign the online worship flow for audio-only).

## Critical files & anchors

- `public/sw.js` — full rewrite; artifact handler + rangeResponseFrom; delete precacheAndRoute.
- `src/app/songsets/[id]/play/controller/page.tsx:44-159` — loadData three-phase rework; `isOfflineMedia` state; mp3 fallback wiring.
- `src/components/play/ControllerPlayer.tsx:330-382` (video handlers), `:948-961` (`<video>`), `:1102-1118` (overlay pattern to mirror) — error/stalled UX, `audioSrc` prop.
- `src/components/play/OfflineStatus.tsx:85-131` — refactor onto `downloadAndCacheArtifacts`; index writes; new `songsetId` prop.
- `src/lib/offline/artifact-cache.ts` — unchanged except no changes needed; cache key constants reused (`ARTIFACT_CACHE_NAME`, key format documented at :24-26).
- `src/lib/offline/offline-index.ts` — new; IndexedDB wrapper.
- `src/components/render/RenderPageClient.tsx` + `RenderSubmitted.tsx` — completion polling + auto-cache trigger.
- `src/app/layout.tsx` — SW registrar + OfflineIndicator mounts.

## Verification

Prerequisites: `cd delivery/webapp && pnpm dev` (dev server on 8080, already possibly running — reuse, do not start a second). Test user creds via `SOW_WEBAPP_TESTUSER_LOGIN`/`SOW_WEBAPP_TESTUSER_PASSWORD` env vars; headless Chrome via hub with `--ignore-certificate-errors --remote-debugging-port=9222` per AGENTS.md browser recipe.

1. **Unit tests** (existing suites, must stay green): `pnpm test` — expect `OfflineStatus.test.tsx` (update for the extracted helper + new prop), `artifact-cache.test.ts` (unchanged behavior), `controller-page.test.tsx` (new offline-path tests added), `RenderForm.test.tsx` (offlineEnabled removal), `settings` tests untouched.
2. **New unit tests to add** (behavior, not plumbing):
   - `offline-index.test.ts`: put→get→list→delete round-trip on a fake-indexeddb-like in-memory mock (mirror `artifact-cache.test.ts`'s mock style; no new dependency).
   - `controller-page.test.tsx`: (a) with a populated offline index + `caches` mock holding `mp4`/`chapters`, the page renders `ControllerPlayer` with `videoSrc="/api/r2/artifact/<jobId>/output.mp4"` and zero fetch calls (assert `global.fetch` not called); (b) offline index with mp3-only → `audioSrc` prop set, no error; (c) no offline record → existing 4-fetch chain asserted (already covered — keep).
   - `sw.js` cannot be unit-tested with the Workbox CDN import; verify via the browser recipe instead.
3. **End-to-end offline proof (browser recipe)**: sign in → open a songset with a completed render → PrePlayCard → click `Download for offline` → wait for "Offline ready" badge → in `tab.run`, verify `caches.open("sow-artifacts")` contains keys for the jobId (assert via `await cache.keys()` and check `/sow-artifact-cache/<jobId>/mp4` present) and IndexedDB `sow-offline-index` has the record. Then kill the network (in `tab.run`: `page.setOfflineMode(true)`) → navigate to `/songsets/<id>/play/controller` → expect the player to boot with `videoSrc` proxy URL, `<video>` `readyState >= 2`, and playback progresses (`currentTime` advances over ~5s, assert via `page.evaluate` polling `video.currentTime`). Then seek to a later chapter and confirm `video.currentTime` moves (range serving works from cache).
4. **Mid-stream offline proof**: start playback online, let it play ~5s, `page.setOfflineMode(true)`, seek forward 30s → playback continues from the cached artifact (no stall overlay within 5s). This exercises the SW range path on an already-open media element.
5. **Auto-cache proof**: submit a render with `offlineAutoCache` default (true) → poll `GET /api/render-jobs/<id>` via `tab.run` until completed (or use the existing completed job) → assert `caches.open("sow-artifacts")` gains the new jobId's keys and `PUT /api/settings` toggle to false suppresses the behavior (verify by absence of new keys after a fresh render completion).
6. **Regression**: online playback path unchanged — signed-url still minted when no offline record; Cast still connects (manual check via diagnostic sheet presence, not full Cast session).
7. Push gate per AGENTS.md session completion (git pull --rebase, push).

Risky-step ties: Step 2's manual 206 range logic is the highest-risk piece — verify it first with `curl -H "Range: bytes=0-100"` against a cached URL through the SW in the e2e step 3 (inspect response status/content-range via `tab.run` fetch from a fresh tab-controlled page context, or assert via video seek in step 3's last clause).

## Assumptions & contingencies

- `workbox-cdn` (runtime CDN import in sw.js) stays — it already works for the routes pattern in the current sw.js and adding a build-time Workbox pipeline (webpack plugin) is a larger change with no benefit here. If the CDN import is blocked in some environment, the SW fails to import and behaves as no-SW — the controller's explicit cache-first path (Step 5) still works without the SW for booting; only range-seeking degrades (full-file `<video>` fetch from cache.match). Pre-decided fallback: if range serving proves unreliable in testing, switch the offline `videoSrc` to a `URL.createObjectURL(await cache.match(...).blob())` — one-line change in the controller, no other edits.
- Cache Storage availability on the test device is the same constraint OfflineStatus already enforces (iOS 17.4+); no new gating introduced for desktop.
- The `sow-artifacts` cache now holds large files fetched via SW — the existing 1 GB hard-limit check in `cacheArtifacts` still governs writes made through the helper; SW-cached writes during playback (the network-fallback branch of artifactHandler) bypass the limit by design (bounded by the same R2 files already counted).