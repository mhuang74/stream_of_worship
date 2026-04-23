# Accurate Chinese Worship Lyrics + Timecodes — Pipeline Redesign

## Context

**Problem.** Producing accurate, complete LRC files (every word actually sung, with phrase timestamps) for ~685 Chinese worship songs. Today's pipeline has two primary paths — YouTube-transcript-with-LLM-correction, and faster-whisper-large-v3-with-lyrics-prompt-then-LLM-alignment, optionally refined by a self-hosted Qwen3-ForcedAligner-0.6B. Both fail on the same hard cases: Chinese character accuracy on sung audio, and handling performed repeats that the canonical sop.org lyrics don't document. Taption.com reportedly nails the transcription side (timestamps are "close enough").

**What Taption is almost certainly doing.** A CJK-tuned cloud ASR with context biasing. As of 2026, the best-in-class option matching this profile is **Qwen3-ASR-Flash** (Alibaba DashScope):
- Best published benchmark on Chinese singing voice (M4Singer, MIR-1k-vocal, Popcs) and full-song transcription with background music.
- Accepts an arbitrary **context** string (keywords, lists, full lyric documents) that biases recognition toward those terms — this is the critical feature for worship songs where character errors dominate.
- Returns character/word-level timestamps; up to 5 min per request ($0.000035/sec ≈ $0.01/song; Filetrans variant handles up to 12 hours).

**Goal.** Replace YouTube-transcript path with a Qwen3-ASR-Flash path that:
1. Runs on cleaned vocals (wire up the existing BS-Roformer + de-echo POC into production).
2. Biases ASR with the scraped canonical lyrics so Chinese characters come out right.
3. Trusts ASR for the **performed sequence** (repeats preserved automatically).
4. Snaps each ASR line to the closest canonical line (fuzzy match) to correct residual character errors, while keeping ASR timestamps.
5. Optionally polishes timestamps with the already-deployed Qwen3-ForcedAligner-0.6B microservice.

Outcome target: 95%+ zero-touch. Outliers handled by the existing admin `upload-lrc` flow with a user-prepared local LRC.

## Approach

### New pipeline (replaces YouTube path as primary)

```
MP3 (from R2)
  │
  ▼
[1] Two-stage vocal separation  ─── existing POC, promoted to prod
    BS-Roformer-Viperx-1297 → UVR-De-Echo-Normal
    → dry_vocals.flac (cached in R2 alongside audio)
  │
  ▼
[2] Qwen3-ASR-Flash (DashScope, cloud)
    - input: dry_vocals.flac
    - context: scraped canonical lyrics (zh-Hant) as hotword/biasing text
    - language: zh
    - enable_words: true   (character-level timestamps)
    → list of segments {text, start, end} — performed sequence with repeats
  │
  ▼
[3] Canonical-line snap (character correction)
    For each ASR segment:
      - compute similarity (RapidFuzz token_set_ratio on CJK chars) vs every
        canonical line from sop.org
      - if best match ≥ threshold (e.g. 0.6): replace ASR text with canonical
      - else: keep ASR text (usually ad-lib, English, or mis-split)
    Timestamps come from ASR segment.start. Repeats preserved naturally.
  │
  ▼
[4] Optional Qwen3-ForcedAligner refinement  ─── already deployed
    Only when audio ≤ 5 min and options.use_qwen3 is true. Same as today.
  │
  ▼
[5] Write LRC to R2 (unchanged downstream)
```

### Why this works

- **Character accuracy** comes from (a) Qwen3-ASR-Flash being SOTA on Chinese singing, and (b) context biasing pulling recognition toward the canonical vocabulary.
- **Repeat handling** is automatic — ASR reports what was sung, in order. No need to detect chorus boundaries or expand canonical lyrics.
- **Timestamp quality** is already acceptable from ASR (Taption-level); Qwen3-ForcedAligner stays available as the existing polish step.
- **Cleaned vocals** reduce the "heard the guitar as a word" failure mode and make the context-biasing lift more effective.

### Fallback order

1. Qwen3-ASR-Flash on dry vocals (new primary)
2. Qwen3-ASR-Flash on original mix (if Stage 2 produces empty/corrupt output)
3. faster-whisper + LLM alignment (existing code path, kept untouched as safety net)

YouTube-transcript path is **removed from the primary route** but the code stays in-tree (deprecated) until the new path proves out in production.

## Files to modify

Core worker:
- `services/analysis/src/sow_analysis/workers/lrc.py` — add `_run_qwen3_asr()` as the new primary transcription step; add `_snap_to_canonical()` for character correction; rewire `generate_lrc()` to prefer it. Keep existing `_run_whisper_transcription` and `_llm_align` as fallback.
- `services/analysis/src/sow_analysis/workers/youtube_transcript.py` — leave as-is for now, no longer called by default.

New client:
- `services/analysis/src/sow_analysis/services/qwen3_asr_client.py` — thin async wrapper over DashScope `qwen3-asr-flash` (and `qwen3-asr-flash-filetrans` for >5 min). Mirrors the existing `qwen3_client.py` patterns (base_url/api_key env vars, typed response dataclass, retries).

Vocal separation promoted to prod:
- `services/analysis/src/sow_analysis/workers/vocal_stem.py` — port the logic from `poc/gen_clean_vocal_stem.py`. Produces `dry_vocals.flac` and caches it at `s3://{bucket}/{hash12}/dry_vocals.flac` alongside `audio.mp3`.
- `services/analysis/src/sow_analysis/workers/lrc.py` — call vocal_stem before ASR; gracefully degrade to original mix if separation fails.

Config:
- `services/analysis/src/sow_analysis/config.py` — add `SOW_QWEN3_ASR_API_KEY`, `SOW_QWEN3_ASR_BASE_URL` (default `https://dashscope-intl.aliyuncs.com/api/v1`), `SOW_QWEN3_ASR_MODEL` (default `qwen3-asr-flash`), `SOW_USE_CLEAN_VOCALS_FOR_ASR` (bool, default true).
- `services/analysis/src/sow_analysis/models.py` — extend `LrcOptions` with `use_qwen3_asr: bool = True` and `asr_context_max_chars: int = 10000`.

Tests:
- `services/analysis/tests/workers/test_lrc_qwen3_asr.py` — unit tests with mocked DashScope client covering: canonical snap threshold behavior, repeats preservation, fallback to Whisper when ASR returns nothing, context truncation.
- `services/analysis/tests/services/test_qwen3_asr_client.py` — request/response contract, retry, long-audio routing to Filetrans model.

Bridge script (optional, for backfilling):
- `scripts/regenerate_lrc_with_asr.py` — iterate catalog, regenerate LRCs for songs where the current LRC was generated via YouTube or Whisper paths. Uses existing admin services.

## Key reuse (do NOT rewrite)

- `audio_separator.separator.Separator` — already pinned in `pyproject.toml`. The POC logic in `poc/gen_clean_vocal_stem.py` lines 21–199 is the reference implementation. Port verbatim with minor cleanup.
- `services/analysis/src/sow_analysis/services/qwen3_client.py` — mirror its structure for the new ASR client (same settings/retry/timeout conventions).
- `services/analysis/src/sow_analysis/workers/lrc.py::_write_lrc`, `::_format_timestamp`, `::_parse_qwen3_lrc`, `::_validate_alignment_coverage` — reuse unchanged.
- `src/stream_of_worship/admin/services/analysis.py` — admin LRC commands (`lrc_recording`, `view-lrc`, `upload-lrc`) already work end-to-end; no admin-side changes needed. `upload-lrc` covers the outlier manual path.
- `rapidfuzz` — already in `song_analysis` extra (verify; add if missing) — use `fuzz.token_set_ratio` on CJK character sets for canonical-line snapping.

## Environment

New secrets required:
- `SOW_QWEN3_ASR_API_KEY` — DashScope API key (get from Alibaba Cloud Model Studio console).
- Choose regional endpoint: `dashscope-intl.aliyuncs.com` (Singapore, recommended from US), `dashscope.aliyuncs.com` (Beijing), `dashscope-us.aliyuncs.com` (Virginia, slightly cheaper).

Cost estimate: ~685 songs × 4 min avg × $0.000035/s = **~$5.75 one-time backfill**. Per new song: ~$0.008.

## Verification

1. **Unit tests** — `PYTHONPATH=services/analysis/src uv run --extra analysis pytest services/analysis/tests/workers/test_lrc_qwen3_asr.py services/analysis/tests/services/test_qwen3_asr_client.py -v`
2. **Benchmark on known-good songs.** Pick 10 songs already reviewed manually; regenerate LRC via new pipeline and diff against the hand-corrected version. Acceptance: ≥95% of lines match character-for-character; avg timestamp delta ≤ 0.5 s.
3. **End-to-end admin flow.** On one song:
   - `uv run sow-admin audio lrc_recording --song-id <id>` — expect the new logs showing "Qwen3-ASR path" chosen, vocal separation stage, context size, segments returned, canonical-snap corrections applied.
   - `uv run sow-admin audio view-lrc --song-id <id>` — sanity-check structure.
   - Render in the user app lyrics preview; confirm repeats are present and lines land on beat in `src/stream_of_worship/app/screens/lyrics_preview.py`.
4. **Failure modes.** Force each fallback by toggling flags: (a) no DashScope key → Whisper path; (b) vocal separation disabled → ASR on mix; (c) >5 min song → Filetrans model path.
5. **Update** `report/current_impl_status.md` and memory once landed, per CLAUDE.md convention.

## Out of scope (for this plan)

- Replacing Qwen3-ForcedAligner-0.6B with something better — timestamp refinement already works.
- Building a lyric-review UI — user confirmed outliers handled via existing `upload-lrc`.
- Retiring the YouTube-transcript code path — kept deprecated until new pipeline proves out, then delete in a follow-up.
