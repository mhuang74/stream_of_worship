# Plan: `poc/gen_lrc_qwen3_asr_mvsep_force_align_v2.py`

## Goal

Create a POC script that combines Qwen3-ASR transcription with Qwen3-ForcedAligner to produce high-quality LRC files. The key insight: use ASR for the **text** (which reflects the actual performance structure with repeated sections), then use the ForcedAligner to produce accurate timestamps by aligning that text against the audio. Chunk-based alignment eliminates the 5-minute limit and the quality cliff of a single-window approach.

## Pipeline

```
Audio (full song)
  │
  ├─► MVSEP vocal extraction (cloud API)
  │
  ├─► Qwen3-ASR-Flash (full song)
  │     ├─► Word-level timestamps (for chunk text assignment + fallback)
  │     └─► Sentence-level text (concatenated → alignment input)
  │
  ├─► ASR verification: compare ASR text vs canonical (diagnostic only)
  │
  ├─► Chunk planning:
  │     • If audio ≤ 5 min → single chunk (full audio)
  │     • If audio > 5 min → overlapping 5-min chunks (60s overlap)
  │     • Use word-level ASR timestamps to assign text per chunk
  │
  ├─► For each chunk:
  │     ├─► Extract audio segment
  │     ├─► Qwen3-ForcedAligner (chunk audio + chunk ASR text)
  │     └─► Offset timestamps by chunk start time
  │
  ├─► Merge chunks:
  │     • Deduplicate at overlap boundaries
  │     • Prefer alignment from chunk interior (farther from edges)
  │     • Full-song force-aligned segments
  │
  ├─► [Optional] Sequential canonical snap (--snap)
  │     └─► Replace text with canonical lyrics using sequential fuzzy match
  │         with wrap-around reset (keeps force-aligned timestamps)
  │
  └─► Output LRC + diagnostics
```

## Key Design Decisions

### 1. ASR text as alignment input (not canonical lyrics)

Canonical lyrics represent the *song structure* (unique lines), not the *performance structure* (with repeated verses/choruses, ad-libs, skipped verses). ASR text reflects what is actually sung, making it the correct input for forced alignment.

- Concatenate all `sentence.text` from ASR response into a single string (newline-joined), discarding ASR sentence-level timestamps
- This text is fed to the ForcedAligner
- `map_segments_to_lines()` works correctly because the aligner's output text matches the input text exactly

### 2. Chunk-based alignment (eliminates 5-minute limit and quality cliff)

The ForcedAligner has a 5-minute max. Instead of a single window, split songs into overlapping chunks:

- **Audio ≤ 5 min**: single chunk (full audio), no splitting needed
- **Audio > 5 min**: overlapping 5-min chunks with 60-second overlap
  - Chunk size: 300 seconds (5 min)
  - Overlap: 60 seconds
  - Step: 240 seconds (300 - 60)
  - Example for a 7-minute song:
    - Chunk 1: 0:00–5:00
    - Chunk 2: 4:00–7:00
  - Example for a 10-minute song:
    - Chunk 1: 0:00–5:00
    - Chunk 2: 4:00–9:00
    - Chunk 3: 8:00–10:00 (clamped to audio end)

**Merge strategy**: At overlap boundaries, prefer alignment from the chunk whose timestamps are farther from its edges (since alignment quality may degrade near chunk boundaries). Specifically:
- For each line in the overlap region, compute its distance from the nearest chunk edge
- Keep the alignment from the chunk where the line is more interior
- This is a heuristic; if edge degradation is not observed in practice, simplify to midpoint-based splitting

**Result**: The entire song gets force-aligned timestamps. No ASR-timestamp fallback for any portion of the song.

### 3. Word-level ASR timestamps for chunk text assignment

The ASR API is called with `enable_words: True`, but v1 only extracted sentence-level timestamps. V2 extracts word-level timestamps for two purposes:

1. **Chunk text assignment**: Precisely determine which ASR text falls within each chunk's time range, so the correct text is fed to the ForcedAligner for each chunk
2. **Per-chunk fallback**: If forced alignment fails on a specific chunk, fall back to word-level ASR timestamps for that chunk only (better than sentence-level)

**Implementation note**: The exact schema of the `words` field in the ASR response must be validated during implementation. The `enable_words: True` flag is already set in `call_qwen3_asr()`, but the word-level data in the response has not been parsed before. The implementation should inspect the raw response to determine the field structure (likely `words[]` within each sentence, with `begin_time`/`end_time`/`text` per word).

### 4. ASR verification layer (diagnostic only)

Before alignment, compare ASR text against canonical lyrics to detect quality issues:

- Compute an overall fuzzy match score between concatenated ASR text and canonical lyrics
- If score is high (≥ 0.8): log "high confidence" diagnostic
- If score is moderate (0.5–0.8): log "moderate confidence" warning — ASR may have misrecognized some words
- If score is low (< 0.5): log "low confidence" warning — possible live/alternate version, or very different performance from canonical lyrics
- This does **not** change the pipeline behavior — it only adds diagnostic output

### 5. Sequential canonical snap with wrap-around

The existing `canonical_line_snap()` matches each segment independently against all canonical lines, which fails for repeated sections (both instances of a chorus line match the same canonical line with no way to distinguish them).

V2 uses **sequential fuzzy matching**:

- Maintain a cursor through canonical lyrics, advancing forward
- For each force-aligned line, search forward from the current cursor position
- If a good match (≥ threshold) is found: replace text, advance cursor past that canonical line
- If no good match is found forward: **wrap around** to the beginning of canonical lyrics and re-search
  - If a good match is found after wrap-around: replace text, reset cursor to just past the matched line
  - If still no match: keep original ASR text, do not advance cursor
- This naturally handles repeated sections because the cursor advances through the canonical lyrics in performance order

**Wrap-around reset heuristic**: When wrapping, if the fuzzy score drops below threshold for several consecutive lines, reset the cursor to 0 and re-search. This handles cases where the performance jumps back to verse 1 after a bridge.

### 6. Vocal extraction

MVSEP cloud API (same as `gen_lrc_qwen3_asr_mvsep.py`), with all the same stage1/stage2 options.

### 7. ASR models

Support both `qwen3-asr-flash` and `qwen3-asr-flash-filetrans` (same as mvsep script).

### 8. Context biasing

`--lyrics-context` / `--no-lyrics-context` flag (default: enabled). Passes canonical lyrics as context to the ASR call for better transcription accuracy.

## File Structure

New file: `poc/gen_lrc_qwen3_asr_mvsep_force_align_v2.py`

### Reused functions (copied from existing scripts)

| Function | Source | Notes |
|---|---|---|
| `call_qwen3_asr()` | `gen_lrc_qwen3_asr_mvsep.py` | ASR API call (both models) |
| `extract_segments()` | `gen_lrc_qwen3_asr_mvsep.py` | Parse ASR response into sentence-level segments |
| `results_to_lrc()` | `gen_lrc_qwen3_asr_mvsep.py` | Convert results to LRC format |
| `write_diagnostic()` | `gen_lrc_qwen3_asr_mvsep.py` | Diagnostic markdown output (extended for v2) |
| `resolve_song_audio_path_mvsep()` | `gen_lrc_qwen3_asr_mvsep.py` | MVSEP vocal extraction + path resolution |
| `align_lyrics()` | `gen_lrc_qwen3_force_align.py` | Run Qwen3ForcedAligner |
| `map_segments_to_lines()` | `gen_lrc_qwen3_force_align.py` | Map char-level to line-level |
| `format_timestamp()` | `poc/utils.py` | Timestamp formatting |
| `extract_audio_segment()` | `poc/utils.py` | Audio segment extraction |

**Important**: These functions are currently defined inline in each script (not importable as modules). The new script should **copy** the needed functions or refactor them into `poc/utils.py`. Given POC convention, copying is acceptable, but a note should be added about future refactoring.

### New functions

| Function | Purpose |
|---|---|
| `extract_word_timestamps(response)` | Extract word-level timestamps from ASR response. **Must validate response schema during implementation** — the `enable_words: True` flag is already set but word-level data has not been parsed before. Returns `list[(start, end, text)]` at word granularity. |
| `extract_asr_text(response)` | Extract concatenated text from ASR response (all `sentence.text` joined by newline) |
| `plan_chunks(audio_duration, overlap)` | Compute chunk boundaries: list of `(chunk_start, chunk_end)` tuples. 300s chunks, configurable overlap (default 60s), step = 300 - overlap. Single chunk if ≤ 300s. |
| `assign_text_to_chunks(asr_words, chunks)` | Use word-level ASR timestamps to determine which words (and thus which sentence text) fall within each chunk's time range. Returns `dict[chunk_index, str]` mapping each chunk to its ASR text. |
| `align_chunk(audio_path, chunk_start, chunk_end, chunk_text, ...)` | Extract audio segment for chunk, run `align_lyrics()`, offset timestamps by `chunk_start`. Returns `list[(start, end, text)]`. Falls back to word-level ASR timestamps if alignment fails. |
| `merge_chunks(chunk_results, chunks)` | Merge force-aligned results from all chunks. Deduplicate at overlap boundaries by preferring alignment from chunk interior (farther from edges). Returns full-song `list[(start, end, text)]`. |
| `sequential_canonical_snap(segments, lyrics, threshold)` | Sequential fuzzy snap with wrap-around. Maintains cursor through canonical lyrics, advances forward, wraps on repeated sections. Returns `list[(start, text, replaced)]`. |
| `verify_asr_quality(asr_text, canonical_lyrics)` | Compute overall fuzzy match score between ASR text and canonical lyrics. Returns `(score, label)` tuple for diagnostic output. Does not affect pipeline behavior. |

### CLI options

```
song_id                    Song ID or path to audio file

# Vocal extraction (MVSEP)
--mvsep-vocals/--no-mvsep-vocals   Use MVSEP vocal extraction (default: True)
--mvsep-api-token                   MVSEP API token (or MVSEP_API_KEY env)
--stage1-sep-type                   Stage 1 sep_type (default: 48)
--stage1-add-opt1                   Stage 1 model variant (default: 11)
--stage2-sep-type                   Stage 2 sep_type (default: 22)
--stage2-add-opt1                   Stage 2 model variant (default: 0)
--stage2-add-opt2                   Stage 2 add_opt2 (default: 1)
--output-format                     MVSEP output format (default: 2)
--timeout                           Max seconds per MVSEP stage (default: 900)
--reuse-stage1                      Reuse existing Stage 1 vocals

# ASR
--model                             qwen3-asr-flash or qwen3-asr-flash-filetrans (default: qwen3-asr-flash)
--region                            intl, cn, us (default: intl)
--lyrics-context/--no-lyrics-context  Enable context biasing with lyrics (default: True)

# Forced alignment
--device                            auto/mps/cuda/cpu (default: auto)
--dtype                             bfloat16/float16/float32 (default: float32)
--model-cache-dir                   Custom HuggingFace cache directory
--language                          Language hint (default: Chinese)

# Chunking
--chunk-overlap                     Overlap between chunks in seconds (default: 60)

# Snap
--snap/--no-snap                    Enable canonical-line sequential snap (default: True)
--snap-threshold                    Minimum fuzzy score to snap (default: 0.60)

# Output
--output, -o                        Output file (default: stdout)
--save-raw                          Directory to save raw ASR response + diagnostics
--lyrics-file                       Path to lyrics file (overrides DB lyrics)
```

## Detailed Flow

### Step 1: Resolve audio + lyrics

- Call `resolve_song_audio_path_mvsep()` to get vocal audio path and lyrics from DB
- If `--lyrics-file` provided, override DB lyrics with file contents
- Lyrics are required if `--snap` or `--lyrics-context` is enabled

### Step 2: Run ASR on full song

- Call `call_qwen3_asr()` on the full audio
- If `--lyrics-context` enabled, pass lyrics as context biasing
- Save raw response if `--save-raw` specified
- Extract sentence-level segments via `extract_segments()` → list of `(start, end, text)` tuples
- Extract word-level timestamps via `extract_word_timestamps()` → list of `(start, end, text)` tuples
- Extract concatenated text via `extract_asr_text()` → single string for force alignment

### Step 3: ASR verification (diagnostic)

- If canonical lyrics are available, call `verify_asr_quality()` with concatenated ASR text and canonical lyrics
- Log the confidence level (high/moderate/low) to stderr
- This does not affect pipeline behavior

### Step 4: Plan chunks

- Get audio duration
- Call `plan_chunks(audio_duration, overlap)` → list of `(chunk_start, chunk_end)` tuples
- If single chunk (audio ≤ 5 min): skip chunk assignment, use full ASR text directly
- If multiple chunks: call `assign_text_to_chunks(asr_words, chunks)` to determine ASR text per chunk

### Step 5: Force-align each chunk

For each chunk:
- Call `align_chunk()`:
  - Extract audio segment for the chunk via `extract_audio_segment()`
  - Run `align_lyrics()` with chunk audio + chunk ASR text
  - Offset all timestamps by `chunk_start` (since aligner sees a clipped segment starting at 0)
  - If alignment fails: fall back to word-level ASR timestamps for this chunk's time range, log warning
  - Clean up temporary audio segment file

### Step 6: Merge chunks

- Call `merge_chunks(chunk_results, chunks)`:
  - For single-chunk songs: return results directly
  - For multi-chunk songs:
    - Collect all aligned segments from all chunks (with global timestamps)
    - For overlap regions: for each segment, compute distance from nearest chunk edge
    - Keep the segment from the chunk where it is more interior
    - Sort all retained segments by start time
  - Returns full-song `list[(start, end, text)]`

### Step 7: Optional sequential canonical snap

- If `--snap` enabled: run `sequential_canonical_snap()` on the merged results
- This replaces text with canonical lyrics using sequential fuzzy matching with wrap-around
- Timestamps remain unchanged (only text replacement)
- Returns `list[(start, text, replaced)]`

### Step 8: Output LRC

- Convert to LRC via `results_to_lrc()`
- Write to file or stdout
- Write diagnostic if `--save-raw` specified (includes per-chunk alignment stats, snap results, ASR verification score)

## Edge Cases

- **No ASR segments**: Error out (same as mvsep script)
- **No ASR words**: Fall back to sentence-level timestamps for chunk text assignment (less precise but functional)
- **No lyrics + snap enabled**: Error out with clear message
- **No lyrics + lyrics-context enabled**: Warn and proceed without context
- **Force alignment fails on a chunk**: Fall back to word-level ASR timestamps for that chunk only; other chunks unaffected
- **Force alignment fails on all chunks**: Fall back to full ASR sentence-level output with warning
- **Chunk shorter than overlap**: Last chunk may be shorter than 300s; clamp to audio end
- **Very short songs (< 30s)**: Single chunk, no overlap issues
- **Direct audio file path (no song ID)**: No DB lyrics available; require `--lyrics-file` if snap/context needed
- **Word-level ASR response schema differs from expected**: `extract_word_timestamps()` should validate the response structure and fall back gracefully with a warning

## Differences from V1

| Aspect | V1 | V2 |
|---|---|---|
| 5-min limit handling | Single window (last 5 min default) | Chunk-based overlapping alignment (entire song) |
| Pre-window timestamps | ASR sentence-level (quality cliff) | No fallback needed — entire song is force-aligned |
| Alignment text source | ASR text | ASR text (same — correct for performance structure) |
| Canonical snap | Independent fuzzy match (breaks on repeated lines) | Sequential fuzzy match with wrap-around |
| Word-level ASR | Not used | Used for chunk text assignment + per-chunk fallback |
| ASR verification | None | Diagnostic confidence score |
| `--start`/`--end` | Manual window override | Removed (chunking handles all durations) |
| Chunk overlap | N/A | 60s configurable via `--chunk-overlap` |

## Differences from Source Scripts

| Aspect | `gen_lrc_qwen3_asr_mvsep.py` | `gen_lrc_qwen3_force_align.py` | **V2 script** |
|---|---|---|---|
| Timestamps source | ASR only | ForcedAligner only | ForcedAligner (chunked, full song) |
| Text source | ASR (with optional snap) | Canonical lyrics | ASR text → ForcedAligner → optional sequential snap |
| Audio limit | None | 5 min (hard) | None (chunked) |
| Vocal extraction | MVSEP | Local | MVSEP |
| Requires lyrics | Yes (for snap) | Yes (for alignment) | No (for ASR-only); Yes (for snap/context) |
| Snap strategy | Independent fuzzy match | N/A | Sequential fuzzy match with wrap-around |
| Word-level ASR | Not extracted | N/A | Extracted and used |

## Implementation Notes

1. **Word-level ASR response validation**: The `enable_words: True` flag is already set in `call_qwen3_asr()`, but the word-level data has never been parsed. The implementation must first inspect a raw ASR response to determine the exact field structure (likely `words[]` within each sentence object, with `begin_time`/`end_time`/`text` per word). If the schema differs, `extract_word_timestamps()` must be adapted accordingly.

2. **Chunk merge edge quality**: The "prefer interior alignment" heuristic assumes alignment quality degrades near chunk edges. This should be validated empirically — if edge degradation is not observed, simplify to midpoint-based splitting (use chunk N for everything before the overlap midpoint, chunk N+1 for everything after).

3. **Sequential snap wrap-around tuning**: The wrap-around behavior (when to reset the cursor to the beginning of canonical lyrics) may need tuning. The initial implementation should use a simple strategy: wrap when no forward match is found, reset cursor to 0. If this produces poor results on test songs, consider a "consecutive miss" threshold (wrap after N consecutive misses).

4. **Future refactoring**: The copied functions from existing scripts should eventually be refactored into `poc/utils.py` or a shared module. This is deferred per POC convention.
