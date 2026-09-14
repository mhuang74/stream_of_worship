# Add grounded worked example to YouTube-transcript LRC correction prompt

## Context

`reports/worked-example-problem-yt-transcript-prompt.md` documents dropping the
worked example from the LRC correction prompt: no artifact-faithful example existed
that didn't teach content-timing shift or contradict the bake-off-validated rules.
The user has now supplied a **ground-truth ruling** that overturns that decision:

For the first two cues of 全新的生命 (FAmBStYXv6I, snippet starts 15.914 and 23.254),
the correct LRC output is **one line per cue**, merging official-lyrics lines that
share one cue:

```
[00:15.91] 放下一切憂傷和羞愧 放下一切痛苦和纏累
[00:23.25] 主我來到祢施恩座前
```

— despite the scraped/structured lyrics showing the first sung phrase split across
two lines (放下一切憂傷和羞愧 / 放下一切痛苦和纏累). The report's Option A dilemma
("the middle line needs a timestamp that exists nowhere in the input") assumed a
**3-line** output; under the clarified 2-line ground truth no borrowed timestamp is
needed and the dilemma dissolves.

Deliverable: add the (now groundable) worked example to the production prompt, amend
the rule that forbids it, fix the eval judge so the change is validatable, and
re-bake-off before pushing.

## Findings (all verified against artifacts this session)

1. **Cue structure** — `output/eval-models-for-fixing-youtube-transcription/transcripts/FAmBStYXv6I__zh.json`:
   the 15.914 entry is ONE snippet whose `text` contains an embedded `\n`
   ("Laying down all my sorrow and shame\nLaying down all my sin and my pain") — both
   fragments share timestamp 15.914. `_format_transcript_text` renders it as one
   `00:15.91` header followed by two text lines. Same shape at 31.261, 48.876, 65.29,
   99.286, 115.199, 132.548, 165.209, 223.159.
2. **The "PASS" outputs park lyrics on metadata cues** — both
   `20260915-strict-merged-verify-run/parsed/prod/…deepseek-v4-flash.lrc` and the
   20260914 strict PASS start `[00:05.23] 放下一切憂傷和羞愧` /
   `[00:08.24] 放下一切痛苦和纏累` — the title and credits cues — ~10s early. This is
   wrong per the ruling: those cues match no lyric and must produce NO output.
3. **Rule 3 forbids the correct output** — `STRICT_REQUIREMENTS_BLOCK` rule 3
   (`youtube_transcript.py:515-516`): "each output line's text must be exactly one
   full lyric line" — a two-line join matches no single official line.
4. **Judge is blind to the defect AND fails the correct output** — proven by running
   the skill's `mechanical_checks` on both shapes this session:
   - ground-truth merged output → `unmatched_line_indexes [0]`,
     `complete_phrases False`, `overall_pass False`;
   - shift-parked output → coverage 1.0, passes everything.
   Neither `mechanical_checks` nor `JUDGE_CRITERIA` looks at cues/content-timing, so
   the bake-off's "100% pass" validated the wrong behavior for this case.
5. **Official lyric text** — production DB `songs.lyrics_lines` for
   `quan_xin_de_sheng_ming_5a797042` and the bake-off fixture both read
   `主我來到祢施恩座前` (no U+3000 between 主 and 我). The user's ruling renders
   `主　我來到祢施恩座前`; the example must match the lyrics as production supplies
   them, so it uses the DB text (see Assumptions).
6. Cache key is `lrc-lang-v3` (`queue.py:66`); prompt changes require a bump.
7. Eval-skill `run_models.py` keeps a duplicate `STRICT_BLOCK` (strict-variant
   machinery; currently a no-op) that must stay in sync.

## Approach

### Step 1 — Amend rule 3 of `STRICT_REQUIREMENTS_BLOCK`

File: `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py`
(lines 507–518). Replace rule 3 only; keep rules 1, 2, 4 and the section heading
byte-identical (they were bake-off-validated; existing tests pin
"Never emit a partial phrase", "never emit them as lyric lines"):

```
3. Never emit a partial phrase: each output line's text must be one full lyric line
   from the Official Lyrics — or, when one transcribed cue covers two or more official
   lyric lines, those complete lines joined with a single space (repeated phrases
   allowed).
```

### Step 2 — Add `WORKED_EXAMPLE_ZH` constant and insert into the zh template

Same file. New module constant above `build_correction_prompt` (verbatim content;
note the embedded triple-backtick fences are literal prompt text, and the curly
quotes in “A New Beginning” are U+201C/U+201D exactly as the transcript renders):

```python
WORKED_EXAMPLE_ZH = """\
## Worked Example

This example is from a different song. Learn the merge and drop behavior it shows;
never copy its lyric text into your output.

Transcribed subtitle (excerpt):

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

Official lyrics (excerpt):

```
[Verse]
放下一切憂傷和羞愧
放下一切痛苦和纏累
主我來到祢施恩座前
```

Correct output:

```
[00:15.91] 放下一切憂傷和羞愧 放下一切痛苦和纏累
[00:23.25] 主我來到祢施恩座前
```

Why: the 00:05.23 and 00:08.24 cues are a song title and a credits card — they match
no lyric line, so they produce NO output lines. The 00:15.91 cue holds two
transcribed lines that share one timestamp and together cover TWO official lyric
lines, so they merge into ONE output line at 00:15.91, both texts joined with a
single space. The 00:23.25 cue covers one official line and keeps its own
timestamp."""
```

Insertion: in the **zh template only** (the `language == "zh"` return), between the
block and the output format — change

```
{STRICT_REQUIREMENTS_BLOCK}

## Output Format
```

to

```
{STRICT_REQUIREMENTS_BLOCK}

{WORKED_EXAMPLE_ZH}

## Output Format
```

The en template gets Step 1's amended block (shared constant) but **no example** —
no grounded en-language bake-off artifacts exist, and fabricating one repeats the
original provenance sin (see Assumptions). Timestamps in the example are the
LRC-normalized `[mm:ss.xx]` forms (`_format_transcript_text` renders 5.228→`00:05.23`,
15.914→`00:15.91`, 23.254→`00:23.25`; verified this session), not the raw snippet
starts (15.914/23.254). Update `build_correction_prompt`'s docstring to mention the
embedded worked example (zh only).

### Step 3 — Bump LRC cache key

`ops/analysis-service/src/sow_analysis/workers/queue.py:66`:
`lrc-lang-v3` → `lrc-lang-v4` (cached LRCs must regenerate under the new prompt).
No test pins the version literal (verified: tests only compare two keys for
inequality).

### Step 4 — Sync the eval skill's `STRICT_BLOCK` copy

`lab/skills/eval-models-for-fixing-youtube-transcription/scripts/run_models.py`
(lines 42–53): apply Step 1's rule-3 replacement verbatim so the future-iteration
variant machinery doesn't drift from production. (Its injection guard keyed on
`"## Additional Requirements" not in prompt` still no-ops against the prod prompt —
unchanged.)

### Step 5 — Judge: recognize joins, add placement criterion

File: `lab/skills/eval-models-for-fixing-youtube-transcription/scripts/judge_results.py`.

5a. New helper (place above `mechanical_checks`) — joined lines count as complete:

```python
def _is_official_line_or_join(text: str, official_stripped: list[str]) -> bool:
    """True if text is one official line, or >=2 official lines joined by single spaces."""
    if text in set(official_stripped):
        return True
    n = len(text)

    @lru_cache(maxsize=None)
    def match_from(i: int) -> bool:
        if i == n:
            return True
        for off in official_stripped:
            end = i + len(off)
            if end <= n and text.startswith(off, i):
                if end == n:
                    return True
                if text[end] == " " and match_from(end + 1):
                    return True
        return False

    return match_from(0)
```

(Import `lru_cache` from `functools` at module top. Because the single-line case is
handled by the early set membership return, `match_from(0)` returning True implies
≥2 segments. Backtracking handles multi-word official lines like
`緊緊抓住祢 唯一的主`, whose internal ASCII space must not be mistaken for a join
delimiter.)

5b. In `mechanical_checks`, classify in this order: exact
(`text in official_set`) → `_is_official_line_or_join(...)` (also append to
`exact_idx`) → partial substring → unmatched. Joined lines therefore count toward
`exact_match_coverage` as complete content.

5c. Reword `JUDGE_CRITERIA` criterion 1 and add criterion 4:

```
1. Each timestamp must carry complete lyrics — its text must be exactly one full line
   from the official lyrics, or two or more complete official lines joined with single
   spaces when one transcript cue covers several (repeated phrases allowed); a
   partial/fragment phrase is a failure.
```

Criterion 4 (append after the existing ending-window criterion; keep criteria 2–3
verbatim):

```
4. Placement: each output line's timestamp must be the start of the transcript cue
   that contains the matching sung content. A lyric line placed on a cue whose text
   is a title, credits card, spoken introduction, or a different lyric line's content
   is a failure.
```

5d. `JUDGE_SCHEMA_HINT`: add a `"placement"` key to the schema example:
`"placement": {"pass": true, "issues": [{"index": 0, "timestamp": "00:05.23", "text": "...", "reason": "..."}]}`.

5e. `build_judge_prompt(official, lrc_lines, duration_seconds, transcript_text=None)`:
when `transcript_text` is provided, insert a section between Official and Candidate:

```
## Transcript (timestamped cues)
```
{transcript_text}
```
```

When None, insert the line
`(Transcript unavailable — criterion 4 cannot be assessed; mark placement pass with a note.)`.

5f. In `main()`: load the transcript per item and judge placement.
- `transcripts_dir = meta.get("transcripts_dir")`.
- Per row: `entry = entries[song_id]`; build transcript text via
  `extract_video_id(entry["youtube_url"])` → `transcript_cache_path(transcripts_dir, video_id, entry.get("language", "zh"))`
  → `load_transcript(cache)["snippets"]` → `snippets_to_namespace(...)` → production
  `_format_transcript_text(...)`. Any miss (no dir / no video id / no cache file)
  → `transcript_text = None`.
- Extend the production import (`from sow_analysis.workers.youtube_transcript import
  parse_lrc_response`) with `extract_video_id, _format_transcript_text`; import
  `transcript_cache_path, load_transcript, snippets_to_namespace` from `_common`.
- LLM-judge path: `verdict["criteria"]["placement"] = bool(judge_verdict["criteria"].get("placement", {}).get("pass", False))`.
- If `transcript_text is None` (LLM path): force
  `verdict["criteria"]["placement"] = True` after parsing (deterministic skip; the
  prompt also tells the judge to pass it).
- `--mechanical-only` path: `criteria["placement"] = True` (placement is
  LLM-assessed only; mechanical checks cannot see cues).

### Step 6 — Report plumbing for the 4th criterion

File: `lab/skills/eval-models-for-fixing-youtube-transcription/scripts/build_report.py`.

- `agg_row`: init `"placement_fails": 0`; count
  `if not c.get("placement", True): row["placement_fails"] += 1`
  (`.get(..., True)` keeps pre-existing run dirs reportable).
- Ranking table header and row: add a `placement fails` column after `ending fails`.
- Delta `failure_delta` keys tuple: add `"placement_fails"`.
- Per-item render loop: iterate
  `("complete_phrases", "unique_timestamps", "ending_window", "placement")`.

### Step 7 — Docs

- `lab/skills/eval-models-for-fixing-youtube-transcription/SKILL.md`: update the
  Overview criteria list (criterion 1 reworded; add criterion 4 — placement,
  LLM-judge only, marked pass under `--mechanical-only`); update the `prod` variant
  description to "…embeds the 'Additional Requirements' block and the zh worked
  example…".
- Append a dated addendum section to
  `reports/worked-example-problem-yt-transcript-prompt.md` (do not rewrite history):
  "## Update — 2026-09-14 clarification (decision overturned)". Content: the ruling
  (one output line per cue; 00:15.91 merged join), why Option A's dilemma dissolves
  (wrong 3-line assumption), the two defects found (rule 3 forbids the correct merge;
  judge passes parked outputs and fails the correct one — cite this session's
  mechanical experiment), and the new decision (grounded example added to zh prompt,
  rule 3 amended, placement criterion added, re-bake-off required before ship).

### Step 8 — Tests

File: `ops/analysis-service/tests/test_youtube_transcript.py`, class
`TestBuildCorrectionPrompt` (existing tests in lines 60–101 keep passing —
"Never emit a partial phrase" and the block-order assertions are retained by Steps
1–2). Add:

```python
    def test_includes_worked_example_zh(self):
        prompt = build_correction_prompt("00:00.00\ntest\n", ["測試"])
        assert "## Worked Example" in prompt
        assert "放下一切憂傷和羞愧 放下一切痛苦和纏累" in prompt
        assert "主我來到祢施恩座前" in prompt
        assert (
            prompt.index("## Additional Requirements")
            < prompt.index("## Worked Example")
            < prompt.index("## Output Format")
        )

    def test_worked_example_absent_from_en_prompt(self):
        prompt = build_correction_prompt("00:00.00\ntest\n", ["Test"], language="en")
        assert "## Worked Example" not in prompt

    def test_rule3_allows_joined_lines(self):
        prompt = build_correction_prompt("00:00.00\ntest\n", ["測試"])
        assert "Never emit a partial phrase" in prompt
        assert "joined with a single space" in prompt
```

## Critical files & anchors

1. `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py` —
   `STRICT_REQUIREMENTS_BLOCK` (507–518), new `WORKED_EXAMPLE_ZH` + zh template
   (571–598). The example text is byte-exact above; do not re-type from the user
   message (it uses raw seconds; the prompt uses `[mm:ss.xx]`).
2. `ops/analysis-service/src/sow_analysis/workers/queue.py:66` — cache key bump.
3. `lab/skills/eval-models-for-fixing-youtube-transcription/scripts/judge_results.py` —
   `mechanical_checks` (80–126), `JUDGE_CRITERIA` (52–61), `build_judge_prompt`
   (64–77), criteria plumbing in `main()` (273–282).
4. `lab/skills/eval-models-for-fixing-youtube-transcription/scripts/build_report.py` —
   `agg_row` (89–131), delta keys (173–181), per-item loop (338).
5. `lab/skills/eval-models-for-fixing-youtube-transcription/scripts/run_models.py` —
   `STRICT_BLOCK` sync (42–53).

## Verification

Prereqs: LLM creds auto-load from `/opt/sow/.env` (host default; scripts never echo
values). Export `SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS=180` for eval runs (skill note).
Transcripts are already cached (no YouTube calls).

1. **Unit tests** (all green, incl. 3 new):
   `cd ops/analysis-service && uv run --extra dev pytest tests/test_youtube_transcript.py -v`
2. **Judge-fix proof** (throwaway script, delete after): feed two candidate texts
   through the skill's fixed `mechanical_checks` with the fixture's tag-excluded
   official lines —
   (a) `[00:15.91] 放下一切憂傷和羞愧 放下一切痛苦和纏累` +
   `[00:23.25] 主我來到祢施恩座前` → `complete_phrases True`,
   `unmatched_line_indexes []`;
   (b) `[00:05.23] 放下一切憂傷和羞愧` + `[00:08.24] 放下一切痛苦和纏累` +
   `[00:15.91] 主我來到祢施恩座前` → still mechanically complete (parking is
   criterion 4's job, LLM-judged) — confirming the division of labor.
3. **End-to-end re-bake-off** (the empirical validation the report demands):
   ```
   uv run --project ops/analysis-service python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/run_models.py \
     --fixtures output/eval-models-for-fixing-youtube-transcription/fixtures-20260914-005330.json \
     --models deepseek-v4-flash --variants prod
   uv run --project ops/analysis-service python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/judge_results.py --run-dir <new-run-dir>
   uv run --project ops/analysis-service python lab/skills/eval-models-for-fixing-youtube-transcription/scripts/build_report.py --run-dir <new-run-dir>
   ```
   **Success gate** (all must hold for the parsed LRC of
   `quan_xin_de_sheng_ming_5a797042__deepseek-v4-flash.lrc`):
   - first line exactly `[00:15.91] 放下一切憂傷和羞愧 放下一切痛苦和纏累`,
     second exactly `[00:23.25] 主我來到祢施恩座前`;
   - NO `[00:05.23]` or `[00:08.24]` output lines (title/credits cues dropped);
   - verdict `overall_pass: true` with `complete_phrases/unique_timestamps/
     ending_window/placement` all true.
   Contingency: if deepseek-v4-flash fails the gate, repeat with
   `qwen3.6-35b-fast` (the other previously-PASS model). If both fail: stop, do not
   weaken criteria, keep artifacts, and report back before pushing — the example
   then ships unvalidated and needs user review.
4. **Cleanup/push** (repo-mandated): `uvx black --line-length 100` + `uvx ruff check`
   on touched Python files; `graphify update .` (churn → separate chore commit);
   append nothing else. `git pull --rebase && git push` until `git status` shows up
   to date with origin.

## Assumptions & contingencies

- **主我 text**: the example uses `主我來到祢施恩座前` (no U+3000) because that is
  what production supplies (DB `songs.lyrics_lines`, `lyrics_raw`, and the bake-off
  fixture all agree) and rule 3 requires output text to match the Official Lyrics as
  given. If the canonical text should contain a full-width space, fix the DB lyrics
  first — separate task, out of scope here.
- **zh-only example**: en template gets the amended rules but no example until an
  en-language bake-off produces grounded artifacts.
- **Accepted tension**: the zh/en main Rules still say "Preserve the number of
  lines"; the Additional Requirements block overrides (empirically demonstrated by
  the strict PASS models). Left untouched to minimize diff to the validated prompt.
- **Old run dirs** remain reportable: placement defaults to pass when absent from a
  verdict (`c.get("placement", True)`), and judging an old run without cached
  transcripts forces placement pass with the unavailable-transcript note.
- If `judge_results.py` meets a transcript-language mismatch (cache keyed
  `<video_id>__zh.json`), `transcript_cache_path` handles the lookup — no new logic.