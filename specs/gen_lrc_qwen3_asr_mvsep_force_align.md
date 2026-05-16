# Plan: `poc/gen_lrc_qwen3_asr_mvsep_force_align.py`

## Goal

Create a new POC script that combines Qwen3-ASR transcription with Qwen3-ForcedAligner to produce LRC files. The key insight: use ASR only for the **text** (discarding its timestamps), then use the ForcedAligner to produce accurate timestamps by aligning that text against the audio. Optionally snap ASR text to canonical lyrics at the end.

## Pipeline

```
Audio (full song)
  │
  ├─► MVSEP vocal extraction (cloud API)
  │
  ├─► Qwen3-ASR-Flash (full song)
  │     └─► Extract concatenated text from all sentence.text fields
  │         (discard ASR timestamps)
  │
  ├─► Determine alignment window:
  │     • If audio ≤ 5 min → align full audio
  │     • If audio > 5 min → align last 5 min by default
  │     • --start / --end override the window
  │
  ├─► Qwen3-ForcedAligner (on the alignment window)
  │     └─► Produces (start, end, text) per line with accurate timestamps
  │
  ├─► Merge: ASR timestamps for non-aligned portion + force-aligned timestamps
  │     for the aligned portion → full-song LRC
  │
  └─► [Optional] Canonical-line snap (--snap)
        └─► Replace text with canonical lyrics, keep force-aligned timestamps
```

## Key Design Decisions

1. **ASR text only**: Concatenate all `sentence.text` from ASR response into a single string (newline-joined), discarding ASR timestamps entirely. This text is fed to the ForcedAligner.

2. **5-minute limit handling**: The ForcedAligner has a 5-minute max. The script:
   - If audio ≤ 5 min: align the full audio
   - If audio > 5 min: default to aligning the **last 5 minutes** (quality degrades toward end of songs)
   - `--start` / `--end` flags override this behavior for manual control
   - The alignment window must not exceed 5 minutes

3. **Full-song LRC output**: The final LRC covers the entire song:
   - For the portion **before** the alignment window: use ASR segment timestamps (from `extract_segments()`)
   - For the alignment window: use force-aligned timestamps
   - This avoids losing the beginning of the song

4. **Vocal extraction**: MVSEP cloud API (same as `gen_lrc_qwen3_asr_mvsep.py`), with all the same stage1/stage2 options.

5. **ASR models**: Support both `qwen3-asr-flash` and `qwen3-asr-flash-filetrans` (same as mvsep script).

6. **Context biasing**: `--lyrics-context` / `--no-lyrics-context` flag (default: enabled). Passes canonical lyrics as context to the ASR call for better transcription accuracy.

7. **Snap (optional)**: `--snap` / `--no-snap` flag (default: enabled). When enabled:
   - Canonical lyrics are **required** (from DB or `--lyrics-file`)
   - Replaces ASR text with canonical lyrics text using fuzzy matching
   - **Keeps force-aligned timestamps unchanged** (only text replacement)
   - Uses the same `canonical_line_snap()` function from the mvsep script

## File Structure

New file: `poc/gen_lrc_qwen3_asr_mvsep_force_align.py`

### Reused functions (imported from existing scripts)

| Function | Source | Notes |
|---|---|---|
| `call_qwen3_asr()` | `gen_lrc_qwen3_asr_mvsep.py` | ASR API call (both models) |
| `extract_segments()` | `gen_lrc_qwen3_asr_mvsep.py` | Parse ASR response into segments |
| `canonical_line_snap()` | `gen_lrc_qwen3_asr_mvsep.py` | Fuzzy snap to canonical lyrics |
| `results_to_lrc()` | `gen_lrc_qwen3_asr_mvsep.py` | Convert results to LRC format |
| `write_diagnostic()` | `gen_lrc_qwen3_asr_mvsep.py` | Diagnostic markdown output |
| `resolve_song_audio_path_mvsep()` | `gen_lrc_qwen3_asr_mvsep.py` | MVSEP vocal extraction + path resolution |
| `align_lyrics()` | `gen_lrc_qwen3_force_align.py` | Run Qwen3ForcedAligner |
| `map_segments_to_lines()` | `gen_lrc_qwen3_force_align.py` | Map char-level to line-level |
| `format_timestamp()` | `poc/utils.py` | Timestamp formatting |
| `extract_audio_segment()` | `poc/utils.py` | Audio segment extraction |

**Important**: These functions are currently defined inline in each script (not importable as modules). The new script should **copy** the needed functions or refactor them into `poc/utils.py`. Given POC convention, copying is acceptable, but a note should be added about future refactoring.

### New functions

| Function | Purpose |
|---|---|
| `extract_asr_text(response)` | Extract concatenated text from ASR response (all sentence.text joined by newline) |
| `determine_alignment_window(audio_duration, start, end)` | Compute the alignment window: auto-select last 5 min if > 5 min, or use --start/--end overrides |
| `merge_asr_and_aligned(asr_segments, aligned_segments, window_start, window_end)` | Merge ASR segments (before window) with force-aligned segments (within window) into full-song LRC |

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

# Alignment window
--start                             Start timestamp in seconds (overrides auto window)
--end                               End timestamp in seconds (overrides auto window)

# Snap
--snap/--no-snap                    Enable canonical-line fuzzy snap (default: True)
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
- Extract segments via `extract_segments()` → list of `(start, end, text)` tuples
- Extract concatenated text via `extract_asr_text()` → single string for force alignment

### Step 3: Determine alignment window

- Get audio duration
- If `--start` / `--end` specified: use those (validate ≤ 5 min window)
- Else if duration ≤ 5 min: align full audio (window = 0 to duration)
- Else: align last 5 min (window = duration-300 to duration)
- Extract audio segment for the alignment window

### Step 4: Force-align the window

- Call `align_lyrics()` with the alignment window audio + concatenated ASR text
- This produces `(start, end, text)` tuples with accurate timestamps
- Offset timestamps by window start time (since aligner sees a clipped segment)

### Step 5: Merge ASR + force-aligned segments

- ASR segments **before** the alignment window: keep as-is (ASR timestamps + text)
- Force-aligned segments **within** the window: use force-aligned timestamps + text
- Result: full-song list of `(start, text, replaced)` tuples

### Step 6: Optional snap

- If `--snap` enabled: run `canonical_line_snap()` on the merged results
- This replaces text with canonical lyrics where fuzzy match ≥ threshold
- Timestamps remain unchanged

### Step 7: Output LRC

- Convert to LRC via `results_to_lrc()`
- Write to file or stdout
- Write diagnostic if `--save-raw` specified

## Edge Cases

- **No ASR segments**: Error out (same as mvsep script)
- **No lyrics + snap enabled**: Error out with clear message
- **No lyrics + lyrics-context enabled**: Warn and proceed without context
- **Alignment window exactly 5 min**: Fine, proceed
- **--start/--end window > 5 min**: Error out
- **Force alignment fails**: Fall back to ASR-only output with warning
- **Direct audio file path (no song ID)**: No DB lyrics available; require `--lyrics-file` if snap/context needed

## Differences from Source Scripts

| Aspect | `gen_lrc_qwen3_asr_mvsep.py` | `gen_lrc_qwen3_force_align.py` | **New script** |
|---|---|---|---|
| Timestamps source | ASR only | ForcedAligner only | ASR (pre-window) + ForcedAligner (window) |
| Text source | ASR (with optional snap) | Canonical lyrics | ASR text → ForcedAligner → optional snap |
| Audio limit | None | 5 min | 5 min (auto-window to last 5 min) |
| Vocal extraction | MVSEP | Local | MVSEP |
| Requires lyrics | Yes (for snap) | Yes (for alignment) | No (for ASR-only); Yes (for snap/context) |
