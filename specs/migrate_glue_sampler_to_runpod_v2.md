# Migrate the Glue sampler to Runpod

Implementation specification, v2. Baseline is `main` at `227ad00f`.
This specification supersedes the draft `migrate_glue_sampler_to_runpod.md` for implementation.

## 1. Scope and product behavior

- Keep SQS, the standard Glue Lambda, the catalog and arrangement inputs, and the existing take
  staging and selection pipeline. Run SimDPS-Multi inference on a queue-based Runpod L4 endpoint.
- Default to ElevenLabs. With `SOW_GLUE_PROVIDER=simdps`, prefer Runpod for eligible junctions.
  Automatically route deterministic SimDPS geometry and search-source incompatibilities to
  ElevenLabs before provider contact. The selected provider owns the whole attempt.
- A confirmation authorizes one new attempt from the existing rolling quota for either provider.
  Remove provider credit estimates from every user-facing Glue quote and confirmation. Keep the
  remaining slot count, reset time, and distinction between retrying processing for free and
  generating another take with a new slot. Report the fallback provider in operator telemetry.
- Keep the canonical descriptor, its `music_v2_5` identity token, and take selection rules. Treat the
  token as a legacy descriptor constant. Reuse and selection of compatible ready takes remain
  free, including takes created before rollout. Record actual provider and model on every new take.
- Use the ACE-Step 1.5 base prior, 100 steps, and the job's stored seed for v1 of the Runpod handler
  contract. Turbo, step tuning, multi-window inference, and changes to staged audio processing
  are outside this migration.

## 2. Deployment baseline and time budgets

The deployed `sow-glue-worker-standard` in `us-west-2` was verified Active with a configured
180-second timeout. Standard Lambda supports up to 1,800 seconds. Managed Instances were retired in
ADR 0015. Increase the standard function timeout to **600 seconds** and set the queue visibility to
**at least 3,600 seconds** before increasing the function timeout. Keep batch size 1, event-source
maximum concurrency 2, and partial batch failure reporting. Set and verify these values in the
bootstrap script, CI deployment guard, and operator rollout. Retain the existing 3,008 MB memory
and 2,048 MB temporary storage unless a measured resource test requires an adjustment.
[ADR 0015](../docs/adr/0015-standard-lambda-for-glue.md) ·
[Teardown report](../reports/glue_managed_instances_teardown_impl_summary.md) ·
[AWS Lambda quotas](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html) ·
[AWS SQS visibility guidance](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-configure.html)

| Limit | Value |
| --- | --- |
| Runpod job policy execution timeout | `executionTimeout: 180000` ms of execution |
| Runpod job policy lifetime | `ttl: 330000` ms from submission, including queue time |
| Lambda Runpod API budget | 360 s from start of the submit sequence, including POST, backoff, polling, and a final R2 lookup |
| Lambda reserves | Require at least 420 s at the contact CAS. Reserve 60 s after the Runpod API budget for verifying and persisting raw bytes, staging or handing it off for free recovery, and recording an outcome. |
| R2 signed GET/PUT | 600 s from signing, issued immediately before contact |

Use the Lambda context and an absolute monotonic deadline to cap each socket, sleep, and retry.
Use at most 3 s to connect and 10 s to read for each Runpod API call, capped further by the
remaining deadline. Bound status JSON to 64 KiB.
The polling loop should also stop early enough to use the outcome reserve if time remaining is
lower than expected. Keep the current ElevenLabs upload, compose, and persistence limits, including
its existing deadline checks. A longer Lambda timeout does not increase the ElevenLabs call budget.
Use capped timeouts for preparation I/O and ffmpeg and check remaining time again at the CAS.
[Runpod job policy](https://docs.runpod.io/serverless/endpoints/send-requests#execution-policies)

## 3. Prepare audio and select a provider

1. Claim the owned job and rederive the saved junction and canonical descriptor as today. Check
   the configured mode and credentials before asset work. A previously contacted job follows
   section 5 using its stored state.
2. Compute fill frames from the existing `generation_duration_ms(beats, toTempoBpm)` function,
   including its 3-second minimum and half-up millisecond rounding. Extract staging's current
   milliseconds-to-frames calculation into a shared render-worker helper and use it on both paths.
   Preserve its existing frame rounding. For example, the 4-beat POC fixture at 86.13 BPM has a
   122,880-frame test gap, while the same beat and tempo request in production is 132,300 frames.
3. In SimDPS mode materialize the canonical 44.1 kHz stereo PCM16 A tail and B head (up to 12 s
   each) using `glue_excerpts` and the saved Arrangement Steps. Expose a local-only construction
   path that leaves an ElevenLabs upload ID intact. Retain the existing handling of a known cached
   copyright rejection, including the check using the actual logical excerpt key if decode changes
   its length. Do not treat a local file cache hit as an ElevenLabs upload cache hit.
4. Form the montage from the exact integer A samples, zeros for the fill, and exact integer B
   samples. Use FLAC PCM16 for lossless transport. Record the actual A, fill and B frame counts.
   Derive the gap using the A frame count and fill frame count. Test frame-accurate extraction
   across Arrangement Step joins, including repeats, skips and short or truncated recordings.
5. Evaluate the window against the pinned upstream geometry before contact. From actual montage
   frames, compute the ceiling of the resampled length at 48 kHz. Map both gap endpoints with
   upstream's rounding. With hop `H=1920`, window `W=375*H`, and rounded endpoints `a,b`:

   ```text
   padded_length = max(resampled_length, W)
   ws = floor(min(max(floor((a+b)/2) - W/2, 0), padded_length-W) / H) * H
   fs = floor((a-ws)/H) - 20
   fe = ceil((b-ws)/H) + 20
   eligible_window = 0 < fs < fe < 375
   ```

   Use integer floor/ceiling where possible and assert parity with upstream on endpoint rounding,
   window placement and edge cases. The worker independently checks this geometry. A duration
   approximation alone misses failures close to a window edge.
6. Build the first **up to 18 arranged seconds** of the incoming instrumental stem from
   `{recording_hash_prefix}/stems/instrumental.flac`, using the same saved B steps and the existing
   audio decode and window planning. Use stereo PCM24 FLAC at 44.1 kHz. If the stem is missing or
   definitively too short, use the first up to 18 arranged seconds of the incoming mix, rebuilt
   from the same steps. A real storage, FFmpeg or configuration failure stays a free preparation
   failure. Record `instrumental_stem` or `incoming_mix` in the provider context.

   Check source length separately from the window. Pinned `latent_search` compares 5 frames of
   context and 5 frames of crossfade on either side, clipped to the available window. If
   `left=min(5,fs)` and `right=min(5,375-fe)`, it needs at least
   `(fe-fs) + left + right` source latents for one candidate. Use a conservative lower bound
   `floor(resampled_guide_frames / H) - 1` and confirm it against the pinned VAE on the boundary
   fixtures. Fewer than 15 candidates are supported by the upstream search but must be covered by
   the listening gate. An 18-second cap leaves room for the maximum feasible hole and its search
   context. A shorter arrangement can still require ElevenLabs.
7. Route a deterministic window or source-length failure to the existing ElevenLabs preparation
   and provider stage. Log the reason and recheck its deadline and cache state before its CAS.
   An unavailable required mix still fails with the existing audio error. Runpod outages, bad
   responses and transient R2 failures do not trigger fallback or a second provider request.
   Keep ElevenLabs credentials available for fallback and rollback in both deployment modes.
8. For Runpod, write both inputs to private R2 using a fresh submission UUID in the object keys,
   and sign the two GETs and one result PUT. Prepare the complete request and verify the time
   reserve before using the contact CAS. Cancellation or input changes before contact retain the
   existing free behavior. Preparation has no authority to send an inference request.

[Pinned window code](https://github.com/s-turland/SimDPSInpainting/blob/f5175f14b3be67e28ffc7c95874265a0390a9ada/simdps/inpainter.py) ·
[Pinned search](https://github.com/s-turland/SimDPSInpainting/blob/f5175f14b3be67e28ffc7c95874265a0390a9ada/simdps/search.py) ·
[POC measurement](../reports/simdps_glue_poc_impl_summary.md)

## 4. Runpod contract and R2 durability

Send one `POST https://api.runpod.ai/v2/{endpoint_id}/run` with the configured server-side API
key and `Content-Type: application/json`. Poll `GET /status/{id}` using the same credentials. Use
Runpod's queue API, with the JSON envelope below. Replace angle-bracket values at runtime.

```json
{
  "input": {
    "version": 1,
    "submission_id": "<uuid>",
    "montage_url": "<R2 signed GET>",
    "montage_sha256": "<sha256 of encoded montage>",
    "guide_url": "<R2 signed GET>",
    "guide_sha256": "<sha256 of encoded guide>",
    "result_url": "<R2 signed PUT>",
    "result_headers": {
      "Content-Type": "application/octet-stream",
      "Content-Length": "2644320",
      "If-None-Match": "*",
      "x-amz-meta-submission-id": "<uuid>",
      "x-amz-meta-model": "acestep-v15-base",
      "x-amz-meta-upstream-revision": "f5175f14b3be67e28ffc7c95874265a0390a9ada",
      "x-amz-meta-checkpoint-revision": "e432212fec32b8965a14ffa57ae653438d6abd14"
    },
    "a_frames": 264600,
    "fill_frames": 132300,
    "b_frames": 264180,
    "guide_frames": 264180,
    "seed": 0
  },
  "policy": { "executionTimeout": 180000, "ttl": 330000 }
}
```

- Use `glue-simdps-tmp/{user_id}/{job_id}/{submission_id}/` for `montage.flac`, `guide.flac`, and
  `seam.pcm`. All are private. Sign exact R2 object methods, sizes for PUT, expected content type,
  create-only condition, and submission metadata. Include model/revision metadata in the signed
  result headers and validate those values against the worker's loaded model before accepting a
  request. The handler only accepts HTTPS URLs for the configured R2 S3 host and temporary
  object paths for the same submission, forbids redirects, uses bounded streaming downloads,
  checks both input hashes, and validates FLAC format, rate, stereo channel count, subtype and
  expected frames. Limit each input to 10 MB compressed and enforce the decoded frame caps
  before allocation. Validate numeric fields, dimensions and model compatibility independently
  of the Lambda.
- Use a single synchronous handler per GPU. Call the pinned POC's `inpaint(..., source=guide,
  method="multi", raw=True, seed=job_seed, steps=100)`. Reuse its `extract_fill` implementation
  for the model-window crop, 48 kHz to 44.1 kHz resample and exact length. Validate finite samples
  after resampling. Quantize only the fill to PCM16 LE with explicit clipping so positive full
  scale cannot wrap. Copy A and B integer PCM frames directly from the montage.
- PUT the complete headerless stereo PCM16 seam before returning success. It contains
  `4 * (a_frames + fill_frames + b_frames)` bytes, at most about 6.6 MB. The conditional PUT
  makes a stored result immutable across retries. On `412`, Lambda verifies the first stored result.
  Return small JSON under Runpod's `output` envelope with `version`, `submission_id`,
  `model_id`, `upstream_revision`, `checkpoint_revision`, `seed`, `steps`, `gpu`,
  `model_load_seconds`, `sampling_seconds`, `processing_seconds`, and the three seam frame
  counts. Use the Runpod result for timing and diagnostics. The R2 object authorizes recovery
  even when status JSON is unavailable or has expired. Count any Runpod-managed execution
  retries in operating cost even when Lambda submitted only once.
- The Lambda verifies size within the existing raw-response cap, metadata, actual SHA-256, and
  byte-for-byte A/B samples against its verified montage (using persisted input and its checksum
  on recovery). Apply the existing generated audio level validation before accepting a corrupt
  or silent fill as a recoverable result. Publish the checked bytes using the existing immutable
  raw store at `glues/{user_id}/jobs/{job_id}/response.pcm`. Record raw key, size and hash, and
  enter `storing-response` only after that succeeds. Verify a pre-existing raw object on retry.
- R2 temp objects expire after one day under a lifecycle rule scoped only to `glue-simdps-tmp/`.
  This covers expected SQS redelivery before the DLQ. Preserve the existing retention and access
  rules for the final raw response and take. Never put a signed URL, authorization value, provider
  body or raw audio in logs, public job data, error details, or persistent provider context.
  Verify that catalog permissions cover sending the selected clips to Runpod before enabling.

[Runpod async API and result retention](https://docs.runpod.io/serverless/endpoints/operation-reference) ·
[R2 presigning](https://developers.cloudflare.com/r2/api/s3/presigned-urls/) ·
[R2 conditional PUT](https://developers.cloudflare.com/r2/api/s3/conditional-operations/)

## 5. Contact, retries and staging

Add nullable `provider_context_json` to `glue_jobs` through Drizzle with corresponding Python
read/write support. Store a versioned context with actual route, model/revision, seed, step
count, fallback or search-source reason, immutable input keys and checksums, submission UUID,
result key, exact frame counts, endpoint ID and submission deadline. Store the Runpod job ID in
that context with a guarded update after POST returns. Record the context in the same transaction
as `record_provider_contact` changes the reservation and phase. Use `compose` for Runpod's initial
phase and retain `upload-a` for ElevenLabs. Legacy jobs with no context read as legacy ElevenLabs
for resume and staging. The context is internal and carries no credentials or audio URLs.

The finalized take gets `metrics_json.provider` and `metrics_json.providerModel` from the job's
stored route; include the SimDPS upstream and checkpoint revisions and search source. Use
`elevenlabs` and `music_v2_5` for newly staged ElevenLabs jobs, including fallback jobs.
Preserve old take rows as written. Staging stays free and keeps original gain, ramps, channel
handling and selection behavior. A/B equality is an assertion about the raw seam; the
production render still uses the existing staged fill and needs an audible seam check.

| Event | Required outcome |
| --- | --- |
| Missing credentials or configuration, input preparation failure, or deadline gate before CAS | Stable existing error and appropriate free retry. Release the reservation and finish the job. In particular, map missing Runpod config to a durable pre-contact `GLUE_NOT_CONFIGURED` failure; simply throwing `GlueConfigurationError` can leave the job preparing. |
| CAS loses to cancellation, a different submitter, or stale input | Re-read the row. Follow its cancellation/in-progress state; never send POST or overwrite the winning worker's outcome. |
| CAS succeeds | Issue one `/run` with client retries and redirects disabled. Heartbeat during polls at least every 30 seconds. Poll `IN_QUEUE` and `IN_PROGRESS` with jittered 1–5 s delays using only `/status`. Retry transient GETs (including `429`) inside the budget. Preserve existing ElevenLabs retry semantics independently. |
| POST returns a clear authentication, configuration, or request rejection | Record a sanitized stable error (`GLUE_NOT_CONFIGURED` for credentials/config, or `GLUE_WORKER_FAILED` for invalid request), retry kind `none`, and an operator alert. The CAS is the consumption point. Pre-contact failures release the slot. |
| POST times out or returns `5xx`, unexpected `429`, or an unreadable response or ID | Assume acceptance is possible. Check the immutable result through the same bounded deadline using R2. If absent at the cutoff, finish `GLUE_PROVIDER_UNKNOWN`, retry kind `paid`. Never POST twice for this job. |
| Runpod reports `COMPLETED`, `FAILED`, `CANCELLED` or `TIMED_OUT` | Check R2 before classifying the outcome. Recover valid bytes. A final failure or invalid output is `GLUE_WORKER_FAILED`, retry kind `paid`. A transient R2 error leaves the record eligible for recovery. |
| Unexplained status `404`, unreadable polling responses, or client deadline | Keep checking for R2 output within the budget. At the cutoff, make a bounded best-effort cancel when an ID is known and a job may still be running. Check R2 again before finishing an unverified outcome as `GLUE_PROVIDER_UNKNOWN`, retry kind `paid`. A cancel response alone does not authorize another submit. |
| Result is stored but raw promotion or DB recording fails transiently | Let the SQS record fail if the in-invocation storage/DB retry is exhausted. On redelivery, inspect the result and immutable raw key, verify and repair metadata, then run staging. Never mark it as requiring a new paid attempt solely because promotion or metadata failed. |
| Stale Runpod invocation or duplicate message | A live claim is left alone. A new valid claim of a previously contacted Runpod job first checks the stored raw metadata and result object independently of the currently configured provider. If still inside the stored budget with a Runpod ID, it can poll the existing job; if no ID, look for the result. After the deadline with neither verified output nor a continuing remote job, finish with the unknown outcome. A recovered take still goes through the current-junction selection guard. |
| Raw key/hash/size are durable, including after rollback | Resume staging based on the stored job record and provider context. All retry-processing work is free and issues no inference call. |

Verify active phase and contact ownership when persisting and advancing; keep state changes and
reservation consumption transactional. A job that dies between the CAS and the POST can have an
unknown outcome with no accepted Runpod job. That gap is deliberate to enforce at-most-one submit.
The worker can recover audio after the Runpod status retention window because the GPU writes R2
before completion. The DLQ runbook should explain how to recover a verified result without a new
submit and how to inspect unresolved exhausted retries. Any late result found after a job was
marked terminal is operator-recoverable evidence and cannot silently replace a later selected take.
[Durable Glue jobs](../docs/adr/0014-durable-glue-jobs.md) ·
[Runpod submit retry semantics](https://docs.runpod.io/serverless/endpoints/send-requests#handle-rate-limits-in-your-client)

## 6. Build and change list

| Area | Deliverable |
| --- | --- |
| `delivery/simdps-worker/` | Add a Linux amd64 CUDA 12.8 image, Python 3.11, PyTorch 2.8.0, git, libsndfile, and locked Runpod SDK, SimDPS and audio/model dependencies. Use the POC's verified direct CUDA initialization first. Start the SDK with a single GPU handler after model initialization. Source code and weights originate from pinned revisions. |
| Weight build | Bake the base weights and VAE from `ACE-Step/acestep-v15-base@e432212fec32b8965a14ffa57ae653438d6abd14` and `ACE-Step/Ace-Step1.5@19671f406d603126926c1b7e2adc169acbcade22`; install SimDPS at `f5175f14b3be67e28ffc7c95874265a0390a9ada`. Preserve the files and snapshot layout used by the pinned loader. Start with complete required files. Validate cold offline construction and an actual job with `HF_HUB_OFFLINE=1` before accepting a build. Treat pruning, CPU-first model initialization and model warmup as measured optimizations after baseline verification. |
| POC and shared audio | Share `extract_fill` between the GPU image and `lab/poc-scripts/modal_apps/simdps_inpaint_app.py`. Explicitly include it in both build contexts and test Modal import/packaging. Add a Runpod option to `lab/poc-scripts/gen_glue_simdps.py` using the same signed R2 transport contract and isolated test prefix/lifecycle. The listening harness should exercise both original fixtures and the production staging path. |
| `delivery/render-worker/` | Implement provider selection, local excerpt/guide materialization, geometry, pre-contact gates, the Runpod provider and R2 verification/recovery. Reuse the existing immutable raw store, durable job transaction and finalizer. Expose new settings only on the Glue Lambda; keep `RUNPOD_API_KEY` and `RUNPOD_ENDPOINT_ID` off the general render function and webapp. An invalid selected mode fails closed. |
| `delivery/webapp/` | Add the nullable DB migration. Update `lib/glue/quote.ts`, `resolve.ts`, shared API types, the arrangement editor and render preflight wherever estimates appear, plus both locales and relevant tests. Remove credit copy from initial-confirm and try-another flows. Retain authoritative quota and duration data, existing paid/fallback behavior on old rows, and cache resolution before configuration checks. Ship UI consumers that ignore credit fields before removing those fields from responses; verify any supported clients or stale bundles that read the response and use a deprecation interval if needed. |
| Deploy | Publish immutable GPU image tags with their digest from a worker-specific CI job. Include shared audio and lockfile changes in build triggers, and include upstream notices if publishing the image. Configure a dedicated private queue-based endpoint using that image, constrained to L4 where available, one GPU/worker, min 0, max 2, idle 10 s, queue-delay scaling, and sufficient disk to unpack the image. Apply the request policy in section 2. Verify actual device and signed conditional PUT against real services. |
| Operations | Update `delivery/render-worker/deploy/aws-up-glue.sh`, `.github/workflows/deploy.yml`, the operator runbook and worker README to use the standard function and documented secrets workflow. Use a cheap endpoint health check or alert with an operator response for long inactivity. Runpod documents automatic max-worker reduction to 0 after seven idle days, so scale-to-zero and disabled capacity must be distinguishable before production jobs are accepted. Keep AWS and Runpod limits/alerts in the rollout checklist. |

[Pinned loader](https://github.com/s-turland/SimDPSInpainting/blob/f5175f14b3be67e28ffc7c95874265a0390a9ada/simdps/priors/acestep.py) ·
[Runpod endpoint settings](https://docs.runpod.io/serverless/endpoints/endpoint-configurations)

## 7. Verification and rollout

1. **Offline and CI.** Cover both provider selections; unsupported window with valid context,
   window with insufficient context, guide too short despite valid window, stem missing, incoming
   mix fallback, and a junction unsuitable for both. Include the 3-second floor, edge rounding,
   last supported latent, mono-to-stereo source normalization, reordered steps, and cached
   ElevenLabs upload/rejection behavior. Test provider config, quote and localization, free cached
   take reuse, action confirmations, actual provider/model persistence and migration from old rows.
   At the transport seam fake all status types, transient polls, malformed IDs, invalid audio,
   time budgets, cancellation, and known pre-contact failures. Inject failures after the CAS,
   after submission, after the R2 write, after raw promotion, and after its DB write. Assert
   that duplicate delivery and recovery during rollback never send another `/run` or consume
   another slot. Test that stale successful jobs keep their take without changing a newer selection.
2. **Staging integration.** Deploy an isolated queue, worker config, endpoint and scratch songset.
   Exercise the saved POC fixture (122,880 frames) and a normal production request (including the
   3-second floor). Verify the selected take reaches ready and renders in a representative
   songset. Compare integer A and B outside the raw gap. Prove ElevenLabs fallback on an actual
   unsupported junction and recover a saved Runpod seam after the polling result is unavailable,
   including real signed PUT enforcement and expired credentials.
3. **Listening.** A designated reviewer compares blinded ElevenLabs and production-staged
   Runpod renders across representative reference junctions. Include short and long eligible
   gaps, reordered or repeated steps, instrumental and mix guides, and a short guide with few
   candidates. Review vocal artifacts, musical continuity, audible seam levels, and render
   playback. Record fixture IDs, settings and a human pass/fail; automated audio scores
   supplement the listening result.
4. **Runpod launch measurements.** Run at least 100 representative eligible attempts, including at
   least 20 confirmed cold starts and ordinary two-job bursts. Record all failures, submit
   duplicates, retries, staging recoveries and verified ready takes. Measure from job acceptance
   to ready so SQS delay, Runpod cold start, R2 and staging count. Initial acceptance targets
   are at least 99% ready, zero duplicate paid submissions or lost durable results, and p95
   at most 60 s. Report warm and cold distributions separately and include timeouts in the
   sample. No production enablement until the measured results meet the targets or the owner
   explicitly revises them based on the results.
5. **Cost.** Use Runpod account billing and measured Lambda duration, R2 operations and queue
   usage to compute cost per ready Runpod take, including failed attempts, startup, and idle.
   Compare projected mixed-route monthly cost at 500 attempts with current ElevenLabs-only
   billing and the ADR 0015 AWS budget. Record the ElevenLabs fallback rate and costs separately.
   Obtain operator approval for the measured GPU spend and monthly projection before enabling.
   The Modal POC measured 29.655 s sampling and 130.441 s cold elapsed for one fixture. Those
   are reference points. Use the actual Runpod account SKU and billing for the launch decision.
   CPU and Cloud Run comparisons are optional and use the same model, schedule and input if run.
6. **Cutover and rollback.** Deploy the additive schema, quote experience and compatible worker
   with ElevenLabs selected. Verify queue settings before raising the Lambda timeout. Run the
   staging gates and operator pilot on the isolated setup, record the exact image digest and
   active endpoint configuration, then enable SimDPS on the production worker. Roll
   back new submissions by selecting ElevenLabs in the standard Lambda config. Keep the
   recovery-capable worker, Runpod endpoint, and temporary R2 prefix available while contacted
   Runpod jobs drain. Verify there are no pending recoveries before endpoint retirement or
   reducing the Lambda timeout. The selector is evaluated for fresh jobs; a contacted job
   uses its durable context even after the selector changes.

[Runpod billing and worker lifecycle](https://docs.runpod.io/serverless/pricing) ·
[POC sample and timing evidence](../reports/simdps_glue_poc_impl_summary.md) ·
[Glue operator runbook](../docs/glue-operator-runbook.md)
