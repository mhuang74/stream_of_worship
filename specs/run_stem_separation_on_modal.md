# Move stem-separation failover from the container to Modal

> Status: implemented. Supersedes the in-container `AudioSeparatorWrapper` path
> (`workers/separator_wrapper.py`, deleted), the model-download half of
> `specs/melband_roformer_local_fallback.md`, and `SOW_FREE_ONLY_MODE` from
> `specs/free-only-patient-mode-v1.md` (quota waiting is now unconditional).
> Companion to `specs/run_whisper_on_modal_v2.md` — same client/retry/observability
> shape.

## Problem

The analysis container is CPU-only, but the stem-separation failover ran
`audio-separator` in-process: MelBand Roformer + UVR-De-Echo, plus the whole ONNX
runtime, resampy, diffq, and ~3-5 GB of model files bind-mounted from the host. Every
fallback burned CPU for minutes, and the container carried a dependency stack the
service does not otherwise need.

MVSEP stays the **primary** backend (it is a cloud API and already returns
vocals/instrumental/de-reverb). Only the failover moves.

## Decision

- New Modal app `sow-stems` (`modal_apps/stem_separator_app.py`), class
  `StemSeparator`, GPU `L4`, `max_containers=2`, `timeout=1800`, no `retries=`.
- New client `services/modal_stem_separator_client.py` (`ModalStemSeparatorClient`,
  singleton getter) owns retries, size cap, error typing, and `request_id` log
  correlation — mirroring `modal_whisper_client.py` / `modal_forced_aligner_client.py`
  and sharing `services/modal_retry.py`.
- `workers/separator_wrapper.py` deleted; `stem_separation.py` takes
  `stem_separator: ModalStemSeparatorClient` and its stage names became
  `fallback_modal` / `fallback_modal_stage2`.
- `audio-separator` + `onnxruntime` dropped from the `service` extra; model dir
  settings, docker-compose bind mount, `/models/audio-separator` dir, logging
  suppressors, and the start-dev/deploy model-download steps deleted.
- `SOW_FREE_ONLY_MODE` removed. Waiting for free-tier quota (Qwen3 ASR, MVSEP) is now
  unconditional; the flag only ever chose between "wait" and "fall back to local
  models", and there is no local model to fall back to any more. The five
  `SOW_YOUTUBE_TRANSCRIPT_*_FREE` overrides and
  `SOW_YOUTUBE_TRANSCRIPT_FREE_MODE_MAX_BREAKER_CYCLES` went with it — a rate-limited
  YouTube fetch now falls through to ASR instead of burning cooldown cycles.

## Facts verified in `audio-separator` 0.44.1 source

Findings that shaped the app; each was checked against the installed wheel, not
inferred:

| # | Fact | Consequence |
|---|------|-------------|
| 1 | `Separator.load_model()` snapshots `self.output_dir` into the per-model instance's common params (`separator.py:879`) | Setting `Separator.output_dir` after load does **not** redirect output |
| 2 | Write paths join `model_instance.output_dir` (`common_separator.py:317`, `:405`) | `_redirect_output()` sets both `separator.output_dir` and `separator.model_instance.output_dir` before every `separate()`; otherwise stems land in the constructor's temp dir and `_classify_*` resolves nothing |
| 3 | `separate()` returns bare filenames, not paths (`common_separator.py:490-507`) | Classification resolves relative names against the stage dir |
| 4 | `clear_file_specific_paths()` does not reset `output_dir` (`common_separator.py:466-479`) | Reusing warm model instances across calls is safe |
| 5 | PyPI package is `audio-separator` (hyphen), latest 0.47.1; 0.44.1 ships a `py3-none-any` wheel and a `cpu` extra that only adds `onnxruntime` | Modal image installs `audio-separator[cpu]==0.44.1`; the `[cpu]` extra exists for the module-scope `onnxruntime` import |
| 6 | Both models run on torch/CUDA (MelBand Roformer = MDXC, UVR-De-Echo = VR) | `gpu="L4"`; `onnxruntime` is never used for these models |
| 7 | Downloads land in `model_file_dir` (`/models` volume), not `output_dir` | Volume mount at `/models`, markers `.downloaded-<model>` committed after both loads succeed |

## Failover semantics

- Stage 1 exhausts its retries → whole pipeline reruns on Modal (`fallback_modal`),
  returning `(vocals_dry, vocals, instrumental)`.
- Stage 1 succeeded, Stage 2 exhausts its retries → MVSEP's vocals are de-reverbed on
  Modal (`fallback_modal_stage2`); the instrumental/timing come from MVSEP.
- `MvsepParsingError` (files returned but none classified as vocals) is **not** an
  outage: it propagates and fails the job, with no Modal call.
- Quota exhaustion waits for the UTC reset, then retries MVSEP; Modal is reached only
  when retries/interruptions end.

## Client contract

- `separate_stems(input_path, output_dir) -> (vocals_dry, vocals, instrumental)`,
  `remove_reverb(vocals_path, output_dir) -> (dry, None)`; any element may be `None`.
- Stems are written atomically (`*.part` then replace), so a failed call cannot leave a
  truncated FLAC that a later cache check would accept.
- Retryable: `ConnectionError`/`TimeoutError`/`InternalFailure`/`RemoteError` and
  `grpclib.GRPCError` with `DEADLINE_EXCEEDED|UNAVAILABLE|CANCELLED|INTERNAL|UNKNOWN`.
  Non-retryable: `RequestSizeError`, oversize blob (413/"too large"), `NotFoundError`
  → config error naming the deploy command, `AuthError` → token error,
  `ExecutionError`, and any other container exception (logged with its class name).
- `SOW_MODAL_STEM_MAX_AUDIO_BYTES` (default 100 MB) caps the upload; Stage 2 uploads the
  lossless vocals FLAC, so the cap is above Whisper's/aligner's.

## Knobs

`SOW_MODAL_STEM_APP_NAME`, `SOW_MODAL_STEM_CLASS_NAME`, `SOW_MODAL_STEM_MAX_ATTEMPTS`,
`SOW_MODAL_STEM_RETRY_DELAY_SECONDS`, `SOW_MODAL_STEM_MAX_AUDIO_BYTES`, plus the two
model filenames `SOW_VOCAL_SEPARATION_MODEL` / `SOW_DEREVERB_MODEL` (Modal class
parameters, no redeploy needed to switch models).

## Removed

| Removed | Why |
|---|---|
| `workers/separator_wrapper.py` | Replaced by the Modal client |
| `audio-separator`, `onnxruntime` in the `service` extra | Only the Modal image needs them |
| `SOW_AUDIO_SEPARATOR_MODEL_DIR`, `SOW_AUDIO_SEPARATOR_MODEL_ROOT` | No host model dir |
| `SOW_FREE_ONLY_MODE` | Quota waiting is unconditional |
| `SOW_YOUTUBE_TRANSCRIPT_*_FREE`, `..._FREE_MODE_MAX_BREAKER_CYCLES` | Died with the flag |
| `/models/audio-separator` mount + download scripts (`start-dev.sh`, `scripts/deploy.sh`, `DEPLOYMENT.md`) | Models live in the `sow-stems-models` volume |
| `audio_separator` log suppressors in `logging_config.py` | Package no longer imported in-container |

Leftover `SOW_FREE_ONLY_MODE`/`*_FREE` keys in a deployment `.env` are ignored
(pydantic-settings drops unknown keys).

## API surface

The stem-separation job's stage names are part of the polling contract:
`mvsep_stage1_*`, `mvsep_stage2_*`, `fallback_modal`, `fallback_modal_stage2`.
Android/webapp clients treat any non-`complete`/`failed` stage as "in progress", so
the two renames need no client change.

## Verification

- `tests/test_mvsep_fallback.py` — 29 cases: per-stage failover, handoff with MVSEP
  vocals, parsing-error propagation, quota wait, backoff timing.
- `tests/test_modal_stem_separator_client.py` — 12 cases: stem order/content,
  `None` payloads, atomic writes, config/size errors before any RPC, retryable vs
  non-retryable gRPC status, exhausted-retry message, deploy hint, container exception
  class, per-model-pair class instances.
- `uv run --extra dev pytest tests/ --ignore=tests/integration` — full suite.
- Live check (needs Modal credentials): deploy `sow-stems`, submit a
  `stem_separation` job with MVSEP disabled, confirm `vocals_dry/vocals/instrumental`
  land in R2 and `modal app logs sow-stems` shows the matching `request_id`.
