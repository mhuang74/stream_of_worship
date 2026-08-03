# Songset Constructor Skill

Agentic worship songset constructor. Plans multi-song worship sets following a
5-phase arc (Call → Thanksgiving → Worship → Response → Commission), selecting
songs from the catalog pool with smooth tempo and key transitions.

See [SKILL.md](./SKILL.md) for the full agent workflow, constraints (H0–H8),
scoring, and report generation.

## Operating Mode & LLM Configuration

**This skill is agentic-only. It does NOT use a separate LLM API key.**

The skill runs inside an agent (e.g. OpenCode) that IS the LLM planner,
validator, refiner, and judge. The bundled scripts are deterministic and never
call a chat LLM, so **no `SOW_LLM_API_KEY` / `SOW_LLM_MODEL` are required** to
run this skill.

### Environment variables used by the skill scripts

| Variable | Used by | Purpose |
|----------|---------|---------|
| `SOW_DATABASE_URL` (or `config.toml`) | all scripts | Catalog database access |
| `SOW_R2_ACCESS_KEY_ID` / `SOW_R2_SECRET_ACCESS_KEY` | `get_lyrics.py`, `preflight.sh` | LRC lyrics from Cloudflare R2 |
| `SOW_EMBEDDING_API_KEY` / `SOW_EMBEDDING_BASE_URL` | `semantic_search.py` (semantic mode only) | pgvector semantic search; embedding model is hardcoded to `text-embedding-3-small` |

`preflight.sh` verifies DB connectivity, the `theme_anchors` table (12 rows),
R2 credentials, and the pool cache before a run.

## Related: Agentic Constructor (uses an LLM)

The **related** agentic constructor in `ops/admin-cli` and `lab/poc-scripts`
(`poc/songset_constructor`) DOES call an LLM. That mode is configured via:

| Variable | Purpose |
|----------|---------|
| `SOW_LLM_API_KEY` | API key for the OpenAI-compatible chat provider (required) |
| `SOW_LLM_MODEL` | Chat model name (required; no hardcoded default — set via env or `--llm-model`) |
| `SOW_LLM_BASE_URL` | Optional base URL for an OpenAI-compatible gateway |

The chat model is built in `graph/llm.py` with `temperature=0.2` and
`max_retries=2`. Agentic mode fails fast if `SOW_LLM_API_KEY` or
`SOW_LLM_MODEL` is missing. This is distinct from the skill itself, which needs
no LLM credentials.
