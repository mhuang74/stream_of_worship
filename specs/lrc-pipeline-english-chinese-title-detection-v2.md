# LRC Pipeline English/Chinese Title-Based Language Detection v2

## Summary

Enhance LRC generation so English songs use English-aware transcription, transcript
selection, prompts, and cache entries, while Chinese songs keep the current Chinese
behavior. The analysis service resolves the effective language once per LRC job from
`language` and `song_title`, then passes only resolved `zh` or `en` into downstream
pipeline paths.

This version incorporates review decisions:

- `auto` is the default admin language option.
- Any CJK character in the title resolves to `zh`, even when the title also contains
  English.
- Resolved language is logged only; it is not stored in job result or DB-visible
  metadata.

## Goals

- Improve English LRC quality without regressing Chinese LRC generation.
- Keep old persisted jobs and direct API clients compatible.
- Prevent language-specific prompt/cache changes from reusing stale Chinese-only
  outputs.
- Avoid data loss or stale cache pointers when rerunning a recording under different
  language modes.

## Non-Goals

- Do not add languages beyond `zh` and `en`.
- Do not expose resolved language in `JobResult`, recording rows, or admin DB metadata.
- Do not change canonical lyrics storage or scraping behavior.

## Language Option Semantics

`LrcOptions.language` should accept exactly:

- `auto`: resolve from title/lyrics.
- `zh`: force Chinese behavior.
- `en`: force English behavior.

Admin CLI commands should default to `language="auto"`. Explicit `--lang zh` and
`--lang en` remain available for exceptions such as pinyin Chinese titles or imported
metadata that does not reflect the actual sung language.

The API should validate language values at submission time and reject anything other
than `auto`, `zh`, or `en`. Internal transcription, prompt, transcript, and cache code
must receive only resolved `zh` or `en`, never raw `auto`.

## Request Model Changes

Add `song_title: str = ""` to `LrcJobRequest`.

Compatibility requirements:

- Missing `song_title` remains valid for old persisted jobs and direct API clients.
- Existing `language="zh"` payloads remain valid and force Chinese behavior.
- Existing jobs with no language field continue to use the current Pydantic default.
  If the model default changes to `auto`, confirm old persisted job JSON without an
  options block still behaves acceptably. If not, preserve backward compatibility with
  a migration shim or explicit legacy reconstruction behavior.

## Language Resolver

Create a shared resolver, for example:

```python
resolve_lrc_language(language: str, song_title: str, lyrics_text: str) -> Literal["zh", "en"]
```

Rules:

1. Explicit `zh` or `en` wins.
2. `auto` inspects `song_title`.
3. If `song_title` contains any CJK character, resolve `zh`.
4. Else if `song_title` contains Latin letters, resolve `en`.
5. Else inspect `lyrics_text` with the same CJK-first rule.
6. Empty, numeric-only, punctuation-only, or otherwise ambiguous title and lyrics
   default to `zh` for backward compatibility.

Recommended script detection:

- CJK: Unicode ranges covering CJK Unified Ideographs and common CJK extensions used
  by Chinese lyrics.
- Latin: ASCII A-Z/a-z is sufficient for this feature unless current catalog data
  shows accented English titles.

Log the decision once near the start of `JobQueue._process_lrc_job`, including:

- requested language
- resolved language
- title
- reason, such as `explicit`, `title_cjk`, `title_latin`, `lyrics_cjk`,
  `lyrics_latin`, or `default_zh`

## Admin Submission Updates

Update every admin LRC submission call site to pass `song.title` and default to
`language="auto"` unless the user supplies a different `--lang` value.

Known call sites to cover:

- `sow-admin audio lrc` single submission.
- `sow-admin audio lrc --stdin` batch submission.
- helper `_submit_lrc_job`.
- batch/download workflows that currently hard-code `language="zh"`.
- lost-job resubmission paths that currently hard-code `language="zh"`.

Update `AnalysisClient.submit_lrc()` to accept `song_title: str = ""` and include it
in the payload.

## Pipeline Updates

In `JobQueue._process_lrc_job`:

1. Validate/request-normalize language.
2. Resolve language once after confirming the request is `LrcJobRequest`.
3. Use the resolved language for:
   - LRC result cache key.
   - YouTube transcript language preference and correction prompt.
   - Qwen3 ASR context.
   - Qwen3 ASR cache key language field.
   - Qwen3 ASR alignment prompt.
   - Whisper transcription cache key.
   - Whisper `language` parameter.
   - Whisper initial prompt.
   - Main LLM alignment prompt.

Avoid mutating persisted request data unless there is a deliberate reason. Prefer
passing `resolved_language` explicitly to functions that need it. If function
signatures become noisy, create a small internal context object for LRC runtime state.

## Prompt Updates

All prompt builders should branch on resolved language.

### Whisper Initial Prompt

Chinese:

- Preserve current Chinese worship wording and lyric-guided behavior.

English:

- Identify the audio as an English worship song.
- Include truncated official lyrics when available.
- Instruct Whisper via prompt context to preserve English words and phrasing.

### Main LLM Alignment Prompt

Chinese:

- Preserve the current behavior and Chinese examples.

English:

- Refer to English worship songs and official lyrics.
- Preserve English casing, punctuation, contractions, and line text exactly from
  canonical lyrics.
- Do not include Chinese-only example requirements in the English prompt.

### Qwen3 ASR Context

Chinese:

- Preserve the current Chinese context header.

English:

- Use an English context header for English worship songs.
- Include official English lyrics within the same character limit logic.

### Qwen3 ASR Alignment Prompt

Chinese:

- Preserve Chinese worship wording.

English:

- Refer to English worship songs and canonical English lyric lines.
- Preserve timestamps and repeated sung sections.
- Preserve official casing/punctuation.

### YouTube Transcript Correction Prompt

Chinese:

- Preserve current Chinese correction behavior.

English:

- Correct YouTube transcript lines against official English lyrics.
- Preserve transcript timecodes.
- Preserve repeated sung sections.
- Preserve casing/punctuation from official lyrics.
- Remove only lines that clearly do not correspond to sung lyrics.

## YouTube Transcript Language Selection

Make transcript fetching language-aware. The current Chinese-first preference is not
safe for English songs because a video can have translated Chinese captions while the
canonical lyrics are English.

For resolved `zh`:

- Prefer Chinese transcripts first, then English fallback if needed.

For resolved `en`:

- Prefer English transcripts first, then Chinese fallback only if no English transcript
  is available.

Apply the same preference order to both direct `fetch(video_id, languages=...)` and
the list fallback transcript ranking. Tests should cover videos with both Chinese and
English transcripts to ensure the resolved language controls the selected transcript.

## Cache Behavior

### LRC Result Cache

Version the LRC result cache key so new language-aware prompts never reuse old
Chinese-only LRC result cache entries.

Include at least:

- audio content hash
- lyrics hash
- resolved language
- LRC prompt/cache version
- source-relevant configuration if it can alter the final LRC

### R2 Object Key Risk

Do not let multiple language-specific cache entries point at a single mutable
`{hash_prefix}/lyrics.lrc` object if the same recording can be rerun under different
language modes. Otherwise, a later run can overwrite the object while older cache
entries still return the same URL.

Choose one of:

- Keep only one canonical LRC per recording and invalidate or overwrite all prior
  language-mode cache entries for that recording.
- Store language/cache-version-specific LRC objects, such as
  `{hash_prefix}/lyrics.{resolved_language}.vN.lrc`, and have the admin DB update point
  to the chosen result.

The second option is safer for debugging and reruns; the first option is simpler if
the product guarantees one active LRC per recording.

### Whisper Transcription Cache

Replace the audio-hash-only Whisper cache with a derived key that includes:

- audio content hash
- resolved transcription audio or stem kind
- resolved language
- Whisper model
- exact initial prompt version
- lyrics hash or exact prompt-input hash when lyrics are included in the initial prompt

Because Whisper currently uses lyrics in `initial_prompt`, do not use a cache entry
generated with different lyrics or a different prompt version.

### Qwen3 ASR Cache

Continue using rich Qwen3 ASR cache keys, but ensure they use:

- resolved language, not raw `auto`
- language-specific context
- context hash derived from the exact context sent to Qwen3

Avoid building the Qwen context separately in queue and worker with different inputs.
Either pass the computed context down or ensure both call the same deterministic helper
with the same resolved language.

## Runtime Safety Notes

- If language validation rejects a payload, fail fast with a 422 API response.
- If a direct API client sends `language="auto"` without `song_title`, fall back to
  lyrics detection and then default `zh`.
- If resolved language is `en` but lyrics contain mostly CJK, log a warning before
  processing. Do not fail the job.
- If resolved language is `zh` but lyrics contain mostly Latin, log a warning before
  processing. Do not fail the job.
- Keep force flags behavior unchanged:
  - `force` bypasses LRC result cache.
  - `force_whisper` bypasses Whisper transcription cache.
  - `force_qwen3_asr` bypasses Qwen3 ASR cache only.

## Test Plan

### Resolver Tests

- Explicit `zh` resolves `zh`.
- Explicit `en` resolves `en`.
- Chinese-only title resolves `zh`.
- English-only title resolves `en`.
- Mixed Chinese/English title resolves `zh`.
- Empty title with Chinese lyrics resolves `zh`.
- Empty title with English lyrics resolves `en`.
- Numeric or punctuation-only title falls back to lyrics.
- Empty or ambiguous title and lyrics defaults `zh`.
- Invalid language values are rejected.

### Admin Tests

- `sow-admin audio lrc` default payload includes `language="auto"` and `song_title`.
- Explicit `--lang zh` payload includes `language="zh"`.
- Explicit `--lang en` payload includes `language="en"`.
- Batch stdin submissions include `song_title`.
- Helper and batch/download LRC submissions no longer hard-code `zh` unless explicitly
  intended.
- Lost-job resubmission preserves `auto` behavior and includes `song_title`.

### Prompt Tests

- English Whisper prompt uses English worship wording.
- Chinese Whisper prompt preserves Chinese worship wording.
- English main alignment prompt preserves English casing/punctuation and has no
  Chinese-only example requirement.
- Chinese main alignment prompt keeps current Chinese behavior.
- Qwen3 context switches by resolved language.
- Qwen3 ASR alignment prompt switches by resolved language.
- YouTube correction prompt switches by resolved language.

### Pipeline Tests

- Queue resolves language once and logs requested/resolved/reason.
- Queue passes resolved `en` to YouTube, Qwen3 ASR, and Whisper paths.
- Queue passes resolved `zh` to YouTube, Qwen3 ASR, and Whisper paths.
- No lower-level worker function receives raw `auto`.
- Whisper transcription receives `language="en"` for English songs.
- Qwen3 cache keys differ for the same audio/lyrics under `zh` vs `en`.
- Whisper cache keys differ for the same audio/stem under `zh` vs `en`.
- LRC result cache keys differ for the same audio/lyrics under `zh` vs `en`.
- R2 object behavior is tested according to the chosen strategy:
  - language/version-specific keys do not overwrite each other, or
  - canonical overwrite invalidates stale language-specific cache entries.

### YouTube Transcript Tests

- Resolved `zh` prefers Chinese transcript over English when both exist.
- Resolved `en` prefers English transcript over Chinese when both exist.
- Resolved `en` falls back to Chinese transcript only when English is unavailable.
- Direct fetch language order and list fallback ranking both honor resolved language.

### Suggested Commands

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest \
  tests/services/analysis/test_lrc_worker.py \
  tests/services/analysis/test_qwen3_asr_pipeline.py \
  services/analysis/tests/test_youtube_transcript.py -v

PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest \
  tests/admin/test_audio_commands.py \
  tests/admin/test_audio_lrc_visibility.py -v
```

## Rollout Checklist

1. Add resolver and validation tests first.
2. Add `song_title` to API/admin request flow.
3. Resolve language once in queue and log the decision.
4. Thread resolved language through YouTube, Qwen3 ASR, Whisper, prompt builders, and
   cache key builders.
5. Fix cache keys and chosen R2 object strategy together.
6. Update admin hard-coded `zh` submission and resubmission paths.
7. Run targeted tests.
8. For the first production run, inspect logs for resolved-language decisions and any
   script-mismatch warnings.
