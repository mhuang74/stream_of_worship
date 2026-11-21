# ElevenLabs instrumental Glue POC

Builds both arranged sides of a songset's first transition from Postgres + R2, uploads an excerpt of each to the ElevenLabs music API, and composes one plan per Glue length. Only the generated middle chunk is kept, so the output is `A arranged + staged Glue + B arranged` and the Glue is the only new audio.

Script: `lab/poc-scripts/gen_elevenlabs_glue.py`.

## Prerequisites

- Environment: `ELEVENLABS_API_KEY`, `SOW_DATABASE_URL`, `SOW_R2_BUCKET`, `SOW_R2_ENDPOINT_URL`, `SOW_R2_ACCESS_KEY_ID`, `SOW_R2_SECRET_ACCESS_KEY`. The script reads `/opt/sow/.env` for any that are unset and exits naming the missing ones.
- `ffmpeg` and `ffprobe` on `PATH`.
- Dependencies: the `elevenlabs_glue` extra (`requests`, `numpy`, `soundfile`) in `lab/poc-scripts/pyproject.toml`.
- A songset with at least two items; only positions 0 and 1 are used, and a larger songset prints a warning.

## Dry run (free)

Prints the take rows, the plan JSON bodies and the credit estimate; no network, no writes.

```bash
uv run --python 3.11 --project lab/poc-scripts --extra elevenlabs_glue \
  python lab/poc-scripts/gen_elevenlabs_glue.py --dry-run
```

## Real run (costs credits)

Billing is 15 credits per second uploaded plus 15 per second generated. Full arranged sides cost ~4,857 upload credits, and the copyright screen rejects a full commercial arrangement with HTTP 400 while keeping half the fee — so upload excerpts instead.

```bash
uv run --python 3.11 --project lab/poc-scripts --extra elevenlabs_glue \
  python lab/poc-scripts/gen_elevenlabs_glue.py --run-name run1 --upload-seconds 12
```

Expected result: four takes (2/4/8/16 beats of the incoming song's tempo) at ~701 credits with 12 s excerpts (360 upload + 341 generation). The 2- and 4-beat takes ask the API for its 3 s floor and keep the tail, so their Glue is shorter than the raw generation chunk. The last stdout line prints the review command.

## Flags

| flag | default | effect |
|---|---|---|
| `--songset` | `hbWfCccGxN_JLQGBEqmdu` | songset id; positions 0 and 1 only |
| `--beats` | `2,4,8,16` | Glue lengths in beats of the incoming song's tempo; comma-separated for several |
| `--takes` | `1` | takes per Glue length |
| `--seed-base` | `0` | seed for take 0 |
| `--run-name` | `run1` | run directory name |
| `--out-root` | `lab/poc-scripts/output/elevenlabs-glue` | output root; `cache/` and `uploads/` are shared across runs |
| `--glue-text` | module default | text prompt for the generation chunk |
| `--positive-styles` | module default | comma-separated positive style tags |
| `--negative-styles` | module default | comma-separated negative style tags |
| `--condition` | `b` | `b` or `none` |
| `--condition-strength` | `high` | `low`, `medium`, `high`, `xhigh` |
| `--condition-seconds` | `8.0` | reference seconds used for conditioning |
| `--upload-seconds` | `0.0` | upload only A's last / B's first N s; 0 = full arranged sides |
| `--context-seconds` | `0.0` | sub-window inside the uploaded side that a plan references; 0 = whole side |
| `--output-format` | `pcm_44100` | API output format (16-bit LE stereo) |
| `--dry-run` | off | print plans; no network, no writes |
| `--force` | off | reuse a non-empty run directory; deletes a stale `failure.json` |
| `--refresh-uploads` | off | ignore the upload cache; also clears a cached copyright rejection |
| `--refresh-responses` | off | compose again instead of reusing `cache/responses/`; costs credits |

`--upload-seconds` decides what is uploaded; `--context-seconds` decides which sub-range of that upload a plan references — different questions, both meaningful.

## Caches and re-runs

- `cache/` (source mp3/wav), `uploads/` and `uploads.json` (content-addressed uploads; copyright rejections cached by audio hash) live under `--out-root` and are shared by every run.
- A real `run1` already exists at `lab/poc-scripts/output/elevenlabs-glue/run1`, so re-running needs `--force` (or another `--run-name`); without it the script exits telling you so.
- Compose is paid and not reproducible, so responses are cached by (plan body, seed, format, take id): a re-render after a code change costs 0 credits. `--refresh-responses` forces a new paid compose.
- Exit codes: 0 success; 2 on a fatal API response, which also writes `failure.json` in the run directory.

## Run directory

```
<out-root>/<run-name>/
  context/     a-arranged.flac, b-arranged.flac — full arranged sides (splice sources)
  plans/       composition plan request body per take
  responses/   raw API response bytes (<take>.pcm, <take>.wav) per take
  glues/       <take>.flac (staged + faded) and <take>-generation.flac (untrimmed)
  full/        full pair per take, plus baseline.flac
  seams/       15 s A + glue + 15 s B excerpt per take
  index.json   every plan field, seed and measured metric; failed takes included
  report.html  listening + scoring page
```

## Listening to the output

Serve it — relative FLAC paths mean `file://` will not work:

```bash
cd lab/poc-scripts/output/elevenlabs-glue/run1
python -m http.server 8092
# open http://127.0.0.1:8092/report.html
```

Each take card has four players. **Glue (spliced)** is what the render would play (staged, dB-ramped, 20 ms fades); **Generation (raw)** is the untrimmed API chunk; **Seam excerpt** is the fastest A/B judgement; **Full pair** is the whole pair (`preload="none"`). **Context** cards (A and B arranged) and a **Baseline** card (the production silence gap) are there for comparison.

The per-card metrics line carries `status ok|check|failed`, `geometry`, `r_a`/`r_b`, `envelope`, `offset ms`, edge gain, the A/B joint step and the click ratio; a `check` names its junction in text (`A joint level step 3.541 dB > 3 dB`). `check` is a measured flag, not a failure.

Scoring: a 0–10 score, a vocals-heard checkbox and a note per take, kept in `localStorage`, with **Export scores** / **Import scores** / **Reset scores** buttons. The ranking table sorts score descending with unscored last; a "Last played" bar and a per-card badge mark the take just auditioned. The intended pass is human — the POC measured, it did not judge musicality.

No browser needed: any FLAC player works, e.g. `ffplay full/beats-8.flac`. To land on the seam, seek past A by reading `items[0].arranged_seconds` from `index.json` (`jq '.items[0].arranged_seconds' index.json`) — for the checked-in `run1`, `ffplay -ss 140 -t 20 full/beats-8.flac`.

## Where the numbers are documented

- [`../../reports/elevenlabs_glue_poc_impl_summary.md`](../../reports/elevenlabs_glue_poc_impl_summary.md) — what was built, departures from plan.
- [`../../reports/elevenlabs_glue_poc_v1_results.md`](../../reports/elevenlabs_glue_poc_v1_results.md) — measured runs and metrics.
- [`../../reports/elevenlabs_inpainting_api_guide.md`](../../reports/elevenlabs_inpainting_api_guide.md) — "Measured" API facts.

This README is usage only; measured values live in those reports and are not duplicated here.
