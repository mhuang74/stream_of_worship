# Separate Chat and Embedding Env Vars (v1)

## Problem

All components that use LLM functionality share a single set of environment
variables — `SOW_LLM_API_KEY`, `SOW_LLM_BASE_URL`, and
`SOW_LLM_EMBEDDING_MODEL` — for both **chat completion** (LRC alignment,
YouTube transcript correction, agentic songset construction) and **text
embedding** (semantic search, song/line embedding generation). Some providers
(e.g., NeuralWatt) offer chat models but no embedding endpoint. When
`SOW_LLM_BASE_URL` points at such a provider, every embedding call fails —
the Web App returns `503` on semantic search, and the Analysis Service's
`EmbeddingWorker` jobs fail.

There is no way to configure chat and embedding to use different providers
without code changes.

## Goal

Introduce separate environment variables for the embedding client so that chat
and embedding can target different OpenAI-compatible providers independently.
The `SOW_LLM_*` vars become chat-only; the new `SOW_EMBEDDING_*` vars become
embedding-only. `SOW_LLM_EMBEDDING_MODEL` is removed entirely — it is replaced
by `SOW_EMBEDDING_MODEL`.

### Design Principles

- **Clean separation:** `SOW_LLM_*` = chat, `SOW_EMBEDDING_*` = embeddings.
  No var is shared between the two use cases. No multi-level fallback chains.
- **Breaking change accepted:** `SOW_LLM_EMBEDDING_MODEL` is removed. Any
  deployment that set it must rename to `SOW_EMBEDDING_MODEL`. This is
  preferable to a confusing three-level fallback (`SOW_EMBEDDING_MODEL` →
  `SOW_LLM_EMBEDDING_MODEL` → default) that silently masks misconfiguration.

## Design

### Environment Variables After This Change

| Env Var | Scope | Purpose | Default |
|---------|-------|---------|---------|
| `SOW_LLM_API_KEY` | Chat | API key for the chat provider | _(none — required for chat features)_ |
| `SOW_LLM_BASE_URL` | Chat | Base URL for the chat provider | _(none — required for chat features)_ |
| `SOW_LLM_MODEL` | Chat | Chat model id (e.g., `qwen3.6-35b`) | _(none — required for chat features)_ |
| `SOW_EMBEDDING_API_KEY` | Embedding | API key for the embedding provider | _(none — required for embedding features)_ |
| `SOW_EMBEDDING_BASE_URL` | Embedding | Base URL for the embedding provider | _(none — required for embedding features)_ |
| `SOW_EMBEDDING_MODEL` | Embedding | Provider-specific embedding model name | `text-embedding-3-small` |

### Removed

| Env Var | Replaced By |
|---------|-------------|
| `SOW_LLM_EMBEDDING_MODEL` | `SOW_EMBEDDING_MODEL` |

### Resolution Rules

```
# Chat
chat_api_key  = SOW_LLM_API_KEY
chat_base_url = SOW_LLM_BASE_URL
chat_model    = SOW_LLM_MODEL

# Embedding
embedding_api_key  = SOW_EMBEDDING_API_KEY
embedding_base_url = SOW_EMBEDDING_BASE_URL
embedding_model    = SOW_EMBEDDING_MODEL  (default: "text-embedding-3-small")
```

No cross-group fallback. If an embedding var is unset, the embedding feature
fails with a clear error — it does not silently use the chat provider's
credentials.

### DB `model_version` Label

The DB `model_version` label stored in `song_embedding` /
`song_line_embedding` remains hardcoded as `"text-embedding-3-small"`
(provider-agnostic). This is the value used for version checking at query
time, not the API call model name.

### Affected Components

| Component | Chat client | Embedding client | Changes |
|----------|-------------|------------------|---------|
| **Web App** | _(none)_ | `embedding.ts` | Switch from `SOW_LLM_*` to `SOW_EMBEDDING_*`; remove `SOW_LLM_EMBEDDING_MODEL` |
| **Analysis Service** | `lrc.py`, `youtube_transcript.py`, `health.py` (`check_llm_connection`) | `embedder.py`, `health.py` (`check_embedding_connection`) | `config.py` + `embedder.py` + `health.py` + `main.py` |
| **POC Scripts** | `gen_lrc_youtube.py`, `graph/llm.py` | `regen_theme_anchors.py` | `regen_theme_anchors.py` only |
| Admin CLI | _(none — delegates to Analysis Service)_ | _(none — delegates to Analysis Service)_ | No changes |
| Render Worker | _(none)_ | _(none)_ | No changes |
| Android App | _(none)_ | _(none)_ | No changes |

---

## Implementation Plan

### Phase 1: Analysis Service (Python)

#### 1.1 `ops/analysis-service/src/sow_analysis/config.py`

**Remove** the `SOW_LLM_EMBEDDING_MODEL` field (line 71):

```python
# DELETE this line:
SOW_LLM_EMBEDDING_MODEL: str = "text-embedding-3-small"
```

**Replace** it with a new embedding config block:

```python
# Embedding Provider Configuration (OpenAI-compatible API)
# Separate from SOW_LLM_* so chat and embedding can use different providers.
SOW_EMBEDDING_API_KEY: str = ""
SOW_EMBEDDING_BASE_URL: str = ""
SOW_EMBEDDING_MODEL: str = "text-embedding-3-small"
```

#### 1.2 `ops/analysis-service/src/sow_analysis/workers/embedder.py`

**`EmbeddingWorker.__init__`** (lines 27–44):

- Replace `settings.SOW_LLM_API_KEY` validation with
  `settings.SOW_EMBEDDING_API_KEY`.
- Replace `settings.SOW_LLM_BASE_URL` validation with
  `settings.SOW_EMBEDDING_BASE_URL`.
- Update `LLMConfigError` messages to reference `SOW_EMBEDDING_API_KEY` and
  `SOW_EMBEDDING_BASE_URL`.
- Construct the OpenAI client with the embedding-specific vars:

```python
self._client = OpenAI(
    api_key=settings.SOW_EMBEDDING_API_KEY,
    base_url=settings.SOW_EMBEDDING_BASE_URL,
    timeout=60.0,
    maxRetries=2,
)
```

**`EmbeddingWorker._embed_texts`** (line 89):

- Replace `settings.SOW_LLM_EMBEDDING_MODEL` with
  `settings.SOW_EMBEDDING_MODEL`.

#### 1.3 `ops/analysis-service/src/sow_analysis/routes/health.py`

**`check_embedding_connection`** (lines 93–126):

- Replace `settings.SOW_LLM_BASE_URL` check with
  `settings.SOW_EMBEDDING_BASE_URL`.
- Replace `settings.SOW_LLM_API_KEY` check with
  `settings.SOW_EMBEDDING_API_KEY`.
- Construct the OpenAI client with embedding-specific vars.
- Replace `model=settings.SOW_LLM_EMBEDDING_MODEL` with
  `model=settings.SOW_EMBEDDING_MODEL`.
- Update error messages to reference `SOW_EMBEDDING_*` var names.

**`check_llm_connection`** (lines 45–90):

- No changes — chat still uses `SOW_LLM_*` directly.

#### 1.4 `ops/analysis-service/src/sow_analysis/main.py`

**Startup config log** (lines 120–121):

- Add embedding provider info to the config table:

```python
("LLM", "model", settings.SOW_LLM_MODEL or "(not set)"),
("LLM", "provider", settings.SOW_LLM_BASE_URL or "(not set)"),
("Embedding", "model", settings.SOW_EMBEDDING_MODEL),
("Embedding", "provider", settings.SOW_EMBEDDING_BASE_URL or "(not set)"),
```

#### 1.5 `ops/analysis-service/src/sow_analysis/workers/lrc.py`

**`generate_lrc` function** (lines 581–618):

- No changes needed — LRC uses chat (`SOW_LLM_*`), not embeddings.

#### 1.6 `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py`

**`_correct_transcript_with_llm` function** (lines 375–401):

- No changes needed — YouTube transcript correction uses chat (`SOW_LLM_*`).

#### 1.7 `ops/analysis-service/docker-compose.yml`

**Replace** the existing embedding line (line 20):

```yaml
  # BEFORE:
  SOW_LLM_EMBEDDING_MODEL: ${SOW_LLM_EMBEDDING_MODEL:-text-embedding-3-small}

  # AFTER:
  SOW_EMBEDDING_API_KEY: ${SOW_EMBEDDING_API_KEY}
  SOW_EMBEDDING_BASE_URL: ${SOW_EMBEDDING_BASE_URL}
  SOW_EMBEDDING_MODEL: ${SOW_EMBEDDING_MODEL:-text-embedding-3-small}
```

#### 1.8 `ops/analysis-service/.env.example`

**Replace** the existing embedding block (lines 66–70):

```bash
# ========================================
# Embedding Provider Configuration (Required for embedding generation)
# ========================================
# Separate from SOW_LLM_* so chat and embedding can use different providers.
# Required for: song/line embedding generation (audio embed command).

SOW_EMBEDDING_API_KEY="sk-..."
# API key for the embedding provider.
# Supports: OpenRouter, OpenAI, nano-gpt, etc.

SOW_EMBEDDING_BASE_URL="https://api.openai.com/v1"
# Embedding API base URL.
# Examples:
#   - OpenAI: https://api.openai.com/v1
#   - OpenRouter: https://openrouter.ai/api/v1
#   - nano-gpt: https://nano-gpt.com/api/v1

SOW_EMBEDDING_MODEL="text-embedding-3-small"
# Provider-specific embedding model name.
# Examples:
#   - OpenAI direct: text-embedding-3-small
#   - OpenRouter: openai/text-embedding-3-small
```

#### 1.9 Analysis Service Tests

**`ops/analysis-service/tests/integration/test_config.py`:**

- Add tests verifying:
  - `SOW_EMBEDDING_MODEL` defaults to `"text-embedding-3-small"` when unset.
  - `SOW_EMBEDDING_API_KEY` / `SOW_EMBEDDING_BASE_URL` default to empty
    string when unset.
  - Setting `SOW_EMBEDDING_*` env vars populates the fields correctly.
  - `SOW_LLM_EMBEDDING_MODEL` is no longer a recognized field (setting it has
    no effect — pydantic `extra="ignore"` will silently drop it).

**`ops/analysis-service/tests/integration/test_lrc_worker.py`:**

- No changes needed — LRC worker still uses `SOW_LLM_*`.

---

### Phase 2: Web App (TypeScript / Next.js)

#### 2.1 `delivery/webapp/src/lib/embedding.ts`

**Replace** the entire env var resolution block (lines 1–27):

```typescript
import OpenAI from "openai";

if (!process.env.SOW_EMBEDDING_API_KEY) {
  throw new Error(
    "SOW_EMBEDDING_API_KEY environment variable not set. " +
    "Set this to your OpenAI-compatible API key for embeddings."
  );
}
if (!process.env.SOW_EMBEDDING_BASE_URL) {
  throw new Error(
    "SOW_EMBEDDING_BASE_URL environment variable not set. " +
    "Set this to your OpenAI-compatible API base URL for embeddings " +
    "(e.g., https://api.openai.com/v1)."
  );
}

const EMBEDDING_MODEL = process.env.SOW_EMBEDDING_MODEL || "text-embedding-3-small";

const openai = new OpenAI({
  apiKey: process.env.SOW_EMBEDDING_API_KEY,
  baseURL: process.env.SOW_EMBEDDING_BASE_URL,
  timeout: 10_000,
  maxRetries: 2,
});

const MODEL = EMBEDDING_MODEL;
const DIMENSIONS = 1536;

export async function embedQuery(text: string): Promise<number[]> {
  const response = await openai.embeddings.create({
    model: MODEL,
    input: text,
    dimensions: DIMENSIONS,
  });
  return response.data[0].embedding;
}

export const QUERY_MODEL = EMBEDDING_MODEL;
```

#### 2.2 `delivery/webapp/.env.example`

**Replace** the existing LLM embedding block (lines 42–59):

```bash
# Embedding API for semantic search (text-embedding-3-small).
# Required for the "Describe" tab in Browse Sheet.
# Separate from SOW_LLM_* (chat) so embedding can use a different provider.
# Supports OpenAI-compatible providers (OpenRouter, OpenAI, nano-gpt, etc.).
SOW_EMBEDDING_API_KEY=

# Embedding API base URL.
# Examples:
#   - OpenAI: https://api.openai.com/v1
#   - OpenRouter: https://openrouter.ai/api/v1
#   - nano-gpt: https://nano-gpt.com/api/v1
SOW_EMBEDDING_BASE_URL=

# Provider-specific embedding model name for API calls.
# Defaults to "text-embedding-3-small" if not set.
# Examples:
#   - OpenAI direct: text-embedding-3-small
#   - OpenRouter: openai/text-embedding-3-small
SOW_EMBEDDING_MODEL=
```

#### 2.3 `delivery/webapp/.env.production.example`

**Replace** the existing LLM embedding block (lines 145–165):

```bash
# -----------------------------------------------------------------------------
# Embedding API (Semantic Search)
# -----------------------------------------------------------------------------
# OpenAI-compatible API key for text-embedding-3-small, used by the "Describe"
# tab in Browse Sheet to embed user queries for semantic song search.
# Separate from SOW_LLM_* (chat) so embedding can use a different provider.
# Supports: OpenRouter, OpenAI, nano-gpt, etc.
# Cost: ~$0.02/1M tokens; typical query is ~10 tokens.
SOW_EMBEDDING_API_KEY=

# OpenAI-compatible API base URL for embedding generation.
# Examples:
#   - OpenAI: https://api.openai.com/v1
#   - OpenRouter: https://openrouter.ai/api/v1
#   - nano-gpt: https://nano-gpt.com/api/v1
SOW_EMBEDDING_BASE_URL=

# Provider-specific embedding model name for API calls.
# Defaults to "text-embedding-3-small" if not set.
# Examples:
#   - OpenAI direct: text-embedding-3-small
#   - OpenRouter: openai/text-embedding-3-small
SOW_EMBEDDING_MODEL=
```

#### 2.4 `delivery/webapp/src/test/deployment/deployment.test.ts`

**Replace** the three existing `SOW_LLM_*` embedding tests (lines 251–264):

```typescript
it("documents SOW_EMBEDDING_API_KEY", () => {
  const content = readEnvExample();
  expect(content).toContain("SOW_EMBEDDING_API_KEY=");
});

it("documents SOW_EMBEDDING_BASE_URL", () => {
  const content = readEnvExample();
  expect(content).toContain("SOW_EMBEDDING_BASE_URL=");
});

it("documents SOW_EMBEDDING_MODEL", () => {
  const content = readEnvExample();
  expect(content).toContain("SOW_EMBEDDING_MODEL=");
});
```

> **Note:** The existing `SOW_LLM_API_KEY` and `SOW_LLM_BASE_URL` tests
> (lines 251–258) should be **removed** — the Web App no longer uses these
> for any purpose. The Web App has no chat feature, so `SOW_LLM_*` vars are
> not needed in the webapp env at all.

#### 2.5 Web App Tests

**`delivery/webapp/src/test/api/songs/search/semantic.test.ts`:**

- No changes needed — `embedQuery` is mocked at the module level (line 22), so
  the env var resolution in `embedding.ts` is not exercised by these tests.

---

### Phase 3: POC Scripts (Python)

#### 3.1 `lab/poc-scripts/gen_lrc_youtube.py`

**Lines 185–187:**

- No changes needed — this script uses chat (`SOW_LLM_*`), not embeddings.

#### 3.2 `lab/poc-scripts/poc/songset_constructor/graph/llm.py`

**Lines 20–21:**

- No changes needed — `ChatOpenAI` uses chat (`SOW_LLM_*`), not embeddings.

#### 3.3 `lab/poc-scripts/poc/songset_constructor/regen_theme_anchors.py`

**Lines 33–39:**

- Replace `SOW_LLM_*` env var reads with `SOW_EMBEDDING_*`:

```python
api_key = os.environ.get("SOW_EMBEDDING_API_KEY")
if not api_key:
    raise RuntimeError(
        "SOW_EMBEDDING_API_KEY is required to regenerate theme anchors"
    )
base_url = os.environ.get("SOW_EMBEDDING_BASE_URL")
if not base_url:
    raise RuntimeError(
        "SOW_EMBEDDING_BASE_URL is required to regenerate theme anchors"
    )
model = os.environ.get("SOW_EMBEDDING_MODEL", "text-embedding-3-small")
embeddings = OpenAIEmbeddings(
    model=model,
    api_key=api_key,
    base_url=base_url,
)
```

#### 3.4 `lab/poc-scripts/poc/songset_constructor/README.md`

Update the "Theme Anchors" section:

```markdown
## Theme Anchors

Regenerate anchors only with a working embedding gateway:

```bash
uv run --project lab/poc-scripts --extra songset_constructor \
  python -m poc.songset_constructor.regen_theme_anchors
```

Set `SOW_EMBEDDING_API_KEY` / `SOW_EMBEDDING_BASE_URL` to an
OpenAI-compatible provider that supports the embeddings endpoint.
`SOW_EMBEDDING_MODEL` defaults to `text-embedding-3-small`.
```

---

### Phase 4: Documentation

#### 4.1 `DEVELOPER.md`

Update the "LLM / Embedding Environment Variables" section:

- **Remove** `SOW_LLM_EMBEDDING_MODEL` from the env var table.
- **Add** `SOW_EMBEDDING_API_KEY`, `SOW_EMBEDDING_BASE_URL`, and
  `SOW_EMBEDDING_MODEL` to the env var table.
- Update the "Usage by Component" table:
  - Web App: uses `SOW_EMBEDDING_*` only (no `SOW_LLM_*`).
  - Analysis Service: chat uses `SOW_LLM_*`, embedding uses `SOW_EMBEDDING_*`.
  - POC Scripts: chat uses `SOW_LLM_*`, embedding uses `SOW_EMBEDDING_*`.
- Update the "Provider Considerations" section to explain the clean
  separation and how to configure different providers for chat vs. embeddings.
- **Remove** the "Known limitation" note about separate env vars (resolved).

#### 4.2 `docs/research_semantic_song_search.md`

Update the env var reference table:

- Remove `SOW_LLM_EMBEDDING_MODEL`.
- Add `SOW_EMBEDDING_API_KEY`, `SOW_EMBEDDING_BASE_URL`,
  `SOW_EMBEDDING_MODEL`.
- Update the "Analysis Service + Web App" scope labels to reflect that chat
  and embedding now use separate var groups.

#### 4.3 `docs/lrc-job-flow.md`

No changes needed — LRC flow uses chat (`SOW_LLM_*`) only.

#### 4.4 `specs/consolidate-embedding-env-vars-to-sow-llm-v3.md`

Add a note at the top:

```markdown
> **Superseded:** This spec introduced `SOW_LLM_EMBEDDING_MODEL`. It has been
> replaced by `SOW_EMBEDDING_MODEL` (see
> `specs/separate-chat-and-embedding-env-vars-v1.md`) to cleanly separate chat
> and embedding env vars. `SOW_LLM_EMBEDDING_MODEL` is removed.
```

---

## Migration Guide

### Breaking Change: `SOW_LLM_EMBEDDING_MODEL` Removed

Any deployment that set `SOW_LLM_EMBEDDING_MODEL` must rename it to
`SOW_EMBEDDING_MODEL`. If unset, the default `text-embedding-3-small` applies.

### For deployments using the same provider for chat and embedding

Set both var groups to the same provider:

```bash
# Chat
SOW_LLM_API_KEY="sk-..."
SOW_LLM_BASE_URL="https://api.openai.com/v1"
SOW_LLM_MODEL="gpt-4o-mini"

# Embeddings (same provider)
SOW_EMBEDDING_API_KEY="sk-..."
SOW_EMBEDDING_BASE_URL="https://api.openai.com/v1"
SOW_EMBEDDING_MODEL="text-embedding-3-small"
```

### For deployments needing different chat/embedding providers

```bash
# Chat (LRC alignment, YouTube transcript, songset agent)
SOW_LLM_API_KEY="sk-70c8f64afdb90795753083cd0aaed698cdddd3c2cb0ed6d1b604fa08a3deeaa2"
SOW_LLM_BASE_URL="https://api.neuralwatt.com/v1"
SOW_LLM_MODEL="qwen3.6-35b"

# Embeddings (semantic search, song embedding generation)
SOW_EMBEDDING_API_KEY="sk-nano-41d07084-21ce-40bb-8565-83bea22e98b9"
SOW_EMBEDDING_BASE_URL="https://nano-gpt.com/api/v1"
SOW_EMBEDDING_MODEL="text-embedding-3-small"
```

### For Vercel (Web App)

1. **Add** `SOW_EMBEDDING_API_KEY`, `SOW_EMBEDDING_BASE_URL`, and optionally
   `SOW_EMBEDDING_MODEL` in the Vercel project environment variables
   dashboard.
2. **Remove** `SOW_LLM_API_KEY`, `SOW_LLM_BASE_URL`, and
   `SOW_LLM_EMBEDDING_MODEL` — the Web App no longer reads these.

### For Analysis Service (Docker)

1. **Add** `SOW_EMBEDDING_API_KEY`, `SOW_EMBEDDING_BASE_URL`, and optionally
   `SOW_EMBEDDING_MODEL` to the `.env` file.
2. **Remove** `SOW_LLM_EMBEDDING_MODEL` — no longer recognized.
3. The `docker-compose.yml` changes pass the new vars through automatically.

### For local dev (`/opt/sow/.env`)

```bash
# Chat (LRC alignment, YouTube transcript, songset agent)
SOW_LLM_API_KEY="sk-70c8f64afdb90795753083cd0aaed698cdddd3c2cb0ed6d1b604fa08a3deeaa2"
SOW_LLM_BASE_URL="https://api.neuralwatt.com/v1"
SOW_LLM_MODEL="qwen3.6-35b"

# Embeddings (semantic search, song embedding generation)
SOW_EMBEDDING_API_KEY="sk-nano-41d07084-21ce-40bb-8565-83bea22e98b9"
SOW_EMBEDDING_BASE_URL="https://nano-gpt.com/api/v1"
SOW_EMBEDDING_MODEL="text-embedding-3-small"
```

---

## Verification

### Analysis Service

```bash
cd ops/analysis-service
PYTHONPATH=src pytest tests/integration/test_config.py -v
PYTHONPATH=src pytest tests/integration/test_lrc_worker.py -v
```

### Web App

```bash
cd delivery/webapp
pnpm test -- --reporter=verbose src/test/deployment/deployment.test.ts
pnpm test -- --reporter=verbose src/test/api/songs/search/semantic.test.ts
pnpm lint
```

### Manual Smoke Test

1. Set `SOW_LLM_*` to NeuralWatt (chat-only) and `SOW_EMBEDDING_*` to an
   embedding-capable provider.
2. Start the Web App dev server.
3. Navigate to Browse Sheet → Describe tab.
4. Enter a search query and confirm results return (no `503`).
5. Start the Analysis Service and submit an embedding job via
   `sow-admin audio embed --song-id <id>`.
6. Confirm the embedding job completes successfully.
7. Hit `GET /api/v1/health` on the Analysis Service and confirm both `llm`
   and `embedding` sub-statuses report `healthy`.

---

## Out of Scope

- Adding embedding-specific timeout/retry configuration — the existing
  timeouts in `embedding.ts` (10s) and `embedder.py` (60s) are adequate.
- Adding a startup health probe for the Web App (the lazy-load behavior is
  documented; a startup probe is a separate enhancement).
- Changes to the Admin CLI, Render Worker, or Android App — none of these
  components read `SOW_LLM_*` or `SOW_EMBEDDING_*` directly.
- Removing `SOW_LLM_API_KEY` / `SOW_LLM_BASE_URL` from the Web App env —
  these are removed as part of this change since the Web App has no chat
  feature and never used them for anything other than embeddings.
