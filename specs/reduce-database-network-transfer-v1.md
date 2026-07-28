# Reduce Database Network Transfer

**Status:** Implementation Plan (Not Yet Implemented)
**Created:** 2026-07-27
**Storage:** 0.17 GB → **Network Transfer:** 5.5 GB (32× amplification)

## Problem

The database layer transfers ~5.5 GB of data over the network while the actual stored data is only ~0.17 GB. This 32× amplification factor is caused by:

1. **Stateless HTTP driver** (`@vercel/postgres` / `neon-http`) opening a new HTTP connection per query with full-row serialization
2. **`SELECT *` on wide tables** — `render_jobs` has 35 columns, many with large TEXT/JSONB payloads, fetched even when only 2-3 are needed
3. **`RETURNING *` on every mutation** — INSERT/UPDATE/DELETE on `render_jobs` returns all 35 columns repeatedly
4. **Redundant queries** — `check_cancelled()` fetches the full job row, then `get_render_ratio()` fetches it again; progress updates fetch the job just to write back the same data
5. **Polling at 2s intervals** — Android app polls every 2s, each request triggers 4+ database round-trips
6. **Full column selection** — song and songset queries fetch all columns including rarely-used ones (recording, lyrics, etc.)
7. **Separate COUNT queries** — pagination uses `SELECT COUNT(*)` separately from data queries
8. **Missing index coverage** — some WHERE clauses on non-indexed columns cause full table scans

## Goals

| Metric | Current | Target |
|--------|---------|--------|
| Total network transfer | 5.5 GB | < 1.5 GB (70% reduction) |
| Per-query data transfer | ~1.2 KB avg | < 200 bytes avg |
| Render job polling round-trips | 4+ per poll | 1 per poll |
| Page load round-trips | 5+ (songsets) | 1-2 |

## Change Categories

### 1. Driver Switch: `@vercel/postgres` → `@neondatabase/serverless`

**File:** `delivery/webapp/src/db/index.ts`

**Current:** Uses `@vercel/postgres` which wraps `neon-http` — a stateless HTTP driver that opens a new HTTPS connection per query, serializes full rows to JSON, and deserializes them on the client.

**Change:** Switch to `@neondatabase/serverless` which supports connection pooling via `NeonConnectionPool` or at minimum has better batching support. Evaluate whether a persistent connection pool (via `pg` with `pg-boss` or similar) is viable in the Next.js edge/runtime context.

**Files affected:**
- `delivery/webapp/src/db/index.ts`
- `delivery/webapp/src/db/schema.ts` (verify type compatibility)
- `delivery/webapp/package.json` (dependency change)

**Measurement:**
- Before: `console.time` around `db.query()` calls, measure bytes transferred via network inspector
- After: Same instrumentation, compare total transfer size

**Tests:**
- `delivery/webapp/src/lib/db/songsets.test.ts` — verify all queries still return correct shapes
- `delivery/webapp/src/lib/db/songs.test.ts` — same
- `delivery/webapp/src/app/(app)/songs/page.test.tsx` — page renders correctly
- Integration test: verify connection pool reuses connections

**Assumptions:**
- Neon database supports both drivers (it does — same underlying protocol)
- Edge runtime compatibility is maintained

### 2. Render Worker: Replace `SELECT *` / `RETURNING *` with Projections

**File:** `delivery/render-worker/src/sow_render_worker/db.py`

**Current:** Every operation on `render_jobs` uses `SELECT *` and `RETURNING *`. The table has 35 columns including large TEXT fields (`ffmpeg_args_json`, `error_message`, `tags`) and JSONB (`progress_data`).

**Change:** Replace all `SELECT *` with explicit column lists. For mutations, use `RETURNING` with only the columns the caller needs.

**Specific changes:**
```python
# Before:
cursor.execute("INSERT INTO render_jobs (...) VALUES (...) RETURNING *")
# After:
cursor.execute("""
    INSERT INTO render_jobs (songset_id, status, created_at, updated_at)
    VALUES (...) RETURNING id, status, created_at, updated_at
""")

# Before:
cursor.execute("SELECT * FROM render_jobs WHERE id = %s")
# After (for status check):
cursor.execute("SELECT status, progress_data FROM render_jobs WHERE id = %s")
```

**Files affected:**
- `delivery/render-worker/src/sow_render_worker/db.py`
- `delivery/render-worker/src/sow_render_worker/pipeline.py` (update callers to match new return shapes)

**Measurement:**
- Measure average row size before/after via `cursor.description` and row byte sizes
- Target: < 200 bytes per render_jobs row transfer (from ~1.2 KB)

**Tests:**
- `delivery/render-worker/tests/test_db.py` — verify all DB operations return correct shapes
- `delivery/render-worker/tests/test_pipeline.py` — verify pipeline still works with projected columns
- `delivery/render-worker/tests/test_video_engine.py` — verify video engine integration

**Assumptions:**
- psycopg2 supports column-projection queries (it does)
- All callers of `db.py` functions are updated to handle new return shapes

### 3. Render Worker: Eliminate Redundant Queries

**File:** `delivery/render-worker/src/sow_render_worker/pipeline.py`

**Current patterns:**
- `check_cancelled()` calls `get_render_job()` which does `SELECT * FROM render_jobs WHERE id = %s`
- `get_render_ratio()` is called twice in `process_render_job()` — once before processing, once after
- Progress update queries fetch the full row, modify it in Python, then write it back

**Change:**
1. Inline `check_cancelled()` to do a lightweight `SELECT status FROM render_jobs WHERE id = %s`
2. Cache the render ratio from the first `get_render_ratio()` call
3. Use `UPDATE ... SET progress_data = jsonb_set(progress_data, ...)` without fetching first

**Specific changes:**
```python
# Before (check_cancelled):
def check_cancelled(job_id):
    job = get_render_job(job_id)  # SELECT *
    return job.status == 'cancelled'

# After:
def check_cancelled(job_id):
    cursor.execute("SELECT status FROM render_jobs WHERE id = %s", (job_id,))
    row = cursor.fetchone()
    return row and row[0] == 'cancelled'

# Before (get_render_ratio called twice):
ratio1 = get_render_ratio(job_id)  # SELECT *
# ... processing ...
ratio2 = get_render_ratio(job_id)  # SELECT * again

# After:
ratio = get_render_ratio(job_id)  # once
# ... processing ...
# use ratio
```

**Files affected:**
- `delivery/render-worker/src/sow_render_worker/pipeline.py`
- `delivery/render-worker/src/sow_render_worker/db.py` (add lightweight query functions)

**Measurement:**
- Count queries per render job before/after
- Target: 60% reduction in queries per job (from ~15 to ~6)

**Tests:**
- `delivery/render-worker/tests/test_pipeline.py` — verify cancellation detection still works
- `delivery/render-worker/tests/test_pipeline.py` — verify ratio caching doesn't break progress tracking

### 4. Render Worker: Optimize Progress Updates

**File:** `delivery/render-worker/src/sow_render_worker/pipeline.py`

**Current:** Progress is updated every 5 seconds. Each update does:
1. `SELECT * FROM render_jobs WHERE id = %s` (fetch full row)
2. Modify `progress_data` in Python
3. `UPDATE render_jobs SET ... RETURNING *` (write full row back)

**Change:** Use `UPDATE ... SET progress_data = jsonb_set(progress_data, '{step}', '{value}') WHERE id = %s` to avoid fetching first. Only fetch when the UI needs the full job state.

**Files affected:**
- `delivery/render-worker/src/sow_render_worker/pipeline.py`

**Measurement:**
- Measure bytes transferred per progress update before/after
- Target: ~500 bytes per update (from ~1.5 KB)

**Tests:**
- `delivery/render-worker/tests/test_pipeline.py` — verify progress updates still work correctly

### 5. Android: Increase Polling Interval with Exponential Backoff

**File:** `delivery/android/app/src/main/java/org/streamofworship/android/feature/render/RenderViewModel.kt`

**Current:** Polls render job status every 2 seconds with `maxRetries = 10` (20 seconds total).

**Change:** Implement exponential backoff:
- First 10 seconds: 2s interval (responsive for quick jobs)
- Next 30 seconds: 5s interval (moderate)
- After 40 seconds: 10s interval (long-running jobs)
- Max total polling: 120 seconds

**Specific changes:**
```kotlin
// Before:
private val POLL_INTERVAL_MS = 2000L
private val MAX_RETRIES = 10

// After:
private fun getPollInterval(elapsedMs: Long): Long = when {
    elapsedMs < 10_000 -> 2000L
    elapsedMs < 40_000 -> 5000L
    else -> 10000L
}
```

**Files affected:**
- `delivery/android/app/src/main/java/org/streamofworship/android/feature/render/RenderViewModel.kt`
- `delivery/android/app/src/main/java/org/streamofworship/android/feature/render/RenderScreen.kt` (if polling logic is there)

**Measurement:**
- Count API calls per render job before/after
- Target: 50% reduction in API calls (from ~100 per long job to ~50)

**Tests:**
- `delivery/android/app/src/test/java/.../RenderViewModelTest.kt` — verify backoff logic
- Manual test: verify UI responsiveness for short jobs (< 10s)

### 6. Webapp: Consolidate Page Load Queries

**File:** `delivery/webapp/src/lib/db/songsets.ts`

**Current:** `getRenderPageData()` makes 5 sequential database round-trips:
1. `SELECT * FROM songsets WHERE user_id = $1 ORDER BY ...` (pagination)
2. `SELECT COUNT(*) FROM songsets WHERE user_id = $1`
3. `SELECT * FROM songs WHERE id IN ($1, $2, ...)` (for each songset's songs)
4. `SELECT * FROM songs WHERE id = $1` (for user's songs list)
5. `SELECT * FROM render_jobs WHERE user_id = $1 AND status = 'running'`

**Change:** Create a single `getRenderPageDataRaw()` function that does one query with JOINs and `GROUP_BY` to fetch songsets + their songs + counts in one round-trip. Use client-side grouping in TypeScript.

**Specific changes:**
```typescript
// Before: 5 round-trips
async function getRenderPageData(userId, page, perPage) {
  const songsets = await db.query.songsets.findMany({...});
  const total = await db.query.songsets.count({...});
  const songs = await db.query.songs.findMany({...});
  const userSongs = await db.query.songs.findMany({...});
  const runningJobs = await db.query.renderJobs.findMany({...});
  return { songsets, total, songs, userSongs, runningJobs };
}

// After: 2 round-trips
async function getRenderPageData(userId, page, perPage) {
  const [songsetData, runningJobs] = await Promise.all([
    getRenderPageDataRaw(userId, page, perPage),  // 1 query with JOINs
    db.query.renderJobs.findMany({...})  // 1 query for running jobs
  ]);
  return songsetData;
}
```

**Files affected:**
- `delivery/webapp/src/lib/db/songsets.ts`
- `delivery/webapp/src/app/(app)/render/page.tsx` (update caller if needed)

**Measurement:**
- Count database round-trips per page load before/after
- Target: 60% reduction (from 5 to 2)

**Tests:**
- `delivery/webapp/src/lib/db/songsets.test.ts` — verify data shape matches
- `delivery/webapp/src/app/(app)/render/page.test.tsx` — page renders correctly

**Deprecated code to remove:**
- `getSongset()` (N+1 pattern, already deprecated)
- `computeRenderState()` (N+1 pattern, already deprecated)

### 7. Webapp: Column Projection for Songs and Search

**File:** `delivery/webapp/src/lib/db/songs.ts`

**Current:** Song queries select all columns including `recording` (large binary reference), `lyrics` (large TEXT), `lyrics_lrc` (large TEXT), and `analysis` (JSONB). Most pages only need `id`, `title`, `artist`, `key`, `tempo`, `duration_ms`.

**Change:** Create typed projection functions:
```typescript
// Before:
const songs = await db.query.songs.findMany({
  where: eq(songsTable.userId, userId),
});

// After:
const songs = await db.query.songs.findMany({
  columns: {
    id: true,
    title: true,
    artist: true,
    key: true,
    tempo: true,
    duration_ms: true,
  },
  where: eq(songsTable.userId, userId),
});
```

**Files affected:**
- `delivery/webapp/src/lib/db/songs.ts`
- `delivery/webapp/src/lib/db/search.ts`
- `delivery/webapp/src/app/(app)/songs/page.tsx` (update if it needs full columns)
- `delivery/webapp/src/app/(app)/render/page.tsx` (update if it needs full columns)

**Measurement:**
- Measure average song row size before/after
- Target: ~80% reduction per row (from ~2 KB to ~400 bytes)

**Tests:**
- `delivery/webapp/src/lib/db/songs.test.ts` — verify all query shapes
- `delivery/webapp/src/lib/db/search.test.ts` — verify search still works

### 8. Webapp: Eliminate Separate COUNT Queries

**File:** `delivery/webapp/src/lib/db/songs.ts`, `delivery/webapp/src/lib/db/search.ts`

**Current:** Pagination uses separate `SELECT COUNT(*)` queries:
```typescript
const [items, countResult] = await Promise.all([
  db.query.songs.findMany({ limit, offset, ... }),
  db.query.songs.count({ where: ... }),
]);
```

**Change:** For most cases, use a sentinel row approach — fetch `limit + 1` rows and check if the array length exceeds the limit. This eliminates the COUNT query entirely. For cases where the exact count is needed (e.g., "Page 3 of 12"), use database `COUNT(*) OVER()` window function.

**Specific changes:**
```typescript
// Before: 2 queries
const [items, total] = await Promise.all([
  db.query.songs.findMany({ limit, offset, where }),
  db.query.songs.count({ where }),
]);

// After: 1 query (sentinel approach)
const items = await db.query.songs.findMany({
  limit: limit + 1,
  offset,
  where,
});
const hasMore = items.length > limit;
if (hasMore) items.pop();
```

**Files affected:**
- `delivery/webapp/src/lib/db/songs.ts`
- `delivery/webapp/src/lib/db/search.ts`
- `delivery/webapp/src/lib/db/songsets.ts`

**Measurement:**
- Count queries per paginated page load before/after
- Target: 40% reduction in queries

**Tests:**
- `delivery/webapp/src/lib/db/songs.test.ts` — verify pagination still works
- `delivery/webapp/src/lib/db/search.test.ts` — verify search pagination

### 9. Webapp: Optimize Render Job Creation Path

**File:** `delivery/webapp/src/app/api/render-jobs/route.ts`

**Current:** POST handler makes 4+ round-trips:
1. Validate songset exists
2. Check for existing running render job
3. INSERT new render job
4. Publish to SQS

**Change:** Combine validation and insertion. Use a single `INSERT ... SELECT` with a `WHERE NOT EXISTS` clause to atomically check for existing running jobs and create a new one.

**Specific changes:**
```sql
-- Before: 4 round-trips
-- 1. SELECT * FROM songsets WHERE id = $1
-- 2. SELECT * FROM render_jobs WHERE songset_id = $1 AND status = 'running'
-- 3. INSERT INTO render_jobs ...
-- 4. SQS publish

-- After: 2 round-trips
-- 1. INSERT INTO render_jobs
--     SELECT $1, 'pending', ...
--     WHERE EXISTS (SELECT 1 FROM songsets WHERE id = $1)
--       AND NOT EXISTS (SELECT 1 FROM render_jobs WHERE songset_id = $1 AND status = 'running')
--     RETURNING id, status
-- 2. SQS publish (only if INSERT succeeded)
```

**Files affected:**
- `delivery/webapp/src/app/api/render-jobs/route.ts`

**Measurement:**
- Count round-trips per render job creation before/after
- Target: 50% reduction (from 4 to 2)

**Tests:**
- `delivery/webapp/src/app/api/render-jobs/route.test.ts` — verify atomicity
- `delivery/webapp/src/app/api/render-jobs/route.test.ts` — verify duplicate prevention

### 10. Index Verification

**File:** `delivery/webapp/drizzle/0014_page_load_hot_path_indexes.sql`

**Current indexes (verify coverage):**
- `idx_render_jobs_user_id_status` — covers `WHERE user_id = ? AND status = ?`
- `idx_render_jobs_songset_id_status` — covers `WHERE songset_id = ? AND status = ?`
- `idx_songsets_user_id` — covers `WHERE user_id = ?`
- `idx_songs_user_id` — covers `WHERE user_id = ?`

**Change:** Verify all WHERE clauses in the codebase are covered by indexes. Add any missing indexes.

**Files affected:**
- `delivery/webapp/drizzle/XXXX_add_missing_indexes.sql` (new migration)

**Measurement:**
- Run `EXPLAIN ANALYZE` on all hot queries before/after
- Target: All hot queries use index scans (not sequential scans)

**Tests:**
- `delivery/webapp/src/lib/db/index.test.ts` — verify query plans use indexes

## Implementation Order

Changes are ordered by impact and risk:

| Phase | Changes | Impact | Risk |
|-------|---------|--------|------|
| 1 | #2 (render worker projections), #3 (redundant queries), #4 (progress updates) | High (render worker is the biggest transfer source) | Low (isolated to render worker) |
| 2 | #10 (index verification) | Medium | Low (read-only changes) |
| 3 | #7 (column projection), #8 (COUNT elimination) | Medium | Low (additive changes, backward compatible) |
| 4 | #6 (page load consolidation), #9 (render creation) | High | Medium (requires careful testing of query shapes) |
| 5 | #1 (driver switch) | Medium | Medium (driver change, need to verify all edge cases) |
| 6 | #5 (Android polling) | Low | Low (client-side change, no DB impact) |

## Rollback Plan

Each change is independently reversible:
- **Projections (#2-4):** Revert to `SELECT *` — no data loss, just more transfer
- **Driver switch (#1):** Revert to `@vercel/postgres` — same database, different driver
- **Column projection (#7):** Revert to full column selection — no data loss
- **COUNT elimination (#8):** Revert to separate COUNT queries — extra query but correct behavior
- **Query consolidation (#6, #9):** Revert to separate queries — more round-trips but correct behavior
- **Android polling (#5):** Revert to 2s interval — more API calls but responsive UI
- **Indexes (#10):** Drop indexes if they cause write performance issues

## Assumptions

1. **Neon database supports connection pooling** — verified, `@neondatabase/serverless` supports `NeonConnectionPool`
2. **psycopg2 supports column projections** — verified, standard PostgreSQL behavior
3. **Android exponential backoff is acceptable UX** — 2s→5s→10s backoff is standard practice
4. **Sentinel pagination is acceptable** — loses exact page count but this is a common tradeoff
5. **`INSERT ... SELECT ... WHERE NOT EXISTS` is supported by Neon** — standard PostgreSQL feature
6. **Next.js edge runtime is compatible with chosen driver** — needs verification during implementation
7. **All render worker callers can be updated** — render worker is a single Python package

## Measurement Strategy

### Before Implementation

1. **Enable query logging on Neon:**
   ```sql
   ALTER DATABASE stream_of_worship SET log_statement = 'mod';
   ```

2. **Capture baseline metrics:**
   - Total network transfer from Neon dashboard (last 7 days)
   - Average query size via `pg_stat_statements`
   - Round-trip count per page load via browser DevTools Network panel
   - Render job polling count via Android network monitoring

3. **Run `EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)` on all hot queries**

### After Each Phase

1. **Compare network transfer** — Neon dashboard, same time window
2. **Compare query counts** — `pg_stat_statements`
3. **Compare page load metrics** — browser DevTools, Lighthouse
4. **Run all existing tests** — ensure no regressions

### Success Criteria

- [ ] Total network transfer reduced by ≥ 70% (5.5 GB → < 1.5 GB)
- [ ] Average per-query transfer < 200 bytes
- [ ] Render job polling round-trips ≤ 1 per poll
- [ ] Page load round-trips ≤ 2
- [ ] All existing tests pass
- [ ] No increase in p95 query latency
- [ ] No increase in error rate

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Driver switch breaks edge runtime compatibility | Medium | High | Keep `@vercel/postgres` as fallback, feature flag |
| Column projection breaks downstream code | High | Medium | Comprehensive test coverage, gradual rollout |
| Sentinel pagination loses page count UX | Low | Low | Add optional COUNT query for specific pages that need it |
| Indexes slow down writes | Low | Medium | Monitor write latency, drop if needed |
| Android backoff feels unresponsive | Low | Low | Keep 2s interval for first 10 seconds |
| `INSERT ... SELECT` atomicity issues | Low | High | Test with concurrent requests, add retry logic |

## Out of Scope

- Database schema changes (adding/removing columns)
- Caching layer (Redis/Memcached) — this is a separate optimization
- Query result caching — separate from reducing per-query transfer
- Migration to a different database system
- Changing the Android app's polling mechanism to WebSocket/SSE (separate feature)

## Related Specs

- `specs/reduce-webapp-page-load-time.md` — related but focuses on latency, not transfer size
