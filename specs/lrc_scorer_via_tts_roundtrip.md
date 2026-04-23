# LRC Accuracy Scorer via Qwen3-TTS Round-Trip

## Context

LRC files are being generated automatically from vocal stems using the
Qwen3-ASR pipeline in `poc/gen_lrc_qwen3_asr_local.py` (and its DashScope
sibling). Quality varies (cf. `tmp_output/zhe_shi_sheng_jie_zhi_di_259.lrc`:
the pipeline injected 4 extra chorus lines after line 18). The team needs an
**automated quality score per LRC file** so bad ones can be flagged for
manual re-work — at scoring time only the vocal stem and the generated LRC
exist (no hand-verified transcription, no canonical lyrics).

Approach: **audio round-trip comparison, fully local on Apple Silicon**.
Synthesize speech from each LRC line via **Qwen3-TTS on MLX** (runs on
the M2 laptop, no DashScope calls), then compare the synthesized clip
against the matching time window of the original vocal stem in a
**phonetic embedding space** (speaker/pitch-invariant). Lines whose
phonetic content doesn't appear in the stem at the claimed time are
flagged. Ad-libs / background phrases in the stem that aren't in the
LRC are naturally ignored because the scorer only interrogates
LRC-derived windows.

## Approach

### 1. Inputs & outputs

- CLI: `poc/score_lrc_quality.py` (Typer, consistent with `gen_lrc_*.py`).
- Required args: `--stem <path.flac>` and `--lrc <path.lrc>`.
- Optional: `--threshold 0.60` (PASS/REVIEW cutoff), `--tts-cache-dir`,
  `--report <path.md>`, `--score-json <path.json>`,
  `--tts-model mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16`,
  `--tts-voice Chelsie`.
- Outputs:
  - `<lrc>.score.json` — per-line and overall scores.
  - `<lrc>.report.md` — human-readable report, problem lines listed.
  - Exit code 0 = PASS, 1 = REVIEW (so it composes into CI/batch jobs).

### 2. Algorithm (per-line)

For each `LRCLine(time, text)`:

1. **Synthesize** via MLX Qwen3-TTS locally on the M2:
   ```python
   from mlx_audio.tts.utils import load_model
   model = load_model("mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16")
   results = list(model.generate(text=line.text, voice="Chelsie",
                                  language="Mandarin"))
   tts_audio_mx = results[0].audio       # mx.array
   sample_rate = model.sample_rate
   ```
   The model is loaded once per run (singleton). Each line's TTS is cached
   to `~/.cache/qwen3_tts/<sha1(model+voice+text)>.wav` so re-runs are
   cheap. Traditional Chinese is normalized to Simplified via `zhconv`
   (already a repo dep) before synthesis to maximize the TTS's character
   coverage.
2. **Extract stem window**: `stem_window = stem[time : next_line_time]`
   (or `time + max_window=15s` for the last line).
3. **Phonetic embeddings**: run both `tts_audio` and `stem_window` through
   a frozen self-supervised model (`facebook/wav2vec2-xls-r-300m` — multilingual,
   widely available on HF, mean-pooled last hidden state → single vector per clip).
4. **Score**: `line_score = cosine_sim(embed(tts), embed(stem_window))`,
   range ~[0, 1]. Also compute a DTW-aligned version on framewise embeddings
   for lines where the simple cosine is near threshold, to rule out timing slop
   being mistaken for content error. (Only invoked when `0.4 ≤ score ≤ 0.7`.)
5. **Order sanity**: the LRC is already time-ordered; additionally record
   `peak_offset` = time within the window where cosine sim with TTS peaks
   (via sliding inner product on framewise embeddings). If the peak is
   consistently late across many lines, it signals systematic timestamp drift
   — reported but not used in the numeric score per user direction.

### 3. Overall scoring & flag

- `overall = mean(line_scores)`; also expose `min`, `p10`, and
  `num_below_threshold`.
- Flag logic: `REVIEW` if `overall < threshold` OR `num_below_threshold / N > 0.15`.
  Otherwise `PASS`.
- Threshold default 0.60 is a starting guess — to be calibrated on
  the two sample LRCs already in `tmp_output/`. The 89.3% (problematic) and
  100% (clean) cases from the existing line-level comparison give known
  good/bad anchors for calibration.

### 4. Code components

New module **`poc/score_lrc_quality.py`**:

- `MLXQwen3TTS` singleton wrapper:
  - `load()` — `mlx_audio.tts.utils.load_model(model_id)` once per run.
  - `synth(text, voice) -> (np.ndarray float32 mono, sample_rate)` —
    calls `model.generate(text, voice, language="Mandarin")`, converts the
    `mx.array` output to numpy, writes to the disk cache.
- `load_stem_window(stem_path, start, end) -> np.ndarray` — `soundfile.read`
  (already in `tui` extra). Resample to 16 kHz mono for the embedding model.
- `embed_audio(audio_16k) -> np.ndarray` — wav2vec2-xls-r feature extractor +
  model, mean-pooled hidden states. Lazy-loaded singleton.
- `score_line(stem_win, tts_wav) -> dict{score, peak_offset, dtw_score?}`.
- `score_lrc(stem_path, lrc_path, ...) -> ScoreReport`.
- `write_report(report, md_path, json_path)`.

Reused from existing code:
- `src/stream_of_worship/admin/services/lrc_parser.py::parse_lrc()` — LRC parser.
- `poc/utils.py::format_timestamp` — for pretty-printing line times in the report.
- Typer-app pattern from `poc/gen_lrc_qwen3_asr.py`.

### 5. Dependencies

Add a new optional-dep group `score_lrc` to `pyproject.toml` (Apple Silicon
only, like the existing `poc_qwen3_local` group):

```
score_lrc = [
    "mlx",                   # already pulled via poc_qwen3_local
    "mlx-audio>=0.1.0",      # already pulled via poc_qwen3_local — provides Qwen3-TTS
    "soundfile>=0.12.0",
    "librosa>=0.10.0",
    "numpy>=1.24.0",
    "torch>=2.8.0,<2.9.0",
    "transformers>=4.40.0",  # wav2vec2-xls-r feature extractor + model
    "zhconv>=1.4.0",         # Traditional → Simplified normalization
    "typer>=0.12.0",
]
```

Install via `uv sync --extra score_lrc`. First run downloads the
`mlx-community/Qwen3-TTS-12Hz-0.6B-Base-bf16` model (~1GB quantized)
and `facebook/wav2vec2-xls-r-300m` (~1.2GB) from HuggingFace.
Both cache to `~/.cache/huggingface/hub/` and are reused across runs.

### 6. Critical files

- **New**: `poc/score_lrc_quality.py`
- **Modified**: `pyproject.toml` (add `score_lrc` optional dep group)
- **Read-only reuse**:
  - `poc/gen_lrc_qwen3_asr.py` (DashScope client pattern)
  - `src/stream_of_worship/admin/services/lrc_parser.py` (`parse_lrc`)
  - `poc/utils.py` (`format_timestamp`)

## Verification

1. **Smoke test on the two sample LRCs** (runs fully on-device, no API key):
   ```
   PYTHONPATH=. uv run --extra score_lrc python poc/score_lrc_quality.py \
     --stem tmp_input/wo_yao_clean_vocals.flac \
     --lrc  tmp_output/wo_yao.lrc \
     --report tmp_output/wo_yao.quality.md \
     --score-json tmp_output/wo_yao.quality.json
   ```
   Expected: overall ≥ 0.7, `PASS` (this LRC is 100% correct against the
   verified reference).

2. **Positive-negative contrast**:
   ```
   … --stem tmp_input/zhe_shi_sheng_jie_zhi_di_259_clean_vocal.flac \
     --lrc  tmp_output/zhe_shi_sheng_jie_zhi_di_259.lrc …
   ```
   Expected: `REVIEW`, with lines 26 (`在祢大能榮耀光中` tagged as
   `我全心來敬拜祢主...` — wrong text) and the 4 injected EXTRA lines
   (28-31 per `zhe_shi_sheng_jie_zhi_di_259_comparison.txt`) scoring lowest.
   If these don't rank at the bottom, the approach is mis-calibrated —
   re-tune threshold or switch to DTW-based per-line scoring.

3. **Cache behavior**: re-run same command; second run should be ≪ first-run
   wall-clock (all TTS cached, only re-embed + compare).

4. **Runtime sanity**: on first run, cold download of TTS + wav2vec2
   dominates; steady-state should be a few seconds per line (TTS
   synth + 2× wav2vec2 inferences). Log RTF. No API/cloud cost.

5. **Non-goal**: timestamp-accuracy scoring (confirmed skipped for v1).

## Known risks / open points

- Qwen3-TTS may pronounce unfamiliar Traditional characters awkwardly;
  normalize to Simplified via `zhconv` (already a dep) before synth.
- wav2vec2-xls-r (~1.2GB) + Qwen3-TTS-12Hz-0.6B-Base-bf16 (~1GB) are
  pulled from HF on first run — warn at CLI startup so it's not surprising.
- Line time-window = (next_line_time - this_line_time) can be long during
  instrumental breaks; cap at 15s to avoid diluting the embedding.
- Threshold calibration is empirical; start at 0.60 and tune on the two
  anchor files.
- If MLX Qwen3-TTS quality is insufficient on this specific Mandarin
  worship vocabulary, fall back to `mlx-community/Qwen3-TTS-12Hz-1.7B-...`
  (larger variant) — the CLI's `--tts-model` flag makes this a one-line
  swap, no code change.
