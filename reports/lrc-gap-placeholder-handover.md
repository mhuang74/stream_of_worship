# Handover: LRC Gap Placeholder Insertion (2026-09-15)

Feature: insert blank placeholder LRC lines during long instrumental gaps so the rendered lyrics video blanks the screen (congregation stops staring at the last sung line) instead of lingering on stale lyrics. Full design was settled with the user via grilling; every semantic below is user-confirmed.

Branch: `admin_cli_fixes_0913` (pushes after commit per AGENTS.md session-completion rule).

All five admin call sites verified carrying `tempo_bpm=recording.tempo_bpm` (lrc_jobs.py:128/270/359, audio.py:5976/6963). All touched Python files ast-parse clean. `parse_lrc_response` regex requires non-empty text — placeholders can only come from the deterministic insert, never the LLM.

Known cosmetic nit (non-blocking): audio.py:6949-6950 lost a blank line between `return (True, None)` and `new_job = analysis_client.submit_lrc(` — run Black (line 100) over edited Python files before commit.

## Status: implementation COMPLETE, all affected suites verified green; full-suite confirmation run outstanding

Verified runs (final states): test_youtube_transcript_gaps 15/15; test_youtube_transcript 139 passed; test_structured_lyrics_aligner 60 passed 1 skipped (confirmed twice, last run after all fixes); test_section_segmenter 42 passed 1 skipped; webapp vitest 15 files 362 passed. A full-suite run DURING the MockSettings completion showed 23 failures, all of them MockSettings AttributeErrors / one max-attempts mismatch, later fixed; the remaining gap is one confirmation run of the whole suite plus the admin-cli suite. Earlier full-suite failures in section_segmenter/structured_lyrics_aligner were PRE-EXISTING on this branch (MockSettings in test_mvsep_client.py injects a module-wide config mock; it lacked attrs those tests read). The completed MockSettings (gap knobs + SOW_LLM_* + SOW_STEP_HEARTBEAT_INTERVAL_SECONDS) fixes them; note SOW_LLM_STRUCTURED_LYRICS_MAX_ATTEMPTS=3 on the mock vs test_max_attempts_exhausted_uses_best_effort expecting 2 — if that test fails with IndexError: pop from empty list, set the mock's value to 2.

Do NOT re-derive design decisions. The remaining work is: (1) one full-suite run to confirm green, (2) admin-cli test suite run, (3) cleanup + docs + commit + push.

## Settled semantics (do not change)

- Gap = `next_line_start − line_end`, where line_end = end of the LAST transcript cue starting in `[line_start, next_line_start)`. Span semantics cover merged AND dropped cues (conservative: never underestimates singing).
- Threshold: gap > 12 beats (strictly greater). Placeholder = line_end + 4 beats, empty text, inserted between the pair. One placeholder per gap. Interior gaps only (no intro/outro placeholders).
- Skips insertion when: tempo_bpm absent/≤0, no cues, <2 LRC lines, no cue matches a line's timestamp, threshold_seconds ≤ 0, or placeholder would land outside (line, next). The clamp to next−0.01 is defensive-only: for any qualifying gap, next − line_end > 12 beats while placeholder − line_end = 4 beats, so placeholder < next − 8 beats always holds — it can never exceed next_start.
- Deterministic post-LLM insertion (NOT prompt-driven): LLM correction prompt is untouched; eval bake-off harness unaffected.
- LRC cache tag bumped `lrc-lang-v4` → `lrc-lang-v5` (queue.py `_compute_lrc_cache_key`) so re-submitted jobs regenerate.
- Whisper/Qwen3 fallback paths intentionally untouched (explicit follow-up; user approved YouTube-only scope).

Song 十架的愛 (shi_jia_de_ai_288ba1c9), tempo_bpm = 68.0 (candidate_pool.csv row 64):
`[02:19.93] 十架的愛` + cue duration 6.09s → line_end 146.02; + 4 beats (3.529s) = 149.549 → renders `[02:29.55]`; next line at 179.90 (gap 33.9s > 12 beats = 10.59s). Verified: function reproduces `[02:19.93] / [02:29.55] / [02:59.90]` exactly (rounding via LRCLine.format's `05.2f`).

## Changes on disk (13 files, all AST-parse-verified)

1. `ops/analysis-service/src/sow_analysis/config.py` — new knobs `SOW_LRC_GAP_THRESHOLD_BEATS=12.0`, `SOW_LRC_GAP_PLACEHOLDER_BEATS=4.0` (after SOW_LLM_MODEL block, ~line 118).
2. `ops/analysis-service/src/sow_analysis/models.py` — `LrcOptions.tempo_bpm: Optional[float] = None` (line 129). NOTE: during editing, `qwen3_asr_snap_threshold` and `qwen3_asr_min_usable_segments` were accidentally dropped then restored — verify both exist with a quick `LrcOptions()` instantiation before running suites (last verified OK).
3. `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py` — the core:
   - `_TIMESTAMP_MATCH_TOLERANCE = 0.011` (line ~25)
   - `TranscriptCue` frozen dataclass (start, duration, `.end` property) + `_extract_cue_timings(transcript)` — handles snippet objects AND dicts (dict path needed by fixture script), clamps negative durations (lines ~698-716)
   - `insert_gap_placeholder_lines(lrc_lines, cues, tempo_bpm)` (lines ~719-813): sorts lines/cues; per consecutive pair computes line_end from cues in window; guards: gap <= threshold+1e-6 → skip; placeholder clamp to next−0.01 with 1e-6 tolerance (defensive, unreachable); zero-threshold early return; extensive INFO logging
   - `youtube_transcript_to_lrc(..., tempo_bpm: Optional[float] = None)` — new Step 6 (lines ~1120-1123): `cues = _extract_cue_timings(transcript); lrc_lines = insert_gap_placeholder_lines(lrc_lines, cues, tempo_bpm)` inserted after Step 5 parse, before elapsed logging
   - **Editing hazards hit repeatedly**: edit tool spliced/corrupted this file twice (duplicate def headers, missing Step 4, duplicated import block). All fixed and ast-parsed OK. If anything looks off, `python -c "import ast; ast.parse(open(f).read())"` each file first.
4. `ops/analysis-service/src/sow_analysis/workers/lrc.py` — `try_youtube_transcript_lrc` passes `tempo_bpm=options.tempo_bpm` into `youtube_transcript_to_lrc` (line ~887).
5. `ops/analysis-service/src/sow_analysis/workers/queue.py` — cache tag `lrc-lang-v5` (line 66).
6. `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py` — `submit_lrc(..., tempo_bpm: Optional[float] = None)` param + payload `"options": {"tempo_bpm": tempo_bpm}` + docstring. Optional is already imported.
7. `ops/admin-cli/src/stream_of_worship/admin/services/lrc_jobs.py` — 3 call sites pass `tempo_bpm=recording.tempo_bpm` (submit_lrc_single ~115, submit_lrc_batch ~257, submit_lrc_job ~346).
8. `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` — 2 call sites pass `tempo_bpm=recording.tempo_bpm` (~5963, ~6950).
9. `delivery/webapp/src/lib/render/lrc-parser.ts` — `parseLRC` now KEEPS empty-text lines (removed `if (text)` guard, comment explains gap placeholders, lines ~40-46).
10. `delivery/webapp/src/lib/render/chapters.ts` — `generateChaptersManifest` filters `line.text.trim() !== ''` before mapping chapter lines (~line 95). This keeps jump chips clean; do NOT also filter in LyricJumpList (reverted — manifest is the single filter point; double-filtering shifts lineIndex → wrong seek).
11. `delivery/webapp/src/test/lib/render/lrc-parser.test.ts` — flipped 3 pinned tests: "skips lines without text" → "keeps empty-text lines as gap placeholders" (3 lines incl. empty at 5.5s); zero-length + whitespace-only tests now expect length 2 with `""` text. All 59 parser tests pass.
12. `ops/analysis-service/tests/test_youtube_transcript_gaps.py` (NEW, 15 tests) — worked example + threshold/span/tempo/cues/edge cases. ALL PASS.
13. `ops/analysis-service/tests/test_mvsep_client.py` — `MockSettings` extended with all attrs the module-swap-starved tests need: gap knobs, SOW_YOUTUBE_PROXY*, SOW_COMPONENTS_USE_LLM_SEGMENTATION=False, SOW_LLM_* (API_KEY/BASE_URL/MODEL/SEGMENTATION_*/STRUCTURED_LYRICS_*/MAX_CONCURRENT/MIN_INTERVAL/RATE_LIMIT_*/TIMEOUT), SOW_STEP_HEARTBEAT_INTERVAL_SECONDS. These tests were ALREADY broken pre-feature on this branch (23 failures incl. section_segmenter/structured_lyrics_aligner MockSettings AttributeErrors); the MockSettings completion fixes them.

## Verified results (evidence)

- `pytest tests/test_youtube_transcript_gaps.py` → 15 passed.
- `pytest tests/test_youtube_transcript.py` → 139 passed.
- `pytest tests/test_structured_lyrics_aligner.py` → 60 passed, 1 skipped.
- `pytest tests/test_section_segmenter.py` → 42 passed, 1 skipped.
- Full suite last ran DURING mid-fixes (23 failures = the MockSettings gaps + gaps-file, all since addressed); individual reruns of every affected file pass post-fix. **One confirming full-suite run remains** — run it as step 1 of Remaining steps below.
- Webapp: `pnpm vitest run src/test/lib/render` → 59 passed; `src/test/lib/render src/test/components/play` → 15 files, 362 passed.
- Throwaway script `/tmp/verify_gap_placeholders.py` — end-to-end fixture verification PASSED both cases (worked example renders `[02:29.55]` exactly; 0sk6Kk3G0QA fixture → 1 placeholder at 126.42+3.529=129.95 after the 30.28s real gap). DELETE this file before commit (it's in /tmp, but confirm).
- Render worker needs NO changes: `frame_renderer.render_lyrics` (frame_renderer.py:735) already has the blank-line branch — holds previous lyric `_BLANK_PREVIOUS_HOLD_SECONDS`, fades, blanks, previews next lyric 4 beats early via `_compute_blank_preview_alpha` with segment tempo_bpm (fallback `_DEFAULT_TEMPO_BPM`); its `parse_lrc` keeps empty-text lines already. `video_engine` passes `segment.item.tempo_bpm` through.
- ProjectionPlayer is video-only (no lyric rendering) — no webapp projection change needed.

## Remaining steps (in order)

1. Full analysis-service suite: `cd ops/analysis-service && uv run --extra dev pytest tests/ -q --tb=line` (expect ~873 passed, 8 skipped; takes ~2.5-3 min, auto-backgrounds — wait for delivery, do not relaunch). If `test_section_segmenter`/`structured_lyrics_aligner` still show MockSettings AttributeErrors, compare against test_mvsep_client.py MockSettings field list above.
2. Admin CLI suite: `uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest -v` (NEVER bare pytest from repo root — torch INTERNALERROR; see memory rule).
3. `git checkout -- ops/analysis-service/uv.lock` if it shows modified again (test runs churn it).
4. Docs: CONTEXT.md new term under `## Language > Lyrics` — suggested: **Gap Placeholder**: an intentionally blank timestamped Lyrics line marking a long instrumental passage; rendered as a blank screen moment rather than lingering lyrics. _Avoid_: empty line, spacer. ADR candidate (docs/adr/0008-…): qualifies (hard-to-reverse LRC schema impact for curated lyrics + surprising: producer inserts lines the LLM never emitted + real tradeoff post-LLM vs prompt-driven). Number it 0008, format per docs/adr ADR-FORMAT.
5. `graphify update .` (AGENTS.md rule after code changes).
6. Delete `/tmp/verify_gap_placeholders.py` (throwaway).
7. Commit (single feat commit is fine; exclude uv.lock churn), then `git pull --rebase && git push && git status` (MUST show up to date — session-completion rule).
8. Optional follow-up (user-approved deferral, do NOT implement now): Whisper/Qwen3 fallback path gap parity in lrc.py — note in PR description as follow-up.

## Key context for future sessions

- Render worker beat math already matches the 4-beat convention (`4*60/bpm` in _compute_blank_preview_alpha) — the LRC-side constants mirror it deliberately.
- Component analysis reads lyrics.lrc via `parse_lrc` (sow_analysis workers/lrc_parser.py, byte-identical admin twin) — it KEEPS blank lines; `_render_numbered_lrc` will number placeholders, so regenerated LRC shifts component line_start/line_end by the inserted-line count. Existing workflow: re-run component analysis after LRC regeneration (no code change needed).
- LRC result cache: nothing auto-regenerates; admins re-submit LRC jobs (`sow-admin lyrics generate`) to regenerate with placeholders; cache-tag bump ensures no stale hits.
- The worked-example fixture `output/eval-models-for-fixing-youtube-transcription/transcripts/0sk6Kk3G0QA__zh.json` shows the real snippet shape: `{start, duration, text}` — `duration` IS present on YouTube cues; that was the key discovery enabling post-LLM span semantics.
- Editing hazard learned: the line-anchored edit tool corrupted youtube_transcript.py twice via mid-function splices; after EVERY structural edit re-read the region. ast-parse is the cheap gate.