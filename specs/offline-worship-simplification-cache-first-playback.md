# Plan: Offline Worship simplification — cache-first playback + /offline list (issue #211 follow-up)

## Context

After #211, offline UX is unstable: Play screen's Start Worship gray-outs (fetch failure leaves `renderJob` null → `disabled={!hasRenderArtifacts}`), three different offline back-nav outcomes (SPA RSC dead-end, "Failed to load songsets", SW fallback HTML "You are offline. Please reconnect."), and a global banner that flashes on cold start. User decision (grilling rounds 1–3): bypass Songset List/Detail/Play indirection on the playback path; make playback **cache-first, not connectivity-first**; add a dedicated `/offline` list rendered purely from the IndexedDB offline index. Deliverables: (1) new `/offline` route with Update/remove actions and offline redirect targets, (2) cache-first controller boot, (3) all entry points navigate straight to `/play/controller`, (4) delete the Play screen (`/songsets/[id]/play`) — share links never used it (share flow = `/share/<token>` → `/share/<token>/play/controller`, untouched), (5) offline back/exit returns to `/offline` when booted from there. Connectivity machinery (`useConnectivity`, `OfflineIndicator`, probes) stays for online-dependent surfaces; offline worship path never reads it.

Reference findings: `local://offline-ux-findings.md`, `local://play-removal-findings.md` (both in session `local://` store; if unavailable at execution time, re-derive from the files named below).

## Approach

All paths relative to `delivery/webapp/`. Steps 1–3 are independent of each other; step 4 depends on 1–3; step 5 depends on 4.

### Step 1 — Cache-first controller boot (`src/app/songsets/[id]/play/controller/page.tsx`)

In `loadData` (lines 231–261), replace branch 1 (`getConnectivity() === "offline"`) with: first `await getOfflineRecord(songsetId)`; if a record exists **and** `resolveOfflinePlayback(songsetId)` returns non-null → `loadOffline()` directly (works online and offline; implements "cached ⇒ always offline copy"). If resolveOfflinePlayback returns null despite a record (corrupt/missing bytes) → fall through to online logic when not OS-offline; when OS-offline → throw `t("control.offlineUnavailable")` (existing key). Remaining branches unchanged: OS-offline with no record → same error; online chain with its existing `probeConnectivity()` + `loadOffline()` fallback (now unreachable when cached, still guards non-cached sets).

Update the boot-hint condition (lines 461–471) to use the same "definitively offline" signal (`offlineNow` stays `connectivity === "offline"`); no change needed beyond keeping it consistent.

Update `src/test/app/controller-page.test.tsx` line ~712 ("runs the online chain when online, even with a downloaded copy"): invert to "boots offline copy when cached, even when online"; add: record exists but bytes missing while online → online chain; record exists, bytes missing, OS-offline → `control.offlineUnavailable`.

### Step 2 — Entry points navigate straight to `/play/controller`

Change all four list-entry handlers, swapping target `/play` → `/play/controller` in **both** branches (offline full-document `window.location.assign` — SW serves the pre-cached controller document; online `router.push`). Additionally: when `getOfflineRecord(songsetId)` (or the already-merged `isOfflineAvailable` row flag) is truthy, use `window.location.assign` **even when online** — cached ⇒ offline boot per step 1, deterministic regardless of connectivity:

- `src/app/page/HomePageClient.tsx:108-120` `handleSongsetPlay` (uses `useConnectivity` today; keep hook only for the non-cached offline branch).
- `src/app/songsets/SongsetsClient.tsx:246-252` `handlePlay` — row already has `isOfflineAvailable` from `transformSongsetsWithOffline`; key the cached branch off that flag (already computed from the index), not a new index read.
- `src/app/songsets/[id]/SongsetEditorClient.tsx:338-348` `handlePlay` — same pattern; this is the Detail page's Start Worship (transitions customization explicitly out of scope).

Controller exit paths:
- `src/components/play/ControllerPlayer.tsx:735`: `exitRoute ?? \`/songsets/${playerId}/play\`` — keep the default fallback but the songset controller page (next bullet) now always passes an explicit `exitRoute`, so the `/play` default becomes dead for songsets; leave the default line as-is only if the share controller still relies on explicit `exitRoute` (it passes its own — verify; if truly unused, change default to `/songsets`).
- `src/app/songsets/[id]/play/controller/page.tsx`: pass `exitRoute` to `ControllerPlayer` — **when booted via the SW controller document offline** the page cannot know the origin, so pass `exitRoute="/offline"` (Q5 decision: back → offline list). Simplest consistent rule: always `exitRoute="/offline"` for the songset controller; share controller keeps its own `/share/<token>`.
- `src/app/songsets/[id]/play/controller/page.tsx:481` error-screen go-back button: target `/offline` (same rule).

Test updates: `src/test/app/songsets-offline-merge.test.tsx` lines 259–278 (`locationAssign` target → `/songsets/songset-1/play/controller`); `src/test/app/home/HomePageClient.test.tsx` lines 152–180 (targets → `/play/controller`); `src/test/components/play/ControllerPlayer.test.tsx` — add exit-uses-`exitRoute` pin if the default was changed.

### Step 3 — New `/offline` route (list, always boots from IndexedDB)

Files to create: `src/app/offline/page.tsx` (thin authenticated server wrapper rendering the client component, mirroring `src/app/songsets/page.tsx` pattern) and `src/app/offline/OfflineClient.tsx`:

- On mount: `listOfflineRecords()` (`src/lib/offline/offline-index.ts:101`) → render rows `{songsetId, songsetName, cachedAt, renderJobId}`. **No network fetch ever** — the page always boots offline.
- Row tap → `window.location.assign(`/songsets/${songsetId}/play/controller`)` (SW serves pre-cached controller doc; cache-first boot per step 1).
- Per-record byte verification for the inert-tap case (Q6): lightweight — if `resolveOfflinePlayback` is too heavy for list render, verify via `matchCachedArtifact(record.renderJobId, record.cachedMp4 ? "mp4" : "mp3")` (`src/lib/offline/offline-playback.ts`); entries returning null render with a "needs re-download" hint (`offline.needsRedownload` key) and a no-op tap (inert while offline). This check is async post-render; rows start tappable and downgrade to inert on verification failure.
- Stale hint + Update action (Q3c/Q10, online only): a `useConnectivity() === "online"` section fetches `/api/songsets?` (or per-id `/api/songsets/${id}`) once to get `latestRenderJobId`; `record.renderJobId !== latestRenderJobId` → "Update available" hint (`offline.updateAvailable`) + per-row **Update** button calling `downloadOfflineArtifacts({songsetId, songsetName, renderJobId: latestRenderJobId}, onProgress)` (`src/lib/offline/download-offline.ts:70`) with the exact caller pattern from `OfflineStatus.handleDownloadOffline` (`src/components/play/OfflineStatus.tsx:84-113`): success toast `audio.offline.downloaded`, `NoArtifactsError` → `audio.offline.noArtifacts`, else `audio.offline.downloadFailed`; per-row progress percent while downloading. Offline (or fetch failure): skip the comparison section entirely — list shows plain "Offline ready" rows.
- Row menu also offers **Remove from offline** → `removeOfflineSongset(songsetId)` (`offline-index.ts:150`), reusing `SongsetsClient.handleRemoveOffline` toast contract.
- Empty state (Q12): message `offline.empty` ("Nothing downloaded yet… open a songset while online and tap Download for offline") + link to `/songsets`.
- i18n: new bundle file `src/lib/i18n/messages/offline.ts` (EN + zh-Hant), registered in `src/lib/i18n/messages.ts:126-140`; `bundle()` enforces key parity. Keys at minimum: `offline.title`, `offline.empty`, `offline.emptyLink`, `offline.ready`, `offline.needsRedownload`, `offline.updateAvailable`, `offline.update`, `offline.remove`, `offline.play`.
- Navigation entry: `src/components/layout/BottomNav.tsx:19-23` add `{ href: "/offline", key: "nav.offline" }` (i18n key in `core.ts` EN `:11-18` + zh-Hant `:285-292`); `src/components/layout/Header.tsx:40-61` add desktop link.

### Step 4 — Offline redirect guard (Q11: `/`, `/songsets`, detail, `/favorites`, `/settings`)

New hook `src/hooks/useOfflineRedirect.ts`: `useEffect` on `connectivity === "offline"` (from existing `useConnectivity`; OS-offline only — `navigator.onLine === false` — never on `unknown`/failed probe) → `window.location.replace("/offline")` (replace, not assign: no history junk; full-document so the browser drops the dead SPA context). Call it at the top of: `HomePageClient`, `SongsetsClient`, `SongsetEditorClient`, `FavoritesClient`, `settings` page client. `docs`, `share/*`, `login`, controller, projection pages excluded.

Edge: `/offline` itself must NOT include the hook (no loop). `/offline` must boot fully offline — it is client-rendered from IndexedDB, but its **document** must be servable offline: add `/offline` to the SW's pre-cached documents. Concretely: when `downloadOfflineArtifacts` runs (already calls `cacheControllerDocument`), also cache the `/offline` document — extend `src/lib/offline/document-cache.ts` with `cacheOfflineListDocument()` (same `cache.put("/offline", docResponse)` pattern as `cacheControllerDocument` at `document-cache.ts:104-129`), called from `download-offline.ts` next to the `cacheControllerDocument` call (line 107). Additionally opportunistically cache it whenever any authed page boots online (`useEffect` in `Header` or `layout` client component, best-effort `.catch(() => {})`) so the list works before any download. If neither cache exists and the user is OS-offline, the SW shows its fallback HTML — acceptable last resort; the guard redirect will land there only in that degenerate case.

Test: new `src/test/hooks/useOfflineRedirect.test.ts` (redirect fires only on `offline`, not `unknown`/`online`; uses `location.replace`); extend `useConnectivity.test.ts` fixtures as needed.

### Step 5 — Delete the Play screen (clean cutover)

Delete:
- `src/app/songsets/[id]/play/page.tsx` (entire `PlayPage`; `SongsetNotFoundError` class is local to it).
- `src/components/play/PrePlayCard.tsx` (only consumer was the Play page, `play/page.tsx:267`).
- `src/components/play/OfflineAvailableCard.tsx` + its test `src/test/components/offline/OfflineIndicator.test.tsx` sibling coverage — specifically `src/test/components/play/OfflineAvailableCard.test.tsx` if present (verify with glob; the scout found OfflineStatus/OfflineIndicator tests; OfflineAvailableCard test existence unverified — confirm first, delete if it exists).
- `src/components/play/OfflineStatus.tsx` — **move, don't delete**: it hosts the download-for-offline control, which Q16 moves to the Detail page. Render `<OfflineStatus songsetId songsetName renderJobId>` inside `SongsetEditorClient`'s render (`src/app/songsets/[id]/SongsetEditorClient.tsx`, near the Start Worship control; it already has `songsetId`, and fetches the songset with `latestRenderJobId` for the `renderJobId` prop — verify the field name in that file before wiring). Keep `src/test/components/play/OfflineStatus.test.tsx` (component unchanged, only its mount point moves).
- `src/test/app/play-page.test.tsx` (entire suite pins the deleted page).
- Orphaned i18n keys after deletion — remove keys with zero remaining references (grep each): `play.title`, `play.backAriaLabel`, `play.notFound`, `play.loadFailed`, `play.backToSongsets`, `play.offline.heading`, `play.offline.hint`, all `preplay.*` (`src/lib/i18n/messages/play.ts:12-16`, `120-124`; `preplay.ts` bundle). **Keep** `audio.offline.*` (used by moved OfflineStatus + new Update action). If `play.ts` bundle empties, unregister it from `messages.ts:126-140`.
- `useSongsetListBack` (`src/lib/songset-list-state.ts` consumers): still used by editor + render page error states — keep; only the Play page import disappears.

Grep-verifiable zero-reference check before finishing: `grep -rn "songsets/\${.*}/play\b" src/` (backticked template targets) returns no `/play` (non-controller) hits; `grep -rn "PlayPage\|PrePlayCard\|OfflineAvailableCard" src/` returns nothing.

## Critical files & anchors

- `src/app/songsets/[id]/play/controller/page.tsx` — `loadData` branches 231–261; go-back 474–486; `exitRoute` prop pass to `ControllerPlayer`
- `src/components/play/ControllerPlayer.tsx:735` — exit default `exitRoute ?? /songsets/${playerId}/play`
- `src/app/songsets/SongsetsClient.tsx:49-66, 246-252` — `transformSongsetsWithOffline` (`isOfflineAvailable` flag source) and `handlePlay`
- `src/lib/offline/offline-playback.ts:129` — `resolveOfflinePlayback` (cache-first decision primitive)
- `src/lib/offline/offline-index.ts:101,150` — `listOfflineRecords`, `removeOfflineSongset`
- `src/lib/offline/download-offline.ts:70,107` — `downloadOfflineArtifacts`; `cacheControllerDocument` call site to extend with offline-list document caching
- `src/hooks/useConnectivity.ts` — consumed by new `useOfflineRedirect`; unchanged itself
- `public/sw.js:124-126,154,193-200` — navigate regex route, `sow-pages` NetworkFirst, `setCatchHandler` fallback (read to confirm `/offline` document caching behavior; change only if the navigate-route regex excludes `/offline`)

## Verification

Prereq: dev server `cd delivery/webapp && pnpm dev` (`--experimental-https`, port 8080); browser via CDP headless Chrome per AGENTS.md recipe (cert-tolerant Chrome on 9222, `app.cdp_url`). Offline emulation: CDP `Network.emulateNetworkConditions({offline: true})` (NOT `Emulation.setOfflineMode`) per project memory; for true no-network also verify with device Airplane Mode if available.

1. `pnpm test` — full Vitest suite green after test updates in steps 1–4.
2. Cached-offline boot: download a songset (online, via Detail page's moved OfflineStatus control), then `Network.emulateNetworkConditions(offline: true)`, hard-navigate to `/` → expect redirect to `/offline`; tap the songset row → controller boots and plays from cache with zero network (assert via CDP `Network` events: no requests except blob/proxy artifact URL).
3. Cache-first while online: with network ON, tap the cached row from `/songsets` → full-document assign to `/play/controller`, controller boots offline copy (no live fetch chain).
4. Stale + Update: re-render the songset server-side (or mutate `latestRenderJobId` in a test fixture), reload `/offline` online → "Update available" visible; click Update → progress → success toast; row hint clears.
5. Back/exit: in offline playback, exit button → `/offline` (assert `location` path after click).
6. Inert broken row: delete the artifact cache entries via devtools (`caches.delete` on the artifact cache for that renderJobId) keeping the index record → row shows "needs re-download", tap does nothing.
7. Redirect scope: with offline emulation, `/favorites` and `/settings` redirect to `/offline`; `/docs` and `/share/<token>` do not.
8. Play screen gone: navigating to `/songsets/<id>/play` 404s (Next default), share link `/share/<token>` still works online (its controller plays).

## Assumptions & contingencies

- `OfflineAvailableCard.test.tsx` existence unverified (scout listed OfflineStatus/OfflineIndicator tests only) — confirm with glob during step 5; delete if present.
- `exitRoute` default at `ControllerPlayer.tsx:735`: if share controller is the only explicit `exitRoute` user (verified by scout) and songset controller now always passes `exitRoute="/offline"`, change the default to `/songsets` for safety; if any other consumer surfaces, leave default unchanged.
- `SongsetEditorClient` fetch shape: confirm it exposes `latestRenderJobId` (scout says it fetches the songset); if not, wire `OfflineStatus` from the same `/api/songsets/${id}` response field the Play page used (`renderJob.latestRenderJobId` chain may differ — read the editor's fetch before wiring; fallback: fetch `/api/render-jobs` by songset as the Play page did).
- If `/offline` document caching via SW proves flaky in verification step 2 (fallback HTML appears), pre-decided fallback: also add `"/offline"` to the SW install-time precache list in `public/sw.js` (simple static precache of the authed document; auth redirect on the cached doc is acceptable because `/offline` renders its empty state client-side and the redirect target after re-login returns to it).
- i18n zh-Hant translations: draft EN-first; zh-Hant rendered in the same style as existing `core.ts` `:285-292` entries (user reviews wording after).
