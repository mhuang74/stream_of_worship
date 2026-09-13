# Plan: `eval-models-for-fixing-youtube-transcription` skill

## Context

User asked for an agent skill under `lab/skill/` that evaluates candidate LLM models on the production task "fix YouTube transcript into timed LRC lyrics", reusing production code from `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py`. At skill run time it must interview the user for provider credentials, candidate model IDs (list or file), and a judge LLM. The judge scores each model's corrected LRC on three criteria: (1) every timestamp carries a complete lyrics phrase, not a partial; (2) timestamps are unique — a phrase never split across multiple identical timestamps; (3) the last timestamp falls within 30 seconds of total song duration. Deliverable: skill directory + ranked comparison report per run.

Directory: `lab/skills/eval-models-for-fixing-youtube-transcription/` — repo convention dir is `lab/skills/` (existing `songset-constructor` skill lives there); user's literal `lab/skill/` is treated as a typo (see Assumptions).

## Locked decisions (from interview)

- Env vars: `SOW_LLM_API_KEY` + `SOW_LLM_BASE_URL` (production names; key value stays in env, never written to files).
- Candidate models: no baked defaults; user supplies at run time as a comma-separated list OR a path to a file with one model ID per line (`#` comments allowed).
- Judge model: defaults to the value of `SOW_LLM_MODEL`; user may override in the Step 0 interview.
- Fixtures: DB generator script + manual `fixtures/songs.example.json` schema (both shipped).

## Approach

All scripts run with cwd = repo root. Two invocation runtimes (both verified working on this host):

- **Analysis env** (imports production code; `sow_analysis` installed editable in existing `ops/analysis-service/.venv`):
  `uv run --project ops/analysis-service python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/<script>.py ...`
- **Admin env** (only `make_fixture.py`; needs psycopg + admin package):
  `uv run --project ops/admin-cli --extra admin python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/make_fixture.py ...`

Scripts import production code with the `fetch_pool.py` bootstrap pattern: `PROJECT_ROOT = Path(__file__).resolve().parents[4]`, then `sys.path.insert(0, str(PROJECT_ROOT / "ops" / "analysis-service" / "src"))` — verified `from sow_analysis.workers.youtube_transcript import ...` imports cleanly in both venvs (only the admin venv lacks `aiosqlite`, which the analysis venv has).

### 1. Scaffold `lab/skills/eval-models-for-fixing-youtube-transcription/`

Files:
```
SKILL.md
fixtures/songs.example.json
scripts/_common.py
scripts/preflight.py
scripts/make_fixture.py
scripts/fetch_transcripts.py
scripts/run_models.py
scripts/judge_results.py
scripts/build_report.py
```
No README, no pytest suite (matches `songset-constructor` convention: scripts verified by smoke runs).

### 2. `SKILL.md`

Frontmatter:
```yaml
---
name: eval-models-for-fixing-youtube-transcription
description: >-
  Run an LLM bake-off for the YouTube-transcript → timed-lyrics (LRC) correction
  task using the production pipeline. Interviews the user for provider creds,
  candidate models, and a judge LLM; outputs a ranked report. Invoke by name.
disable-model-invocation: true
---
```

Body sections (steps, each with exact commands):

- **Overview**: what is evaluated (production `_llm_correct` prompt path on cached YouTube transcripts), artifact layout, cost note (1 LLM call per song×model + 1 judge call per song×model).
- **Prerequisites**: repo root cwd; `SOW_LLM_API_KEY` and `SOW_LLM_BASE_URL` exported (never paste key values into files); analysis-service venv present (`uv sync` in `ops/analysis-service` if missing); for DB fixtures `SOW_DATABASE_URL`/`SOW_DATABASE_PASSWORD` (via `AdminConfig`); optional `SOW_YOUTUBE_PROXY`.
- **Step 0 — Interview (ask tool)**: (a) confirm creds env vars are exported — check `os.environ.get(...)` presence only, never echo values; if absent, ask user for values and pass them per-invocation via `env: {...}`/`VAR=... cmd` without writing files; (b) candidate model IDs — comma-separated list or file path; (c) judge model ID — propose default `$SOW_LLM_MODEL`, warn if judge is also a candidate; (d) test songs — `--song-id` list for DB generation, `--auto N`, or an existing fixture JSON path; (e) language default `zh`.
- **Step 1 — Preflight** → `preflight.py` (command above).
- **Step 2 — Fixtures** → `make_fixture.py` or use existing fixture JSON.
- **Step 3 — Fetch transcripts** → `fetch_transcripts.py`.
- **Step 4 — Run models** → `run_models.py`.
- **Step 5 — Judge** → `judge_results.py`.
- **Step 6 — Report** → `build_report.py`; then summarize ranking table to the user and link `report.md`.

### 3. `scripts/_common.py` (shared helpers, no I/O side effects at import)

- `PROJECT_ROOT = Path(__file__).resolve().parents[4]`; `ANALYSIS_SRC = PROJECT_ROOT / "ops" / "analysis-service" / "src"`.
- `bootstrap_analysis_src()` — idempotent `sys.path.insert` of `ANALYSIS_SRC`.
- `load_fixture(path) -> list[dict]` — validates each entry has non-empty `song_id`, `youtube_url`, `duration_seconds` (> 0 float), `language` (`zh`|`en`), `lyrics` (non-empty list of strings, official lyric lines). Missing/invalid → `FixtureError` listing entry index and field. Required keys exactly: `song_id`, `title`, `language`, `youtube_url`, `duration_seconds`, `lyrics`.
- `load_model_ids(spec: str) -> list[str]` — `spec` is either comma-separated IDs or a path to a file with one ID per line (`#` comments, blank lines skipped). Deduplicates, preserves order; empty result → `EvalError`.
- `resolve_lyrics_lines(fixture_entry) -> list[str]` — returns `entry["lyrics"]` (already normalized lines; fixtures never re-parse).
- Transcript cache I/O: `transcript_cache_path(transcripts_dir, video_id)`, `load_transcript(path) -> list[snippets]`, `save_transcript(path, video_id, language, snippets)`; snippet dict keys exactly `start` (float s), `duration` (float s), `text` (str).
- `snippets_to_namespace(snippets) -> list` — wraps dicts in `types.SimpleNamespace(start=..., text=..., duration=...)` so production `_format_transcript_text` consumes them unchanged.
- `lrc_text(lines: List[LRCLine]) -> str` — joins `str(LRCLine)` (production `LRCLine.__str__` emits `[mm:ss.xx] text`).
- `write_jsonl(path, rows)`, `read_jsonl(path)`.
- `parse_judge_json(text: str) -> dict` — `json.loads` on the whole text; on failure extract the first fenced ```json block via regex; on failure raise `JudgeParseError`.
- `require_env(names: list[str])` — exits 1 printing the MISSING var NAMES only (never values), with export instructions.
- `now_run_id() -> str` — `YYYYMMDD-HHMMSS` UTC.

### 4. `scripts/preflight.py` (analysis env)

Args: `--models "<comma list or @file>"`, `--skip-ping`.
- `require_env(["SOW_LLM_API_KEY", "SOW_LLM_BASE_URL"])`; `load_model_ids`.
- Unless `--skip-ping`: for each candidate model AND the judge model (default `$SOW_LLM_MODEL`), one 1-token chat call (`max_tokens=1`, `temperature=0`) via a plain `openai.OpenAI(api_key=env, base_url=env, max_retries=1)` client; print rich table `model | OK | latency` or `FAIL | error`. Any FAIL → exit 1 after printing all rows.

### 5. `scripts/make_fixture.py` (admin env — reuses production lyrics resolution)

Bootstrap: `sys.path.insert(0, str(PROJECT_ROOT / "ops" / "admin-cli" / "src"))` (same as `fetch_pool.py:21-24`).
Args: `--song-id` (repeatable) | `--auto N`, `--album` (optional filter), `--language` (default `zh`), `--out` (default `output/eval-models-for-fixing-youtube-transcription/fixtures-<run_id>.json`).
- Connect like `fetch_pool.py`: `config = AdminConfig.load()`, `provider = ConnectionProvider(config.get_connection_url())`, `db = DatabaseClient(provider)`.
- `--song-id` mode: `db.get_song(song_id)` + `db.get_recording_by_song_id(song_id)`; skip+warn if song/recording missing.
- `--auto N` mode: `db.list_recordings_with_songs(visibility="published", limit=200)` then keep entries with non-empty `recording.youtube_url`, take first N (deterministic `imported` order).
- Official lyrics: `resolve_lyrics_text(song, recording)` from `stream_of_worship.admin.services.lrc_jobs` (exact production helper), split to non-empty lines; skip+warn when empty.
- Skip+warn when `recording.youtube_url` missing or `recording.duration_seconds` None.
- Emit fixture entries `{song_id, title, language, youtube_url, duration_seconds, lyrics}`; print summary (kept/skipped counts); write JSON.

### 6. `scripts/fetch_transcripts.py` (analysis env)

Args: `--fixtures <path>`, `--out-dir` (default `output/eval-models-for-fixing-youtube-transcription/transcripts`), `--refresh`.
- For each fixture entry: `video_id = extract_video_id(youtube_url)` (None → per-song error, continue); cache hit and not `--refresh` → skip; else `transcript = await fetch_youtube_transcript(video_id, language=entry["language"])` (production rate limiter/proxy honored); save cache `{video_id, language, snippets: [{start, duration, text}]}` (snippets expose `.start/.duration/.text`).
- Print summary: fetched / cached / failed (per-song errors, never fatal).

### 7. `scripts/run_models.py` (analysis env)

Args: `--fixtures <path>`, `--transcripts-dir`, `--models "<comma list or @file>"`, `--run-dir` (default `output/eval-models-for-fixing-youtube-transcription/<run_id>-run`).
- Creates `run_dir/{raw,parsed,verdicts}`, writes `meta.json` {models, judge placeholder, base_url host only, fixture path, transcript dir, UTC timestamp, git HEAD}.
- Sequential over (song, model) — production `call_llm_with_retry` min-interval throttle (2s) paces calls; no extra concurrency.
- Per item: cache load → `snippets_to_namespace` → `transcript_text = _format_transcript_text(...)` → `prompt = build_correction_prompt(transcript_text, lyrics_lines, language=entry["language"])` → `response = await _llm_correct(prompt, model)` (production retry/backoff path; `llm_model` arg overrides `SOW_LLM_MODEL`, so `SOW_LLM_MODEL` itself is never consulted).
- Save `raw/<song_id>__<model-slug>.txt`; `lrc_lines = parse_lrc_response(response)`; save `parsed/<song_id>__<model-slug>.lrc` via `lrc_text(lines)`.
- `results.jsonl` row: `{song_id, model, language, status: "ok"|"error", n_lines, raw_path, parsed_path, error}`; on `YouTubeTranscriptError`/`LLMConfigError`/`ValueError` (no valid LRC) → `status: "error"` with `error` string, continue. Missing transcript cache → error row telling user to run Step 3.
- `<model-slug>` = model id with `/` and `:` replaced by `-`.

### 8. `scripts/judge_results.py` (analysis env)

Args: `--run-dir`, `--judge-model` (default `os.environ["SOW_LLM_MODEL"]`; error if unset), `--mechanical-only`.
- For each `status: "ok"` row in `results.jsonl`: load parsed LRC lines (re-parse `parsed/*.lrc` with `parse_lrc_response`), official lyrics from fixture, `duration_seconds`.
- **Mechanical checks** (computed in-script, always):
  - duplicate timestamps: exact `[mm:ss.xx]` string collisions (list colliding pairs);
  - order: timestamps non-decreasing;
  - ending gap: `gap = duration_seconds - last_line.time_seconds` (report float; negative = timestamp beyond song end);
  - exact-match coverage: fraction of lines whose text equals an official lyric line (strip whitespace only);
  - partial-phrase heuristic: lines whose text is a proper substring of an official line.
- **Judge LLM** (skipped by `--mechanical-only`): one call per item via `openai.OpenAI(api_key/base_url from env, max_retries=0)` wrapped in `call_llm_with_retry(...)` (production retry util), `temperature=0`. Prompt contains: numbered official lyrics, numbered candidate LRC lines, `duration_seconds`, and the three criteria phrased exactly as:
  1. Each timestamp must carry a complete lyrics phrase — its text must be exactly one full line from the official lyrics (repeated phrases allowed); a partial/fragment phrase is a failure.
  2. Each timestamp must be unique — no two lines may share the same timestamp, and one lyric phrase must never be split across multiple lines with identical timestamps whose texts together form one official phrase.
  3. The last timestamp must be within 30 seconds of the total song duration: `0 <= duration - last_timestamp <= 30`.
  Required strict-JSON output schema (exact):
  ```json
  {
    "criteria": {
      "complete_phrases": {"pass": true, "issues": [{"index": 3, "timestamp": "01:23.45", "text": "...", "reason": "..."}]},
      "unique_timestamps": {"pass": true, "issues": [{"detail": "..."}]},
      "ending_window": {"pass": true, "gap_seconds": 12.4, "detail": "..."}
    },
    "overall_pass": true,
    "notes": "one short line"
  }
  ```
  Parse with `parse_judge_json`; on `JudgeParseError` retry once appending "Output ONLY the JSON object." to the user message; second failure → record `judge_error` (item keeps mechanical verdict, `overall_pass = false`, flagged in report).
- Write `verdicts/<song_id>__<model-slug>.json` = `{song_id, model, judge_model, mechanical: {...}, judge: {...}, criteria: {complete_phrases, unique_timestamps, ending_window, overall_pass}}` — `pass` values are the judge's when present, else derived from mechanical checks (duplicates→unique fail; partial-substring→complete fail; `gap` outside `[0,30]`→ending fail).

### 9. `scripts/build_report.py` (analysis env, no LLM calls)

Args: `--run-dir`.
- Reads `meta.json`, `results.jsonl`, `verdicts/*.json`; writes `scores.json` and `report.md`.
- Per-model ranking row: `songs`, `pass_rate` (overall_pass/ok-items), `complete_phrases_fails`, `unique_timestamps_fails`, `ending_window_fails`, `partial_phrase_lines` (sum), `duplicate_timestamps` (sum), `avg_exact_match_coverage`, `errors`. Rank: pass_rate desc → partial_phrase_lines asc → coverage desc.
- `report.md`: ranking table, per song×model verdict summary with judge `notes`, per-criterion issue lists, relative links to `raw/`+`parsed/` artifacts; print the top-3 ranking to stdout.

### 10. `fixtures/songs.example.json` (exact schema, one realistic example entry + `lyrics` as list of lines)

```json
[
  {
    "song_id": "example_song_0000000000",
    "title": "範例歌曲",
    "language": "zh",
    "youtube_url": "https://www.youtube.com/watch?v=VIDEO_ID",
    "duration_seconds": 245.0,
    "lyrics": ["第一句歌詞", "第二句歌詞"]
  }
]
```

## Critical files & anchors

- `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py` — reuse unchanged: `extract_video_id` (L462), `_format_transcript_text` (L488), `build_correction_prompt` (L507, zh/en prompts), `parse_lrc_response` (L580), `_llm_correct` (L773, env-driven `settings` + retry), `fetch_youtube_transcript` (L677, rate limiter + proxy). No edits to this file.
- `ops/analysis-service/src/sow_analysis/workers/llm_rate_limit.py` — `call_llm_with_retry` (L474) reused for the judge call.
- `ops/analysis-service/src/sow_analysis/workers/lrc.py` — `LRCLine` (L142, `__str__` emits LRC text) for parsing/serialization.
- `ops/admin-cli/src/stream_of_worship/admin/services/lrc_jobs.py` — `resolve_lyrics_text(song, recording)` (L39) reused by `make_fixture.py` for official lyrics (same source as production LRC jobs).
- `lab/skills/songset-constructor/scripts/fetch_pool.py` — L21-24 `PROJECT_ROOT`/`sys.path` bootstrap and `AdminConfig.load()`→`ConnectionProvider` wiring (L65-68) copied as pattern.

## Verification

Prereqs: cwd repo root; `SOW_LLM_API_KEY`/`SOW_LLM_BASE_URL` exported (present in this session); analysis venv exists.

1. Import proof: `cd ops/analysis-service && ./.venv/bin/python -c "from sow_analysis.workers.youtube_transcript import build_correction_prompt, parse_lrc_response, _llm_correct, fetch_youtube_transcript, _format_transcript_text, extract_video_id; print('ok')"` → prints `ok`.
2. Preflight: `uv run --project ops/analysis-service python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/preflight.py --models qwen3.6-35b-fast` → table row `qwen3.6-35b-fast OK` (1-token ping).
3. Offline determinism check (no LLM): build a throwaway run dir under `/tmp` with a synthetic fixture and a planted `parsed/` LRC containing a partial phrase, a duplicated timestamp, and a last timestamp 60s before duration → `judge_results.py --mechanical-only` marks all three criteria `pass: false` in the verdict JSON; a clean synthetic LRC (exactly official lines, unique ascending timestamps, last timestamp 10s before duration) passes all three.
4. End-to-end mini run (~4 LLM calls): `make_fixture.py --auto 1` → `fetch_transcripts.py` → `run_models.py --models qwen3.6-35b-fast` → `judge_results.py` → `build_report.py`. Assert run dir contains `raw/`, `parsed/`, `verdicts/`, `results.jsonl`, `meta.json`, `scores.json`, `report.md`; `report.md` contains the ranking table with 1 model row.
5. Read `report.md` and the verdict JSON to confirm judge output matches the schema and the three criteria are reported per song×model.

## Assumptions & contingencies

- Skill dir `lab/skills/` (repo convention) though user wrote `lab/skill/` — if a literal `lab/skill/` is wanted, `mv lab/skills/eval-models-for-fixing-youtube-transcription lab/skill/` and fix the two command blocks in SKILL.md.
- Judge defaults to `$SOW_LLM_MODEL` (user pick). If that model is also a candidate, Step 0 prints a circularity warning; user may still proceed.
- DB unreachable in `make_fixture.py` → instruct user to fill `fixtures/songs.example.json` manually (required `duration_seconds`; source durations from `sow-admin catalog show <song_id>` or the recording row).
- YouTube fetch 429/permanent failure → song dropped with per-song error in the run; suggest exporting `SOW_YOUTUBE_PROXY` and re-running Step 3.
- Provider must be OpenAI chat-completions compatible (production contract); judge failures after one retry count the item as fail and flag `judge_error` in the report (conservative ranking).
- If `ops/analysis-service/.venv` is absent on another host, run `uv sync --project ops/analysis-service` first (heavy install, documented in SKILL.md prerequisites).
- `graphify update .` after the session's code changes (AGENTS.md rule; mechanical post-work step, not planned here).
