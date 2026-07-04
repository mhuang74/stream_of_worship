# Consolidate Embedding Env Vars to SOW_LLM_* (v3)

> **Superseded:** This spec introduced `SOW_LLM_EMBEDDING_MODEL`. It has been
> replaced by `SOW_EMBEDDING_MODEL` (see
> `specs/separate-chat-and-embedding-env-vars-v1.md`) to cleanly separate chat
> and embedding env vars. `SOW_LLM_EMBEDDING_MODEL` is removed.

## Summary

Replace the standalone `SOW_OPENAI_API_KEY` environment variable with the existing `SOW_LLM_API_KEY` + `SOW_LLM_BASE_URL` pair, and add a new `SOW_LLM_EMBEDDING_MODEL` variable for the provider-specific embedding model name. This eliminates a redundant env var, enables OpenAI-compatible embedding providers (OpenRouter, NeuralWatt, etc.), and correctly handles provider-specific model naming — matching the pattern already used by the LRC and YouTube transcript workers.

The `model_version` label (stored in DB, used for version checking) remains hardcoded as `text-embedding-3-small` (provider-agnostic). The new `SOW_LLM_EMBEDDING_MODEL` is only used for the actual API call (e.g., `openai/text-embedding-3-small` on OpenRouter, `text-embedding-3-small` on OpenAI direct).

## Changes from v2

1. **New `SOW_LLM_EMBEDDING_MODEL` env var** — Different providers use different model identifiers for the same underlying model (e.g., OpenRouter: `openai/text-embedding-3-small`, OpenAI direct: `text-embedding-3-small`). The API call needs the provider-specific name, but the DB `model_version` label should be provider-agnostic. This env var bridges the gap.
2. **Admin CLI files included** — v2 omitted admin CLI files (`schema.py`, `models.py`, `analysis.py`). These also have `openai-text-embedding-3-small` defaults that must be updated to `text-embedding-3-small` for consistency with the analysis service.
3. **Analysis Service infra files included** — `docker-compose.yml` and `.env.example` need `SOW_LLM_EMBEDDING_MODEL` added.
4. **`youtube_transcript.py` import update** — v2 didn't mention updating `youtube_transcript.py`'s `LLMConfigError` import from `.lrc` to `..workers.exceptions`. This is needed since `LLMConfigError` moves to the shared module.
5. **`LRCWorkerError` inheritance change** — `LRCWorkerError` changes from `LRCWorkerError(Exception)` to `LRCWorkerError(WorkerError)` so it inherits from the shared base while remaining a local class with all existing callers unchanged.

## Variable Mapping

| Current | New | Scope |
|---|---|---|
| `SOW_OPENAI_API_KEY` | `SOW_LLM_API_KEY` | Analysis Service + Web App |
| _(none)_ | `SOW_LLM_BASE_URL` | Web App (already exists in Analysis Service) |
| _(none)_ | `SOW_LLM_EMBEDDING_MODEL` | Analysis Service + Web App |

> **Note:** `SOW_LLM_MODEL` is NOT reused — the embedding model is different from the LLM chat model (`openai/gpt-4o-mini`). The embedding model name for API calls is `SOW_LLM_EMBEDDING_MODEL` (default: `text-embedding-3-small`). The `model_version` DB label remains hardcoded as `text-embedding-3-small` (provider-agnostic).

## Files to Modify (19 files)

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

- **Replace** `LRCWorkerError(Exception)` with `LRCWorkerError(WorkerError)` — inherits from shared base, remains a local class:
  ```python
  # Remove:
  class LRCWorkerError(Exception): ...

  # Add:
  from ..workers.exceptions import WorkerError

  class LRCWorkerError(WorkerError): ...
  ```
- **Remove** the local `LLMConfigError` definition and import from shared module:
  ```python
  # Remove:
  class LLMConfigError(LRCWorkerError): ...

  # Add:
  from ..workers.exceptions import LLMConfigError
  ```
- **Keep** `WhisperTranscriptionError`, `LLMAlignmentError`, `Qwen3RefinementError` as subclasses of `LRCWorkerError` (unchanged).

### 3. `services/analysis/src/sow_analysis/workers/youtube_transcript.py`

- **Update** `LLMConfigError` import from `.lrc` to `..workers.exceptions`:
  ```python
  # Before (line 18):
  from .lrc import LLMConfigError, LRCLine, LRCWorkerError

  # After:
  from ..workers.exceptions import LLMConfigError
  from .lrc import LRCLine, LRCWorkerError
  ```

### 4. `services/analysis/src/sow_analysis/config.py`

- **Add** `SOW_LLM_EMBEDDING_MODEL` field after the existing LLM config block:
  ```python
  # LLM Configuration (OpenAI-compatible API for LRC alignment)
  # Supports OpenRouter, nano-gpt.com, synthetic.new, or OpenAI direct
  SOW_LLM_API_KEY: str = ""
  SOW_LLM_BASE_URL: str = ""  # e.g., "https://openrouter.ai/api/v1"
  SOW_LLM_MODEL: str = ""  # e.g., "openai/gpt-4o-mini" for OpenRouter

  # Embedding Model Configuration (OpenAI-compatible API for embedding generation)
  # Provider-specific model name for the embeddings API call.
  # The DB model_version label is always "text-embedding-3-small" (provider-agnostic).
  SOW_LLM_EMBEDDING_MODEL: str = "text-embedding-3-small"
  ```

### 5. `services/analysis/src/sow_analysis/workers/embedder.py`

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
- **`_embed_texts()`** — Use `settings.SOW_LLM_EMBEDDING_MODEL` instead of hardcoded model name:
  ```python
  # Before
  model="text-embedding-3-small",

  # After
  model=settings.SOW_LLM_EMBEDDING_MODEL,
  ```
- **`model_version` field** in `EmbeddingJobResult` — Change from `"openai-text-embedding-3-small"` to `"text-embedding-3-small"` (drop the `openai-` prefix since the provider is now configurable).

### 6. `services/analysis/src/sow_analysis/models.py`

- **`EmbeddingJobResult.model_version`** default — Change from `"openai-text-embedding-3-small"` to `"text-embedding-3-small"`:
  ```python
  # Before (line 160)
  model_version: str = "openai-text-embedding-3-small"

  # After
  model_version: str = "text-embedding-3-small"
  ```

### 7. `services/analysis/src/sow_analysis/routes/health.py`

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
            model=settings.SOW_LLM_EMBEDDING_MODEL,
            input="health check",
            dimensions=1536,
        )

        return {
            "status": "healthy",
            "model": settings.SOW_LLM_EMBEDDING_MODEL,
            "dimensions": len(response.data[0].embedding),
        }

    except Exception as e:
        logger.warning(f"Embedding health check failed: {e}")
        return {
            "status": "unhealthy",
            "model": settings.SOW_LLM_EMBEDDING_MODEL,
            "error": str(e),
        }
```

- **Add to `health_check()` response** — add `"embedding": check_embedding_connection()` to the `services` dict.

### 8. `services/analysis/docker-compose.yml`

- **Add** `SOW_LLM_EMBEDDING_MODEL` to the `x-common-env` anchor (after the existing LLM config block, line 18):
  ```yaml
  # LLM Configuration for LRC generation
  SOW_LLM_API_KEY: ${SOW_LLM_API_KEY}
  SOW_LLM_BASE_URL: ${SOW_LLM_BASE_URL}
  SOW_LLM_MODEL: ${SOW_LLM_MODEL}
  # Embedding Model Configuration
  SOW_LLM_EMBEDDING_MODEL: ${SOW_LLM_EMBEDDING_MODEL:-text-embedding-3-small}
  ```

### 9. `services/analysis/.env.example`

- **Add** `SOW_LLM_EMBEDDING_MODEL` documentation after the existing LLM config block (after line 65):
  ```
  # ========================================
  # Embedding Model Configuration (Required for embedding generation)
  # ========================================

  SOW_LLM_EMBEDDING_MODEL="text-embedding-3-small"
  # Provider-specific model name for the embeddings API call.
  # The DB model_version label is always "text-embedding-3-small" (provider-agnostic).
  # Examples:
  #   - OpenAI direct: text-embedding-3-small
  #   - OpenRouter: openai/text-embedding-3-small
  #   - NeuralWatt: text-embedding-3-small
  ```

### 10. `webapp/src/lib/embedding.ts`

**Current state:** Uses `SOW_OPENAI_API_KEY` only, no base URL.

**Changes:**

```typescript
// Before
const openai = new OpenAI({
  apiKey: process.env.SOW_OPENAI_API_KEY,
  timeout: 10_000,
  maxRetries: 2,
});

const MODEL = "text-embedding-3-small";

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

const EMBEDDING_MODEL = process.env.SOW_LLM_EMBEDDING_MODEL || "text-embedding-3-small";

const openai = new OpenAI({
  apiKey: process.env.SOW_LLM_API_KEY,
  baseURL: process.env.SOW_LLM_BASE_URL,
  timeout: 10_000,
  maxRetries: 2,
});

const MODEL = EMBEDDING_MODEL;
```

- **`QUERY_MODEL` export** — Change from `"openai-text-embedding-3-small"` to `"text-embedding-3-small"`.

> **Note:** The OpenAI Node SDK uses `baseURL` (camelCase), while the Python SDK uses `base_url` (snake_case). The guards are placed at module level so the app fails on the first request that imports this module (not at server startup — Next.js App Router loads server modules lazily per-request).

### 11. `webapp/src/lib/db/songs.ts`

**Replace `hasMismatchedModelVersion()` with per-song filtering in `semanticSearchSongs()`.**

The current `hasMismatchedModelVersion()` function runs a global `SELECT EXISTS` check — if ANY row has a mismatched `model_version`, ALL semantic search returns 503. This creates a full outage during the `model_version` label migration window.

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

### 12. `webapp/src/app/api/songs/search/semantic/route.ts`

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

### 13. `webapp/src/db/schema.ts`

**Update `model_version` defaults** — Change from `"openai-text-embedding-3-small"` to `"text-embedding-3-small"`:

```typescript
// Before (songEmbeddings and songLineEmbeddings tables)
.default("openai-text-embedding-3-small")

// After
.default("text-embedding-3-small")
```

> **Migration note:** This changes the *default* for new rows. Existing rows with `"openai-text-embedding-3-small"` will be excluded from semantic search results (filtered out by the `model_version` check in `semanticSearchSongs`) until the SQL migration is run. This is a graceful degradation — search still works for any songs that have been re-embedded or migrated, rather than a hard 503 for all songs. After running `npx drizzle-kit generate`, a new migration SQL file will be created that changes the column default. The drizzle migration `0008_*.sql` is left as a historical artifact.

**Remediation SQL** (run after deployment):
```sql
UPDATE song_embedding SET model_version = 'text-embedding-3-small' WHERE model_version = 'openai-text-embedding-3-small';
UPDATE song_line_embedding SET model_version = 'text-embedding-3-small' WHERE model_version = 'openai-text-embedding-3-small';
```

### 14. `webapp/.env.example`

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

# Embedding model name for API calls (provider-specific).
# Defaults to "text-embedding-3-small" if not set.
# Examples:
#   - OpenAI direct: text-embedding-3-small
#   - OpenRouter: openai/text-embedding-3-small
SOW_LLM_EMBEDDING_MODEL=
```

### 15. `webapp/.env.production.example`

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

# Provider-specific embedding model name for API calls.
# Defaults to "text-embedding-3-small" if not set.
# Examples:
#   - OpenAI direct: text-embedding-3-small
#   - OpenRouter: openai/text-embedding-3-small
SOW_LLM_EMBEDDING_MODEL=
```

### 16. `src/stream_of_worship/admin/db/schema.py`

**Update `model_version` defaults** in both CREATE TABLE statements:

```python
# Before (lines 121, 135)
model_version TEXT NOT NULL DEFAULT 'openai-text-embedding-3-small',

# After
model_version TEXT NOT NULL DEFAULT 'text-embedding-3-small',
```

### 17. `src/stream_of_worship/admin/db/models.py`

**Update `model_version` defaults** in both dataclasses:

```python
# Before (lines 432, 455)
model_version: str = "openai-text-embedding-3-small"

# After
model_version: str = "text-embedding-3-small"
```

### 18. `src/stream_of_worship/admin/services/analysis.py`

**Update `model_version` fallback default** (line 662):

```python
# Before
model_version=result_data.get(
    "model_version", "openai-text-embedding-3-small"
),

# After
model_version=result_data.get(
    "model_version", "text-embedding-3-small"
),
```

Also update the `EmbeddingResult` dataclass default (line 88):

```python
# Before
model_version: str = "openai-text-embedding-3-small"

# After
model_version: str = "text-embedding-3-small"
```

## Test Files to Modify (3 files)

### 19. `webapp/src/test/api/songs/search/semantic.test.ts`

**Update mock and test data:**

- Line 24: `QUERY_MODEL: "openai-text-embedding-3-small"` → `QUERY_MODEL: "text-embedding-3-small"`
- Line 55: `modelVersion: "openai-text-embedding-3-small"` → `modelVersion: "text-embedding-3-small"`
- **Remove** the `hasMismatchedModelVersion` mock (line 30) and the corresponding test case (lines 116-124: "returns 503 when model version mismatch (pre-check)").
- **Remove** the `hasMismatchedModelVersion` import from the `vi.mock("@/lib/db/songs", ...)` block.
- **Update** `semanticSearchSongs` mock calls to include the third `expectedModelVersion` parameter where assertions check call arguments (lines 165, 176).
- **Add** a new test case: "excludes songs with mismatched model_version from results" — mock `semanticSearchSongs` to return only matching songs, verify the route returns 200 with filtered results.

### 20. `webapp/src/test/db/schema.test.ts`

**Update model_version default assertions:**

- Line 268-269: `expect(songEmbeddings.modelVersion.default).toBe("openai-text-embedding-3-small")` → `expect(songEmbeddings.modelVersion.default).toBe("text-embedding-3-small")`
- Line 272-273: `expect(songLineEmbeddings.modelVersion.default).toBe("openai-text-embedding-3-small")` → `expect(songLineEmbeddings.modelVersion.default).toBe("text-embedding-3-small")`

### 21. `webapp/src/test/deployment/deployment.test.ts`

**Add three new test cases** in the existing `.env.production.example` describe block (after line 249):

```typescript
it("documents SOW_LLM_API_KEY", () => {
  const content = readEnvExample();
  expect(content).toContain("SOW_LLM_API_KEY=");
});

it("documents SOW_LLM_BASE_URL", () => {
  const content = readEnvExample();
  expect(content).toContain("SOW_LLM_BASE_URL=");
});

it("documents SOW_LLM_EMBEDDING_MODEL", () => {
  const content = readEnvExample();
  expect(content).toContain("SOW_LLM_EMBEDDING_MODEL=");
});
```

## Files NOT Modified

| File | Reason |
|---|---|
| `services/analysis/src/sow_analysis/workers/queue.py` | No `SOW_OPENAI_API_KEY` reference. `EmbeddingWorker` import is unchanged. `LRCWorkerError` import still works (class still exists, just inherits from `WorkerError` now). |
| `services/analysis/src/sow_analysis/workers/__init__.py` | Still imports `LRCWorkerError` from `.lrc` — no change needed since `LRCWorkerError` remains in `lrc.py`. |
| `webapp/drizzle/0008_*.sql` | Historical migration file — left as-is. A new migration will be generated by `drizzle-kit generate` after `schema.ts` changes. |
| `specs/semantic-search-implementation*.md` | Historical design docs — left as-is per decision. |
| `reports/handover_semantic_search_v3.md` | Same. |

## Risks & Deployment Notes

### Hard Cutover — No Backward Compatibility

This is a hard cutover. `SOW_OPENAI_API_KEY` will no longer be read by any component. Env vars must be updated **before** code deployment:

1. **Analysis Service (Docker)**: `SOW_LLM_API_KEY` and `SOW_LLM_BASE_URL` are already configured (used by LRC worker). Add `SOW_LLM_EMBEDDING_MODEL` if using a provider that requires a different model name (e.g., OpenRouter: `openai/text-embedding-3-small`).
2. **Web App (Vercel)**: Add `SOW_LLM_API_KEY`, `SOW_LLM_BASE_URL`, and optionally `SOW_LLM_EMBEDDING_MODEL` to Vercel environment variables. Remove `SOW_OPENAI_API_KEY` after deployment.
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

The Python `EmbeddingWorker` now validates env vars at construction time. If `SOW_LLM_API_KEY` or `SOW_LLM_BASE_URL` is unset, the worker will fail to instantiate. In the Analysis Service, `EmbeddingWorker` is constructed in `queue.py` per job (not at module import time) — so the service will start but individual embedding jobs will fail. This is intentional (fail fast per-job) and consistent with how the LRC worker would fail on its first job. The health check endpoint (`check_embedding_connection()`) provides early warning.

### SOW_LLM_EMBEDDING_MODEL Default

The default value `text-embedding-3-small` works for OpenAI direct and most providers. OpenRouter users must set `SOW_LLM_EMBEDDING_MODEL=openai/text-embedding-3-small`. The health check endpoint will catch misconfiguration by making a live API call.

## Post-Implementation Verification

```bash
# Analysis Service — lint + import check
cd services/analysis
PYTHONPATH=src python -c "from sow_analysis.workers.embedder import EmbeddingWorker; print('OK')"
PYTHONPATH=src python -c "from sow_analysis.routes.health import check_embedding_connection; print('OK')"
PYTHONPATH=src python -c "from sow_analysis.workers.exceptions import LLMConfigError, WorkerError; print('OK')"
PYTHONPATH=src python -c "from sow_analysis.workers.lrc import LRCWorkerError; assert issubclass(LRCWorkerError, WorkerError); print('OK')"
PYTHONPATH=src python -c "from sow_analysis.workers.youtube_transcript import YouTubeTranscriptError; print('OK')"

# Web App — lint + test
cd webapp
pnpm lint
pnpm test
```

## Out of Scope / Follow-up

- **Vercel environment variables**: Must be manually updated in the Vercel dashboard (add `SOW_LLM_API_KEY`, `SOW_LLM_BASE_URL`, `SOW_LLM_EMBEDDING_MODEL`; remove `SOW_OPENAI_API_KEY`).
- **SQL migration for model_version**: Must be run against the production Neon Postgres database after deployment.
- **Drizzle migration generation**: Run `npx drizzle-kit generate` after `schema.ts` changes to produce the column default migration.
- **Historical specs/reports**: Left as-is — they accurately reflect the design at time of writing.
- **Separate embedding env vars** (`SOW_EMBEDDING_API_KEY` / `SOW_EMBEDDING_BASE_URL`): Deferred. If a deployment needs different providers for LLM chat vs. embeddings, a follow-up can add fallback vars that default to `SOW_LLM_*`.
