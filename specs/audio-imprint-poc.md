# Plan: Audio Imprinting POC — ACE-Step 1.5 repaint seams for songset hbWfCccGxN_JLQGBEqmdu

## Context

Build a throwaway POC that generates **replacement seam music** between the two songs of songset
`hbWfCccGxN_JLQGBEqmdu` ("Small Group Morning Fortress"), plus one intra-song component seam, so the user
can listen to AI-generated alternatives and validate the idea of "audio imprinting": new music spliced into
the seam, conditioned on the real recordings on both sides (repaint), replacing the current 1.39 s of silence.

Wavespeed's `ace-step-1.5` is text-only (`tags`, `lyrics`, `duration`, `seed` — no audio input; the
`ace-step/audio-to-audio|inpaint|outpaint` endpoints are 404), so generation runs **self-hosted ACE-Step 1.5 on
Modal** using `task_type="repaint"`, which conditions on source audio. End state: 15 seam clips + a static
listening page, with every prompt, seed, parameter and measured metric recorded in a JSON index.

## Verified facts the implementation depends on

Songset (queried live; `songset_items` ordered by `position`, `arrangement_steps` is a JSON text column):

| | item 0 | item 1 |
|---|---|---|
| `song_id` | `hereibow_a9271bf4` | `hereforyou_62e79ae9` |
| title | Here I Bow | Here for You |
| `recording_hash_prefix` | `6a410a5c222a` | `20379ea26256` |
| `r2_audio_url` | `s3://stream-of-worship/6a410a5c222a/audio.mp3` | `s3://stream-of-worship/20379ea26256/audio.mp3` |
| recording duration / tempo / key / loudness | 263.6858 s / 68 BPM / D major / −12.66 dB | 579.5236 s / 86.13 BPM / C major / −19.85 dB |
| `arrangement_steps` first→last step | last: `{"sectionIndex":1,"label":"chorus","startSeconds":40.55,"endSeconds":71.85}` | first: `{"sectionIndex":0,"label":"chorus","startSeconds":0.7,"endSeconds":22.04}` |
| boundary `song_components` row | chorus occ 1, role `entry`, 38.82–73.59, energy −16.59 dB, bpm 84.72, key D, theme 敬拜, posture To God | chorus occ 1, role `entry`, 113.78–139.20, energy −18.04 dB, bpm 84.72, key C, theme 敬拜, posture To God |
| `gap_beats` / crossfade | 2 / off → 1.393 s of silence (`audio_engine.calculate_gap_ms`, 2 beats @ 86.13 BPM) | — |

Consequence to respect: the two **boundary components share one tempo (84.72 BPM)** — the 68-vs-86 gap is a
recording-level half/double artifact. The real seam differences are **key (D → C)** and **level
(−16.59 → −18.04 dB)**. Do not plan atempo work; tempo is context-carried by repaint.

ACE-Step 1.5 (pin commit `ca1e85fe9430179831e6bc6be790c332190a3866`, 2026-08-29):
`acestep.handler.AceStepHandler().initialize_service(project_root, config_path, device)`,
`acestep.llm_inference.LLMHandler().initialize(checkpoint_dir, lm_model_path, backend, device)`,
`acestep.inference.generate_music(dit_handler, llm_handler, params, config, save_dir=...)`,
`GenerationParams(task_type="repaint", src_audio=<abs path>, repainting_start=<s>, repainting_end=<s>,
chunk_mask_mode="explicit", caption=..., bpm=..., keyscale=..., duration=..., seed=..., thinking=False,
use_cot_metas=False, use_cot_caption=False, use_cot_language=False, instrumental=<bool>, lyrics=...)`,
`GenerationConfig(batch_size=1, use_random_seed=False, seeds=[seed], audio_format="flac")`.
Weights are ungated: bundle `ACE-Step/Ace-Step1.5` (vae + Qwen3-Embedding-0.6B + acestep-v15-turbo +
acestep-5Hz-lm-1.7B) and `ACE-Step/acestep-5Hz-lm-0.6B`; downloader CLI `acestep-download --dir <ckpt> [--model X --skip-main]`.
This repo already runs 5 Modal apps on `gpu="L4"`; `modal` CLI is authenticated on this host.

## Approach

### Step 1 — Modal app hosting ACE-Step repaint

Create `lab/poc-scripts/modal_apps/acestep_repaint_app.py`, app name `sow-acestep-poc`, volume
`sow-acestep-models` mounted at `/models`.

Image (no local clone needed — clone inside the build):

```python
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "ffmpeg", "libsndfile1", "build-essential")
    .run_commands(
        "git clone https://github.com/ACE-Step/ACE-Step-1.5.git /opt/ace-step",
        "cd /opt/ace-step && git checkout ca1e85fe9430179831e6bc6be790c332190a3866",
        "pip install 'uv==0.7.*'",
        "cd /opt/ace-step && uv sync --frozen --no-dev",
    )
)
```

Do **not** copy the repo's CUDA Dockerfile; `uv sync --frozen` is the repo's own supported install and pins
torch via `uv.lock`. `L4` + 2B turbo needs no quantization/offload.

`@app.cls(image=image, gpu="L4", cpu=8, memory=32768, timeout=1800, startup_timeout=7200,
scaledown_window=600, max_containers=1, volumes={"/models": model_volume})`.

`@modal.enter()` `bootstrap()`:
1. If `/models/acestep-v15-turbo` is absent: `/opt/ace-step/.venv/bin/acestep-download --dir /models` then
   `/opt/ace-step/.venv/bin/acestep-download --dir /models --model acestep-5Hz-lm-0.6B --skip-main`, then
   `model_volume.commit()`.
2. `sys.path.insert(0, "/opt/ace-step")`; `AceStepHandler().initialize_service(project_root="/opt/ace-step",
   config_path="acestep-v15-turbo", device="cuda")`; `LLMHandler().initialize(checkpoint_dir="/models",
   lm_model_path="acestep-5Hz-lm-0.6B", backend="pt", device="cuda")`. The LM is never used for repaint
   (`thinking=False`, all `use_cot_*` False); it exists only because `generate_music` takes a handler.
3. Keep the imported `GenerationParams`/`GenerationConfig`/`generate_music` on `self`.

Single remote method — synchronous, no HTTP layer (matches the other 5 `modal_apps/` classes):

```python
@modal.method()
def repaint(self, audio_bytes: bytes, filename: str, request: dict) -> dict:
    # request keys: caption (str), lyrics (str), instrumental (bool), bpm (int),
    #   keyscale (str), duration (float), seed (int), repaint_start (float), repaint_end (float)
    # writes audio_bytes to a fresh tempfile, calls generate_music(save_dir=tmpout),
    # returns {"audio": <first output file bytes, flac>, "meta": {seed, duration, generation_info, dit_model}}
```

Failure semantics: raise `RuntimeError(result.error)` when `not result.success`; raise when zero outputs.
Empty `audio_bytes`, a `request` missing a key, or `repaint_end <= repaint_start` → `ValueError` before any
GPU work (no silent fallbacks).

### Step 2 — POC dependency extra

In `lab/poc-scripts/pyproject.toml` under `[project.optional-dependencies]` add:

```toml
audio_imprint = [
    "modal>=1.2.6,<2",
    "openai>=1.40.0",
    "librosa>=0.10.0",
    "soundfile>=0.13.1",
    "numpy>=1.26.0",
    "boto3>=1.34.0",
]
```

`psycopg[binary]` arrives from the existing base dependency `stream-of-worship[postgres]`; do not re-add it.

### Step 3 — POC script: read the songset, fetch audio

Create `lab/poc-scripts/gen_audio_imprint.py` (single file, `argparse`, no new package). Reuse, do not
re-invent:

- DB: `psycopg.connect(os.environ["SOW_DATABASE_URL"])` (read-only `SELECT`s; the DSN already carries the
  password). No `ConnectionProvider`/`ReadOnlyClient` needed.
- R2: `from stream_of_worship.admin.services.r2 import R2Client`;
  `R2Client(bucket=os.environ["SOW_R2_BUCKET"], endpoint_url=os.environ["SOW_R2_ENDPOINT_URL"], region="auto")`
  then `client.download_audio(hash_prefix, dest_path)` (fetches `{hash_prefix}/audio.mp3`).
- Decode/encode with the `ffmpeg` CLI (8.1.2 on PATH), PCM in memory via `soundfile` + `numpy`:

```
ffmpeg -v error -y -i <audio.mp3> -ar 44100 -ac 2 -c:a pcm_s16le <cache>/<hash_prefix>.wav
```

Cache downloaded mp3 and decoded wav under `<out>/cache/`; skip both when the file already exists.

SQL (exact; one statement per fact so each failure names itself):

```sql
SELECT position, song_id, recording_hash_prefix, gap_beats, crossfade_enabled,
       arrangement_steps
  FROM songset_items WHERE songset_id = %s ORDER BY position;
SELECT hash_prefix, duration_seconds, tempo_bpm, musical_key, musical_mode, loudness_db
  FROM recordings WHERE hash_prefix = ANY(%s);
SELECT component_type, occurrence_index, role, start_time, end_time, energy_level, bpm, key,
       theme, vocal_posture
  FROM song_components WHERE content_hash = ANY(%s) AND start_time IS NOT NULL
   AND end_time IS NOT NULL ORDER BY content_hash, start_time;
```

(`content_hash` comes from `SELECT content_hash FROM recordings WHERE hash_prefix = ANY(%s)`.)

Build a `Seam` record per seam with these exact windows (sample offsets = `round(seconds * 44100)`), clamped
into the owning arrangement step / component (`max(step.start, t)` / `min(step.end, t)`):

| seam | source A window (15 s) | montage A part (8 s) | hole | montage B part (8 s) | source B window (15 s) |
|---|---|---|---|---|---|
| boundary 0→1 | A `[56.85, 71.85]` | A `[63.85, 71.85]` | `[8.0, 8.0+N)` s of digital silence | B `[0.7, 8.7]` | B `[0.7, 15.7]` |
| intra (Here I Bow verse 1 → chorus 1 at 38.82 s) | A `[23.82, 38.82]` | A `[30.82, 38.82]` | `[8.0, 16.0)` s | B `[38.82, 46.82]` | B `[38.82, 53.82]` |

Boundary windows come from item 0's **last** arrangement step (`endSeconds` 71.85) and item 1's **first**
step (`startSeconds` 0.7); intra windows come from `song_components` `end_time`/`start_time` of the chosen
adjacent pair. Selected clip durations are therefore `N + 30` s (28 s of real context + N of generated):
38 s for `N=8`, 46 s for `N=16`.

### Step 4 — Caption authoring (LLM, cached, editable)

`<out>/captions.json` is both input and output — if it exists and `--refresh-captions` is not passed, it is
reused verbatim (this is how the user retunes prompts without touching code).

Schema (exact):

```json
{"version": 1, "source": "llm" | "fallback", "model": "<SOW_LLM_MODEL>",
 "captions": {"<scope>/<arc>/<treatment>/<left|right>": {"caption": "..."}}}
```

Keys: `scope ∈ {boundary, intra}`, `arc ∈ {keep-intensity, settle-then-lift, quiet-interlude}`,
`treatment ∈ {inst, texture}` (intra: `inst` only → 6 keys), side ∈ {left, right}. Total 18 keys.

Authoring call: `openai.OpenAI(api_key=os.environ["SOW_LLM_API_KEY"], base_url=os.environ["SOW_LLM_BASE_URL"])`,
`chat.completions.create(model=<SOW_LLM_MODEL with any ":effort" suffix stripped via
stream_of_worship.admin.services.llm_models.split_llm_model_effort>, messages=[...],
response_format={"type": "json_object"}, max_tokens=2000, extra_body={"reasoning_effort": effort} when
present)`. One call; the user message carries the verified seam facts (titles, keys, bpm 84.72, energy dB,
themes 敬拜, posture To God, the arrangement labels) and these constraints: English; ≤ 300 characters;
comma-separated style descriptors; name instruments, density, dynamics; **do not invent key/bpm values**
(they are passed as parameters, not prose); `left` = must follow Here I Bow's chorus seamlessly;
`right` = must lead into Here for You's chorus; `inst` = no voices at all; `texture` = wordless
"ooh/ahh" vocal pad, no words.

Validation: all 18 keys present, each a non-empty string ≤ 300 chars; on any failure (missing env, HTTP
error, bad JSON, missing key) write the fallback captions below and set `"source": "fallback"`. Do not crash.

Fallback captions (write verbatim if that happens):

| key | caption |
|---|---|
| boundary/keep-intensity/inst/left | "worship band, steady 4/4 eighth-note piano, warm bass, acoustic guitar, full kit, sustained pad, no vocals, continuous drive, no build" |
| boundary/keep-intensity/inst/right | "same worship band texture, steady eighth-note piano and warm bass, full kit, sustained pad, no vocals, sustained intensity leading forward" |
| boundary/keep-intensity/texture/left | "worship band with wordless ooh vocal pad, eighth-note piano, warm bass, soft kit, sustained, no words" |
| boundary/keep-intensity/texture/right | "wordless ahh vocal pad over eighth-note piano and warm bass, soft kit, sustained, no words" |
| boundary/settle-then-lift/inst/left | "worship band settling, piano and pad, drums dropping out, warm bass, no vocals, gentle release" |
| boundary/settle-then-lift/inst/right | "worship band rebuilding, piano pulse returning, kick entering, warm bass, no vocals, lifting" |
| boundary/settle-then-lift/texture/left | "settling worship texture, wordless ooh pad, piano, drums dropping out, no words" |
| boundary/settle-then-lift/texture/right | "rebuilding worship texture, wordless ahh pad, piano pulse, light kick, no words" |
| boundary/quiet-interlude/inst/left | "solo piano and soft pad, spacious, slow arpeggios, no drums, no bass, no vocals" |
| boundary/quiet-interlude/inst/right | "solo piano and soft pad, spacious, light arpeggios building gently, no drums, no vocals" |
| boundary/quiet-interlude/texture/left | "spacious piano and pad with faint wordless ooh vocal, no drums, no words" |
| boundary/quiet-interlude/texture/right | "spacious piano and pad with faint wordless ahh vocal, gently building, no words" |
| intra/keep-intensity/inst/left | "worship band verse texture, sparse piano, light kit, warm bass, no vocals, steady build" |
| intra/keep-intensity/inst/right | "worship band chorus texture, fuller piano and kit, warm bass, no vocals, continuous energy" |
| intra/settle-then-lift/inst/left | "worship band pulling back before the chorus, piano and pad, drums dropping out, no vocals" |
| intra/settle-then-lift/inst/right | "worship band lifting into the chorus, piano chords, kick entering, no vocals, rising" |
| intra/quiet-interlude/inst/left | "sparse piano and pad, verse ending, no drums, no vocals, spacious" |
| intra/quiet-interlude/inst/right | "sparse piano and pad resolving into a fuller chorus entry, no drums, no vocals" |

### Step 5 — Repaint orchestration (sequential halves)

Variant ids: `boundary__<arc>__<N>s__<treatment>` and `intra__<arc>__8s__inst`.
Matrix: boundary 3 arcs × {8, 16} × {inst, texture} = 12; intra 3 arcs × 8 s × inst = 3. **15 clips,
30 repaint calls.**

Per variant, two chained remote calls (both keep full both-side context; the second sees the first's output):

1. pass `left` — src = montage, `repaint_start=8.0`, `repaint_end=8.0 + N/2`, `bpm=85`,
   `keyscale="D Major"` (boundary) / the pair's shared key (intra), `duration=len(montage)`,
   `seed=<--seed>`, caption = `.../left`.
2. pass `right` — src = pass-1 FLAC, `repaint_start=8.0 + N/2`, `repaint_end=8.0 + N`,
   `bpm=85`, `keyscale="C Major"` (boundary) / same shared key (intra), `duration=len(montage)`,
   `seed=<--seed + 1>`, caption = `.../right`.

`lyrics`/`instrumental`: `inst` → `instrumental=True`, `lyrics="[Instrumental]"`; `texture` →
`instrumental=False`, `lyrics="[Chorus]\nOoh, ooh, ahh, ooh"` (bronze rule: no real words), `vocal_language="en"`.

Length guard: decode the returned FLAC; if `|duration − montage_duration| > 0.05 s`, trim or zero-pad the
tail to the montage duration and record `"length_corrected": true` in the index. Retries: 2 attempts per
remote call with 5 s backoff; after that mark the variant `"status": "failed"` with the error string, keep
going, and exit non-zero only if every variant failed.

Idempotency: skip a variant when `<out>/clips/<id>.flac` exists unless `--force`.

CLI (exact flags — no others):

```
--songset hbWfCccGxN_JLQGBEqmdu   --out lab/poc-scripts/output/audio-imprint/run1
--arcs keep-intensity,settle-then-lift,quiet-interlude
--lengths 8,16                    --treatments inst,texture
--seed 4242                       --force        --refresh-captions
--skip-intra                      --dry-run
```

`--dry-run`: build montages + captions + index skeleton, make zero Modal calls (used as the preflight).

### Step 6 — Gain staging, clip assembly, outputs

Gain: measure `rms_db = 20*log10(rms)` over the last 4 s of the A montage part and the first 4 s of the B
montage part (these regions are untouched by repaint). Apply a linear-in-dB ramp across the hole from the A
level to the B level, computed on 10 ms frames and linearly interpolated per sample. This is the "imprint":
A's level hands over to B's level through generated music instead of through 1.39 s of silence.

Assemble `<out>/clips/<id>.flac` = `A_source_window[:7 s]` + repainted montage (`8+N+8` s) + `B_source_window[7 s:]`,
with 5 ms equal-power fades only at those two joins; if peak > 0.98 scale the whole clip. Write
`<out>/bridges/<id>.flac` = the gained hole region only, and `<out>/montages/<id>.flac` = the uploaded source
(both for inspection). All FLAC, 44.1 kHz, stereo, PCM_16.

Metrics per variant (stored, not judged): `bpm` of the hole via
`librosa.beat.beat_track(y=..., sr=44100)`, `rms_db` at four probe points (A tail, hole first third,
hole last third, B head), `duration_seconds` of bridge and clip, both seeds, the two captions, the full
`request` dict, and the returned `meta`. Do **not** claim key detection.

`<out>/index.json`:

```json
{"version": 1, "songset_id": "...", "created_at": "...", "model": "acestep-v15-turbo",
 "app": "sow-acestep-poc", "seed_base": 4242,
 "variants": [{"id": "...", "scope": "boundary", "arc": "...", "length_seconds": 8,
               "treatment": "inst", "status": "ok", "clip": "clips/<id>.flac",
               "bridge": "bridges/<id>.flac", "montage": "montages/<id>.flac",
               "captions": {"left": "...", "right": "..."}, "requests": [{...}, {...}],
               "metrics": {...}, "length_corrected": false, "error": null}]}
```

`<out>/report.html`: one self-contained file, relative `src` paths, no JS framework, grouped
`Boundary (8 s)`, `Boundary (16 s)`, `Intra-song (8 s)`; per variant `<audio controls preload="none">` for the
clip and a second one for the bridge, the arc/treatment/seed shown, both captions in `<details>`, and the
metrics as one line. Title states the songs and the seam level/key facts. No external CDNs.

### Step 7 — Intra-song seam

Implemented by the same `Seam` builder, not a second script. Pair selection: among the songset recordings'
` song_components` with non-null `start_time`/`end_time`/`energy_level`, take the pair that is adjacent in
`start_time` order with the largest `|Δenergy_level|`. For this songset that yields Here I Bow verse 1
(11.01–38.82, −20.53 dB) → chorus 1 (38.82–73.59, −16.59 dB), Δ = 3.94 dB — assert it in the run log; if the
query yields nothing, log a warning and skip the intra variants (boundary variants still run).

### Step 8 — Domain term

Add to `CONTEXT.md` under `### Songset & Transitions`, immediately after **Transition**:

```markdown
**Audio Imprint**:
Newly generated music that occupies the seam between two adjacent songs, produced by conditioning a music
model on the recorded material immediately before and after that seam. An Audio Imprint replaces the silent
gap rather than fading one recording into the other, and leaves both songs' arranged material intact.
_Avoid_: AI transition, generated bridge, stitch
```

No ADR: the decision is reversible (POC, nothing in production consumes it).

### Step 9 — Session completion

Run `graphify update .`, then commit via the eval `commit` namespace (draft + commit), `git pull --rebase`,
`git push`; `git status` must end up to date. Generated media under `lab/poc-scripts/output/` is already
gitignored — do not force-add it.

## Critical files & anchors

- `lab/poc-scripts/modal_apps/acestep_repaint_app.py` — new; only GPU boundary; deploy/pin/bootstrapping.
- `lab/poc-scripts/gen_audio_imprint.py` — new; all DB/R2/DSP/orchestration/report logic.
- `lab/poc-scripts/pyproject.toml:34-140` — `[project.optional-dependencies]`, add `audio_imprint`.
- `ops/admin-cli/src/stream_of_worship/admin/services/r2.py:112,419` — `R2Client`, `download_audio`.
- `ops/analysis-service/modal_apps/stem_separator_app.py:38-56` — the `@app.cls(...)` shape to copy
  (`gpu="L4"`, volumes, `max_containers`, `timeout`, `startup_timeout`, deliberate absence of `retries=`).
- `CONTEXT.md` — `### Songset & Transitions` section for the new glossary entry.

## Verification

Prerequisites for every command: `cd /home/mhuang/Development/sow_feature_branch_2 && set -a && . /opt/sow/.env && set +a`.

1. **App deploys**: `uv run --no-project --with 'modal>=1.2.6,<2' modal deploy lab/poc-scripts/modal_apps/acestep_repaint_app.py`
   → then `modal app list` shows `sow-acestep-poc` `deployed`.
2. **Preflight**: `uv run --project lab/poc-scripts --extra audio_imprint python lab/poc-scripts/gen_audio_imprint.py --songset hbWfCccGxN_JLQGBEqmdu --out lab/poc-scripts/output/audio-imprint/run1 --dry-run`
   → `montages/` holds `boundary__keep-intensity__8s__inst.flac` (24.0 s), `…__16s__inst.flac` (32.0 s),
   `intra__…__8s__inst.flac` (24.0 s); `captions.json` has 18 keys (non-empty, ≤300 chars); `clips/` empty;
   zero Modal calls; log line `intra pair: verse1 -> chorus1, delta=3.94 dB`.
3. **Smoke (2 GPU calls)**: same command with `--lengths 8 --treatments inst --arcs keep-intensity --skip-intra`
   → `clips/boundary__keep-intensity__8s__inst.flac` exists at 38.0 ± 0.05 s; its first 7.0 s and last 7.0 s
   are sample-identical to the corresponding source slices (compare decoded arrays; only the 5 ms fade edges
   may differ); hole RMS sits between the two measured endpoint levels; `index.json` records both seeds and
   `"status": "ok"`.
4. **Full run**: drop the narrowing flags (keep the same `--out`) → 15 variants, 30 calls, `index.json` has 15
   `"status": "ok"` entries with clip durations in {38.0, 46.0} s; total GPU wall time and cold start logged.
5. **Listenable proof**: serve `python3 -m http.server 8765 --directory lab/poc-scripts/output/audio-imprint/run1`
   and open `http://127.0.0.1:8765/report.html` in a browser tab: 15 clip players + 15 bridge players render,
   each `<audio>` reports a finite `duration` (no 404s), and the page groups Boundary 8 s / 16 s / Intra.
6. **Glossary**: `grep -n "Audio Imprint" CONTEXT.md` returns the new entry.

## Assumptions & contingencies

- **ACE-Step API surface** is taken from the pinned commit's `docs/en/INFERENCE.md`; if a parameter name
  differs at `ca1e85f`, read that file in the image and adapt the `request` dict — the POC-side contract
  (method name, `request` keys) stays fixed.
- **Image build**: if `uv sync --frozen --no-dev` fails inside Modal, fall back to
  `uv_pip_install("-r /opt/ace-step/requirements.txt", "-e /opt/ace-step --no-deps",
  "nano-vllm @ file:///opt/ace-step/acestep/third_parts/nano-vllm")`; record which path the image took in a
  module docstring line.
- **LM handler**: if initializing `acestep-5Hz-lm-0.6B` with `backend="pt"` fails, set
  `ACESTEP_INIT_LLM=false` in the image env and pass the uninitialized handler — repaint ignores the LM by
  design; if `generate_music` then errors on a null LM, initialize `acestep-5Hz-lm-1.7B` instead (already in
  the main bundle, no extra download).
- **Repaint length drift**: after trim/pad, if any clip still deviates > 0.5 s, retry that variant with
  `chunk_mask_mode="auto"` and record the switch in `requests`.
- **LLM authoring**: `.env`'s own note says the neuralwatt key is for non-embedding POC use; if the caption
  call fails for any reason the fallback table above is written and `"source": "fallback"` makes it visible.
- **Prompt iteration** is expected: `captions.json` is an input, so re-running with edited captions and
  `--force` regenerates clips without code changes or a redeploy.

## Status: delivered (2026-11-20)

Delivered on branch `audio_imprint_poc`: `lab/poc-scripts/modal_apps/acestep_repaint_app.py`,
`lab/poc-scripts/gen_audio_imprint.py`, the `audio_imprint` extra, and the **Audio Imprint** glossary entry
in `CONTEXT.md`.

Departures from the plan as written:

- **GPU is `A10G`, not `L4`.** L4 inputs stayed queued for 20+ min with `Active Containers: None` while
  T4/A10G/A100 launched instantly, so the app was redeployed on A10G; no other change.
- **`llm_handler=None`.** `generate_music` guards every LM use and repaint skips the LM anyway, so the 0.6B
  checkpoint is never downloaded or initialized.
- **`peak_limit` ∈ {`none`, `clip`, `hole`} replaces "scale the whole clip".** The generated seam is trimmed
  to the 0.98 ceiling first so the recorded material stays sample-identical; only a recording already over
  the ceiling scales the clip. `hole_level_db_after` is measured **after** the guard (pre-guard it overstated
  clamped bridges by up to 1.5 dB).

Verified run (`run1`, songset `hbWfCccGxN_JLQGBEqmdu`): 15 variants / 30 repaint calls, wall 542.7 s,
GPU 65.4 s, 0 failures; clips exactly 38/46 s, bridges 8/16 s, 44.1 kHz stereo PCM_16; head and tail
bit-identical to the source slices outside the 5 ms fades (max |diff| 0.0); bridge equals its region inside
the clip (max |diff| 0.0); captions authored by `SOW_LLM_MODEL` (`source: "llm"`).
