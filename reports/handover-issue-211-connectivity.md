# Handover: Issue #211 — Unified Connectivity state with reachability probe

**Branch:** `fix_offline_worship_playback` (spec lands inside PR #209 per issue)
**Status mid-implementation** — health endpoint, Connectivity module, OfflineIndicator and controller page are DONE and green. Play page is MID-EDIT. Read this whole doc before continuing.

## Spec source

`issue://211` (GitHub issue mhuang74/stream_of_worship#211, label ready-for-agent). Full spec is in the issue body — read it; it contains all 18 user stories, implementation decisions, testing decisions, out-of-scope list. CONTEXT.md already carries the new "Offline"/"Connectivity" glossary (uncommitted user edit — include it in the commit).

## Design decisions LOCKED (do not re-litigate)

State machine in `src/hooks/useConnectivity.ts`:

- `"online"` — `navigator.onLine` true AND probe (`HEAD /api/health`, expects 204) succeeded.
- `"offline"` — `navigator.onLine` false (OS is definitive downward; no probe attempted).
- `"unknown"` — onLine true but probe never / not positively confirmed (never probed, in-flight, rejected, timeout, non-204). **Treated as Offline for all offline affordances (fail toward offline)** — one deliberate, documented exception: the controller's cache-first boot branch (branch 3) gates on definitive `"offline"` ONLY, so an in-flight probe at cold boot cannot steal a fresh online boot (else stale playback + Cast hidden for online leaders). Unknown at boot → online chain → branch-2 failure fallback covers genuinely-offline. This is commented in the controller page and the module header.
- Navigation guards (play page / home / list / editor Play clicks) use `connectivity !== "online"` → `window.location.assign` (document path). Fail-toward-offline → deterministic path is safe both ways.
- Probe triggers, event-driven only, NO timers/polling: module boot (`void probeConnectivity()` at module init in the client-only block), `online` event, `visibilitychange`→visible, and explicit `void probeConnectivity()` after app-level fetch failures (wired: controller branch-2 catch; play page network-failure catch — **not yet wired in play page, see below**).
- Probe dedupes in-flight (`probeInFlight` flag). Timeout 5s via AbortController.
- Test seam (issue-mandated, the only new one): `setConnectivityProbe(fn | null)` — null restores default + resets probe memory. `getConnectivity()` imperative getter for effect/click-time reads; `useConnectivity()` hook (useSyncExternalStore, server snapshot `"online"`).

## DONE and verified green

1. `src/app/api/health/route.ts` — NEW. HEAD+GET → 204, `Cache-Control: no-store`, touches no DB/auth.
2. `src/proxy.ts` — `/api/health` added to PUBLIC_PATHS (probe must never depend on session).
3. `src/test/proxy.test.ts` — new it.each case: health probe not redirected unauthenticated.
4. `src/test/api/health.test.ts` — NEW. 204 + no-store for HEAD and GET.
5. `src/hooks/useConnectivity.ts` — NEW, complete (module header has full rationale).
6. `src/test/hooks/useConnectivity.test.ts` — NEW, 11 tests green (state machine, probe outcomes incl. non-204/reject/never-settling, event-driven probes, dedupe, shared store).
7. `src/components/offline/OfflineIndicator.tsx` — migrated to `useConnectivity()`; banner shows when `connectivity === "online"` → null, i.e. banner visible for offline AND unknown.
8. `src/test/components/offline/OfflineIndicator.test.tsx` — rewritten; 11 tests green. NOTE: "when online" tests must `await confirmOnline()` (one successful probe) before asserting no banner — fail-toward-offline shows the banner until Online is confirmed.
9. `src/app/songsets/[id]/play/controller/page.tsx` — migrated: removed local `isOffline()` + `subscribeConnectivityNever`; uses `useConnectivity()` (render hint, `offlineNow = connectivity === "offline"`) and `getConnectivity() === "offline"` at boot effect time (decision made once, deliberately NOT in effect deps — see comment block above the useEffect). Branch-2 catch fires `void probeConnectivity()`.
10. `src/test/app/controller-page.test.tsx` — 55 tests green. Added: two tests under "offline boot" pinning that a failed/in-flight probe → still runs the online chain (not silent cache-first) and lands branch-2 with toast when chain fails. Added `setConnectivityProbe(null)` to that describe's `afterEach`, and the import is at line ~47 (`import { setConnectivityProbe } from "@/hooks/useConnectivity";`).

## IN PROGRESS — play page (`src/app/songsets/[id]/play/page.tsx`)

Edit APPLIED: `handleStartWorship` now checks `connectivity !== "online"` with dep `[connectivity, router, songsetId]`. BUT `connectivity` is **not yet defined in the component** — you must add:

```tsx
import { probeConnectivity, useConnectivity } from "@/hooks/useConnectivity";
```
and inside `PlayPage()` (near other hooks):
```tsx
const connectivity = useConnectivity();
```

Still to do on this file:
1. `void probeConnectivity()` in the network-failure catch of `loadSongset` (the catch block that calls `offerOfflineEntry()` on genuine network rejections — NOT the `SongsetNotFoundError` return). Spec trigger: "probe fires after an app-level fetch failure".
2. Update `src/test/app/play-page.test.tsx`:
   - Top of file: import `setConnectivityProbe` and add `probe.mockResolvedValue(true)` + `setConnectivityProbe(probe)` in a top-level `beforeEach` (mirror OfflineIndicator.test.tsx pattern: `const probe = vi.fn<() => Promise<boolean>>()`); `setConnectivityProbe(null)` in afterEach so a failing probe never leaks across suites.
   - Existing tests then pass unchanged: "online Start Worship keeps SPA navigation" (state online), "offline Start Worship is a full document navigation" (stubOnline(false) → definitive offline), offline-entry 503/rejection tests (behavior unchanged).
   - ADD (spec: probe Iraqi outcome at page seam — onLine true + probe fails → Unknown → document path): render with `stubOnline(true)`, `stubLocation()`, chain success fetches (reuse the two `mockResolvedValueOnce` pattern from the SPA test), probe mocked false, click start → expect `locationAssignMock` called with `/songsets/test-songset/play/controller`, `mockPush` NOT called with it.
   - Then run `npx vitest run src/test/app/play-page.test.tsx` — must be green before moving on. (Do NOT batch-run suites before each edit lands cleanly.)

## TODO — home/list/editor guards (same 3-line pattern each)

1. `src/app/page/HomePageClient.tsx` (guard in `handleSongsetPlay`, ~line 108): replace `typeof navigator !== "undefined" && navigator.onLine === false` with `connectivity !== "online"`; add `const connectivity = useConnectivity();` at top, dep in useCallback.
2. `src/app/songsets/SongsetsClient.tsx` (guard in `handlePlay`, ~line 243): same.
3. `src/app/songsets/[id]/SongsetEditorClient.tsx` (guard in `handlePlay`, ~line 339): same.
4. Tests:
   - `src/test/app/home/HomePageClient.test.tsx`: existing test "navigates to the play page when play is clicked" needs probe stubbed true in beforeEach (else unknown → document path → mockPush never called). ADD offline test: `stubOnline(false)` + window.location stub (`Object.defineProperty(window, "location", { value: { assign: mockAssign }, configurable: true })` — see play-page.test.tsx `stubLocation()` for the pattern) → click Play → assert assign to `/songsets/s1/play`, mockPush not called. Restore onLine in afterEach.
   - SongsetsClient guard: extend `src/test/app/songsets-offline-merge.test.tsx` (it renders the full SongsetsClient with URL-dispatch fetch mock — add a play-click offline nav test there; note its `beforeEach` leaves probe unstubbed → unknown → guards fire document path already with onLine=false; for the online-SPA side leave untested or stub probe true).
   - Editor client guard has no direct page test file (SongsetEditor.test.tsx tests the inner component). A minimal case: skip creating a heavy new suite OR add one tiny test in a new `src/test/app/songset-editor-page.test.tsx` only if cheap — module store + guards are shared with the other 5 screens which are all tested; use judgment, don't gold-plate.

## TODO — Service worker carve-out

`public/sw.js`: add BEFORE the `/api/songs` route (after `workbox.setConfig({ debug: false })`):

```js
// Reachability probe (issue #211): the client's Connectivity check must
// always reflect a genuine round trip — no strategy may ever cache or
// time out around the health endpoint.
workbox.routing.registerRoute(
  ({ url }) => url.pathname === "/api/health",
  new workbox.strategies.NetworkOnly()
);
```

`src/test/lib/offline/artifact-cache-sw-parity.test.ts`: extend with a `describe("service worker ↔ app health-probe contract (issue #211)")` following the file's established string-slice style (marker comment `// Reachability probe (issue #211)` sliced to the next route comment): assert block contains `"/api/health"` and `NetworkOnly`, contains no `CacheableResponsePlugin`/`cacheName`. Note: on network failure, Workbox falls to `setCatchHandler` → 503 `{"error":"offline"}` → probe sees non-204 → offline. Correct; no catch-handler change needed.

## Gotchas / invariants

- **Do not** put `connectivity`/`getConnectivity()` depending effect deps on the controller boot effect — decision is once-at-boot by design (comment explains why).
- Controller branch 3 ("zero API fetches" test asserts `global.fetch` not called) works because `probeConnectivity()` skips probing when `navigator.onLine === false`.
- Controller branch-1 tests pass with the probe unconfirmed because boot treats unknown → chain; keep `setConnectivityProbe(null)` restore in afterEach.
- `useConnectivity` module fires one probe at module init on client (inside the `typeof window !== "undefined"` block at the bottom) — in jsdom tests this uses unstubbed global fetch → rejects harmlessly (state stays unknown). Never remove the try/catch in `defaultProbe`.
- Repo rules that bit already: **no `any`** (biome rule fired), avoid one-line wrapper functions.
- Do not touch `share/` flow, artifacts, document-cache (out of scope per issue).
- After code work: `npx tsc --noEmit`, `pnpm lint`, targeted suites while iterating, then full `pnpm test` ONCE at end. Then `/code-review` skill, commit, `graphify update .` (separate chore commit per repo memory), `git pull --rebase && git push` — push is MANDATORY per AGENTS.md.
- Commit message suggestion: `feat(webapp): unify offline detection behind shared Connectivity state with reachability probe (#211)`. The uncommitted CONTEXT.md Offline/Connectivity glossary belongs in this commit.

## Acceptance checklist from spec (verify before finishing)

- All six scattered `navigator.onLine` reads gone (grep `navigator\.onLine` under `src/app`+`src/components` should only match the migrated files' comments, none in logic).
- Fail-toward-offline story 9, Airplane-toggle banner story 2, boot cache-first story 5/6, doc-path story 7 — covered by the tests listed above.
- Real-device Pixel 8 Chrome validation is the user's (out of our scope) — mention in summary.
