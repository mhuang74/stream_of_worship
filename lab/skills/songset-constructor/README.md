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

## Script Reference

| Script | Input | Output |
|--------|-------|--------|
| `preflight.sh` | none | exit code 0/1 + diagnostic text to stdout |
| `fetch_pool.py` | CLI flags | JSON array of SongCandidate (stdout) |
| `enrich_pool.py` | JSON array of SongCandidate (stdin/`--input`) | JSON array of enriched SongCandidate (stdout) |
| `build_transitions.py` | JSON array of enriched SongCandidate (stdin/`--input`) | JSON object `{"transitions": [...], "pool": [...]}` (stdout) |
| `score_songset.py` | JSON object `{"items": [...], "pool": [...], "transitions": [...], "config": {...}}` (stdin/`--input`) | JSON object `{"score": {...}, "validation": {...}, "proposal": {...}}` (stdout) |
| `semantic_search.py` | CLI flags (`--query`, `--album-series` repeatable, `--limit`, etc.) | JSON array of song dicts (stdout) |
| `get_lyrics.py` | CLI flags (`--hash-prefix` or `--song-id`) | LRC/raw lyrics text (stdout) |
| `write_report.py` | JSON object `{"proposals": [...], "pool": [...], "config": {...}, "transitions": [...], "summary": "..."}` (stdin) | file path to `proposal_report.md` (stdout) |

## Pipeline Data Flow

```
fetch_pool.py → [raw pool array]
    ↓
enrich_pool.py → [enriched pool array]
    ↓
build_transitions.py → {"transitions": [...], "pool": [...]}
    ↓
(score_songset.py needs items + pool + transitions + config merged into one object)
    ↓
write_report.py → proposal_report.md
```

## Persisting a Songset

See SKILL.md Step 12 for using `sow-admin songset create` to persist the
top-ranked proposal. Use the `song_id` field (format: `{slug}_{8-hex}`,
e.g., `wo_de_ye_su_4c27d159`) from SongCandidate objects — not
`recording_hash_prefix`.
