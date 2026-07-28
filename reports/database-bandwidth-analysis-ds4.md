# Database Network Transfer Optimization Analysis

## Executive Summary

Your Neon Postgres database shows a **32x bandwidth amplification ratio**: 0.17 GB stored versus 5.5 GB network transfer this month. This document identifies the root causes and provides prioritized recommendations to reduce network bandwidth.

---

## Architecture Overview

The project uses three distinct database connection patterns across its components:

| Component | Driver | Connection Type | Bandwidth Impact |
|---|---|---|---|
| **Webapp (Next.js)** | `@neondatabase/serverless` v1.1.0 (neon-http) | HTTP — each query is a separate HTTPS request | **Critical** — HTTP overhead per query |
| **Admin CLI** | `psycopg` (binary protocol) via `ops/admin-cli/src/stream_of_worship/db/connection.py` | Persistent TCP connection | Low |
| **Render Worker** | `psycopg2-binary` via `delivery/render-worker/src/sow_render_worker/db.py` | Persistent TCP connection | Low |

**Root cause**: The webapp's use of `neon-http` (see `delivery/webapp/src/db/index.ts`) generates a separate HTTPS request for every database query. For small queries (single-row lookups, boolean checks, tiny result sets), the HTTP framing (headers, TLS overhead, JSON envelope) can exceed the actual data payload.

---

## Top 5 Bandwidth Consumers

### 1. Auth Session Verification on Every API Request

**File**: All 24 route handlers in `delivery/webapp/src/app/api/`

Every API route calls `auth.api.getSession()` at the top of its handler. Better Auth's Drizzle adapter hits the `session` table and optionally the `user` table for every single request.

**Affected routes**:
- `GET /api/songs` (list + count queries)
- `GET /api/songs/search` (full-text search)
- `GET /api/songs/search/semantic` (pgvector search)
- `GET /api/songsets` (list + aggregation)
- `GET /api/songsets/[id]` (editor data)
- `POST /api/songsets` (create)
- `PATCH /api/songsets/[id]` (update)
- `DELETE /api/songsets/[id]` (delete)
- `GET /api/songsets/[id]/items`
- `POST /api/songsets/[id]/items`
- `PATCH /api/songsets/[id]/items`
- `DELETE /api/songsets/[id]/items`
- `POST /api/songsets/[id]/duplicate`
- `GET /api/render-jobs`
- `POST /api/render-jobs`
- `GET /api/render-jobs/[id]`
- `DELETE /api/render-jobs/[id]`
- `GET /api/render-jobs/[id]/artifact-sizes`
- `GET /api/share` (create)
- `GET /api/share/[token]`
- `GET /api/signed-url`
- `GET /api/offline/cache`
- `GET /api/settings`
- `PATCH /api/settings`
- `GET /api/transitions/preview`
- `GET /api/songs/albums`
- `GET /api/songs/[id]`
- `PATCH /api/lyrics/marks`
- `PATCH /api/lyrics/overrides`

**Compounding factor**: Server-side rendered pages also call `auth.api.getSession()` before the client-side fetch repeats the same auth check. Pages like `/songsets/page.tsx`, `/songsets/[id]/page.tsx`, and `/songsets/[id]/render/page.tsx` generate **double auth queries** per navigation.

**Example**: A user loading the home page, opening editor, and performing one search triggers 8+ auth-related database queries.

**Estimated bandwidth**: ~2-4 KB per auth check × ~8 queries per typical page session = **16-32 KB/page-session just for authentication**.

---

### 2. Render Worker Frequent Progress Updates

**File**: `delivery/render-worker/src/sow_render_worker/pipeline.py`

The render worker calls `update_render_progress()` during rendering:
- **Every 5 seconds** during the video encoding phase (lines 464-478 in `pipeline.py`)
- At each phase transition (lines 281, 327, 376, 415, 510)
- Additional `get_render_job` reads for validation (lines 217, 234, 564)

**Per render job estimate** (typical 2-4 minutes):
- Phase transition updates: ~5 calls
- Video encoding progress polls (every 5s): ~24-48 calls
- `get_render_job` read checks: ~3 calls
- `get_render_ratio` historical queries: 2 calls
- `complete_render_job`: 2 UPDATE statements in a transaction
- Total: **~36-60 DB round-trips per render job**

Each `update_render_progress()` call uses `RETURNING *` (see `delivery/render-worker/src/sow_render_worker/db.py` line 298), sending the full `render_jobs` row (~35 columns, ~2-3 KB) back for every update, even though only `phase`, `elapsed_seconds`, and `percent_complete` changed.

---

### 3. Wide Column Fetches on Catalog Tables

Both the webapp and admin CLI fetch **all columns** from songs and recordings, including large text and JSON blob fields:

| Table | Large Fields | Estimated Size per Row |
|---|---|---|
| `songs` | `lyrics_raw`, `lyrics_lines`, `sections` | 3-15 KB |
| `recordings` | `beats`, `downbeats`, `sections`, `key_candidates` | 1-5 KB |

**Key files**:
- `admin/db/schema.py`: `SONG_COLUMNS_SELECT`, `RECORDING_COLUMNS_SELECT` include all fields
- `delivery/webapp/src/lib/db/songs.ts`: `mapSongWithRecordings()` fetches all columns

When `listSongs(limit=50)` returns results, it pulls ~50 songs × ~2-10 KB of lyrics data per row = **100-500 KB per call**, plus HTTP framing.

This pattern is repeated across:
- `listSongs()` in the browse page
- `searchSongs()` in the search page
- `fullTextSearchSongs()` in the search API
- `MapSongWithRecordings()` in the play/editor pages

---

### 4. Complex Multi-Table Aggregation Queries

#### `listSongsetSummaries()` — Songset list view

**File**: `delivery/webapp/src/lib/db/songsets.ts` (line 171)

```sql
SELECT songsets.*, songset_items.*, recordings.*, render_jobs.*
FROM songsets
LEFT JOIN songset_items ON songset_items.songset_id = songsets.id
LEFT JOIN recordings ON recordings.hash_prefix = songset_items.recording_hash_prefix
LEFT JOIN render_jobs ON render_jobs.id = songsets.latest_render_job_id
WHERE songsets.user_id = ?
GROUP BY songsets.id, render_jobs.id
```

Joins **4 tables** with aggregates (`COUNT()`, `SUM()`, `MAX()`, `LEFT JOIN`). The full result set includes all item rows per songset, recording data, and render job metadata.

This function is called twice per page load (server-side SSR + client-side fetch) when viewing the songset list.

#### `getSongsetEditorData()` — Songset editor page

**File**: `delivery/webapp/src/lib/db/songsets.ts` (line 297)

Two separate database queries:
1. Fetch the songset row + latest render job (1 query, 2 tables joined)
2. Fetch all items with left-joins to songs, recordings, and lyricMarks count (1 query, 4 tables joined)

The second query has a `GROUP BY` on all selected columns (lines 351-380 in `songsets.ts`) to count distinct lyric marks.

#### `getRenderPageData()` — Render page

**File**: `delivery/webapp/src/lib/db/songsets.ts` (line 480)

Runs **4 independent queries**:
1. Songset row + render job
2. Items with lyricMarks count + duration
3. User settings (one query per user)
4. Latest render job
5. Previous completed render job

These could potentially be consolidated into fewer queries per page load.

---

### 5. Admin CLI Bulk UPDATE Patterns

**File**: `ops/admin-cli/src/stream_of_worship/admin/db/client.py` (line 1961)

The admin CLI issues individual UPDATE statements per recording when updating status fields:

```python
# Each recording gets its own UPDATE:
UPDATE recordings SET analysis_status = 'completed' WHERE hash_prefix = ?
UPDATE recordings SET lrc_status = 'completed' WHERE hash_prefix = ?
UPDATE recordings SET visibility_status = 'review' WHERE hash_prefix = ?
```

For a catalog of ~500+ recordings, running batch status updates generates 500+ round-trips.

---

## Database Schema Details

### Tables & Column Profile

```
songs (catalog, managed by admin CLI, read-only from webapp)
  id: text PK
  title, title_pinyin, composer, lyricist: text
  album_name, album_series: text
  musical_key, musical_key_root, musical_key_mode: text
  musical_key_start/end_root, musical_key_start/end_pitch_class: text/integer
  musical_key_parse_status: text
  lyrics_raw: text        ← LARGE (2-10 KB)
  lyrics_lines: text      ← LARGE (1-5 KB, JSON array)
  sections: text          ← LARGE (JSON array)
  source_url, scraped_at: text (not null)
  table_row_number, deleted_at: integer, timestamp
  search_vector: tsvector (GIN indexed)
  created_at, updated_at: timestamp

recordings
  content_hash: text PK
  song_id: text FK → songs
  original_filename, file_size_bytes, imported_at: text/integer
  r2_audio_url, r2_stems_url, r2_lrc_url: text
  duration_seconds, tempo_bpm, loudness_db: real
  musical_key, musical_mode, key_confidence: text/real
  key_algorithm_version, key_score_margin, key_window_agreement: text/real
  key_candidates: text    ← LARGE (JSON blob)
  beats: text             ← LARGE (JSON array of beat timings)
  downbeats: text         ← LARGE (JSON array)
  sections: text          ← LARGE (JSON array of section markers)
  embeddings_shape: text
  analysis_status, lrc_status: text (defaults: "pending")
  analysis_job_id, lrc_job_id: text
  visibility_status, download_status: text
  deleted_at, created_at, updated_at: timestamp
  youtube_url: text

songsets (per-user)
  id: text PK
  user_id: bigint FK → users
  name, description: text
  latest_render_job_id, last_failed_render_job_id, last_completed_render_job_id: text
  created_at, updated_at: timestamp

songset_items (per-user)
  id: text PK
  songset_id: text FK → songsets
  song_id: text (FK to songs, not enforced)
  recording_hash_prefix: text FK → recordings
  position: integer (not null)
  gap_beats, crossfade_duration_seconds, tempo_ratio: real
  crossfade_enabled, key_shift_semitones: integer
  created_at, updated_at: timestamp

render_jobs
  id: text PK
  songset_id, user_id: text/bigint FK
  status: text (default: "queued")
  phase, phase_index, total_phases: text/integer
  percent_complete, estimated_seconds_left, elapsed_seconds: real
  error_message: text
  estimated_total_seconds, total_duration_seconds: real
  started_at, completed_at: timestamp
  template, resolution: text
  audio_enabled, video_enabled: boolean
  font_size_preset, font_family, include_title_card: text/boolean
  title_card_duration_seconds, title_card_lines: real/text
  song_count, songset_duration_seconds: integer
  mp3_r2_key, mp4_r2_key, chapters_r2_key: text
  created_at, updated_at: timestamp

user_settings
  user_id: bigint PK FK → users
  offline_auto_cache: boolean
  default_gap_beats, default_resolution, default_font_size_preset, default_font_family: text/real
  default_video_template, lyrics_loop_window_seconds: text/real
  default_key_shift_semitones: integer
  timing_review_font: text
  created_at, updated_at: timestamp

user_lrc_override
  id: text PK
  user_id: bigint FK → users
  recording_content_hash: text FK → recordings
  lrc_content: text ← POTENTIALLY LARGE
  created_at, updated_at: timestamp

lyric_mark
  id: text PK
  user_id: bigint FK → users
  recording_content_hash: text FK → recordings
  timestamp_seconds: double precision
  created_at: timestamp

songset_share
  token: text PK
  songset_id: text FK → songsets
  render_job_id: text
  created_by_user_id: bigint FK → users
  allow_download, expires_at, revoked_at: boolean/timestamp
  created_at: timestamp

client_error_log
  id: serial PK
  ip_hash, message, kind: text
  meta_json: text
  created_at: timestamp

Better Auth tables (user, account, session, verification)
  session — contains token, expires_at, user_id, ip, user_agent
```

### Critical Indexes

```
songs.search_vector: GIN index on tsvector (full-text search)
recordings.song_id, visibility_status, deleted_at: composite index
songs.updated_at: descending (used in ordering)
songsets.user_id, updated_at: composite index
songset_items.songset_id, position: composite index
songset_items.songset_id, updated_at: composite index
song_embedding.embedding: pgvector cosine index (1536 dimensions)
song_line_embedding.song_id: index
song_line_embedding.embedding: pgvector cosine index (1536 dimensions)
render_jobs.songset_id, created_at: composite index
render_jobs.status, updated_at: composite index
idx_client_error_log_created: timestamp index
```

---

## Estimated Bandwidth Breakdown

### Per Request (Webapp API)

| API Call | Approx. Requests to DB | Estimated Bandwidth |
|---|---|---|
| Each API route call | 1 (auth check) | ~2-4 KB |
| GET /api/songs (limit=50) | 1 auth + 1 songs list + 1 count = 3 | ~300-500 KB (songs+recordings) |
| GET /api/songs/search | 1 auth + 1 search + 1 count = 3 | ~300-500 KB + HTTP overhead |
| GET /api/songsets (limit=50) | 1 auth + 1 complex join + 1 count = 3 | ~200-400 KB |
| GET /api/songsets/[id] | 1 auth + 1 songset + 1 items join = 3 | ~50-200 KB |
| POST /api/render-jobs | 1 auth + 1 items list + 1 active check + 1 INSERT = 4 | ~20-50 KB |
| GET /api/render-jobs/[id] | 1 auth + 1 render job = 2 | ~5-10 KB |
| Semantic search | 1 auth + 1 vector search + 1 line embeddings = 3 | ~200-500 KB |

### Per Render Job (render-worker)

| Phase | DB Calls | Estimated Bandwidth |
|---|---|---|
| start_render_job | 1 UPDATE + RETURNING * | ~10 KB |
| fetch_songset_items | 1 SELECT with 2 JOINs | ~10-50 KB |
| get_render_ratio | 1 SELECT (aggregation) | ~1-2 KB |
| Phase transition updates (5) | 5 × UPDATE + RETURNING * | ~50-100 KB |
| Video encoding polls (24-48) | 24-48 × UPDATE + RETURNING * | ~240-480 KB |
| get_render_job checks (3) | 3 × SELECT | ~15-30 KB |
| complete/fail_render_job (2) | 2 × UPDATE (transactions) | ~20-40 KB |
| **Total** | **~36-60** | **~350-710 KB** |

### Monthly Projection

If a typical user loads 50 pages per month and triggers 5 render jobs:
- Auth checks: 50 pages × 4 auth queries/page × 3 KB = **600 KB**
- Data queries: 50 pages × 250 KB average = **12.5 MB**
- Render progress: 5 renders × 530 KB average = **2.65 MB**

This doesn't account for the admin CLI bulk operations or batch LRC generation, which can move 10-100 MB per run.

---

## Recommendations

### 🔴 Priority 0

**Switch from neon-http to neon-ws (WebSocket) driver**

**File**: `delivery/webapp/src/db/index.ts`

Current (line 3):
```typescript
import { neon } from "@neondatabase/serverless";
import { drizzle } from "drizzle-orm/neon-http";
```

Recommended:
```typescript
import { neonConfig, Pool } from "@neondatabase/serverless";
import { drizzle } from "drizzle-orm/neon-serverless";

neonConfig.wsProxy = (host) => `${host}:5432/v1`;

const pool = new Pool({ connectionString: process.env.SOW_DATABASE_URL });
export const db = drizzle(pool, { schema });
```

**Rationale**: `neon-http` makes each query a separate HTTPS request. The `@neondatabase/serverless` WebSocket driver (`@neondatabase/serverless` also exports a `Pool` class that uses a persistent WebSocket connection to the Neon server — no per-query TLS handshakes or repeated HTTP headers.)

**Impact**: Eliminates ~500-1000 bytes of HTTP framing overhead on every single query. For an application making hundreds of small queries per page load, this is the single biggest bandwidth saver.

---

### 🟠 Priority 1

**Batch auth session verification**

**Files**: All 24 route handlers + server layouts

Problem: `auth.api.getSession()` is called independently in every handler, each triggering a database query to the `session` and potentially `user` tables.

Solution A — Middleware:
Add a Next.js middleware that runs `auth.api.getSession()` once per request:

```typescript
// middleware.ts (Next.js)
import { auth } from "@/lib/auth";
export async function middleware(request: NextRequest) {
  const session = await auth.api.getSession({ headers: request.headers });
  const response = NextResponse.next();
  // Attach session to headers so route handlers can read without hitting DB
  response.headers.set("x-auth-session", JSON.stringify(session));
  return response;
}
```

Route handler then extracts from headers instead of calling `auth.api.getSession()`.

Solution B — Server Components:
For server-rendered pages, the auth check is already done during rendering. Client-side fetching should avoid re-authenticating by passing the session token.

**Impact**: Halves auth-related database queries for pages that double-call.

---

### 🟠 Priority 2

**Reduce width of SELECT columns in API queries**

**Files**:
- `delivery/webapp/src/lib/db/songs.ts` — listSongs, searchSongs, fullTextSearchSongs
- `ops/admin-cli/src/stream_of_worship/admin/db/schema.py` — SONG_COLUMNS_SELECT, RECORDING_COLUMNS_SELECT
- `delivery/webapp/src/lib/db/songsets.ts` — listSongsetSummaries, getSongsetEditorData

Problem: Many queries use `select()` with all columns instead of `columns()` with specific fields. In Drizzle ORM, `db.query.songs.findMany({ columns: { id: true, title: true, albumName: true } })` fetches only the specified columns.

Example — listSongs currently fetches:
- songs table: all ~25 columns including lyrics_raw, lyrics_lines, sections (large blobs)
- recordings table: all ~35 columns including beats, downbeats, sections, key_candidates

Change to only request displayed fields: id, title, composer, albumName, musicalKey, and recordings: durationSeconds, tempoBpm, musicalKey, r2AudioUrl, visibilityStatus.

Similarly for the render worker:
- `update_render_progress` uses `RETURNING *` — change to `RETURNING id, status` or simply check `rowcount > 0`.

**Impact**: Reduces per-query response size by 60-80% for catalog and songset listing queries.

---

### 🟠 Priority 3

**Reduce render progress update frequency**

**File**: `delivery/render-worker/src/sow_render_worker/pipeline.py` (line 464)

Current: Every 5 seconds during video encoding.

Options:
1. **Increase interval to 10-15 seconds**: Still responsive enough for a 2-4 minute render job
2. **Batch updates**: Accumulate progress data and write once per phase instead of per-frame
3. **Skip if no change**: Compare new values against cached values, only update if something changed

Example:
```python
if now - _last_video_db_update_time >= 30:  # Changed from 5 to 30 seconds
    ...
```

**Impact**: For a 3-minute render job with 40 progress updates, this reduces to ~6 updates — saving ~1.5 MB in unnecessary transferred bytes.

---

### 🟡 Priority 4

**Batch admin CLI UPDATE operations**

**File**: `ops/admin-cli/src/stream_of_worship/admin/db/client.py`

Replace individual `UPDATE ... WHERE hash_prefix = ?` loops with `ANY` or `IN` clauses:

```python
# Instead of:
for rec in recordings:
    cursor.execute(
        "UPDATE recordings SET analysis_status = %s WHERE hash_prefix = %s",
        (status, rec.hash_prefix),
    )

# Do:
cursor.execute(
    "UPDATE recordings SET analysis_status = %s WHERE hash_prefix = ANY(%s)",
    (status, [r.hash_prefix for r in recordings]),
)
```

Also check the admin CLI's `bulk_update_analysis_status`, `bulk_update_lrc_status`, etc. for similar patterns.

**Impact**: For bulk operations on 500+ recordings, reduces from 500 round-trips to 1-3.

---

### 🟡 Priority 5

**Cache songset list data**

**File**: `delivery/webapp/src/lib/db/songsets.ts` — `listSongsetSummaries()`

The songset list (showing render state, duration, item count) changes infrequently. Clients navigating between list view and editor view trigger duplicate queries.

Client-side options in Next.js App Router:
1. Use React Query / SWR with 30-60 second stale-while-revalidate
2. Use `revalidatePath()` or SWR's `mutate()` from the songset editor page to invalidate cached data after edits

Server-side (Next.js):
```typescript
export const revalidate = 60; // Revalidate songset list every 60 seconds
```

**Impact**: Eliminates redundant re-fetch of the same data during a user session.

---

### 🟡 Priority 6

**Consolidate multi-query page loads**

**Files**:
- `delivery/webapp/src/lib/db/songsets.ts` — `getRenderPageData()` (5 independent queries)
- `delivery/webapp/src/lib/db/songsets.ts` — `getSongsetEditorData()` (can be 2 queries)

Consider using database transactions or combining queries:

For `getRenderPageData()`, replace the 5 separate queries with a single query that joins songset → items → song → recording, plus the user settings in parallel (which cannot be avoided due to the cross-table join).

---

## Monitoring Recommendations

1. **Enable Neon's database query logging**: Set `log_statement = 'all'` in Neon project settings temporarily to identify the heaviest queries in actual production usage.

2. **Add query-level metrics in the webapp**: In `delivery/webapp/src/lib/db/index.ts` or the Drizzle adapter, add a request interceptor to log query counts and response sizes.

3. **Monitor per-route DB call counts**: Currently the webapp makes many independent queries per API call. Track the number of DB calls per route per request to identify hotspots.

4. **Neon dashboard**: Neon's console shows data transfer per project. Compare pre/post optimization numbers after each change.

---

## Implementation Risk Assessment

| Recommendation | Risk | Effort | Estimated Bandwidth Savings |
|---|---|---|---|
| P0: Switch to WebSocket driver | Low | 2 hours | 40-60% of webapp bandwidth |
| P1: Batch auth | Low | 1 hour | 20-30% of auth bandwidth |
| P2: Reduce column width | Low | 4 hours | 60-80% per-column in affected queries |
| P3: Reduce render polling | Low | 30 minutes | 50-70% of render worker bandwidth |
| P4: Batch admin updates | Low | 2 hours | 90%+ for bulk ops |
| P5: Cache songset list | Low | 3 hours | 30-50% of list view bandwidth |
| P6: Consolidate queries | Low | 4 hours | 20-40% per aggregated page load |

---

## Appendix: Full List of API Routes with Estimated DB Cost

| Route | Method | DB Queries (estimated) | Response Size |
|---|---|---|---|
| `/api/songs` | GET | 3 (auth + list + count) | 300-500 KB |
| `/api/songs/[id]` | GET | 2 (auth + detail) | 50-200 KB |
| `/api/songs/search` | GET | 3 (auth + search + count) | 300-500 KB |
| `/api/songs/search/semantic` | POST | 3 (auth + vector + embeddings) | 200-500 KB |
| `/api/songs/albums` | GET | 2 (auth + albums) | 10-20 KB |
| `/api/songsets` | GET | 3 (auth + list + count) | 200-400 KB |
| `/api/songsets` | POST | 2 (auth + insert) | 0.5-1 KB |
| `/api/songsets/[id]` | GET | 2-3 (auth + editor data) | 50-200 KB |
| `/api/songsets/[id]` | PATCH | 2 (auth + update) | 0.5-1 KB |
| `/api/songsets/[id]` | DELETE | 1 (auth + delete) | 0.1 KB |
| `/api/songsets/[id]/items` | GET | 2 (auth + items) | 10-50 KB |
| `/api/songsets/[id]/items` | POST | 3 (auth + item + update songset) | 0.5-2 KB |
| `/api/songsets/[id]/items/[itemId]` | PATCH | 2 (auth + update) | 2-5 KB |
| `/api/songsets/[id]/items/[itemId]` | DELETE | 2 (auth + delete + update songset) | 0.1 KB |
| `/api/songsets/[id]/duplicate` | POST | 3 (auth + get source + insert) | 5-20 KB |
| `/api/songsets/[id]/items/reorder` | PATCH | 2 (auth + reorder items + update songs) | 0.1 KB |
| `/api/render-jobs` | GET | 1 (auth only — no DB query in list) | 0.5 KB |
| `/api/render-jobs` | POST | 4 (auth + items + active check + insert + update songset) | 1-2 KB |
| `/api/render-jobs/[id]` | GET | 2 (auth + render job) | 1-3 KB |
| `/api/render-jobs/[id]` | DELETE | 2 (auth + cancel) | 1-3 KB |
| `/api/render-jobs/[id]/artifact-sizes` | GET | 1 (auth only — checks R2, not DB) | 0.5 KB |
| `/api/share` | GET | 2 (auth + create share) | 0.5-1 KB |
| `/api/share/[token]` | GET | 1-2 (share lookup, potentially auth if logged in) | 5-20 KB |
| `/api/signed-url` | GET | 2 (auth + recording lookup or render job lookup) | 0.5 KB |
| `/api/offline/cache` | GET/DELETE | 2 (auth + render job lookup) | 0.5-1 KB |
| `/api/settings` | GET | 1 (auth + settings) | 0.5 KB |
| `/api/settings` | PATCH | 2 (auth + update settings) | 0.5 KB |
| `/api/transitions/preview` | POST | 3 (auth + items + compute) | 1-5 KB |
| `/api/log-client-error` | POST | 2 (auth + insert) | 0.2 KB |
| `/api/lyrics/marks` | PATCH | 2 (auth + mark update) | 0.2 KB |
| `/api/lyrics/overrides` | PATCH | 2 (auth + lrc override) | 0.5 KB |

---

## Appendix: Key File Locations

### Webapp (Next.js — `delivery/webapp/src/`)

| File | Purpose |
|---|---|
| `src/db/schema.ts` | All Drizzle table definitions + relationships |
| `src/db/index.ts` | Database connection (neon-http driver) |
| `src/lib/auth.ts` | Better Auth configuration |
| `src/lib/db/songs.ts` | Song queries (list, search, semantic) |
| `src/lib/db/songsets.ts` | Songset CRUD + aggregate queries |
| `src/lib/db/search.ts` | Full-text search + search helpers |
| `src/lib/render/job-manager.ts` | Render job DB operations |
| `src/lib/embedding.ts` | OpenAI embedding (not DB) |
| `src/lib/rate-limit.ts` | Neon/pg rate limiting |
| `src/app/api/routes` | All API route handlers |

### Admin CLI (`ops/admin-cli/src/`)

| File | Purpose |
|---|---|
| `stream_of_worship/db/connection.py` | ConnectionProvider (psycopg) |
| `stream_of_worship/admin/db/client.py` | AdminDatabaseClient (1961 lines, bulk ops) |
| `stream_of_worship/admin/db/schema.py` | Admin column definitions |
| `stream_of_worship/admin/commands/catalog.py` | Catalog CRUD, scraping, audio processing |
| `stream_of_worship/admin/commands/songset.py` | Songset management |
| `stream_of_worship/admin/commands/audio.py` | Audio import, analysis, LRC generation |
| `stream_of_worship/admin/services/scraper.py` | YouTube + sop.org scraping |
| `stream_of_worship/db/app/read_client.py` | Read-only catalog client |
| `stream_of_worship/db/app/songset_client.py` | Write songset client |
| `stream_of_worship/db/user_client.py` | User management client |

### Render Worker (`delivery/render-worker/src/`)

| File | Purpose |
|---|---|
| `sow_render_worker/db.py` | Postgres helpers (get/update/complete render jobs) |
| `sow_render_worker/pipeline.py` | Main rendering pipeline (progress updates) |
| `sow_render_worker/lambda_handler.py` | AWS Lambda entry point |
| `sow_render_worker/asset_fetcher.py` | R2/YouTube asset fetching |
| `sow_render_worker/video_engine.py` | FFmpeg video rendering |
| `sow_render_worker/audio_engine.py` | pydub audio mixing |

### Analysis Service (`ops/analysis-service/src/`)

| File | Purpose |
|---|---|
| `sow_analysis/storage/db.py` | Analysis service DB operations |
| `sow_analysis/workers/analyzer.py` | Batch analysis pipeline (key, tempo detection) |
| `sow_analysis/models.py` | Analysis result models |

---

## Conclusion

The 5.5 GB network transfer is primarily caused by the webapp's use of `neondatabase/serverless`'s HTTP driver, which sends a full HTTPS request for every database query. The three biggest contributors are:

1. **Auth session verification on every API route** (~20-30% of total transfer)
2. **Wide column fetches on songs + recordings tables** (~50-60% of payload per query)
3. **Render worker progress polling** (~10-15% of per-job transfer)

Implementing **P0 (WebSocket driver)** alone would reduce bandwidth by 40-60%. Combining P0 with P2 (column pruning) could reduce monthly transfer by 60-75%, bringing the amplification ratio down from 32x to roughly 8-10x.
