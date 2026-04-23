# Cache raw ASR output in `gen_lrc_qwen3_asr_local.py`

## Context

`poc/gen_lrc_qwen3_asr_local.py` caches the wrong thing. Today
`save_cached_transcription` persists the **post-processed phrase segments**
produced by `extract_segments()`; the raw `TranscriptionResult` (top-level
`text` plus per-character `segments`) is discarded after extraction. This
creates three problems:

1. **Any change downstream of the model forces a GPU rerun.** Tweaking
   `extract_segments`, the phrase-splitting punctuation set, or choosing to
   group segments differently invalidates the cache even though the model
   output is unchanged. A Qwen3-ASR 1.7B pass on this song takes ~110 s on
   Apple Silicon; this cost is being paid unnecessarily.

2. **`--save-raw` only writes `asr_raw.json` on cache-miss runs.** The write
   lives inside the `if not used_cache:` block (current lines ~825–837). Once
   a song is cached, there's no way to regenerate `asr_raw.json` for
   inspection without `--force-rerun`.

3. **Cache key does not cover inference parameters.** The current filename is
   `{song_id}_{model}_transcription.json`. Parameters that change ASR output
   are missing from the key:
   - `backend`
   - `lyrics_context` and `context_max_chars` (context biasing changes the
     transcription)
   - `--start` / `--end` (different audio slice → different ASR)
   - `use_vocals` (vocals stem vs full mix → different ASR)

   Two runs with different values for any of these would silently hit the
   same cache file and return stale output.

Additionally, the current cache validator rejects a cache when fewer than
50 % of segments have non-empty text (lines ~283–289). That heuristic was a
workaround for the now-fixed per-character extraction bug and can wrongly
reject legit caches of short-phrase songs. Drop it as part of this change.

The snap-algorithm redesign (fragment merge, sequential walk, opening
anchor, short-fragment scoring, dedup) is already implemented in the
working copy; it is **not** part of this spec.

## Goals

- Cache the raw model output (the `TranscriptionResult` rendered as a plain
  dict — same shape `asr_raw.json` already has on disk).
- Include all ASR-affecting parameters in the cache key, so a parameter
  change is a cache miss, and a parameter change that doesn't affect ASR
  (e.g. `--snap-threshold`) is a cache hit.
- `--save-raw` writes `asr_raw.json` on every run (hit or miss), always
  sourced from the cached raw dict.
- Extraction (`extract_segments`) runs on every path — from freshly produced
  raw or from cache — so `extract_segments` edits take effect without a
  model rerun.

## Non-goals

- Migrating old v1 cache files. POC-scale cache; orphan v1 files sit in
  `~/.cache/qwen3_asr/` unused and the user can `rm -rf` when convenient.
- Changing `canonical_line_snap` or `write_diagnostic`. Already handled.
- Extending caching to the cloud (API) variant. Separate file, separate
  concern.
- Eviction / size limits on the cache directory.

## Cache file schema (`cache_version: 2`)

```json
{
  "cache_version": 2,
  "model": "1.7B",
  "backend": "mlx-qwen3-asr",
  "params": {
    "use_vocals": true,
    "lyrics_context": false,
    "context_max_chars": 2000,
    "start": 0.0,
    "end": null,
    "language": "Chinese"
  },
  "wall_time": 110.92,
  "timestamp": 1713456789.123,
  "raw": {
    "text": "嗯。我要一心奉献你，…",
    "language": "Chinese",
    "segments": [
      { "text": "我", "start": 17.168375, "end": 17.488375 },
      ...
    ],
    "chunks": null,
    "speaker_segments": null
  }
}
```

- `cache_version` — integer; bump when the schema breaks. Readers treat any
  other value as a miss.
- `model`, `backend` — duplicated in payload for inspection even though they
  are also in the filename; cheap.
- `params` — the dict that goes into the key hash. Kept in the payload so a
  cache inspector can see what was used without reverse-hashing.
- `wall_time`, `timestamp` — diagnostics only. Not consulted for validity.
- `raw` — exact dict shape produced by `raw_to_dict(result)`; mirrors what
  `asr_raw.json` already contains. Validator requires `raw` be a dict with
  non-empty string `text` and a non-empty list `segments`.

## Cache key

Filename:

```
{safe_song_id}_{model}_{backend}_{params_hash8}.json
```

where `params_hash8` = first 8 hex chars of
`hashlib.sha256(json.dumps(params, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()`.

Eight hex chars = 32 bits. Collision odds are astronomical for a local
per-song cache; correctness is preserved by the fact that the full `params`
dict is also stored inside the file — on the rare hit-with-mismatch case we
could check equality, but for a POC the hash alone is sufficient. (If this
ever moves to a production path, add a `params == cache["params"]` guard
before accepting the cache.)

## Function changes in `poc/gen_lrc_qwen3_asr_local.py`

### New helpers

- `raw_to_dict(result) -> dict` — centralizes the "convert
  `TranscriptionResult` dataclass to plain dict" logic currently inlined in
  `main` at the `asr_raw.json` write site. Prefers `result.__dict__` (works
  for the frozen dataclass); falls back to `result` if already a dict. Run
  through `json.loads(json.dumps(d, ensure_ascii=False, default=str))` so the
  returned dict is JSON-round-trippable (stringifying anything exotic like
  numpy scalars). The existing `asr_raw.json` write code at lines ~829–834
  moves to call this helper.

- `compute_params_hash(params: dict) -> str` — returns 8-char SHA256 prefix
  of canonical JSON of `params`.

### Updated signatures

- `cache_file_name(cache_dir, song_id, model, backend, params_hash)` — add
  `params_hash` arg; build filename as above.

- `save_cached_transcription(cache_path, raw, model, backend, params, wall_time)`
  — persist `{cache_version, model, backend, params, wall_time, timestamp, raw}`.
  No longer takes `segments`.

- `load_cached_transcription(cache_path) -> Optional[dict]` — returns the
  **raw dict** (not segments) or `None`. Validations:
  - file exists, JSON parses
  - `cache_version == 2`
  - `raw` is a dict with non-empty `text: str` and non-empty `segments: list`

  Drop the old "<50 % empty text" heuristic. If `extract_segments` later
  returns empty from cached raw (shouldn't happen), the caller warns and
  falls through to rerun.

### `main` flow

Conceptual flow after the change:

```
params = {
    "use_vocals": use_vocals,
    "lyrics_context": lyrics_context,
    "context_max_chars": context_max_chars,
    "start": start,
    "end": effective_end,
    "language": "Chinese",
}
params_hash = compute_params_hash(params)
cache_path = cache_file_name(cache_dir, song_id, model, backend, params_hash)

raw: Optional[dict] = None
wall_time = 0.0
used_cache = False

if reuse_transcription and not force_rerun:
    cached_raw = load_cached_transcription(cache_path)
    if cached_raw is not None:
        raw = cached_raw
        used_cache = True
        # wall_time left at 0.0 — diagnostic will mark as cached

if raw is None:
    # ... existing audio-slice extraction + Session.transcribe() ...
    result = transcribe_mlx_qwen3_asr(...)
    wall_time = time.time() - wall_time_start
    raw = raw_to_dict(result)
    save_cached_transcription(cache_path, raw, model, backend, params, wall_time)

# UNCONDITIONAL asr_raw.json write (works on cache hit and miss)
if save_raw:
    save_raw.mkdir(parents=True, exist_ok=True)
    (save_raw / "asr_raw.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

segments = extract_segments(raw)
if not segments:
    typer.echo("Error: No segments extracted from ASR result", err=True)
    raise typer.Exit(1)
```

(Exact edits stay in the existing `main`; this is the shape, not a
cut-and-paste replacement.)

Notes:

- `extract_segments` is called on the raw dict in both paths, so a
  phrase-splitting change takes effect immediately without rerun.
- `used_cache` still drives the stderr "Using cached transcription" message.
- Existing `--force-rerun` semantics unchanged: skips the cache read, still
  writes the new cache.
- The `segment_path` cleanup in the `finally` block stays in the fresh-run
  branch; no change there.

## Scope fences

- Don't modify `extract_segments`, `canonical_line_snap`, or
  `write_diagnostic`.
- Don't touch unrelated issues in this file:
  - Unreachable `typer.echo` after `raise typer.Exit(1)` at lines ~751–755.
  - Dead `os` import at line 14.
  Call these out in the commit message as follow-ups if worth a cleanup
  pass.
- The cloud variant (`poc/gen_lrc_qwen3_asr.py` if present) is out of scope.

## Verification

Run from repo root; expect `~/.cache/qwen3_asr/` to be empty at the start
(`rm -rf ~/.cache/qwen3_asr/` before step 1 to make it deterministic).

1. **Cache miss — first run:**
   ```
   uv run --extra transcription python poc/gen_lrc_qwen3_asr_local.py \
     wo_yao_yi_xin_cheng_xie_mi_247 \
     --save-raw ./tmp_output -o ./tmp_output/out.txt \
     --no-lyrics-context --force-rerun
   ```
   Expect:
   - `~/.cache/qwen3_asr/wo_yao_yi_xin_cheng_xie_mi_247_1.7B_mlx-qwen3-asr_<8hex>.json`
     exists.
   - File has `cache_version: 2`, `params` reflecting the CLI flags,
     `raw.segments` ~299 per-character dicts, `raw.text` non-empty.
   - `tmp_output/asr_raw.json` exists and is byte-identical to `raw` in the
     cache file (a `jq '.raw' cache.json > a.json && diff a.json asr_raw.json`
     sanity check).

2. **Cache hit — same params:**
   ```
   uv run --extra transcription python poc/gen_lrc_qwen3_asr_local.py \
     wo_yao_yi_xin_cheng_xie_mi_247 \
     --save-raw ./tmp_output -o ./tmp_output/out.txt \
     --no-lyrics-context
   ```
   Expect:
   - Stderr contains "Using cached transcription".
   - No model-load stderr, finishes in seconds (no GPU time).
   - `tmp_output/asr_raw.json` rewritten identically to step 1.
   - `out.txt` identical to step 1.

3. **ASR-affecting param changed:**
   ```
   # drop --no-lyrics-context → uses catalog lyrics as context
   uv run --extra transcription python poc/gen_lrc_qwen3_asr_local.py \
     wo_yao_yi_xin_cheng_xie_mi_247 \
     --save-raw ./tmp_output -o ./tmp_output/out.txt
   ```
   Expect:
   - New cache file with a different `params_hash8`.
   - Step 1's cache file untouched.
   - Model reruns.

4. **Non-ASR param changed:**
   ```
   uv run --extra transcription python poc/gen_lrc_qwen3_asr_local.py \
     wo_yao_yi_xin_cheng_xie_mi_247 \
     --save-raw ./tmp_output -o ./tmp_output/out.txt \
     --no-lyrics-context --snap-threshold 0.5
   ```
   Expect:
   - Hits the step 1 cache (same `params_hash8`).
   - No model rerun. `out.txt` may differ because snap uses a different
     threshold, but `asr_raw.json` unchanged.

5. **`extract_segments` edit takes effect from cache:**
   - Add a harmless print statement inside `extract_segments`.
   - Rerun step 2 command. The print fires; no model reload.
   - (Revert the print afterward.)

6. **Orphan v1 files tolerated:**
   - Manually place a v1-shaped file (no `cache_version`, `segments` key at
     top level) in the cache dir under an old filename. Rerun step 1's
     command. Behavior: new v2 file created, v1 file untouched (different
     filename due to new key scheme).

## Risks / edge cases

- **Params hash collision**: 32 bits → effectively zero for a per-song
  cache. If/when this moves to production, add a `params ==
  cache["params"]` equality check before accepting the cache.
- **`raw_to_dict` stability across `mlx_qwen3_asr` versions**: current
  library emits a frozen dataclass (`TranscriptionResult` in
  `.venv/…/mlx_qwen3_asr/transcribe.py:56-73`). `result.__dict__` works
  today. If a future version changes the shape, `json.dumps(..., default=str)`
  still yields *something* and `extract_segments` already tolerates
  dict-or-object via its internal `_get_field`. Low risk.
- **Orphan v1 cache files**: accumulate until user deletes them. Acceptable
  for POC.
- **Cache dir growing**: one file per (song × model × backend × params
  combination). POC scale; not addressed here. If this moves to production,
  add an age- or size-based eviction.
- **Hash changes across Python versions**: SHA256 is deterministic; canonical
  JSON via `sort_keys=True, ensure_ascii=False` is deterministic; safe
  across Python versions.
