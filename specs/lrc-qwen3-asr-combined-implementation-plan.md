# LRC Qwen3 ASR Combined Implementation Plan

## Summary

Update production LRC generation so uncached or forced jobs use this priority order:

1. Final generated-LRC cache, unless `force=True`.
2. YouTube transcript plus existing LLM correction.
3. DashScope Qwen3 ASR, canonical-line snap, then LLM alignment.
4. Local Whisper transcription plus existing LLM alignment.

Qwen3 ASR is default-on for cache misses and forced LRC jobs when DashScope is configured.
DashScope failures, disabled configuration, malformed responses, empty responses, and too few
usable segments are non-fatal for the LRC job and fall through to Whisper. Qwen3 ForcedAligner
is removed from the automatic LRC pipeline, but the existing `services/qwen3/` service and
`qwen3_client.py` can remain for manual or future workflows.

All generated LRC still enters the existing Admin review workflow. Do not add quality holds,
warning statuses, or review UI changes in this implementation.

## Source Plan Decisions

Use the worker-priority plan as the baseline for queue ownership, public interface cleanup,
final cache behavior, Qwen ASR cache semantics, and result propagation. Fold in the redesign
plan's production-hardening ideas: retry, circuit breaker, DashScope concurrency limit,
dedicated Qwen ASR LLM prompt, and benchmark validation.

Important conflict resolutions:

- **DashScope integration**: use the official `dashscope` SDK, wrapped in executor-backed async
  calls. The repository POCs already validate `MultiModalConversation`, `QwenTranscription`,
  and `OssUtils` for Qwen3 ASR/filetrans. Direct HTTP can be revisited later only if SDK
  behavior blocks production.
- **Cache key**: use a rich Qwen ASR cache key, not audio-only. Qwen output depends on lyrics
  context, stem kind, model, region, language, context limit, and cache version.
- **Orchestration boundary**: keep final cache, YouTube attempt, audio download, stem resolution,
  Qwen/Whisper fallback, upload, and `lrc_source` assignment in `JobQueue._process_lrc_job()`.
  Keep `lrc.py` focused on producing LRC content for a selected input audio path.
- **Qwen audio input**: prefer dry/clean vocals, but allow Qwen ASR on raw vocals or full mix
  when no dry/clean stem exists. This preserves the intended Qwen-before-Whisper priority.
- **Snap to LLM handoff**: pass snapped timestamped phrases into LLM alignment. Do not compute
  snapped lines and then call LLM alignment with the raw ASR phrases.
- **Legacy ForcedAligner options**: reject legacy options for new submissions while preserving
  old persisted job JSON reconstruction.

## Public Interfaces

Extend `LrcOptions`:

```python
use_qwen3_asr: bool = True
force_qwen3_asr: bool = False
qwen3_asr_context_max_chars: int = 10000
qwen3_asr_snap_threshold: float = 0.60
qwen3_asr_min_usable_segments: int = 3
```

Deprecated fields:

- Keep `use_qwen3` and `max_qwen3_duration` loadable for old persisted job JSON if needed.
- Reject new API submissions containing `use_qwen3` or `max_qwen3_duration`.
- Replace Admin `--no-qwen3` with `--no-qwen3-asr`; reject the old CLI flag with an error that
  says Qwen3 ForcedAligner is no longer part of automatic LRC generation.

Add Admin/API options:

- `--no-qwen3-asr` / `use_qwen3_asr=False`
- `--force-qwen3-asr` / `force_qwen3_asr=True`

Extend `lrc_source` propagation:

- `youtube_transcript`
- `qwen3_asr`
- `whisper_asr`

Ensure this source survives queue result creation, final LRC cache save/load, FastAPI
`job_to_response()`, Admin client parsing, status polling, and Admin sync/update logic.

## Configuration And Dependencies

Add analysis-service settings:

```python
SOW_DASHSCOPE_API_KEY: str = ""
SOW_DASHSCOPE_ASR_REGION: str = "intl"  # intl, cn, us
SOW_DASHSCOPE_ASR_FLASH_MODEL: str = "qwen3-asr-flash"
SOW_DASHSCOPE_ASR_FILETRANS_MODEL: str = "qwen3-asr-flash-filetrans"
SOW_DASHSCOPE_ASR_CONTEXT_MAX_CHARS: int = 10000
SOW_DASHSCOPE_ASR_SNAP_THRESHOLD: float = 0.60
SOW_DASHSCOPE_ASR_TIMEOUT_SECONDS: int = 300
SOW_DASHSCOPE_ASR_FILETRANS_TIMEOUT_SECONDS: int = 1800
SOW_DASHSCOPE_ASR_MAX_CONCURRENT: int = 2
SOW_DASHSCOPE_ASR_CACHE_VERSION: int = 1
```

Region endpoint mapping:

```text
intl -> https://dashscope-intl.aliyuncs.com/api/v1
cn   -> https://dashscope.aliyuncs.com/api/v1
us   -> https://dashscope-us.aliyuncs.com/api/v1
```

Add service-local dependencies to `services/analysis/pyproject.toml`:

- `dashscope`
- `rapidfuzz`
- `zhconv`

Do not add `qwen-asr`; this production path uses cloud DashScope Qwen3 ASR only. Keep existing
`SOW_QWEN3_API_KEY` and `SOW_QWEN3_BASE_URL` for the old ForcedAligner service only.

Update Docker Compose, env examples, deployment docs, and startup logging for the new DashScope
settings. Do not imply the old `qwen3` service is required for LRC generation.

## Qwen3 ASR Client

Add `services/analysis/src/sow_analysis/services/qwen3_asr_client.py` with typed result
objects:

- `Qwen3AsrSegment(text, start, end)`
- `Qwen3AsrWord(text, start, end)`
- `Qwen3AsrResult(segments, words, text, raw_response, model, region, mode)`
- `Qwen3AsrError`
- `Qwen3AsrNonRetriableError`
- `Qwen3AsrTimeoutError`

Client behavior:

- Set `dashscope.base_http_api_url` from `SOW_DASHSCOPE_ASR_REGION`.
- Run all blocking SDK calls and result URL fetches in an executor.
- Direct flash uses `dashscope.MultiModalConversation.call()` with a local `file://` audio path,
  `result_format="message"`, and `asr_options={"enable_itn": False, "enable_words": True,
  "language": "zh"}`.
- Filetrans uploads with `dashscope.utils.oss_utils.OssUtils.upload()`, submits with
  `dashscope.audio.qwen_asr.QwenTranscription.async_call()`, polls until success/failure/timeout,
  and fetches `transcription_url` JSON.
- Use direct flash when audio is within direct limits. Use filetrans for long or large files.
  If duration cannot be measured cheaply, route by file size and allow one direct-to-filetrans
  fallback if the direct call rejects size or duration.
- Log direct-vs-filetrans routing reason and note that filetrans lacks system-message context.
- Retry transient 429, 5xx, timeout, and network errors with exponential backoff. Do not retry
  401/403/auth errors; trip an in-process circuit breaker so subsequent jobs skip Qwen ASR until
  worker restart.
- Enforce `SOW_DASHSCOPE_ASR_MAX_CONCURRENT` with a queue-owned semaphore. Do not acquire the
  local model semaphore for DashScope calls.
- Treat API errors, timeouts, malformed schema, empty results, and fewer than
  `qwen3_asr_min_usable_segments` as Qwen ASR failures.

## Canonical Snap And LLM Alignment

Add `services/analysis/src/sow_analysis/services/canonical_snap.py`, porting the production-safe
parts of the POC:

- Normalize Traditional/Simplified Chinese with `zhconv`.
- Use `rapidfuzz` fuzzy matching.
- Prefer word timestamps to reconstruct lyric-line-sized phrases.
- Fall back to sentence segments when word timestamps are missing or unusable.
- Preserve performed repeats; do not deduplicate canonical lyrics.
- Snap only when confidence meets `qwen3_asr_snap_threshold`.
- Keep ASR text for unresolved phrases.
- Provide diagnostic scoring only; do not gate the pipeline on quality score.

Add a Qwen-specific LLM prompt builder in `lrc.py`. It should tell the model:

- Qwen ASR phrases may already be snapped to canonical lyrics.
- Preserve timestamps; only fix text, assign/reorder canonical lines, and preserve repeated sung
  sections.
- Return the same JSON shape consumed by existing `_parse_llm_response()`.

Refactor `_llm_align()` to accept an optional prompt builder. The Whisper path keeps the current
prompt by default. The Qwen path must pass snapped `WhisperPhrase`-compatible phrases into this
alignment step.

Add `generate_lrc_from_qwen3_asr()` in `lrc.py`:

```python
async def generate_lrc_from_qwen3_asr(
    audio_path: Path,
    lyrics_text: str,
    options: LrcOptions,
    output_path: Path,
    cache_key: str,
    cache_manager: CacheManager,
    dashscope_semaphore: asyncio.Semaphore,
) -> tuple[Path, int, list[WhisperPhrase]]:
    ...
```

Behavior:

1. Build context from a short Chinese worship-song instruction plus canonical lyrics, capped by
   option/env context limit and truncated at line boundaries when possible.
2. Load Qwen ASR cache unless `force_qwen3_asr=True`.
3. Run Qwen ASR on the resolved transcription audio when cache misses.
4. Save raw Qwen ASR response to the Qwen cache.
5. Snap ASR result to canonical lyrics.
6. Convert snapped lines into `WhisperPhrase`-compatible phrases.
7. Run Qwen-specific LLM alignment on snapped phrases.
8. Write final LRC with existing `_write_lrc()`.
9. Return the snapped/ASR phrases for local processing only; never save them to the Whisper cache.

## Queue And Cache Changes

Add Qwen ASR cache methods to `CacheManager`:

```python
get_qwen3_asr_transcription(cache_key: str) -> Optional[dict]
save_qwen3_asr_transcription(cache_key: str, payload: dict) -> Path
```

Cache key fields:

- `content_hash`
- `lyrics_hash`
- `stem_kind`
- `model`
- `region`
- `language`
- `context_max_chars`
- `context_hash`
- `cache_version`

Final LRC cache behavior:

- `force=True` bypasses only the final LRC cache.
- Cache hit must restore `lrc_source` if present. Existing cache entries without source can return
  `lrc_source=None`.
- New cache saves include `lrc_source`.

Refactor transcription audio resolution into a queue helper:

```python
@dataclass
class ResolvedTranscriptionAudio:
    path: Path
    r2_url: Optional[str]
    stem_kind: str  # vocals_dry, vocals_clean, vocals, full_mix
    is_dry_or_clean_vocals: bool

async def _resolve_lrc_transcription_audio(...) -> ResolvedTranscriptionAudio:
    ...
```

Resolution behavior:

- Prefer existing `vocals_dry.flac`, then legacy clean vocals, then raw vocals.
- If no stem exists and `use_vocals_stem=True`, submit the existing `STEM_SEPARATION` child job
  and wait with the current 2-hour timeout.
- If child job completes, prefer dry/clean output; otherwise accept raw vocals if available.
- If stem resolution fails or times out, use full mix.
- Use the same resolved audio for Qwen ASR and Whisper fallback to avoid duplicate prep.

Rewire `_process_lrc_job()`:

1. Persist `processing/starting`.
2. Check final LRC cache unless `force=True`.
3. Try YouTube transcript before downloading audio.
4. On YouTube success, upload/cache with `lrc_source="youtube_transcript"` and stop.
5. Download audio and resolve shared transcription audio.
6. If `use_qwen3_asr=True` and DashScope is configured, try Qwen ASR using the resolved audio.
7. On Qwen success, upload/cache with `lrc_source="qwen3_asr"` and stop.
8. On Qwen skip/failure, update stage to `falling_back_to_whisper`.
9. Run existing Whisper path with existing Whisper cache semantics.
10. Save Whisper transcription cache only when Whisper actually ran.
11. Upload/cache with `lrc_source="whisper_asr"`.

Add stages:

- `resolving_transcription_audio`
- `qwen3_asr_cached`
- `qwen3_asr_transcribing`
- `qwen3_asr_snapping`
- `qwen3_asr_llm_aligning`
- `qwen3_asr_done`
- `falling_back_to_whisper`

Remove automatic Qwen ForcedAligner behavior:

- Remove the automatic refinement block from `generate_lrc()`.
- Remove `Qwen3Client`/`OutputFormat` imports from `workers/lrc.py` if no remaining code in that
  module uses them.
- Remove or leave `_qwen3_refine()` and `_parse_qwen3_lrc()` based on actual remaining imports;
  do not delete `services/qwen3/`.

## API And Admin Updates

FastAPI:

- Reject legacy submit payload options at `/jobs/lrc`.
- Add new options to accepted request model.
- Include `lrc_source` in `job_to_response()`.

Admin client and commands:

- Add `use_qwen3_asr` and `force_qwen3_asr` parameters to `AnalysisClient.submit_lrc()`.
- Replace all internal `use_qwen3=True` submissions with `use_qwen3_asr=True`.
- Add `--no-qwen3-asr` and `--force-qwen3-asr` to single and batch LRC commands.
- Update help text and summary counters so Qwen ASR is reported separately from Whisper ASR.
- Preserve existing generated/reconciled LRC `visibility_status="review"` behavior.

## Tests

Qwen ASR client tests:

- Parse direct flash sentence segments.
- Parse direct flash word timestamps.
- Parse filetrans result by fetching `transcription_url`.
- Fallback from words to sentence segments when words are unavailable.
- Route direct vs filetrans by duration/size.
- Retry transient errors.
- Circuit-break on auth errors.
- Timeout filetrans polling.
- Raise typed errors on API error, malformed schema, empty result, and too few usable segments.

Canonical snap and LLM input tests:

- Traditional Chinese exact matches snap correctly.
- Simplified ASR text snaps to Traditional canonical lyrics.
- Repeated chorus lines remain repeated.
- Short fragments do not snap to unrelated lyrics.
- Unresolved phrases keep ASR text.
- Qwen success invokes LLM alignment with snapped phrases, not raw ASR phrases.

Queue orchestration tests:

- Final LRC cache hit skips all generation.
- Final LRC cache hit returns cached `lrc_source` when present.
- `force=True` bypasses final LRC cache and can invoke Qwen ASR.
- YouTube success skips audio download, stem separation, Qwen ASR, and Whisper.
- Existing dry/clean vocals run Qwen ASR.
- Missing stems submit `STEM_SEPARATION`.
- Missing DashScope key still resolves audio for Whisper and skips Qwen.
- Qwen hard failure falls back to Whisper.
- Qwen success sets `lrc_source="qwen3_asr"` and does not run Whisper.
- Qwen phrases are not saved to the Whisper cache.
- `force_qwen3_asr=True` bypasses only Qwen ASR cache.

API/Admin tests:

- `submit_lrc()` sends `use_qwen3_asr` and `force_qwen3_asr`.
- `--no-qwen3-asr` disables Qwen ASR.
- `--force-qwen3-asr` bypasses only Qwen ASR cache.
- Deprecated `--no-qwen3`, `use_qwen3`, and `max_qwen3_duration` are rejected for new
  submissions.
- Old persisted job JSON with legacy options can still be reconstructed.
- `lrc_source="qwen3_asr"` is visible through API response and Admin polling.
- Generated LRC sync keeps existing `visibility_status="review"`.

Regression checks:

- Existing YouTube jobs still complete without audio download.
- Existing Whisper-only behavior works with `use_qwen3_asr=False`.
- Final LRC cache still includes lyrics hash and is bypassed by `force=True`.
- Whisper transcription cache remains separate from Qwen ASR cache.
- DashScope calls do not acquire the local model semaphore.
- Local model semaphore still protects Whisper and local stem fallback.
- Existing ForcedAligner automatic tests are removed or rewritten for the new Qwen ASR path.

Recommended command after implementation:

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ \
  --ignore=tests/services/analysis \
  --ignore=services/qwen3/tests \
  --ignore=services/analysis/tests -v
```

Also run the relevant analysis-service tests from `services/analysis/` with its package
environment, including the new Qwen ASR client, snap, cache, and queue tests.

## Acceptance Criteria

- Uncached or forced LRC jobs use YouTube -> Qwen3 ASR -> Whisper.
- Cached final LRC jobs still return immediately unless `force=True`.
- Qwen ASR is default-on when DashScope is configured.
- Qwen ASR output is snapped and then always passed through LLM alignment.
- Snapped phrases, not raw ASR phrases, are what the Qwen LLM prompt sees.
- Qwen ForcedAligner is no longer called automatically by the LRC worker.
- Jobs can complete with `lrc_source="qwen3_asr"`, visible through API and Admin tooling.
- Whisper fallback remains functional.
- Qwen ASR cache and Whisper transcription cache are separate.
- Admin CLI can disable Qwen ASR or force only the Qwen ASR cache.
- Legacy forced-alignment options are rejected for new submissions.
- Existing generated-LRC review visibility behavior remains unchanged.

## Out Of Scope

- Removing the YouTube transcript path.
- Removing the Whisper fallback.
- Removing the `services/qwen3/` ForcedAligner service tree.
- Adding a new LRC review or quality warning workflow.
- Changing webapp or user app LRC consumption.
- Local Qwen ASR fallback.
- Chunking long songs to force direct flash context biasing.
