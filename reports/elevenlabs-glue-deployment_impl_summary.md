# ElevenLabs Glue (AI transitions) — production deployment summary

Date: 2026-11-23. Repo `mhuang74/stream_of_worship`; production webapp
`https://app.streamofworship.com`; region `us-west-2`, account `762288208920`.

Status: **shipped and proven end to end in production.** Three PRs merged, all
deploy workflows green, two paid generations produced takes that a real render
consumed.

## Merges

| PR | Squash merge | Content |
|---|---|---|
| 325 | `763a1629` | Glue feature itself (durable jobs, queue, worker, webapp UI/API, render preflight) |
| 327 | `b50eb3f0` | Deploy fixes: scaling retry, timeout 3300 s, metric-filter fix, 409 mapping |
| 328 | `2430f02e` | Published-version timeout guard + operator docs |

Deploy runs: `45131155447` (first, Glue job failed on scaling), `45135725699`
(green), `45136965978` (green, exercising the new published-timeout guard).

## Provisioned resources

| Resource | Configuration |
|---|---|
| VPC `sow-glue` | `10.60.0.0/16`, two public subnets (`us-west-2a`, `us-west-2c`) with IGW route, `sow-glue-worker-sg` (no inbound) |
| Capacity provider `sow-glue-capacity` | Active, `x86_64`, `MaxVCpuCount=16`, operator role `sow-glue-capacity-operator` (`AWSLambdaManagedEC2ResourceOperator`) |
| Queues | `sow-glue-jobs` visibility 3500 s, redrive `maxReceiveCount=3` → `sow-glue-jobs-dlq` |
| Function `sow-glue-worker` | Managed Instances, timeout **3300 s**, memory 3072 MB, ephemeral 2048 MB, `PerExecutionEnvironmentMaxConcurrency=1`, image from ECR `sow-render-worker` (Python 3.13, AL2023), `$LATEST.PUBLISHED` Active with timeout 3300 |
| Scaling | min 0 / max 2 execution environments (2-way provider concurrency; the ESM rejects `MaximumConcurrency`) |
| Event source mapping | batch 1, `ReportBatchItemFailures`, Enabled |
| Environment | Glue function has the provider key + queue URL; `sow-render-worker` has **no** provider key (verified `None`) |
| Observability | 12 metric filters + 5 alarms (`dlq-arrival`, `provider-ambiguous`, `queue-age`, `heartbeat-missing`, `staging-recovery-spike`), thresholds 3300 s / 3,300,000 ms, all ActionsEnabled, SNS `sow-glue-alarms` |
| IAM | shared policy `lambda-render-worker-deploy` v4 (Glue function family, capacity provider, `sow-glue-jobs` SendMessage) |
| Config | GitHub secret `SOW_ELEVENLABS_API_KEY`; Vercel prod `SOW_GLUE_SQS_QUEUE_URL`, `SOW_GLUE_AWS_REGION`, `SOW_ELEVENLABS_API_KEY` (production only, never Preview) |

## Defects found by the deployment and fixed

1. **`publish-version` without `--publish-to LATEST_PUBLISHED`** (fixed before the
   first merge, PR 325). A plain publish creates a numbered version the ESM never
   invokes.
2. **`PutFunctionScalingConfig` raced the new version** — `ResourceConflictException`
   failed the first deploy job. Now retried on that code only (30 × 10 s), then
   `AppliedFunctionScalingConfig` is read back and must be min 0 / max 2.
3. **Timeout could not satisfy its own deadline gate.** First paid job
   `d8436130` failed `GLUE_EXECUTION_DEADLINE` at the compose gate: `remainingMs`
   was 1,895,669 at invoke, `require_deadline(compose)` needs 1,890 s, and an
   upload may consume its whole 990 s reserve first. `1900 < 990 + 1890`. Raised
   to 3300 s (visibility 3500, alarm budget 3300); a test now pins
   `timeout >= upload+compose reserves` and `visibility > timeout`.
4. **Every Glue metric filter was dead.** Lambda's JSON log format wraps records
   as `{timestamp, level, message}`; patterns written as `{ $.event = … }` matched
   nothing (`glue.job.claimed` in logs, `GlueJobClaimed` zero datapoints). Filters
   now match `$.message`, and `retryKind` is logged on terminal failures so
   `GluePaidFailure` can ever fire. Validated in a scratch log group: claimed,
   paid, and free lines each matched exactly once.
5. **Render admission race returned 500 instead of 409.** Drizzle carries the
   partial-index constraint on `err.cause`; the route only inspected
   `err.message`. Now walks the cause chain.
6. **Verification gap closed:** the deploy's Active-version gate now requires the
   *published* timeout to be 3300 s, not just `State=Active` — the exact shape of
   defect 3 (unqualified read said 3300 while the published snapshot served 1900).

## End-to-end evidence

| Step | Evidence |
|---|---|
| Dispatch | `POST /api/songsets/…/glue-jobs` → SQS → ESM invocation `07ae1d85…` / `246e7e7b…` |
| Provider | `provider_status=200` at compose; raw response persisted, 4,762,800 bytes, SHA-256 recorded |
| Take | `defa5544-fd1c-4dcb-8638-b173516cf3d8` (3.0 s), `a6116a42-7ddb-4983-b054-7cd06a76a967` (3.0 s), both `status=ready` |
| Job lifecycle | `128f5068` created 07:45:33 → ready 07:46:01 (28 s); log `glue.job.ready` + `glue.staging.recovered` |
| Render | `V5OxHIU9OzJGni9vaT9zr` render elapsed **73.6 s** (`started_at` 07:57:08 → `completed_at` 07:58:22); output media length 152.45 s for a 146 s songset + 5 s title card. `glue_transitions` holds both takes (take id, `r2_key`, `sha256`, `seconds`); worker logs `glue.render.glue.loaded` for both |
| Artifacts | `renders/V5OxHIU9OzJGni9vaT9zr/output.mp3` and `output.mp4` recorded on the job |

## Cost and quota

- Two paid generations, each a **separate junction**: `128f5068` (item 0 →
  item 1, quoted 405 credits) and `65a3b228` (item 1 → item 2, quoted 45
  credits). Both jobs did a full upload + compose of their own raw response
  (distinct `raw_response_r2_key`, both 4,762,800 bytes); neither reused the
  other's upload. The 405-vs-45 difference is *not* explained by any recorded
  evidence and is left as observed.
- The failed job (`d8436130`) consumed one daily quota unit without producing a
  take (`glue_paid_attempts` `dcf21b8c`, state `consumed`). Per ADR 0014 a
  consumed reservation is never refunded automatically.
- Capacity costs nothing while idle: min 0 execution environments.

## Current state of the test songset

`Test Arrangement` (`i3-Ps8YpofpBmJ8_rNLzN`): both junctions switched back to
ordinary transitions after the smoke (`glue_enabled=false`), paid takes retained
and still selectable — the ADR's "disabling keeps the take" path exercised for
real.

## Residual notes

- `SOW/Glue` datapoints begin with the next Glue job; the filters were corrected
  after today's runs, so the counters are empty until then. The patterns were
  verified in a scratch log group rather than in production logs.
- Any future change to the function timeout/memory/env requires
  `publish-version --publish-to LATEST_PUBLISHED` plus a scaling re-apply; the
  numbered version it would otherwise create is never invoked. Documented in
  `docs/glue-operator-runbook.md` ("Configuration changes require a republish")
  and enforced by the deploy guard.
- The deployed image is the shared `sow-render-worker` image; Glue and render
  share code but not the provider key, and Glue is capped at two execution
  environments.
