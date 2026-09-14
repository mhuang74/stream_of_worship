# Session handover: LRC correction prompt strict-block merge

Date: 2026-09-14. Branch: `admin_cli_fixes_0913`. Pushed: `96b8d7e1` (code), `f3ef5e38` (graphify), `7c25baba` (report).

## What shipped

- `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py`: `STRICT_REQUIREMENTS_BLOCK` module constant (verbatim from the bake-off's strict block), interpolated into BOTH zh and en templates of `build_correction_prompt`, immediately before `## Output Format`. Docstring updated. **No worked example** — see `reports/worked-example-problem-yt-transcript-prompt.md`.
- `ops/analysis-service/src/sow_analysis/workers/queue.py:66`: LRC cache key `lrc-lang-v2` → `lrc-lang-v3` (all cached LRC results regenerate on next request; one LLM correction call per song).
- `lab/skills/eval-models-for-fixing-youtube-transcription/scripts/run_models.py:170`: strict variant now guarded with `and "## Additional Requirements" not in prompt` (prod embeds the block → strict is a no-op); docstring updated.
- `lab/skills/eval-models-for-fixing-youtube-transcription/SKILL.md:21-27`: prod/strict variant descriptions updated ("both variants identical today; run prod-only for screening").
- Tests added in `ops/analysis-service/tests/test_youtube_transcript.py` (`TestBuildCorrectionPrompt`): `test_includes_strict_requirements_zh`, `test_includes_strict_requirements_en` — assert block presence, exact-once semantics (via the `index < index` ordering), block-before-OutputFormat, and key rule substrings. Plan's example tests dropped with the example.

## Verification (all done, evidence in transcript)

- pytest `tests/test_youtube_transcript.py`: **136 passed**.
- Prompt-shape proof (no LLM): zh + en both show block exactly once before `## Output Format`, no `### Example`.
- End-to-end (1 LLM call + judge): `deepseek-v4-flash` × prod × 全新的生命, new run dir `output/eval-models-for-fixing-youtube-transcription/20260915-strict-merged-verify-run/`: old prod FAILED (9 dup timestamp pairs + metadata echo); new prod output = 15 lines, all unique timestamps, no metadata echo, judge `overall_pass: true`, `exact_match_coverage: 1.0`. Matches the bake-off strict result exactly.
- Black: 3 files fail `--check` but pre-existing (pristine tree also fails; all complaints in rate-limiter region, lines 276–390, untouched). Left unformatted intentionally.

## The worked-example problem (see reports/…md for full detail)

The plan's example claimed provenance from `parsed/strict/quan_xin_de_sheng_ming_5a797042__deepseek-v4-flash.lrc` lines 1–4 but contradicted it: real artifact puts 放下一切憂傷和羞愧/放下一切痛苦和纏累 at **00:05.23/00:08.24** (metadata snippets' timestamps) and 主我來到祢施恩座前 at 00:15.91. Both PASS models (deepseek-v4-flash, qwen3.6-35b-fast) used the same slot-parking (content-timing shift ~10s early). Grounded alternatives either taught timing shift (bad for a timing pipeline) or contradicted rule 2's bake-off-measured wording. Dropped; block alone is the empirically validated artifact. Future example needs a fresh bake-off on a song where fragments genuinely merge into ONE official line.

## Loose ends

- `ops/analysis-service/uv.lock` has a 1-line unstaged diff (`yt-dlp-ejs` requires-dist). It predates this session (not mine). `yt-dlp-ejs>=0.8.0` is declared in `ops/admin-cli/pyproject.toml:47`; the lock sync likely belongs to the earlier `a3481122` dep-sync commit but wasn't fully staged. Leave for the user or next session; do NOT commit blindly — but it's safe to include in any future admin-cli dep commit.
- `git pull --rebase` keeps refusing because of that unstaged `uv.lock` diff — push still succeeds; next session should either commit or restore it.
- Plan doc `local://yt-transcript-strict-prompt-plan.md` still contains the dropped example; the report is the authoritative record of why.