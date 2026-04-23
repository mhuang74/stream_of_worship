# POC: Chinese Worship Lyrics via Qwen3-ASR (Local, MLX on Apple Silicon)

## Context

**Problem.** Same as `specs/use_qwen3_asr_api_for_transcription_poc.md` — current production paths (YouTube-transcript + LLM, and faster-whisper + LLM alignment) miss Chinese characters on sung audio and mangle repeats. Taption-level accuracy is the target.

**Why a local variant of that plan.** The cloud POC uses **Qwen3-ASR-Flash** via DashScope. This spec covers the same hypothesis run **entirely locally on the Apple M2** (no API keys, no per-song cost, no data leaving the machine):

- **Open weights exist.** `Qwen/Qwen3-ASR-0.6B` and `Qwen/Qwen3-ASR-1.7B` were released on 2026-01-29 under Apache-2.0 on Hugging Face. Both are ASR models derived from Qwen3-Omni (audio-token LLM, not Whisper-style encoder-decoder). The 1.7B version is the accuracy SKU; 0.6B is the fast SKU.
- **Apple Silicon-native MLX ports are available.** Two relevant projects:
  - `moona3k/mlx-qwen3-asr` (PyPI: `mlx-qwen3-asr`) — ground-up MLX reimplementation, ~1.2 GB at 0.6B / ~3.4 GB at 1.7B. **Supports `context=` parameter for term biasing** and `return_timestamps=True`. This is the critical match for our hypothesis.
  - `Blaizzy/mlx-audio` (PyPI: `mlx-audio`) — broader MLX audio library with Qwen3-ASR under `mlx_audio.stt`. Uses 8-bit quantized weights (`mlx-community/Qwen3-ASR-{0.6B,1.7B}-8bit`). Returns `segments` with timestamps but **does not expose a context-biasing parameter**.
- **Flash vs open-weights.** The cloud `qwen3-asr-flash` SKU uses undisclosed serving optimizations and likely contains proprietary post-training; the open 1.7B model is the closest public proxy, reported as "competitive with the strongest proprietary commercial APIs." Expect somewhat lower ceiling than Flash, but still ahead of Whisper-large-v3 on Chinese singing.

**Goal of this POC.** Mirror the cloud POC but run transcription locally. Lets me:
1. Validate the biasing hypothesis without any API spend.
2. Establish a baseline for self-hosted production (if the cloud POC proves out, we may still want local as a fallback).
3. Compare Flash vs open-weight-1.7B on the same hand-reviewed songs.

Decision criteria: on a handful of hand-reviewed songs, does local Qwen3-ASR-1.7B with context biasing clearly beat current production (Whisper+LLM and YouTube paths) on character accuracy, repeat handling, and timestamp drift — even if it doesn't match cloud Flash?

## Deliverable

One new script: **`poc/gen_lrc_qwen3_asr_local.py`**.

Mirrors `poc/gen_lrc_qwen3_asr.py` (cloud variant, pending) and follows conventions of `poc/gen_lrc_whisper.py` exactly:

- Typer CLI, `song_id` as the positional argument (path-or-catalog-ID).
- Reuses `poc.utils.resolve_song_audio_path(song_id, use_vocals=...)` for audio + lyrics lookup.
- Reuses `poc.utils.format_timestamp` for LRC output.
- Output: LRC to stdout by default, or to `--output` path.
- Progress to stderr via `typer.echo(..., err=True)`.

### CLI

```
uv run --extra poc_qwen3_local python poc/gen_lrc_qwen3_asr_local.py <song_id> \
  [--use-vocals/--no-use-vocals]    # default True (matches gen_lrc_whisper.py)
  [--output <path>] [-o <path>]
  [--model 0.6B|1.7B]               # default 1.7B for accuracy
  [--backend mlx-qwen3-asr|mlx-audio]  # default mlx-qwen3-asr (needed for context)
  [--snap/--no-snap]                # default on — canonical-line fuzzy snap
  [--snap-threshold 0.60]
  [--lyrics-context/--no-lyrics-context]  # default on — biasing via context=
  [--context-max-chars 2000]        # truncate lyrics before sending as context
  [--save-raw <dir>]                # write asr_raw.json + diagnostic.md
  [--start <s>] [--end <s>]         # optional segment-only mode
```

### Pipeline

```
[1] Resolve inputs
    audio_path, lyrics = resolve_song_audio_path(song_id, use_vocals=use_vocals)
    if lyrics is None: fail fast (biasing/snap require catalog lyrics).
    lyrics_text = "\n".join(lyrics)

[2] Build biasing context (if --lyrics-context)
    # Unlike the cloud SDK which takes a full system message, mlx-qwen3-asr
    # exposes context= as a plain string. Keep it short and vocab-focused.
    context = lyrics_text
    if len(context) > context_max_chars:
        context = context[:context_max_chars]
        # Log truncation.

[3] Load model (first run triggers HuggingFace download — cache under ~/.cache/huggingface)
    if backend == "mlx-qwen3-asr":
        from mlx_qwen3_asr import Session
        session = Session(model=f"Qwen/Qwen3-ASR-{model}")
    else:  # mlx-audio
        from mlx_audio.stt import load
        session = load(f"mlx-community/Qwen3-ASR-{model}-8bit")
        # mlx-audio doesn't support context biasing — warn if --lyrics-context was set.

[4] Transcribe
    if backend == "mlx-qwen3-asr":
        result = session.transcribe(
            str(audio_path),
            context=context if lyrics_context else None,
            language="Chinese",
            return_timestamps=True,
        )
    else:
        result = session.generate(str(audio_path), language="Chinese")
    segments = result.segments  # each has start, end, text

    If --save-raw: dump result to <save_raw>/asr_raw.json.

[5] If --no-snap: emit LRC directly from segments and exit.

[6] Canonical-line snap (default)
    from rapidfuzz import fuzz
    canonical_lines = [l for l in lyrics if l.strip()]
    for seg in segments:
        scored = [(line, fuzz.token_set_ratio(seg["text"], line) / 100.0)
                  for line in canonical_lines]
        best_line, best_score = max(scored, key=lambda x: x[1])
        out_text = best_line if best_score >= snap_threshold else seg["text"]
        final.append((seg["start"], out_text, seg["text"], best_line, best_score,
                      best_score >= snap_threshold))
    Order preserved; repeats preserved naturally.

[7] Emit LRC
    Sort by start. Print `[mm:ss.xx] text` lines. Write to --output or stdout.

[8] If --save-raw: write diagnostic.md
    Table: start | asr_text | matched_canonical | score | replaced?
    Summary: replaced, kept, total_segments, avg_score, wall-clock time, RAM peak.
```

### Dependencies

Add a new extra in `pyproject.toml` (keep POC extras narrow — do not bloat `poc`):

```toml
[project.optional-dependencies]
poc_qwen3_local = [
  "mlx",
  "mlx-qwen3-asr",   # primary backend with context biasing
  "mlx-audio",       # fallback backend
  "rapidfuzz",       # also needed by the cloud POC — add once, share
  "huggingface_hub", # already transitively present, pin explicitly for clarity
]
```

macOS-only extra. Guard imports behind the `--backend` switch so the script fails loudly and early if MLX isn't installed.

Weights auto-download from Hugging Face on first run; point `HF_HOME` at a known cache dir to avoid filling `~/.cache` silently. First-run footprint:

- 0.6B mlx-qwen3-asr: ~1.2 GB weights + ~1.5 GB runtime RAM.
- 1.7B mlx-qwen3-asr: ~3.4 GB weights + ~4.5 GB runtime RAM.
- 8-bit mlx-audio variants: ~0.6 GB / ~1.8 GB.

All well within M2 RAM. No `Qwen3-ForcedAligner-0.6B` needed — MLX ports embed timestamp decoding in the main `transcribe` call.

## Key reuse

- `poc/utils.py::resolve_song_audio_path` — identical call shape to `gen_lrc_whisper.py` and the planned cloud POC.
- `poc/utils.py::format_timestamp` — LRC timestamp formatter.
- `poc/utils.py::extract_audio_segment` — used if `--start/--end` provided.
- Vocal stem production: out-of-band via `poc/gen_clean_vocal_stem.py`. The POC just consumes whatever `resolve_song_audio_path(..., use_vocals=True)` returns.
- Canonical-snap logic, `diagnostic.md` layout, and LRC emit code: **keep identical** between cloud and local POCs so results are directly comparable. If one lands first, copy-paste the post-ASR block verbatim.

## Risks & things to validate during POC

1. **Context biasing works on open weights too.** The cloud Flash SKU advertises biasing prominently; the open weights card does not. `mlx-qwen3-asr` exposes a `context=` arg, but whether the open model responds to it as strongly as Flash is unknown. First A/B: `--lyrics-context` vs `--no-lyrics-context` on the same song, same weights.
2. **Open-weight accuracy vs Flash.** Benchmarks say 1.7B is "competitive" with commercial APIs; expect it to be close to but possibly below Flash quality. Acceptable if it still clearly beats Whisper-large-v3 on our test set.
3. **MLX port maturity.** `mlx-qwen3-asr` is a single-maintainer project (as of 2026-Q1). Pin a specific PyPI version in `pyproject.toml`. If it breaks, fall back to `mlx-audio` (no biasing — will be a cleaner test of the un-biased ceiling).
4. **Cold-start download time.** First run downloads multi-GB weights; subsequent runs are fast. Warm-up the cache manually before timing runs.
5. **Long songs.** Qwen3-ASR handles long audio natively in both MLX ports (unlike the cloud Flash 5-min cap). No chunking needed. Validate with a 6–7 min worship song.
6. **Vocals-stem vs mix.** Same A/B as the cloud POC — toggle `--use-vocals`.
7. **Response schema drift.** Both MLX ports are new; schema (`segments[]` field names) may differ between versions. Always save `asr_raw.json` on first runs of each version.

## Verification

On 3–5 hand-reviewed test songs (short/long, vocal-heavy/band-heavy):

1. `--no-snap --save-raw` → inspect `asr_raw.lrc` vs hand-reviewed. Eyeball CER and timestamp drift.
2. Default (snap on) → compare `final.lrc` to hand-reviewed.
3. A/B: `--lyrics-context` vs `--no-lyrics-context` (same weights, same audio).
4. A/B: `--model 0.6B` vs `--model 1.7B`.
5. A/B: `--use-vocals` vs `--no-use-vocals`.
6. Cross-compare: this POC's output vs the cloud-Flash POC output on the same songs. Note per-song wall-clock: cloud is ~few seconds; local 1.7B on M2 should still be well under real-time for a 4-min song.
7. Load the resulting `.lrc` into `src/stream_of_worship/app/screens/lyrics_preview.py` against the MP3 to see timing in-app.

Acceptance to move toward production: local 1.7B + snap + biasing beats current production LRC clearly on character accuracy and repeat handling. Even if cloud Flash wins the absolute quality race, a working local path is valuable as a cost-free fallback and as an offline/privacy option.

## Out of scope

- No changes to `services/analysis/`, `src/stream_of_worship/`, or admin tool.
- No vocal-separation invocation inside the POC (run `poc/gen_clean_vocal_stem.py` separately if needed).
- No batch/backfill script — one song per invocation.
- No automated CER/WER/MAE computation. Eyeballing `diagnostic.md` is fine at POC stage.
- No CUDA path. This POC is explicitly Apple Silicon / MLX. For CUDA usage, use the cloud POC or stand up a separate vLLM-based POC.
