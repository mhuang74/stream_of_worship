# Reduce Database Network Transfer — v2

**Status:** Implementation Plan (Not Yet Implemented)
**Supersedes:** `specs/reduce-database-network-transfer-v1.md`
**Created:** 2026-07-27
**Storage:** 0.17 GB → **Network Transfer:** 5.5 GB (32× amplification)

## What Changed From v1

v1 contained multiple technical inaccuracies verified against the codebase:

| v1 Claim | Reality |
|---|---|
| Webapp uses `@vercel/postgres` | Already on `@neondatabase/serverless` `neon-http` (`delivery/webapp/src/db/index.ts:1-2`) |
| `render_jobs` has 35 columns incl. `ffmpeg_args_json`, `tags`, `progress_data` | ~29 columns; those three do not exist (`db.py:46-76`) |
| Progress updates do read-modify-write on `progress_data` JSONB | Pure `UPDATE…RETURNING *` on scalar columns (`db.py:296`) — no `progress_data` |
| `songs` table has `userId` column | `songs` is a shared catalog; no `user_id` (confirmed in DS4 schema dump, `songs.ts:291-327`) |
| `getRenderPageData` runs 5 sequential queries | 2 sequential + 1 `Promise.all` of 3 conditional queries (`songsets.ts:580-679`) |
| Android polls "every 2s with maxRetries 10 (20s total)" | `maxRetries` bounds **error retries** only; steady-state polling is indefinite; backoff already exists for errors (`RenderViewModel.kt:67-70, 182-184`) |
| Phase 1 = render worker (biggest source) | Unverified; DS4 estimates webapp dominates |

v2 corrects these, adds a Phase 0 instrumentation gate, includes the auth-session optimization v1 omitted, allows additive schema columns for denormalization, and standardizes pagination on `COUNT(*) OVER()`.

## Scope Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Webapp runtime | Verify per-route before committing driver switch | Mixed/unknown — WebSocket `Pool` requires Node runtime; Edge forbids it |
| Schema changes | Allow additive columns only (no column drops, no renames) | Permits `songsets.item_count` / `songsets.total_duration_seconds` backfill migration |
| Auth middleware caching | Include with care | Document TTL, signing; v1 entirely omitted this DS4 P1 |
| Priority attribution | Instrument first, prioritize second | DS4 vs Gemma disagreement; defer ordering to data |
| Pagination | `COUNT(*) OVER()` window function | Preserves "Page N of M" UI; single query |
| Feature flags | None — rely on per-change revert + tests | Simpler ops |

## Goals

| Metric | Current | Target |
|---|---|---|
| Total network transfer | 5.5 GB | < 1.5 GB (≥70% reduction) |
| Per-query average payload | ~1.2 KB | < 300 bytes |
| Auth DB queries per API request | 1 (every route) | 0 in steady state (cached in middleware) |
| `render_jobs`-row bytes per progress update | ~1.2 KB (RETURNING *) | < 200 bytes (projected RETURNING) |
| `listSongsetSummaries` queries per call | 2 (data + COUNT) | 1 (window function) |
| Steady-state Android poll interval | 2s constant | 2s (first 10s) → 5s → 10s by job age |

## Phase 0 — Baseline Instrumentation (Gate for Phases 1+)

**Goal:** Establish per-route, per-source attribution of DB transfer before any change lands.

### Sub-tasks

1. **Verify webapp runtime per route.**
   - Inspect `delivery/webapp/next.config.*` and route-segment `export const runtime` declarations.
   - Output: table of routes × runtime (nodejs | edge | inherit).
   - Decision gate: if **any** high-traffic route is on `edge`, the driver switch (Phase 9) is **dropped**; v2 then doubles down on projections + auth caching.

2. **Enable `pg_stat_statements` on Neon** (not `log_statement='mod'` — that floods logs).
   - Confirm extension enabled: `CREATE EXTENSION IF NOT EXISTS pg_stat_statements;`
   - Snapshot baseline: `SELECT query, calls, total_exec_time, rows, mean_exec_time FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 50;`
   - Capture to `reports/baseline-pg-stat-YYYYMMDD.csv`.

3. **Add ad-hoc DB-query counter middleware** (temporary, dev-only).
   - Wrap `db.query.*` invocations in `delivery/webapp/src/db/index.ts` behind a `Proxy` that counts calls + byte sizes per request.
   - Log `route -> { calls, totalBytes }` to console in `dev`.

4. **Neon dashboard baseline.** Snapshot "Data transfer — last 7 days" to `reports/baseline-neon-transfer-YYYYMMDD.md`. Tag with webapp version.

5. **Render-worker job profile.** For one representative render job, log every `psycopg2` query + row bytes via a temporary `cursor_factory` wrapper.

### Deliverables

- `reports/baseline-pg-stat-YYYYMMDD.csv`
- `reports/baseline-neon-transfer-YYYYMMDD.md`
- `reports/baseline-route-profile.md` (per-route query counts from sub-task 3)
- `reports/baseline-render-worker-profile.md` (from sub-task 5)

### Phase Exit Criteria

- All baselines captured and committed.
- Per-route runtime table complete.
- Driver-switch feasibility decision documented (GO / NO-GO).

## Phase 1 — Webapp: Column Projection on Hot Catalog Queries

**Goal:** Cut payload size on `songs` + `recordings` reads. Tackles Gemma's primary finding (over-fetching) and the second-biggest v1 concern without any operational risk.

### Files

- `delivery/webapp/src/lib/db/songs.ts` — `listSongs`, `searchSongs`, `getSong`
- `delivery/webapp/src/lib/db/search.ts` — `fullTextSearchSongs`
- `delivery/webapp/src/lib/db/songsets.ts` — `getSongsetEditorData` items query

### Changes

Replace `db.query.*.findMany({ with: { recordings: true } })` with explicit `db.select({...})` projecting only UI-displayed fields:

| Caller | Project to |
|---|---|
| Song list (`listSongs`) | `songs.{id, title, composer, albumName, musicalKey, musicalKeyMode, updatedAt}` + `recordings.{durationSeconds, tempoBpm, musicalKey, musicalMode, visibilityStatus}` |
| Song search (`searchSongs`, `fullTextSearchSongs`) | Same as list |
| Semantic search | Same as list (vector search doesn't need wide payload back) |
| `getSongsetEditorData` items query | Already projected — no change needed (verified `songsets.ts:603-630`) |

Note: `getSong` (song detail page) legitimately needs `lyricsRaw`/`lyricsLines`/`sections` — do **not** project here.

### Tests

- New: `delivery/webapp/src/lib/db/songs.test.ts` — assert projected shape
- New: `delivery/webapp/src/lib/db/search.test.ts` — assert search payload schema
- Reuse: existing API integration tests for /api/songs, /api/songs/search
- Verify `pnpm test` passes, `pnpm lint` passes.

### Measurement

- Compare `pg_stat_statements` rows × mean row size for affected queries vs Phase 0 baseline.
- Target: ≥60% reduction in bytes returned per `listSongs(limit=50)` call.

### Rollback

Pure projection — revert to `with: { recordings: true }`. No semantic change.

## Phase 2 — Webapp: Auth Session Caching in Middleware

**Goal:** Eliminate the per-API-route DB hit for `auth.api.getSession()` (DS4 P1; absent in v1).

Verified pattern: `getSession` is invoked at the top of every route handler — 30+ call sites confirmed via grep (`src/app/api/**`). Each call hits Better Auth's Drizzle adapter → `session` table.

### Files

- New: `delivery/webapp/src/middleware.ts` (Next.js middleware)
- Update: all `delivery/webapp/src/app/api/**/route.ts` — replace direct `auth.api.getSession()` with header read
- Update: server-rendered pages `src/app/songsets/page.tsx`, `src/app/songsets/[id]/page.tsx`, `src/app/songsets/[id]/render/page.tsx`

### Design — with care

1. **Middleware calls `auth.api.getSession` once** per request, before route handler runs.
2. **Propagate to handler via `request.headers`** — set a request-scoped header `x-sow-session`.
3. **Do not serialize the full session into a cookie** (security: it would be replayable). The header lives in the request only, never persisted client-side.
4. **Signing**: sign the header with an HMAC of `(session.id + timestamp + secret)` to prevent handler-level spoofing when middleware is bypassed (e.g. direct route invocation in tests).
5. **TTL**: header is valid for the duration of a single request only; no cross-request caching.
6. **Public routes** (`/api/share/[token]`, `/api/auth/[...all]`): bypass middleware; route handler still calls `auth.api.getSession` directly when needed.

### Helper

```ts
// src/lib/server-session.ts
import { auth } from "@/lib/auth";

export async function getServerSession(req: Request): Promise<Session | null> {
  const signed = req.headers.get("x-sow-session");
  if (signed) return verifySigned(signed);
  // Fallback when middleware didn't run (tests, public routes)
  return auth.api.getSession({ headers: req.headers });
}
```

### Tests

- New: `delivery/webapp/src/lib/server-session.test.ts` — verify signature, TTL, fallback, public-route bypass
- Update: existing route tests — mock the header instead of `auth.api.getSession`
- Verify `pnpm test` passes.

### Measurement

- Phase 0 route-profile: ~1 call/route to `session` table → 0 for cached path.
- Target: 100% reduction in `session`-table reads for authenticated API calls during steady-state.

### Rollback

Remove middleware, restore direct `auth.api.getSession` calls. Fallback path keeps handlers functional.

## Phase 3 — Webapp: Pagination via `COUNT(*) OVER()` + Eliminate Standalone COUNT Queries

**Goal:** Halve query count on paginated list endpoints without losing exact total count UX.

### Files

- `delivery/webapp/src/lib/db/songs.ts` — `listSongs`, `searchSongs`
- `delivery/webapp/src/lib/db/search.ts` — `fullTextSearch`
- `delivery/webapp/src/lib/db/songsets.ts` — `listSongsetSummaries`

### Change

Replace:
```ts
const [items, countResult] = await Promise.all([
  db.query.songs.findMany({ limit, offset, where }),
  db.select({count: sql`count(*)`}).from(songs).where(where),
]);
```

With explicit `db.select` using a window function:
```ts
const rows = await db
  .select({
    id: songs.id,
    title: songs.title,
    /* ...projected columns... */
    totalCount: sql<number>`count(*) over()`,
  })
  .from(songs)
  .where(where)
  .orderBy(desc(songs.updatedAt))
  .limit(limit)
  .offset(offset);

const total = rows.length > 0 ? Number(rows[0].totalCount) : 0;
const items = rows.map(({ totalCount, ...item }) => item);
```

Replaces v1's sentinel `LIMIT+1` approach (`LIMIT+1` loses total count UX — only use sentinel for genuinely infinite-scroll lists; webapp doesn't have any today).

### Tests

- New: `delivery/webapp/src/lib/db/songs.test.ts` — verify `total` correctness on first/last page, empty results
- New: `delivery/webapp/src/lib/db/songsets.test.ts` — assert `listSongsetSummaries` returns correct pagination metadata

### Rollback

Revert to two separate queries (data + COUNT). Behavior unchanged.

## Phase 4 — Webapp: Songset List Denormalization (additive schema change)

**Goal:** Eliminate the `LEFT JOIN songset_items + LEFT JOIN recordings + GROUP BY + aggregate` cost on `listSongsetSummaries` (`songsets.ts:343-373`).

Gemma's mid-term recommendation; v1 excluded by "no schema changes" constraint. v2 permits additive columns with backfill migration.

### Files

- New migration: `delivery/webapp/drizzle/0018_songset_denormalized_totals.sql`
- `delivery/webapp/src/db/schema.ts` — add `itemCount`, `totalDurationSeconds` columns
- `delivery/webapp/src/lib/db/songsets.ts` — read from new columns
- `delivery/webapp/src/lib/db/songsets.ts` writers: `createSongsetItem`, `updateSongsetItem`, `deleteSongsetItem`, `duplicateSongset` — maintain denormalized columns in same transaction

### Migration

```sql
ALTER TABLE songsets
  ADD COLUMN IF NOT EXISTS item_count integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS total_duration_seconds double precision NOT NULL DEFAULT 0;

UPDATE songsets s
SET item_count = sub.item_count,
    total_duration_seconds = sub.total
FROM (
  SELECT
    si.songset_id,
    count(si.id) FILTER (WHERE r.deleted_at IS NULL) AS item_count,
    COALESCE(SUM(COALESCE(r.duration_seconds, 0)) FILTER (WHERE r.deleted_at IS NULL), 0) AS total
  FROM songset_items si
  LEFT JOIN recordings r ON r.hash_prefix = si.recording_hash_prefix
  GROUP BY si.songset_id
) sub
WHERE s.id = sub.songset_id;
```

### Writers

When creating/updating/deleting items, update the parent `songsets.item_count` and `total_duration_seconds` inside the same DB transaction. Verify all existing write paths use transactions.

### Tests

- New: `delivery/webapp/src/lib/db/songsets.test.ts` — verify denormalized counts stay in sync after CRUD ops
- Existing: duplicate-songset, item reorder tests must still pass
- Roll forward migration in test DB; verify backfill row counts match Phase 0 baseline aggregates

### Rollback

Drop the two columns. Read path falls back to JOINed query (keep the legacy `select({... count(...) filter ...})` query as a fallback — feature-detect via `column exists` check or just a constant flag).

## Phase 5 — Render Worker: Project `RETURNING` and Eliminate Redundant Reads

**Goal:** Cut per-render-job transfer from ~350-710 KB to < 80 KB.

### Files

- `delivery/render-worker/src/sow_render_worker/db.py`
- `delivery/render-worker/src/sow_render_worker/pipeline.py`

### Changes

1. **Project `RETURNING` on all write paths.** For each `UPDATE ... RETURNING *`:
   - `start_render_job` (`db.py:184`) → `RETURNING id, status, started_at, updated_at`
   - `reclaim_stale_job` (`db.py:225`) → `RETURNING id, status, started_at, phase, phase_index, percent_complete, updated_at`
   - `update_render_progress` (`db.py:296`) → `RETURNING id, status` — caller only needs to detect "still running" (None == cancelled/failed). Drop the full row return in callers.
   - `complete_render_job` (`db.py:346`) → `RETURNING id, status, songset_id`
   - `fail_render_job` (`db.py:400`) → `RETURNING id, status, songset_id`
   - `recover_orphaned_jobs` (`db.py:437`) → already projects `RETURNING id, songset_id` (no change)

2. **Add lightweight `check_cancelled` helper in `db.py`:**
   ```python
   def get_render_job_status(conn, job_id, user_id) -> Optional[str]:
       with conn.cursor() as cur:
           cur.execute(
               "SELECT status FROM render_jobs WHERE id = %s AND user_id = %s",
               (job_id, user_id),
           )
           row = cur.fetchone()
       return row[0] if row else None
   ```
   Replace `check_cancelled()` body in `pipeline.py:233-236` to use it.

3. **Keep both `get_render_ratio` calls** — first call (`pipeline.py:322`) provides the estimate; second call (`pipeline.py:371`) is for accurate post-audio-mix ratio. They are not redundant — they read at different points in time. Document this in pipeline comment to prevent future "fixes."

4. **Stop calling `get_render_job()` in `complete_render_job`.** `db.py:328` does a redundant full-row `SELECT *` to compute `final_elapsed_seconds`. Replace with a projection: `SELECT started_at FROM render_jobs WHERE id = %s AND user_id = %s`.

### Tests

- Update: `delivery/render-worker/tests/test_db.py` — assert new projected shapes
- Update: `delivery/render-worker/tests/test_pipeline.py` — verify cancellation still works via lightweight status query
- Existing: `test_timeout_handling.py`, `test_video_engine.py` must pass

### Measurement

- Phase 0 render-worker profile baseline vs post-change.
- Target: >70% reduction in bytes transferred per render job.

### Rollback

Revert to `RETURNING *`; callers tolerate extra fields.

## Phase 6 — Render Worker: Reduce Progress Update Frequency

**Goal:** Reduce per-job progress writes from ~24-48 down to ~6-8.

### Files

- `delivery/render-worker/src/sow_render_worker/pipeline.py` — `video_progress_callback` (lines 439-480)

### Changes

Current threshold (`pipeline.py:458`): `if now - _last_video_db_update_time >= 5:`
Change to adaptive interval based on total video duration:

```python
total_video_seconds = total_frames / video_engine.fps
if total_video_seconds > 600:  # >10 min
    progress_interval = 30
elif total_video_seconds > 180:  # >3 min
    progress_interval = 15
else:
    progress_interval = 10
```

Always emit a final update at 100% before phase exit — already done at `pipeline.py:510`.

### Tests

- Update: `test_pipeline.py` — assert progress writes count for short/medium/long video durations
- Verify no test asserts the 5s interval specifically (would need update)

### Rollback

Revert to `progress_interval = 5`.

## Phase 7 — Render Worker: Phase 0-Concluded Items (NOT Following v1)

**Drop v1 Change #3 "Use `jsonb_set(progress_data, ...)`"** — `progress_data` column doesn't exist; would require schema change that v1 explicitly excluded.

**Drop v1 Change #9 "INSERT ... SELECT ... WHERE NOT EXISTS"** — there's already a partial unique index `0009_add_active_render_job_unique_index.sql` enforcing one active job per songset. Replace with cleaner guard using `ON CONFLICT DO NOTHING` if needed; current 2-step SELECT+INSERT in a transaction is acceptable.

## Phase 8 — Android: Steady-State Poll Interval Backoff

**Goal:** Cut render-job polling traffic during long-running renders.

### Files

- `delivery/android/app/src/main/java/org/streamofworship/android/feature/render/RenderViewModel.kt` (lines 67, 167)

### Changes

Note: existing `maxBackoffMillis = 30_000` and exponential backoff at lines 182-184 applies to **error-retry path**, not steady-state. That's correct — don't touch.

Change steady-state `delay(pollIntervalMillis)` (line 167) to a job-age-aware interval:

```kotlin
private fun steadyStateDelay(elapsedMs: Long): Long = when {
    elapsedMs < 10_000 -> 2_000L   // responsive for short jobs
    elapsedMs < 60_000 -> 5_000L  // moderate
    else -> 10_000L               // long-running
}
```

Track `pollStartMs` in `startPolling` and pass to the delay computation. Cap is naturally bounded by job completion; no `maxRetries` change for the normal path.

### Tests

- Update: `RenderViewModelTest.kt` — add tests for steady-state delay at 0s / 30s / 120s elapsed

### Rollback

Revert to constant `delay(pollIntervalMillis)`.

## Phase 9 — Webapp: Driver Switch (Conditional on Phase 0 Decision)

**Status:** BLOCKED pending Phase 0 sub-task 1 output (per-route runtime verification).

### Conditional Branch A — Node.js Runtime Confirmed

If all high-traffic routes run on Node.js runtime (not Edge):

#### Files

- `delivery/webapp/src/db/index.ts`
- `delivery/webapp/package.json`

#### Changes

Switch `drizzle-orm/neon-http` → `drizzle-orm/neon-serverless` + `Pool`:

```ts
import { neonConfig, Pool } from "@neondatabase/serverless";
import { drizzle } from "drizzle-orm/neon-serverless";

neonConfig.wsProxy = (host) => `${host}:5432/v1`;
neonConfig.pipelineConnect = false;  // safer for serverless cold starts

const pool = new Pool({
  connectionString: process.env.SOW_DATABASE_URL,
  max: 10,
  idleTimeoutMillis: 20_000,
});

export const db = drizzle(pool, { schema });
```

#### Tests

- All existing DB tests must pass
- Add: cold-start benchmark (instrument `db` first-query latency across 20 cold invocations)
- Verify `next build` still succeeds (no edge-runtime declaration incompatibilities)

#### Rollback

Revert to `neon` + `drizzle-orm/neon-http`.

### Conditional Branch B — Edge Runtime Detected

If any high-traffic route uses Edge runtime:

**Do not switch driver.** Instead:
1. Review which Edge routes genuinely need Edge (likely none beyond middleware).
2. If the only Edge use is `middleware.ts` (Phase 2), this is acceptable — middleware can do auth without a DB pool if we route DB queries via HTTP-only server actions.
3. Otherwise, port Edge routes to Node.js runtime as a separate prerequisite change.

## Phase 10 — Index Verification (No Fabricated Indexes)

**Goal:** Verify hot queries use indexes; reject v1's fabricated `idx_songs_user_id` (column doesn't exist).

### Files

- `delivery/webapp/drizzle/0014_page_load_hot_path_indexes.sql` (already exists — verified)

### Changes

1. **Confirm `0014_*` indexes are applied to production** via `SELECT indexname FROM pg_indexes WHERE schemaname='public';`
2. **EXPLAIN (ANALYZE, BUFFERS)** on hot queries:
   - `listSongsetSummaries` (post-denormalization, Phase 4)
   - `listSongs` (post-projection, Phase 1)
   - `getSongsetEditorData` items query
   - `getRenderPageData` songset lookup
3. If any show `Seq Scan`, add a targeted index. Candidate (post-Phase 4 when `item_count` exists):
   ```sql
   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_songsets_user_updated_count
     ON songsets (user_id, updated_at)
     INCLUDE (item_count, total_duration_seconds);
   ```
4. **Drop any fabricated index references** that v1 mentioned for `songs.user_id` (column doesn't exist).

### Rollback

Drop any new indexes if write latency regresses.

## Implementation Order

Driven by Phase 0 baseline output; default ordering if attribution is inconclusive:

| Order | Phase | Impact | Risk |
|---|---|---|---|
| 0 | Phase 0 (instrumentation) | — | None |
| 1 | Phase 1 (column projection) | High (webapp payload) | Low — additive |
| 2 | Phase 2 (auth middleware) | High (every API call) | Medium — auth is sensitive; restrict to single-request TTL; fall back path in handlers |
| 3 | Phase 3 (COUNT(*) OVER()) | Medium | Low |
| 4 | Phase 4 (songset denormalization) | High (list view) | Medium — migration with backfill |
| 5 | Phase 5 (render worker projection + redundant reads) | Medium | Low |
| 6 | Phase 6 (render worker progress interval) | Low-Medium | Low |
| 7 | Phase 8 (Android polling) | Low | Low |
| 8 | Phase 9 (driver switch) | High if Node.js / N/A if Edge | Medium |
| 9 | Phase 10 (index verification) | Variable | Low |

Phases 5, 6, 8 ship **independently** — do not bundle (v1's bundling increased blast radius).

## Rollback Plan

Each phase is independently reversible as documented above. No feature flags per scope decision.

**Global rollback trigger:** if Neon dashboard shows >10% week-over-week transfer increase after any phase ships, revert that phase immediately and re-measure.

## Measurement Strategy

### Per-Phase

After each phase ships:
1. Snapshot `pg_stat_statements` and compare against Phase 0 baseline (same queries, 7-day window).
2. Snapshot Neon dashboard "Data transfer — last 7 days."
3. Run full test suite (`pnpm test`, `uv run --extra dev pytest tests/`).
4. Capture to `reports/post-phase-N-YYYYMMDD.md`.

### Final Success Criteria

- [ ] Total network transfer reduced ≥70% (5.5 GB → <1.5 GB)
- [ ] Per-query average payload <300 bytes
- [ ] Auth `session`-table reads ≈0 in steady-state API traffic
- [ ] `listSongsetSummaries` makes 1 query (down from 2)
- [ ] Render-job polling: ≥50% reduction in API calls for jobs >60s
- [ ] All tests pass
- [ ] No p95 latency regression
- [ ] No error-rate regression

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Webapp runs on Edge runtime → driver switch dropped | Medium | High (lose biggest win) | Phase 0 verifies first; if Edge, concentrate on projections + auth + denormalization |
| Auth middleware header spoofing | Low | High (auth bypass) | HMAC-signed header; verify signature in handler; checks fail closed to direct `auth.api.getSession` call |
| Denormalized `item_count` drift on buggy writers | Medium | Low (UI shows wrong number) | Wrap all writers in transactions; add integration test asserting sync after each CRUD op |
| Edge runtime breaks if `Pool` imported | High | High | Guarded by Phase 0 gate; do not import `Pool` until runtime confirmed |
| `COUNT(*) OVER()` performance on large tables | Low | Low | Already indexed hot queries; verified via EXPLAIN in Phase 10 |
| Render-job `RETURNING` projection breaks Python callers | Medium | Medium | Update `RenderJob` dataclass consumers; full test suite on `pipeline.py` |
| `pg_stat_statements` extension not installed | Low | Medium | Phase 0 includes extension check; fall back to Neon's built-in query insight if unavailable |

## Out of Scope

- Redis/Memcached result caching layer (separate spec)
- WebSocket/SSE for render job status (replaces Android polling — separate feature)
- Migrating off Neon Postgres
- Column removal or table restructuring (additive columns only)
- Removing Better Auth entirely
- Service Worker / offline cache strategies for the webapp
- Admin CLI bulk UPDATE optimization (DS4 P4 — separate from webapp transfer; the admin CLI uses psycopg2 over TCP, not neon-http, so its per-query overhead is much lower; will be handled in a follow-up spec if Phase 0 reveals it as significant)

## Related

- `specs/reduce-database-network-transfer-v1.md` (superseded)
- `reports/database-bandwidth-analysis-ds4.md` (DS4 agent analysis)
- `reports/database-bandwidth-analysis-gemma.md` (Gemma agent analysis)
- `specs/reduce-webapp-page-load-time.md` (page latency, related but distinct)
