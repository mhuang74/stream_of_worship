# ElevenLabs instrumental Glue — POC v1 results

Run 2026-11-21 against songset `hbWfCccGxN_JLQGBEqmdu` with a paid ElevenLabs plan.
Script: `lab/poc-scripts/gen_elevenlabs_glue.py` (`--upload-seconds 12`, `model_id` `music_v2_5`,
`output_format` `pcm_44100`). Artifacts: `lab/poc-scripts/output/elevenlabs-glue/run1/`.

A = "Here I Bow" arranged 146.51 s (68.0 BPM, D major, 5 steps).
B = "Here For You" arranged 177.31 s (86.13281 BPM, C major, 6 steps). Beat = 0.69660 s (B's tempo).

## What ran

| Take | Beats | Target glue | Plan | Returned seam | Glue used |
|---|---|---|---|---|---|
| beats-2 | 2 | 1.393 s | 27.00 s | 27.000 s | 1.393 s (tail of 3 s) |
| beats-4 | 4 | 2.786 s | 27.00 s | 27.000 s | 2.786 s (tail of 3 s) |
| beats-8 | 8 | 5.573 s | 29.573 s | 29.560 s | 5.573 s |
| beats-16 | 16 | 11.146 s | 35.146 s | 35.160 s | 11.146 s |

Baseline (production behaviour today) = the same pair with a 2-beat (1.393 s) silence gap.
Full-pair durations: baseline 325.213 s, beats-2 325.213 s, beats-4 326.606 s, beats-8 329.393 s,
beats-16 334.966 s — each equals `A + glue + B`. Samples outside the glue are the original arranged
audio (asserted on every take, re-checked bit-exact on `full/beats-2.flac`).

Cost: **701 credits** (360 upload: two 12 s excerpts; 341 generation: four takes).

## Findings

### 1. The upload gate is the model's plan, not the enterprise tier

`POST /v1/music/upload` succeeded on a paid subscription; the earlier HTTP 402 is gone.

### 2. The copyright screen rejects full commercial arrangements, not short excerpts

Uploading A's whole 146.51 s arranged side returned

```
HTTP 400 copyrighted_material_detected
"The song you tried to upload contains copyrighted material. You were still charged half price."
```

Two 12 s excerpts of the same audio (A's last 12 s, B's first 12 s) uploaded without complaint. The
vendor guide's "upload excerpts" recipe is therefore load-bearing, not just a cost saving; the script
now has `--upload-seconds N` for it. Full sides would also have cost 4,857 credits of upload.

### 3. Reference chunks come back **re-rendered**, not inserted

Measured against the uploaded excerpts:

| Comparison | beats-2 | beats-8 |
|---|---|---|
| waveform Pearson r, best over ±200 ms (the locator's score) | 0.35 / 0.45 | 0.47 / 0.27 |
| waveform Pearson r at the plan-aligned position | −0.09 / +0.01 | — |
| 50 ms energy-envelope r | 0.993 / 0.980 | 0.991 / 0.960 |
| spectral-envelope r | 0.94 / 0.96 | — |
| level, returned ref vs upload | within 0.1 dB | — |

Same performance, same dynamics, different waveform: the model regenerates the whole plan and uses
the refs as conditioning. Consequences applied in the script:

- the Glue is located by **plan geometry** (`glue_start = a_ref_samples`,
  `glue_end = glue_start + generation_ms`), because no sample-identical window exists to correlate;
- position and length still hold exactly (`lag 0 samples`, seam length == plan length to within 14 ms
  on the longest take, and the returned ref levels match), so geometry is safe;
- the region identity is now proven by a 50 ms energy-envelope correlation (floor 0.90) instead of
  wave correlation (floor 0.95, unreachable here);
- `index.json` records `geometry: plan` plus both envelope numbers per take.

### 4. Gain staging is required; one take still dips at the seam

The glue is ramped in dB from A's exit level to B's entry level over 200 ms edges and faded 20 ms.
Residuals after staging are ≤ 0.6 dB on every take (`edge_gain_db` spans −4.64 … +7.63 dB, i.e. the
model's own exit/entry levels are far from the songs'). Joint metrics:

| Take | A joint step L/R | B joint step L/R | click ratio L/R | status |
|---|---|---|---|---|
| beats-2 | **−3.72** / +1.64 dB | — | 0.91 / 0.62 | check |
| beats-4 | +0.74 / +0.44 dB | — | 0.92 / 0.62 | ok |
| beats-8 | +2.04 / +0.78 dB | — | 0.83 / 0.61 | ok |
| beats-16 | +2.08 / +1.42 dB | — | 0.67 / 0.62 | ok |

beats-2 is flagged because the returned glue's **first 50 ms** on the left channel sits at −32.4 dB
while A's last 50 ms sits at −25.6 dB (−6.8 dB), even though the 200 ms edge gain matched within
0.2 dB. It is the re-rendered onset (an attack that starts quietly after the 20 ms fade), not a
staging error: the same ramp yields +0.7 dB on beats-4. A crossfade at the joints, or a fade aligned
to the glue's first onset, would remove it; neither is implemented in v1.

Click ratios are all < 1 (the joint sample step is smaller than the local 99.99th percentile of
sample diffs), i.e. no click discontinuity at either joint on any take.

### 5. `pcm_44100` is 16-bit little-endian stereo

`bytes / (44100 × 2 ch × 2 B)` equals the plan duration exactly on all four responses, so the raw
payload is decoded directly with no fallback to `mp3_48000_320`.

## How to review

```bash
cd lab/poc-scripts/output/elevenlabs-glue/run1
python -m http.server 8092   # -> http://127.0.0.1:8092/report.html
```

Each take has four players: Glue (staged + faded, what the render plays), Generation (raw API middle
chunk), Seam excerpt (15 s A + glue + 15 s B), Full pair. The page keeps 0–10 scores, notes and a
vocals-heard flag in localStorage with JSON export/import — the intended listening pass is by the
project owner; v1 measured, it did not judge musicality.

Verified on this host with headless Chrome at 1280 px: all 20 audio files serve 200 `audio/flac`,
playback advances (`currentTime` 0.55 s of 5.573 s on beats-8), the last-played bar and card badge
update, and the ranking table lists all five entries.

## Reproduce

```bash
uv run --project lab/poc-scripts --extra elevenlabs_glue \
  python lab/poc-scripts/gen_elevenlabs_glue.py --run-name run1 --force --upload-seconds 12
```

Uploads are cached by content hash in `<out-root>/uploads.json`, so a re-run with unchanged arranged
audio costs generation only (341 credits for the four default takes).
