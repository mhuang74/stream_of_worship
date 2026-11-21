# Audio-inpaint POC runs

Listening pages for the seam inpainting proof of concept, under `output/audio-inpaint/`
(that directory is git-ignored: the runs are local artifacts, this file is the record).
Each run directory holds
`index.json` (every prompt, seed, parameter and measurement), `clips/` (the listenable
splices), `bridges/` (the generated region only), `montages/` (what was uploaded),
`fills/` (gap mode: the raw model fill), `reference/`, `context/` and `report.html`.

Serve a page (the audio `src`s are relative, so open it over HTTP, not `file://`):

```bash
cd lab/poc-scripts/output/audio-inpaint/run5-arranged && python -m http.server 8090
# then http://localhost:8090/report.html
```

Each take is scored 0–10 on five weighted criteria in the page itself; the guide at the
top of every report lists the measured proxy for each criterion.

## Runs

| run | mode | engine | model | records | what it is |
|---|---|---|---|---|---|
| `run1` | bridge | base | `acestep-v15-base` | delivered | the original 2-pass half-hole run; kept as the defect reference |
| `run2` | gap | sa3 | `sa3-small-music` | delivered | Stable Audio 3 on the 1.393 s production gap |
| `run3` | gap | sa3 | `sa3-small-music` | delivered | run2 plus onset-based `trim`/`grid` timing correction |
| `run5-arranged` | bridge | base | `acestep-v15-base` | 45 (45 ok) | boundary 8 s and 16 s bridges (15 s context), intra 8 s bridges (8 s context) |
| `run5-arranged-sa3` | bridge | sa3 | `sa3-medium` | 45 (45 ok) | same geometry on Stable Audio 3 medium |
| `run5-gap` | gap | base | `acestep-v15-base` | 18 (13 ok) | the 2-beat gap, `raw`/`trim`/`grid` per take |
| `run5-gap-sa3` | gap | sa3 | `sa3-medium` | 14 (2 ok) | same, on SA3 medium |

A gap take makes one model call and writes up to three records (`raw`, `trim`, `grid`);
a take that fails at the raw stage writes one, which is why `run5-gap-sa3` has 14
records for 6 takes.

Reproduce (all four in parallel, ~15 min wall, ~470 s GPU — one model call per take:
378 s base bridge + 42 s SA3 bridge + 45 s base gap + 6 s SA3 gap):

```bash
uv run --project lab/poc-scripts --extra audio_inpaint \
  python lab/poc-scripts/gen_audio_inpaint.py --songset hbWfCccGxN_JLQGBEqmdu \
  --out lab/poc-scripts/output/audio-inpaint/rerun-bridge
# ... --engine sa3 for the SA3 bridge; --mode gap [--engine sa3] for the gap runs
```

A take whose clip and sidecar exist is skipped, so a re-run into the same `--out`
resumes; `--force` regenerates every take. `--lengths` defaults to `8,16`, `--takes` to 3.

`--mode gap` forces `align=incoming`; the gap is two beats at B's 86.13281 BPM
(1.393 s = 61,431 frames). `--context-seconds N` sets how much arranged audio is
uploaded each side (default 15; `0` uploads the entire arrangement). The intra seam has
only 8 s of arranged audio per side, so its montages record 8 s even at the 15 s default.

## Bridges: 45 takes each (boundary 8 s and 16 s holes at 15 s context, intra 8 s at 8 s)

| measured | ACE-Step base | SA3 medium | run1 reference |
|---|---|---|---|
| join RMS step, left | 2.22 dB (0.64–3.74) | 2.27 dB (1.90–4.95) | — |
| join RMS step, right (after the 0.5 s handoff) | −3.84 dB | −3.99 dB | — |
| raw right splice (what the handoff replaced) | 0.43 dB, up to 6.9 | 4.87 dB, up to 16.2 | — |
| chroma vs real A / B | 0.37 / 0.40 | 0.65 / 0.45 | 0.12–0.16 |
| stereo L/R coherence | 0.84 (0.50–0.99) | 0.60 (0.01–0.84) | 0.00–0.23 |
| A audio borrowed for the head | 0.17 s | 0.75 s (0.15–2.40) | — |
| GPU per take | 8.4 s | 0.9 s | 2–5 s (17 s montage) |

Both engines now land on the real audio at both joins: the head is A's own recording
(only where the model fades in), the tail is B's first 0.5 s sample for sample, and
nothing in the uploaded contexts is generated. Base wins on stereo coherence and raw
tail behaviour; SA3 wins on key/harmony agreement with A and is ~9× faster.

## Gap: the 1.393 s production gap

ACE-Step base fills it; SA3 medium does not.

| | ok / refused | beat-two peak error | trailing dead air |
|---|---|---|---|
| base `raw` | 6 / 0 | −26.8 … +44.1 ms | 0–0.02 ms |
| base `trim` | 5 / 1 | −45.7 … +44.2 ms | 23–46 ms |
| base `grid` | 2 / 4 | +13.4, +36.4 ms | 0.02 ms (ok); 68–90 ms (refused) |
| SA3, all corrections | 2 / 12 | +16.6, +39.8 ms | 0–0.02 ms (ok); 58 ms (refused) |

For a gap the right join is expected to be *positive*: the fill's last hit decays into
B's downbeat, so `join_rms_jump_db.right` reads +0.7 … +23.9 dB on the ok rows and that
is the transition, not a defect. The gate here is trailing dead air (refused above 50 ms)
and unstaged residue, not the step: a fill that stops 90 ms before the downbeat leaves a
hole in the music, while a fill whose tail rings 20 dB under a downbeat is a drum fill.

SA3's refusals are its own output: 2 fills came back as −66 dB RMS silence and 9 could
not be staged (residual 17–42 dB after an 18 dB edge ramp). The `grid` refusals are the
stretch-then-pad shape: `grid` stretches the fill and zero-pads the remainder, which run3
shipped as reported dead air and this run refuses above 50 ms.

## What changed to get here

In short: one repaint pass with a mask around the hole, recordings-sourced key/tempo, a
30 s style reference, a bounded 15 s context per side (feeding the whole 331.8 s
arrangement made SA3 fade its hole in from digital silence), level staging anchored on
the joints instead of means, real-audio handoffs at both ends, and model failures
refused rather than spliced.
