# Removing the Glue Managed Instances leftovers

**November 25, 2026.** The Glue worker moved to standard Lambda in
`us-west-2`, but deactivating the old Managed Instances resources did not
remove them. Every legacy object still existed: a single publish or scaling
call could have restarted the EC2 provisioning that cost **$24.51 of EC2
compute across Nov 23–24, plus $3.68 in Managed Instances management hours**
($28.19 total). This change deletes those resources, removes the repo
instructions that could recreate them, and puts guards in place that do not
depend on tags.

## What was deleted

Verified present before the teardown and absent after it, account
`762288208920`, region `us-west-2`.

| Resource | Identifier | Before | After |
| --- | --- | --- | --- |
| Capacity provider | `sow-glue-capacity` | `Active`, `MaxVCpuCount 16`, `ScalingMode Auto` | deleted; `list-capacity-providers` returns `[]` |
| Legacy function | `sow-glue-worker` | present alongside `sow-glue-worker-standard` | deleted |
| Event source mapping | `0f9a1fb7-aa9c-4cc6-8a00-1ac33746d85d` | `Disabled`, queue `sow-glue-jobs` | deleted |
| IAM role | `sow-glue-capacity-operator` | present with managed EC2 operator policy | `get-role` returns `NoSuchEntity` |
| Log group | `/aws/lambda/sow-glue-worker` | no retention, 17,966 bytes | deleted |
| VPC | `vpc-0d8066989740714d3` (tag `sow-glue`) | present with 2 subnets, SG `sg-006ea270442b6465f`, IGW `igw-049838987cc286f89`, RTB `rtb-0f805e9c3cfd624d6` | all deleted; every ID returns `NotFound` |
| Policy versions | `lambda-render-worker-deploy` `v1`–`v4` | non-default; `v4` granted `lambda:PassCapacityProvider` and `lambda:PutFunctionScalingConfig` on `sow-glue-worker*` | deleted; only default `v5` remains |

The capacity provider could only be removed after the function, because
`delete-capacity-provider` rejects a provider that any function references.
The VPC was safe to delete: `describe-network-interfaces` returned `[]`
beforehand, so nothing held an ENI.

What remains is intentional: `sow-glue-worker-standard` (180 s timeout,
3,008 MB memory, 2,048 MB ephemeral storage, no capacity provider config),
mapping `29796477-c0bc-4cb8-b030-e4b3f3d2f8ca` on `sow-glue-jobs` with
1,080-second visibility, the `sow-glue-jobs`/`sow-glue-jobs-dlq` queues, the
14-day log group `/aws/lambda/sow-glue-worker-standard`, and the three native
alarms.

## Billing evidence

Cost Explorer, `us-east-1`, unblended cost by usage type.

| Day | EC2 compute | Managed Instances management hours |
| --- | --- | --- |
| 2026-11-23 | `$10.7745` `c8a.2xlarge` + `$3.1430` `c8i.2xlarge` = **$13.92** | `$1.6162` + `$0.4715` |
| 2026-11-24 | `$10.5902` `c8a.2xlarge` | `$1.5885` |
| 2026-11-25 | none | none |

For 2026-11-25 the `AWS Lambda` service returns **no** `Managed-Instances`
usage types at all. That day is still partial (account total `$0.15`) and Cost
Explorer finalizes a day about 24 hours later, so the closed-day reading should
be reconfirmed once November 25 settles. Running EC2 inventory was empty
before and after.

## Guards

The tag-based budget could never have caught this spend: the Glue resources
were tagged `Project=Glue` while Billing only knew a lowercase `project` key,
and cost allocation keys are case-sensitive. The guard set is now:

| Guard | Configuration | Purpose |
| --- | --- | --- |
| `Glue EC2 Backstop` | COST, monthly, `$1`, Service = `Amazon Elastic Compute Cloud - Compute`, email above `$1` actual | Tag-independent tripwire. This account runs no EC2 compute, so any alarm means something is provisioning instances again. Reports the pre-cutover spend until November rolls over. |
| `Monthly Glue Infrastructure` | COST, monthly, `$5`, filter `user:project$Glue`, email above `$3` actual or `$5` forecast | Glue-specific spend. Works because the function and both queues are tagged lowercase `project=Glue` and the `project` cost allocation tag is active. |
| `Daily Budget` | COST, daily, `$2`, whole account, email above 100% | Coarse ceiling created outside this work. Recent whole-account days exceed `$2`, so it alarms continuously and is not a Glue signal. |

`arn:aws:sns:us-west-2:762288208920:sow-glue-alarms` was `PendingConfirmation`
with no confirmed subscription through most of this work; the subscription is
now confirmed and the three native alarms deliver to the operator address.

The Glue budget still reads `$0.00`: Billing attributes tagged cost only after
the `project` key was activated, so Glue spend appears from the next
cost-and-usage refresh. The Nov 23–24 charges accrued under the retired capital
key and cannot appear retroactively.

## Repo changes

| Commit | Change |
| --- | --- |
| `4a39ec12` | Removed the `managedinstances="Supported"` base-image mandate from `CLAUDE.md`; pointed ADR 0014's hosting decision at standard Lambda; replaced the runbook rollout section with standard-Lambda operation and image-only rollback; fixed the render-worker README log group and dropped `lambda:PublishVersion` / `lambda:PutFunctionScalingConfig` from the deploying principal. |
| `30a9ffc2` | `deploy/aws-up-glue.sh` tags the Glue function and both queues `project=Glue`, and the runbook records why the key must stay lowercase. |
| `fb556696` | The runbook's spend section now documents all three budgets, their limits, and what an alarm means. |

Deliberate wording choice: the runbook avoids the removed service's name
outside the historical ADR, so a future reader is not tempted to reintroduce a
capacity provider. `reports/` keeps the earlier summary as a historical record.

## Verification

- `rg "Managed Instances|managedinstances|PassCapacityProvider|PutFunctionScalingConfig|3,500" CLAUDE.md docs delivery/render-worker` matches only ADR 0015's history sentence.
- `cd delivery/render-worker && uv run --extra dev pytest tests/test_deploy_glue.py` — 14 passed; the runbook and README doc gates still hold.
- `bash -n delivery/render-worker/deploy/aws-up-glue.sh` — clean.
- `aws lambda list-capacity-providers` → `[]`; only `29796477-…` maps `sow-glue-jobs`; `sow-glue-worker-standard` configuration unchanged; `sow-glue-jobs` visibility `1080`; the three Glue alarms are `OK`.
- Post-merge Deploy run `45530599889` on merge commit `90d2045b` succeeded, including the Glue job's steps: standard-config guard (`Timeout 180`, no capacity provider config), shared image update, and provider-key update. The function reports `LastModified 2026-11-25T02:11:45Z` and its tags remain `project=Glue`.
- `pnpm vitest run src/test/deployment/deployment.test.ts` in `delivery/webapp` — 83 passed after the obsolete `GLUE_CAPACITY_PROVIDER_ARN` assertions were removed; CI's `Webapp Lint & Test` then passed on the merged commit.

See [ADR 0015](../docs/adr/0015-standard-lambda-for-glue.md) and the
[Glue operator runbook](../docs/glue-operator-runbook.md) for ongoing
operation.
