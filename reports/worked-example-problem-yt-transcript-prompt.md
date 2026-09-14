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