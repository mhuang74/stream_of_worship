# Glue Operator Runbook

Operator recovery for durable Glue (AI transition) jobs, keyed by
`glue_jobs.id`. Implementation reference: `specs/inpaint-transition-elevenlabs-v2.md`
§2.4, §3.2, §4.2, §6.4. This runbook is about infrastructure recovery; it never
authorizes a paid provider call.

## Safety rules

- Never print or paste API keys, signed URLs, raw provider responses, Lyrics,
  or unrestricted prompt text into a ticket, chat, or shell transcript.
- A DLQ arrival does **not** authorize a provider retry. It only says a delivery
  failed three times.
- Never repeat a paid upload or compose whose outcome is ambiguous. Only the
  user's explicit, confirmed new paid attempt can do that.
- Recovery is free when durable state proves the provider was never contacted,
  or when a complete/checksummed raw response already exists.
- Keep terminal jobs (`ready`, `failed`, `cancelled`, `dispatch-failed`) outside
  the active index. A stale queued record stays recoverable by id; age alone
  does not make it spent.

## 1. Inspect the job by id

Start here for every alert. Use the job id, never a title or item name.

```sql
SELECT j.id,
       j.songset_id,
       j.from_item_id,
       j.to_item_id,
       j.take_mode,
       j.descriptor_key,
       j.descriptor_json,
       j.phase,
       j.active,
       j.dispatch_state,
       j.dispatch_attempt,
       j.attempt_id,
       j.heartbeat_at,
       j.provider_contacted,
       j.provider_phase,
       j.provider_status,
       j.paid_reservation_id,
       j.paid_reservation_state,
       j.retry_kind,
       j.pending_take_id,
       j.take_ordinal,
       j.seed,
       j.resulting_take_id,
       j.active_take_id,
       j.raw_response_r2_key,
       j.raw_response_sha256,
       j.raw_response_bytes,
       j.error_code,
       j.error_detail,
       j.created_at,
       j.started_at,
       j.updated_at,
       j.finished_at
FROM glue_jobs j
WHERE j.id = '<glue_jobs.id>';
```

Then inspect the paid attempt and any persisted upload ids:

```sql
SELECT id, glue_job_id, state, consumed_at, released_at, created_at
FROM glue_paid_attempts
WHERE glue_job_id = '<glue_jobs.id>';

SELECT user_id, logical_key, side, phase, elevenlabs_song_id,
       audio_sha256, rejection_code, safe_failure_detail, updated_at
FROM glue_excerpts
WHERE user_id = <user_id>;
```

Checklist for the inspection:

- descriptor key and the **A → B** item pair (`from_item_id`, `to_item_id`)
- current `phase` and whether `active` agrees with it
- `attempt_id`, `dispatch_state`, `dispatch_attempt`
- `heartbeat_at` (check the 210 s claim expiry)
- `provider_contacted`, `provider_phase`, `provider_status`
- paid reservation id/state and the `glue_paid_attempts` row
- persisted successful upload `elevenlabs_song_id` values
- raw-response key, SHA-256, and byte count
- resulting/active take, `retry_kind`
- structured logs for the same job id

```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/sow-glue-worker-standard \
  --filter-pattern '"<glue_jobs.id>"' \
  --limit 100
```

Stable events include `glue.job.created`, `glue.dispatch.failed`,
`glue.provider.upload.succeeded`, `glue.provider.ambiguous`,
`glue.response.persisted`, `glue.staging.recovered`, `glue.take.selected`,
`glue.job.failed`, and `glue.render.preflight.failed`. Never copy a field named
`key`, `url`, `signedUrl`, or a raw provider body from a log line.

Classify the job before acting:

| Evidence | Class |
|---|---|
| `provider_contacted = false`, phase `queued`/`dispatch-failed`, or heartbeat older than the budget | Free dispatch |
| `provider_contacted = true`, raw response key/SHA/bytes verified, phase `storing-response`/`staging` | Free staging recovery |
| Persisted upload `elevenlabs_song_id` exists, no compose was sent, descriptor/cancellation still valid | Conditional resume |
| Phase `uploading`/`composing`, request may have been sent, no durable success record | Ambiguous paid outcome |
| Staged object exists but the take/selection transaction did not commit | Reconcile finalization |
| Terminal phase | Recovered-by-id/terminal; no active-index action |

**What the user sees while you inspect:** the persisted phase and elapsed time,
never a fabricated percent completion. Do not change the job just to clear an
alert.

## 2. Free fresh dispatch (queued / no heartbeat, no provider contact)

Use when the job is `queued` (or `dispatch-failed`) and `provider_contacted =
false`. The user may have waited past the operation budget, but the paid
attempt was never consumed.

1. Confirm `active`/`phase` and that `glue_paid_attempts.state` is still
   `reserved` (or no attempt row exists).
2. Prefer the user-facing free action
   `POST /api/glue-jobs/<id>/redispatch`, which reuses the same job and
   idempotency identity.
3. If the API is unavailable, enqueue the same `<jobId, songsetId, userId>`
   message to `sow-glue-jobs`; do not create a new job, take, seed, or
   reservation.
4. Re-inspect `dispatch_attempt`, `phase`, and the active index afterward.

This recovery is **free** and consumes no quota. Do not mark the job spent.

**What the user sees:** the existing job resumes with its real phase/elapsed
time; no new charge, and Preview/Listen keep using the ordinary transition or
any previously selected ready take.

## 3. Staging-recovery only (verified raw response)

Use when a compose succeeded and the raw response was stored and checksummed,
but decode/QA/staging/R2 metadata/DB finalization failed. The job is at
`storing-response` or `staging` with a raw-response key, SHA-256, and byte
count.

1. Verify the raw object exists and matches its recorded byte count and SHA-256.
   Do not offer signed URLs or object contents in chat/tickets.
2. Confirm the descriptor still matches the persisted descriptor key/JSON and
   the job is not cancelled.
3. Use `POST /api/glue-jobs/<id>/retry-processing`, or invoke the worker in
   `staging` mode for this same job id. It must not compose again.
4. Confirm exactly one take is created/selected and one
   `glue.staging.recovered` event is emitted.

This recovery is **free**, never consumes quota, and must never call upload or
compose.

**What the user sees:** “Processing can be retried for free” — the existing
take/selection is completed without another charge or another take.

## 4. Conditional resume after a persisted upload

Use only when durable state proves exactly which provider requests completed:
a successful upload `elevenlabs_song_id` is persisted, compose was definitely
not sent, the descriptor still matches, and cancellation did not win.

1. Verify the upload row belongs to this user/job and its excerpt identity still
   matches the current descriptor.
2. Confirm `record_provider_contact` did not leave the job
   `uploading`/`composing` with an unknown request.
3. Resume the same job; skip the completed upload and continue from the first
   not-yet-sent request only.
4. Never repeat the persisted upload automatically.

**What the user sees:** the job continues from where it safely stopped; no
duplicate upload charge and no duplicated take.

## 5. Never blindly requeue an ambiguous paid request

Use for a legacy ElevenLabs context when the phase is `uploading` or `composing` and a request may have left the
machine, and there is no complete durable success record. This includes DLQ
arrivals for such jobs.

1. Do not call upload or compose again, and do not enqueue another provider
   attempt.
2. Retain/record the unknown outcome with `GLUE_PROVIDER_UNKNOWN` and
   `retry_kind = "paid"` if it is not already terminal.
3. The user must explicitly confirm a new paid attempt (a fresh take, seed, and
   paid quota slot). DLQ arrival is not that confirmation.
4. Preserve every existing take and raw object.

**What the user sees:** “The provider outcome is unknown; another attempt may
be charged.” The job id is shown inline and the previous selected take remains
usable.

## 6. Reconcile a staging success whose final DB write failed

Use when the immutable staged R2 object exists and its metadata is valid, but
the take/selection transaction did not commit.

1. Verify the staged object’s key, length, and checksum against the recorded
   staging metrics.
2. Re-run only the finalization transaction for the same job, descriptor, and
   pending take id/ordinal/seed.
3. Commit the take and selection only if the descriptor still matches and the
   job is not cancelled. Never create another take and never mutate or
   overwrite the staged object.
4. Mark the job terminal and remove it from the active index.

**What the user sees:** the take appears as ready and selected, with no second
take and no extra provider charge.

## 7. Terminal jobs and stale queue records

- `ready`, `failed`, `cancelled`, and `dispatch-failed` are terminal for the
  job. They must not appear in the active index.
- A queued record with an old heartbeat is **not** automatically a spent paid
  attempt. Recover it by job id using §2 (free dispatch) or §3 (verified raw
  response) as its durable state allows.
- Never delete takes or cache objects during recovery. A reverted
  arrangement/order can make an earlier take current again.

**What the user sees:** terminal jobs stop showing as active; a recovered
queued job resumes with its real phase rather than a fabricated percent.

## Standard Lambda operation

Deploy with `delivery/render-worker/deploy/aws-up-glue.sh`. Raise and verify queue visibility at 4,680 seconds or higher before raising the standard Glue Lambda timeout to 780 seconds. The bootstrap script and CI deploy apply these changes in that order. Keep memory at 3,008 MB, temporary storage at 2,048 MB, and the mapping at batch size 1, maximum concurrency 2, and `ReportBatchItemFailures`. Keep the existing redrive policy. The CI role needs permission to set the Glue queue attributes. [AWS SQS visibility guidance](https://docs.aws.amazon.com/lambda/latest/dg/services-sqs-configure.html)

For preflight use isolated `GLUE_QUEUE_NAME` and `GLUE_FUNCTION_NAME` resources. Bootstrap creates a new mapping disabled. Verify its queue and function before enabling it.

## Alerts and spend

Run `deploy/glue-observability.sh` with `GLUE_ALARM_SNS_TOPIC_ARN` for notifications. It keeps 14 days of structured logs and creates `sow-glue-dlq-arrival`, `sow-glue-queue-age`, and `sow-glue-lambda-errors` from native metrics. DLQ arrival does **not** authorize a provider retry.

Three budgets guard spend, all notifying `mhuang74@gmail.com`:

- `Glue EC2 Backstop` — COST, MONTHLY, $1 limit, `Service = Amazon Elastic Compute Cloud - Compute`, alarm above $1 actual. Tag-independent: this account runs no EC2 compute (Glue is standard on-demand Lambda), so any alarm means something is provisioning instances again. It also works when tag ingestion is broken, which is why it is the primary tripwire; it reports the EC2 spend recorded before the cutover until November 2026 rolls over.
- `Monthly Glue Infrastructure` — COST, MONTHLY, $5 limit, filter `user:project$Glue`, notices above $3 actual or $5 forecast. The `project` cost allocation tag is active, and the Glue function and both queues are tagged `project=Glue`. The key must stay lowercase: cost allocation keys are case-sensitive, so `Project` and `project` are distinct and only the activated key reports spend — the budget reads `$0` if either side drifts.
- `Daily Budget` — COST, DAILY, $2 limit, whole account, notice above 100%. Recent whole-account days exceed $2, so it alarms continuously; treat it as a coarse ceiling, never as a Glue-specific signal.

Use per-function billed duration, SQS requests, CloudWatch ingestion, transfer, and the Glue R2 prefix to reconcile the first week and billing month. AWS free allowances are account-wide. Retained Glue Takes remain available and accrue storage as the catalog grows.


## Provider cascade

`SOW_GLUE_PROVIDER=cascade` selects the cascade for fresh jobs. Version 2 provider context records the policy and deadlines per job. The worker continues reading the static ElevenLabs and SimDPS contexts.

- ElevenLabs gets 60 seconds from its first provider operation, including uploads. Cached copyright rejections skip ElevenLabs. A possibly billed compose goes out at most once. Any ElevenLabs failure can advance to GPU generation.
- Modal starts first. RunPod starts when Modal fails or is pending after 180 seconds from submission, including queue and startup. The worker checks Modal first in each poll and requests cancellation of any losing call whose remote ID is known. The winning audio must verify against the saved geometry.
- `SOW_GLUE_ELEVENLABS_SECONDS` and `SOW_GLUE_MODAL_HEDGE_SECONDS` are positive whole seconds. Their sum plus the 360-second RunPod polling budget, 40 seconds of minimum preparation time and 60 seconds of verification and finalization must fit in 780 seconds. RunPod retains its 330-second remote lifetime and 180-second execution limit. [RunPod operations](https://docs.runpod.io/serverless/endpoints/operation-reference)
- The editor shows the longer-running message after three minutes from creation, including SQS queue time. The job continues in the background and can be reopened for the take or retry.

### Compatibility deployment and staging

1. Deploy migration `0030_glue_provider_context`, the compatible worker and editor with `SOW_GLUE_PROVIDER=elevenlabs` in production. Keep deprecated credit response fields while previous browser bundles drain.
2. Verify catalog clearance for sending excerpts and the guide to **both** GPU services. Use an isolated R2 bucket for staging. Add a one-day lifecycle rule scoped to `glue-simdps-tmp/` in staging and production. Preserve adjacent rules and canonical audio retention.
3. Use the SimDPS image workflow to publish the Linux amd64 image. Run `docker run --gpus all --network none <image@digest> --check-model` on an isolated GPU host. Deploy that digest to the private RunPod queue endpoint with one L4 per worker, min 0, max 2, 10-second idle timeout and queue-delay scaling. Set `SIMDPS_R2_HOST` and `SIMDPS_R2_BUCKET` on the endpoint.

   **Image input for both services** (immutable per commit, `ENV HF_HUB_OFFLINE=1` baked weights):

   ```
   ghcr.io/mhuang74/stream_of_worship-simdps-worker@sha256:23fbb0f528633b2a5034166c54efadd83246d8bf6d94d2ed5b921b61fd051cc6
   ```

   Published by run [`45717439002`](https://github.com/mhuang74/stream_of_worship/actions/runs/45717439002) for commit `2514dcdb`; `delivery/simdps-worker/**` and `simdps_audio.py` are unchanged from that commit through `7a6dfa01`, so the digest still describes the current worker. RunPod already loaded `acestep-v15-base` from those baked weights on an NVIDIA L4 with this digest (9.055 s model load, 18.694 s sampling, `staging ok`, endpoint `ry7hkdvvq7cvj7`), which is the offline-completeness proof; re-run `--check-model` only if a new digest is published.
4. Create a Modal secret named `sow-glue-simdps-r2` holding the same R2 host and bucket variables. For a private image, also configure a Modal registry secret and set `SIMDPS_MODAL_REGISTRY_SECRET` to its name. From the repository root deploy the same immutable image digest with:

   ```bash
   SIMDPS_IMAGE="$SIMDPS_IMAGE_DIGEST" uv run --project delivery/render-worker \
     modal deploy --env "$SOW_GLUE_MODAL_ENVIRONMENT" delivery/simdps-worker/modal_app.py
   ```

   The app uses one L4 and one input per container, scales to zero, and caps at two containers. [Modal invocation methods](https://modal.com/docs/guide/function-invocation-methods)
5. Supply `SOW_ELEVENLABS_API_KEY`, `RUNPOD_API_KEY`, `RUNPOD_ENDPOINT_ID`, `MODAL_TOKEN_ID`, and `MODAL_TOKEN_SECRET` to the Glue Lambda through its deployment environment. Set `SOW_GLUE_MODAL_ENVIRONMENT` to the deployed Modal environment if it differs from `main`. Keep GPU credentials off the render Lambda and webapp. Set `SOW_GLUE_PROVIDER=cascade` on the isolated worker and in the isolated webapp server environment so the quote and worker agree.
6. Run saved Junctions through the editor and render on staging. Include ElevenLabs success, fresh and cached copyright rejection, shared GPU ineligibility, cold Modal and RunPod runs, cancellation, and redelivery from lost submit acknowledgements and delayed status. Verify both services report an L4, pinned ACE-Step base and VAE revisions, 100 steps, the saved seed and precise frame geometry. Compare the preserved A/B PCM with the montage. Verify a repeat signed PUT returns 412, an expired URL fails, and the rendered take passes a blinded listening check.
7. Record at least 100 representative attempts, including 20 confirmed cold starts and ordinary two-job bursts. Report acceptance-to-ready percentiles, queue and startup time, sampling time, fallback and overlap rates, and cost per ready take. Split ordinary ElevenLabs and cold GPU fallbacks. Review both against the existing gates of at least 99% ready, zero duplicate paid submissions or lost durable results, and p95 at most 60 seconds. A slower cold cohort needs an explicit operator decision before activation. Reconcile billed ElevenLabs calls, both GPU services, Lambda, R2 and SQS, and compare the fallback-adjusted 500-attempt monthly projection with ElevenLabs-only billing. Record fixture IDs, render and listening results, and the operator's approval.
8. Verify the scheduled RunPod capacity check using a separate `RUNPOD_MONITOR_API_KEY` with `serverless:read`. Zero live workers is expected at idle. A configured maximum of zero requires restoring capacity and running a staging job. [RunPod endpoint settings](https://docs.runpod.io/serverless/endpoints/endpoint-configurations)

Enable production only after the staging review and operator approval. Set the worker selector to `cascade` first, then deploy the webapp with the matching server-side selector. A legacy copyright failure becomes eligible once the cascade quote is active.

### Redrive and rollback

An abrupt crash can delay recovery until the queue makes the message visible again. Inspect the active Job's provider context, including each intent, its remote ID if available, signed-contract result key, and the winner's checksum and byte count. Redrive the original SQS message within the temporary objects' retention period. The worker can check a result key even if it never received a remote ID. The saved deadline governs further submissions, and a recovered verified result can still be promoted or staged after that deadline.

A Job with canonical raw metadata has a free processing retry. An exhausted contacted Job without verified audio can offer a new paid confirmation. A Job whose excerpts or shared GPU geometry are incompatible requires an edit. Preserve the contact record and the original message identity. Late objects on terminal Jobs remain available for inspection within retention. Record unresolved Jobs and any manual recovery decisions with the billing review.

For provider rollback, set the webapp and worker selector to `elevenlabs` for fresh Jobs. Keep the compatible worker image, both GPU endpoints and credentials, queue visibility, and temporary objects available until version 2 contexts drain. Check active contexts with `provider_context_json->>'provider' IN ('cascade', 'simdps')` and inspect the DLQ before retiring an endpoint. Keep the compatible worker in place for recovery even when fresh Jobs use ElevenLabs.
