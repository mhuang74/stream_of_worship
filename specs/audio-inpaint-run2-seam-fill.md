# Plan: Seam Fill run2 — DSP-first gap fill, Stable Audio 3 vs ACE-Step

## Context

Run1 asked ACE-Step 1.5 turbo to invent 8–16 s of new music per seam and it sounded wrong: no clip
resembled either recording (best chroma correlation against the real context was 0.12–0.16), the
stereo field was near-decorrelated, and the user rejected **all** variants including the same-key,
same-tempo intra-song pair. The run also had four concrete defects: pass 1 repainted half the hole
while its right-hand context was digital silence; pass 2 repainted the other half with a different
`keyscale`, butt-joining D-major and C-major material at the hole midpoint; `bpm=85` was sent for a
70 BPM outgoing song (component BPM is wrong; the recordings row says 68); and no `reference_audio`
was ever sent, so the model never heard the songs it was matching.

Run2 changes the unit of work to the production seam itself: **one 1.393 s gap** (2 beats at the
incoming song's 86.13 BPM) between Here I Bow (D major, 68 BPM) and Here for You (C major,
86.13 BPM). It renders four real-audio DSP baselines, then asks Stable Audio 3 Small-Music and ACE-Step
(turbo and base) to fill the same gap with a **drum fill only**, and produces a listening page. No
production code changes. User decisions already made: short gap fill, songs otherwise untouched, A may
be extended past its arrangement cut, intra-song seam dropped, drum-only prompts, ModelScope weight
route, listening page without blind scoring.

## Why this should sound better than run1

|run1|run2|
|---|---|
|8–16 s of new music per seam|1.393 s drum fill only|
|two passes, butt-joined at the hole midpoint|one pass over the whole hole|
|pass 1's right context was digital silence|real audio on both sides of the mask|
|no reference audio|30 s reference of both songs|
|`bpm=85` for a 68 BPM song|`bpm=86` (incoming), `keyscale="C Major"`|
|hard mask-edge splices|50 ms waveform crossfade + 10 latent frames|
|full band asked for|no melody/vocals; the model cannot invent a lead line or slur it|

The DSP baselines are the control: if the Tail Extension or the B pickup is clearly better than every
generated fill, the honest answer is "do not generate this seam" and the POC says so. A drum fill is
also the one texture where a synthesized gap can plausibly pass; if even that fails, that is decisive
evidence against seam inpainting for this material.

## Verified facts the implementation depends on

Songset `hbWfCccGxN_JLQGBEqmdu`, item 0 → item 1:

||item 0|item 1|
|---|---|---|
|`song_id`|`hereibow_a9271bf4`|`hereforyou_62e79ae9`|
|title|Here I Bow|Here for You|
|`recording_hash_prefix`|`6a410a5c222a`|`20379ea26256`|
|recording tempo / key|68 BPM / D major|86.13 BPM / C major|
|arrangement boundary|last step ends **71.85 s**|first step starts **0.7 s**|
|audio at the boundary|real chorus continues past 71.85 (71.85–73.24 RMS −18.1 dB)|0–0.7 s is a −38 dB lead-in; first downbeat 0.7 s, −25.2 dB|

- Production gap: `calculate_gap_ms(item, item.tempo_bpm)` = `round(2 * 60000 / 86.13)` = **1393 ms**
  (`delivery/render-worker/src/sow_render_worker/audio_engine.py:84`). Use `gap_ms=1393`, not the
  component BPM (both components wrongly report 84.72).
- A's post-cut audio 71.85–73.243 is the same chorus, so a zero-generation Tail Extension exists.
- Decoded 44.1 kHz stereo cache from run1 can be reused at
  `lab/poc-scripts/output/audio-inpaint/run1/cache/{6a410a5c222a,20379ea26256}.wav`; the new script
  still verifies/creates them under `run2/cache/` via the existing helper.

Stable Audio 3 (upstream `Stability-AI/stable-audio-3`, MIT code, pin
**`790793b89ddceea612be9ce42ff6830d18f6fa57`**):

- `small-music`: 433M params, SAME-Small autoencoder, 44.1 kHz stereo, 120 s max, post-trained
  (8 steps; `cfg_scale`/`negative_prompt` are ignored on post-trained checkpoints), CPU-capable but
  run on A10G.
- Inpainting API: `StableAudioModel.generate(inpaint_audio=(sr, tensor[C,T]), inpaint_mask_start_seconds=…,
  inpaint_mask_end_seconds=…, prompt=…, duration=…, steps=8, seed=…, duration_padding_sec=0.0,
  truncate_output_to_duration=True)`. Mask value 1 = preserve, 0 = regenerate; times are converted with
  `int(seconds * 44100)`.
- Loader: `StableAudioModel.from_pretrained` only resolves HF ids (gated). Local weights load via
  `stable_audio_3.loading_utils.load_diffusion_cond(model_config, ckpt_path, device="cuda",
  model_half=True)` then `StableAudioModel(model, model_config, "cuda", True)`.
- The T5Gemma text conditioner takes `model_path` (preferred over `repo_id`); its config also carries
  `subfolder: "t5gemma-b-b-ul2"`.
- ModelScope mirror serves all files without auth and supports ranged GETs (verified 206 responses):
  base `https://modelscope.cn/models/stabilityai/stable-audio-3-small-music/resolve/master/`,
  files `model.safetensors` (2.27 GB), `model_config.json`, `t5gemma-b-b-ul2/{config.json,
  model.safetensors, tokenizer.json, tokenizer_config.json, special_tokens_map.json, tokenizer.model,
  generation_config.json}` (1.18 GB weights).
- `stable-audio-3` is not on PyPI; install from the pinned Git clone. Its deps pin `torch==2.7.1`,
  `torchaudio==2.7.1`, `transformers>=5.8.0`; Flash Attention is optional (SDPA fallback) and only
  `medium` requires it.

ACE-Step 1.5 (app already deployed as `sow-acestep-poc`, pin `ca1e85fe…`):

- `GenerationParams` supports `reference_audio` (VAE-encoded and cross-attended; the handler's
  `process_reference_audio` samples 3×10 s from the file, and for a file of **exactly 30.0 s at 48 kHz**
  (1,440,000 samples) all three crop ranges collapse to zero, making the sampling deterministic — source
  verified), plus `repaint_wav_crossfade_sec` (default 0.0 = hard cut) and
  `repaint_latent_crossfade_frames` (default 10), `inference_steps`, `guidance_scale`, `use_adg`, `shift`.
- `acestep-v15-base` exists (`ACE-Step/acestep-v15-base`, ~4 GB) and the downloader supports
  `--model acestep-v15-base --skip-main`; docs recommend base + 32–64 steps + `use_adg=True`.
- `repaint` is a single-pass masked edit; unmasked regions are pinned to noised source latents and the
  decoded waveform is spliced back from the source outside the mask. A digital-silence hole is therefore
  valid context, but the fill must be generated in **one** pass over the whole hole.

## Approach

### Step 1 — Stable Audio 3 inpainting app

Create `lab/poc-scripts/modal_apps/sa3_inpaint_app.py`, app `sow-sa3-poc`, volume `sow-sa3-models`
mounted at `/models`, model dir `/models/sa3-small-music`.

Constants:

```python
APP_NAME = "sow-sa3-poc"
VOLUME_NAME = "sow-sa3-models"
MODEL_MOUNT = "/models"
MODEL_DIR = "/models/sa3-small-music"
REPO_DIR = "/opt/stable-audio-3"
REPO_COMMIT = "790793b89ddceea612be9ce42ff6830d18f6fa57"
MIRROR = "https://modelscope.cn/models/stabilityai/stable-audio-3-small-music/resolve/master"
FILES = (
    "model.safetensors", "model_config.json",
    "t5gemma-b-b-ul2/config.json", "t5gemma-b-b-ul2/model.safetensors",
    "t5gemma-b-b-ul2/tokenizer.json", "t5gemma-b-b-ul2/tokenizer_config.json",
    "t5gemma-b-b-ul2/special_tokens_map.json", "t5gemma-b-b-ul2/tokenizer.model",
    "t5gemma-b-b-ul2/generation_config.json",
)
REQUIRED_REQUEST_KEYS = ("prompt", "seed", "steps", "start_seconds", "end_seconds", "duration_seconds")
```

Image: `modal.Image.debian_slim(python_version="3.11").apt_install("git", "ffmpeg", "libsndfile1",
"curl").run_commands(git clone → checkout REPO_COMMIT, "cd /opt/stable-audio-3 && pip install
--no-cache-dir .")`. Do not install `stable-audio-tools`; do not add flash-attn.

`@app.cls(image=image, gpu="A10G", cpu=8, memory=16384, timeout=900, startup_timeout=3600,
scaledown_window=300, max_containers=1, volumes={"/models": model_volume})`; no `retries=`.

`@modal.enter()` `bootstrap()`:

1. If any of `FILES` is missing under `MODEL_DIR`, `mkdir -p` each parent and download with
   `subprocess.run(["curl", "-L", "--fail", "--retry", "3", "--retry-delay", "2", "-o", dest,
   f"{MIRROR}/{rel}"], check=True)`; then `model_volume.commit()`.
2. Read `<MODEL_DIR>/model_config.json`; for every entry of
   `cfg["model"]["conditioning"]["configs"]` with `"id" == "prompt"`, set
   `entry["config"]["model_path"] = MODEL_DIR` (keep `subfolder`), and write
   `<MODEL_DIR>/model_config.local.json` (always rewrite it, so a changed constant is picked up).
3. `sys.path.insert(0, REPO_DIR)`; `from stable_audio_3.loading_utils import load_diffusion_cond`;
   `from stable_audio_3.model import StableAudioModel`;
   `model = load_diffusion_cond(cfg, f"{MODEL_DIR}/model.safetensors", device="cuda", model_half=True)`;
   `self.sa = StableAudioModel(model, cfg, "cuda", True)`.

Class name **`Sa3Inpaint`** (the client resolves it by name). Method
`@modal.method() def inpaint(self, audio_bytes: bytes, filename: str, request: dict) -> dict`:

- Validate before GPU work: non-empty bytes; all `REQUIRED_REQUEST_KEYS` present; `0 <= start_seconds <
  end_seconds <= duration_seconds + 1e-6`; raise `ValueError` otherwise.
- Write bytes to a temp file, read with `soundfile.read(dtype="float32", always_2d=True)`; require
  `sr == 44100` and 2 channels (the client always sends decoded 44.1 kHz stereo FLAC).
- `wave = torch.from_numpy(samples.T.copy())` (shape `[2, T]`), call:

```python
out = self.sa.generate(
    inpaint_audio=(44100, wave),
    inpaint_mask_start_seconds=float(request["start_seconds"]),
    inpaint_mask_end_seconds=float(request["end_seconds"]),
    prompt=str(request["prompt"]),
    duration=float(request["duration_seconds"]),
    duration_padding_sec=0.0,
    steps=int(request["steps"]),
    seed=int(request["seed"]),
    batch_size=1,
    truncate_output_to_duration=True,
)
```

- `out` is `[1, 2, T]` float; convert to `[T, 2]` numpy, write FLAC (`PCM_16`, 44.1 kHz) via
  `soundfile`, return `{"audio": <bytes>, "meta": {"model": "small-music", "seed": …, "steps": …,
  "prompt": …, "duration_seconds": …, "sample_rate": 44100, "elapsed_seconds": …}}`.
- Raise `RuntimeError` if the returned tensor has fewer than `round(0.25 * 44100)` samples.

### Step 2 — ACE-Step app: reference audio, quality knobs, base model

Edit `lab/poc-scripts/modal_apps/acestep_repaint_app.py`:

1. Extract the existing `@modal.enter` body into a module-level helper
   `def _bootstrap(handler, dit_config: str, download_submodel: bool) -> dict` that: ensures the main
   bundle exists (existing behaviour), optionally runs `--model <dit_config> --skip-main` when
   `download_submodel`, initializes `AceStepHandler().initialize_service(project_root=REPO_DIR,
   config_path=dit_config, device="cuda")`, initializes `LLMHandler` exactly as today (0.6B then 1.7B
   fallback), and returns the imported `GenerationParams`/`GenerationConfig`/`generate_music` plus the
   handlers. `AceStepRepaint.bootstrap` calls it with `("acestep-v15-turbo", False)`; the new class uses
   `("acestep-v15-base", True)`.
2. Change the method signature to
   `def repaint(self, audio_bytes: bytes, filename: str, request: dict, reference_bytes: bytes | None = None) -> dict`.
   When `reference_bytes` is present, write it to `Path(tmp)/"reference.flac"` and pass
   `reference_audio=str(reference_path)` to `GenerationParams`; otherwise pass `None`.
3. Add optional request reads (keep `REQUIRED_REQUEST_KEYS` unchanged):
   `inference_steps=int(request.get("inference_steps", 8))`,
   `guidance_scale=float(request.get("guidance_scale", 7.0))`,
   `use_adg=bool(request.get("use_adg", False))`,
   `repaint_wav_crossfade_sec=float(request.get("repaint_wav_crossfade_sec", 0.05))`,
   `repaint_latent_crossfade_frames=int(request.get("repaint_latent_crossfade_frames", 10))`,
   `shift=float(request.get("shift", 3.0))`.
   Forward all six to `GenerationParams`. Turbo keeps 3.0 (upstream recommendation); the client sends
   `shift=1.0` for the base model (upstream default; 3.0 is recommended only for turbo).
   *(As built: the two `repaint_*_crossfade*` fields are dead — the pinned handler overwrites them with
   `_resolve_repaint_config(mode, strength)`. See the crossfade departure under Status.)*
4. Add a second class `AceStepSeamBase` with the same `@app.cls(...)` decorator, `DIT_CONFIG =
   "acestep-v15-base"`, and a `@modal.enter` that calls `_bootstrap(self, DIT_CONFIG, True)`; its
   `repaint` method is identical to `AceStepRepaint.repaint` (share the body through a module-level
   `def _repaint(self, audio_bytes, filename, request, reference_bytes)` free function called by both).
5. Keep `chunk_mask_mode="explicit"` and the existing `audio_cover_strength` default; the crossfade
   parameters above are the only change to the mask-edge splice.

### Step 3 — POC script: DSP baselines, engines, assembly

Create `lab/poc-scripts/gen_seam_fill.py` (single file, `argparse`). Import and reuse from
`gen_audio_inpaint` (same directory, `sys.path[0]` when run as a script): `SR`, `decode_audio`,
`read_wav`, `write_flac`, `decode_flac_bytes`, `slice_window`, `rms_db`, `peak_level`, `fit_length`,
`gain_ramp`, `apply_join_fades`, `seconds_to_samples`, `fetch_songset`, `ensure_source_audio`,
`keyscale`, `SongsetItem`, `Recording`, `Component`. `fetch_songset`'s
`Recording.tempo_bpm`/`musical_key` are the authoritative seam values (never `Component.bpm`).

Constants: `SIDE_SECONDS = 8.0`, `HEAD_SECONDS = 7.0`, `GAP_BEATS = 2`, `GAP_MS = 1393`
(assert it equals `round(2 * 60000 / recordings[b_hash].tempo_bpm)`), `FILL_FADE_SECONDS = 0.01`,
`JOIN_FADE_SECONDS = 0.005`, `TAIL_FADE_SECONDS = 0.05`, `PEAK_CEILING = 0.98`.

Seam windows (assert `a_end == 71.85` and `b_start == 0.7` from the live DB; fail loudly if the songset
changed):

```
head        = A[56.85 : 63.85]
a_part      = A[63.85 : 71.85]
hole        = zeros(61431 samples)            # 1.393 s; gap_seconds = 1393/1000
extension   = A[71.85 : 73.243]
b_part      = B[0.7  : 8.7]
tail        = B[8.7  : 15.7]
montage     = a_part | hole | b_part          # 17.393 s
clip        = head   | montage | tail         # 31.393 s
```

**DSP baselines** (`engine = "dsp"`, no GPU, `fills/` = the fill audio):

|id|fill|
|---|---|
|`dsp__current-silence`|`zeros(61431)` (today's production behaviour); `fills/<id>.flac` is digital silence|
|`dsp__butt-splice`|none: body = `a_part \|b_part`; clip is 30.0 s, 5 ms equal-power fade at the join; `fill` is `null` and no fills file is written|
|`dsp__tail-extension`|`extension`; contiguous with `a_part` (no fade at 71.85), 50 ms equal-power fade-out at the end into B's lead-in|
|`dsp__b-pickup`|`B[112.387 : 113.78]` (B's own 2-beat pre-chorus pickup), 10 ms fades at both edges|

Baseline clips are written sample-identically: `clip = head + body + tail` with only
`apply_join_fades` at the head/tail joins; no gain staging, no peak scaling. If a baseline clip exceeds
the 0.98 ceiling, write it anyway and record `"peak_limited": false, "peak": …` (real audio is not
modified; run1's source peaked at 0.94–0.98). `montage` is `null` for every baseline.

**Generated fills.** Two prompts, drum-only:

```python
SA3_PROMPTS = {
    "drum-a": "TrackType: Music, VocalType: Instrumental, Genre: Worship, a short live acoustic drum kit fill with floor toms and snare rolling into a crash cymbal on the downbeat, no melodic instruments, no vocals, 86 BPM",
    "drum-b": "TrackType: Instrument, solo drum kit, a punchy two-beat fill with floor toms and snare, recorded live in a church room, no other instruments, no vocals, 86 BPM",
}
ACESTEP_CAPTIONS = {
    "drum-a": "solo live acoustic drum kit fill, floor toms and snare rolling into a crash cymbal, two beats, no melodic instruments, no piano, no guitar, no vocals, seamless lead into the next section",
    "drum-b": "acoustic drum kit, punchy two-beat tom and snare fill, live church room, no other instruments, no vocals",
}
```

Written to `<out>/prompts.json` (`{"version": 1, "sa3": {...}, "acestep": {...}}`) and reused verbatim
unless `--refresh-prompts`; this is the only prompt-tuning input.

Variant matrix (14 clips):

|engine|prompts × seeds|ids|
|---|---|---|
|`sa3-small-music`|2 × 3 (7001–7003)|`sa3__drum-a__s7001`, …|
|`acestep-v15-turbo`|2 × 1 (7101, 7102)|`acestep-turbo__drum-a__s7101`, …|
|`acestep-v15-base`|2 × 1 (7201, 7202)|`acestep-base__drum-a__s7201`, …|

Requests:

- SA3: `{"prompt": SA3_PROMPTS[style], "seed": seed, "steps": 8, "start_seconds": 8.0,
  "end_seconds": 9.393, "duration_seconds": montage.shape[0] / SR}`. Do not send `negative_prompt` or
  `cfg_scale` (post-trained ignores them).
- ACE-Step turbo: `inference_steps=8`, `guidance_scale=7.0` (auto-corrected to 1.0 by turbo),
  `use_adg=False`, `bpm=86`, `keyscale="C Major"`, `instrumental=True`, `lyrics="[Instrumental]"`,
  `duration=montage_seconds`, `repaint_start=8.0`, `repaint_end=9.393`, `seed`, caption from
  `ACESTEP_CAPTIONS`, `repaint_wav_crossfade_sec=0.05`, `repaint_latent_crossfade_frames=10`.
  *(As built: those two knobs never reach the sampler; the request sends `repaint_mode="balanced"` and
  `repaint_strength=0.5`, whose resolved effect is 0.5 injection / 12 latent frames / 0.025 s wav
  crossfade. See Status.)*
- ACE-Step base: same request with `inference_steps=32`, `guidance_scale=7.0`, `use_adg=True`.
- Both ACE-Step classes receive `reference_bytes` = FLAC of the reference montage
  `A[61.85:71.85] + B[0.7:10.7] + B[112.387:122.387]` (3 × 10 s), resampled to **48 kHz** and
  `fit_length`-ed to exactly **1,440,000 samples** (30.0 s) before writing; the app asserts the decoded
  length is exactly 1,440,000 and the sample rate is 48000, since the server's reference crop is only
  deterministic at that exact length/rate.

Client helpers: `modal_client_turbo()` = `modal.Cls.from_name("sow-acestep-poc",
"AceStepRepaint")()`; `modal_client_base()` = `...("AceStepSeamBase")()`; `modal_client_sa3()` =
`modal.Cls.from_name("sow-sa3-poc", "Sa3Inpaint")()` (class name `Sa3Inpaint` in Step 1). One retry per
call with 5 s backoff, then mark the variant failed.

**Fill assembly (generated variants).** Decode the returned FLAC to 44.1 kHz stereo, `fit_length` to
`montage.samples.shape[0]` (record `length_corrected`), take `fill = body[hole_start:hole_end]`. Gain:
`a_level = rms_db(a_part[-1 s:])`, `b_level = rms_db(b_part[:1 s])`, `target = (a_level + b_level)/2`,
`fill *= 10 ** ((target - rms_db(fill))/20) * gain_ramp(fill_samples, a_level - target, b_level - target)`.
If `peak_level(fill) > 0.98`, scale the fill to 0.98 and set `peak_limited=True`. Then
`clip = head + a_part + fill + b_part + tail`, applying 10 ms equal-power fades at the two fill edges
and 5 ms fades at the head/tail joins. Never scale `head`, `a_part`, `b_part`, or `tail`.

**Metrics** per variant (stored, not used for selection): `a_level_db`, `b_level_db`, `fill_rms_db`
(null when the fill is digital silence), `fill_peak`, `fill_spectral_centroid_hz` (librosa, mono,
`n_fft=1024`; null when silent), `fill_stereo_corr` (null when silent), `fill_onset_density_per_s`,
`fill_duration_seconds`, `clip_duration_seconds`, `peak`, `peak_limited`, `length_corrected`, plus
`seed`, `prompt`, `request`, `meta`, `elapsed_seconds`. Every non-finite metric (`-inf`, `nan`) is
stored as JSON `null`; `json.dumps` must never emit `-Infinity`. Do not report BPM (1.393 s is too
short).

**Outputs** under `<out>`: `cache/`, `prompts.json`, `montages/<id>.flac` (generated variants only),
`fills/<id>.flac` (every variant except `dsp__butt-splice`), `clips/<id>.flac`, `variants/<id>.json`
(idempotency sidecar), `index.json`, `report.html`.

`index.json`:

```json
{"version": 2, "songset_id": "hbWfCccGxN_JLQGBEqmdu", "created_at": "<iso8601>",
 "seam": {"a_hash": "6a410a5c222a", "b_hash": "20379ea26256", "a_end_seconds": 71.85,
          "b_start_seconds": 0.7, "gap_ms": 1393, "gap_seconds": 1.393,
          "a_tempo_bpm": 68.0, "b_tempo_bpm": 86.13, "a_key": "D Major", "b_key": "C Major"},
 "models": {"sa3": "small-music", "acestep_turbo": "acestep-v15-turbo", "acestep_base": "acestep-v15-base"},
 "variants": [{"id": "...", "engine": "sa3-small-music", "status": "ok", "prompt": "...",
               "seed": 7001, "clip": "clips/<id>.flac", "fill": "fills/<id>.flac",
               "montage": "montages/<id>.flac", "request": {...}, "meta": {...},
               "metrics": {...}, "length_corrected": false, "error": null}]}
```

`fill` and `montage` are `null` when they do not exist (`dsp__butt-splice` has no fill; DSP variants
have no montage); `request`, `meta`, `prompt`, `seed`, and `elapsed_seconds` are `null` for DSP
variants.

Idempotency: skip a variant when `clips/<id>.flac` exists and its sidecar has `status == "ok"`, unless
`--force`. Failure: catch per variant, record `status: "failed"` + error, keep going. A generated fill
that is silent (`rms_db` non-finite or `peak < 0.001`) is a failure, not an `ok` variant. Exit non-zero
only if every generated variant failed (DSP baselines cannot fail).

CLI (exact flags, no others):

```
--songset hbWfCccGxN_JLQGBEqmdu   --out lab/poc-scripts/output/audio-inpaint/run2
--seed 7000    --force    --refresh-prompts
--skip-dsp     --skip-sa3     --skip-acestep     --dry-run
```

`--dry-run` writes baselines + montages + prompts + index skeleton, and makes zero Modal calls.

### Step 4 — Listening page

`<out>/report.html`: one self-contained file, relative `src` paths, no JS framework, no CDN. Sections in
this order: `DSP baselines (real audio)`, `Stable Audio 3 Small-Music`, `ACE-Step 1.5 turbo`,
`ACE-Step 1.5 base`. Each variant row: `<audio controls preload="none" src="clips/<id>.flac">`, plus a
second `<audio controls preload="none" src="fills/<id>.flac">` when the fill exists (omitted for
`dsp__butt-splice`), the id, engine, prompt in `<details>`, seed, and the metrics on one line. Title states: Here I Bow (D major, 68 BPM) → Here for You (C major, 86.13 BPM), gap 2 beats =
1.393 s, A extended from 71.85 s. Group order is fixed; no randomization.

### Step 5 — Domain terms

Add to `CONTEXT.md` under `### Songset & Transitions`, immediately after **Seam Inpaint**:

```markdown
**Seam Fill**:
The audio that occupies the gap between two adjacent Songs in a rendered Songset. A Seam Fill is either
real material taken from one of the two recordings (a Tail Extension) or newly generated music (a Seam
Inpaint); it is not a fade between the Songs.
_Avoid_: bridge, transition bed, stitch

**Tail Extension**:
A Seam Fill made by continuing the outgoing Song's own recording past its arrangement cut, so the
listener hears that Song finish rather than silence or new music.
_Avoid_: ring-out, outro pad, post-roll
```

No ADR: throwaway POC, nothing in production consumes it.

### Step 6 — Session completion

Run `graphify update .`, then commit with the eval `commit` namespace (draft + commit), `git pull
--rebase`, `git push`; `git status` must end up to date. Generated media under
`lab/poc-scripts/output/` is gitignored; do not force-add it.

## Critical files & anchors

- `lab/poc-scripts/modal_apps/sa3_inpaint_app.py` — new; only SA3 GPU boundary; cache/download/config
  rewrite.
- `lab/poc-scripts/modal_apps/acestep_repaint_app.py` — `REQUIRED_REQUEST_KEYS`, `repaint()` signature,
  `bootstrap`; add reference audio, quality knobs, base class.
- `lab/poc-scripts/gen_seam_fill.py` — new; DB/R2 fetch, baselines, variant matrix, assembly, report.
- `lab/poc-scripts/gen_audio_inpaint.py` — source of reused helpers; do not edit.
- `CONTEXT.md:51` — after the existing **Seam Inpaint** entry.

## Verification

Prerequisites for every command: `cd /home/mhuang/Development/sow_feature_branch_2 && set -a && . /opt/sow/.env
&& set +a`.

1. **Apps deploy**: `uv run --no-project --with 'modal>=1.2.6,<2' modal deploy
   lab/poc-scripts/modal_apps/sa3_inpaint_app.py` and `… modal deploy
   lab/poc-scripts/modal_apps/acestep_repaint_app.py`; `modal app list` shows `sow-sa3-poc` and
   `sow-acestep-poc` deployed.
2. **Dry run**: `uv run --project lab/poc-scripts --extra audio_inpaint python
   lab/poc-scripts/gen_seam_fill.py --songset hbWfCccGxN_JLQGBEqmdu --out
   lab/poc-scripts/output/audio-inpaint/run2 --dry-run` → log line `gap_ms=1393 (2 beats @ 86.13)`;
   `clips/` contains exactly the four `dsp__*` clips; `fills/dsp__tail-extension.flac` equals
   `A[71.85:73.243]` sample-for-sample except the last 2205 samples (the 50 ms fade-out); that clip's
   first 8.0 s equal `A[56.85:64.85]` and its last 8.0 s equal `B[7.7:15.7]` sample-for-sample; and
   `montages/sa3__drum-a__s7001.flac` has 767,031 frames with 61,431 silent frames between frame
   352,800 and 414,231; `prompts.json` has 4 prompts; zero Modal calls.
3. **SA3 smoke**: same dry-run command with `--skip-dsp --skip-acestep` → `fills/sa3__drum-a__s7001.flac`
   exists at 1.393 s, its RMS is inside `[b_level_db, a_level_db]` ±3 dB, `peak <= 0.98`, and
   `index.json` records `engine: "sa3-small-music"`, `meta.model: "small-music"`, both seed and elapsed
   time.
4. **ACE-Step smoke**: `--skip-dsp --skip-sa3` → one turbo and one base fill exist; each fill is not
   silent and `meta.dit_model` is `acestep-v15-turbo` / `acestep-v15-base` respectively.
5. **Full run**: drop all skips (same `--out`) → 14 clips; every `index.json` variant `status == "ok"`;
   clips are 1,384,431 frames except `dsp__butt-splice` (1,323,000); every fill is 61,431 frames except
   `dsp__butt-splice` (none); `length_corrected` only set when a model returned a different length
   (recorded, never silently ignored).
6. **Listenable proof**: `python3 -m http.server 8765 --directory
   lab/poc-scripts/output/audio-inpaint/run2`, open `http://127.0.0.1:8765/report.html` in a browser
   tab and assert via `page.evaluate` that exactly 27 `audio` elements exist (14 clips + 13 fills),
   every `duration` is finite, and the four section headings render.
7. **Glossary**: `grep -n "Seam Fill\|Tail Extension" CONTEXT.md` returns both entries.

## Assumptions & contingencies

- **ModelScope download failure** (404/redirect change): the run cannot continue without weights. Stop
  and report the failing URL; do not fall back to HF (no token on this host).
- **`load_diffusion_cond` + local `model_path` mismatch** (e.g. T5Gemma expects a different config
  key): read the traceback, then set `model_name` explicitly to `"google/t5gemma-b-b-ul2"` only if
  `model_path` fails; record the working config in the app docstring. If neither works, fall back to
  the ungated `small-music-base` checkpoint (config `cfg_scale=7.0`, `steps=50`) from the same mirror
  (`https://modelscope.cn/models/stabilityai/stable-audio-3-small-music-base/resolve/master/`) and
  record `model: "small-music-base"` in the index; the listening page still ships.
- **SA3 rejects or degrades a 1.393 s mask** (output silent/tonal/too short): keep the run as-is and add
  a second SA3 pass with `start_seconds=8.0, end_seconds=10.786` (one bar at 86.13 BPM) labeled
  `sa3-long__*`; do not change the production gap, and do not rerun ACE-Step.
- **ACE-Step base init/download fails**: skip `acestep-base__*`, record the failure, ship the turbo
  comparison; do not retry more than once.
- **Reference audio changes output unpredictably**: it is sampled deterministically only because the
  file is exactly 30.0 s *at 48 kHz*; if the client's concatenation/resampling is not exactly
  1,440,000 samples, pad/trim it before upload and assert the length. If the app's `soundfile` read
  reports 44.1 kHz instead of 48 kHz, fail loudly (the server would resample and break the crop).
- **A's extension material differs on a future re-fetch** (re-scraped recording): the window is read by
  absolute time from the same hash; if `a_part + extension` is not contiguous in the decoded array
  (i.e. `extension` empty), fail the run instead of silently zero-filling.

## Status: delivered (2026-11-20)

Delivered on branch `audio_imprint_poc` (commits `69ff2abc`, `c2bbafe6`, `44ccd51c`, `3b14505a`):
`lab/poc-scripts/modal_apps/sa3_inpaint_app.py`, `lab/poc-scripts/modal_apps/acestep_repaint_app.py`,
`lab/poc-scripts/gen_seam_fill.py`, and the **Seam Fill** / **Tail Extension** glossary entries in
`CONTEXT.md`.

Departures from the plan as written:

- **A's arrangement now ends at 40.55 s, not 71.85 s.** The live `arrangement_steps` JSON ends at
  `verse 1 11.85–40.55`; the run re-anchors every A window to the live value and prints
  `warn: outgoing arrangement ends at 40.55s, not the 71.85s the plan recorded`. The Tail Extension is
  `A[40.55:41.943]` — still real chorus (−24.6 dB after gain), so the baseline premise holds.
- **SA3 needed a checkpoint-integrity fix before it could run.** A killed container left a truncated
  `model.safetensors` (2.27 GB) on `sow-sa3-models`; `load_diffusion_cond` died with
  `SafetensorError: incomplete metadata, file not fully covered` on every boot, which looked like GPU
  scheduling starvation (`Pending`, no container). `_file_is_intact()` now parses the safetensors header
  and the JSON files at boot, downloads go to `<file>.part` and are renamed only on success, and the
  volume was repaired with `modal volume put` (8.5 MB/s local vs 1.7 MB/s inside Modal). Boot log:
  `verified 685/685 model tensors from the checkpoint`, `ready in 134.9s (device=cuda, half=True)`.
- **CPU twin class + selector.** `Sa3InpaintCpu` (`gpu=None`, `model_half=False`) exists for capacity
  outages and is selected with `SOW_SA3_CLASS`; the smoke and the full run used the GPU class
  `Sa3Inpaint` (A10G), and the index records `device: "cuda"`.
- **ACE-Step crossfade recording was wrong and is fixed.** The pinned handler ignores
  `repaint_wav_crossfade_sec` / `repaint_latent_crossfade_frames` and derives both from
  `_resolve_repaint_config(repaint_mode, repaint_strength)` (`inference.py:872-875` → `generate_music.py:420,449,493`).
  The request now sends `repaint_mode="balanced"` / `repaint_strength=0.5` through `GenerationParams`
  and the meta records the resolved `repaint_effective` = 0.5 injection, 12 latent frames, 0.025 s wav
  crossfade — the plan's "50 ms + 10 frames" never ran. All 14 variants were regenerated with the final
  code.
- **Butt-splice gets three join fades** (head→a_part, the A|B splice itself, b_part→tail), not two.
- **Report clip players are gated on `status == "ok"`** so a failed or dry-run record cannot advertise
  a clip that does not exist.

Verified run (`lab/poc-scripts/output/audio-inpaint/run2`): 14/14 `status == "ok"`, wall 121.5 s
(Modal 112.8 s); clips 1,384,431 frames except `dsp__butt-splice` 1,323,000; fills 61,431 frames
(`dsp__butt-splice` has none); `length_corrected` true only for ACE-Step (`returned_samples=765,576`,
33 ms trimmed off a montage tail outside the masked hole); SA3 returned exactly 767,031 samples for
every seed. Page: 27 `<audio>` elements (14 `clips/` + 13 `fills/`, butt-splice contributes one clip and
no fill), every metadata duration finite, four sections in fixed order.
