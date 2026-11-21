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

Cost of this run: **701 credits** (360 upload: two 12 s excerpts; 341 generation: four takes).
Credits spent to get here, for the record: 1,099 for the rejected full-side upload (half of 2,198),
360 for the excerpts, and 682 generation (the first pass ran before the locator fix, so the four
composes were paid twice) — **≈2,141 credits total**.

Compose responses are now cached by `(plan body, seed, format, take)` under
`<out-root>/cache/responses/`, so the run above was regenerated after the metric fixes for **0
credits** from run1's four paid responses. The take id is part of the key because the API is not
reproducible: 2-beat and 4-beat takes send an *identical* plan body (both request a 3 s generation)
and came back with different audio.

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
- position and length still hold: seam length matches the plan to within 14 ms, and the boundaries
  are *measured*, not assumed — a ±1 s search on 50 ms energy blocks puts A's tail and B's head
  within one block of the plan offset (reported per take as `measured_offset_a_ms`/`_b_ms`; all four
  takes measure 0 ms at that resolution);
- the region identity is now proven by a 50 ms energy-envelope correlation (floor 0.90) instead of
  wave correlation (floor 0.95, unreachable here);
- `index.json` records `geometry: plan` plus both envelope numbers per take.

### 4. Gain staging is required; the A joint is where the glue's onset shows

The glue is ramped in dB from A's exit level to B's entry level over 200 ms edges and faded 20 ms.
Residuals after staging are ≤ 0.6 dB on every take (`edge_gain_db` spans −4.64 … +7.63 dB, i.e. the
model's own exit/entry levels are far from the songs'). Joint metrics are measured on the **mono
mix** (they are named `a`/`b` for the A and B junctions — not stereo channels, which an earlier
draft of this report mislabelled) and the 50 ms windows skip the deliberate fade:

| Take | A joint step | B joint step | click ratio A/B | status |
|---|---|---|---|---|
| beats-2 | −2.29 dB | +2.50 dB | 0.91 / 0.62 | ok |
| beats-4 | +2.20 dB | +1.01 dB | 0.92 / 0.62 | ok |
| beats-8 | **+3.54 dB** | +0.36 dB | 0.83 / 0.61 | check |
| beats-16 | **+3.55 dB** | −0.21 dB | 0.67 / 0.62 | check |

Reading: the fade itself costs 1.35 dB over the first 50 ms, so measuring across it (as the first
pass did) reported −3.72/+2.04/+2.08/+0.74 dB. With the fade excluded, the A junction shows the
glue's own onset relative to A's tail: **−2.29 dB on beats-2** (the returned glue starts quietly —
its pre-fade staged head is −27.8 dB against A's −25.6 dB, so this is content, not the fade) and
**+3.5 dB on the 8- and 16-beat takes**, where the glue starts on an attack above A's decaying tail.
The 200 ms edge gain still matches within 0.6 dB on all four; the flags are about the first
milliseconds, which is exactly guide QA item 5's musical caution. A short crossfade at the joints,
or a fade aligned to the glue's first onset, would remove both effects; neither is implemented in v1.

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
update, and the ranking table lists all five entries. The per-card metrics line carries
`geometry`, `envelope` and `offset`, and a flagged take names its junction (`A joint level step
3.541 dB > 3 dB`), so a reader cannot mistake a re-rendered reference for a failed locator.

Known limit of the envelope check: it is evaluated on a 3 s window (~60 blocks of 50 ms) at the
glue's boundary, and returns 0.0 when either envelope has zero variance — a reference that is a
constant-level drone therefore flags `check` even though nothing contradicts it. The 12 s regions
measured above (0.96–0.99) are the corroborating evidence, not the gate.

## Reproduce

```bash
uv run --project lab/poc-scripts --extra elevenlabs_glue \
  python lab/poc-scripts/gen_elevenlabs_glue.py --run-name run1 --force --upload-seconds 12
```

Uploads are cached by content hash in `<out-root>/uploads.json`, so a re-run with unchanged arranged
audio costs generation only (341 credits for the four default takes).
