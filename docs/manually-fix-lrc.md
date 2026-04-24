# Manual LRC Fixing Guide

This guide covers the step-by-step workflow for manually fixing LRC lyrics timing for songs in the Stream of Worship catalog.

## When to Use This Workflow

Use this workflow when:
- An existing LRC file has poor timing accuracy
- LRC quality scoring indicates failures or errors
- Manual review reveals misaligned lyrics
- Testing new transcription/alignment methods

## Prerequisites

Before beginning, ensure you have the required dependencies installed:

```bash
# Install dependencies for the full workflow (single command)
uv sync --extra fix_lrc

# Install mlx-audio separately (due to dependency conflicts with qwen-asr)
# Note: mlx-audio>=0.4.0 is required for quality scoring (tested with 0.4.2)
# mlx-audio and qwen-asr have incompatible transformers version requirements:
#   - qwen-asr requires transformers==4.57.6
#   - mlx-audio>=0.4.0 requires transformers>=5.0.0
# Therefore, mlx-audio must be installed manually AFTER running fix_lrc workflow
uv pip install "mlx-audio>=0.4.0" --prerelease=allow
```

## Audio Source Resolution

All LRC fixing scripts resolve audio from the local cache directory with the following priority:

1. **`clean_vocals.flac`** — Clean vocal stem (de-echoed, generated in Step 0). Preferred for best alignment accuracy.
2. **`vocal.wav`** — Standard vocal stem (from R2, downloaded via `sow_admin audio cache`). Fallback when clean vocals haven't been generated yet.
3. **`audio.mp3`** — Full mix. Last resort when no vocal stems are available.

If `clean_vocals.flac` is not in the cache, it is strongly recommended to run Step 0 to generate it and copy it to the cache directory for future use. Only fall back to `vocal.wav` or `audio.mp3` if generating clean vocals is not feasible.

The cache directory for a song can be obtained via:

```bash
sow_admin audio cache <song_id>
```

Look for the "Cache location:" line in the output to find the local directory path (e.g., `~/.config/sow-app/cache/<hash_prefix>/`). The files are located at:

- `clean_vocals.flac` — `<cache_dir>/<hash_prefix>/clean_vocals.flac`
- `vocal.wav` — `<cache_dir>/<hash_prefix>/stems/vocals.wav`
- `audio.mp3` — `<cache_dir>/<hash_prefix>/audio/audio.mp3`

---

## Step-by-Step Workflow

### Step 0: Generate Clean Vocal Stems (Recommended)

Generate high-quality vocal stems using two-stage vocal extraction (BS-Roformer + De-Echo). If `clean_vocals.flac` is not already in the cache, you should generate it here and copy it to the cache directory for all subsequent steps to use.

```bash
# Get the cache directory for the song
CACHE_DIR=$(sow_admin audio cache <song_id> 2>/dev/null | grep "Cache location:" | awk '{print $NF}')

# Generate clean vocals from the cached audio
uv run --extra stem_separation python poc/gen_clean_vocal_stem.py \
  "$CACHE_DIR/audio/audio.mp3" \
  -o ./tmp_output/vocals
```

**What This Does:**
1. **Stage 1**: Extracts vocals from the mix using BS-Roformer-Viperx-1297
2. **Stage 2**: Removes echo/reverb using UVR-De-Echo-Normal

**Output Files:**
- `./tmp_output/vocals/stage1_vocal_separation/` - Initial vocal separation
- `./tmp_output/vocals/stage2_dereverb/` - Clean vocals (no echo)
- Look for files containing `(No Echo)` or `dry` for the cleanest vocals

**Replacing Cached Stems:**

Once you have the clean vocals, save them as `clean_vocals.flac` in the cache directory so that subsequent steps use it:

```bash
# Find the clean vocals file (usually contains "No Echo" in the name)
CLEAN_VOCALS=$(find ./tmp_output/vocals/stage2_dereverb -name "*No Echo*.flac" | head -1)

# Get the cache directory
CACHE_DIR=$(sow_admin audio cache <song_id> 2>/dev/null | grep "Cache location:" | awk '{print $NF}')

# Save as clean_vocals.flac in the cache directory
cp "$CLEAN_VOCALS" "$CACHE_DIR/clean_vocals.flac"

# Verify the replacement
ls -la "$CACHE_DIR/"
```

**Key Options:**
- `--dereverb-model` - Choose de-echo model:
  - `UVR-De-Echo-Normal.pth` (default, balanced)
  - `UVR-De-Echo-Aggressive.pth` (stronger echo removal)
  - `UVR-DeEcho-DeReverb.pth` (combined de-echo and de-reverb)
- `--reuse-stage1` - Skip Stage 1 if already run (saves time)

---

### Step 1: Transcribe Lyrics with Qwen3-ASR MLX

Generate a new transcription using the local MLX-based Qwen3-ASR model. This gives you word-level timestamps with context biasing optional.

```bash
uv run --extra poc_qwen3_mlx python poc/gen_lrc_qwen3_asr_local.py \
  --save-raw ./tmp_output \
  --output ./tmp_output/out.txt \
  --no-lyrics-context \
  --snap-algo dp \
  --force-rerun \
  <song_id>
```

**Key Options:**
- `--save-raw ./tmp_output` - Save raw ASR response for debugging
- `--output ./tmp_output/out.txt` - Output LRC file path
- `--no-lyrics-context` - Disable context biasing (use when lyrics are accurate)
- `--snap-algo dp` - Use dynamic programming algorithm for canonical line matching
  - Alternative: `greedy` (faster, less accurate for repetitive sections)
- `--force-rerun` - Ignore cached results and re-transcribe
- `--use-vocals` - Use vocal stems from cache if available (default: True). Audio source priority: `clean_vocals.flac` → `vocal.wav` → `audio.mp3` (see [Audio Source Resolution](#audio-source-resolution))
- `<song_id>` - Song ID (e.g., `dan_dan_ai_mi_249`)

**Snap Algorithm Options:**
- **greedy** - Faster, suitable for simple songs without chorus repeats
- **dp** (Dynamic Programming) - More accurate, handles chorus repeats and layered structures
  - Additional options for dp:
    - `--dp-skip-penalty 0.15` - Penalty for skipping canonical indices within a layer
    - `--dp-wrap-penalty 0.05` - Penalty for starting a new layer mid-sequence
    - `--dp-k-max 4` - Maximum number of layer wraps (for chorus repeats)

**Output:**
- `./tmp_output/out.txt` - Generated LRC file
- `./tmp_output/<song_id>_raw.json` - Raw ASR response (if `--save-raw` specified)

---

### Step 2: Align Lyrics with Qwen3 Forced Aligner

Align the known lyrics to audio using the Qwen3 forced alignment model. This leverages existing lyrics from the database and aligns them precisely to timestamps.

```bash
uv run --extra poc_qwen3_align python poc/gen_lrc_qwen3_force_align.py \
  --output tmp_output/aligned.txt \
  <song_id>
```

**Key Options:**
- `--output tmp_output/aligned.txt` - Output LRC file path
- `<song_id>` - Song ID (e.g., `dan_dan_ai_mi_249`)

**Additional Options:**
- `--device auto` - Device selection (auto/mps/cuda/cpu)
- `--dtype float32` - Data type (bfloat16/float16/float32)
- `--use-vocals` - Use vocal stems from cache if available (default: True). Audio source priority: `clean_vocals.flac` → `vocal.wav` → `audio.mp3` (see [Audio Source Resolution](#audio-source-resolution))
- `--language Chinese` - Language hint
- `--lyrics-file <path>` - Override lyrics with external file
- `--model-cache-dir <path>` - Custom model cache directory

**Note:** The `--offline` and `--download` flags are deprecated. The script now always downloads from R2 if the cache misses.

**Important Notes:**
- Maximum audio length is 5 minutes (Qwen3ForcedAligner limitation)
- Requires lyrics to exist in the database or be provided via `--lyrics-file`
- Uses clean vocal stems (`clean_vocals.flac`) for best alignment accuracy; falls back to `vocal.wav` then `audio.mp3`

**Output:**
- `tmp_output/aligned.txt` - Aligned LRC file

---

### Step 3: Upload LRC to Database

Upload the finalized LRC file to the R2 storage and update the song database record. This must be done **before** evaluating quality so that the eval step uses the canonical LRC from the database.

```bash
sow_admin audio upload-lrc <song_id> <lrc_file_path>
```

**Arguments:**
- `<song_id>` - Song ID (e.g., `dan_dan_ai_mi_249`)
- `<lrc_file_path>` - Path to LRC file (e.g., `tmp_output/aligned.txt`)

**What This Does:**
1. Uploads LRC file to R2 storage
2. Updates the song's recording metadata
3. Makes the LRC available to all application components

---

### Step 4: Evaluate LRC Quality

Score the LRC quality using TTS round-trip comparison to detect content errors and alignment issues. The lyrics used for evaluation always come from the canonical LRC stored in the database (same source as `sow_admin audio view-lrc <song_id>`), ensuring the eval reflects what users will actually see.

```bash
uv run --extra score_lrc_base python poc/score_lrc_quality.py \
  --report tmp_output/quality.md \
  --score-json tmp_output/quality.json \
  <song_id>
```

**Key Options:**
- `--report tmp_output/quality.md` - Path to write detailed quality report
- `--score-json tmp_output/quality.json` - Path to write JSON scores
- `<song_id>` - Song ID (e.g., `dan_dan_ai_mi_249`)

**Additional Options:**
- `--stem <path>` - Override vocal stem path
- `--threshold 0.6` - Minimum score threshold (default: 0.6 for PASS, lower for REVIEW)

**Output:**
- Report file with per-line scores and overall PASS/REVIEW status
- JSON file with detailed scoring data

**Interpreting Results:**
- **PASS (exit code 0)** - LRC quality is acceptable
- **REVIEW (exit code 1)** - Manual review needed
  - Check `<score_json>` for per-line scores
  - Review `<report_md>` for detailed analysis

---

## Complete Example Workflow

Here's a complete example showing all steps for a single song:

```bash
# Create temporary output directory
mkdir -p tmp_output

# Cache audio assets and get the cache directory
sow_admin audio cache dan_dan_ai_mi_249
CACHE_DIR=$(sow_admin audio cache dan_dan_ai_mi_249 2>/dev/null | grep "Cache location:" | awk '{print $NF}')

# Step 0: Generate clean vocal stems (recommended)
uv run --extra stem_separation python poc/gen_clean_vocal_stem.py \
  "$CACHE_DIR/audio/audio.mp3" \
  -o ./tmp_output/vocals

# Save clean vocals to cache as clean_vocals.flac
CLEAN_VOCALS=$(find ./tmp_output/vocals/stage2_dereverb -name "*No Echo*.flac" | head -1)
cp "$CLEAN_VOCALS" "$CACHE_DIR/clean_vocals.flac"

# Step 1: Transcribe with Qwen3-ASR
uv run --extra poc_qwen3_mlx python poc/gen_lrc_qwen3_asr_local.py \
  --save-raw ./tmp_output \
  --output ./tmp_output/out.txt \
  --no-lyrics-context \
  --snap-algo dp \
  --force-rerun \
  dan_dan_ai_mi_249

# Step 2: Align lyrics
uv run --extra poc_qwen3_align python poc/gen_lrc_qwen3_force_align.py \
  --output tmp_output/aligned.txt \
  dan_dan_ai_mi_249

# Step 3: Upload to database (must be done before evaluating)
sow_admin audio upload-lrc dan_dan_ai_mi_249 tmp_output/aligned.txt

# Step 4: Evaluate quality (uses canonical LRC from database)
uv run --extra score_lrc_base python poc/score_lrc_quality.py \
  --report tmp_output/quality.md \
  --score-json tmp_output/quality.json \
  dan_dan_ai_mi_249

# Review the quality report
cat tmp_output/quality.md

# Verify the LRC content matches what was uploaded
sow_admin audio view-lrc dan_dan_ai_mi_249
```

---

## Troubleshooting

### Issue: ModuleNotFoundError for mlx_qwen3_asr

**Cause:** Missing `poc_qwen3_mlx` extra installation

**Solution:**
```bash
uv sync --extra poc_qwen3_mlx
```

---

### Issue: ModuleNotFoundError for qwen_asr

**Cause:** Missing `poc_qwen3_align` extra installation

**Solution:**
```bash
uv sync --extra poc_qwen3_align
```

---

### Issue: Audio duration exceeds 5 minutes

**Cause:** Forced aligner has a 5-minute maximum

**Solution:** 
- Use only the verses/chorus section of the song
- Edit audio to be under 5 minutes before processing
- Consider using transcription-only approach (step 1) without forced alignment

---

### Issue: Quality scoring fails with "mlx-audio is not installed"

**Cause:** `mlx-audio` dependency conflict requires manual installation

**Solution:**
```bash
uv pip install "mlx-audio>=0.4.0" --prerelease=allow
```

---

### Issue: Lines not snapping correctly with DP algorithm

**Cause:** Default DP parameters may need tuning for your song structure

**Solution:** 
- Adjust `--dp-wrap-penalty` for chorus repeats
- Adjust `--dp-skip-penalty` for within-layer skipping
- Increase `--dp-k-max` if song has more than 4 chorus repeats

---

## Tips for Better Results

1. **Generate clean vocal stems first:** If `clean_vocals.flac` is not already cached, run Step 0 to generate it and copy it to the cache directory. This dramatically improves alignment accuracy for all subsequent steps. All scripts prefer `clean_vocals.flac` over `vocal.wav` and `audio.mp3` (see [Audio Source Resolution](#audio-source-resolution)).

2. **Use vocals-only audio:** The forced aligner works best with cleaned vocal stems (`clean_vocals.flac`). If clean vocals aren't available and cannot be generated, `vocal.wav` from the cache (downloaded via `sow_admin audio cache <song_id>`) is the next best option.

3. **Choose the right snap algorithm:
   - Simple songs without repetition: Use `--snap-algo greedy`
   - Songs with choruses/bridges: Use `--snap-algo dp` with adjusted penalties

3. **Review raw ASR output:** Check the `-save-raw` directory to see the raw transcription and identify any vocabulary errors.

4. **Iterate on quality:** If quality scoring fails, manually review the problematic lines and either:
   - Clean up the LRC file manually
   - Re-run with different snap parameters
   - Adjust the source lyrics in the database

5. **Context biasing:** Enable `--lyrics-context` when transcription might be uncertain, but disable it (`--no-lyrics-context`) when you want a fresh transcription.

6. **Evaluate against canonical LRC:** Always upload the LRC before evaluating, so the scoring compares against the canonical version in the database (same as `sow_admin audio view-lrc <song_id>`).

---

## Appendix: Available Extra Dependencies

For reference, here are the pyproject.toml extras used in this workflow:

```toml
# Unified: All LRC fixing tools in one extra (recommended)
fix_lrc = [
    "stream-of-worship[poc_qwen3_mlx,poc_qwen3_align,score_lrc_base]",
]

# Stem separation for clean vocal extraction (BS-Roformer + De-Echo)
stem_separation = [
    "audio-separator>=0.30.0",
    "onnxruntime>=1.17.0",
]

# Qwen3-ASR local MLX transcription (Apple Silicon only)
poc_qwen3_mlx = [
    "mlx",
    "mlx-qwen3-asr>=0.1.0",
    "mlx-audio>=0.4.0",
    "rapidfuzz>=3.0.0",
    "huggingface-hub>=0.20.0",
    "zhconv>=1.4.0",
]

# Qwen3 Forced Aligner dependencies
poc_qwen3_align = [
    "qwen-asr",
    "torch>=2.8.0,<2.9.0",
    "numpy>=2.0.2,<2.1.0",
    "pydub>=0.25.0",
    "typer>=0.12.0",
]

# LRC quality scoring via TTS round-trip (Apple Silicon only)
score_lrc_base = [
    "soundfile>=0.12.0",
    "librosa>=0.10.0",
    "numpy>=1.24.0",
    "torch>=2.8.0,<2.9.0",
    "transformers>=4.40.0",
    "zhconv>=1.4.0",
    "typer>=0.12.0",
    "scipy>=1.10.0",
    "tomli_w>=1.0.0",
    "boto3>=1.34.0",
]
```

**Note:** `mlx-audio>=0.4.0` is required (tested with 0.4.2) and must be installed separately with `--prerelease=allow` after installing `fix_lrc`.

---

## Related Documentation

- [Qwen3-ASR Documentation](./qwen3_asr.md)
- [Forced Alignment Guide](./forced_alignment.md)
- [LRC Quality Scoring](./lrc_quality_scoring.md)
