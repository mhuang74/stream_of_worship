# AI transition provider cascade

**Superseded:** this summary describes the original `b01e0aa9` cascade. The current policy is `cascade-simdps` — RunPod, then Modal, then ElevenLabs, strictly sequential. See [ADR 0017](../docs/adr/0017-cascade-simdps-sequential-failover.md) and [the operator runbook](../docs/glue-operator-runbook.md#provider-cascade).

The opt-in cascade routes a Glue Job through ElevenLabs, then SimDPS on Modal L4, with RunPod L4 available when Modal fails or runs past its hedge threshold. One confirmation authorizes one quota slot, one seed, and at most one new Glue Take. The implementation is in `b01e0aa9` on `docs/glue-sampler-runpod-v2`. ElevenLabs is the default for the compatibility rollout. [Architecture decision](../docs/adr/0016-glue-provider-cascade.md) · [Operator runbook](../docs/glue-operator-runbook.md#provider-cascade)

## Routing and recovery

- ElevenLabs gets 60 seconds from the first provider operation, including uncached uploads. Errors, timeouts, and unusable audio trigger GPU fallback. Fresh copyright rejections advance to Modal and cached rejections skip ElevenLabs. Each compose is sent at most once. An ambiguous compose may still incur a provider charge.
- RunPod starts when Modal fails or is still pending after 180 seconds from submission, including queueing and startup. Modal stays eligible after RunPod starts. The worker checks Modal first when both results are visible in the same poll and requests cancellation of the losing call when it has a remote ID.
- The worker guards each submission with the active claim and saved Junction, persists intent before contact, and consumes the quota slot at first contact. Redelivery uses stored remote IDs and immutable result keys to recover existing work. Only the recorded winner can become the canonical raw response, and its backend and model are recorded on the take. Verified raw audio supports free processing recovery. An exhausted contacted generation can offer a new paid confirmation.
- Both GPU services share the pinned ACE-Step base sampler, 100 steps, the saved seed, and the signed R2 contract. The worker prepares the montage and guide once, reuses the bytes across submission prefixes, and verifies result metadata, preserved PCM frames, and generated audio level before selecting a winner. [Cascade worker](../delivery/render-worker/src/sow_render_worker/glue_cascade.py) · [Modal app](../delivery/simdps-worker/modal_app.py) · [Shared GPU handler](../delivery/simdps-worker/handler.py)

## Editor and deployment

The Junction editor shows a longer-running message in English and Traditional Chinese at three minutes from job creation. Elapsed time includes the SQS wait. Reopening the editor rediscovers the job through existing status streaming and polling and shows the take or applicable retry. Cascade quotes allow GPU generation after cached and legacy ElevenLabs copyright rejections. Saved-input and minimum-excerpt checks still apply. A Junction with incompatible GPU geometry or guide audio prompts the user to adjust it, and compatible ready takes remain available.

| Setting | Value |
|---|---|
| Fresh-job selector | `SOW_GLUE_PROVIDER=cascade` on the worker and webapp server |
| ElevenLabs window | 60 seconds by default via `SOW_GLUE_ELEVENLABS_SECONDS` |
| Modal hedge | 180 seconds by default via `SOW_GLUE_MODAL_HEDGE_SECONDS` |
| Modal capacity | One L4 and one input per container, zero minimum and two maximum containers |
| RunPod limits | 360-second polling budget, 330-second remote lifetime, 180-second execution limit |
| Glue Lambda and queue | 780-second Lambda timeout, with SQS visibility verified at 4,680 seconds or higher before the increase |
| Verification and finalization reserve | 60 seconds |

Rollback selects ElevenLabs for fresh jobs while the compatible worker and both GPU endpoints drain saved cascade contexts. An abrupt worker crash can delay recovery until redelivery. The existing SQS redrive path resumes the original job. [Deployment workflow](../.github/workflows/deploy.yml) · [Redrive and rollback](../docs/glue-operator-runbook.md#redrive-and-rollback)

## Verification and rollout status

- **Worker:** 1,207 tests passed. Cascade tests exercised fake-clock routing, both GPU winners, simultaneous completion, invalid output, snapshot and claim guards, duplicate delivery, and crash recovery.
- **Webapp:** 3,596 tests passed, 3 skipped, and 10 marked todo. TypeScript checks passed. Editor tests covered the three-minute threshold, reopening, and retry in both locales.
- **Live rollout:** The compatibility deployment and GPU staging remain pending. The `sow-glue-simdps-r2` secret was absent in Modal’s `main` environment. Staging must verify catalog clearance for both GPU services, actual L4 execution, pinned model loading, R2 conditional writes and expiry, and rendered audio quality. Review acceptance-to-ready latency, queue and sampling time, fallback and overlap rates, and cost per ready take separately for ordinary ElevenLabs jobs and cold GPU fallbacks before operator approval. [Compatibility deployment and staging](../docs/glue-operator-runbook.md#compatibility-deployment-and-staging)
