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

## Standard Lambda rollout

1. Confirm numbered version `1` has no aliases, event source mappings, or callers. Deactivate it with `put-function-scaling-config --qualifier 1 --function-scaling-config MinExecutionEnvironments=0,MaxExecutionEnvironments=0`.
2. Run `deploy/aws-up-glue.sh` for the replacement. It creates the new production mapping disabled and preserves the legacy queue visibility. For a preflight queue, set `GLUE_QUEUE_NAME` to a test queue name.
3. Verify the replacement on the test queue, then disable the legacy mapping and wait for it to reach `Disabled` and for old invocations to finish. Set production queue visibility to 1,080 seconds and enable the new mapping. The Lambda deployment targets the unqualified standard function.
4. Verify a production delivery, then deactivate the legacy `$LATEST.PUBLISHED` version. Verify the provider's managed EC2 instances terminate. Remove the three retired alarms (`sow-glue-provider-ambiguous`, `sow-glue-heartbeat-missing`, `sow-glue-staging-recovery-spike`) and the legacy Glue log group's `SOW/Glue` metric filters.
5. Scope the CI policy `lambda-render-worker-deploy` to `sow-glue-worker-standard` for Glue deployment. Use the operator profile for manual Managed Instances rollback.
6. To roll back, disable the new mapping first, restore queue visibility to 3,500 seconds, reactivate legacy `$LATEST.PUBLISHED` with min 0 / max 2, and enable its mapping. Keep numbered version `1` deactivated.

## Alerts and spend

Run `deploy/glue-observability.sh` with `GLUE_ALARM_SNS_TOPIC_ARN` for notifications. It keeps 14 days of structured logs and creates `sow-glue-dlq-arrival`, `sow-glue-queue-age`, and `sow-glue-lambda-errors` from native metrics. DLQ arrival does **not** authorize a provider retry.

The `Monthly Glue Infrastructure` AWS Budget uses the `Project=Glue` cost allocation tag, with notices above $3 actual or $5 forecast. Activate the `Project` tag in Billing once AWS lists it. Use per-function billed duration, SQS requests, CloudWatch ingestion, transfer, and the Glue R2 prefix to reconcile the first week and billing month. AWS free allowances are account-wide. Retained Glue Takes remain available and accrue storage as the catalog grows.
