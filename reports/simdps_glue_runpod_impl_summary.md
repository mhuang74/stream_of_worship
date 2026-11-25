# Runpod migration for the Glue sampler

The Glue worker can generate takes with SimDPS-Multi on Runpod. ElevenLabs remains the default. With `SOW_GLUE_PROVIDER=simdps`, the worker selects Runpod for eligible junctions and routes unsupported window geometry or insufficient guide audio to ElevenLabs before provider contact. The implementation landed on `docs/glue-sampler-runpod-v2` in `245d9f5d`, with recovery hardening in `201ce240` and CI disk preparation in `06698c26`. The branch contains the deployment code. Live staging and production cutover remain open.

[Implementation spec](../specs/migrate_glue_sampler_to_runpod_v2.md) · [Operator runbook](../docs/glue-operator-runbook.md#runpod-staging-and-rollout)

## How it works

1. The standard Glue Lambda claims the SQS delivery and revalidates the saved junction. It builds canonical 44.1 kHz stereo excerpts from the arranged outgoing tail and incoming head. The Runpod montage preserves the integer A and B samples around an exact silent gap sized by the production duration helper. The worker tries up to 18 arranged seconds of the incoming instrumental stem, then the incoming mix if the stem is missing or too short for the latent search.
2. The contact transaction verifies the claim and saved junction, consumes the reserved attempt, and records the selected provider context. The worker submits at most one Runpod `/run` for that job. The CUDA worker runs the pinned ACE-Step 1.5 base prior with 100 steps and the saved seed. It writes the seam to private R2 with a create-only signed PUT. [Provider transport](../delivery/render-worker/src/sow_render_worker/glue_runpod.py) · [GPU handler](../delivery/simdps-worker/handler.py) · [Schema migration](../delivery/webapp/drizzle/0030_glue_provider_context.sql)
3. The Lambda verifies the result size and metadata, checks the persisted montage checksum, compares the A and B samples byte for byte, and validates the fill level. It promotes the seam to the existing immutable raw store and hands it to standard staging. SQS redelivery can recover a stored result after status expiry, a transient promotion failure, a lost database acknowledgement, or a provider rollback. Staging still checks whether the junction is current before selecting the take.
4. The editor shows remaining attempts and reset time for initial generation and another take. Processing retries and compatible ready-take reuse remain free. New takes record the actual provider and model. The canonical descriptor retains the legacy `music_v2_5` token. The API retains deprecated credit estimates during the browser compatibility window.

## Deployment changes

| Setting | Value |
|---|---|
| Standard Glue Lambda timeout | 600 s |
| SQS visibility | At least 3,600 s, verified before the timeout increase |
| SQS event source mapping | Batch size 1, maximum concurrency 2, partial batch failure reporting |
| Runpod execution and job lifetime | 180 s execution, 330 s lifetime |
| Lambda Runpod API budget | 360 s, with a 60 s outcome reserve |
| Temporary R2 objects | 600 s signed URLs, with a one-day prefix lifecycle to apply during rollout |

The GPU build workflow publishes commit-tagged Linux amd64 images and records the digest. The capacity workflow checks the configured Runpod worker maximum even when the endpoint has scaled to zero. The bootstrap script supports isolated queue and Lambda names for staging. [GPU image workflow](../.github/workflows/build-simdps-worker.yml) · [Capacity check](../.github/workflows/check-simdps-capacity.yml) · [Lambda bootstrap](../delivery/render-worker/deploy/aws-up-glue.sh)

## Verification

| Check | Result |
|---|---|
| Render-worker suite | 1,186 tests passed. The additional lost-acknowledgement regression passed in a subsequent focused run. |
| Webapp on Node 24 | 3,591 tests passed, 3 skipped, and 10 marked todo. Typechecking passed. |
| Local PostgreSQL | Drizzle migrations applied and all four Glue smoke tests passed. A separate transaction check verified nullable legacy context, stale claim rejection, single reservation consumption, and guarded raw promotion. |
| Glue POC suites | 72 tests passed across SimDPS, ElevenLabs, and Treblo. The SimDPS dry runs produced 122,880 frames for the original fixture and 132,300 frames with the production three-second floor. |
| GPU image definition | The amd64 Docker build check and the worker dependency lock check passed. Offline GPU model loading and inference remain live staging checks. |

The standards and spec reviews identified a contact-ownership race, a junction snapshot check outside the transaction, an R2 socket timeout issue, and a scratch deployment path that could update the production function. The implementation addressed those findings and added recovery coverage for failures around raw promotion.

## Remaining rollout gates

- Provision an isolated L4 endpoint, Glue Lambda and queue, and private scratch R2 bucket. Verify catalog permissions, apply the scoped lifecycle rule, record the image digest, and test offline model loading and inference. Verify conditional PUT and expiry on real R2, then exercise fallback and recovery through a saved Songset.
- Obtain a blinded listening verdict for production-staged transitions. Run at least 100 representative attempts with at least 20 confirmed cold starts. The launch targets are at least 99% ready, zero duplicate submissions or lost durable results, and acceptance-to-ready p95 at most 60 s.
- Reconcile measured GPU and infrastructure costs with the ElevenLabs baseline and obtain operator approval before enabling SimDPS in production. Verify that supported clients have stopped reading the deprecated credit fields before removing them.

[Rollout and recovery procedures](../docs/glue-operator-runbook.md#runpod-staging-and-rollout)
