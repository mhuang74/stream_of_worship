# Handover: Offline Worship Simplification — cache-first playback + /offline list

**Branch:** `fix_offline_worship_playback` · **Base:** `048a2d4c` · **Date:** 2026-09-18
**Spec:** GitHub issue [#212](https://github.com/mhuang74/stream_of_worship/issues/212) (also at `specs/offline-worship-simplification-cache-first-playback.md`)
**Plan of record:** `specs/offline-worship-simplification-cache-first-playback.md` (verbatim copy of the approved execution plan)

## Status: implementation COMPLETE, browser verification PARTIAL, push NOT done

All five plan steps are implemented and the full Vitest suite is green
(2452 passed / 0 failed). Production build passes. Browser verification
verified login + dashboard load; the offline-scenario steps (2–8 of the plan's
Verification section) were **not** run. Nothing is committed or pushed.

## What was done (per plan step)

### Step 1 — Cache-first controller boot
`delivery/webapp/src/app/songsets/[id]/play/controller/page.tsx`
- `loadData` restructured: OS-offline branch first (loadOffline → else
  `control.offlineUnavailable`, no fetch). Then cache-first: if
  `getOfflineRecord(songsetId)` exists and `loadOffline()` (which calls
  `resolveOfflinePlayback`) succeeds → boot offline copy **even when online**.
  Record-without-usable-bytes online → falls through to the online chain.
- Added `import { getOfflineRecord } from "@/lib/offline/offline-index"`.

### Step 2 — Entry points → `/play/controller`
- `HomePageClient.tsx` `handleSongsetPlay`: now async; `(await getOfflineRecord(id)) || connectivity !== "online"` → `location.assign(.../play/controller)`, else `router.push(.../play/controller)`.
- `SongsetsClient.tsx` `handlePlay`: keys off row's `isOfflineAvailable` flag (from `transformSongsetsWithOffline`) OR not-online → assign to `/play/controller`; else push.
- `SongsetEditorClient.tsx` `handlePlay`: same pattern; adds `isOfflineAvailable` state seeded from `getOfflineRecord` in an effect.
- `DashboardSongsetCard.tsx`: `onPlay` type widened to `void | Promise<void>`.
- `ControllerPlayer.tsx:735`: exit default changed to `exitRoute ?? "/songsets"` (was `/songsets/${playerId}/play`). Both controllers pass explicit exitRoute.
- Controller page: passes `exitRoute="/offline"`; error-screen go-back button → `router.push("/offline")`.

### Step 3 — `/offline` route
- NEW `src/app/offline/page.tsx` (auth-gated server wrapper) and `src/app/offline/OfflineClient.tsx`.
- OfflineClient: renders from `listOfflineRecords()` only, no boot network fetch; async byte verification (`matchCachedArtifact(mp4|mp3)`) downgrades broken rows to inert + `offline.needsRedownload`; online-only staleness comparison (per-row `/api/songsets/${id}` → `latestRenderJobId`) with Update button → `downloadOfflineArtifacts` + toasts; Remove → `removeOfflineSongset` + `songsets.toast.offlineRemoved`; empty state → `/songsets` link; Play → `location.assign(.../play/controller)`.
- NEW `src/lib/i18n/messages/offline.ts` (EN + zh-Hant), registered in `messages.ts`. Keys: `offline.title/empty/emptyLink/ready/needsRedownload/updateAvailable/update/updated/remove/play/cachedPrefix`.
- `core.ts`: added `nav.offline` ("Offline" / 「離線」). `BottomNav.tsx` + `Header.tsx` add the `/offline` entry.

### Step 4 — Redirect guard + /offline document caching
- NEW `src/hooks/useOfflineRedirect.ts`: `location.replace("/offline")` only on definitive `connectivity === "offline"` (never Unknown). Mounted in HomePageClient, SongsetsClient, SongsetEditorClient, FavoritesClient, settings page. NOT on /offline, controllers, share, docs, login, projection.
- `document-cache.ts`: refactored `cacheControllerDocument` to share `cacheDocumentAtPath(path)`; added `cacheOfflineListDocument()` caching `/offline`.
- `download-offline.ts`: now also calls `cacheOfflineListDocument()` after `cacheControllerDocument`.
- `Header.tsx`: opportunistic `cacheOfflineListDocument()` on authed online boot (best-effort).
- `public/sw.js`: unexpiring document route regex extended with `|| url.pathname === "/offline"`.

### Step 5 — Play screen deleted (clean cutover)
- Deleted: `src/app/songsets/[id]/play/page.tsx`, `src/components/play/PrePlayCard.tsx`, `src/components/play/OfflineAvailableCard.tsx`, `src/test/app/play-page.test.tsx`, `src/test/components/play/PrePlayCard.test.tsx`. (No `OfflineAvailableCard.test.tsx` existed.)
- `OfflineStatus.tsx` MOVED (not deleted) into the detail page: `SongsetEditor.tsx` gained an `offlineStatusSlot?: React.ReactNode` prop rendered under the header; `SongsetEditorClient.tsx` fetches `/api/render-jobs/${latestRenderJobId}` for the R2 keys and mounts `<OfflineStatus>`.
- i18n `play.ts`: removed all `play.*` (7 keys) and `preplay.*` (26 keys) from both locales; kept projection/controls/lyrics/controller namespaces. Bundle NOT emptied, still registered.
- `src/types/presentation-api.d.ts` comment updated (was referencing PrePlayCard).
- Zero-reference checks pass: no `PlayPage|PrePlayCard|OfflineAvailableCard` hits; no bare `/play` template targets remain.

## Test changes (all green)
- `controller-page.test.tsx`: inverted "runs online chain when online" → "boots offline copy when cached, even when online"; new tests for record-without-bytes online (→ online chain) and OS-offline (→ offlineUnavailable); branch-2 fallback/401/probe tests rewritten to non-cached premise using `mockGetOfflineRecord.mockResolvedValueOnce(null).mockResolvedValue(OFFLINE_RECORD)` (cache-first boot reads the index once; fallback/recovery reads again); `beforeEach` now calls `setConnectivityProbe(null)` (module-level `lastProbeSucceeded` was leaking "online" across tests).
- `songsets-offline-merge.test.tsx`: assign target → `/play/controller`; new cached-row-when-online test.
- `HomePageClient.test.tsx`: targets → `/play/controller`; new cached-songset test; click handlers now async → wrapped in `await act(async () => fireEvent.click(...))`; added `locationReplaceMock` to window.location stubs (guard calls `replace`); `offline-index` mocked (`getOfflineRecord` default null).
- `ControllerPlayer.test.tsx`: `mockPush` extracted; new pins for explicit `exitRoute="/offline"` and default `/songsets`.
- `download-offline.test.ts`: mock gained `cacheOfflineListDocument`; doc pre-cache test asserts both calls after index write.
- NEW `src/test/hooks/useOfflineRedirect.test.ts` (4 tests).
- Lint: 0 errors, 7 pre-existing warnings.

## Environment fix applied (important, outside repo)
`/opt/sow/.env_webapp` line 5: `SOW_DATABASE_URL` value contains `&` and was
UNQUOTED — every `source` silently dropped the variable (shell backgrounded
`channel_binding=require`), which broke `pnpm build` with "SOW_DATABASE_URL
environment variable is required". Fixed by quoting the value; backup at
`/opt/sow/.env_webapp.bak`. Build then passed (33/33 pages).

## Remaining work (in order)

1. **Browser verification (plan steps 2–8 of Verification).** Dev server
   running as hub process `webapp` (`pnpm dev:https`, port 8080, HTTPS
   self-signed) and headless Chrome as `cdp-chrome` (CDP on 9222, profile
   `/tmp/sow-chrome-profile`). Browser tab `sow-offline` already signed in
   with test-user creds from `SOW_WEBAPP_TESTUSER_LOGIN`/`_PASSWORD` env vars
   (sourced from the shell; NOT in the repo). Login quirk: after submit the
   client redirect may not complete — do `tab.goto("https://localhost:8080/")`.
   Offline emulation: CDP `Network.emulateNetworkConditions({offline: true})`
   via `tab.run(async ({page}) => ...)` with the raw CDP session — NOT
   `Emulation.setOfflineMode`. Verify: cached-offline boot (redirect `/` →
   `/offline`, row tap plays with zero network), cache-first while online,
   stale/Update flow, exit → `/offline`, inert broken row (`caches.delete` the
   artifact cache for the renderJobId), redirect scope (`/favorites`,
   `/settings` redirect; `/docs`, `/share/*` do not), `/songsets/<id>/play`
   404s, share flow still works.
2. **Commit + push.** AGENTS.md mandate: `git pull --rebase && git push`, then
   `git status` shows "up to date with origin". Nothing is committed yet —
   all changes are working-tree only (28 modified/deleted + `src/app/offline/`
   untracked + `specs/offline-worship-simplification-cache-first-playback.md`
   untracked). Suggested message: `feat(webapp): cache-first offline playback + /offline list; delete Play screen (issue #212)`.
3. **`graphify update .`** at repo root (AGENTS.md rule after code changes).
4. **Optionally** comment on issue #212 with the completion summary; the
   outstanding manual Pixel 8 airplane-mode validation of #211 still applies.

## Gotchas
- `.next/dev/types` and `.next/types` may hold stale validator refs to the
  deleted Play page → `tsc --noEmit` fails; `rm -rf .next/dev/types .next/types`
  clears it (already done once).
- Dev script: plain `pnpm dev` is HTTP-only; use `pnpm dev:https` (the AGENTS.md
  recipe and the SW/CDP flows assume HTTPS on 8080).
- `pnpm vitest run` takes ~2 min; run scoped files during iteration.
- The test user has downloaded songsets? Unknown — if `/offline` is empty
  during verification, download one first via the detail page's OfflineStatus
  control (now on the detail page, not a Play screen).
