# Plan: Tighten cache behavior in `poc/gen_lrc_qwen3_asr_local.py`

## Context

The current cache flow in `gen_lrc_qwen3_asr_local.py` mostly works, but it fails two of the stated goals:

1. **On cache miss, it silently falls through to inference** (line 839's `if raw is None:` block unconditionally calls `transcribe_mlx_qwen3_asr`). The user wants inference to be gated strictly behind `--force-rerun` — a missing/invalid cache should stop and inform the user, not trigger a multi-minute model load + inference.
2. **When the cache file is absent**, `load_cached_transcription` returns `None` silently (line 296-297) with no message — the user should see why reuse failed.

Other goals are already satisfied:
- `from mlx_qwen3_asr import Session` is lazy-imported inside `transcribe_mlx_qwen3_asr` (line 45), so cache-hit path is already fast and loads no model code.
- Canonical lyrics matching (`canonical_line_snap`, `extract_segments`, `results_to_lrc`) runs after the cache branch regardless of cache hit/miss, so reuse still produces LRC output.
- `params` dict (lines 811-818) + `model`/`backend` in filename already keys the cache correctly on inference-affecting args.

## Changes

### 1. `load_cached_transcription` — inform user when cache file is missing
File: `poc/gen_lrc_qwen3_asr_local.py:287-297`

Replace the silent `return None` at line 296-297 with a typer.echo message:

```python
if not cache_path.exists():
    typer.echo(f"Cache not found at: {cache_path}", err=True)
    return None
```

The existing `typer.echo` warnings for version mismatch, missing `raw`, missing `text`, missing `segments`, and JSON parse errors (lines 304, 309, 313, 318, 324) already satisfy the "inform on invalid cache" goal and need no change.

### 2. `main` — gate inference strictly behind `--force-rerun`
File: `poc/gen_lrc_qwen3_asr_local.py:838` (between the cache-check block and the inference block)

Insert a guard before line 839's `if raw is None:`:

```python
# No valid cache available — only run inference if explicitly requested
if raw is None and not force_rerun:
    typer.echo(
        "No valid cached transcription available. "
        "Rerun with --force-rerun to perform qwen3-asr inference.",
        err=True,
    )
    raise typer.Exit(1)
```

This ensures:
- Cache hit → skip inference, continue to snap + LRC output (goal 5).
- Cache miss + no `--force-rerun` → inform user and exit without loading qwen3-asr libraries (goals 2, 4, 6).
- `--force-rerun` (with or without existing cache) → run inference (goal 3).

Note: when `reuse_transcription=False` without `--force-rerun`, this guard also blocks inference. That is the correct behavior given the stated goals (only `--force-rerun` authorizes inference).

## Critical files

- `poc/gen_lrc_qwen3_asr_local.py` — only file modified
  - Lines 296-297: add missing-cache message
  - Line 838 (before existing `if raw is None:`): add force-rerun guard

## Verification

From repo root, with a song that has lyrics in the catalog (e.g., `wo_yao_quan_xin_zan_mei_244`):

1. **Cache miss, no force-rerun** (expect: message + exit 1, no model load):
   ```
   uv run --extra app python poc/gen_lrc_qwen3_asr_local.py <song_id> --cache-dir /tmp/qwen3_cache_empty
   ```
   Expect stderr to show "Cache not found at: ..." and "Rerun with --force-rerun...". Exit code 1. No "Loading mlx-qwen3-asr" line.

2. **Cache miss, with force-rerun** (expect: full inference + cache write):
   ```
   uv run --extra app python poc/gen_lrc_qwen3_asr_local.py <song_id> --cache-dir /tmp/qwen3_cache_empty --force-rerun
   ```
   Expect model load, transcription, "Saved transcription to cache", full LRC output.

3. **Cache hit** (run step 2 first, then):
   ```
   uv run --extra app python poc/gen_lrc_qwen3_asr_local.py <song_id> --cache-dir /tmp/qwen3_cache_empty
   ```
   Expect "Loaded cached transcription from: ...", "Using cached transcription", no model load, snap runs, LRC output printed. Should complete in seconds.

4. **Corrupted cache** (write garbage to the cache file, then run without `--force-rerun`):
   Expect "Warning: Cache file invalid, ignoring: ..." followed by "Rerun with --force-rerun..." and exit 1.
