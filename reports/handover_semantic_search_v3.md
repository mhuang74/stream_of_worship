# Semantic Search v3 Implementation — Handover Document

**Date:** 2026-05-29
**Spec:** `specs/semantic-search-implementation-v3.md`
**Status:** ~95% complete. All code written, lint passes, all relevant tests pass (schema 50/50, search 7/7, semantic route 12/12, SemanticSearch component 24/24, BrowseSheet 24/24). Remaining: git commit + push, deployment steps.

---

## What Was Done

All 9 phases of the spec have been implemented in code. Here is a summary of every file changed:

### Phase 1: Schema Migration

| File | Change |
|---|---|
| `webapp/src/db/schema.ts` | Rewrote `songEmbeddings` table: PK=`songId` (FK→songs.id), `embedding vector(1536)`, `modelVersion` default `"openai-text-embedding-3-small"`, `contentHash`, HNSW index. Added `songLineEmbeddings` table with `serial` PK, `songId`, `lineIndex`, `lineText`, `embedding vector(1536)`, `modelVersion`, HNSW + song_id indexes. Updated relations: `songEmbeddingsRelations`→songs, added `songLineEmbeddingsRelations`. Removed `recordingsRelations.songEmbeddings`. Added `songsRelations.songEmbeddings` and `songsRelations.songLineEmbeddings`. Added `serial` import. |
| `webapp/drizzle/0008_rebuild_song_embedding_add_song_line_embedding.sql` | **New**: Hand-written migration: DROP old tables, CREATE new tables with HNSW indexes |
| `webapp/drizzle/meta/_journal.json` | Added entry for migration 0008 |

### Phase 2a: Analysis Service

| File | Change |
|---|---|
| `services/analysis/src/sow_analysis/models.py` | Added `JobType.EMBEDDING`, `EmbeddingJobRequest`, `EmbeddingJobResult`, `LineEmbedding` models. Updated `Job.request` Union type and `Job.result` type. |
| `services/analysis/src/sow_analysis/workers/embedder.py` | **New**: `EmbeddingWorker` class with OpenAI client, CJK filter (`_count_cjk_chars`), content hash (`_compute_content_hash`), truncation warning, `embed_song()` and `_embed_texts()` methods |
| `services/analysis/src/sow_analysis/workers/queue.py` | Added `EmbeddingJobRequest`/`EmbeddingJobResult` imports, optional `EmbeddingWorker` import, `_embedding_semaphore` (max 5), `EMBEDDING` dispatch in `_process_job_with_semaphore`, `_process_embedding_job()` method, `JobType.EMBEDDING` in stats |
| `services/analysis/src/sow_analysis/routes/jobs.py` | Added `EmbeddingJobRequest` import, `POST /jobs/embedding` endpoint |

### Phase 2b: Admin CLI

| File | Change |
|---|---|
| `src/stream_of_worship/admin/services/analysis.py` | Added `submit_embedding()` method, `LineEmbeddingResult` and `EmbeddingResult` dataclasses, updated `JobInfo.result` type to `Union[AnalysisResult, EmbeddingResult]`, updated `_parse_job_response()` to handle embedding results |
| `src/stream_of_worship/admin/db/client.py` | Added `upsert_song_embedding()`, `upsert_song_line_embeddings()`, `get_songs_without_embeddings()`, `get_all_songs_with_lyrics()`, `get_embedding_content_hash()` |
| `src/stream_of_worship/admin/db/schema.py` | Added `CREATE_SONG_EMBEDDING_TABLE`, `CREATE_SONG_LINE_EMBEDDING_TABLE`, `CREATE_EMBEDDING_INDEXES` DDL, updated `ALL_SCHEMA_STATEMENTS` |
| `src/stream_of_worship/admin/db/models.py` | Added `SongEmbedding` and `SongLineEmbedding` dataclasses |
| `src/stream_of_worship/admin/commands/audio.py` | Added `os` import, `_compute_content_hash()`, `_submit_embedding_single()`, `_write_embedding_result()`, `embed` command with `--all`/`--force`/`--wait` flags. Extended `_process_batch()` with Phase 3.5 (auto-submit embedding jobs after LRC). |

### Phase 3: Query-Time Embedding

| File | Change |
|---|---|
| `webapp/package.json` | Added `openai` dependency |
| `webapp/src/lib/embedding.ts` | **New**: `embedQuery()` function + `QUERY_MODEL` export |
| `webapp/next.config.ts` | Added `"openai"` to `serverExternalPackages` |
| `webapp/.env.example` | Added `SOW_OPENAI_API_KEY=` |
| `webapp/.env.production.example` | Added `SOW_OPENAI_API_KEY=` with documentation |

### Phase 4: Fix API Route

| File | Change |
|---|---|
| `webapp/src/app/api/songs/search/semantic/route.ts` | **Rewritten**: Accepts `{ query, limit }` via Zod. Pre-check `hasMismatchedModelVersion()` → 503. Calls `embedQuery(query)` → 503 on failure. Calls `semanticSearchSongs()`, then `findTopMatchingLines()`. Returns `{ songs, query, total }` with `matchingSnippet` + `whyThisMatch`. |

### Phase 5: Update DB Queries

| File | Change |
|---|---|
| `webapp/src/lib/db/songs.ts` | Rewrote `semanticSearchSongs`: join `song_embedding → songs → recordings` (keyed by song_id), 1536-dim validation, includes `model_version`. Added `findTopMatchingLines()`: SQL ROW_NUMBER + pgvector `<=>` on `song_line_embedding`, CJK filter via `regexp_replace`, top 2 per song. Added `hasMismatchedModelVersion()`: `SELECT EXISTS` query. Updated `SemanticSearchResult` interface with `modelVersion`, `matchingSnippet`, `whyThisMatch`. |

### Phase 6: Frontend Updates

| File | Change |
|---|---|
| `webapp/src/components/search/SemanticSearch.tsx` | Added `matchingSnippet` display (italic, `▸` prefix), expandable "Why this match?" section with `ChevronDown`/`ChevronRight`, `onSwitchToSearchTab` callback prop for 503 auto-fallback, `expandedSongId` state |
| `webapp/src/components/songset/BrowseSheet.tsx` | Added `initialSearchQuery` state, `handleSwitchToSearchTab` callback. Wired `onSwitchToSearchTab` to SemanticSearch, `initialQuery` to SongSearch |
| `webapp/src/components/songset/SongSearch.tsx` | Added `initialQuery` prop. Auto-triggers search when `initialQuery` is set (using `useRef` guard to avoid re-triggering) |

### Phase 7: Remove Dead Code

| File | Change |
|---|---|
| `webapp/src/lib/db/search.ts` | Removed `getEmbeddingForRecording()`, removed `songEmbeddings` import, removed `semanticSearchSongs` re-export |

### Phase 8: Tests

| File | Change |
|---|---|
| `webapp/src/test/api/songs/search/semantic.test.ts` | **Rewritten**: Mocks `embedQuery`, `hasMismatchedModelVersion`, `findTopMatchingLines`, `semanticSearchSongs`. Tests `{ query, limit }` shape, 503 model version mismatch, 503 OpenAI failure, success with snippets, null snippets, custom limit, limit > 50 |
| `webapp/src/test/components/search/SemanticSearch.test.tsx` | **Rewritten**: Added `matchingSnippet`/`whyThisMatch` to mock data, tests snippet rendering, "Why this match?" expand, 503 auto-fallback via `onSwitchToSearchTab`, error when no callback |
| `webapp/src/test/lib/db/search.test.ts` | **Rewritten**: Removed `getEmbeddingForRecording` tests, removed `songEmbeddings` mock |
| `webapp/src/test/db/schema.test.ts` | Updated `songEmbeddings` tests: PK=`songId`, `contentHash`. Added `songLineEmbeddings` table name, columns, FK, and default tests. Updated FK test: `songEmbeddings.songId` → `songs.id`. Added `modelVersion` default tests for both tables. |

---

## What Remains

### 1. ✅ Tests Pass

All relevant tests verified:
- `schema.test.ts`: 50 passed
- `search.test.ts`: 7 passed
- `semantic.test.ts` (route): 12 passed
- `SemanticSearch.test.tsx` (component): 24 passed
- `BrowseSheet.test.tsx`: 24 passed

### 2. ✅ CJK Filter Bug Fixed

The `regexp_replace` pattern in `findTopMatchingLines()` was corrected to `'[^\u4e00-\u9fff]'` (with `^` inside brackets) to properly count CJK characters.

### 3. Run Migration Against Database

```bash
cd webapp && npx drizzle-kit push   # dev
# or
cd webapp && npx drizzle-kit migrate  # prod
```

### 5. Set Environment Variables

- Add `SOW_OPENAI_API_KEY` to Vercel environment variables
- Add `SOW_OPENAI_API_KEY` to Analysis Service environment (Docker compose)

### 6. Deploy Analysis Service

Build and deploy the Analysis Service Docker image with the new `EMBEDDING` job type.

### 7. Populate Embeddings

```bash
sow-admin audio embed --all --wait
```

### 8. Verify End-to-End

1. Open webapp → Browse Sheet → "Describe" tab
2. Type a query (e.g., "关于神的恩典的赞美诗")
3. Verify results appear with similarity badges, matching snippets, and "Why this match?" expand
4. Test 503 fallback: temporarily set wrong `SOW_OPENAI_API_KEY` → should auto-switch to Browse tab

---

## Known Issues / Design Notes

1. **`SongCardData` interface mismatch**: The `SemanticSearchResult` extends `SongCardData` which has `recordings` with only `contentHash`, `hashPrefix`, `durationSeconds`, `tempoBpm`, `musicalKey`. But the API returns more recording fields. The `SongCard` component only uses the `SongCardData` fields, so this is fine for display, but the type narrowing may cause issues.

2. **`_process_batch` embedding auto-submit**: The batch extension only checks `get_embedding_content_hash(song_id) is None` — it doesn't check staleness (content hash mismatch). This is intentional for the batch flow (staleness is handled by `embed --all` without `--force`).

3. **`EmbeddingWorker` initialization**: The worker creates a new `OpenAI` client for each job. This could be optimized by creating a singleton, but for the expected volume (hundreds of songs, not thousands), this is acceptable.

4. **`Job.result` type in Analysis Service**: The `Job` dataclass's `result` field is now `Optional[Union[JobResult, EmbeddingJobResult]]`. The `job_to_response()` function in `routes/jobs.py` only maps `JobResult` fields, so embedding job results won't appear in the `JobResponse.result` field. The Admin CLI's `_parse_job_response()` handles this by checking `job_type == "embedding"` and parsing accordingly. But the Analysis Service's own response won't include the embedding data in the `result` field of `JobResponse`. This is fine because the Admin CLI polls and parses the raw JSON, but if someone queries the Analysis Service API directly, they won't see embedding results in the standard `JobResponse`. Consider adding embedding result fields to `JobResponse` in a follow-up.

---

## File Change Summary (25 files per spec)

| # | File | Phase | Status |
|---|---|---|---|
| 1 | `webapp/src/db/schema.ts` | 1 | ✅ Done |
| 2 | `webapp/drizzle/0008_rebuild_song_embedding_add_song_line_embedding.sql` | 1 | ✅ Done |
| 3 | `services/analysis/src/sow_analysis/models.py` | 2a | ✅ Done |
| 4 | `services/analysis/src/sow_analysis/workers/embedder.py` | 2a | ✅ Done |
| 5 | `services/analysis/src/sow_analysis/workers/queue.py` | 2a | ✅ Done |
| 6 | `services/analysis/src/sow_analysis/routes/jobs.py` | 2a | ✅ Done |
| 7 | `services/analysis/pyproject.toml` | 2a | ✅ Verified (openai already present) |
| 8 | `src/stream_of_worship/admin/services/analysis.py` | 2b | ✅ Done |
| 9 | `src/stream_of_worship/admin/commands/audio.py` | 2b | ✅ Done |
| 10 | `src/stream_of_worship/admin/db/client.py` | 2b | ✅ Done |
| 11 | `src/stream_of_worship/admin/db/schema.py` | 2b | ✅ Done |
| 12 | `src/stream_of_worship/admin/db/models.py` | 2b | ✅ Done |
| 13 | `webapp/.env.example` | 3 | ✅ Done |
| 14 | `webapp/.env.production.example` | 3 | ✅ Done |
| 15 | `webapp/package.json` | 3 | ✅ Done (openai added) |
| 16 | `webapp/next.config.ts` | 3 | ✅ Done |
| 17 | `webapp/src/lib/embedding.ts` | 3 | ✅ Done |
| 18 | `webapp/src/app/api/songs/search/semantic/route.ts` | 4 | ✅ Done |
| 19 | `webapp/src/lib/db/search.ts` | 7 | ✅ Done |
| 20 | `webapp/src/lib/db/songs.ts` | 5 | ✅ Done |
| 21 | `webapp/src/components/search/SemanticSearch.tsx` | 6 | ✅ Done |
| 22 | `webapp/src/test/api/songs/search/semantic.test.ts` | 8 | ✅ Done |
| 23 | `webapp/src/test/components/search/SemanticSearch.test.tsx` | 8 | ✅ Done |
| 24 | `webapp/src/test/lib/db/search.test.ts` | 8 | ✅ Done |
| 25 | `webapp/src/test/db/schema.test.ts` | 8 | ✅ Done |

**Bonus files changed** (not in spec but required):
- `webapp/drizzle/meta/_journal.json` — migration journal entry
- `webapp/src/components/songset/BrowseSheet.tsx` — tab-switch wiring
- `webapp/src/components/songset/SongSearch.tsx` — `initialQuery` prop
