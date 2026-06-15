# LRC Pipeline English/Chinese Title-Based Language Detection

## Summary

Enhance LRC generation so English songs are transcribed and aligned with English-aware prompts, while Chinese songs keep the existing Chinese behavior. The analysis service will infer `zh` vs `en` from `song_title`, fall back to lyric script when the title is ambiguous, and allow explicit `--lang zh|en` overrides.

## Key Changes

- Add `song_title: str = ""` to `LrcJobRequest`.
- Change LRC language option semantics to support `auto`, `zh`, and `en`; make `auto` the default for new admin submissions.
- Add a shared resolver:
  - Explicit `zh`/`en` wins.
  - `auto` detects CJK vs Latin script from `song_title`.
  - Mixed, empty, numeric, or punctuation-only titles fall back to `lyrics_text`.
  - If still ambiguous, default to `zh` for backward compatibility.
- Update admin LRC submission call sites to pass `song.title` and use `language="auto"` unless the user supplies `--lang`.
- Preserve compatibility for old persisted jobs and direct API clients by allowing missing `song_title` and existing `language="zh"` payloads.

## Pipeline Updates

- Resolve language once in `JobQueue._process_lrc_job`, then pass the resolved `zh`/`en` value into all LRC paths.
- Update all language-specific prompt builders:
  - Whisper initial prompt in `_run_whisper_transcription`
  - Main LLM alignment prompt in `_build_alignment_prompt`
  - Qwen3 ASR context in `_build_qwen3_context`
  - Qwen3 ASR alignment prompt in `_build_qwen3_asr_alignment_prompt`
  - YouTube transcript correction prompt in `youtube_transcript.py`
- For English prompts, refer to English worship songs, English official lyrics, and preserve English casing/punctuation from canonical lyrics.
- For Chinese prompts, keep current Chinese-focused behavior and examples.
- Use resolved language for Whisper's `language` parameter and Qwen3 ASR cache language field.

## Cache Behavior

- Version the LRC result cache key so language-aware prompt changes do not reuse old Chinese-only results.
- Include `resolved_language` in the LRC cache key.
- Replace Whisper transcription cache usage with a derived key including:
  - audio content hash
  - resolved transcription audio/stem kind
  - resolved language
  - lyrics hash or prompt version
- Continue using Qwen3 ASR cache keys, but ensure they use the resolved language and language-specific context.

## Test Plan

- Add unit tests for language detection:
  - Chinese title resolves `zh`
  - English title resolves `en`
  - mixed/ambiguous title falls back to lyrics
  - empty title with English lyrics resolves `en`
  - empty/ambiguous everything defaults `zh`
  - explicit `zh` or `en` overrides detection
- Add prompt tests:
  - English alignment prompt contains English-specific wording and no Chinese-only example requirement.
  - Chinese alignment prompt keeps Chinese behavior.
  - Qwen3 context and YouTube correction prompts switch by language.
- Add pipeline tests:
  - Admin `submit_lrc` payload includes `song_title`.
  - Queue passes resolved language to YouTube, Qwen3 ASR, and Whisper paths.
  - Whisper transcription receives `language="en"` for English songs.
  - Cache keys differ for the same audio/lyrics under `zh` vs `en`.
- Run:
  - `PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/services/analysis/test_lrc_worker.py tests/services/analysis/test_qwen3_asr_pipeline.py tests/services/analysis/test_youtube_transcript.py -v`
  - targeted admin tests covering LRC submission payloads.

## Assumptions

- Only Chinese and English are supported for this feature.
- Title-based detection is the default product behavior; manual `--lang zh|en` remains available for exceptions such as pinyin Chinese titles.
- Existing queued jobs without `song_title` remain valid and fall back to lyrics/default behavior.
