# LRC Timecode Verification & Fixing Automation — Design

High-level design and rationale for automating detection and fixing of LRC issues
(timing and content) for songs in `review` visibility. Decomposed into GitHub
issues #236–#240 (`mhuang74/stream_of_worship`).

## Problem Statement

Pipeline-generated LRC files land in `review` visibility. Today a human must
hand-check each one — cache audio, build clean vocal stems, transcribe,
forced-align, upload, score — before promoting it to `published` via
`sow-admin audio set-visibility`. This manual loop does not scale with the
review backlog, and the backlog regrows every time LRC generation runs.

The goal: given a set of songs whose timecodes are manually verified
(`published`), automate verifying and fixing the timecodes of songs in
`review` — and ultimately detect content problems (missing/extra/wrong lyric
lines) too.

## Constraints Discovered (Why the Design Looks Like This)

These findings came out of design interrogation of the codebase; each one
shaped the design:

1. **Published ≠ verified.** Recording visibility is free text
   (`published`/`review`/`hold`/NULL). When an LRC lands on a recording whose
   visibility is NULL, the catalog update auto-promotes it to `published`.
   So the published set is contaminated with never-reviewed LRCs. *Consequence:
   calibration needs a maintainer-supplied known-good list, not "all
   published".*

2. **LRC bytes live only in R2.** The canonical object is
   `{hash_prefix}/lyrics.lrc`; Postgres stores pointers, status, and
   provenance (`lrc_source`), never per-line timestamps. *Consequence: "fixing"
   a timecode means replacing an R2 object; there is no DB surgery.*

3. **The forced-alignment job is not side-effect-free.** At completion it
   uploads the aligned LRC to the *canonical* key. Running it as a "verify"
   step would overwrite the live LRC (and the CLI caller would then demote
   visibility to `review`) before any comparison exists. *Consequence: a
   staging output mode is a hard prerequisite for compare-first verification.*

4. **The aligner is fed the LRC's own text.** Forced alignment input is the
   current LRC's text with timestamps stripped (falling back to nominal
   catalog lyrics). Alignment therefore answers "do the timestamps match the
   audio *given this text*" — it structurally cannot detect missing, extra,
   or wrong lines; a wrong-text LRC still aligns to plausible timestamps.
   *Consequence: timing verification and content verification are separate
   signals; content detection is deferred to a later phase with its own
   signal.*

5. **The TTS round-trip quality scorer cannot run in the service.** It needs
   `mlx-audio` (Apple-Silicon-only) and its dependency set conflicts with the
   aligner's pinned `transformers` version. *Consequence: the service-side
   signal is forced-alignment diff; the TTS scorer stays an optional
   host-side cross-check on Mac hardware, outside the service.*

6. **A known-bad set already exists.** The lyrics feedback queue
   (ADR-0007: advisory, never mutates pipeline state) carries user reports
   with reasons: `timing`, `missing`, `wrong_text`, `other`.
   `sow-admin lyrics feedback list --rating poor --reason timing --format ids`
   yields confirmed-bad-timing songs — a ready-made negative calibration set
   and a routing signal for content fixes.

7. **Forced alignment caps audio at 300s.** Enforced both in the CLI and the
   service. *Consequence: over-cap songs are skipped and reported in v1; the
   chunked-alignment POC is only ported if the skipped population proves
   large.*

8. **Gap placeholders are real LRC lines** (ADR-0008: intentionally blank,
   timestamped lines inserted post-LLM). *Consequence: any future content
   diff must tolerate empty-text lines, line splits/merges, and placeholder
   insertion; the existing fuzzy line matching in the structured-lyrics
   aligner is the prior art.*

## Solution

A phased automation, each phase shippable and useful on its own:

### Phase 1 (tickets #236–#239): timing verification, report-only

- The forced-alignment job gains `output_mode: canonical | staging`. Staging
  writes `{hash_prefix}/lyrics.candidate.lrc` and never touches the canonical
  object. (#236)
- New `sow-admin lyrics verify` command: for each target song, submit a
  staging-mode alignment job, download candidate + current LRC, compute a
  per-line timing diff (|stored − aligned|) plus the existing structural
  heuristics (monotonicity, duplicate timestamps, duration sanity), and emit
  a JSON run report + console summary. Verdicts use operator-supplied
  `--tolerance-seconds` / `--max-fail-ratio`; no baked-in defaults until
  calibration. (#237)
- Batch surface: `--stdin`, `--all-review`, `--visibility` scope selection
  and manifest-tracked resume, so an interrupted batch re-polls without
  resubmitting. (#238)
- `--format pass-ids` prints PASS song IDs pipeable into
  `audio set-visibility --status published --stdin` — publishing stays a
  deliberate human act, one command away. (#239)

**Report-only invariant:** Phase 1 performs zero writes to the canonical LRC,
catalog status, provenance, or visibility. Every write in the system remains
a deliberate human command. This is what makes it safe to run the verifier
over the whole catalog (including the published set) during calibration.

### Phase 2 (ticket #240): calibration

Two truth sets bound the verifier's error rates:

- **Positive (known-good):** the maintainer's hand-verified published songs —
  an explicit ID list, because of constraint 1.
- **Negative (known-bad timing):** the feedback queue's open `timing`
  reports, filtered to drop songs whose LRC changed after the report (stale
  negatives).

Delta distributions over both sets pick the threshold defaults, which are
then written into the command and this spec's successor.

### Phase 3 (future): fixing

- **Timing fixes:** commit the staging candidate (R2 copy to canonical +
  catalog update with `lrc_source='forced_alignment'`, *preserving the
  recording's current visibility* — the catalog update auto-promotes NULL to
  `published`, so preservation must be explicit). A `FIXED` song stays in
  `review` for human spot-check against the report; it never auto-publishes.
- **Content fixes:** songs with `missing`/`wrong_text` feedback bypass
  verification entirely (aligning wrong text is meaningless) and route to
  regeneration (`lyrics generate --force`). This routing already works today
  via the feedback queue.

### Phase 4 (future): content-aware verification and auto-publish

Add a content signal alongside the timing diff — nominal-lyrics comparison
with fuzzy line matching (tolerant of ADR-0008 placeholders and line
splits/merges), and/or aligner coverage stats (vocal spans with no aligned
line). Only a **double PASS (timing + content)** earns auto-publish; until
then PASS stays `review` and the pipeable pass-ids keep publishing one
command away. The host-side TTS scorer may join as an optional deep-check
stage on Mac hardware, never in the service container.

### How this achieves the goal

- **Detecting timing issues:** forced-alignment diff against staged
  candidates — the direct measurement of the quantity in question.
- **Detecting content issues:** feedback-queue routing (now), content signals
  in the verify path (Phase 4).
- **Fixing:** candidates already computed by verification; committing them is
  a copy + catalog update away (Phase 3), with visibility preserved and
  human spot-check before publish.
- **Trust:** two-sided calibration bounds both false-rejects (annoyance) and
  false-accepts (bad LRC reaching the congregation) before any verdict is
  acted on, and every automated step short of publish leaves an auditable
  JSON report.

## User Stories

1. As a worship-tech admin, I want to verify the timing of every `review` song in one command, so that I don't hand-check each LRC.
2. As an admin, I want verification to never modify the live LRC, so that a buggy verifier can't corrupt published lyrics.
3. As an admin, I want verification to never change catalog status or visibility, so that pipeline state stays under my control.
4. As an admin, I want a per-line timing diff in a JSON report, so that I can audit exactly which lines disagree and by how much.
5. As an admin, I want structural problems (non-monotonic, duplicate, duration-insane timestamps) flagged without running ML, so that obvious garbage is caught cheaply.
6. As an admin, I want to pipe review-song IDs from `audio list` into `lyrics verify`, so that batch verification composes with existing tooling.
7. As an admin, I want to verify the whole review queue with `--all-review`, so that backlog sweeps are one command.
8. As an admin, I want to verify the published set with `--visibility published`, so that I can calibrate thresholds against known-good songs.
9. As an admin, I want an interrupted batch to resume without resubmitting jobs, so that long runs survive network/laptop failures.
10. As an admin, I want over-300s songs skipped with a reason in the report, so that I can see the long-song population and decide whether to port chunked alignment.
11. As an admin, I want pass-IDs output pipeable into `set-visibility`, so that publishing verified songs is one deliberate command.
12. As an admin, I want FAIL songs to stay in `review`, so that nothing unverified reaches the congregation.
13. As an admin, I want songs with `missing`/`wrong_text` feedback routed to regeneration instead of verification, so that we don't waste aligner jobs on wrong text.
14. As an admin, I want a known-bad negative set from the feedback queue, so that calibration measures the verifier's catch rate, not just its false-reject rate.
15. As an admin, I want threshold defaults chosen from measured distributions, so that verdicts are evidence-based rather than guessed.
16. As an admin, I want candidate LRCs preserved in R2 for inspection, so that a fix is reviewable before it's committed.
17. As an admin, I want a committed fix to preserve the song's current visibility, so that fixing never silently publishes.
18. As an admin, I want a fixed song to stay in `review` for spot-check, so that machine-written LRCs get one human glance before publishing.
19. As a congregation member, I want lyrics on screen at the right time, so that I can sing along without confusion.
20. As a congregation member, I want missing or wrong lyric lines fixed, so that the projection matches what is sung.

## Implementation Decisions

- **Service change (analysis-service):** forced-alignment job request options
  gain `output_mode` (`canonical` | `staging`, default `canonical`). The
  worker uploads to the staging key `{hash_prefix}/lyrics.candidate.lrc` when
  in staging mode; existing ETag stale-object protection and timestamped
  backups apply to whichever key is written. Default behavior unchanged.
- **CLI addition (admin-cli):** new `lyrics verify` command in the lyrics
  command group. Orchestrates staging-mode job submission via the existing
  analysis-service HTTP client, polling, and R2 downloads. The diff/verdict
  math is pure Python on two parsed LRC structures — it lives in the CLI, not
  the service (the service's job is ML; comparison is not).
- **No new job type:** a dedicated `lrc_verify` job was considered and
  rejected — it duplicates the entire forced-alignment pipeline for a
  one-flag difference.
- **No DB schema changes:** verdicts and scores live in the JSON run report,
  not new columns (visibility is free text across webapp + admin schemas;
  adding quality columns is churn across three components for data the report
  already carries).
- **No `lrc_status` tracking for verify jobs:** the report-only invariant
  forbids catalog mutation; job tracking is manifest-local, following the
  existing LRC batch manifest/resume pattern minus its DB writes.
- **Thresholds are CLI options, not constants**, until calibration (ticket
  #240) picks defaults from measured distributions.
- **Heuristics reuse:** monotonicity, duplicate-timestamp, and duration-sanity
  checks reuse the existing interactive-editor validation logic rather than
  establishing a second convention.
- **Commit mechanics (Phase 3):** reuse the existing R2 upload path (ETag
  protection + backups) and catalog update, passing the recording's current
  visibility explicitly to defeat the NULL→`published` auto-promotion.
- **Fix routing:** feedback reason drives the path — `timing` →
  verify/align-fix; `missing`/`wrong_text` → regenerate; `other` → human.
- **Content blindness is documented, not patched:** Phase 1 cannot see content
  errors (constraint 4); this is stated in the command help and report header
  so operators don't over-trust a PASS.

## Testing Decisions

Good tests exercise external behavior at existing seams, not internals:

- **Service seam — the job worker:** tests drive the forced-alignment worker
  with a fake aligner and fake R2 client; assert that staging mode writes the
  candidate key and leaves the canonical object's ETag unchanged, and that
  canonical mode is byte-for-byte the old behavior. Prior art: existing
  analysis-service worker tests.
- **CLI seam — the command:** tests drive `lyrics verify` with a fake
  analysis-service client and fake R2/DB clients; assert the report contents,
  the skip behavior for over-cap songs, and the zero-write invariant
  (canonical LRC, status, provenance, visibility provably unchanged). Prior
  art: existing admin-cli command tests with faked clients.
- **Pure seam — the diff/verdict engine:** unit tests over parsed LRC pairs:
  boundary deltas at the tolerance, non-monotonic input, duplicate
  timestamps, duration sanity, empty/placeholder lines. Prior art: editor
  validation tests.
- **The pipe contract:** `--format pass-ids` emits exactly the PASS IDs on
  stdout (no decoration), and composes with `set-visibility --stdin`. Prior
  art: `audio list --format ids` conventions.

Deliberately not tested: that the aligner produces "good" timestamps (model
quality is calibration's concern, not a unit test's), and wiring echoes
between client and service request models.

## Out of Scope

- **v1 auto-fix commits** (Phase 3) and **content signals** (Phase 4) — the
  seams are designed for them, but they are not part of tickets #236–#240.
- **Auto-publish on PASS** — requires a content signal and calibration
  evidence; until then publishing is a human command aided by pass-ids.
- **Porting the TTS round-trip scorer off MLX** — new dependency stack and
  container work for an unproven marginal signal.
- **Chunked alignment for >300s songs** — POC-grade today; revisit when the
  skip reports show the population size.
- **Webapp, Android, and render-worker changes** — none required; visibility
  semantics and R2 layout are unchanged from their perspective.
- **New visibility statuses** (e.g. `needs-manual-review`) — `hold` already
  exists and suffices.
- **Resolving ADR-0007's manual feedback triage** — feedback stays advisory;
  this automation reads the queue but never resolves it.

## Further Notes

- The manual fixing loop this replaces is documented in
  `docs/agent_instructions-fix-lrc.md`; that doc remains the fallback for
  songs the automation flags as hard failures.
- Authoritative pipeline reference: `docs/lrc-job-flow.md`.
- Relevant ADRs: 0007 (feedback is advisory), 0008 (gap placeholders —
  content-diff tolerance requirement).
- The `lyrics.candidate.lrc` key is overwritten by each new staging job for
  the same song; it is an inspection artifact, not a versioned history —
  canonical backups already provide history.
