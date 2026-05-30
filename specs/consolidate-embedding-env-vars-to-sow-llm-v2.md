# Consolidate Embedding Env Vars to SOW_LLM_* (v2)

## Summary

Replace the standalone `SOW_OPENAI_API_KEY` environment variable with the existing `SOW_LLM_API_KEY` + `SOW_LLM_BASE_URL` pair across both the Analysis Service and Web App. This eliminates a redundant env var and enables OpenAI-compatible embedding providers (OpenRouter, NeuralWatt, etc.) — matching the pattern already used by the LRC and YouTube transcript workers.

The embedding model name (`text-embedding-3-small`) remains hardcoded per prior decision.

## Changes from v1

1. **Per-song model version mismatch** — Replace the global `hasMismatchedModelVersion()` pre-check (which blocks ALL semantic search with 503 if ANY row mismatches) with per-song filtering inside `semanticSearchSongs()`. This prevents a full outage during the `model_version` label migration window.
2. **Eager validation in Python** — Move env var validation from `embed_song()` (lazy) to `EmbeddingWorker.__init__()` (eager), matching the TypeScript side and failing fast at worker construction time.
3. **Corrected TS validation claim** — The v1 spec claimed module-level guards cause "fails fast on startup." In Next.js App Router, server modules load lazily per-request, so the guard actually fails on the first semantic search request. The spec text is corrected.
4. **Shared exception module** — Move `LLMConfigError` from `workers/lrc.py` to a shared `workers/exceptions.py` to avoid cross-worker import dependency.

## Variable Mapping

| Current | New | Scope |
|---|---|---|
| `SOW_OPENAI_API_KEY` | `SOW_LLM_API_KEY` | Analysis Service + Web App |
| _(none)_ | `SOW_LLM_BASE_URL` | Web App (already exists in Analysis Service) |

> **Note:** `SOW_LLM_MODEL` is NOT reused — the embedding model (`text-embedding-3-small`) is different from the LLM chat model (`openai/gpt-4o-mini`). The embedding model remains hardcoded.

## Files to Modify (10 files)

### 1. `services/analysis/src/sow_analysis/workers/exceptions.py` (NEW)

Extract `LLMConfigError` into a shared module so both `embedder.py` and `lrc.py` can import it without cross-worker coupling.

```python
class WorkerError(Exception):
    """Base exception for worker errors."""
    pass


class LLMConfigError(WorkerError):
    """Raised when LLM configuration is missing or invalid."""
    pass
```

### 2. `services/analysis/src/sow_analysis/workers/lrc.py`

- **Replace** the local `LRCWorkerError` / `LLMConfigError` definitions with imports from `..workers.exceptions`:
  ```python
  # Remove:
  class LRCWorkerError(Exception): ...
  class LLMConfigError(LRCWorkerError): ...

  # Add:
  from ..workers.exceptions import LLMConfigError
  ```
- **Keep** `LRCWorkerError` as a local subclass if other code references it, or replace usages with `WorkerError` from the shared module. Verify callers first.

### 3. `services/analysis/src/sow_analysis/workers/embedder.py`

**Current state:** Reads `SOW_OPENAI_API_KEY` via `os.environ.get()`, no base URL, no validation.

**Changes:**

- **Remove** `import os` (only usage is `os.environ.get("SOW_OPENAI_API_KEY")`)
- **Add** `from ..config import settings` import
- **Add** `from ..workers.exceptions import LLMConfigError` import
- **`EmbeddingWorker.__init__()`** — Replace client construction AND add eager validation:
  ```python
  # Before
  def __init__(self):
      self._client = OpenAI(
          api_key=os.environ.get("SOW_OPENAI_API_KEY"),
          timeout=60.0,
          max_retries=2,
      )

  # After
  def __init__(self):
      if not settings.SOW_LLM_API_KEY:
          raise LLMConfigError(
              "SOW_LLM_API_KEY environment variable not set. "
              "Set this to your OpenAI-compatible API key."
          )
      if not settings.SOW_LLM_BASE_URL:
          raise LLMConfigError(
              "SOW_LLM_BASE_URL environment variable not set. "
              "Set this to your OpenAI-compatible API base URL "
              "(e.g., https://openrouter.ai/api/v1)."
          )
      self._client = OpenAI(
          api_key=settings.SOW_LLM_API_KEY,
          base_url=settings.SOW_LLM_BASE_URL,
          timeout=60.0,
          max_retries=2,
      )
  ```
- **Remove** the lazy validation block from `embed_song()` (it no longer exists there — validation is now in `__init__`).
- **`model_version` field** in `EmbeddingJobResult` — Change from `"openai-text-embedding-3-small"` to `"text-embedding-3-small"` (drop the `openai-` prefix since the provider is now configurable).

### 4. `services/analysis/src/sow_analysis/routes/health.py`

**Add `check_embedding_connection()`** — similar to `check_llm_connection()` but tests the `/v1/embeddings` endpoint:

```python
def check_embedding_connection() -> dict:
    """Check if embedding provider is configured and can create an embedding."""
    if not settings.SOW_LLM_BASE_URL:
        return {"status": "not_configured", "error": "SOW_LLM_BASE_URL not set"}
    if not settings.SOW_LLM_API_KEY:
        return {"status": "missing_credentials", "error": "SOW_LLM_API_KEY not set"}

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=settings.SOW_LLM_API_KEY,
            base_url=settings.SOW_LLM_BASE_URL,
        )

        response = client.embeddings.create(
            model="text-embedding-3-small",
            input="health check",
            dimensions=1536,
        )

        return {
            "status": "healthy",
            "model": "text-embedding-3-small",
            "dimensions": len(response.data[0].embedding),
        }

    except Exception as e:
        logger.warning(f"Embedding health check failed: {e}")
        return {
            "status": "unhealthy",
            "model": "text-embedding-3-small",
            "error": str(e),
        }
```

- **Add to `health_check()` response** — add `"embedding": check_embedding_connection()` to the `services` dict.

### 5. `webapp/src/lib/embedding.ts`

**Current state:** Uses `SOW_OPENAI_API_KEY` only, no base URL.

**Changes:**

```typescript
// Before
const openai = new OpenAI({
  apiKey: process.env.SOW_OPENAI_API_KEY,
  timeout: 10_000,
  maxRetries: 2,
});

// After
if (!process.env.SOW_LLM_API_KEY) {
  throw new Error(
    "SOW_LLM_API_KEY environment variable not set. " +
    "Set this to your OpenAI-compatible API key."
  );
}
if (!process.env.SOW_LLM_BASE_URL) {
  throw new Error(
    "SOW_LLM_BASE_URL environment variable not set. " +
    "Set this to your OpenAI-compatible API base URL " +
    "(e.g., https://openrouter.ai/api/v1)."
  );
}

const openai = new OpenAI({
  apiKey: process.env.SOW_LLM_API_KEY,
  baseURL: process.env.SOW_LLM_BASE_URL,
  timeout: 10_000,
  maxRetries: 2,
});
```

- **`QUERY_MODEL` export** — Change from `"openai-text-embedding-3-small"` to `"text-embedding-3-small"`.

> **Note:** The OpenAI Node SDK uses `baseURL` (camelCase), while the Python SDK uses `base_url` (snake_case). The guards are placed at module level so the app fails on the first request that imports this module (not at server startup — Next.js App Router loads server modules lazily per-request).

### 6. `webapp/src/lib/db/songs.ts`

**Replace `hasMismatchedModelVersion()` with per-song filtering in `semanticSearchSongs()`.**

The current `hasMismatchedModelVersion()` function (`songs.ts:453`) runs a global `SELECT EXISTS` check — if ANY row has a mismatched `model_version`, ALL semantic search returns 503. This creates a full outage during the `model_version` label migration window.

**Changes:**

- **Remove** the `hasMismatchedModelVersion()` function entirely.
- **Add a `modelVersion` filter parameter** to `semanticSearchSongs()`:
  ```typescript
  export async function semanticSearchSongs(
    embedding: number[],
    limit: number,
    expectedModelVersion: string,
  ): Promise<...> {
  ```
- **Add a WHERE clause** to the existing SQL query in `semanticSearchSongs()` to filter out songs with mismatched `model_version`:
  ```sql
  -- Add to the existing WHERE clause inside the subquery:
  AND se.model_version = ${expectedModelVersion}
  ```
  This ensures only songs with embeddings matching the current query model are returned. Songs with stale `model_version` labels are silently excluded from results (they won't appear in search, but won't cause a 503 either).

- **No changes to `findTopMatchingLines()`** — it already filters by `songIds` from the result of `semanticSearchSongs()`, so mismatched songs are excluded transitively.

### 7. `webapp/src/app/api/songs/search/semantic/route.ts`

**Remove the global mismatch pre-check and pass `QUERY_MODEL` to `semanticSearchSongs()`.**

```typescript
// Before (lines 40-49)
const mismatch = await hasMismatchedModelVersion(QUERY_MODEL);
if (mismatch) {
  return NextResponse.json(
    { error: "Semantic search unavailable — embeddings need regeneration. Contact admin." },
    { status: 503 }
  );
}

// After — removed entirely. Pass QUERY_MODEL to semanticSearchSongs instead:
const songs = await semanticSearchSongs(queryEmbedding, limit, QUERY_MODEL);
```

- **Remove** the `hasMismatchedModelVersion` import from `@/lib/db/songs`.
- **Update** the `semanticSearchSongs` call to include the third `expectedModelVersion` parameter.

### 8. `webapp/src/db/schema.ts`

**Update `model_version` defaults** — Change from `"openai-text-embedding-3-small"` to `"text-embedding-3-small"`:

```typescript
// Before (songEmbeddings and songLineEmbeddings tables)
.default("openai-text-embedding-3-small")

// After
.default("text-embedding-3-small")
```

> **Migration note:** This changes the *default* for new rows. Existing rows with `"openai-text-embedding-3-small"` will be excluded from semantic search results (filtered out by the `model_version` check in `semanticSearchSongs`) until the SQL migration is run. This is a graceful degradation — search still works for any songs that have been re-embedded or migrated, rather than a hard 503 for all songs.

**Remediation SQL** (run after deployment):
```sql
UPDATE song_embedding SET model_version = 'text-embedding-3-small' WHERE model_version = 'openai-text-embedding-3-small';
UPDATE song_line_embedding SET model_version = 'text-embedding-3-small' WHERE model_version = 'openai-text-embedding-3-small';
```

### 9. `webapp/.env.example`

**Replace** the `SOW_OPENAI_API_KEY` section:

```
# Before
# OpenAI API key for semantic search (text-embedding-3-small).
# Required for the "Describe" tab in Browse Sheet.
SOW_OPENAI_API_KEY=

# After
# LLM API key for semantic search (text-embedding-3-small).
# Required for the "Describe" tab in Browse Sheet.
# Supports OpenAI-compatible providers (OpenRouter, OpenAI, NeuralWatt, etc.).
SOW_LLM_API_KEY=

# LLM API base URL for semantic search.
# Required for the "Describe" tab in Browse Sheet.
# Examples:
#   - OpenAI: https://api.openai.com/v1
#   - OpenRouter: https://openrouter.ai/api/v1
SOW_LLM_BASE_URL=
```

### 10. `webapp/.env.production.example`

**Replace** the "OpenAI API (Semantic Search)" section:

```
# Before
# -----------------------------------------------------------------------------
# OpenAI API (Semantic Search)
# -----------------------------------------------------------------------------
# API key for OpenAI text-embedding-3-small, used by the "Describe" tab
# in Browse Sheet to embed user queries for semantic song search.
# Cost: ~$0.02/1M tokens; typical query is ~10 tokens.
SOW_OPENAI_API_KEY=

# After
# -----------------------------------------------------------------------------
# LLM API (Semantic Search)
# -----------------------------------------------------------------------------
# OpenAI-compatible API key for text-embedding-3-small, used by the "Describe"
# tab in Browse Sheet to embed user queries for semantic song search.
# Supports: OpenRouter, OpenAI, NeuralWatt, etc.
# Cost: ~$0.02/1M tokens; typical query is ~10 tokens.
SOW_LLM_API_KEY=

# OpenAI-compatible API base URL for embedding generation.
# Required — must be set even when using OpenAI directly.
# Examples:
#   - OpenAI: https://api.openai.com/v1
#   - OpenRouter: https://openrouter.ai/api/v1
SOW_LLM_BASE_URL=
```

## Test Files to Modify (3 files)

### 11. `webapp/src/test/api/songs/search/semantic.test.ts`

**Update mock and test data:**

- Line 24: `QUERY_MODEL: "openai-text-embedding-3-small"` → `QUERY_MODEL: "text-embedding-3-small"`
- Line 55: `modelVersion: "openai-text-embedding-3-small"` → `modelVersion: "text-embedding-3-small"`
- **Remove** the `hasMismatchedModelVersion` mock (line 30) and the corresponding test case (lines 116-124: "returns 503 when model version mismatch (pre-check)").
- **Update** `semanticSearchSongs` mock calls to include the third `expectedModelVersion` parameter where assertions check call arguments (lines 165, 176).
- **Add** a new test case: "excludes songs with mismatched model_version from results" — mock `semanticSearchSongs` to return only matching songs, verify the route returns 200 with filtered results.

### 12. `webapp/src/test/db/schema.test.ts`

**Update model_version default assertions:**

- Line 268-269: `expect(songEmbeddings.modelVersion.default).toBe("openai-text-embedding-3-small")` → `expect(songEmbeddings.modelVersion.default).toBe("text-embedding-3-small")`
- Line 272-273: `expect(songLineEmbeddings.modelVersion.default).toBe("openai-text-embedding-3-small")` → `expect(songLineEmbeddings.modelVersion.default).toBe("text-embedding-3-small")`

### 13. `webapp/src/test/deployment/deployment.test.ts`

**Add two new test cases** in the existing `.env.production.example` describe block (after line 249):

```typescript
it("documents SOW_LLM_API_KEY", () => {
  const content = readEnvExample();
  expect(content).toContain("SOW_LLM_API_KEY=");
});

it("documents SOW_LLM_BASE_URL", () => {
  const content = readEnvExample();
  expect(content).toContain("SOW_LLM_BASE_URL=");
});
```

## Files NOT Modified

| File | Reason |
|---|---|
| `services/analysis/docker-compose.yml` | `SOW_LLM_API_KEY` and `SOW_LLM_BASE_URL` are already passed through. No new env vars needed. |
| `services/analysis/.env.example` | Already documents `SOW_LLM_API_KEY` and `SOW_LLM_BASE_URL`. No `SOW_OPENAI_API_KEY` reference exists here. |
| `services/analysis/src/sow_analysis/config.py` | `SOW_LLM_API_KEY` and `SOW_LLM_BASE_URL` already defined. No new fields needed. |
| `services/analysis/src/sow_analysis/workers/queue.py` | No `SOW_OPENAI_API_KEY` reference. `EmbeddingWorker` import is unchanged. |
| `specs/semantic-search-implementation*.md` | Historical design docs — left as-is per decision. |
| `reports/handover_semantic_search_v3.md` | Same. |

## Risks & Deployment Notes

### Hard Cutover — No Backward Compatibility

This is a hard cutover. `SOW_OPENAI_API_KEY` will no longer be read by any component. Env vars must be updated **before** code deployment:

1. **Analysis Service (Docker)**: `SOW_LLM_API_KEY` and `SOW_LLM_BASE_URL` are already configured (used by LRC worker). No action needed if they're already set.
2. **Web App (Vercel)**: Add `SOW_LLM_API_KEY` and `SOW_LLM_BASE_URL` to Vercel environment variables. Remove `SOW_OPENAI_API_KEY` after deployment.
3. **Local dev `.env.local`**: Developers must add `SOW_LLM_API_KEY` and `SOW_LLM_BASE_URL`, and can remove `SOW_OPENAI_API_KEY`.

### model_version Column — Graceful Degradation (Not Hard Outage)

The `model_version` default changes from `"openai-text-embedding-3-small"` to `"text-embedding-3-small"`. After deployment:

- **New embeddings** will have `model_version = "text-embedding-3-small"`.
- **Existing embeddings** still have `model_version = "openai-text-embedding-3-small"`.
- **Semantic search** will exclude songs with the old `model_version` label from results (filtered in SQL), rather than returning 503 for all songs. This is a graceful degradation — search still works for any re-embedded or migrated songs.

**Remediation** (recommended, run soon after deploy):
```sql
UPDATE song_embedding SET model_version = 'text-embedding-3-small' WHERE model_version = 'openai-text-embedding-3-small';
UPDATE song_line_embedding SET model_version = 'text-embedding-3-small' WHERE model_version = 'openai-text-embedding-3-small';
```
This is safe because the embedding vectors are identical — only the label changed. No re-embedding needed.

### Embedding Provider Must Support /v1/embeddings

Not all OpenAI-compatible providers support the embeddings endpoint. OpenRouter supports it for select models. OpenAI direct supports it. Verify the chosen provider supports `text-embedding-3-small` before deploying.

### Eager Validation in EmbeddingWorker.__init__

The Python `EmbeddingWorker` now validates env vars at construction time. If `SOW_LLM_API_KEY` or `SOW_LLM_BASE_URL` is unset, the worker will fail to instantiate. In the Analysis Service, `EmbeddingWorker` is constructed in `queue.py` at module import time — so the service will fail to start if the env vars are missing. This is intentional (fail fast) and consistent with how the LRC worker would fail on its first job. Verify that the Analysis Service's Docker entrypoint and health check handle this gracefully.

## Post-Implementation Verification

```bash
# Analysis Service — lint + import check
cd services/analysis
PYTHONPATH=src python -c "from sow_analysis.workers.embedder import EmbeddingWorker; print('OK')"
PYTHONPATH=src python -c "from sow_analysis.routes.health import check_embedding_connection; print('OK')"
PYTHONPATH=src python -c "from sow_analysis.workers.exceptions import LLMConfigError; print('OK')"

# Web App — lint + test
cd webapp
pnpm lint
pnpm test
```

## Out of Scope / Follow-up

- **Vercel environment variables**: Must be manually updated in the Vercel dashboard (add `SOW_LLM_API_KEY`, `SOW_LLM_BASE_URL`; remove `SOW_OPENAI_API_KEY`).
- **SQL migration for model_version**: Must be run against the production Neon Postgres database after deployment.
- **Historical specs/reports**: Left as-is — they accurately reflect the design at time of writing.
- **Separate embedding env vars** (`SOW_EMBEDDING_API_KEY` / `SOW_EMBEDDING_BASE_URL`): Deferred. If a deployment needs different providers for LLM chat vs. embeddings, a follow-up can add fallback vars that default to `SOW_LLM_*`.
