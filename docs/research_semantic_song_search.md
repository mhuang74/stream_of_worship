# Semantic Song Search — Architecture & Review

## Overview

Semantic song search lets users find worship songs by describing what they're looking for in natural language (e.g., "grace and forgiveness" or "praise and thanksgiving"). It uses OpenAI `text-embedding-3-small` embeddings (1536 dimensions) stored in PostgreSQL via the `pgvector` extension, with cosine similarity for ranking.

The system has three phases: **embedding generation** (offline/batch), **embedding storage** (PostgreSQL + pgvector), and **query-time semantic search** (real-time webapp API).

---

## End-to-End Flow

### Phase 1: Embedding Generation (Offline/Batch)

Triggered by the Admin CLI `sow-admin audio embed` command.

1. **Admin CLI** discovers songs needing embeddings via `db_client.get_songs_without_embeddings()`
2. **Staleness check**: Computes a content hash (`SHA-256(title\0composer\0lyrics_raw\0lyrics_lines)[:16]`) and compares against the stored `content_hash` in `song_embedding`. Skips if hash matches and `--force` is not set.
3. **Job submission**: HTTP POST to Analysis Service `POST /api/v1/jobs/embedding` with `{ song_id, title, composer, lyrics_raw, lyrics_lines[] }`
4. **Analysis Service** dispatches to `EmbeddingWorker.embed_song()`:
   - **Song-level embedding**: Concatenates `"{title} {composer} {lyrics_raw}"` → single 1536-d vector
   - **Line-level embeddings**: Filters lyrics lines to only those with ≥ 4 CJK characters (`U+4E00`–`U+9FFF`), then batch-embeds them
   - Returns `EmbeddingJobResult` with `model_version="text-embedding-3-small"` and `content_hash`
5. **Admin CLI** polls for job completion and writes results to PostgreSQL

### Phase 2: Embedding Storage (PostgreSQL + pgvector)

Two tables with HNSW indexes for fast approximate nearest neighbor search:

| Table | Key Columns | Indexes |
|-------|-------------|---------|
| `song_embedding` | `song_id` (PK), `embedding vector(1536)`, `model_version`, `content_hash` | HNSW cosine on `embedding` |
| `song_line_embedding` | `id` (serial), `song_id`, `line_index`, `line_text`, `embedding vector(1536)`, `model_version` | HNSW cosine on `embedding`, B-tree on `song_id` |

### Phase 3: Query-Time Semantic Search (Webapp)

API route: `POST /api/songs/search/semantic` — `{ query: string, limit?: number (1-50) }`

1. **Auth**: Verify session via Better Auth (401 if unauthenticated)
2. **Input validation**: Zod schema validates `query` (non-empty) and `limit` (1-50, default 20)
3. **Query embedding** (`embedding.ts` → `embedQuery()`): Real-time OpenAI API call to embed the user query as a 1536-d vector. Returns 503 if API fails.
4. **Song-level search** (`songs.ts` → `semanticSearchSongs()`): Raw SQL with pgvector `<=>` cosine distance operator, filtered by `model_version` and `visibility_status = 'published'`. Returns top-N songs ranked by cosine similarity.
5. **Line-level snippet matching** (`songs.ts` → `findTopMatchingLines()`): For each matched song, finds the top 2 most similar lyric lines (≥ 4 CJK chars) to explain *why* the song matched.
6. **Response**: `{ songs: [...], query, total }` — each song includes `matchingSnippet` and `whyThisMatch`.

```
┌──────────────────────────────────────────────────────────────────┐
│              EMBEDDING GENERATION (Offline)                       │
│                                                                  │
│  Admin CLI  ──>  Analysis Service  ──>  OpenAI API               │
│  sow-admin       POST /jobs/embedding    embeddings.create()     │
│  audio embed     EmbeddingWorker                               │
│                   .embed_song()                                │
│       │                │                                       │
│       │<── JobResult ──┘                                       │
│       ▼                                                        │
│  PostgreSQL (pgvector)                                         │
│  ┌────────────────────┐  ┌──────────────────────────┐          │
│  │ song_embedding     │  │ song_line_embedding       │          │
│  │ embedding(1536)    │  │ embedding(1536)           │          │
│  │ model_version      │  │ line_text, model_version  │          │
│  │ content_hash       │  │                          │          │
│  │ HNSW cosine index  │  │ HNSW cosine + B-tree     │          │
│  └────────────────────┘  └──────────────────────────┘          │
└──────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────┐
│              SEMANTIC SEARCH (Query-Time)                        │
│                                                                  │
│  Browser ──> POST /api/songs/search/semantic ──> OpenAI API     │
│              { query, limit }                    embedQuery()    │
│                     │                            ↓               │
│                     │                        1536-d vector       │
│                     ▼                            │               │
│              semanticSearchSongs()              │               │
│              (cosine similarity via              │               │
│               pgvector <=> operator)             │               │
│                     │                            │               │
│              findTopMatchingLines()              │               │
│              (top 2 lyric lines/song)            │               │
│                     ▼                            ▼               │
│              { songs + matchingSnippet + whyThisMatch }         │
└──────────────────────────────────────────────────────────────────┘
```

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Two-level embedding (song + line) | Song-level for ranking, line-level for explainability ("why this match") |
| `model_version` gate | Prevents returning results from mismatched embedding models during migrations |
| Content hash for staleness | Avoids re-embedding songs whose lyrics haven't changed |
| CJK filtering (≥ 4 chars) | Excludes metadata lines, short interjections, and English-only lines that produce noisy similarity scores |
| HNSW indexes | Fast approximate nearest neighbor search for production-scale vector queries |
| Separate embedding semaphore | Embedding jobs only call external OpenAI API — no GPU/CPU contention with heavy ML jobs |
| OpenAI-compatible provider | `SOW_EMBEDDING_BASE_URL` + `SOW_EMBEDDING_API_KEY` supports OpenRouter, direct OpenAI, etc. |

---

## Environment Variables

| Variable | Scope | Purpose |
|----------|-------|---------|
| `SOW_EMBEDDING_API_KEY` | Analysis Service + Web App | API key for OpenAI-compatible embedding provider |
| `SOW_EMBEDDING_BASE_URL` | Analysis Service + Web App | Base URL for embedding API calls (e.g., `https://openrouter.ai/api/v1`) |
| `SOW_EMBEDDING_MODEL` | Analysis Service + Web App | Provider-specific embedding model name for API calls (default: `text-embedding-3-small`; OpenRouter: `openai/text-embedding-3-small`) |
| `SOW_LLM_API_KEY` | Analysis Service chat only | API key for OpenAI-compatible chat provider |
| `SOW_LLM_BASE_URL` | Analysis Service chat only | Base URL for chat API calls |
| `SOW_LLM_MODEL` | Analysis Service chat only | LLM chat model for LRC alignment (e.g., `openai/gpt-4o-mini`) — NOT used for embeddings |

> **Note**: The DB `model_version` label is always hardcoded as `"text-embedding-3-small"` (provider-agnostic), while `SOW_EMBEDDING_MODEL` is the provider-specific name used for the actual API call.

---

## Review: Concerns with `consolidate-embedding-env-vars-to-sow-llm-v3.md`

### Operational Concerns

1. **`SOW_EMBEDDING_BASE_URL` is required even for OpenAI direct** — Users must explicitly set `SOW_EMBEDDING_BASE_URL` even when using OpenAI directly. This adds friction for the simplest use case. **Recommendation**: Default `SOW_EMBEDDING_BASE_URL` to `https://api.openai.com/v1` in `config.py` and `embedding.ts` instead of requiring it and raising `LLMConfigError` on absence.

2. **No rollback plan** — The spec acknowledges hard cutover but doesn't address what happens if deployment needs to be reverted. Since `SOW_OPENAI_API_KEY` is removed from code, a rollback would require either re-adding the old var or ensuring the new vars are already in place. **Recommendation**: Add a brief rollback note (e.g., "ensure both old and new env vars are set during the deployment window, then remove old vars after confirming stability").

3. **Health check makes a live paid API call** — `check_embedding_connection()` calls `embeddings.create` on every `/health` hit. This costs money (tiny per call, but adds up if polled frequently) and can report "unhealthy" on transient network errors even when the service is functional. **Recommendation**: Use a lightweight check (just verify env vars are set) for the default `/health` endpoint, and gate the live API call behind a separate `/health/deep` endpoint or query parameter.

### Data Quality Concerns

4. **`model_version` label is not truly provider-agnostic** — The spec calls `"text-embedding-3-small"` "provider-agnostic" but it's actually the OpenAI model name. If a non-OpenAI embedding model is ever used (e.g., Cohere `embed-v4`), the `model_version` DB label would still say `"text-embedding-3-small"`, which is misleading. The label conflates model identity with provider identity. **Recommendation**: Document this limitation explicitly, or consider a more abstract labeling scheme (e.g., `embedding-v1`, `embedding-v2`) that decouples the DB label from the model name.

5. **Silent exclusion of old-model songs with no admin visibility** — The per-song `WHERE se.model_version = ${expectedModelVersion}` filter silently drops songs from search results. This is better than a hard 503, but there's no admin-visible signal that songs are missing. **Recommendation**: Log a count of excluded songs (or add a metric) so operators know remediation SQL is still pending. Alternatively, add an admin CLI command that reports songs with mismatched `model_version`.

6. **No dimension mismatch guard** — If someone configures a different embedding model that produces non-1536-dimension vectors, the `semanticSearchSongs` validation will reject it, but the `model_version` check alone doesn't prevent this. The `SOW_EMBEDDING_MODEL` env var could be set to any model name without dimension validation. **Recommendation**: Consider validating the `dimensions` parameter against the configured model, or at minimum documenting which models produce 1536-d vectors.

### UX Concerns

7. **Webapp errors surface lazily, not at startup** — The `embedding.ts` guards fail on first request import (not server startup) due to Next.js App Router's lazy module loading. A misconfigured deployment can pass smoke tests and only fail when a user actually tries semantic search. **Recommendation**: Add a startup health probe or build-time env var validation for `SOW_EMBEDDING_API_KEY` and `SOW_EMBEDDING_BASE_URL`.

8. **Dual model naming is a cognitive burden** — `SOW_EMBEDDING_MODEL` (API call, provider-specific) vs. hardcoded `model_version` (DB label, "provider-agnostic") requires developers to understand two naming schemes for the same concept. **Recommendation**: Add inline comments in `config.py` and `embedding.ts` linking the two, and document the relationship in the `.env.example` files.

9. **No automated remediation** — The spec provides manual SQL for `model_version` migration but no admin CLI command or migration script. This increases the risk of the step being forgotten, leaving songs silently excluded from search indefinitely. **Recommendation**: Add an `sow-admin db migrate-embedding-model-version` command (or include it in the existing migration tooling) to automate the `UPDATE` statements.

---

## Key Files

| File | Role |
|------|------|
| `services/analysis/src/sow_analysis/workers/embedder.py` | Embedding generation worker (song + line embeddings) |
| `services/analysis/src/sow_analysis/models.py` | `EmbeddingJobRequest`, `EmbeddingJobResult`, `LineEmbedding` models |
| `services/analysis/src/sow_analysis/config.py` | `SOW_LLM_*` env var configuration |
| `services/analysis/src/sow_analysis/routes/health.py` | Health check including `check_embedding_connection()` |
| `services/analysis/src/sow_analysis/workers/exceptions.py` | `WorkerError`, `LLMConfigError` shared exceptions |
| `webapp/src/lib/embedding.ts` | Query-time embedding client (`embedQuery()`, `QUERY_MODEL`) |
| `webapp/src/lib/db/songs.ts` | `semanticSearchSongs()`, `findTopMatchingLines()` |
| `webapp/src/app/api/songs/search/semantic/route.ts` | API route handler |
| `webapp/src/db/schema.ts` | Drizzle ORM schema for `songEmbeddings`, `songLineEmbeddings` |
| `src/stream_of_worship/admin/commands/audio.py` | Admin CLI `embed` command |
| `src/stream_of_worship/admin/services/analysis.py` | Admin client `submit_embedding()` + result parsing |
| `src/stream_of_worship/admin/db/client.py` | DB client `upsert_song_embedding()`, `upsert_song_line_embeddings()` |
| `src/stream_of_worship/admin/db/schema.py` | Raw SQL for embedding tables + HNSW indexes |
