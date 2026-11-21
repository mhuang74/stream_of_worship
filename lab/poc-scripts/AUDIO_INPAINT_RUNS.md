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

| run | mode | engine | model | takes | what it is |
|---|---|---|---|---|---|
| `run1` | bridge | base | `acestep-v15-base` | delivered | the original 2-pass half-hole run; kept as the defect reference |
| `run2` | gap | sa3 | `sa3-small-music` | delivered | Stable Audio 3 on the 1.393 s production gap |
| `run3` | gap | sa3 | `sa3-small-music` | delivered | run2 plus onset-based `trim`/`grid` timing correction |
| `run5-arranged` | bridge | base | `acestep-v15-base` | 45 | 8/16/30 s replacement bridges, 15 s of context each side |
| `run5-arranged-sa3` | bridge | sa3 | `sa3-medium` | 45 | same geometry on Stable Audio 3 medium |
| `run5-gap` | gap | base | `acestep-v15-base` | 18 | the 2-beat gap, `raw`/`trim`/`grid` per take |
| `run5-gap-sa3` | gap | sa3 | `sa3-medium` | 18 | same, on SA3 medium |

Reproduce (all four in parallel, ~15 min wall, ~470 s GPU):

```bash
uv run --project lab/poc-scripts --extra audio_inpaint \
  python lab/poc-scripts/gen_audio_inpaint.py --songset hbWfCccGxN_JLQGBEqmdu \
  --out lab/poc-scripts/output/audio-inpaint/run5-arranged --force
# ... --engine sa3 for the SA3 bridge; --mode gap [--engine sa3] for the gap runs
```

`--mode gap` forces `align=incoming`; the gap is two beats at B's 86.13281 BPM
(1.393 s = 61,431 frames). `--context-seconds N` sets how much arranged audio is
uploaded each side (default 15; `0` uploads the entire arrangement).

## Bridges: 8/16/30 s holes, 45 takes each (15 s context)

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
nothing in the 15 s contexts is generated. Base wins on stereo coherence and raw tail
behaviour; SA3 wins on key/harmony agreement with A and is ~9× faster.

## Gap: the 1.393 s production gap

ACE-Step base fills it; SA3 medium does not.

| | takes ok | refused | beat-two peak error | dead air at the end |
|---|---|---|---|---|
| base `raw` | 6 / 6 | 0 | mean 5.6 ms, max 46 ms | 0–2 ms |
| base `trim` | 5 / 6 | 1 | — | 46 ms |
| base `grid` | 2 / 6 | 4 | — | 68–90 ms (refused) |
| SA3, all corrections | 2 / 14 | 12 | — | — |

SA3's refusals are its own output: 2 fills came back as −66 dB RMS silence and 9 could
not be staged (residual 17–42 dB after an 18 dB edge ramp). The `grid` refusals are the
stretch-then-pad shape: `grid` stretches the fill and zero-pads the remainder, which run3
shipped as reported dead air and this run refuses above 50 ms.

## What changed to get here

See `local://audio-inpaint-quality-plan.md` (revisions R1–R8). In short: one repaint
pass with a mask around the hole, recordings-sourced key/tempo, a 30 s style reference,
a bounded 15 s context (the whole 331.8 s arrangement made SA3 fade its hole in from
digital silence), level staging anchored on the joints instead of means, real-audio
handoffs at both ends, and model failures refused rather than spliced.
