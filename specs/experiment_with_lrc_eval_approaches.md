# LRC Signal Experiment — Run Book

## What this experiment does

Computes seven per-line timing signals on two reference songs to identify which
signal(s) best separate a known-bad LRC (timing drift) from a known-good one.
No thresholds are set here — the goal is raw signal data for comparison.

**Reference songs:**
- **Bad:** `dan_dan_ai_mi_249` — audibly drifting; `score_lrc_quality.py`
  incorrectly scores it 0.950 due to mean-pooling + repetitive phrases.
- **Good:** `wo_yao_yi_xin_cheng_xie_mi_247` — hand-verified timing.

**Script:** `poc/experiment_lrc_signals.py`

**Outputs** (committed / reproducible):
- `poc/experiment_output/dan_dan_ai_mi_249/signals.csv`
- `poc/experiment_output/wo_yao_yi_xin_cheng_xie_mi_247/signals.csv`
- `poc/experiment_output/signals.md` — cross-song summary with spot-check tables

---

## How to repeat the experiment

### 1. Ensure assets are in local cache

Both songs must have their vocal stem (`stems/vocals.wav`) and canonical LRC
(`lrc/lyrics.lrc`) in `~/.cache/stream-of-worship/<hash_prefix>/`.

```bash
sow-admin audio cache dan_dan_ai_mi_249
sow-admin audio cache wo_yao_yi_xin_cheng_xie_mi_247
```

If `sow-admin` is not on PATH, run via uv:
```bash
PYTHONPATH=src uv run --extra admin python -m stream_of_worship.admin.main audio cache dan_dan_ai_mi_249
```

Hash prefixes (for reference / direct R2 fallback):
- `dan_dan_ai_mi_249` → `5b445438847a`
- `wo_yao_yi_xin_cheng_xie_mi_247` → `c105e75972f7`

### 2. Populate TTS cache

The experiment reads pre-synthesized TTS audio from
`~/.cache/qwen3_tts/<sha1>.wav`. These are produced by `score_lrc_quality.py`.
As of the initial run, both songs are fully cached (30 entries covering all
unique line texts). If TTS cache misses occur, run:

```bash
PYTHONPATH=src:. uv run --extra score_lrc python poc/score_lrc_quality.py dan_dan_ai_mi_249
PYTHONPATH=src:. uv run --extra score_lrc python poc/score_lrc_quality.py wo_yao_yi_xin_cheng_xie_mi_247
```

Note: `score_lrc` extra requires `mlx-audio>=0.4.0` installed separately:
```bash
uv pip install "mlx-audio>=0.4.0" --prerelease=allow
```

### 3. (Optional) Install MFA for the MFA drift signal

MFA (`mfa_drift`) is a stub that returns NaN unless MFA is installed. To enable it:

```bash
conda install -c conda-forge montreal-forced-aligner
mfa model download acoustic mandarin_mfa
mfa model download dictionary mandarin_mfa
```

The stub detects `mfa` on PATH via `mfa version` and prints install instructions
if absent. Skip `--skip-mfa` only if MFA is installed and the Mandarin model downloaded.

### 4. Run the experiment

**Full run** (all signals including qwen3-forcedaligner):
```bash
PYTHONPATH=src:. uv run --extra score_lrc_base --extra poc_qwen3_align python poc/experiment_lrc_signals.py
```

**Without qwen3** (score_lrc_base only — faster, no forced aligner):
```bash
PYTHONPATH=src:. uv run --extra score_lrc_base python poc/experiment_lrc_signals.py --skip-qwen3
```

**Single song:**
```bash
PYTHONPATH=src:. uv run --extra score_lrc_base --extra poc_qwen3_align python poc/experiment_lrc_signals.py --song dan_dan_ai_mi_249
```

**Skip slow signals during development:**
```bash
PYTHONPATH=src:. uv run --extra score_lrc_base python poc/experiment_lrc_signals.py --skip-dtw --skip-tone --skip-qwen3
```

**Custom stem / LRC paths:**
```bash
PYTHONPATH=src:. uv run --extra score_lrc_base --extra poc_qwen3_align python poc/experiment_lrc_signals.py \
  --song dan_dan_ai_mi_249 \
  --stem /path/to/vocals.wav \
  --lrc /path/to/lyrics.lrc
```

Note: `--extra` flags must be separate — `--extra score_lrc_base,poc_qwen3_align` is not valid syntax.

---

## Signals computed per line

| Column | Description |
|--------|-------------|
| `voiced_frac` | Silero VAD: fraction of frames in the LRC window that are voiced. Loaded via `torch.hub` (no separate install). Requires `torchaudio`. |
| `dtw_path_cosine` | Mean cosine similarity along the DTW warping path between framewise wav2vec2 embeddings of TTS and stem window `[t−0.5, t+tts_dur+1.0]`. |
| `dtw_slope_dev` | Std-dev of local slope along the DTW path (ideal=1.0 means no time-stretch). |
| `onset_match_ratio` | Fraction of TTS onsets (librosa) that have a stem onset within ±150 ms. Stem window: `[t−0.3, t+tts_dur+0.3]`. |
| `tone_corr` | Pearson r between expected Mandarin tone slope signs (pypinyin) and observed pYIN F0 slope signs per character. Noisy on sung vocals — treat as exploratory. |
| `qwen3_drift` | Absolute drift (seconds) between the LRC timestamp and the start time assigned by Qwen3-ForcedAligner-0.6B. Aligner runs once on the full stem + full lyrics text, then per-line drift is `\|qwen3_start − t_lrc\|`. Requires `poc_qwen3_align` extra. Stems must be ≤ 5 minutes. |
| `mfa_drift` | Absolute drift (seconds) from Montreal Forced Aligner. Stub — returns NaN unless `mfa` is on PATH and the Mandarin model is downloaded. |

Signals 1–5 (`voiced_frac` through `tone_corr`) are computed per-line from the
stem audio window. Signals 6–7 (`qwen3_drift`, `mfa_drift`) run once per song
on the full stem and distribute results back to each line.

Dependencies for signals 1–5 come from the `score_lrc_base` extra.
Signal 6 additionally requires the `poc_qwen3_align` extra (`qwen-asr` package,
model `Qwen/Qwen3-ForcedAligner-0.6B`).

---

## Results from initial run (2026-04-24)

### Cross-song signal means

| Signal | dan_dan (BAD) | wo_yao_yi_xin (GOOD) | Δ | Separates? |
|--------|:---:|:---:|:---:|:---:|
| VAD voiced fraction | 0.235 | 0.726 | 0.491 | **YES** |
| DTW path cosine mean | 0.955 | 0.959 | 0.004 | no |
| DTW slope std-dev | 0.485 | 0.495 | 0.010 | no |
| Onset match ratio | 0.817 | 0.795 | 0.022 | no |
| Tone/F0 correlation | 0.013 | −0.014 | 0.027 | no |
| Qwen3 aligner drift (s) | 14.831 | 33.613 | −18.782 | **inverted** — see note |
| MFA drift (s) | n/a | n/a | — | not installed |

### Key finding

**VAD voiced fraction is the only signal that cleanly separates the two songs.**
`dan_dan` has 5 lines scoring exactly 0.0 (pure silence), all clustered at the
stacked timestamps `[01:19.52]` through `[01:30.25]`. Mean VAD 0.235 vs 0.726.

DTW, onset, and tone signals are all ineffective — the repetitive short phrases
in `dan_dan` allow DTW to find high-cosine matches regardless of timing, and
onset/tone are too noisy at the per-line level to distinguish the songs.

### Why DTW doesn't work here

The song recycles 5–6 short phrases. Any 3–6 s stem window contains a
near-phonetic match for any line text, so DTW cosine saturates near 0.955 on
both songs. `dtw_slope_dev` similarly provides no signal because the window is
wide enough that the aligner always finds a plausible path.

### Why Qwen3 drift is inverted (higher on the GOOD song)

The Qwen3 forced aligner processes the entire stem audio in one pass alongside
the full lyrics text. On `dan_dan` (16 short lines, highly repetitive, ~1:30
span), it stays roughly synchronized — max drift 34.9 s.

On `wo_yao_yi_xin` (34 lines over 4:10), the aligner accumulates drift as
repeated phrases (e.g. "我呼求時祢必應允我" appears in multiple verses) cause it
to skip ahead or fall behind. Later lines show drifts of 57–107 s even though
the LRC is correct. The mean drift (33.6 s) is higher on the GOOD song.

**Conclusion:** `qwen3_drift` as currently computed is not a reliable quality
signal for long songs with repeated lyric phrases. It would need per-section
normalization or a sliding-window approach to be useful.

### Recommendation for final scorer

Use **VAD voiced fraction** as the primary signal with a threshold around 0.25–0.30.
This flags lines placed in silence without false-positives on the good song
(min VAD on good song = 0.10 for one line at a section boundary).

---

## What's not implemented yet

- **MFA drift:** stub in place; results available once MFA + Mandarin model
  installed (see step 3 above).
- **Thresholds / final scorer:** out of scope for this phase. Pick after
  reviewing `poc/experiment_output/signals.md`.
- **Replacing `score_lrc_quality.py`:** separate follow-up task once scorer
  is chosen.
- **Qwen3 drift improvement:** sliding-window or per-section alignment to
  avoid cumulative drift on long songs with repeated lyrics.

---

## File map

| File | Role |
|------|------|
| `poc/experiment_lrc_signals.py` | Experiment driver — all signal computation |
| `poc/score_lrc_quality.py` | Legacy scorer; also used to populate TTS cache |
| `poc/gen_lrc_qwen3_force_align.py` | Qwen3-ForcedAligner wrapper (referenced by experiment) |
| `poc/experiment_output/*/signals.csv` | Per-song per-line signal data |
| `poc/experiment_output/signals.md` | Cross-song markdown summary |
| `specs/lrc_eval_alternative_strategies.md` | Background: original five-strategy enumeration |
