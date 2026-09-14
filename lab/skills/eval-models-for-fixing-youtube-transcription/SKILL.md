---
name: eval-models-for-fixing-youtube-transcription
description: >-
  Run an LLM bake-off for the YouTube-transcript → timed-lyrics (LRC) correction
  task using the production pipeline. Interviews the user for provider creds,
  candidate models, and a judge LLM; outputs a ranked report. Invoke by name.
disable-model-invocation: true
---

# eval-models-for-fixing-youtube-transcription

## Overview

Evaluates candidate LLM models on the production task "fix YouTube transcript
into timed LRC lyrics" — the exact `_llm_correct` prompt path from
`ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py`, run
unchanged against cached YouTube transcripts.

Each candidate is evaluated under **two prompt variants**:

- `prod` — the production correction prompt, verbatim.
- `strict` — the production prompt with an appended "Additional Requirements"
  block (merge fragment runs, dedup identical timestamps, never emit partial
  phrases, never emit `[bracketed]` section tags).

Running both separates prompt-induced failures from model-capability failures.
**Cost: doubles LLM spend** — per song×model ≈ 2 correction calls (one per
variant) + up to 2 judge calls (one per ok variant item), plus `N+1` preflight
pings (1 per candidate + judge), multiplied by retries under provider 429s.

The judge scores each model's corrected LRC on three criteria:

1. Every timestamp carries a complete lyrics phrase, not a partial.
2. Timestamps are unique — a phrase is never split across multiple identical
   timestamps.
3. The last timestamp falls within 30 seconds of total song duration.

### Artifact layout

```
output/eval-models-for-fixing-youtube-transcription/
├── fixtures-<run_id>.json                 # Step 2 (DB mode)
├── transcripts/<video_id>__<language>.json # Step 3 cache
└── <run_id>-run/
    ├── meta.json
    ├── results.jsonl
    ├── raw/<variant>/<song_id>__<model-slug>.txt
    ├── parsed/<variant>/<song_id>__<model-slug>.lrc
    ├── verdicts/<variant>/<song_id>__<model-slug>.json
    ├── scores.json
    └── report.md
```

### Operations notes

- **Rate-limit budget:** export `SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS=180` for
  eval runs. Production default is 1200s; `_llm_correct` wraps every exhausted
  LLM failure as `YouTubeTranscriptError`, so a 429-heavy model would otherwise
  stall up to 20 min per item inside `call_llm_with_retry`.
- **Cold import:** the analysis-env cold import of production symbols takes
  ~20–25s per script invocation. Expected; not a hang.

## Prerequisites

- cwd = **repo root** (all scripts resolve `PROJECT_ROOT` from their own path,
  but output dirs are relative to cwd).
- `SOW_LLM_API_KEY` and `SOW_LLM_BASE_URL`. **Never paste key values into
  files** — if both env files are absent, ask the user for values and pass
  per-invocation via `VAR=... cmd` or `env: {...}`.
  - Env resolution (all scripts, per key): already-exported `os.environ`
    wins → then `lab/skills/eval-models-for-fixing-youtube-transcription/fixtures/.env`
    (per-run override, optional, never commit real keys) → then the host
    default `/opt/sow/.env`. Later files only fill keys not yet set; values
    are never echoed.
- `openai` package available in the analysis env (it is, via production deps).
- For DB fixtures: `SOW_DATABASE_URL` / `SOW_DATABASE_PASSWORD` (via
  `AdminConfig`, i.e. `/opt/sow/.env` on this host).
- Optional: `SOW_YOUTUBE_PROXY` for transcript fetches.

All commands below run as:

```
uv run --project ops/analysis-service python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/<script>.py ...
# except make_fixture.py (admin env):
uv run --project ops/admin-cli --extra admin python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/make_fixture.py ...
```

## Step 0 — Interview (ask tool)
1. **Credentials:** the scripts auto-load `SOW_LLM_API_KEY` / `SOW_LLM_BASE_URL`
   via the env resolution order below (`os.environ` → `fixtures/.env` →
   `/opt/sow/.env`). Presence-check only (`os.environ.get(...)`) — never echo
   values. If both env files are missing the keys, ask the user for values and
   pass per-invocation without writing files.
2. **Candidate models:** first check `SOW_TRANSCRIPT_LLM_MODELS` (comma-
   separated model IDs, or a path to a file with one ID per line — `#`
   comments allowed, leading `@` accepted). If unset, ask the user for a
   comma-separated list or file path (same format, via `--models`). No baked
   defaults. `--models` (if given) overrides the env var.
3. **Judge model:** propose default `$SOW_LLM_MODEL`; user may override.
   **If the judge is also a candidate**, present the choice: pick a different
   judge (default) or explicitly opt into self-judging (`--allow-self-judge`
   gets passed to Step 5; the report badges self-judged rows).
4. **Test songs:** `--song-id` list for DB generation, `--auto N`, or an
   existing fixture JSON path.
5. **Language:** default `zh`. DB mode labels every entry with this value (the
   DB has no per-song language column); manual fixtures may set per-entry
   `language` for mixed-language corpora.
6. **Prompt variants:** default both (`prod,strict`); single-variant runs
   allowed for cheap screening.

## Step 1 — Preflight

```
uv run --project ops/analysis-service python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/preflight.py \
  --models "<comma list or @file>"   # or export SOW_TRANSCRIPT_LLM_MODELS
```

Tiny chat ping (`max_tokens=8`, `temperature=0`) per candidate + judge. Any
FAIL is a **warning; exit 0** (reasoning models reject `max_tokens=1`; a ping
failure is a signal, not a verdict). Pass `--strict-ping` to make any FAIL
exit 1. `--skip-ping` to skip the calls entirely.

## Step 2 — Fixtures

```
uv run --project ops/admin-cli --extra admin python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/make_fixture.py \
  --auto 2 --language zh
# or: --song-id song_0001 --song-id song_0002
# or: use an existing fixture JSON (schema: fixtures/songs.example.json)
```

DB unreachable? Fill `fixtures/songs.example.json` manually (durations from
`sow-admin catalog show <song_id>` or the recording row).

## Step 3 — Fetch transcripts

```
uv run --project ops/analysis-service python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/fetch_transcripts.py \
  --fixtures <fixture.json>
```

Cache is language-safe: filename `<video_id>__<language>.json`; per fetched
song shows `requested → fetched (<fetched_language_code>)` so a
fallback-language fetch is visible. `--refresh` refetches.

## Step 4 — Run models

```
uv run --project ops/analysis-service python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/run_models.py \
  --fixtures <fixture.json> --models "<models>"
# (--models optional if SOW_TRANSCRIPT_LLM_MODELS is exported)
```

Writes `raw/` + `parsed/` per variant, plus `results.jsonl` and `meta.json`.
Sequential; production retry/throttle paces calls.

## Step 5 — Judge

```
uv run --project ops/analysis-service python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/judge_results.py \
  --run-dir <run_dir>
```

Self-judge guard: if the judge model is a candidate, exit 1 unless
`--allow-self-judge`. `--mechanical-only` skips the LLM judge and derives
verdicts from strengthened mechanical checks alone.

## Step 6 — Report

```
uv run --project ops/analysis-service python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/build_report.py \
  --run-dir <run_dir>
```

Writes `scores.json` + `report.md`. **Summarize the ranking tables to the user
and link `report.md`.**

## Standing notes

- **Prompt-lyrics vs judge-lyrics divergence:** the correction prompt sees
  `[bracketed]` section tags (production-faithful); judge/mechanical official
  lines exclude them. A model echoing a tag line fails mechanically.
- **Language fallback:** a transcript's actual language can differ from the
  requested one (production fallback chain); `fetched_language_code` is
  recorded and shown in Step 3 output.
- **`lyrics_source: raw` caveat:** fixtures built from `lyrics_raw` (no
  structured lyrics) split official lyrics on paragraph boundaries rather than
  sung phrases — exact-match coverage is structurally weaker for those songs;
  the report carries the per-song `lyrics_source`.
- **Duration anomalies:** if EVERY model×variant item for a song fails the
  ending window, the report flags the song `duration_suspect` (fixture
  artifact, not model failure).
- **Rerun destroys run state:** re-invoking `run_models.py` with `--run-dir`
  pointing at an existing run truncates `results.jsonl` and rewrites
  `meta.json` at phase start (scripts/run_models.py:133, :128) — a make-up
  rerun for one model×variant wipes every other row. Back up `results.jsonl`
  first. Recovery: rebuild rows from surviving `raw/` + `parsed/` artifacts
  (re-parse with production `parse_lrc_response`, verify `parsed/*.lrc` round-trips)
  and restore `meta.json` models/variants/judge_model before `build_report.py`.
- **Gateway per-attempt timeout:** Cloudflare-fronted providers (e.g.
  api.neuralwatt.com) return 524 at a hard 120s proxy-read ceiling — a single
  LLM call needing >120s fails every retry regardless of
  `SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS`. Observed: `qwen-3.8-27b` under the
  strict prompt 524s across 3 attempts and two runs while its prod call passed.
  Mark the cell unmeasurable instead of retry-looping.
- **Language fallback is normal:** requested `zh` may fetch `en-US`
  auto-generated captions; the prompt still carries the official zh lyrics, so
  models map English transcript lines onto Chinese phrases.
- **Candidates are NOT auto-loaded from `fixtures/.env`:** `load_skill_env`
  (scripts/_common.py:312) only fills `SOW_LLM_API_KEY` / `SOW_LLM_BASE_URL` /
  `SOW_LLM_MODEL` / `SOW_YOUTUBE_PROXY`. `SOW_TRANSCRIPT_LLM_MODELS` stored in
  `fixtures/.env` is silently ignored — read it from the file and pass
  `--models` explicitly. Also never `sed|export` the quoted `.env` value
  yourself: surrounding `"`/`'` leak into the model IDs and the provider 404s
  (`model_not_found`); scripts strip quotes only when they load `fixtures/.env`
  internally. Strip them (`tr -d '"'`) if exporting manually.
- **Recovery ground truth:** after a truncation, the pre-truncate
  `results.jsonl` backup (if it exists) is the authoritative row source — copy
  it back verbatim; artifact re-parsing is only the fallback when no backup
  survived.
