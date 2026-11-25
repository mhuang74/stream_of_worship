# AI transitions on standard Lambda

**November 25, 2026.** Production Glue jobs now run on `sow-glue-worker-standard` in `us-west-2`. Both versions of the legacy Managed Instances worker were deactivated, and the post-cutover EC2 inventory showed no running instances for `sow-glue-capacity`.

## Why we moved

The legacy worker used Managed Instances to support a 55-minute job deadline. An accidental publish left numbered version `1` active with a minimum of three execution environments, even though all seven observed jobs went through `$LATEST.PUBLISHED`. Those jobs ran for **139 seconds combined**. Cost Explorer had reported **$24.51 in estimated EC2 compute** for Nov 23–24 UTC at the time of the investigation, plus **$3.68 in Lambda management fees**. The Nov 24 billing data was still incomplete.

The new design targets **under $5 per month in cloud infrastructure at 500 new jobs per month**. ElevenLabs keeps its separate budget. Spend alerts leave job admission open.

## Production configuration

| Component | Setting |
| --- | --- |
| Worker | Standard on-demand Lambda, x86, 180-second timeout, 3,008 MB memory and 2,048 MB scratch space. AWS capped memory at 3,008 MB in this account. |
| Queue | Existing `sow-glue-jobs` and `sow-glue-jobs-dlq`. Visibility is 1,080 seconds, with redrive after three receives. |
| Event source mapping | Batch size 1, partial-batch failure reporting, standard polling, and maximum concurrency 2. The mapping enforces the limit within the account’s concurrency quota. |
| Deployment | Reuses the render-worker image in ECR. The deploy workflow checks for standard compute and a 180-second timeout. CI’s Glue permissions are scoped to the replacement function. |
| Monitoring | Native Lambda errors, SQS queue age, and DLQ depth alarms. Structured logs have 14-day retention. The legacy custom metric filters and alarms were retired. |

## Deadline and recovery behavior

Each uncached upload gets a 20-second budget including persistence. Compose gets 90 seconds including response streaming. The worker reserves another 20 seconds for durable response storage and 10 seconds to record the outcome. Cached excerpts skip the upload. A request refused before sending can reuse saved upload IDs on retry. A request sent with an unknown outcome still requires an explicit new paid attempt.

An atomic claim protects a live job from duplicate deliveries. The existing staging-only message now bypasses provider calls and uses the saved raw response. A transient staging failure keeps the job eligible for free recovery. The web API, queue message format, provider quota, and retained takes continue to work with stored jobs.

## Cutover and verification

- Deactivated numbered version `1`, provisioned the replacement with its production mapping disabled, and acknowledged a completed job through a separate test queue.
- Disabled the legacy mapping, checked for in-flight jobs, reduced queue visibility, and enabled the replacement mapping. Replayed an existing ready job through the production queue on the final image, then confirmed no Glue Managed Instances remained. The replay verified SQS delivery, worker startup, Postgres access, and safe handling of terminal jobs. It incurred no new ElevenLabs generation and did not exercise a fresh paid transition or render in production.
- The render-worker suite passed 1,171 tests during the migration. The final focused run passed 172 worker tests, and the webapp deployment workflow suite passed 36 tests.
- Implementation commits `ff0119f0` and `f35d6196` are on `reduce_aws_cost_1125`. The production AWS cutover used the container published from that branch.

## Cost tracking

At 500 new jobs plus 100 additional deliveries averaging 45 billed seconds, Lambda compute is about **$1.35 per month before the shared free allowance**, using 3 GiB for the estimate. Queue polling, logs, outbound transfer, and retained R2 data are separate costs. First-week and first-month readings will show how the estimate compares with actual usage.

The `Monthly Glue Infrastructure` AWS Budget has notifications for **$3 actual** and **$5 forecast**. At the time of this report, AWS had yet to list the `Project` cost allocation tag for activation, so the budget was configured but tagged reporting was pending. The operational alarms use the existing Glue SNS topic, which had no confirmed subscribers during verification.

See [ADR 0015](../docs/adr/0015-standard-lambda-for-glue.md) and the [Glue operator runbook](../docs/glue-operator-runbook.md) for rollout and rollback details.
