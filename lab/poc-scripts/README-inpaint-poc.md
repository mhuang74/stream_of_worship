# Inpaint POC scripts

These scripts replace exactly one region of a rendered Songset with generated audio: a seam bridge, the production 2-beat gap, or the junction Glue between two songs. Both arranged sides stay sample-identical to the render — only the replaced region is new. Every script ships a production-parity baseline and a self-contained `report.html` listening page next to it.

## Scripts

| script | region replaced | default provider/model | extra | output root |
|---|---|---|---|---|
| `gen_seam_inpaint_acestep.py` | 8 s / 16 s bridge hole, or the production 2-beat gap | `acestep-v15-base` (Modal); `--engine turbo`, `--engine sa3` | `audio_inpaint` | `--out` (e.g. `output/audio-inpaint/run5-arranged`) |
| `gen_gap_fill_dsp_sa3.py` | the 1.393 s production gap, against real-audio DSP baselines | `sa3-small-music` (Modal) plus ACE-Step and 4 DSP baselines | `audio_inpaint` | `--out` (e.g. `output/audio-inpaint/run2`) |
| `gen_gap_fill_sa3.py` | the same gap, with onset-based `trim`/`grid` timing correction | `sa3-small-music` (Modal) | `audio_inpaint` | `--out` (e.g. `output/audio-inpaint/run3`) |
| `gen_glue_elevenlabs.py` | the junction Glue between adjacent songs | ElevenLabs music API | `elevenlabs_glue` | `--out-root` (default `output/elevenlabs-glue`) |
| `gen_glue_treblo.py` | the junction Glue between adjacent songs | Treblo `POST /generations/v2/inpaint` | `glue_treblo` | `--out-root` (default `output/treblo-glue`) |
| `gen_glue_simdps.py` | the junction Glue between adjacent songs | SimDPS-Multi with ACE-Step 1.5 base (Modal L4 or RunPod L4) | `audio_inpaint` | `--out-root` (default `output/simdps-glue`) |

## Naming convention

Every inpaint script is named `gen_<subject>_<provider>.py`. `<subject>` is what region gets replaced — `seam_inpaint` (a bridge hole or the production gap inside an arrangement), `gap_fill` (the 2-beat gap only), `glue` (the junction between two songs). `<provider>` is the engine or API the script defaults to, always last. A script may drive other engines via `--engine`; the default is what the name says, so `gen_seam_inpaint_acestep.py` defaults to `acestep-v15-base` and `gen_gap_fill_dsp_sa3.py` carries the `dsp` marker because its distinguishing feature is the real-audio DSP baseline set. The `pyproject.toml` extras keep their own names (`audio_inpaint`, `elevenlabs_glue`, `glue_treblo`): they are dependency sets, not scripts.

## Shared prerequisites

- Environment: `SOW_DATABASE_URL`, `SOW_R2_BUCKET`, `SOW_R2_ENDPOINT_URL`, `SOW_R2_ACCESS_KEY_ID`, `SOW_R2_SECRET_ACCESS_KEY`. Every script reads `/opt/sow/.env` for any that are unset and exits naming the missing ones.
- `ffmpeg` and `ffprobe` on `PATH`.
- An authenticated Modal profile or `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` for the Modal scripts.
- `ELEVENLABS_API_KEY` for `gen_glue_elevenlabs.py`; `TREBLO_API_KEY` for `gen_glue_treblo.py`.
- `SOW_LLM_API_KEY`, `SOW_LLM_BASE_URL`, `SOW_LLM_MODEL` only for `gen_seam_inpaint_acestep.py` bridge caption authoring.
- Dependencies: the extra named in the table above, from `lab/poc-scripts/pyproject.toml`.

## Run records and review

[`AUDIO_INPAINT_RUNS.md`](AUDIO_INPAINT_RUNS.md) is the measured run record: one row per run with engine, model, record counts, wall and GPU time, plus the bridge/gap tables. `build_inpaint_review_index.py` writes the cross-run page `output/audio-inpaint/index.html` above every run's own `report.html`:

```bash
uv run --project lab/poc-scripts --extra audio_inpaint \
  python lab/poc-scripts/build_inpaint_review_index.py --root lab/poc-scripts/output/audio-inpaint
```

It reads each run's `index.json` and its `clips/`/`bridges/` files, so it only works on a machine holding the runs.

## gen_seam_inpaint_acestep.py — seam bridges and the production gap

Builds a montage from the *entire arranged audio* of both sides of every seam plus a hole of digital silence where the seam is, has a deployed Modal app repaint only the hole, gain-stages the result from the outgoing to the incoming level, and splices it between samples of the original recordings. `--mode bridge` repaints an 8 s or 16 s replacement bridge; `--mode gap` repaints only the production 2-beat gap (1.393 s at B's 86.13281 BPM) and applies the onset-based `trim`/`grid` timing correction. `--engine` selects `base` (`acestep-v15-base`, 64 steps, ADG, shift 1.0 — the quality default), `turbo` (`acestep-v15-turbo`, 8 steps, run2/run3's model) or `sa3` (`sa3-medium`, 8 steps, no key/bpm/reference fields).

```bash
set -a && . /opt/sow/.env && set +a
uv run --project lab/poc-scripts --extra audio_inpaint \
  python lab/poc-scripts/gen_seam_inpaint_acestep.py \
  --songset hbWfCccGxN_JLQGBEqmdu --out lab/poc-scripts/output/audio-inpaint/run4 --dry-run
```

| flag | default | effect |
|---|---|---|
| `--songset` | required | songset id to bridge |
| `--out` | required | output directory for this run |
| `--mode` | `bridge` | `bridge` = 8/16 s replacement holes; `gap` = the production 2-beat gap |
| `--engine` | `base` | `base` (acestep-v15-base), `turbo` (acestep-v15-turbo), `sa3` (sa3-medium) |
| `--align` | `incoming` | which side's recording key/tempo the bridge is generated in (ACE-Step only) |
| `--takes` | `3` | seeds per variant; take `i` uses `--seed + i` |
| `--context-seconds` | `0.0` | seconds of arranged audio uploaded each side of the hole; 0 uploads the entire arrangement |
| `--steps` | `0` | 0 = engine default (base 64, turbo 8, SA3 8); otherwise override |
| `--no-reference` | off | skip building/uploading the 30 s style reference (ACE-Step only) |
| `--arcs` | `keep-intensity,settle-then-lift,quiet-interlude` | comma-separated arcs |
| `--lengths` | `8,16` | comma-separated hole lengths |
| `--treatments` | `inst,texture` | comma-separated treatments |
| `--seed` | `4242` | base seed |
| `--force` | off | regenerate takes even when clips exist |
| `--refresh-captions` | off | re-author `captions.json` |
| `--skip-intra` | off | only generate boundary variants |
| `--dry-run` | off | build montages/captions with zero Modal calls |

Outputs under `--out`:

```
cache/      downloaded mp3s and decoded 44.1 kHz wavs
captions.json   LLM-authored (bridge) or fixed (gap) captions; editable input
context/    each seam's arranged A and B audio, exactly as uploaded
reference/  30 s / 48 kHz style reference per seam and alignment
montages/   what was uploaded (A part + silence + B part)
fills/      gap mode only: the raw model fill per take (1.393 s)
clips/      the listenable splice
bridges/    only the gained hole region
variants/   per-take record (idempotency sidecar)
index.json  every prompt, seed, parameter and measured metric
report.html self-contained listening page
```

## gen_gap_fill_dsp_sa3.py — run2 gap fill with DSP baselines

Shrinks the unit of work to the production seam: the gap a rendered Songset leaves between two songs, asking the models for a drum fill only. Real-audio DSP baselines (`dsp__current-silence`, `dsp__butt-splice`, `dsp__tail-extension`, `dsp__b-pickup`) are the control — if a Tail Extension or the incoming song's own pickup beats every generated fill, the honest answer is that this seam should not be generated. It drives Stable Audio 3 (`sa3-small-music`) and both ACE-Step models (`acestep-v15-turbo`, `acestep-v15-base`) against those baselines; the skip flags drop any group.

```bash
uv run --project lab/poc-scripts --extra audio_inpaint \
  python lab/poc-scripts/gen_gap_fill_dsp_sa3.py \
  --songset hbWfCccGxN_JLQGBEqmdu --out lab/poc-scripts/output/audio-inpaint/run2
```

| flag | default | effect |
|---|---|---|
| `--songset` | `hbWfCccGxN_JLQGBEqmdu` | songset id |
| `--out` | required | output directory for this run |
| `--seed` | `7000` | seed base |
| `--force` | off | regenerate even when clips exist |
| `--refresh-prompts` | off | rewrite `prompts.json` |
| `--skip-dsp` | off | skip the DSP baselines |
| `--skip-sa3` | off | skip Stable Audio 3 |
| `--skip-acestep` | off | skip both ACE-Step models |
| `--dry-run` | off | zero Modal calls |

Outputs under `--out`:

```
cache/          downloaded mp3s + decoded 44.1 kHz wavs (reused across runs)
prompts.json    the four drum prompts; edit it or pass --refresh-prompts
montages/       what was uploaded (generated variants only)
fills/          only the fill audio (every variant except dsp__butt-splice)
clips/          the listenable splice
variants/       per-variant record (idempotency sidecar)
index.json      every prompt, seed, parameter and measured metric
report.html     self-contained listening page
```

## gen_gap_fill_sa3.py — run3 SA3 gap fill with trim/grid timing correction

Attacks run2's defect — a generated fill that opens with ~0.42 s of near-digital silence — from both sides: two Stable Audio 3 (`sa3-small-music`) prompts that ask for drums on beat one with no lead-in, three seeds each, plus two deterministic corrections of every raw take. `trim` drops everything before the first detected onset and pads/trims the end; `grid` additionally time-stretches so the second hit lands exactly one incoming-tempo beat after the first. It reuses run2's upload bytes, clips, staged fill and variant sidecar, so it needs no database or R2 access — everything is read from `--run2` on disk.

```bash
set -a && . /opt/sow/.env && set +a
uv run --project lab/poc-scripts --extra audio_inpaint \
  python lab/poc-scripts/gen_gap_fill_sa3.py \
  --out lab/poc-scripts/output/audio-inpaint/run3
```

| flag | default | effect |
|---|---|---|
| `--run2` | `lab/poc-scripts/output/audio-inpaint/run2` | run2 output directory whose artifacts this run reuses |
| `--out` | required | output directory for this run |
| `--seed` | `8000` | seed base |
| `--force` | off | regenerate existing ok takes |
| `--dry-run` | off | zero Modal calls; still writes the run2 reference raw/trim/grid audio |

Outputs under `--out`:

```
clips/          listenable 31.393 s splice per variant
fills/          the fill region of that splice (1.393 s)
montages/       the montage per raw take (what was (or would be) uploaded)
variants/       per-variant record (idempotency sidecar for Modal takes)
prompts.json    the two run3 prompts
index.json      version 3: seam, tempo/beat geometry, and every variant
report.html     self-contained comparison page
```

## gen_glue_elevenlabs.py — instrumental Glue via the ElevenLabs music API

Builds every adjacent junction of a 2–5 song Songset from Postgres + R2, uploads an excerpt of each arranged side to the ElevenLabs music API, and composes one plan per junction per Glue length. Only the generated middle chunk is kept, so each junction becomes `A arranged + staged Glue + B arranged` and the Glue is the only new audio. Every Glue take is also assembled into one complete-set file, next to a production-parity baseline.

### Prerequisites

- Environment: `ELEVENLABS_API_KEY`, `SOW_DATABASE_URL`, `SOW_R2_BUCKET`, `SOW_R2_ENDPOINT_URL`, `SOW_R2_ACCESS_KEY_ID`, `SOW_R2_SECRET_ACCESS_KEY`. The script reads `/opt/sow/.env` for any that are unset and exits naming the missing ones.
- `ffmpeg` and `ffprobe` on `PATH`.
- Dependencies: the `elevenlabs_glue` extra (`requests`, `numpy`, `soundfile`) in `lab/poc-scripts/pyproject.toml`.
- A songset with 2–5 items. Every adjacent junction is processed by default (2 songs → 1 junction, 5 songs → 4); `--junctions` narrows that. A songset with more than 5 items is an error, never truncated: the delivery limit is 5 songs, so a larger set means the DB was written around the webapp cap.
- Every item needs `recordings.tempo_bpm`: the Glue length is whole beats of the *incoming* song's tempo, and the baseline gap is too. A missing tempo exits naming the item.

### Dry run (free)

Prints each junction's simulated sides, the production baseline gap, the take rows, the plan JSON bodies and the aggregate credit estimate; no network, no writes.

```bash
uv run --python 3.11 --project lab/poc-scripts --extra elevenlabs_glue \
  python lab/poc-scripts/gen_glue_elevenlabs.py --dry-run --dry-run-items 5 \
  --beats 4 --upload-seconds 12
```

`--dry-run-items` (2–5) sets the simulated songset size. The 5-item run prints four junctions and `est. credits: upload 1440 + generation 180 = 1620`.

### Real run (costs credits)

Billing is 15 credits per second uploaded plus 15 per second generated. Full arranged sides cost ~4,857 upload credits for a two-song pair, and the copyright screen rejects a full commercial arrangement with HTTP 400 while keeping half the fee — so upload excerpts instead.

```bash
uv run --python 3.11 --project lab/poc-scripts --extra elevenlabs_glue \
  python lab/poc-scripts/gen_glue_elevenlabs.py --run-name run1 --upload-seconds 12
```

Expected result: one 4-beat take per junction at ~360 credits per junction with 12 s excerpts (8 × 12 s of upload + 4 × 3 s of generation ≈ 1,620 credits for a 5-song set). The 4-beat take at 86 BPM is 2.79 s, under the API's 3 s floor, so it asks for 3 s and keeps the tail (the arrival into B). The last stdout lines print the take/full-set status and the review command.

Narrow to one junction (half the cost) while iterating:

```bash
uv run --python 3.11 --project lab/poc-scripts --extra elevenlabs_glue \
  python lab/poc-scripts/gen_glue_elevenlabs.py --run-name run1 --junctions 1 --upload-seconds 12
```

### Flags

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

### Caches and re-runs

- `cache/` (source mp3/wav, compose responses), `uploads/` and `uploads.json` (content-addressed uploads; copyright rejections cached by audio hash) live under `--out-root` and are shared by every run.
- Compose is paid and not reproducible, so responses are cached by (plan body, seed, format, take id). The take id in the cache key is still `beats-4` / `beats-4-t2` — the junction lives in the plan body — so responses bought by earlier single-pair runs still hit and a re-render after a code change costs 0 credits. `--refresh-responses` forces a new paid compose.
- Exit codes: 0 success; 2 on a fatal API response, which also writes `failure.json` in the run directory and marks the full set skipped.

### Run directory

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

### Baselines

Every junction ships a baseline that emulates production exactly:

- **gap junction** (item has no crossfade): `gap_beats` of silence (default 2 beats, `DEFAULT_GAP_BEATS`) between the two arranged sides;
- **crossfade junction**: `crossfade_duration_seconds` of overlapping linear crossfade replaces the gap entirely (`min(duration, incoming arranged seconds)`).

Same arithmetic as `delivery/render-worker/src/sow_render_worker/audio_engine.py`: `start = prev_end + gap - crossfade`, `afade` linear in/out, `amix normalize=0`. Production's `loudnorm` is **not** emulated, so the baseline is a raw PCM mix and its level is not production-normalized; judge the takes against the raw arrangement. `full-set/baseline.flac` is the whole set that way, `junctions/jN/full/baseline.flac` is just that pair.

### Listening to the output

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

### Where the numbers are documented

- [`../../reports/elevenlabs_glue_poc_impl_summary.md`](../../reports/elevenlabs_glue_poc_impl_summary.md) — what was built, departures from plan.
- [`../../reports/elevenlabs_glue_poc_v1_results.md`](../../reports/elevenlabs_glue_poc_v1_results.md) — measured runs and metrics.
- [`../../reports/elevenlabs_inpainting_api_guide.md`](../../reports/elevenlabs_inpainting_api_guide.md) — "Measured" API facts.

This README is usage only; measured values live in those reports and are not duplicated here.

## gen_glue_treblo.py — instrumental Glue via the Treblo v2 inpaint endpoint

Builds both *arranged* sides of every adjacent junction from Postgres + R2, splices them into one uploaded montage with a silent hole where the Glue belongs (`[ A arranged last 12 s ][ silent hole = 4 beats ][ B arranged first 12 s ]`), and asks `POST /generations/v2/inpaint` to replace that one section of that one file. With `selection_crop` the response is the new section only, so the seam is placed by construction rather than located in the response: no needle search, no envelope alignment, no 3 s generation floor. The returned section is not sample-exact, though — measured 3.344 s for a 3.438 s request (Δ −94.7 ms), so `resolve_section` zero-pads (or trims) the tail to the requested length and records `padded_ms`/`trimmed_ms`; that pad is silent by construction and a take whose pad dominated the glue would be refused by the level gate. One request costs **100 credits** (uploads are free, a FAILURE task is not charged), so one junction with one 4-beat take is 100 credits. Paid responses are cached by request body + take id under `cache/treblo-responses/`, and an interrupted run resumes from its saved task id instead of resubmitting.

```bash
uv run --project lab/poc-scripts --extra glue_treblo \
  python lab/poc-scripts/gen_glue_treblo.py --run-name run1
# one junction, two Glue lengths: --run-name run1 --junctions 1 --beats 4,8
# free plan check: --dry-run --dry-run-items 5
```

| flag | default | effect |
|---|---|---|
| `--songset` | `hbWfCccGxN_JLQGBEqmdu` | songset id (2–5 items) |
| `--beats` | `4` | Glue lengths in beats of the incoming tempo; comma-separated (`2,4,8` to sweep) |
| `--junctions` | every adjacent junction | 1-based junction indices, comma-separated |
| `--takes` | `1` | takes per Glue length |
| `--seed-base` | `0` | seed for take 0 |
| `--run-name` | `run1` | run directory name |
| `--out-root` | `lab/poc-scripts/output/treblo-glue` | output root; `cache/` and `uploads/` are shared across runs |
| `--tags` | `worship,praise & worship` | comma-separated tags, each from `data/treblo-v2-tags.txt` |
| `--instrumental` / `--no-instrumental` | on | ask for an instrumental section; `--no-instrumental` needs `--lyrics` |
| `--lyrics` | none | lyrics for a vocal Glue (with `--no-instrumental`) |
| `--prompt-strength` | `2.0` | prompt adherence, 1.4–3.1 |
| `--balance-strength` | `0.7` | v2 balance: higher is more natural vocals, lower sharper instrumentals, 0.0–1.0 (not a reference/hole weighting) |
| `--upload-seconds` | `12.0` | A's last / B's first N seconds inside the montage, 3–30 |
| `--output-format` | `flac` | API output format |
| `--dry-run` | off | print plans; no network, no writes |
| `--dry-run-items` | `2` | simulated songset size (2–5) for `--dry-run` |
| `--force` | off | reuse a non-empty run directory |
| `--refresh-uploads` | off | ignore the upload cache |
| `--refresh-responses` | off | submit again instead of reusing a cached response (costs credits) |

Tags come from the closed v2 vocabulary in [`data/treblo-v2-tags.txt`](data/treblo-v2-tags.txt) and are validated before any request: the API rejects anything off-list with a 422 (a rejected request is not charged). `POST /uploads` is free and sits behind Cloudflare, so a transient edge/origin failure (524 — the origin behind a ~100 s edge gate — the other 520–523, 5xx, or no response at all) is retried three times at 30/60/120 s; the montage bytes stay cached, so a rerun re-sends exactly them for 0 credits. It also refuses audio Treblo will not accept as a source ("This upload matches existing audio"): that 422 carries no `audio_upload_id` and `/v1/uploads` has no list or GET-by-id operation to recover one, so **that junction only** is marked `blocked` (`index.json` `status`/`blocked_reason`, a `blocked:` note in `report.html`, listed in the tail summary) and the run continues with the remaining junctions — the full-set build is skipped with that cause named. Nothing is charged for a blocked junction; never transform audio (or resend it as `audio_base64`/`audio_url`) to get past that filter. Retrying the same bytes is not a workaround either: 15 consecutive uploads of identical bytes (proxied and direct) were all refused (2026-11-24), so the refusal is stable within a run — the rest of the run continues and a later run may differ.

Every Treblo API call except the CDN download can be routed through a rotating proxy by **exporting** `SOW_YOUTUBE_PROXY` in the environment — the port-range notation `http://user:pass@host:10001-10010` gives one randomly chosen port per request, so a retried upload exits from a different IP. A value that exists only in `/opt/sow/.env` is deliberately ignored (the variable is not in `ENV_KEYS`), so the run stays direct unless the shell exports it; the log shows only `http://***@host:range` and, per attempt, `upload attempt N/4 via http://***@host:port`.


**Measured so far (2026-11-24): the v2 inpaint returned near-digital silence for a silent hole.** One paid run (`run2`, songset `2ZV5SHjAzw7ceJE-nREs2`, junction 2, 4 beats) submitted, polled, downloaded and cropped end to end for 100 credits, and for a hole of pure digital zeros Treblo returned 3.3437 s at **peak 0.0015 (50/32768), −88.52 dBFS RMS overall** — a faint onset in the first 0.17 s (−75.71 dBFS) over near-silence (−103.50 dBFS for the remainder). After the 94.7 ms zero-pad the pre-staging glue measures −88.64 dBFS (`glue_rms_db` is taken *before* `stage_edges`), which the level gate refuses (`glue level -88.628 dBFS is below -50 dBFS`), so nothing is spliced; staging alone would have lifted it to −82.77 dBFS, which is why the gate reads the pre-staging level. The raw bytes in `cache/treblo-responses/*.bin` are that near-silence, so the response is the vendor's output, not the splice: geometry (`crop`), the pad, staging and the gate all behaved as designed.

Two rival explanations remain **untested** — do not assume one:

1. *Silence in the hole.* v2 appears to treat the masked region's silence as content to keep. The discriminating probe is one request over a section holding **real** audio from the same cached upload (e.g. `sections=[[0.0, 3.44]]` over A's tail): real music back means the hole content was the cause.
2. *`instrumental: true` inpaint.* Every historically working v2 inpaint config known to us uses `tags` plus `lyrics` with `instrumental: false`; no working instrumental-mode inpaint has been observed. If (1) also fails, this is the remaining suspect.

Note the parameter semantics when designing a retry: `balance_strength` is documented as *vocal naturalness vs. instrumental sharpness* (higher = more natural vocals), **not** a reference/hole weighting, and v2 inpaint rejects `prompt` outright — it wants `tags` plus either `lyrics` or `instrumental: true`. The v3 routes (`operation: replace|insert`, `prefix_context_seconds`/`suffix_context_seconds`) are in the OpenAPI schema but the docs state section replacement runs on v2 only ("replace for v3 is coming soon"), so there is no second endpoint to fall back to today. Each further paid probe costs another 100 credits.

Uploads fail content matching across the library — of four arranged songsets (7 junctions), only `2ZV5SHjAzw7ceJE-nREs2` junction 2 accepts its montage. Treat `run2` as the paid negative result, not a quality reference.

Outputs under `<out-root>/<run-name>`:

```
context/        song-01-<hash8>.flac ... — every arranged side (splice sources)
uploads/        canonical 44.1 kHz stereo WAVs that were uploaded (content-addressed)
uploads.json    full SHA-256 -> {audio_upload_id, seconds, format, uploaded_at} (shared by runs)
cache/          decoded source wavs (shared by runs) and cache/treblo-responses/ paid responses
full-set/       <take>.flac (all junctions at that length) and baseline.flac
junctions/jN/   per junction: montages/ plans/ responses/ glues/ full/ seams/
index.json      every request field, seed and measured metric, per junction and for the full set
report.html     self-contained listening + scoring page, one section per junction
```

Listening works the same way as the ElevenLabs page above — serve the run directory over HTTP and open `report.html`.

## gen_glue_simdps.py — Glue guided by the incoming instrumental stem

The CLI loads a 2–5 song Songset from Postgres and reads the original mixes from R2. The montage contains the outgoing arrangement's last 12 seconds, silence for the requested beats, and the incoming arrangement's first 12 seconds. Each invocation selects one backend. Modal searches the full arranged instrumental stem and uses the full incoming arrangement when the stem is missing. RunPod searches the first 18 arranged seconds of the stem, or of the incoming mix when the stem is missing or too short. The incoming mix can contain vocals. RunPod checks the model window and guide length before submitting each take.

Both backends produce an exact-length 44.1 kHz stereo float WAV. The pinned SimDPS sampler uses a 15-second model window around the gap and requires context beyond the latent mask on both sides. The Modal app uses Python 3.11, an L4, and the ACE-Step 1.5 base prior. Its first invocation downloads the model weights to the `sow-simdps-models` Modal volume. [Pinned SimDPS API](https://github.com/s-turland/SimDPSInpainting/blob/f5175f14b3be67e28ffc7c95874265a0390a9ada/simdps/inpainter.py)

Set `SOW_DATABASE_URL`, `SOW_R2_BUCKET`, `SOW_R2_ENDPOINT_URL`, `SOW_R2_ACCESS_KEY_ID` and `SOW_R2_SECRET_ACCESS_KEY` in the shell or `/opt/sow/.env`. The shell takes precedence. Modal also needs an authenticated profile or Modal token and the deployed `sow-simdps-poc` app.

For RunPod, set `RUNPOD_ENDPOINT_ID`, `RUNPOD_API_KEY` and `SIMDPS_POC_R2_BUCKET` in the same environment or file. Use the isolated L4 test endpoint with the pinned SimDPS worker image. Its `SIMDPS_R2_HOST` and `SIMDPS_R2_BUCKET` must point to the scratch R2 host and bucket. The scratch bucket must differ from the catalog bucket and allow the configured credentials to stage inputs and read verified results. The fixture writes under `glue-simdps-tmp/poc/`, covered by the scratch bucket's one-day lifecycle rule. [RunPod endpoint and scratch setup](../../docs/glue-operator-runbook.md#runpod-staging-and-rollout)

```bash
uv run --project lab/poc-scripts --python 3.11 --extra audio_inpaint \
  modal deploy lab/poc-scripts/modal_apps/simdps_inpaint_app.py

uv run --project lab/poc-scripts --python 3.11 --extra audio_inpaint \
  python lab/poc-scripts/gen_glue_simdps.py --dry-run --dry-run-items 5

uv run --project lab/poc-scripts --python 3.11 --extra audio_inpaint \
  python lab/poc-scripts/gen_glue_simdps.py --provider modal --production-duration \
    --songset hbWfCccGxN_JLQGBEqmdu --junctions 1 --beats 4 --takes 1 --seed-base 0 \
    --steps 100 --run-name modal-l4-production

uv run --project lab/poc-scripts --python 3.11 --extra audio_inpaint \
  python lab/poc-scripts/gen_glue_simdps.py --provider runpod --production-duration \
    --songset hbWfCccGxN_JLQGBEqmdu --junctions 1 --beats 4 --takes 1 --seed-base 0 \
    --steps 100 --run-name runpod-l4-production
```

| Flag | Default |
|---|---|
| `--songset` | `hbWfCccGxN_JLQGBEqmdu` |
| `--junctions` | All adjacent junctions, or a comma-separated list starting at 1 |
| `--beats` | `4` beats at the incoming song's tempo. Accepts a comma-separated list. |
| `--takes` | `1` per beat count and junction |
| `--seed-base` | `0`, incremented for each take at a junction |
| `--context-seconds` | `12` seconds per side, capped at the duration of each arrangement |
| `--steps` | `100` sampler steps |
| `--provider` | `modal`. RunPod uses 100 steps, up to 12 seconds of context per side, and nonnegative signed 64-bit seeds. |
| `--production-duration` | Off. Uses a minimum gap of 3 seconds and production millisecond rounding when enabled. |
| `--out-root` | `lab/poc-scripts/output/simdps-glue` |
| `--run-name` | `run1` |
| `--force` | Allow reuse of an existing run directory and replace overlapping artifacts. The report and index list the current run's artifacts. |

`context/` contains the original arranged mixes. Each `junctions/jN/` has montages, raw float WAV fills, staged FLAC fills, seam excerpts, and complete pair splices alongside the production gap or crossfade baseline. The staged gain and fades apply to the fill only. RunPod also writes a production-staged WAV and its full pair when the fill matches the production duration. `full-set/` contains the production baseline and an assembled version for each beat count and take with a usable fill at every junction.

Both `index.json` and `report.html` show the backend, duration mode, guide source and length per submitted take, reported GPU, and available timing measurements. Only a reported **NVIDIA L4** passes the GPU check. A verified fill remains playable with **GPU unverified** when the worker status lacks device metadata. Take failures are recorded in both files and the CLI exits with status `2` if any take fails. Dry runs work offline without credentials.

```bash
python -m http.server 8092 --bind 127.0.0.1 \
  --directory lab/poc-scripts/output/simdps-glue/modal-l4-production
```

Open `http://127.0.0.1:8092/report.html`. Compare the baseline against the generated seam and full pair. Listen to the raw fill in isolation before marking **vocals heard**. ACE-Step is unconditional and can generate vocals even when the search source is instrumental. Scores, notes, and checkbox state are stored in the browser and can be exported as JSON.

### Reference run, 2026-11-25

Junction 1 of `hbWfCccGxN_JLQGBEqmdu` used four beats at 86.13281 BPM, seed 0 and 100 steps on an NVIDIA L4. SimDPS sampled for 29.655 seconds and returned exactly 122,880 stereo frames at 44.1 kHz. The generated pair and full-set FLACs both matched the two arranged context files sample for sample. The raw and staged fills played in Chrome.

Automated vocal checks were inconclusive. YAMNet gave singing a score below 0.001 in every window of both fills. Essentia's voice/instrumental classifier gave voice scores of 0.514 for the raw fill and 0.370 for the staged fill. Audible vocals still need a human listening verdict. [YAMNet](https://www.tensorflow.org/hub/tutorials/yamnet) · [Essentia classifier](https://essentia.upf.edu/models/classifiers/voice_instrumental/)

### Production-duration verification

- Modal reported an NVIDIA L4 and returned exactly 132,300 stereo frames at 44.1 kHz for the same take with `--production-duration`. Sampling took 28.75 seconds. Both song sides matched the arranged context files in the full pair and the full set.
- RunPod reported an NVIDIA L4 and returned exactly 132,300 stereo frames at 44.1 kHz for the same take, run against endpoint `ry7hkdvvq7cvj7` on image `ghcr.io/mhuang74/stream_of_worship-simdps-worker@sha256:23fbb0f528633b2a5034166c54efadd83246d8bf6d94d2ed5b921b61fd051cc6`. Sampling took 18.694 seconds, model load 9.055 seconds, and the take 22.607 seconds of processing and 50.38 seconds of wall time. The guide was the instrumental stem clamped to the 18-second request cap (793,800 frames) instead of Modal's 7,819,371-frame stem, so the search window matched while the earlier part of the incoming arrangement was not visible. Both song sides matched the arranged context files sample for sample in the full pair, the production pair, and the 326.82-second full set, and production staging returned `ok`. Peak was 0.346 with joint level steps 1.10 / 2.00 dB. A human listen heard no vocals in the raw fill, a discontinuity at joint A (Here I Bow → fill) only, and judged the take equivalent to the Modal reference. That endpoint raised `idleTimeout` to 300 seconds so a cold-start failure could be retried warm, unlike the production endpoint's 10 seconds, which `.github/workflows/check-simdps-capacity.yml` asserts; the worker is otherwise identical. The stored `--provider modal` artifacts never carried a staged WAV, so `junctions/j1/glues/beats-4-production.wav` is the first live check of the production staging path, and it writes PCM_16 by design (`glue_staging._wav_bytes`) rather than the fixture's FLOAT raw fill.
