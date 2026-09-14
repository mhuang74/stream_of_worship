# Context

`reports/worked-example-problem-yt-transcript-prompt.md` dropped the worked example because every grounded construction either parked lyric lines on metadata timestamps or interpolated a timestamp absent from the excerpt input. User ground truth (2026-09-14, supersedes): **YouTube transcript timestamp granularity dictates the line unit** — two consecutive official lyric lines covered by ONE transcript block merge into ONE output line at that block's timestamp, joined with a single half-width space. Correct output for `FAmBStYXv6I`:

```
[00:15.91] 放下一切憂傷和羞愧 放下一切痛苦和纏累
[00:23.25] 主　我來到祢施恩座前
```

Including the `23.254` block in the example excerpt supplies the middle line's timestamp (the exact thing Option A lacked), making the example fully input-derivable. Catalog/DB stays split (source-faithful, user decision); the join lives in the prompt. Timestamps use 2 decimals (production `_format_transcript_text` renders `mm:ss.SS`; user confirmed precision is not an issue). The previously merged verify-run PASS shape (metadata-parking) is invalid under this ground truth, so the judge/mechanical scorer must accept joined lines and the change must be re-baked-off per the report's own flag.

# Approach

Sequential; steps 1–4 are code edits, step 5 tests, step 6 behavioral bake-off, step 7 report. Bake-off (step 6) requires steps 1–4 done.

## 1. Reword rules + add worked example — `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py`

Replace `STRICT_REQUIREMENTS_BLOCK` (:507-518) verbatim with:

```
## Additional Requirements

Timestamp granularity dictates the output line unit: whether two lyric phrases are
split into two output lines or merged into one output line is decided by the
transcript's timestamps.

1. Each transcribed timestamp block may contain several fragments (lines within the
   same block). Match the block's fragments, in order, against the Official Lyrics.
   Several transcribed lines sharing the same timestamp count as one block.
2. If a block's fragments together form ONE official lyric line, emit that line as a
   single output line at the block's timestamp.
3. If a block's fragments span SEVERAL consecutive official lyric lines (one timestamp
   covering them all), emit ONE output line at the block's timestamp: those official
   lines in order, joined with a single half-width space.
4. Never emit a partial phrase and never invent or interpolate text or timestamps: an
   output line's text must be exactly one full official lyric line, or the single-space
   join of several consecutive official lyric lines. Drop fragments that map to no
   official lyric (title, credits, audience noise).
5. Lines in the Official Lyrics consisting entirely of a [bracketed] label are section
   tags (metadata), not sung phrases — never emit them as lyric lines.
```

(Old rule 1 fragment-merge and old rule 2 same-timestamp merge are preserved inside rules 1–2; old rule 3's "exactly one full lyric line" prohibition is superseded by rule 3's join allowance; old rule 4 kept as rule 5.)

Add module-level constant `WORKED_EXAMPLE_ZH` next to `STRICT_REQUIREMENTS_BLOCK`, verbatim:

    (The block below is the EXACT bytes of WORKED_EXAMPLE_ZH; the internal ```
    fences are part of the string. In youtube_transcript.py this constant is a
    plain triple-quoted string — no fence-escaping needed there.)

    ~~~
    ## Example

    Transcribed block excerpt:
    ```
    00:05.23
    “A New Beginning”

    00:08.24
    Lyrics and Music by David Yu

    00:15.91
    Laying down all my sorrow and shame
    Laying down all my sin and my pain

    00:23.25
    Lord, I come to You, as I am
    ```

    Official Lyrics excerpt:
    ```
    [Verse]
    放下一切憂傷和羞愧
    放下一切痛苦和纏累
    主　我來到祢施恩座前
    ```

    Correct output:
    ```
    [00:15.91] 放下一切憂傷和羞愧 放下一切痛苦和纏累
    [00:23.25] 主　我來到祢施恩座前
    ```

    The block at 00:15.91 carries two fragments that cover two consecutive official lyric
    lines under one timestamp, so they merge into one output line joined with a single
    space. The metadata blocks (title, credits) are dropped. The [Verse] tag is never
    emitted. The block at 00:23.25 maps normally to the next official line.
    ~~~

    WORKED_EXAMPLE_ZH = the block between the ~~~ fences with every line's 4-space
    indent stripped (including the fence lines themselves); blank separator lines
    before "Official Lyrics excerpt:" and before "Correct output:" and after the
    closing ``` of each fenced block are preserved as in the source shown.

Interpolate `{WORKED_EXAMPLE_ZH}` in the zh f-string branch only, between `{STRICT_REQUIREMENTS_BLOCK}` and `## Output Format` (:592-594). En template: rules only, NO example (no validated en-official-lyrics artifact exists; fabricating one from caption text is circular). Update `build_correction_prompt` docstring (:526-529) to mention granularity merge + zh example. `parse_lrc_response` (:601) is untouched — single-space joined text parses as one line's text.

## 2. Teach the scorer joined lines — `lab/skills/eval-models-for-fixing-youtube-transcription/scripts/judge_results.py`

- In `mechanical_checks` (:80-126), after `official_set = set(official_stripped)` (:83) build:
  ```python
  joined_set = {
      " ".join(official_stripped[i : j + 1])
      for i in range(len(official_stripped))
      for j in range(i + 1, len(official_stripped))
  }
  ```
- Classification loop (:109-116): `if text in official_set or text in joined_set:` → `exact_idx` (joined lines must count as exact, not partial/unmatched; currently a joined line hits `unmatched_idx` and fails `complete_phrases` via `derive_mechanical_criteria` :131).
- `JUDGE_CRITERIA` (:52-61) rewording: criterion 1 → "exactly one full line from the official lyrics, OR the single-space join of several consecutive official lyric lines when the transcript carries them under one timestamp (repeated phrases allowed); a partial/fragment phrase is a failure". Criterion 2 → append: "a single output line joining several consecutive official lyric phrases under one timestamp is correct — do not flag it as a split or duplicate". `ENDING_WINDOW_SECONDS`, schema hint, `build_judge_prompt`, `build_report.py` unchanged.

## 3. Mirror the block in the eval skill — `lab/skills/eval-models-for-fixing-youtube-transcription/scripts/run_models.py`

Replace `STRICT_BLOCK` (:42-53) text with the step-1 block verbatim. No logic change: the injection guard (:170 `if variant == "strict" and "## Additional Requirements" not in prompt`) already skips injection because the prod prompt now embeds the block, so both variants stay identical.

## 4. Cache key bump — `ops/analysis-service/src/sow_analysis/workers/queue.py:66`

`lrc-lang-v3` → `lrc-lang-v4` in the composite cache key (prompt changed; cached corrections must re-run).

## 5. Tests — `ops/analysis-service/tests/test_youtube_transcript.py` (class `TestBuildCorrectionPrompt` :60-101)

- Existing assertions survive: "Never emit a partial phrase" (:93, still present in new rule 4), "never emit them as lyric lines" (:94, new rule 5).
- Update `test_includes_strict_requirements_zh` (:90-95): add asserts `"Timestamp granularity dictates" in prompt`, `"## Example" in prompt`, `"[00:15.91] 放下一切憂傷和羞愧 放下一切痛苦和纏累" in prompt`, and ordering `index("## Additional Requirements") < index("## Example") < index("## Output Format")`.
- Update `test_includes_strict_requirements_en` (:97-100): add assert `"## Example" not in prompt` (zh-only example) and the granularity sentence IS present (shared block).

## 6. Bake-off verification (report's "not silently patched" flag)

cwd repo root; env auto-loads from `/opt/sow/.env` (SOW_LLM_* present). Export `SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS=180` (skill ops note). Fresh run dir — NEVER pass the old run's `--run-dir` (rerun truncates state, SKILL.md standing note).

```
uv run --project ops/analysis-service python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/preflight.py --models "deepseek-v4-flash"
uv run --project ops/analysis-service python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/run_models.py --fixtures output/eval-models-for-fixing-youtube-transcription/fixtures-20260914-005330.json --models "deepseek-v4-flash" --variants prod
uv run --project ops/analysis-service python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/judge_results.py --run-dir output/eval-models-for-fixing-youtube-transcription/<new-run-id>-run
uv run --project ops/analysis-service python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/build_report.py --run-dir <same>
```

Pass criteria (all required):
1. `raw/prod/quan_xin_de_sheng_ming_5a797042__deepseek-v4-flash.txt` contains the merged line `[00:15.91] 放下一切憂傷和羞愧 放下一切痛苦和纏累`.
2. The `主` line sits at `[00:23.25]` with the official text as embedded in the prompt (catalog spacing).
3. NO `[00:05.23]` / `[00:08.24]` metadata-parking lines (old PASS shape must not reappear).
4. Judge `overall_pass: true` and `exact_match_coverage == 1.0` (step 2's joined_set makes merged lines exact).
5. Repetition blocks (99.286/132.548/165.209/223.159) produce one joined line each; hidden repetitions inside long blocks (32.7s/49.1s) are dropped, not invented.

## 7. Report addendum + push

`reports/worked-example-problem-yt-transcript-prompt.md`: append dated addendum section (edit, keep existing sections) — user's granularity ground truth, Option A/B premise dissolved by including the 23.254 block, example reinstated (zh only), rules reworded, judge synced, verify-run result + run-dir pointer, prior PASS shape invalidated. Then `graphify update .` (repo rule) and commit + push per AGENTS.md.

# Critical files & anchors

- `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py:507-518` — `STRICT_REQUIREMENTS_BLOCK` (replace); `:571-598` zh/en f-strings (example interpolation, zh only)
- `lab/skills/eval-models-for-fixing-youtube-transcription/scripts/judge_results.py:52-61,80-142` — judge criteria + `mechanical_checks` joined-set
- `lab/skills/eval-models-for-fixing-youtube-transcription/scripts/run_models.py:42-53` — `STRICT_BLOCK` mirror
- `ops/analysis-service/src/sow_analysis/workers/queue.py:66` — cache key `lrc-lang-v3` → `lrc-lang-v4`
- `ops/analysis-service/tests/test_youtube_transcript.py:90-101` — prompt-shape assertions

# Verification

- Scoped pytest (cwd `ops/analysis-service`): `uv run --extra dev pytest tests/test_youtube_transcript.py -v` — all pass incl. new example/granularity asserts.
- Behavioral proof = step 6 bake-off: raw output shows merged line at 00:15.91, no metadata parking, judge `overall_pass: true`, coverage 1.0.
- After code edits: `graphify update .`; commit + `git push` until `git status` shows up to date (AGENTS.md).

# Assumptions & contingencies

- Example lives in the zh template only; en prompts carry the reworded shared block (no validated en-official-lyrics artifact; constructing one from caption text would be circular). Revisit if an en song is validated later.
- Example's official text uses the user-quoted scraped lyrics (`主　我來到祢施恩座前`, full-width space). The runtime model always copies the official lines embedded in the prompt verbatim, so catalog spacing governs real outputs; the example teaches granularity mapping, not spacing.
- If the model still parks lyric lines on metadata timestamps despite the example: capture the artifact, record in the report addendum as an open failure; do NOT loop-retry or silently re-patch wording.
- If judge/mechanical flags a joined line as partial/unmatched after step 2: the joined_set construction is wrong — fix before concluding; never weaken the success criteria instead.
- Provider 429/524 during bake-off: per skill standing notes, `SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS=180` bounds retries; if a cell is unmeasurable (e.g. 524 ceiling), mark it unmeasurable rather than retry-looping.