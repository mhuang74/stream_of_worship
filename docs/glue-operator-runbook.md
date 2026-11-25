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

Use when the phase is `uploading` or `composing`, a request may have left the
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

1. Deploy with `delivery/render-worker/deploy/aws-up-glue.sh`. It updates the unqualified `sow-glue-worker-standard` function image and configuration (600 s timeout, 3,008 MB, 2,048 MB ephemeral storage) and, when the mapping does not exist yet, creates the production mapping **disabled**. For preflight, set both `GLUE_QUEUE_NAME` and `GLUE_FUNCTION_NAME` to isolated resource names.
2. Before enabling a newly created mapping, set the `sow-glue-jobs` visibility timeout to 1,080 seconds and enable mapping `29796477-c0bc-4cb8-b030-e4b3f3d2f8ca`. The script clamps visibility to at least 1,080 seconds and never lowers it.
3. No version publishing and no scaling config are used. Deploys replace the `$LATEST` image; there is no numbered version to activate, deactivate, or scale.
4. Roll back by repointing the image only: `aws lambda update-function-code --function-name sow-glue-worker-standard --image-uri <previous ECR tag>`, then `wait function-updated`. Leave queue visibility at 1,080 seconds so the previous image's retries are not cut short.
5. The Managed Instances deployment has been retired. Use the standard function for image rollbacks and the Runpod selector for provider rollbacks.

## Alerts and spend

Run `deploy/glue-observability.sh` with `GLUE_ALARM_SNS_TOPIC_ARN` for notifications. It keeps 14 days of structured logs and creates `sow-glue-dlq-arrival`, `sow-glue-queue-age`, and `sow-glue-lambda-errors` from native metrics. DLQ arrival does **not** authorize a provider retry.

Three budgets guard spend, all notifying `mhuang74@gmail.com`:

- `Glue EC2 Backstop` — COST, MONTHLY, $1 limit, `Service = Amazon Elastic Compute Cloud - Compute`, alarm above $1 actual. Tag-independent: this account runs no EC2 compute (Glue is standard on-demand Lambda), so any alarm means something is provisioning instances again. It also works when tag ingestion is broken, which is why it is the primary tripwire; it reports the EC2 spend recorded before the cutover until November 2026 rolls over.
- `Monthly Glue Infrastructure` — COST, MONTHLY, $5 limit, filter `user:project$Glue`, notices above $3 actual or $5 forecast. The `project` cost allocation tag is active, and the Glue function and both queues are tagged `project=Glue`. The key must stay lowercase: cost allocation keys are case-sensitive, so `Project` and `project` are distinct and only the activated key reports spend — the budget reads `$0` if either side drifts.
- `Daily Budget` — COST, DAILY, $2 limit, whole account, notice above 100%. Recent whole-account days exceed $2, so it alarms continuously; treat it as a coarse ceiling, never as a Glue-specific signal.

Use per-function billed duration, SQS requests, CloudWatch ingestion, transfer, and the Glue R2 prefix to reconcile the first week and billing month. AWS free allowances are account-wide. Retained Glue Takes remain available and accrue storage as the catalog grows.


## Runpod staging and rollout

1. Deploy migration `0030_glue_provider_context` and the attempt-based editor first. Keep the deprecated credit response fields until the previous browser bundles have drained. Verify that supported clients no longer read them before removing them.
2. Set `sow-glue-jobs` visibility to at least 3,600 seconds and verify the AWS readback before raising the standard Glue function to 600 seconds. Keep memory at 3,008 MB, temporary storage at 2,048 MB, and the Glue mapping at batch size 1, maximum concurrency 2, and `ReportBatchItemFailures`. The bootstrap script applies and verifies these values. The CI deploy job checks them before updating the Glue image.
3. Confirm catalog permissions cover sending the clip excerpts and guide to Runpod. Provision a private scratch bucket with a one-day lifecycle rule scoped to `glue-simdps-tmp/`. Add the same scoped rule to the production bucket before enabling Runpod. Preserve adjacent lifecycle rules and final audio retention. The fixture harness writes under `glue-simdps-tmp/poc/` and requires `SIMDPS_POC_R2_BUCKET`.
4. Use the SimDPS build workflow to publish the Linux amd64 image. Deploy its recorded digest to an isolated, private queue-based Runpod endpoint with one L4 per worker, min 0, max 2, a 10 s idle timeout, queue-delay scaling, and enough disk for the unpacked image. Set `SIMDPS_R2_HOST` to the scratch R2 S3 hostname and `SIMDPS_R2_BUCKET` to the scratch bucket. Run `docker run --gpus all --network none <image@digest> --check-model` on a compatible isolated GPU host. Verify an actual job loads offline and reports the expected GPU and revisions.
5. Store the inference key and endpoint ID as GitHub secrets `RUNPOD_API_KEY` and `RUNPOD_ENDPOINT_ID`. Set the same values on the isolated Glue Lambda using the existing server-side deployment environment and retain `SOW_ELEVENLABS_API_KEY`. Keep these keys off the shared render Lambda and webapp. Leave production on `SOW_GLUE_PROVIDER=elevenlabs` for the compatibility deployment.
6. Run the listening harness against the scratch endpoint with `gen_glue_simdps.py --provider runpod`, first with the original POC geometry and then with `--production-duration`. Exercise a saved production junction from the editor through render. Compare raw A/B integer samples, verify that a repeated signed PUT gets 412, and verify expired signing credentials and a forced status outage. Check unsupported junctions reach ElevenLabs and result recovery uses the original Runpod submission.
7. Record the fixture IDs and a blinded listening pass/fail from the designated reviewer. Measure at least 100 representative attempts, including 20 confirmed cold starts and ordinary two-job bursts. Record queue-to-ready warm and cold distributions, failed attempts, execution retries, duplicate submissions and lost results. Targets are at least 99% ready, zero duplicate paid submissions or lost durable results, and p95 at most 60 seconds. Enable the Runpod rejection alarm with `GLUE_RUNPOD_ALERTS=1` and `GLUE_ALARM_SNS_TOPIC_ARN` when running `deploy/glue-observability.sh`. Include the optional custom metric in the AWS cost review. Review the AWS alarms, the Runpod endpoint and account limits, and cost per ready take using actual billing. Include cold starts, idle, failures, retries, Lambda, R2 and SQS, and compare the fallback-adjusted 500-attempt monthly projection with ElevenLabs-only billing and ADR 0015. Obtain operator approval before enabling the production selector.
8. Give the scheduled capacity check a separate `RUNPOD_MONITOR_API_KEY` with `serverless:read` and enable notification for workflow failures. Verify it once before production enablement. It checks the configured maximum via the Runpod management API. Zero live workers can be normal with min 0. Runpod may reduce the configured maximum to 0 after prolonged inactivity. If capacity falls, select ElevenLabs for fresh attempts, inspect the endpoint and billing in the console, restore max 2 and verify a staging job before switching the selector back. [Runpod endpoint settings](https://docs.runpod.io/serverless/endpoints/endpoint-configurations) · [Endpoint management API](https://docs.runpod.io/api-reference-v2/serverless/get-a-serverless-endpoint)

### Runpod redrive and rollback

Switch `SOW_GLUE_PROVIDER` to `elevenlabs` for fresh work. Keep the recovery-capable image deployed and retain Runpod credentials, the endpoint and the temporary prefix until contacted jobs drain. Check active jobs with `provider_context_json->>'provider' = 'simdps'` and review the DLQ before retiring the endpoint or reducing Lambda timeouts.

For a contacted Runpod job in the DLQ, record the owned job ID, phase, submission ID, result key, saved checksums and remote ID if present. Inspect the result and raw metadata before redriving. Redrive the original SQS message while the temporary objects are within their one-day retention. A stale delivery verifies the temporary result against the saved montage, verifies any existing immutable raw object during promotion, records raw metadata and resumes staging. A job with existing raw metadata follows the free staging path. Do not clear contact state, manually call `/run` or use Runpod's retry endpoint.

If redrive is exhausted, inspect both R2 objects and the last sanitized Lambda events for failed reads or writes. Restore infrastructure access and redrive the same active job if its verified result is still recoverable. Treat late objects for terminal jobs as operator evidence. Any manual recovery of a terminal result needs an explicit decision and must preserve newer selections. Record unresolved jobs and take IDs alongside the billing review.
