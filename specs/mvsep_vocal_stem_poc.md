# POC: Two-Stage Vocal Extraction via MVSEP Cloud API

## Context

`poc/gen_clean_vocal_stem.py` performs two-stage vocal extraction (BS Roformer → De-Echo) using local models via `python-audio-separator`. This requires GPU resources and large model downloads. We want a cloud-based alternative using the MVSEP API so extraction can run on any machine without local ML dependencies.

The new script `poc/gen_clean_vocal_stem_mvsep.py` mirrors the local POC's structure and output format, making the two interchangeable.

## Pipeline

| Stage | MVSEP `sep_type` | `add_opt1` (default) | `add_opt2` | Purpose |
|-------|------------------|----------------------|------------|---------|
| 1 | `40` (BS Roformer) | `81` (ver 2025.07, SDR 11.89) | — | Vocal/instrumental separation |
| 2 | `22` (Reverb Removal) | `0` (FoxJoy MDX23C) | `1` (use as is) | Remove reverb from extracted vocals |

Output format: FLAC 16-bit (`output_format=2`).

## File

`poc/gen_clean_vocal_stem_mvsep.py` — single self-contained script, no new modules.

## Dependencies

Only `requests` (already in `[admin]` extra). No new dependencies.

## API Token

Resolved as: `--api-token` CLI arg → `MVSEP_API_KEY` env var → error.

## Implementation

### Constants

```
MVSEP_API_BASE = "https://mvsep.com/api/separation"
POLL_INITIAL_INTERVAL = 5.0 seconds
POLL_MAX_INTERVAL = 30.0 seconds
POLL_BACKOFF_FACTOR = 1.5
DEFAULT_TIMEOUT = 900 seconds (15 min per stage)
```

### Functions

#### `submit_job(audio_path, api_token, sep_type, add_opt1, add_opt2, output_format) -> str`
- POST multipart form to `/create` with `audiofile` binary + params
- Returns job hash from `data.hash`
- Raises `RuntimeError` on API error

#### `poll_job(job_hash, timeout) -> dict`
- GET `/get?hash=<hash>` in a loop with exponential backoff
- Prints status each cycle: `"  Status: {status} (elapsed {elapsed:.0f}s)"`
- Returns full response data on `done`
- Raises `TimeoutError` or `RuntimeError` on timeout/failure

#### `download_files(file_entries, output_dir) -> list[Path]`
- `file_entries` is `data.files` from the MVSEP response (list of dicts with download URLs)
- Stream-downloads each file to `output_dir`
- Returns list of local `Path` objects

#### `extract_vocals_two_stage_mvsep(input_path, output_dir, api_token, vocal_model, dereverb_model, output_format, reuse_stage1, timeout) -> dict`

Core pipeline, mirrors `extract_vocals_two_stage()` from the local POC:

1. **Stage 1** — `stage1_dir = output_dir / "stage1_vocal_separation"`
   - If `reuse_stage1`: scan dir for file with "vocals" (case-insensitive) in name; skip if found
   - Otherwise: `submit_job(input_path, sep_type=40, add_opt1=vocal_model)`
   - `poll_job` → `download_files` → identify vocals file
   - Record `process_time_s` (covers submit + poll + download)

2. **Stage 2** — `stage2_dir = output_dir / "stage2_dereverb"`
   - `submit_job(vocals_file, sep_type=22, add_opt1=dereverb_model, add_opt2=1)`
   - `poll_job` → `download_files` → identify dry vocals file
   - Heuristic: file with "No Reverb" or "noreverb" in name, else first file

3. **Summary** — print output files, total time

4. **Returns** `results` dict matching local POC shape:
   ```python
   {
       "input": str,
       "stages": {
           "stage1": {"model": str, "process_time_s": float, "outputs": [...], "vocals_file": str, "instrumental_file": str},
           "stage2": {"model": str, "process_time_s": float, "outputs": [...], "dry_vocals_file": str, "reverb_file": str},
       },
       "total_time_s": float,
   }
   ```

### CLI Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `input` (positional) | `Path` | required | Input audio file |
| `-o` / `--output-dir` | `Path` | `./vocal_extraction_output/<stem>` | Output directory |
| `--api-token` | `str` | None | MVSEP API token (fallback for env var) |
| `--vocal-model` | `int` | `81` | Stage 1 BS Roformer variant |
| `--dereverb-model` | `int` | `0` | Stage 2 reverb removal variant |
| `--output-format` | `int` | `2` | MVSEP output format code |
| `--timeout` | `float` | `900` | Max seconds to wait per stage |
| `--reuse-stage1` | flag | `False` | Reuse existing Stage 1 vocals |

Epilog documents available model variants for `--vocal-model` and `--dereverb-model`.

### Error Handling

| Scenario | Behavior |
|----------|----------|
| Missing API token | Print error, exit 1 |
| Input file not found | Print error, exit 1 |
| API HTTP error | `RuntimeError` → print, exit 1 |
| Job failed / not_found | `RuntimeError` → print, exit 1 |
| Poll timeout | `TimeoutError` → print, exit 1 |
| No vocals in Stage 1 output | Print error, return partial results |

## Verification

```bash
# Set API token
export MVSEP_API_KEY="your_token"

# Basic run
uv run --extra admin python poc/gen_clean_vocal_stem_mvsep.py poc/audio/some_song.mp3

# Custom output dir + model
uv run --extra admin python poc/gen_clean_vocal_stem_mvsep.py poc/audio/some_song.mp3 -o /tmp/mvsep_test --vocal-model 29

# Reuse stage 1
uv run --extra admin python poc/gen_clean_vocal_stem_mvsep.py poc/audio/some_song.mp3 --reuse-stage1

# Verify outputs
ls vocal_extraction_output/<stem>/stage1_vocal_separation/
ls vocal_extraction_output/<stem>/stage2_dereverb/
cat vocal_extraction_output/<stem>/extraction_results.json
```

Expected: vocals + instrumental files from Stage 1, dry vocals + reverb files from Stage 2, timing in `extraction_results.json`.
