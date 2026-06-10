# LRC Worker Plan: YouTube First, Qwen3 ASR Second, Whisper Fallback

## Summary

Update the production LRC generation pipeline so the worker uses this priority order:

1. YouTube transcript remains the preferred path.
2. Qwen3 ASR through Alibaba DashScope becomes the second transcription path, but only after obtaining clean dry vocals.
3. Local Whisper remains the final fallback.

Remove the Qwen3 ForcedAligner refinement step from the LRC worker. The current forced-alignment output is not directly usable enough for production automation; manual timing cleanup should happen in the Admin Lyrics Editor instead.

This plan intentionally reuses existing production infrastructure where possible:

- The existing YouTube transcript worker and LLM correction remain the primary path.
- The existing `STEM_SEPARATION` child job remains responsible for producing vocals.
- The existing stem worker already tries MVSEP first and falls back to local `audio-separator`; LRC should rely on that behavior instead of adding a separate MVSEP path.
- The existing Whisper + LLM alignment path remains as the safety net.

## Current State

The current LRC worker has these behaviors:

- `docs/lrc-job-flow.md` documents YouTube transcript as primary, Whisper as fallback, and Qwen3 ForcedAligner as optional post-processing.
- `services/analysis/src/sow_analysis/workers/lrc.py` implements:
  - YouTube transcript generation through `try_youtube_transcript_lrc()`.
  - Whisper transcription through `_run_whisper_transcription()`.
  - LLM lyric alignment through `_llm_align()`.
  - Optional Qwen3 forced-alignment refinement through `_qwen3_refine()`.
- `services/analysis/src/sow_analysis/workers/queue.py` currently owns the higher-level LRC orchestration:
  - It checks the LRC cache.
  - It tries YouTube first.
  - If YouTube fails, it downloads audio.
  - It resolves `vocals_dry.flac` from R2 or submits a child `STEM_SEPARATION` job.
  - It waits up to 2 hours for the child stem job, then falls back to full mix if needed.
  - It calls `generate_lrc()` for Whisper + LLM alignment.
- `services/analysis/src/sow_analysis/workers/stem_separation.py` already implements the desired stem priority:
  - Cached R2 stems first.
  - MVSEP cloud separation when configured and available.
  - Local audio-separator fallback when MVSEP is unavailable, times out, or fails.

The POC script `poc/gen_lrc_qwen3_asr_mvsep_force_align_v2.py` proves useful pieces:

- DashScope Qwen3 ASR request construction.
- Flash vs filetrans model handling.
- Response parsing for sentence and word timestamps.
- Lyrics context biasing.
- Canonical lyric snapping ideas.
- MVSEP-derived clean vocal preference.

The POC forced-alignment portion should not be promoted into the worker.

## Target Pipeline

```text
LRC job starts
  |
  |-- cache hit?
  |     `-- yes: return cached LRC
  |
  |-- YouTube URL present?
  |     |
  |     |-- yes: try YouTube transcript + LLM correction
  |     |      |-- success: upload/cache LRC, source=youtube_transcript
  |     |      `-- failure: continue
  |     |
  |     `-- no: continue
  |
  |-- download source audio from R2
  |
  |-- use_vocals_stem?
  |     |
  |     |-- yes: require clean dry vocals for Qwen3 ASR
  |     |      |-- existing vocals_dry/vocals_clean found: download stem
  |     |      |-- missing: submit STEM_SEPARATION child job
  |     |      |       |-- child uses MVSEP first, local fallback internally
  |     |      |       |-- wait existing max 2 hours
  |     |      |-- dry vocals available: continue to Qwen3 ASR
  |     |      `-- dry vocals unavailable: skip Qwen3 ASR
  |     |
  |     `-- no: skip Qwen3 ASR and continue to Whisper
  |
  |-- Qwen3 ASR path
  |     |
  |     |-- call DashScope Qwen3 ASR with lyrics context
  |     |-- reconstruct performed lyric lines from ASR timestamps
  |     |-- snap ASR text to canonical lyrics where confidence is good
  |     |-- low confidence but usable: complete with warning
  |     |-- success: upload/cache LRC, source=qwen3_asr
  |     `-- hard failure/no usable segments: continue to Whisper
  |
  `-- Whisper fallback
        |
        |-- use cached Whisper phrases when valid
        |-- otherwise run local faster-whisper
        |-- align official lyrics with LLM
        `-- upload/cache LRC, source=whisper_asr
```

## Public Interface Changes

### LrcOptions

Update `services/analysis/src/sow_analysis/models.py`.

Keep existing fields for backward compatibility:

```python
whisper_model: str = "large-v3"
llm_model: str = ""
use_vocals_stem: bool = True
language: str = "zh"
force: bool = False
force_whisper: bool = False
```

Add Qwen3 ASR fields:

```python
use_qwen3_asr: bool = True
force_qwen3_asr: bool = False
qwen3_asr_context_max_chars: int = 10000
qwen3_asr_snap_threshold: float = 0.60
qwen3_asr_min_usable_segments: int = 3
```

Deprecate forced-alignment fields:

```python
use_qwen3: bool = False
max_qwen3_duration: int = 300
```

Implementation details:

- Accept `use_qwen3` and `max_qwen3_duration` for API compatibility during one transition release.
- Do not use either field to call Qwen3 ForcedAligner.
- `use_qwen3=False` should not disable Qwen3 ASR. It only represents the old forced-alignment flag.
- Add comments/docstrings marking both as deprecated no-op forced-alignment options.

### JobResult

Extend `lrc_source` values:

- `youtube_transcript`
- `qwen3_asr`
- `whisper_asr`

Use `job.warning` for non-fatal Qwen3 ASR quality warnings, such as low canonical snap confidence.

### Admin CLI/API Client

Update `src/stream_of_worship/admin/services/analysis.py`:

- Add `use_qwen3_asr: bool = True`.
- Add `force_qwen3_asr: bool = False`.
- Continue accepting `use_qwen3`, but document it as deprecated and not used for timestamp refinement anymore.

Update `src/stream_of_worship/admin/commands/audio.py`:

- Add `--no-qwen3-asr` to disable the second-priority Qwen3 ASR path.
- Add `--force-qwen3-asr` to bypass the Qwen3 ASR cache.
- Keep existing `--no-qwen3` temporarily, but update help text to say forced alignment has been removed and this option is deprecated.

## Configuration

Add settings to `services/analysis/src/sow_analysis/config.py`:

```python
# DashScope Qwen3 ASR
SOW_QWEN3_ASR_API_KEY: str = ""
SOW_QWEN3_ASR_REGION: str = "intl"  # intl, cn, us
SOW_QWEN3_ASR_FLASH_MODEL: str = "qwen3-asr-flash"
SOW_QWEN3_ASR_FILETRANS_MODEL: str = "qwen3-asr-flash-filetrans"
SOW_QWEN3_ASR_CONTEXT_MAX_CHARS: int = 10000
SOW_QWEN3_ASR_SNAP_THRESHOLD: float = 0.60
SOW_QWEN3_ASR_TIMEOUT_SECONDS: int = 300
SOW_QWEN3_ASR_FILETRANS_TIMEOUT_SECONDS: int = 1800
```

Region endpoint mapping:

```python
intl -> https://dashscope-intl.aliyuncs.com/api/v1
cn   -> https://dashscope.aliyuncs.com/api/v1
us   -> https://dashscope-us.aliyuncs.com/api/v1
```

Add service-local dependencies to `services/analysis/pyproject.toml` using `uv add --project services/analysis`:

- `dashscope`
- `rapidfuzz`
- `zhconv`

Do not add `qwen-asr` for this plan. The production change uses cloud Qwen3 ASR only and removes forced alignment.

## Implementation Changes

### 1. Add DashScope Qwen3 ASR Client

Create `services/analysis/src/sow_analysis/services/qwen3_asr_client.py`.

Types:

```python
@dataclass
class Qwen3AsrSegment:
    text: str
    start: float
    end: float

@dataclass
class Qwen3AsrWord:
    text: str
    start: float
    end: float

@dataclass
class Qwen3AsrResult:
    segments: list[Qwen3AsrSegment]
    words: list[Qwen3AsrWord]
    model: str
    region: str
    raw: dict
```

Client behavior:

- Set `dashscope.base_http_api_url` from `SOW_QWEN3_ASR_REGION`.
- For direct flash calls, use `dashscope.MultiModalConversation.call()` with:
  - `messages` containing optional system context and local audio file.
  - `result_format="message"`.
  - `asr_options={"enable_itn": False, "enable_words": True, "language": "zh"}`.
- For filetrans calls:
  - Upload via `dashscope.utils.oss_utils.OssUtils.upload()`.
  - Submit via `dashscope.audio.qwen_asr.QwenTranscription.async_call()`.
  - Poll with `QwenTranscription.wait()`.
  - Fetch `transcription_url` JSON when present.
- Run blocking SDK calls through `asyncio.get_running_loop().run_in_executor(None, ...)`.
- Raise a specific `Qwen3AsrError` on hard failure.
- Return empty segments only as a hard failure to the worker.

Model routing:

- Default to auto routing.
- Use `qwen3-asr-flash` when the file is within direct model limits.
- Use `qwen3-asr-flash-filetrans` when duration or file size exceeds direct limits.
- If duration cannot be measured cheaply, choose by file size first and allow the direct call to fail into filetrans once.

### 2. Add ASR Cache Namespace

Extend `services/analysis/src/sow_analysis/storage/cache.py`.

Add:

```python
get_qwen3_asr_transcription(cache_key: str) -> Optional[dict]
save_qwen3_asr_transcription(cache_key: str, payload: dict) -> None
```

Cache key fields:

- `content_hash`
- `lyrics_hash`
- `stem_kind` (`vocals_dry`, `vocals_clean`)
- `model`
- `region`
- `language`
- `context_max_chars`
- `context_hash`
- `cache_version`

Do not share this cache with Whisper. Whisper remains keyed by audio hash only because its cached output predates lyrics-context ASR. Qwen3 ASR must include lyrics/context in the cache key because context can change transcription output.

### 3. Extract Stem Resolution Helper

Refactor duplicated stem handling in `services/analysis/src/sow_analysis/workers/queue.py`.

Add helper inside `JobQueue` or a small worker-local function:

```python
async def _resolve_lrc_transcription_audio(
    request: LrcJobRequest,
    temp_path: Path,
    require_dry_vocals: bool,
) -> ResolvedTranscriptionAudio:
    ...
```

Return:

```python
@dataclass
class ResolvedTranscriptionAudio:
    path: Path
    r2_url: Optional[str]
    stem_kind: str  # full_mix, vocals_dry, vocals_clean, vocals
    is_dry_vocals: bool
    warning: Optional[str] = None
```

Behavior for Qwen3:

- `require_dry_vocals=True`.
- Check R2 for `vocals_dry.flac`, then legacy `vocals_clean.flac`.
- If missing, submit child `STEM_SEPARATION` and wait with the existing 2-hour timeout.
- Accept child result only when `vocals_dry_url` or legacy dry/clean URL exists.
- Do not run Qwen3 ASR on raw `vocals` or full mix.
- If no dry vocals are available, return a warning and let the queue continue to Whisper.

Behavior for Whisper:

- Preserve current behavior.
- Prefer dry vocals when available.
- Fall back to raw vocals or full mix as currently implemented.

### 4. Add Qwen3 ASR LRC Generation

In `services/analysis/src/sow_analysis/workers/lrc.py`, add:

```python
async def generate_lrc_from_qwen3_asr(
    audio_path: Path,
    lyrics_text: str,
    options: LrcOptions,
    output_path: Path,
    content_hash: str,
    stem_kind: str,
) -> tuple[Path, int, list[WhisperPhrase], Optional[str]]:
    ...
```

Return `WhisperPhrase`-compatible phrases so downstream result/caching/test utilities can reuse existing structures.

Context construction:

- Start with a short Chinese worship-song instruction.
- Include canonical lyrics exactly as provided.
- Cap context by `options.qwen3_asr_context_max_chars` or `SOW_QWEN3_ASR_CONTEXT_MAX_CHARS`.
- Truncate at line boundaries when possible.

Segment processing:

- Prefer word timestamps when present.
- Reconstruct LRC lines from ASR words using the POC's useful approach:
  - Normalize ASR and canonical lyrics with `zhconv`.
  - Walk through ASR words in timestamp order.
  - Match against canonical lines with `rapidfuzz`.
  - Preserve repeats by matching the performed sequence, not by deduplicating canonical lines.
- If word timestamps are missing, use sentence segments directly.
- Convert final lines to `LRCLine` and write with existing `_write_lrc()`.

Quality policy:

- If there are fewer than `qwen3_asr_min_usable_segments`, treat as hard failure and fall back to Whisper.
- If there are usable segments but snap confidence is weak, complete the job with:
  - `lrc_source="qwen3_asr"`
  - `job.warning` containing a concise message such as `Qwen3 ASR completed with low canonical snap confidence; review in Lyrics Editor`.
- Do not call forced alignment.
- Do not fall back to Whisper solely because snap confidence is low. This follows the chosen operational policy: complete with warning and use the Admin Lyrics Editor for cleanup.

### 5. Rewire Queue Priority

Modify `_process_lrc_job()` in `queue.py`:

1. Keep cache check unchanged.
2. Keep YouTube branch first.
3. If YouTube succeeds:
   - Set `lrc_source="youtube_transcript"`.
   - Skip audio download, stem separation, Qwen3 ASR, and Whisper.
4. If YouTube fails or is absent:
   - Download source audio.
   - Try Qwen3 ASR if `request.options.use_qwen3_asr` is true.
   - Resolve dry vocals through the new helper.
   - If dry vocals are available, check Qwen3 ASR cache unless `force_qwen3_asr=True`.
   - Generate LRC from Qwen3 ASR.
   - On success, set `lrc_source="qwen3_asr"` and skip Whisper.
   - On hard failure, log and set stage `falling_back_to_whisper`.
5. Run existing Whisper path unchanged except for using the stem helper.

Stages to add:

- `resolving_qwen3_vocals`
- `qwen3_asr_cached`
- `qwen3_asr_transcribing`
- `qwen3_asr_snapping`
- `qwen3_asr_warning`
- `qwen3_asr_done`
- `falling_back_to_whisper`

### 6. Remove Qwen3 Forced Alignment

In `lrc.py`, remove:

- `Qwen3Client` import.
- `OutputFormat` import.
- `Qwen3RefinementError` if unused.
- `_qwen3_refine()`.
- `_parse_qwen3_lrc()`.
- `_get_audio_duration()` if only used for Qwen3 refinement.
- The `if options.use_qwen3 and content_hash:` refinement block.

In services:

- Remove `services/analysis/src/sow_analysis/services/qwen3_client.py` if no imports remain.
- Remove its exports from `services/analysis/src/sow_analysis/services/__init__.py`.

Do not remove unrelated `services/qwen3/` code in this change unless it is proven unused by the rest of the repo. The requested scope is LRC worker behavior, not deleting the separate service tree.

### 7. Update Docs

Update `docs/lrc-job-flow.md`:

- Describe the new priority order.
- Replace Qwen3 refinement section with Qwen3 ASR fallback section.
- Document that Qwen3 ASR requires dry vocals and relies on the stem child job.
- Document that Qwen3 ForcedAligner is removed from the LRC worker.
- Update Mermaid diagram and stage list.
- Add new env vars and option fields.

Add release note text to the new spec or status doc:

- Existing jobs with old `use_qwen3` options continue to submit.
- `use_qwen3` no longer enables timestamp refinement.
- Use `use_qwen3_asr` / `--no-qwen3-asr` for the new transcription path.

## Testing Plan

### Unit Tests

Add or update tests under `tests/services/analysis/` or `services/analysis/tests/`, matching the existing test layout.

Qwen3 ASR client tests:

- Parses direct flash response into sentence segments.
- Parses direct flash response with word timestamps.
- Parses filetrans response by fetching `transcription_url`.
- Handles missing `words` by still returning sentence segments.
- Raises `Qwen3AsrError` on API error.
- Chooses filetrans for long/large audio.

Canonical snap/reconstruction tests:

- Exact Traditional Chinese canonical lines snap correctly.
- Simplified ASR text snaps to Traditional canonical text.
- Repeated chorus lines are preserved as repeated LRC lines.
- Short fragments do not over-snap to unrelated lyrics.
- Low confidence returns usable ASR text plus warning metadata.
- Empty ASR result is treated as hard failure.

Queue orchestration tests:

- YouTube success skips audio download and Qwen3 ASR.
- YouTube failure plus existing `vocals_dry` runs Qwen3 ASR.
- Missing dry vocals submits a `STEM_SEPARATION` child job.
- Completed child job with `vocals_dry_url` runs Qwen3 ASR.
- Child timeout skips Qwen3 ASR and falls back to Whisper.
- Qwen3 ASR hard failure falls back to Whisper.
- Qwen3 ASR low-confidence usable output completes with warning and does not run Whisper.
- `force_qwen3_asr=True` bypasses Qwen3 ASR cache only.
- Deprecated `use_qwen3=True` does not call forced alignment.

Admin tests:

- `submit_lrc()` sends `use_qwen3_asr` and `force_qwen3_asr`.
- `--no-qwen3-asr` sets `use_qwen3_asr=False`.
- Deprecated `--no-qwen3` remains accepted and does not break submission.

### Integration Tests

Run fast backend tests:

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ \
  --ignore=tests/services/analysis \
  --ignore=services/qwen3/tests \
  --ignore=services/analysis/tests -v
```

Run analysis service tests:

```bash
cd services/analysis
PYTHONPATH=src uv run --python 3.11 --extra dev pytest tests -v
```

Run a real DashScope smoke test only when credentials are available:

- Use a song with known good lyrics.
- Ensure no YouTube transcript or force YouTube failure.
- Ensure `vocals_dry.flac` exists or allow child stem generation.
- Verify resulting job has `lrc_source="qwen3_asr"`.
- Open the output in the Admin Lyrics Editor and confirm repeats are preserved.

### Regression Checks

- Existing YouTube transcript jobs still complete without downloading audio.
- Existing Whisper-only behavior works with `use_qwen3_asr=False`.
- LRC cache still includes lyrics hash.
- Whisper transcription cache behavior remains unchanged.
- Local model semaphore is not acquired for DashScope calls.
- MVSEP daily limits and local fallback continue to be controlled by the stem worker.

## Operational Behavior

### Data Quality

- YouTube remains best effort and preferred.
- Qwen3 ASR is expected to improve Chinese sung-audio transcription and repeated-section handling.
- Qwen3 ASR low-confidence output should be surfaced as a warning, not hidden by automatic forced alignment.
- Manual timing cleanup should use the Admin Lyrics Editor.

### Runtime

- Qwen3 ASR adds a cloud API call after stem preparation.
- The slowest path is still missing-stem generation because the LRC job waits on the child `STEM_SEPARATION` job.
- The existing 2-hour child stem wait remains the default.
- Whisper remains available when Qwen3 ASR is unavailable or skipped.

### Cost and Limits

- DashScope usage is gated by `SOW_QWEN3_ASR_API_KEY` and `use_qwen3_asr`.
- MVSEP usage remains gated by existing MVSEP settings and daily limit.
- Qwen3 ASR cache should prevent repeated API charges for the same audio, lyrics, context, model, and stem configuration.

### Failure Policy

Fallback to Whisper when:

- No DashScope API key is configured.
- Dry vocals cannot be obtained.
- DashScope returns an error or times out.
- The parsed ASR result has too few usable segments.
- The ASR response schema cannot be parsed.

Complete with warning when:

- Qwen3 ASR returns enough timestamped text to build an LRC.
- Canonical snap confidence is weak.
- The output likely needs manual review but is still useful in the Lyrics Editor.

Fail the whole LRC job only when:

- YouTube fails or is unavailable.
- Qwen3 ASR fails or is skipped.
- Whisper fallback also fails.

## Acceptance Criteria

- The worker priority is observably YouTube -> Qwen3 ASR -> Whisper.
- Qwen3 ASR only runs on dry/clean vocal stems, never on raw vocals or full mix.
- Missing stems trigger the existing stem child job, which keeps MVSEP-first/local-fallback behavior.
- Qwen3 ForcedAligner is no longer called by the LRC worker.
- Jobs can complete with `lrc_source="qwen3_asr"`.
- Low-confidence Qwen3 output completes with `job.warning`.
- Whisper fallback remains functional.
- Admin CLI can disable or force the Qwen3 ASR cache.
- Tests cover priority ordering, fallbacks, cache behavior, and deprecated forced-alignment options.

