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

Each take is scored 0–10 on five weighted criteria in the run's own `report.html`; the
guide at the top of every report lists the measured proxy for each criterion.

`build_review_index.py` writes the page above all of them — `output/audio-inpaint/index.html`
(same server, `/index.html`): one row per run, every bridge variant with both engines side by
side, and every gap take with `raw`/`trim`/`grid` plus the measured reason a take was refused.
It reads each run's `index.json` and its `clips/`/`bridges/` files, so it only works on a machine
holding the runs: the page is a view over those git-ignored artifacts, not something a clone can
rebuild.

```bash
uv run --project lab/poc-scripts --extra audio_inpaint \
  python lab/poc-scripts/build_review_index.py --root lab/poc-scripts/output/audio-inpaint
```

## Runs

| run | mode | engine | model | records | wall | GPU | what it is |
|---|---|---|---|---|---|---|---|
| `run1` | bridge | base | `acestep-v15-base` | 15 (15 ok) | 542.7 s | — | the original 2-pass half-hole run; kept as the defect reference |
| `run2` | gap | sa3 | `sa3-small-music` | 14 (14 ok) | — | — | Stable Audio 3 on the 1.393 s production gap |
| `run3` | gap | sa3 | `sa3-small-music` | 21 (20 ok) | — | — | run2 plus onset-based `trim`/`grid` timing correction |
| `run5-arranged` | bridge | base | `acestep-v15-base` | 45 (45 ok) | 801.0 s | 378.3 s | boundary 8 s and 16 s bridges (15 s context), intra 8 s bridges (8 s context) |
| `run5-arranged-sa3` | bridge | sa3 | `sa3-medium` | 45 (45 ok) | 376.0 s | 42.0 s | same geometry on Stable Audio 3 medium |
| `run6-full-arranged` | bridge | base | `acestep-v15-base` | 45 (45 ok) | 3404.7 s | 2674.8 s | whole arranged-song input (146.51 s + 177.31 s); boundary 8/16 s + intra 8 s |
| `run6-full-arranged-sa3` | bridge | sa3 | `sa3-medium` | 45 (45 ok) | 787.1 s | 147.6 s | same whole-song input on SA3 medium; a dead head on 24 of the 36 boundary takes, head pad 0.15–3.0 s |
| `run5-gap` | gap | base | `acestep-v15-base` | 18 (13 ok) | 117.0 s | 45.0 s | the 2-beat gap, `raw`/`trim`/`grid` per take |
| `run5-gap-sa3` | gap | sa3 | `sa3-medium` | 14 (2 ok) | 98.8 s | 5.6 s | same, on SA3 medium |

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
uploaded each side (default 0: the whole arranged audio on each side). The intra seam
has only 8 s of arranged audio per side, so its montages record 8 s even at the
whole-song default.

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
B's downbeat, so `join_rms_jump_db.right` reads −0.4 … +23.9 dB on the ok rows (SA3's
single ok `grid` row is the −0.4) and that is the transition, not a defect. The gate
here is trailing dead air (refused above 50 ms) and unstaged residue, not the step: a
fill that stops 90 ms before the downbeat leaves a hole in the music, while a fill
whose tail rings 20 dB under a downbeat is a drum fill.

SA3's refusals are its own output: 2 fills came back as −66 dB RMS silence and 9 could
not be staged (residual 17–42 dB after an 18 dB edge ramp). The `grid` refusals are the
stretch-then-pad shape: `grid` stretches the fill and zero-pads the remainder, which run3
shipped as reported dead air and this run refuses above 50 ms.

## What changed to get here

In short: one repaint pass with a mask around the hole, recordings-sourced key/tempo, a
30 s style reference, a context cap per side — 15 s in run5, lifted in run6 once A's own
post-seam audio was measured to cover SA3's fade-in (see below) — level staging anchored on
the joints instead of means, real-audio handoffs at both ends, and model failures
refused rather than spliced.

`run6` lifts that cap and its table rows and report links carry the measured long-input
behaviour: ACE-Step base gains nothing and stays clean (head pad 0.15 s — the bare lead, no
dead head — on every boundary take), while SA3 opens the hole from digital silence on 24 of
the 36 boundary takes: 0.55–1.05 s on 6 of the 18 8 s holes, and 1.3–2.85 s on all 18 16 s
holes. The head pad replaces that head with A's own post-seam continuation (dead head plus a
0.15 s lead), so `dead edge 0.0` describes the padded head. Six 16 s takes sit at the 3.0 s
`HEAD_PAD_MAX_SECONDS` ceiling — A's audio runs out there — so any residue past 3.0 s is not
measured; the per-take `head pad` and `dead edge` numbers in each report are that finding.

## Implementation summary

What changed and why, the measured results, the review surfaces and the verification:
`reports/audio_inpaint_poc_impl_summary.md`.
