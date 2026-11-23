# Glue generation is a durable job, not a synchronous route

Glue (the user-facing "AI transition") is a paid ElevenLabs music generation inserted at one Junction. v1 deleted the only copy of a paid result when its inputs changed and generated inside the HTTP request path, so a redeploy, a timeout, or an arrangement edit could throw away money and leave the editor without a recoverable result. v2 replaces that with durable jobs on the render-worker image and a dedicated queue.

The decisions below are binding for the implementation. They supersede v1 for future implementation; the untracked v1 specification stays untouched and is not deleted.

**Durable background jobs on a dedicated queue.** A Glue Job is committed to `glue_jobs` before dispatch and executed by `sow-glue-worker` on the render-worker image, fed by its own SQS queue (`sow-glue-jobs`, DLQ `sow-glue-jobs-dlq`) on Lambda Managed Instances. The route returns `202` immediately; it never holds an ElevenLabs request open. The worker claims an atomic phase transition, heartbeats, and runs under a deadline short of the Lambda timeout. This buys restart safety and a place for the 900 s upload / 1800 s compose budgets that Vercel cannot host.

**Paid takes are retained; "Try another" is explicit and paid.** Every successful attempt inserts an immutable ready Glue Take with its own ordinal and seed; a stale descriptor never deletes a take, it only stops it from being effective. Selecting an earlier take for the same descriptor is free, while a new take always requires an explicit paid confirmation and a fresh quota reservation. Descriptor changes therefore cost nothing until the user chooses to generate again.

**One fixed English instrumental prompt, validated on real Traditional Chinese songs.** The provider request carries only model, seed, song ids/ranges, numeric timing, and a fixed English prompt and style lists. It never sends titles, Lyrics, themes, locale, transliteration, or any Chinese text. This is a single audio path for every Recording; there is no language detection and no Chinese prompt variant. The prompt and negative styles were validated against real Traditional Chinese catalog songs, and acceptance still requires a human to verify no vocals and judge musical fit.

**Render preflight blocks with explicit remedies.** Render creation re-resolves every enabled Junction server-side and refuses with `GLUE_NOT_READY` when any enabled Glue lacks an effective ready take, listing each `A → B`, its localized reason, job status, and free/paid Retry, Open editor, and a confirmed Disable action. It never silently falls back to the ordinary transition for an enabled Glue, because a user who paid for a transition would otherwise receive a different deliverable than requested. The accepted snapshot is persisted on the render job so later selection cannot change an in-flight render.

**Raw provider responses are persisted for free staging recovery.** The compose response streams to immutable `glues/<user>/jobs/<job>/response.pcm` with its SHA-256 and byte count recorded before any decode. That persisted response is the only authorization for automatic free recovery: staging can be retried or resumed after a worker failure without another provider call or quota row. Staged WAVs are likewise immutable and checksummed before selection.

**An ambiguous paid request is never retried automatically.** Immediately before the first upload or compose request, the worker consumes the reservation and records provider contact in one compare-and-set against cancellation. If a redelivery or restart finds an in-flight provider phase with no complete durable success record, the job becomes `failed` with an unknown provider outcome; only an explicit user-confirmed paid Try another creates a new attempt. Automatic recovery is limited to pre-contact work, persisted upload ids, and fully persisted raw responses.

## Considered options

- **[Rejected] Generate synchronously in the route** — Vercel's request budget cannot host a 30-minute compose, and a timeout or redeploy loses the paid result with no durable record to recover from.
- **[Rejected] Reuse the render queue and function** — render jobs are long, single-concurrency video encodes; Glue needs its own capacity, timeout, memory, and provider key scoped to one function.
- **[Rejected] Delete takes when inputs change (v1)** — destroys paid output the user may want back; staleness is derived, restoration is free, and deletion buys nothing.
- **[Rejected] Localize the prompt per Recording language** — doubles the prompt surface with no measured benefit and risks sending catalog text to the provider; one fixed English prompt is validated against the actual Traditional Chinese catalog.
- **[Rejected] Silently fall back to the ordinary transition at render** — the deliverable would differ from what the user enabled and paid for; blocking with remedies is honest and recoverable.
- **[Rejected] Retry an ambiguous provider request automatically** — a second compose can charge again for an outcome that may already exist; unknown outcomes must be surfaced, not guessed.

## Consequences

- Glue jobs require the render-worker image, the Glue queue/Lambda, and `SOW_ELEVENLABS_API_KEY` on the webapp's server environment and on the Glue function only; a missing key fails with `GLUE_NOT_CONFIGURED` before any reservation.
- Operators diagnose by `glue_jobs.id` through the runbook; no automatic provider retry is permitted after ambiguity, even when it would look convenient.
- An enabled Junction with a stale or missing take makes render unavailable until the user retries, restores an earlier take, or confirms disabling AI transitions.
