# LRC Worker Plan V2: YouTube First, Qwen3 ASR Second, Whisper Fallback

## Summary

Update production LRC generation to use this priority order for uncached or forced jobs:

1. YouTube transcript plus LLM correction.
2. Qwen3 ASR on dry or clean vocals, followed by canonical snap and LLM alignment.
3. Local Whisper on the best available transcription audio.

Keep the current final LRC cache behavior: cached LRC results return immediately unless
`force=True` / `--force` is used. Qwen3 ASR is default-on for cache misses and forced LRC
jobs. Qwen3 ForcedAligner refinement is removed from the LRC worker.

All system-generated LRC continues to enter the existing Admin review workflow. No special
low-confidence warning, hold, or quality-status behavior is in scope for this version.

## Public Interfaces

Add Qwen3 ASR options to `LrcOptions`:

```python
use_qwen3_asr: bool = True
force_qwen3_asr: bool = False
qwen3_asr_context_max_chars: int = 10000
qwen3_asr_snap_threshold: float = 0.60
qwen3_asr_min_usable_segments: int = 3
```

Reject legacy forced-alignment options at submit boundaries:

- Admin CLI rejects deprecated `--no-qwen3`.
- Analysis API rejects request payloads containing `use_qwen3` or `max_qwen3_duration`.
- Error text should explain that Qwen3 ForcedAligner refinement was removed and callers
  should use `use_qwen3_asr` / `--no-qwen3-asr` instead.
- Keep model/job-store compatibility for old persisted job JSON. Do not make Pydantic model
  validation reject old DB rows during job reconstruction.

Extend result propagation so `lrc_source` survives queue result, API response, Admin client
parsing, and status sync:

- `youtube_transcript`
- `qwen3_asr`
- `whisper_asr`

Add Admin CLI/API options:

- `--no-qwen3-asr` / `use_qwen3_asr=False`
- `--force-qwen3-asr` / `force_qwen3_asr=True`

Keep existing generated-LRC visibility behavior: Admin sync sets generated or reconciled LRC
to `visibility_status="review"` as it does today.

## Configuration And Dependencies

Add analysis-service settings:

```python
SOW_QWEN3_ASR_API_KEY: str = ""
SOW_QWEN3_ASR_REGION: str = "intl"  # intl, cn, us
SOW_QWEN3_ASR_FLASH_MODEL: str = "qwen3-asr-flash"
SOW_QWEN3_ASR_FILETRANS_MODEL: str = "qwen3-asr-flash-filetrans"
SOW_QWEN3_ASR_CONTEXT_MAX_CHARS: int = 10000
SOW_QWEN3_ASR_SNAP_THRESHOLD: float = 0.60
SOW_QWEN3_ASR_TIMEOUT_SECONDS: int = 300
SOW_QWEN3_ASR_FILETRANS_TIMEOUT_SECONDS: int = 1800
SOW_QWEN3_ASR_MAX_CONCURRENT: int = 2
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

Do not add `qwen-asr`. This production path uses cloud DashScope Qwen3 ASR only.

Update Docker Compose, env examples, deployment docs, and startup logging for the new Qwen3
ASR settings. Since the LRC worker no longer needs the forced-aligner service, update docs so
the old `qwen3` service is not implied as required for LRC generation.

## Implementation Changes

Add `services/analysis/src/sow_analysis/services/qwen3_asr_client.py` with typed result
objects for segments, words, model, region, mode, and raw response.

Client behavior:

- Set `dashscope.base_http_api_url` from `SOW_QWEN3_ASR_REGION`.
- Direct flash uses `dashscope.MultiModalConversation.call()` with local audio file,
  `result_format="message"`, and `asr_options={"enable_itn": False, "enable_words": True,
  "language": "zh"}`.
- Filetrans uploads with `dashscope.utils.oss_utils.OssUtils.upload()`, submits with
  `dashscope.audio.qwen_asr.QwenTranscription.async_call()`, polls with explicit timeout,
  and fetches `transcription_url` JSON.
- Blocking SDK and HTTP calls run in an executor.
- Log whether direct flash or filetrans is used and why.
- Treat API errors, timeouts, malformed schema, empty results, and too few usable segments
  as hard Qwen3 ASR failures so the queue can fall back to Whisper.

Model routing:

- Default to automatic routing.
- Use direct flash when the file is within direct model limits.
- Use filetrans when duration or file size exceeds direct limits.
- If duration cannot be measured cheaply, choose by file size first and allow direct flash
  to fail into filetrans once.
- Filetrans is allowed in production, but logs must make clear that lyrics-context behavior
  differs from direct flash.

Add a separate Qwen3 ASR cache namespace to `CacheManager`:

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

`--force` bypasses only the final LRC cache. `--force-qwen3-asr` bypasses the Qwen3 ASR
cache and re-calls DashScope.

Refactor LRC transcription audio resolution into a shared helper:

```python
@dataclass
class ResolvedTranscriptionAudio:
    path: Path
    r2_url: Optional[str]
    stem_kind: str  # full_mix, vocals_dry, vocals_clean, vocals
    is_dry_vocals: bool

async def _resolve_lrc_transcription_audio(
    request: LrcJobRequest,
    temp_path: Path,
    allow_raw_vocals: bool,
) -> ResolvedTranscriptionAudio:
    ...
```

Shared resolution behavior:

- Prefer `vocals_dry.flac`, then legacy `vocals_clean.flac`.
- If missing and `use_vocals_stem=True`, submit the existing `STEM_SEPARATION` child job
  and wait with the current 2-hour timeout.
- Dry vocal stem generation is shared prep for Qwen3 ASR and local Whisper transcription.
  It is still useful when Qwen3 ASR cannot run.
- For Qwen3 ASR, accept only dry/clean vocals.
- For Whisper, preserve current fallback behavior: dry/clean vocals first, raw vocals if
  available, full mix last.

Add Qwen3 ASR LRC generation in `workers/lrc.py`:

```python
async def generate_lrc_from_qwen3_asr(
    audio_path: Path,
    lyrics_text: str,
    options: LrcOptions,
    output_path: Path,
    qwen3_result: Optional[Qwen3AsrResult] = None,
) -> tuple[Path, int, list[WhisperPhrase]]:
    ...
```

Processing behavior:

- Build direct-flash context from a short Chinese worship-song instruction plus canonical
  lyrics, capped by option/env context limit and truncated at line boundaries when possible.
- Prefer word timestamps when present; otherwise use sentence timestamps.
- Normalize ASR and canonical lyrics with `zhconv`.
- Snap Qwen3 phrases to canonical lyrics where confidence is sufficient.
- Preserve performed repeats; do not deduplicate canonical lyrics.
- Unresolved phrases keep ASR text.
- Always run LLM alignment after the snap step, using snapped Qwen3 timestamped phrases as
  the transcription input.
- Write final LRC with existing `_write_lrc()`.
- Return `WhisperPhrase`-compatible phrases only for local processing. Do not save these
  phrases into the Whisper transcription cache.

Rewire `_process_lrc_job()`:

1. Check final LRC cache first unless `force=True`.
2. Try YouTube transcript first.
3. If YouTube succeeds, upload/cache with `lrc_source="youtube_transcript"` and stop.
4. If YouTube fails or is absent, download source audio and resolve shared transcription audio.
5. If Qwen3 ASR is enabled, DashScope is configured, and dry/clean vocals are available:
   - Check Qwen3 ASR cache unless `force_qwen3_asr=True`.
   - Run or load Qwen3 ASR.
   - Snap phrases and run LLM alignment.
   - Upload/cache with `lrc_source="qwen3_asr"` and stop.
6. If Qwen3 ASR is skipped or fails hard, run existing Whisper plus LLM path with the
   resolved transcription audio.
7. Save Whisper transcription cache only when Whisper actually ran.
8. Upload/cache with `lrc_source="whisper_asr"`.

Add stages:

- `resolving_transcription_audio`
- `qwen3_asr_cached`
- `qwen3_asr_transcribing`
- `qwen3_asr_snapping`
- `qwen3_asr_llm_aligning`
- `qwen3_asr_done`
- `falling_back_to_whisper`

Remove Qwen3 ForcedAligner integration from the LRC worker:

- Remove `Qwen3Client` and `OutputFormat` imports from `workers/lrc.py`.
- Remove `_qwen3_refine()`, `_parse_qwen3_lrc()`, and duration gating used only for
  forced alignment.
- Remove analysis-service `qwen3_client.py` exports if no imports remain.
- Do not delete the separate `services/qwen3/` service tree in this change.

## Testing Plan

Qwen3 ASR client tests:

- Parses direct flash sentence segments.
- Parses direct flash word timestamps.
- Parses filetrans response by fetching `transcription_url`.
- Handles missing words by using sentence segments.
- Logs direct-vs-filetrans routing reason.
- Raises typed errors on API error, timeout, malformed schema, empty result, and too few
  usable segments.
- Chooses filetrans for long or large audio.

Canonical snap and LLM-input tests:

- Exact Traditional Chinese canonical lines snap correctly.
- Simplified ASR text snaps to Traditional canonical text.
- Repeated chorus lines are preserved as repeated performed lines.
- Short fragments do not over-snap to unrelated lyrics.
- Unresolved phrases keep ASR text.
- Qwen3 success always invokes LLM alignment with snapped timestamped phrases.

Queue orchestration tests:

- Final LRC cache hit skips all generation.
- `force=True` bypasses final LRC cache and can invoke Qwen3 ASR.
- YouTube success skips audio download, stem separation, Qwen3 ASR, and Whisper.
- Existing dry/clean vocals run Qwen3 ASR.
- Missing dry/clean vocals submit `STEM_SEPARATION`.
- No DashScope key still resolves stems for Whisper, then skips Qwen3.
- Child job returning only raw vocals skips Qwen3 and runs Whisper on raw vocals.
- Qwen3 hard failure falls back to Whisper.
- Qwen3 success sets `lrc_source="qwen3_asr"` and does not run Whisper.
- Qwen3 phrases are not saved to the Whisper cache.
- `force_qwen3_asr=True` bypasses only Qwen3 ASR cache.

Admin/API tests:

- Admin `submit_lrc()` sends `use_qwen3_asr` and `force_qwen3_asr`.
- `--no-qwen3-asr` sets `use_qwen3_asr=False`.
- `--force-qwen3-asr` sets `force_qwen3_asr=True`.
- Deprecated `--no-qwen3`, `use_qwen3`, and `max_qwen3_duration` are rejected at submit
  boundaries.
- Old persisted job JSON with legacy options can still be reconstructed.
- `lrc_source="qwen3_asr"` is visible through API response and Admin client polling.
- Generated LRC sync keeps existing `visibility_status="review"` behavior.

Regression checks:

- Existing YouTube transcript jobs still complete without downloading audio.
- Existing Whisper-only behavior works with `use_qwen3_asr=False`.
- Final LRC cache still includes lyrics hash and is bypassed by `--force`.
- Whisper transcription cache remains separate from Qwen3 ASR cache.
- DashScope calls do not acquire the local model semaphore.
- Local model semaphore still protects Whisper and local stem fallback.
- MVSEP daily limits and local fallback remain controlled by the stem worker.

## Acceptance Criteria

- For uncached or forced jobs, worker priority is YouTube -> Qwen3 ASR -> Whisper.
- Cached final LRC results still return immediately unless `--force` is used.
- Qwen3 ASR runs only on dry/clean vocal stems.
- Dry vocal stem generation remains shared prep for Qwen3 ASR and Whisper.
- Qwen3 ASR output is snapped, then always passed through LLM alignment.
- Qwen3 ForcedAligner is no longer called by the LRC worker.
- Jobs can complete with `lrc_source="qwen3_asr"`, visible through API and Admin polling.
- Whisper fallback remains functional.
- Admin CLI can disable Qwen3 ASR or force only the Qwen3 ASR cache.
- Deprecated forced-alignment options are rejected for new submissions.
- Existing generated-LRC review visibility behavior remains unchanged.

