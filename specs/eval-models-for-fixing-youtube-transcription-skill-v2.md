# Plan v2: `eval-models-for-fixing-youtube-transcription` skill

> Supersedes `eval-models-for-fixing-youtube-transcription-skill.md` (v1) after a design
> review on 2026-09-14. v1's anchors were verified against production code; two of its
> assumptions were found false (`LRCLine.__str__`, cross-venv imports) and are corrected
> here. This file is self-contained — implement from this document only.
>
> Review lineage: verified anchor audit + user interview. Locked review decisions are in
> "Locked decisions"; verified facts are in "Verified facts" (bottom).

## Context

Agent skill under `lab/skills/` that evaluates candidate LLM models on the production
task "fix YouTube transcript into timed LRC lyrics", reusing production code unchanged
from `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py`. At skill run
time it interviews the user for provider credentials, candidate model IDs, and a judge
LLM. The judge scores each model's corrected LRC on three criteria:

1. Every timestamp carries a complete lyrics phrase, not a partial.
2. Timestamps are unique — a phrase is never split across multiple identical timestamps.
3. The last timestamp falls within 30 seconds of total song duration.

Deliverable: skill directory + ranked comparison report per run.

Directory: `lab/skills/eval-models-for-fixing-youtube-transcription/` (repo convention;
user's literal `lab/skill/` treated as typo — see Assumptions).

## Locked decisions

Original (v1, unchanged):

- Env vars: `SOW_LLM_API_KEY` + `SOW_LLM_BASE_URL` (production names, verified
  `ops/analysis-service/src/sow_analysis/config.py:114-116`); key value stays in env,
  never written to files.
- Candidate models: no baked defaults; user supplies at run time as a comma-separated
  list OR a path to a file with one model ID per line (`#` comments allowed; a leading
  `@` on the path is accepted and stripped).
- Judge model: defaults to the value of `SOW_LLM_MODEL`; user may override in the
  Step 0 interview.
- Fixtures: DB generator script + manual `fixtures/songs.example.json` schema (both shipped).

From review interview (2026-09-14):

- **Prompt variants — both run per model.** Each candidate is evaluated against
  `prod` (production prompt verbatim) and `strict` (production prompt + appended
  merge/dedup rules). Rationale: the zh production prompt says "Preserve the number of
  lines and their timecodes exactly" (`youtube_transcript.py:560`), which conflicts with
  criterion 1 on fragment-granular auto-generated transcripts; running both separates
  prompt-induced failures from model-capability failures. Doubles LLM cost.
- **Duration source: recording DB duration + anomaly flag.** Criterion 3 keeps
  `recording.duration_seconds` as the reference; `build_report.py` flags a song
  `duration_suspect` when every model×variant item for that song fails the ending
  window (fixture artifact, not model failure).
- **Self-judging: blocked unless explicit flag.** If the judge model is also a
  candidate, Step 0 asks the user to pick a different judge or explicitly opt in via
  `--allow-self-judge`; `judge_results.py` enforces the guard independently.
- **Mechanical fallback: strengthened to match judge criterion 1.** A candidate line
  that matches NO official line (neither exact nor proper substring) fails
  `complete_phrases` mechanically. Every criteria pass is stamped
  `criteria_source: "judge"|"mechanical"`.
- **Preflight: warn + configurable.** Ping uses `max_tokens=8` (reasoning models reject
  `max_tokens=1`), client timeout 15s; any FAIL is a warning, exit 0 — unless
  `--strict-ping` is passed, which makes any FAIL exit 1.
- **Prompt-lyrics vs judge-lyrics split (documented divergence).** The correction
  prompt receives the full `resolve_lyrics_text` output INCLUDING `[Label]` section-tag
  lines (production-faithful: `youtube_transcript.py:893` splits without stripping; tags
  verified present via `flatten_structured_lyrics`, `structured_lyrics.py:277`). The
  judge and mechanical checks receive the same list minus `^\[.+\]$` lines, so criterion
  1's "full official line" means a sung phrase, and a model echoing a tag line fails.
- **Transcript cache is language-safe.** Cache filename includes the requested language
  (`<video_id>__<language>.json`) and the cache JSON records `fetched_language_code`,
  because `_find_best_transcript`'s fallback (`youtube_transcript.py:672-674`) can return
  a different actual language than requested (zh request → en fallback).

## Approach

All scripts run with cwd = repo root. Two invocation runtimes (both verified on this host):

- **Analysis env** (imports production code; `sow_analysis` importable in
  `ops/analysis-service/.venv`):
  `uv run --project ops/analysis-service python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/<script>.py ...`
- **Admin env** (only `make_fixture.py`; needs psycopg + admin package):
  `uv run --project ops/admin-cli --extra admin python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/make_fixture.py ...`

Scripts import production code with the `fetch_pool.py` bootstrap pattern:
`PROJECT_ROOT = Path(__file__).resolve().parents[4]`, then
`sys.path.insert(0, str(PROJECT_ROOT / "ops" / "analysis-service" / "src"))`.

**Hard constraint (verified): the admin venv CANNOT import `sow_analysis`.**
`sow_analysis.workers.__init__` (L19) imports `.queue` → `storage/db.py` (L8)
`import aiosqlite` → `ModuleNotFoundError` in the admin venv. Therefore:

- `scripts/_common.py` performs **zero top-level `sow_analysis` imports** — production
  imports happen lazily inside the analysis-env scripts that need them.
- `lrc_text()` is duck-typed (`line.format()`), no `LRCLine` import needed.
- `make_fixture.py` (admin env) imports only `_common` + the admin package.

**Serialization constraint (verified): `LRCLine` has `format()`, NOT `__str__`.**
`str(LRCLine(61.5, 'x'))` → `"LRCLine(time_seconds=61.5, text='x')"` (dataclass repr).
All LRC serialization uses `line.format()` → `[mm:ss.xx] text`. Round-trip
`parse_lrc_response(line.format())` verified stable.

Ops notes for SKILL.md:

- Analysis-env cold import is ~20–25s per script invocation (observed) — expected.
- Eval runs SHOULD export `SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS=180`. Production default
  is 1200s (`config.py:139`); `_llm_correct` wraps every exhausted LLM failure as
  `YouTubeTranscriptError` (`youtube_transcript.py:858`), so a 429-heavy model would
  otherwise stall up to 20 min per item inside `call_llm_with_retry`.

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
No README, no pytest suite (matches `songset-constructor` convention: scripts verified
by smoke runs).

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

Body sections:

- **Overview**: what is evaluated (production `_llm_correct` prompt path on cached
  YouTube transcripts, under two prompt variants `prod` and `strict`); artifact layout
  with variant subdirs; cost note: per song×model ≈ 2 correction calls (one per
  variant) + up to 2 judge calls (one per ok variant item), plus `N+1` preflight
  pings (1 per candidate + judge), multiplied by retries under provider 429s;
  operations note: export `SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS=180` for eval runs;
  cold-import note (~20–25s per script).
- **Prerequisites**: repo root cwd; `SOW_LLM_API_KEY` and `SOW_LLM_BASE_URL` exported
  (never paste key values into files); analysis-service venv present (`uv sync` in
  `ops/analysis-service` if missing); for DB fixtures `SOW_DATABASE_URL` /
  `SOW_DATABASE_PASSWORD` (via `AdminConfig`); optional `SOW_YOUTUBE_PROXY`.
- **Step 0 — Interview (ask tool)**:
  (a) confirm creds env vars are exported — check `os.environ.get(...)` presence only,
  never echo values; if absent, ask user for values and pass them per-invocation via
  `env: {...}` / `VAR=... cmd` without writing files;
  (b) candidate model IDs — comma-separated list or file path;
  (c) judge model ID — propose default `$SOW_LLM_MODEL`; **if the judge is also a
  candidate, present the choice: pick a different judge (default) or explicitly opt
  into self-judging** (`--allow-self-judge` gets passed to Step 5; the report badges
  self-judged rows);
  (d) test songs — `--song-id` list for DB generation, `--auto N`, or an existing
  fixture JSON path;
  (e) language default `zh` (DB mode labels every entry with this value — the DB has
  no per-song language column; manual fixtures may set per-entry `language` for
  mixed-language corpora);
  (f) prompt variants — default both (`prod,strict`); single-variant runs allowed for
  cheap screening.
- **Step 1 — Preflight** → `preflight.py`.
- **Step 2 — Fixtures** → `make_fixture.py` or use existing fixture JSON.
- **Step 3 — Fetch transcripts** → `fetch_transcripts.py`.
- **Step 4 — Run models** → `run_models.py`.
- **Step 5 — Judge** → `judge_results.py`.
- **Step 6 — Report** → `build_report.py`; then summarize ranking tables to the user
  and link `report.md`.

Standing notes in SKILL.md (stated, not hidden):

- Prompt-lyrics vs judge-lyrics divergence: the correction prompt sees
  `[bracketed]` section tags (production-faithful); judge/mechanical official lines
  exclude them.
- A transcript's actual language can differ from the requested one (production
  fallback chain); `fetched_language_code` is recorded and shown in Step 3 output.
- Fixtures built from `lyrics_raw` (no structured lyrics) split on paragraph
  boundaries rather than sung phrases — exact-match coverage is structurally weaker
  for those songs; the report carries the per-song `lyrics_source`.

### 3. `scripts/_common.py` (shared helpers, no I/O side effects at import, no top-level `sow_analysis` imports)

- `PROJECT_ROOT = Path(__file__).resolve().parents[4]`; `ANALYSIS_SRC = PROJECT_ROOT /
  "ops" / "analysis-service" / "src"`; `ADMIN_SRC = PROJECT_ROOT / "ops" / "admin-cli" / "src"`.
- `bootstrap_analysis_src()` / `bootstrap_admin_src()` — idempotent `sys.path.insert`.
  Callers import production modules AFTER calling these (lazy-import discipline).
- `load_fixture(path) -> list[dict]` — each entry must have non-empty `song_id`,
  `youtube_url`, `duration_seconds` (> 0), `language` (`zh`|`en`), `lyrics`
  (non-empty list of strings); `title` required; `lyrics_source` optional
  (`structured`|`raw`|absent). Unknown keys → warning (not error). Missing/invalid →
  `FixtureError` listing entry index and field.
- `load_model_ids(spec: str) -> list[str]` — `spec` is comma-separated IDs or a file
  path (optional leading `@` stripped) with one ID per line (`#` comments, blank lines
  skipped). Deduplicates, preserves order; empty → `EvalError`. Then **slug-collision
  check**: `slugify(id)` = id with `/` and `:` replaced by `-`; two distinct IDs
  mapping to the same slug → `EvalError` (prevents silent artifact overwrites).
- `slugify(model_id: str) -> str`.
- `resolve_lyrics_lines(fixture_entry) -> list[str]` — returns `entry["lyrics"]`
  (fixtures never re-parse).
- `judge_official_lines(fixture_entry) -> list[str]` — `entry["lyrics"]` minus lines
  matching `^\[.+\]$` (stripped). Used ONLY by judge/mechanical checks; the correction
  prompt keeps the full list.
- Transcript cache I/O: `transcript_cache_path(transcripts_dir, video_id, language)`
  → `<transcripts_dir>/<video_id>__<language>.json`;
  `load_transcript(path) -> dict` (full cache record);
  `save_transcript(path, video_id, language, fetched_language_code, snippets)`.
  Cache JSON: `{video_id, language, fetched_language_code, snippets: [{start, duration,
  text}]}` (`language` = requested, `fetched_language_code` = actual; snippet keys
  exactly `start` float s, `duration` float s, `text` str).
- `snippets_to_namespace(snippets) -> list` — wraps dicts in
  `types.SimpleNamespace(start=..., text=..., duration=...)` so production
  `_format_transcript_text` consumes them unchanged (it reads `.start`/`.text` only;
  `.duration` cached for completeness).
- `lrc_text(lines) -> str` — `"\n".join(line.format() for line in lines)`
  (production `LRCLine.format()` emits `[mm:ss.xx] text`; do NOT use `str(line)`).
- `write_jsonl(path, rows)` (caller controls append vs truncate), `read_jsonl(path)`.
- `parse_judge_json(text: str) -> dict` — `json.loads` whole text; on failure extract
  the first fenced ```json block via regex; on failure raise `JudgeParseError`.
- `require_env(names: list[str])` — exits 1 printing the MISSING var NAMES only
  (never values), with export instructions.
- `now_run_id() -> str` — `YYYYMMDD-HHMMSS` UTC.

### 4. `scripts/preflight.py` (analysis env)

Args: `--models "<comma list or file>"`, `--skip-ping`, `--strict-ping`.
- `require_env(["SOW_LLM_API_KEY", "SOW_LLM_BASE_URL"])`; `load_model_ids` (runs the
  slug-collision check before any LLM spend).
- Judge model: from `$SOW_LLM_MODEL`; if unset, print warning "judge model unresolved
  — required at Step 5" and skip the judge ping row.
- Unless `--skip-ping`: for each candidate AND the judge, one tiny chat call
  (`max_tokens=8`, `temperature=0`) via `openai.OpenAI(api_key=env, base_url=env,
  max_retries=1, timeout=15)`; 0.5s sleep between pings (avoid provider burst 429).
  Print rich table `model | OK | latency | error`.
- **Any FAIL → warning row; exit 0** — unless `--strict-ping`, in which case any FAIL
  → exit 1 after printing all rows. Rationale: `max_tokens=1` false-FAILs
  reasoning/thinking models; a ping failure is a signal, not a verdict.

### 5. `scripts/make_fixture.py` (admin env — reuses production lyrics resolution)

Bootstrap: `bootstrap_admin_src()` only (admin package + `_common`; NO `sow_analysis`).
Args: `--song-id` (repeatable) | `--auto N`, `--album` (optional filter), `--language`
(default `zh`), `--out` (default
`output/eval-models-for-fixing-youtube-transcription/fixtures-<run_id>.json`).
- Connect like `fetch_pool.py`: `config = AdminConfig.load()`,
  `provider = ConnectionProvider(config.get_connection_url())`,
  `db = DatabaseClient(provider)`.
- `--song-id` mode: `db.get_song(song_id)` + `db.get_recording_by_song_id(song_id)`;
  skip+warn if song/recording missing.
- `--auto N` mode: `db.list_recordings_with_songs(visibility="published",
  album=args.album or None, sort_by="imported", limit=200)` (200 = scan window; warn
  if fewer than N entries qualify); keep entries with non-empty
  `recording.youtube_url`, take first N.
- Official lyrics: `resolve_lyrics_text(song, recording)` from
  `stream_of_worship.admin.services.lrc_jobs` (exact production helper), split to
  non-empty lines. **Tag lines (`^\[.+\]$`) are KEPT** — the correction prompt must
  match production input. Record `lyrics_source`: `"structured"` if
  `recording.structured_lyrics` parsed and flattened, else `"raw"`.
- Skip+warn when `recording.youtube_url` missing or `recording.duration_seconds` None.
- Emit fixture entries `{song_id, title, language, youtube_url, duration_seconds,
  lyrics, lyrics_source}`; print summary (kept/skipped counts, per-source counts); write JSON.

### 6. `scripts/fetch_transcripts.py` (analysis env)

Args: `--fixtures <path>`, `--out-dir` (default
`output/eval-models-for-fixing-youtube-transcription/transcripts`), `--refresh`.
- For each fixture entry: `video_id = extract_video_id(youtube_url)` (None → per-song
  error, continue); cache file `<video_id>__<language>.json` — filename keyed by
  requested language, so a later run with a different `--language` can never hit a
  wrong-language cache. Cache hit and not `--refresh` → skip;
  else `transcript = await fetch_youtube_transcript(video_id, language=entry["language"])`
  (production rate limiter/proxy honored); save
  `{video_id, language, fetched_language_code: transcript.language_code, snippets:
  [{start, duration, text}]}`.
- Summary: fetched / cached / failed (per-song errors, never fatal); per fetched song
  show `requested → fetched (<fetched_language_code>)` so a fallback-language fetch is
  visible, not latent.

### 7. `scripts/run_models.py` (analysis env)

Args: `--fixtures <path>`, `--transcripts-dir`, `--models "<comma list or file>"`,
`--variants prod,strict` (validated against {`prod`,`strict`}), `--run-dir` (default
`output/eval-models-for-fixing-youtube-transcription/<run_id>-run`).
- Creates `run_dir/{raw,parsed}/<variant>/` for each selected variant; writes
  `meta.json` `{models, variants, judge_model: null, base_url_host (host only),
  fixture_path, transcripts_dir, utc_timestamp, git_head}`.
- Sequential over (song, model, variant) — production `call_llm_with_retry`
  min-interval throttle (2s) paces calls; no extra concurrency.
- Per item: cache load by (video_id, language) → `snippets_to_namespace` →
  `transcript_text = _format_transcript_text(...)` →
  `prompt = build_correction_prompt(transcript_text, lyrics_lines, language=entry["language"])`
  (full lyrics incl. tags) → if variant == `strict`, insert the additional-rules block
  (below) immediately before the prompt's `## Output Format` heading (each language
  variant contains that heading exactly once — verified):
  `prompt.replace("## Output Format", STRICT_BLOCK + "\n\n## Output Format", 1)` →
  `response = await _llm_correct(prompt, model)` (production retry/backoff;
  `llm_model` arg overrides `SOW_LLM_MODEL`, so the env default is never consulted —
  verified `youtube_transcript.py:814`). The production file stays untouched.
- Save `raw/<variant>/<song_id>__<model-slug>.txt`;
  `lrc_lines = parse_lrc_response(response)`; save
  `parsed/<variant>/<song_id>__<model-slug>.lrc` via `lrc_text(lines)`.
- `results.jsonl` row: `{song_id, model, variant, language, status: "ok"|"error",
  n_lines, raw_path, parsed_path, error}`. Truncate `results.jsonl` at phase start,
  append per item (partial runs remain inspectable). Missing transcript cache → error
  row pointing at Step 3.
- Error handling: `except (YouTubeTranscriptError, LLMConfigError, ValueError)` +
  **catch-all `except Exception`** per item → `status: "error"` with `error` string,
  continue. One malformed response must never kill the phase.

`STRICT_BLOCK` (harness-authored; production file untouched):
```
## Additional Requirements

1. Transcript lines are frequently fragments of one sung phrase. Whenever a run of
   consecutive transcribed lines together forms ONE lyric line from the Official
   Lyrics, merge them into a SINGLE output line using the FIRST merged line's timestamp.
2. If several transcribed lines share the exact same timestamp, merge them into one
   output line at that timestamp.
3. Never emit a partial phrase: each output line's text must be exactly one full
   lyric line from the Official Lyrics (repeated phrases allowed).
4. Lines in the Official Lyrics consisting entirely of a [bracketed] label are
   section tags (metadata), not sung phrases — never emit them as lyric lines.
```

### 8. `scripts/judge_results.py` (analysis env)

Args: `--run-dir`, `--judge-model` (default `os.environ["SOW_LLM_MODEL"]`; error if
unset), `--mechanical-only`, `--allow-self-judge`.
- **Self-judge guard (independent of the interview):** if the resolved judge model
  appears in `meta.json["models"]` and `--allow-self-judge` is absent → exit 1 with an
  explanation. With the flag, proceed and let the report badge self-judged rows.
- For each `status: "ok"` row in `results.jsonl`: load parsed LRC lines (re-parse
  `parsed/<variant>/*.lrc` with `parse_lrc_response`), official lyrics from fixture,
  `duration_seconds`.
- **Mechanical checks** (computed in-script, always; official list =
  `judge_official_lines`, i.e. tags excluded):
  - duplicate timestamps: exact `[mm:ss.xx]` string collisions (list colliding pairs);
  - order: non-decreasing check → `order_violations` count (reported, non-gating);
  - ending gap: `gap = duration_seconds - max(line.time_seconds)` (max, not last —
    order violations must not corrupt the gap; negative = beyond song end);
  - exact-match coverage: fraction of lines whose text equals an official lyric line
    (strip whitespace only);
  - partial-phrase heuristic: lines whose text is a proper substring of an official
    line;
  - **unmatched lines: lines that are neither exact-equal nor a proper substring of
    any official line** (hallucinations, tag echoes, ≥2-phrase supersets — all fail
    here; a concatenation of two official lines is longer than every official line, so
    no separate "superset" rule is needed).
- **Judge LLM** (skipped by `--mechanical-only`): one call per item via
  `openai.OpenAI(api_key/base_url from env, max_retries=0)` wrapped in
  `call_llm_with_retry(...)` (production retry util), `temperature=0`. Prompt contains:
  numbered official lyrics (tags excluded), numbered candidate LRC lines,
  `duration_seconds`, and the three criteria phrased exactly as:
  1. Each timestamp must carry a complete lyrics phrase — its text must be exactly one
     full line from the official lyrics (repeated phrases allowed); a partial/fragment
     phrase is a failure.
  2. Each timestamp must be unique — no two lines may share the same timestamp, and one
     lyric phrase must never be split across multiple lines with identical timestamps
     whose texts together form one official phrase.
  3. The last timestamp must be within 30 seconds of the total song duration:
     `0 <= duration_seconds - max(timestamp) <= 30`.
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
  Parse with `parse_judge_json`; on `JudgeParseError` retry once appending "Output ONLY
  the JSON object." to the user message; second failure → record `judge_error` (item
  keeps the mechanical verdict, `overall_pass = false`, flagged in report).
- Write `verdicts/<variant>/<song_id>__<model-slug>.json` =
  `{song_id, model, variant, judge_model, criteria_source: "judge"|"mechanical",
  mechanical: {duplicate_pairs, order_violations, ending_gap_seconds,
  exact_match_coverage, partial_phrase_line_indexes, unmatched_line_indexes},
  judge: {...}|null, criteria: {complete_phrases, unique_timestamps, ending_window,
  overall_pass}}` — `pass` values are the judge's when present, else derived from the
  strengthened mechanical checks: unmatched OR partial-substring → complete fail;
  duplicates → unique fail; `gap` outside `[0, 30]` → ending fail; `order_violations`
  never gates.
- Per-item catch-all `except Exception` → `judge_error`, continue.

### 9. `scripts/build_report.py` (analysis env, no LLM calls)

Args: `--run-dir`.
- Reads `meta.json`, `results.jsonl`, `verdicts/<variant>/*.json`; writes `scores.json`
  and `report.md`.
- Per-model-per-variant ranking row: `songs`, `pass_rate`
  (overall_pass / ok-items; **0 ok items → `N/A`, ranked last**),
  `complete_phrases_fails`, `unique_timestamps_fails`, `ending_window_fails`,
  `partial_phrase_lines` (sum), `unmatched_lines` (sum), `duplicate_timestamps` (sum),
  `order_violations` (sum), `avg_exact_match_coverage`, `judge_errors`, `errors`.
  Rank: pass_rate desc (N/A last) → partial_phrase_lines asc → unmatched_lines asc →
  coverage desc. Separate ranking table per variant, plus a `strict` vs `prod` delta
  view (pass_rate and failure-count differences) — this is the prompt-vs-capability
  read the variants exist to provide.
- `duration_suspect`: per song, if EVERY ok item (across models and variants) fails
  `ending_window` → flag the song in report.md and `scores.json` (points at fixture
  duration, not model quality).
- Provenance: report shows `criteria_source` counts per model row; a row whose
  `judge_model` ∈ `meta.json["models"]` is badged **self-judged** (derived, no extra
  plumbing).
- `report.md` header states: judge model, variants, provider host, and the two
  documented divergences (prompt keeps `[bracketed]` tags, judge list excludes them;
  `lyrics_source: raw` songs have paragraph-boundary official lines so coverage is
  structurally weaker).
- Body: per song×model×variant verdict summary with judge `notes`, per-criterion issue
  lists, relative links to `raw/<variant>/` + `parsed/<variant>/` artifacts; print the
  top-3 ranking per variant to stdout.

### 10. `fixtures/songs.example.json` (exact schema, one realistic example entry)

```json
[
  {
    "song_id": "example_song_0000000000",
    "title": "範例歌曲",
    "language": "zh",
    "youtube_url": "https://www.youtube.com/watch?v=VIDEO_ID",
    "duration_seconds": 245.0,
    "lyrics": ["第一句歌詞", "第二句歌詞"],
    "lyrics_source": "structured"
  }
]
```
(`lyrics_source` optional; DB-generated fixtures always set it. Tag lines
(`[Label]`) may appear inside `lyrics` — they are production-prompt-faithful and
excluded from judging automatically.)

## Critical files & anchors (all verified 2026-09-14)

- `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py` — reuse
  unchanged: `extract_video_id` (L462), `_format_transcript_text` (L488; reads
  `.start`/`.text` only), `build_correction_prompt` (L507; zh variant L552-577 with the
  line-count-preserving rule at L560; en variant L523-550; each has exactly one
  `## Output Format` heading), `parse_lrc_response` (L580; regex
  `^\[(\d{2}):(\d{2}\.\d{2})\]\s*(.+)`, `ValueError` when none), `fetch_youtube_transcript`
  (L677; `languages: Optional[List[str]] = None, language: str = "zh"`; fallback
  preference L672-674 can return a different actual language), `_llm_correct` (L773;
  `effective_model = llm_model or settings.SOW_LLM_MODEL` at L814; wraps ALL LLM
  failures as `YouTubeTranscriptError` at L858), production lyrics split at L893 (no
  tag stripping). No edits to this file.
- `ops/analysis-service/src/sow_analysis/workers/llm_rate_limit.py` —
  `call_llm_with_retry(sync_fn, *, description, loop=None)` (L474; async, takes a sync
  callable; budget settings L512-515). Reused for the judge call.
- `ops/analysis-service/src/sow_analysis/workers/lrc.py` — `LRCLine` dataclass
  (L142-153): fields `time_seconds`, `text`; serialization method is `format()`
  (L149-153). There is NO `__str__` — `str()` yields the dataclass repr (v1 bug,
  corrected here).
- `ops/admin-cli/src/stream_of_worship/admin/services/lrc_jobs.py` —
  `resolve_lyrics_text(song, recording)` (L39-53): structured lyrics first (tags
  preserved by `flatten_structured_lyrics`, `structured_lyrics.py:277-280`), else
  `song.lyrics_raw`.
- `ops/admin-cli/src/stream_of_worship/admin/db/client.py` — `DatabaseClient` (L60),
  `get_song` (L340), `get_recording_by_song_id` (L684),
  `list_recordings_with_songs(status, visibility, lrc_status, album, theme,
  sort_by="imported", limit, include_deleted)` (L809-818).
- `ops/admin-cli/src/stream_of_worship/admin/config.py` — `AdminConfig.get_connection_url()` (L49).
- `ops/analysis-service/src/sow_analysis/config.py` — `SOW_LLM_API_KEY` /
  `SOW_LLM_BASE_URL` / `SOW_LLM_MODEL` (L114-116);
  `SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS` 1200s (L139); `SOW_LLM_MIN_INTERVAL_SECONDS`
  2.0 (L145).
- `lab/skills/songset-constructor/scripts/fetch_pool.py` — L21-24 `PROJECT_ROOT`/
  `sys.path` bootstrap and `AdminConfig.load()` → `ConnectionProvider` wiring (L58-68;
  fetch_pool itself uses `ReadOnlyClient` — `make_fixture.py` uses `DatabaseClient`
  per the client.py methods above).

## Verified facts (from the review; implementer should not re-derive)

1. `str(LRCLine(61.5, 'x'))` → `"LRCLine(time_seconds=61.5, text='x')"`;
   `LRCLine(61.5, 'x').format()` → `[01:01.50] x`; `parse_lrc_response(format-output)`
   round-trips stably (analysis venv, direct test).
2. Admin venv: `import sow_analysis.workers.lrc` → `ModuleNotFoundError: aiosqlite`
   via `workers/__init__` L19 → `queue.py` L88 → `storage/db.py` L8 (direct test).
3. Production keeps `[Label]` tags in the correction prompt's official-lyrics list
   (`youtube_transcript.py:893` splits `lyrics_text` without filtering).
4. Analysis-env import of the production symbols takes ~24s cold (observed) — fine for
   a 6-script skill, worth a SKILL.md expectation note.
5. `fetch_pool.py`'s DB wiring pattern (`AdminConfig.load()` → `ConnectionProvider` →
   client) works as spec'd; `DatabaseClient` exposes all three methods make_fixture needs.

## Verification

Prereqs: cwd repo root; `SOW_LLM_API_KEY`/`SOW_LLM_BASE_URL` exported; analysis venv
exists; recommended `export SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS=180`.

1. Import proof (analysis env):
   `cd ops/analysis-service && ./.venv/bin/python -c "from sow_analysis.workers.youtube_transcript import build_correction_prompt, parse_lrc_response, _llm_correct, fetch_youtube_transcript, _format_transcript_text, extract_video_id; print('ok')"` → prints `ok`.
2. Admin-env boundary proof:
   `uv run --project ops/admin-cli --extra admin python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/make_fixture.py --help` → usage text (proves `_common` + admin package import without `sow_analysis`).
   Negative control (expected to fail): the same `from sow_analysis.workers.lrc import LRCLine` in the admin venv → `ModuleNotFoundError: aiosqlite` — confirms the lazy-import constraint is load-bearing.
3. Serialization proof (analysis venv, one-liner): `str(LRCLine)` is the dataclass repr,
   `.format()` emits `[mm:ss.xx] text`, and `parse_lrc_response` round-trips it.
4. Preflight behavior: with a dead model ID in `--models`, default mode prints a FAIL
   warning row and exits 0; with `--strict-ping` it exits 1.
5. Offline determinism (no LLM): throwaway run dir under `/tmp`, synthetic fixture +
   planted `parsed/` LRCs — one with a partial phrase, one with a duplicated timestamp,
   one with an unmatched (hallucinated) line, one whose last timestamp is 60s before
   duration, plus a tag-echo line `[副歌]` in the official list to prove tag exclusion →
   `judge_results.py --mechanical-only` marks the relevant criteria `pass: false` with
   `criteria_source: "mechanical"`, gap computed from `max(time_seconds)`; a clean
   synthetic LRC (exactly official lines, unique ascending timestamps, last timestamp
   10s before duration) passes all three.
6. Self-judge guard: `judge_results.py --judge-model <candidate-id>` (no flag) on a run
   dir whose `meta.json` contains that candidate → exit 1; with `--allow-self-judge` →
   proceeds and report badges the rows.
7. End-to-end mini run (~4 LLM calls): `make_fixture.py --auto 1` →
   `fetch_transcripts.py` → `run_models.py --models <one-model>` (both variants: 2
   correction calls) → `judge_results.py` (2 judge calls) → `build_report.py`. Assert
   run dir contains `raw/prod/`, `raw/strict/`, `parsed/prod/`, `parsed/strict/`,
   `verdicts/prod/`, `verdicts/strict/`, `results.jsonl` (rows carry `variant`),
   `meta.json`, `scores.json`, `report.md`; report contains one ranking table per
   variant plus the delta view.
8. Read `report.md` and one verdict JSON per variant: schema matches §8, `variant` and
   `criteria_source` present, judge notes present, `duration_suspect` logic exercised
   or visibly absent.

## Assumptions & contingencies

- Skill dir `lab/skills/` (repo convention) though user wrote `lab/skill/` — if a
  literal `lab/skill/` is wanted, `mv` the directory and fix the two command blocks in
  SKILL.md (`parents[4]` depth is unchanged by the rename).
- Judge defaults to `$SOW_LLM_MODEL`. If it is also a candidate, the run is BLOCKED at
  Step 0/Step 5 unless the user explicitly opts in with `--allow-self-judge`; the
  report badges self-judged rows so readers can discount them.
- DB unreachable in `make_fixture.py` → fill `fixtures/songs.example.json` manually
  (required `duration_seconds`; source durations from `sow-admin catalog show <song_id>`
  or the recording row).
- YouTube fetch 429/permanent failure → song dropped with per-song error in the run;
  suggest exporting `SOW_YOUTUBE_PROXY` and re-running Step 3.
- Provider must be OpenAI chat-completions compatible (production contract), and ONE
  provider (`SOW_LLM_BASE_URL`) serves both candidates and the judge. Judge failures
  after one retry count the item as fail and flag `judge_error` (conservative ranking).
- A 429-heavy candidate can stall per-item for up to `SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS`
  (production default 1200s); eval runs export `180` (documented in Prerequisites).
- The transcript's actual language can differ from the requested one (production
  fallback chain); recorded as `fetched_language_code` and shown in Step 3 output.
- Mixed-language corpora: DB mode labels every entry with the global `--language` (no
  per-song language column exists); manual fixtures may set per-entry `language`.
- `lyrics_source: raw` fixtures split official lyrics on paragraph boundaries, not
  sung phrases — exact-match coverage is structurally weaker for those songs; the
  report carries this caveat per song.
- The prompt-lyrics vs judge-lyrics tag handling is a deliberate, documented divergence
  (prompt = production-faithful with tags; judging = sung phrases only).
- If `ops/analysis-service/.venv` is absent on another host, run
  `uv sync --project ops/analysis-service` first (heavy install, in SKILL.md
  prerequisites).
- `graphify update .` after the session's code changes (AGENTS.md rule; mechanical
  post-work step, not planned here).
