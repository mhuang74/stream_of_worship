# Consolidate Embedding Env Vars to SOW_LLM_*

## Summary

Replace the standalone `SOW_OPENAI_API_KEY` environment variable with the existing `SOW_LLM_API_KEY` + `SOW_LLM_BASE_URL` pair across both the Analysis Service and Web App. This eliminates a redundant env var and enables OpenAI-compatible embedding providers (OpenRouter, NeuralWatt, etc.) — matching the pattern already used by the LRC and YouTube transcript workers.

The embedding model name (`text-embedding-3-small`) remains hardcoded per prior decision.

## Motivation

- **Two env vars for the same provider family**: `SOW_OPENAI_API_KEY` (embedding) and `SOW_LLM_API_KEY` (LRC/LLM) often point to the same OpenAI-compatible service. Maintaining both is confusing.
- **No base URL for embedding**: `SOW_OPENAI_API_KEY` hardcodes OpenAI's endpoint. Users of OpenRouter or other compatible providers cannot use the embedding worker.
- **Inconsistent config pattern**: The embedder reads `os.environ.get("SOW_OPENAI_API_KEY")` directly, while LRC/YouTube workers use `settings.SOW_LLM_*` from pydantic-settings.

## Variable Mapping

| Current | New | Scope |
|---|---|---|
| `SOW_OPENAI_API_KEY` | `SOW_LLM_API_KEY` | Analysis Service + Web App |
| _(none)_ | `SOW_LLM_BASE_URL` | Web App (already exists in Analysis Service) |

> **Note:** `SOW_LLM_MODEL` is NOT reused — the embedding model (`text-embedding-3-small`) is different from the LLM chat model (`openai/gpt-4o-mini`). The embedding model remains hardcoded.

## Files to Modify (7 files)

### 1. `services/analysis/src/sow_analysis/workers/embedder.py`

**Current state:** Reads `SOW_OPENAI_API_KEY` via `os.environ.get()`, no base URL, no validation.

**Changes:**

- **Remove** `import os` (no longer needed if no other os usage; verify first)
- **Add** `from ..config import settings` import
- **`EmbeddingWorker.__init__()`** — Replace:
  ```python
  # Before
  self._client = OpenAI(
      api_key=os.environ.get("SOW_OPENAI_API_KEY"),
      timeout=60.0,
      max_retries=2,
  )

  # After
  self._client = OpenAI(
      api_key=settings.SOW_LLM_API_KEY,
      base_url=settings.SOW_LLM_BASE_URL,
      timeout=60.0,
      max_retries=2,
  )
  ```
- **Add validation** in `embed_song()` (before API call, matching `lrc.py` pattern):
  ```python
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
  ```
- **Import `LLMConfigError`** from `..workers.lrc` (or define a shared exception in `..models` if preferred — check where `LLMConfigError` is currently defined).
- **`model_version` field** in `EmbeddingJobResult` — Change from hardcoded `"openai-text-embedding-3-small"` to `"text-embedding-3-small"` (drop the `openai-` prefix since the provider is now configurable). This affects the DB `model_version` column default in the webapp schema — see file 5 below.

### 2. `services/analysis/src/sow_analysis/routes/health.py`

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

### 3. `webapp/src/lib/embedding.ts`

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
const openai = new OpenAI({
  apiKey: process.env.SOW_LLM_API_KEY,
  baseURL: process.env.SOW_LLM_BASE_URL,
  timeout: 10_000,
  maxRetries: 2,
});
```

- **`QUERY_MODEL` export** — Change from `"openai-text-embedding-3-small"` to `"text-embedding-3-small"` (drop `openai-` prefix, matching the analysis service change).

> **Note:** The OpenAI Node SDK uses `baseURL` (camelCase), while the Python SDK uses `base_url` (snake_case). Both are required — if `SOW_LLM_BASE_URL` is unset, the OpenAI SDK will default to `https://api.openai.com/v1`, but per user decision we want it to be **required**. Add a runtime guard:

```typescript
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
```

Place these guards at module level (outside the `OpenAI` constructor) so the app fails fast on startup if the vars are missing, rather than failing on first search request.

### 4. `webapp/.env.example`

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

### 5. `webapp/.env.production.example`

**Replace** the "OpenAI API (Semantic Search)" section (lines 126-132):

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

### 6. `webapp/src/db/schema.ts`

**Update `model_version` defaults** — The `model_version` column default in both `songEmbeddings` and `songLineEmbeddings` tables currently defaults to `"openai-text-embedding-3-small"`. Change to `"text-embedding-3-small"` to match the new `QUERY_MODEL` export:

```typescript
// Before (line 265, 293)
.default("openai-text-embedding-3-small")

// After
.default("text-embedding-3-small")
```

> **Migration note:** This changes the *default* for new rows. Existing rows with `"openai-text-embedding-3-small"` will continue to work — the `hasMismatchedModelVersion()` check compares against `QUERY_MODEL`, which will now be `"text-embedding-3-small"`. This means **existing embeddings will be flagged as mismatched** after deployment. The admin must re-generate embeddings for all songs, or run a SQL migration to update the `model_version` column:

```sql
UPDATE song_embedding SET model_version = 'text-embedding-3-small' WHERE model_version = 'openai-text-embedding-3-small';
UPDATE song_line_embedding SET model_version = 'text-embedding-3-small' WHERE model_version = 'openai-text-embedding-3-small';
```

### 7. `webapp/src/test/api/songs/search/semantic.test.ts`

**Update mock and test data** to use the new model version string:

- Line 24: `QUERY_MODEL: "openai-text-embedding-3-small"` → `QUERY_MODEL: "text-embedding-3-small"`
- Line 55: `modelVersion: "openai-text-embedding-3-small"` → `modelVersion: "text-embedding-3-small"`

### 8. `webapp/src/test/db/schema.test.ts`

**Update model_version default assertions:**

- Line 268-269: `expect(songEmbeddings.modelVersion.default).toBe("openai-text-embedding-3-small")` → `expect(songEmbeddings.modelVersion.default).toBe("text-embedding-3-small")`
- Line 272-273: `expect(songLineEmbeddings.modelVersion.default).toBe("openai-text-embedding-3-small")` → `expect(songLineEmbeddings.modelVersion.default).toBe("text-embedding-3-small")`

## Files NOT Modified

| File | Reason |
|---|---|
| `services/analysis/docker-compose.yml` | `SOW_LLM_API_KEY` and `SOW_LLM_BASE_URL` are already passed through. No new env vars needed. |
| `services/analysis/.env.example` | Already documents `SOW_LLM_API_KEY` and `SOW_LLM_BASE_URL`. No `SOW_OPENAI_API_KEY` reference exists here. |
| `services/analysis/src/sow_analysis/config.py` | `SOW_LLM_API_KEY` and `SOW_LLM_BASE_URL` already defined. No new fields needed. |
| `services/analysis/src/sow_analysis/workers/queue.py` | No `SOW_OPENAI_API_KEY` reference. `EmbeddingWorker` import is unchanged. |
| `specs/semantic-search-implementation*.md` | Historical design docs — left as-is per decision. |
| `specs/semantic-search-implementation-v2.md` | Same. |
| `specs/semantic-search-implementation-v3.md` | Same. |
| `reports/handover_semantic_search_v3.md` | Same. |
| `webapp/src/app/api/songs/search/semantic/route.ts` | No env var references — uses `embedQuery()` and `QUERY_MODEL` from `@/lib/embedding`. |
| `webapp/src/test/deployment/deployment.test.ts` | No `SOW_OPENAI_API_KEY` assertions currently exist. Add new assertions for `SOW_LLM_API_KEY` and `SOW_LLM_BASE_URL` (see below). |

## Additional: Deployment Test Coverage

`webapp/src/test/deployment/deployment.test.ts` currently has no assertions for embedding env vars. Add two new test cases in the existing `.env.production.example` describe block:

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

## Risks & Deployment Notes

### Hard Cutover — No Backward Compatibility

This is a hard cutover. `SOW_OPENAI_API_KEY` will no longer be read by any component. Env vars must be updated **before** code deployment:

1. **Analysis Service (Docker)**: `SOW_LLM_API_KEY` and `SOW_LLM_BASE_URL` are already configured (used by LRC worker). No action needed if they're already set.
2. **Web App (Vercel)**: Add `SOW_LLM_API_KEY` and `SOW_LLM_BASE_URL` to Vercel environment variables. Remove `SOW_OPENAI_API_KEY` after deployment.
3. **Local dev `.env.local`**: Developers must add `SOW_LLM_API_KEY` and `SOW_LLM_BASE_URL`, and can remove `SOW_OPENAI_API_KEY`.

### model_version Column Migration

The `model_version` default changes from `"openai-text-embedding-3-small"` to `"text-embedding-3-small"`. After deployment:

- **New embeddings** will have `model_version = "text-embedding-3-small"`.
- **Existing embeddings** still have `model_version = "openai-text-embedding-3-small"`.
- The `hasMismatchedModelVersion()` check compares DB `model_version` against `QUERY_MODEL` (now `"text-embedding-3-small"`), so **existing embeddings will be flagged as mismatched** and semantic search will return 503 for those songs.

**Remediation options:**
1. **SQL migration** (fast, no re-embedding needed — the vectors themselves are unchanged):
   ```sql
   UPDATE song_embedding SET model_version = 'text-embedding-3-small' WHERE model_version = 'openai-text-embedding-3-small';
   UPDATE song_line_embedding SET model_version = 'text-embedding-3-small' WHERE model_version = 'openai-text-embedding-3-small';
   ```
2. **Re-generate embeddings** via the Analysis Service embedding endpoint (slower, but ensures consistency).

Option 1 is recommended since the embedding vectors are identical — only the label changed.

### Embedding Provider Must Support /v1/embeddings

Not all OpenAI-compatible providers support the embeddings endpoint. OpenRouter supports it for select models. OpenAI direct supports it. Verify the chosen provider supports `text-embedding-3-small` before deploying.

## Post-Implementation Verification

```bash
# Analysis Service — lint + import check
cd services/analysis
PYTHONPATH=src python -c "from sow_analysis.workers.embedder import EmbeddingWorker; print('OK')"
PYTHONPATH=src python -c "from sow_analysis.routes.health import check_embedding_connection; print('OK')"

# Web App — lint + test
cd webapp
pnpm lint
pnpm test
```

## Out of Scope / Follow-up

- **Vercel environment variables**: Must be manually updated in the Vercel dashboard (add `SOW_LLM_API_KEY`, `SOW_LLM_BASE_URL`; remove `SOW_OPENAI_API_KEY`).
- **SQL migration for model_version**: Must be run against the production Neon Postgres database after deployment.
- **Historical specs/reports**: Left as-is — they accurately reflect the design at time of writing.
