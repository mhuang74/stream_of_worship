# ElevenLabs instrumental Glue POC

Builds every adjacent junction of a 2–5 song Songset from Postgres + R2, uploads an excerpt of each arranged side to the ElevenLabs music API, and composes one plan per junction per Glue length. Only the generated middle chunk is kept, so each junction becomes `A arranged + staged Glue + B arranged` and the Glue is the only new audio. Every Glue take is also assembled into one complete-set file, next to a production-parity baseline.

Script: `lab/poc-scripts/gen_elevenlabs_glue.py`.

## Prerequisites

- Environment: `ELEVENLABS_API_KEY`, `SOW_DATABASE_URL`, `SOW_R2_BUCKET`, `SOW_R2_ENDPOINT_URL`, `SOW_R2_ACCESS_KEY_ID`, `SOW_R2_SECRET_ACCESS_KEY`. The script reads `/opt/sow/.env` for any that are unset and exits naming the missing ones.
- `ffmpeg` and `ffprobe` on `PATH`.
- Dependencies: the `elevenlabs_glue` extra (`requests`, `numpy`, `soundfile`) in `lab/poc-scripts/pyproject.toml`.
- A songset with 2–5 items. Every adjacent junction is processed by default (2 songs → 1 junction, 5 songs → 4); `--junctions` narrows that. A songset with more than 5 items is an error, never truncated: the delivery limit is 5 songs, so a larger set means the DB was written around the webapp cap.
- Every item needs `recordings.tempo_bpm`: the Glue length is whole beats of the *incoming* song's tempo, and the baseline gap is too. A missing tempo exits naming the item.

## Dry run (free)

Prints each junction's simulated sides, the production baseline gap, the take rows, the plan JSON bodies and the aggregate credit estimate; no network, no writes.

```bash
uv run --python 3.11 --project lab/poc-scripts --extra elevenlabs_glue \
  python lab/poc-scripts/gen_elevenlabs_glue.py --dry-run --dry-run-items 5 \
  --beats 4 --upload-seconds 12
```

`--dry-run-items` (2–5) sets the simulated songset size. The 5-item run prints four junctions and `est. credits: upload 1440 + generation 180 = 1620`.

## Real run (costs credits)

Billing is 15 credits per second uploaded plus 15 per second generated. Full arranged sides cost ~4,857 upload credits for a two-song pair, and the copyright screen rejects a full commercial arrangement with HTTP 400 while keeping half the fee — so upload excerpts instead.

```bash
uv run --python 3.11 --project lab/poc-scripts --extra elevenlabs_glue \
  python lab/poc-scripts/gen_elevenlabs_glue.py --run-name run1 --upload-seconds 12
```

Expected result: one 4-beat take per junction at ~360 credits per junction with 12 s excerpts (8 × 12 s of upload + 4 × 3 s of generation ≈ 1,620 credits for a 5-song set). The 4-beat take at 86 BPM is 2.79 s, under the API's 3 s floor, so it asks for 3 s and keeps the tail (the arrival into B). The last stdout lines print the take/full-set status and the review command.

Narrow to one junction (half the cost) while iterating:

```bash
uv run --python 3.11 --project lab/poc-scripts --extra elevenlabs_glue \
  python lab/poc-scripts/gen_elevenlabs_glue.py --run-name run1 --junctions 1 --upload-seconds 12
```

## Flags

| flag | default | effect |
|---|---|---|
| `--songset` | `hbWfCccGxN_JLQGBEqmdu` | songset id; 2–5 items, all adjacent junctions |
| `--junctions` | every adjacent junction | 1-based junction indices to process, comma-separated (junction `j` joins item `j-1` to item `j`) |
| `--beats` | `4` | Glue lengths in beats of the incoming song's tempo; comma-separated for several (`2,4,8,16` for the old sweep) |
| `--takes` | `1` | takes per Glue length per junction |
| `--seed-base` | `0` | seed for take 0 |
| `--run-name` | `run1` | run directory name |
| `--out-root` | `lab/poc-scripts/output/elevenlabs-glue` | output root; `cache/` and `uploads/` are shared across runs |
| `--glue-text` | module default | text prompt for the generation chunk |
| `--positive-styles` | module default | comma-separated positive style tags |
| `--negative-styles` | module default | comma-separated negative style tags |
| `--condition` | `b` | `b` or `none` |
| `--condition-strength` | `high` | `low`, `medium`, `high`, `xhigh` |
| `--condition-seconds` | `8.0` | reference seconds used for conditioning |
| `--upload-seconds` | `0.0` | upload only A's last / B's first N s per junction; 0 = full arranged sides |
| `--context-seconds` | `0.0` | sub-window inside the uploaded side that a plan references; 0 = whole side |
| `--output-format` | `pcm_44100` | API output format (16-bit LE stereo) |
| `--dry-run` | off | print plans; no network, no writes |
| `--dry-run-items` | `2` | simulated songset size (2–5) for `--dry-run` |
| `--force` | off | reuse a non-empty run directory; deletes a stale `failure.json` |
| `--refresh-uploads` | off | ignore the upload cache; also clears a cached copyright rejection |
| `--refresh-responses` | off | compose again instead of reusing `cache/responses/`; costs credits |

`--upload-seconds` decides what is uploaded; `--context-seconds` decides which sub-range of that upload a plan references — different questions, both meaningful.

## Caches and re-runs

- `cache/` (source mp3/wav, compose responses), `uploads/` and `uploads.json` (content-addressed uploads; copyright rejections cached by audio hash) live under `--out-root` and are shared by every run.
- Compose is paid and not reproducible, so responses are cached by (plan body, seed, format, take id). The take id in the cache key is still `beats-4` / `beats-4-t2` — the junction lives in the plan body — so responses bought by earlier single-pair runs still hit and a re-render after a code change costs 0 credits. `--refresh-responses` forces a new paid compose.
- Exit codes: 0 success; 2 on a fatal API response, which also writes `failure.json` in the run directory and marks the full set skipped.

## Run directory

```
<out-root>/<run-name>/
  context/            song-01-<hash8>.flac ... — every arranged side (splice sources)
  full-set/           <take>.flac (all junctions at that length), baseline.flac
  junctions/j1/       plans/ responses/ glues/ full/ seams/ for junction 1
  junctions/j2/       ... one directory per selected junction
  index.json          per-item, per-junction and full-set fields, seeds and measured metrics
  report.html         listening + scoring page, one section per junction
```

`junctions/jN/glues/` holds `<take>.flac` (staged + faded) and `<take>-generation.flac` (untrimmed), `full/` the full pair (plus `baseline.flac`), `seams/` the 15 s A + glue + 15 s B excerpt (plus `seams/baseline.flac`).

## Baselines

Every junction ships a baseline that emulates production exactly:

- **gap junction** (item has no crossfade): `gap_beats` of silence (default 2 beats, `DEFAULT_GAP_BEATS`) between the two arranged sides;
- **crossfade junction**: `crossfade_duration_seconds` of overlapping linear crossfade replaces the gap entirely (`min(duration, incoming arranged seconds)`).

Same arithmetic as `delivery/render-worker/src/sow_render_worker/audio_engine.py`: `start = prev_end + gap - crossfade`, `afade` linear in/out, `amix normalize=0`. Production's `loudnorm` is **not** emulated, so the baseline is a raw PCM mix and its level is not production-normalized; judge the takes against the raw arrangement. `full-set/baseline.flac` is the whole set that way, `junctions/jN/full/baseline.flac` is just that pair.

## Listening to the output

Serve it — relative FLAC paths mean `file://` will not work:

```bash
cd lab/poc-scripts/output/elevenlabs-glue/run1
python -m http.server 8092
# open http://127.0.0.1:8092/report.html
```

Page order: header → last-played bar and score toolbar → **Context** (one arranged player per song) → **Full set** (baseline + one card per Glue length) → one **section per junction** with its baseline card, take cards, failed-take grid and ranking tables.

Each take card has four players. **Glue (spliced)** is what the render would play (staged, dB-ramped, 20 ms fades); **Generation (raw)** is the untrimmed API chunk; **Seam excerpt** is the fastest A/B judgement; **Full pair** is that pair alone (`preload="none"`). Baseline cards carry only the seam excerpt and the full pair, and print the production numbers (`2 beats (1.393 s) of silence` or `1.500 s crossfade`) as their metrics line. Full-set cards have one player and no scoring UI.

The per-take metrics line carries `status ok|check|failed`, `geometry`, `r_a`/`r_b`, `envelope`, `offset ms`, edge gain, the A/B joint step and the click ratio; a `check` names its reason in text (`A joint level step 3.541 dB > 3 dB`). `check` is a measured flag, not a failure.

Scoring: a 0–10 score, a vocals-heard checkbox and a note per take, kept in `localStorage` keyed by the display id (`j1-beats-4`), with **Export scores** / **Import scores** / **Reset scores** buttons. Ranking tables sort per junction, score descending with unscored last; a "Last played" bar and a per-card badge mark the take just auditioned. The intended pass is human — the POC measured, it did not judge musicality.

No browser needed: any FLAC player works. Play a whole set, then seek per junction using `index.json`:

```bash
ffplay full-set/beats-4.flac
jq '.junctions[0].from, .junctions[0].to' index.json   # which songs a junction joins
jq '.items[].arranged_seconds' index.json               # item lengths for seeking
```

For a two-song set, junction 1 starts at `items[0].arranged_seconds`; later junctions start at the sum of the preceding arranged sides plus the kept Glue lengths.

## Where the numbers are documented

- [`../../reports/elevenlabs_glue_poc_impl_summary.md`](../../reports/elevenlabs_glue_poc_impl_summary.md) — what was built, departures from plan.
- [`../../reports/elevenlabs_glue_poc_v1_results.md`](../../reports/elevenlabs_glue_poc_v1_results.md) — measured runs and metrics.
- [`../../reports/elevenlabs_inpainting_api_guide.md`](../../reports/elevenlabs_inpainting_api_guide.md) — "Measured" API facts.

This README is usage only; measured values live in those reports and are not duplicated here.
