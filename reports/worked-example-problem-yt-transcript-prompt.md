# Worked-Example Problem — YouTube Transcript Correction Prompt

Date: 2026-09-14
Status: worked example **dropped**; requirements block **merged** into production prompt.

## Summary

The plan for merging the eval bake-off's "Additional Requirements" block into the
production LRC correction prompt also included a **worked example** in the prompt (an
official-lyrics excerpt + transcript excerpt + correct output). During execution, the
example was dropped because every candidate construction failed grounding: the plan's
example contradicted its own cited artifacts, and the two grounded alternatives each
broke a hard constraint. The requirements block alone is what the bake-off actually
measured (0% → 100% pass rate), so only it was merged.

## Why the example was dropped

### 1. The plan's example did not match its cited evidence (fabricated provenance)

The plan cited `parsed/strict/quan_xin_de_sheng_ming_5a797042__deepseek-v4-flash.lrc`
lines 1–4 as the source for an example outputting:

```
[00:15.91] 放下一切憂傷和羞愧
[00:23.25] 放下一切痛苦和纏累
```

The actual artifact reads:

```
[00:05.23] 放下一切憂傷和羞愧
[00:08.24] 放下一切痛苦和纏累
[00:15.91] 主我來到祢施恩座前
[00:23.25] 放下一切憂傷和痛悔
```

The transcript (`transcripts/FAmBStYXv6I__zh.json`) shows the real alignment: the
00:15.91 snippet's two English fragments ("Laying down all my sorrow and shame" /
"Laying down all my sin and my pain") content-map to 放下一切憂傷和羞愧 /
放下一切痛苦和纏累, and the 00:23.25 snippet ("Lord, I come to You, as I am")
content-maps to 主我來到祢施恩座前. The plan's claimed output ([00:15.91]
放下...羞愧 / [00:23.25] 放下...痛苦纏累) matches **no model output** and no
input-derivable rule: `[00:23.25]` would double-book the snippet that belongs to
主我來到祢施恩座前, and both PASS models instead put the two 放下 lines on the
leading metadata snippets' timestamps (00:05.23 title, 00:08.24 credits).

### 2. Grounded alternatives each broke a hard constraint

**Option A — include the 00:23.25 snippet in the excerpt** (advisory fix): with a
3-line excerpt (two 放下 lines + 主我來到祢施恩座前), the middle line needs a
timestamp that exists **nowhere in the excerpt's input**. Both PASS models resolved
this via a shift-by-two artifact (lyric lines parked on the leading title/credits
snippets' timestamps, ~10s early). That is not a teachable pattern; it would teach the
model to park lyric lines on metadata slots — an unvalidated timing behavior.

**Option B — reproduce the PASS artifact verbatim** (advisory): excerpt = title +
credits + 00:15.91 two-fragment snippet; output = `[00:05.23] 放下...羞愧 /
[00:08.24] 放下...痛苦纏累 / [00:15.91] 主我來到祢施恩座前`. This is the only fully
artifact-faithful option, but it **institutionalizes content-timing shift** (~10s
early) in a production timing pipeline — worse than the problem it demonstrates.
The faithful alternative (interpolating timestamps for the second line) would
contradict rule 2's verbatim wording, which the bake-off measured as-is; rewriting it
silently would invalidate the empirical evidence.

**Option C — drop the example** (chosen): the bake-off's 0% → 100% evidence covers
the **requirements block alone** — the strict variant contained only the block, no
example. The block is the only empirically validated artifact. An example adds risk
without evidence; dropping it is conservative.

### 3. Cross-check: the other PASS artifact agrees

`qwen3.6-35b-fast`'s strict output has the same slot-parking shape (two 放下 lines at
00:05.23/00:08.24). Two independent models converged on the same content-timing-shift
resolution, confirming the mechanism (slot-collapse onto metadata-snippet timestamps)
rather than the plan's "re-time to next snippet start" narrative — which also
**collides on the full song** (`放下一切痛苦和纏累@00:23.25` vs
`主我來到祢施恩座前@00:23.25`).

## Decision

- **Merged:** the 4-rule "Additional Requirements" block, verbatim, into both zh and
  en templates of `build_correction_prompt` (before `## Output Format`).
- **Dropped:** the worked example — no artifact-faithful version exists that doesn't
  teach timing shift or contradict rule 2's measured wording.
- **Flag for future iteration:** if a validated example is wanted, it must come from
  a new bake-off run on a song where fragments actually merge into one official line
  (rule 1's real case) — or an interpolation rule must be written and re-baked-off,
  not silently patched.

## Verification evidence (this session)

- pytest: 136 passed (`ops/analysis-service/tests/test_youtube_transcript.py`).
- Prompt-shape proof: block appears exactly once, before `## Output Format`, in both
  zh and en; no example.
- End-to-end: `deepseek-v4-flash` × prod × 全新的生命 — old prod FAILED (9 dup
  timestamps + metadata echo); new prod output = 15 unique-timestamp official lines;
  judge `overall_pass: true`
  (`output/eval-models-for-fixing-youtube-transcription/20260915-strict-merged-verify-run/`).
- Cache key bumped `lrc-lang-v2` → `lrc-lang-v3`; eval skill guarded against double
  injection.
- Pushed: `96b8d7e1` (code) + `f3ef5e38` (graphify chore).

## Update — 2026-09-14 clarification (decision overturned)

The user supplied a **ground-truth ruling** for the first two cues of
全新的生命 (FAmBStYXv6I, snippet starts 15.914 and 23.254): the correct LRC output is
**one line per cue**, merging official-lyrics lines that share one cue:

```
[00:15.91] 放下一切憂傷和羞愧 放下一切痛苦和纏累
[00:23.25] 主我來到祢施恩座前
```

This overturns the drop decision: **Option A's dilemma dissolves** because it assumed
a **3-line** output (two 放下 lines + 主我…) needing a timestamp that exists nowhere
in the input. Under the clarified 2-line ground truth the two fragments of the
00:15.91 cue merge into ONE line at that cue's timestamp; no borrowed timestamp is
needed, and the 00:05.23/00:08.24 title/credits cues produce NO output.

Two defects were found this session (proven by running the eval skill's
`mechanical_checks` on both shapes):

1. **Rule 3 forbade the correct output** — "each output line's text must be exactly
   one full lyric line" rejected a two-line join that matches no single official line.
2. **The judge was blind to the real defect** — the shift-parked outputs (both PASS
   artifacts put lyric lines on the 00:05.23/00:08.24 title/credits cues, ~10s early)
   passed every check, while the correct merged output failed
   (`unmatched_line_indexes [0]`, `complete_phrases False`). The bake-off's "100%
   pass" validated the wrong behavior for this case.

New decision (implemented this session):

- Worked example added to the **zh** prompt only (`WORKED_EXAMPLE_ZH`, above
  `build_correction_prompt` in `youtube_transcript.py`); rule 3 amended to allow
  cue-covered joins (single-space join of complete official lines). Rules 1, 2, 4
  byte-identical to the validated block.
- LRC cache key bumped `lrc-lang-v3` → `lrc-lang-v4` (cached LRCs regenerate).
- Eval-skill `STRICT_BLOCK` copy kept in sync.
- Judge: joined lines count as complete (`_is_official_line_or_join`); new
  **criterion 4 — placement** (LLM-judged only; pass when transcript unavailable or
  under `--mechanical-only`); judge prompt gains a transcript section; report gains a
  `placement fails` column (old runs default placement to pass).
- **Re-bake-off required before ship** (deepseek-v4-flash prod on
  `quan_xin_de_sheng_ming_5a797042`; success gate = merged 00:15.91 first line, no
  00:05.23/00:08.24 lines, all four criteria true).
