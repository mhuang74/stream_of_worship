# Handover — Glue on the songset editor

Written 2026-11-23 08:45 +0800 (handover snapshot). Branch `sow_transition_inprint_elevenlabs`, HEAD `aa377018`, `origin/sow_transition_inprint_elevenlabs` level with it (0 commits ahead), working tree clean.

## Read this first

- The working tree is clean. Everything this document describes as "dirty" was committed while the document was being written: `1a7b8d06` (duplicate-item/editor fix), `1574eed2` (quota/lease fix), `aa377018` (graphify refresh). Re-implementing either fix is wasted work.
- This document is committed and pushed on its own, not bundled with the open work below. Step 6 therefore commits only the code; do not expect to find uncommitted doc changes.
- The tree may have advanced since this snapshot. Treat `git log` and `git diff` as the source of truth and this document as the map.

## Merge audit — done, do not redo from scratch

Method: `git diff 81543f48..HEAD -- . ':(exclude)graphify-out'` returns one file, `delivery/render-worker/tests/test_frame_renderer.py` (fixture lyrics keyed by Item id in `c3a57206`). That merge commit `81543f48` (`Merge origin/main into the Glue branch`) is the tree CI verified green. The rebase onto `6d8cf8d4` (origin/main) dropped the merge-only conflict resolutions; `073f8359` restored the merge tree for the eight affected files.

Eighteen files overlapped the rebase. Each was compared three ways — merge commit vs each parent, and HEAD vs merge commit. Every branch-only line that disappeared was a main-side API rename the merge adopted, with callers updated in the same tree:

- `remap_lrc_to_clips(item_id = …)` replaced its `song_title` argument;
- `GlobalLRCLine`'s fourth positional argument is `item_id`, not `title`;
- `group_lyrics_by_item` replaced `group_lyrics_by_song`;
- `songs: [{ id, title }]` replaced `songTitles`;
- `addSongsetItem` no longer takes `position`.

Conclusion: the merge dropped no Glue feature code.

## What the merge did break — fixed, committed, tests green

Commit `1a7b8d06` `fix(glue): preserve transitions across songset item edits` (12 files, +713/−231), with design notes in `specs/fix-songset-item-duplication-glue.md`:

1. `duplicateSongsetItem` (`src/lib/db/songsets.ts`) never copied `glue_enabled`, `glue_beats`, `glue_active_take_id`. `CONTEXT.md:56` and `specs/inpaint-transition-elevenlabs-v2.md`'s duplicate rule both require the copy. It now copies all three under the existing row lock (keeping a same-owner take pointer even when it currently resolves stale), re-resolves the copied pointer against the new successor, projects order and duration with effective takes, and returns `409 SONGSET_TOO_LONG` before writing.
2. `handleUpdateGlue` (`src/app/songsets/[id]/SongsetEditorClient.tsx`) wrote rows through `setItems` without syncing `itemsRef`, so a queued duplicate/append/reorder reverted the selection that had just been saved. Every item mutation now runs through `runItemMutation` and lands via `commitItemLayout(itemsRef.current.map(…))`; structural writes then refresh from `GET /api/songsets/:id` through `refreshItemLayout`, with retry toast key `songsets.error.itemRefreshFailed`.
3. Shared derivations replaced the divergent copies: `glueTakeSelectionError` (`src/lib/glue/resolve.ts`), `normalizeGlueBeats` (`src/lib/glue/quote.ts`), `glueForAdjacency` (`src/lib/glue/draft.ts`), `commitItemLayout` (duration recomputed with adjacent-take awareness).

Tests landed with it: `duplicate-item.test.ts` (8 cases incl. the cap), `duplicate-item-route.test.ts`, `ArrangementEditorSheet.test.tsx`, `SongsetEditorClient.test.tsx` (queue/refresh/retry cases).

## Quota/lease fix — already committed as `1574eed2`

Commit `1574eed2` `fix(glue): clear resume heartbeat and preserve consumed quota` (8 files, +3397/−5):

- `src/db/schema.ts`, `drizzle/0029_preserve_glue_quota_on_delete.sql`, `drizzle/meta/0029_snapshot.json`, `drizzle/meta/_journal.json`: `glue_paid_attempts.glue_job_id` becomes nullable with `ON DELETE SET NULL`. Deleting an Item, Songset or job used to cascade the consumed attempt rows away, refunding the rolling 24-hour quota. Nothing else depends on the FK: the only `glue_paid_attempts` updates by `glue_job_id` (`src/lib/glue/jobs.ts:648`, `:689`) run inside transactions where the job still exists, and `readGlueQuota` (`src/lib/glue/quota.ts:46-56`) filters `user_id`/`state`/`consumed_at` only.
- `src/lib/glue/jobs.ts` `prepareResume` (line ~802): clears `heartbeat_at` and guards the UPDATE on `phase = job.phase AND attempt_id = job.attemptId`. Without the clear, the worker's claim query (`delivery/render-worker/src/sow_render_worker/db.py:741-756`, stale window `GLUE_CLAIM_STALE_SECONDS = 120` at `db.py:118`) sees a heartbeat younger than 120 s and returns `claimed_now=False`, stalling the resumed job. Without the guard, two concurrent resumes both resolve `prepared` and the second overwrites the first attempt's id.
- `src/test/db/schema.test.ts`: FK expectation updated to nullable + `set null`.
- `src/test/integration/postgres-glue-jobs.smoke.test.ts`: asserts the heartbeat clear on `prepareResume` and quota preservation across the three delete paths (outgoing Item, incoming Item, Songset). Currently one case ("clears a recent heartbeat when re-queuing a free failure"); the retry and dual-resume cases below are still missing.
- `specs/inpaint-transition-elevenlabs-v2.md`: `glue_paid_attempts` schema block updated.

## Open work, in order

1. `prepareRetryProcessing` (`src/lib/glue/jobs.ts:758`) carries both defects that `prepareResume` just had fixed: it re-arms a failed job with a fresh `attemptId` but leaves the failed attempt's `heartbeat_at`, and it does not guard on the row it read. A user who retries processing within 120 s of a failure gets a job the worker will not claim. Add `heartbeatAt: null` and `eq(glueJobs.phase, job.phase)` / `eq(glueJobs.attemptId, job.attemptId)` exactly as `prepareResume` does, with the same one-line comment. This is an inference from the claim SQL — confirm it with the smoke case in step 2 rather than by reasoning alone. `prepareRedispatch` (line ~709) already guards `phase = "dispatch-failed"` and needs no change.

2. Extend `src/test/integration/postgres-glue-jobs.smoke.test.ts`:
   - `prepareRetryProcessing` on the `insertFailedJob` row resolves `{ kind: "prepared", job: { heartbeatAt: null } }`;
   - two `prepareResume` calls with different attempt ids on the same failed job: the first resolves `prepared`, the second resolves `{ kind: "not-resumable" }`;
   - leave the three quota cases untouched.

3. Migrate the smoke database before running it. From `delivery/webapp`, with a TCP Postgres reachable: `SOW_DATABASE_URL=postgresql://sow:sow@localhost:5432/sow_test?sslmode=disable pnpm exec drizzle-kit migrate`, then `SOW_DATABASE_URL=… pnpm test:postgres-smoke` — the same commands CI uses (`.github/workflows/ci.yml:60-62`).

4. Known flake, not an app bug: `src/test/components/songset/SongsetEditorClient.test.tsx` → "retains a Glue PATCH while an append and reorder wait in the queue" failed 1 of 8 standalone runs (iteration 6) and once inside a four-file parallel run, always with `TestingLibraryElementError: Unable to find an element by: [data-testid="arrangement-sheet"]` from `openArrangementSheet()` (`SongsetEditorClient.test.tsx:253-256`). `ArrangementEditorSheet` is a `next/dynamic` import (`SongsetEditorClient.tsx:53`), so the first mount waits on a module load, and the helper's `findByTestId` uses the default 1000 ms. Fix the helper: `await screen.findByTestId("arrangement-sheet", undefined, { timeout: 5_000 })`.

5. Gates, from `delivery/webapp`: `pnpm typecheck`, `pnpm test`, `pnpm lint`, `pnpm test:postgres-smoke`; then `cd delivery/render-worker && uv run --extra dev pytest tests/ -v` (worker code is untouched; its claim contract is what the heartbeat fix serves).

6. Commit the remaining work — suggested subject `fix(glue): re-arm retry leases` — then `graphify update .`, then `git pull --rebase && git push`. AGENTS.md makes the push mandatory. This handover document is already committed and pushed; step 6 covers code only.
