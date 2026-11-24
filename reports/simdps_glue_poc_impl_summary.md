# SimDPS Glue POC implementation summary

The SimDPS Glue POC generated a usable four-beat fill for junction 1 of the reference Songset on Modal. The fill was exactly **122,880 stereo frames at 44.1 kHz**, and both songs remained sample-identical to their arranged context files. The implementation is on `poc/simdps-glue` in commit `78c0027c`. The refreshed graph is in `584a7f11`.

## How it works

1. The CLI loads a 2–5 song Songset from Postgres, caches the source mixes from R2, and renders each mix in stored Arrangement Step order. For each selected junction, it builds a montage from the last 12 seconds of the outgoing arrangement, a silent gap sized in beats at the incoming tempo, and the first 12 seconds of the incoming arrangement. Shorter arrangements contribute their available context.
2. The CLI applies the incoming song's full Arrangement Steps to its R2 `stems/instrumental.flac` and sends that guide and the montage as stereo FLAC, along with exact gap frame bounds, the seed, and the step count.
3. The Modal app runs SimDPS-Multi with the ACE-Step 1.5 base model on an L4. SimDPS is pinned to `f5175f14b3be67e28ffc7c95874265a0390a9ada`, and weights are cached in the `sow-simdps-models` volume. The app uses the model's returned gap and window coordinates to crop the raw fill at 48 kHz, then resamples it to the exact requested length and returns a float WAV with timing metadata.
4. The CLI applies the ElevenLabs POC's gain staging and fades to the fill, then inserts it between the original arranged mixes. It writes the production gap or crossfade baseline, arranged context, guide, montage, raw and staged fills, seam excerpts, full pair, and `index.json`. The HTML report includes players, scores, notes, and a **vocals heard** checkbox.

Missing stems, unsupported model windows, and other take failures are recorded in the index and report. The CLI continues through the selected junctions, keeps successful artifacts and baselines, and exits with status `2` if a take fails. A generated full set requires a usable fill at every junction for that beat count and take.

[CLI](../lab/poc-scripts/gen_glue_simdps.py) · [Modal app source](../lab/poc-scripts/modal_apps/simdps_inpaint_app.py) · [Modal deployment](https://modal.com/apps/mhuang74/main/deployed/sow-simdps-poc) · [Deployment and usage](../lab/poc-scripts/README-inpaint-poc.md#gen_glue_simdpspy--glue-guided-by-the-incoming-instrumental-stem)

## Reference run

| Measurement | Result |
|---|---|
| Songset and junction | `hbWfCccGxN_JLQGBEqmdu`, junction 1, Here I Bow → Here for You |
| Take settings | Four beats at 86.13281 BPM, seed 0, 100 sampler steps |
| Fill | 122,880 frames at 44.1 kHz, about 2.786 seconds |
| Modal timing | 29.655 s sampling, 130.441 s elapsed for the remote call including startup |
| Splice verification | Both FLAC outputs matched the source arrangements and the staged fill at every PCM16 sample |
| Browser verification | The raw and staged fills played in Chrome |

The vocal result is **unconfirmed**. YAMNet scored singing below 0.001 across both fills, while Essentia's voice/instrumental classifier scored voice at 0.514 for the raw fill and 0.370 for the staged fill. A human listening verdict remains open. [YAMNet](https://www.tensorflow.org/hub/tutorials/yamnet) · [Essentia classifier](https://essentia.upf.edu/models/classifiers/voice_instrumental/)

The run artifacts are local under `lab/poc-scripts/output/simdps-glue/run1/`. Open `report.html` there to review the take.

## Offline verification

All 70 tests across the ElevenLabs, Treblo, and SimDPS Glue suites passed. The SimDPS tests cover dry-run isolation, Arrangement Step order in the guide, exact gap extraction across sample rates, sample-identical song sides, and continued artifact reporting after a missing stem. [SimDPS tests](../lab/poc-scripts/tests/test_gen_glue_simdps.py)
