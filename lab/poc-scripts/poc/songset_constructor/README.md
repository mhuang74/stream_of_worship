# Songset Constructor POC

Build ranked Chinese worship songset proposal artifacts from read-only catalog data.

## Run

```bash
uv run --project lab/poc-scripts --extra songset_constructor \
  python lab/poc-scripts/construct_songset_agent.py --no-llm
```

Agentic mode additionally requires `SOW_LLM_API_KEY` and `SOW_LLM_MODEL`; `SOW_LLM_BASE_URL` is supported for an OpenAI-compatible gateway.
The CLI loads `/opt/sow/.env` automatically when present, or a custom file with `--env-file`.
Already-exported shell variables take precedence over values in the env file.

If loading env manually in the shell, export the sourced values before running `uv`:

```bash
set -a
source /opt/sow/.env
set +a
uv run --project lab/poc-scripts --extra songset_constructor \
  python lab/poc-scripts/construct_songset_agent.py --pool-limit 20 --interactive-review
```

## Output

Each successful run writes `proposals.json`, `proposal_report.md`, `candidate_pool.csv`, `graph_trace.jsonl`, and `songset_review.md` under the selected `--output-dir`.

## Theme Anchors

Regenerate anchors only with a working embedding gateway:

```bash
uv run --project lab/poc-scripts --extra songset_constructor \
  python -m poc.songset_constructor.regen_theme_anchors
```

The fixture must contain real 1536-dimensional `text-embedding-3-small` vectors.

## Read-Only Guarantee

The POC uses `ReadOnlyClient` and only issues bounded `SELECT` queries. It does not import `SongsetClient`, does not write `songsets` or `songset_items`, and does not run schema migrations.
