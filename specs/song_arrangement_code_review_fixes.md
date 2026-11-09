# PR #289 Fix Plan — Song Arrangement Editor Review Findings

## Context

PR #289 (mhuang74/stream_of_worship) was reviewed by 16 reviewer agents. This plan implements the confirmed fixes: 4 P1 bugs, selected P2 UX/correctness bugs, and P3 cleanups (one review finding dropped as a false positive after verifying drizzle-orm 0.45.2 `mapUpdateSet` drops `undefined`; one claimed P1 — the webapp-vs-worker 1500 s admission basis — dropped after the worker was checked: it already caps the laid-out timeline, so only the spec sentence needed rewording; see step 2). All work is in `delivery/render-worker/` and `delivery/webapp/`. No Android/marketing changes.

Python 3.11, Black/ruff line 100 for worker; webapp tests via Vitest. Repo rule: work is complete only after `git push` succeeds.

## Approach

Order: worker fix (independent) → persistence core (transaction + metrics contract) → render admission route → preview transport → preview hook → editor sheet/client → doc/test cleanups → full verify + push. Step 1 is independent of 2–8; steps 2 and 3 both touch the shared metrics contract, so persistence lands first.

### 1. Worker: `OverflowError` → clean `ValueError` in `arrangements.py`

- `delivery/render-worker/src/sow_render_worker/arrangements.py`, `_is_number` (~L77): wrap the numeric check so a JSON int too large for `float` returns `False` instead of raising `OverflowError`:
  ```python
  try:
      return (
          isinstance(value, (int, float))
          and not isinstance(value, bool)
          and math.isfinite(float(value))
      )
  except (OverflowError, ValueError):
      return False
  ```
  Callers (`_is_integral_number`, `_is_valid_step_payload`, `validate_arrangement`, `_round_seconds` region) then reject the value through the existing `ValueError(PARSE_FAILURE_MESSAGE)` path — no other change.
- Add to `delivery/render-worker/tests/test_arrangements.py`:
  ```python
  def test_parse_song_arrangement_rejects_huge_integer_section_index() -> None:
      big = json.loads("[1" + "0" * 400 + "]")[0]
      step = {"sectionIndex": big, "label": "a", "startSeconds": 0, "endSeconds": 1}
      with pytest.raises(ValueError, match="repair the Recording analysis or reset"):
          validate_arrangement(json.dumps({"version": 1, "steps": [step]}), None, None, None)
  ```
- Also delete the dead `_make_step()` helper at `tests/test_pipeline.py:51-57` and its now-unused `ArrangementStep` import (sole consumer).

### 2. Admission cap: one timeline definition on both sides (resolved by wording, not by rewriting admission)

Review claimed a mismatch: spec said "1500 seconds of selected audio, excluding inter-item gaps/crossfades" and the webapp compared the laid-out total (`getSongsetDurationSeconds`, which nets out crossfade overlap), so 800 s + 800 s with a 100 s crossfade passed webapp save and then failed in the worker.

Verification: the worker does NOT sum raw selected audio. `pipeline.py:340` computes `calculate_total_duration(items, asset_fetcher)` — "the last item's end on the arranged timeline", mirroring `generate_songset_audio` — and compares THAT against `MAX_SONGSET_DURATION_SECONDS` (the raw sum at `pipeline.py:342` is only the `<= 0` fallback). Webapp admission and worker admission already agreed; only the spec sentence disagreed with both.

- Resolution: align the spec with the code both sides already implement — `specs/song_arrangement_editor.md` §1 bullet and §2.2/§2.7 now state the cap is 1500 s on the actual laid-out timeline (gaps included, crossfade overlap subtracted). No admission logic changed.
- `getSongsetRenderMetrics.songsetDurationSeconds` keeps the laid-out definition; its comment now says exactly that (unknown durations contribute 0; null when every relevant source duration is unknown), and `updateSongArrangement`'s doc says "laid-out timeline total" instead of "selected-audio limit".
- `arrangement-persistence.test.ts` keeps its laid-out-timeline admission expectations and adds the boundary/unknown-duration cases against that same definition.

### 3. Webapp persistence: transactional save + recording-swap existence check

- `updateSongArrangement` (`src/lib/db/songsets.ts` ~L1567–1600) and `updateSongsetItem` (~L1439–1462): wrap sibling read → validation → item UPDATE → `songsets.updatedAt` UPDATE in one transaction. Implemented via the new `src/db/mutation-db.ts` client (`neon-serverless` Pool + `drizzle-orm/neon-serverless`), because the read path's `@/db` is `neon-http`, whose session rejects `transaction()`. The first statement is `select ... from songsets where id = ? and user_id = ? for update`, so two concurrent saves for one Songset serialize on the parent row: the loser re-reads the winner's commit and is rejected by admission instead of both passing against one snapshot. The staleness bump stays atomic with the item write.
- Proof: unit tests assert the statement order inside the transaction double; a throwaway live-Neon run (two concurrent `updateSongArrangement` calls on a temp Songset, 810 s each alone vs 1600 s serialized) produced exactly one commit and one `SONGSET_TOO_LONG`, then deleted the fixture.
- `updateSongsetItem`: when `recordingHashPrefix` changes, first verify the target exists before clearing the arrangement:
  ```ts
  const rec = await tx.select({ hashPrefix: recordings.hashPrefix })
    .from(recordings)
    .where(eq(recordings.hashPrefix, recordingHashPrefix))
    .limit(1);
  if (rec.length === 0) return null; // route already maps null → 404
  updateSet.arrangementSteps = null;
  ```
- Do NOT add an undefined-filter to `updateSet`: drizzle-orm 0.45.2 `mapUpdateSet` already strips `undefined` entries (verified at `node_modules/drizzle-orm/utils.js:84`).
- Regression test in `arrangement-persistence.test.ts`: swapping an item to a nonexistent hash returns null (→ 404) and leaves `arrangementSteps` untouched.

### 4. Render admission route: map re-validation race to 409

- `src/app/api/render-jobs/route.ts` (~L118–127): the route validates via `getSongsetRenderMetrics`, then `createRenderJob` re-validates; if the arrangement goes stale between the two reads, the typed error falls to the outer catch → 500. In the inner `catch` around `createRenderJob`, before the unique-constraint branch:
  ```ts
  if (
    err instanceof SongsetRenderValidationError &&
    err.code !== "SONGSET_NOT_FOUND"
  ) {
    return NextResponse.json(
      { error: ARRANGEMENT_UNAVAILABLE_OR_STALE_MESSAGE, code: err.code },
      { status: 409 }
    );
  }
  ```
  `SongsetRenderValidationError` is already exported from `src/lib/render/job-manager.ts` and imported by the route's test.
- Test in `src/test/api/render-jobs/route.test.ts`: mock `createRenderJob` to reject with `new SongsetRenderValidationError("ARRANGEMENT_STALE", ARRANGEMENT_UNAVAILABLE_OR_STALE_MESSAGE)` → expect 409 + code.

### 5. Preview transport (`src/lib/audio/arrangement-preview.ts`): fallback + position fixes

- Fallback mode must produce hard crossfade boundaries ("Crossfades become hard boundaries" docstring is currently false). In `applyElementsAt` (~L455–487), shorten the predecessor's active window by the successor's crossfade in fallback mode:
  ```ts
  const successorCrossfade = fallbackMode
    ? records[record.itemIndex + 1]?.item.crossfadeSeconds ?? 0
    : 0;
  const itemEnd = item.startSeconds + item.durationSeconds - successorCrossfade;
  const active = position >= item.startSeconds && position < itemEnd;
  ```
- Give every graph-routed record its own RMS tap (`wireGraph` ~L593–604): replace the single `analyserAssigned` tap with `sourceNode → gain → analyser` (analyser sink only) per record, so detection works regardless of which record is sounding.
- Silent-output fallback detection (~L598–616): treat a paused OR still-buffering element as inconclusive instead of "silent" — `if (first.audio.paused || first.audio.readyState < 2) return;` — and re-arm `armRmsCheck()` in `applyElementsAt` whenever a graph-routed element is started (so a check that sampled a paused record retries at the next element start). Select the sampled record as the first graph-routed record that is actually sounding, not hardcoded index 0.
- `failPlayback` (~L557–563): record the current position before switching to error state (`pausedPositionSeconds = getTimelinePositionSeconds()`), so a retry resumes from where playback actually stopped.
- Extend `src/test/lib/audio/arrangement-preview.test.ts`: fallback mode stops the predecessor at the successor's start (no full-volume overlap); buffering element (readyState 0/1) does not trigger fallback; failPlayback mid-timeline then retry resumes from the failure position.

### 6. Preview hook (`src/hooks/useArrangementPreview.ts`): phantom state + unhandled rejection

- Teardown must reset state, not just destroy resources (~L336–348): in both teardown effects, after `teardown()`:
  ```ts
  setState((previous) =>
    previous.key === itemsKey && previous.status !== "idle"
      ? idleState(itemsKey)
      : previous
  );
  ```
  Fixes the sheet reopening with the same `itemsKey` re-rendering a dead `"playing"` state that pause cannot dismiss.
- Wrap `await currentSession.play()` (~L233–235) in try/catch that reports through the existing `onPreviewErrorRef` channel and commits `status: "error"` (the transport can reject before its internal handler runs, e.g. Web-Audio-unsupported browsers), leaving no eternal `"loading"`.
- Extend `src/test/hooks/useArrangementPreview.test.tsx`: `active` false→true with unchanged items resets status to idle; a rejecting `session.play()` surfaces the error state instead of staying loading.

### 7. Editor sheet + client UX

- `src/components/songset/ArrangementEditorSheet.tsx`:
  - `validateDraftProblem` (~L224–240): return `null` when `draft.length === 0 || draft.length > MAX_ARRANGEMENT_STEPS` — the >64 case already has the amber too-many-error + disabled Save/Preview; the destructive "saved arrangement is invalid or stale" alert must not appear for a live over-limit draft.
  - `handleStartAt` (~L507–514): clear `previewError` before `preview.startAt(itemId)`.
  - `handleRequestClose` (~L431–445): ignore close requests while `isSaving`; disable draft mutations (drag/duplicate/remove) and Play during save so a successful save can't silently discard a mid-save edit.
- `src/app/songsets/[id]/SongsetEditorClient.tsx`:
  - `handleSaveArrangement` (~L593–605): replace the closure-snapshot `setItems(nextItems)` with a functional update (`setItems((prev) => prev.map(...))`, capture `nextItems` inside the updater for the `setSongset` duration recompute) so concurrent item/transition edits made during the in-flight PUT are preserved (matches `handleAddItem`'s existing convention).
  - Wrap the `await fetch(...)` (~L544–551) in try/catch that throws `new Error(t("arrangement.error.saveFailed"))` so offline/network failures show the translated message instead of raw `TypeError: Failed to fetch`.
- Tests: `src/test/components/songset/ArrangementEditorSheet.test.tsx` — add the negative assertion to the exceeds-64-steps test that the destructive invalid-arrangement alert text is NOT present, and a test that Start-At clears a prior preview error.

### 8. Webapp P3 cleanups

- `src/lib/arrangements.ts` `isValidStep` (~L293–299): label length in code points, not UTF-16 units — `isNonEmptyString(step.label) && [...step.label].length <= 100`, aligning with the Python mirror.
- Fix the dangling spec path `specs/song_arrangement_editor_v2.md` → `specs/song_arrangement_editor.md` in five comments: `src/lib/arrangements.ts:2`, `src/hooks/useArrangementPreview.ts:5`, `src/lib/audio/arrangement-preview.ts:3`, `src/lib/i18n/messages/arrangement.ts:5`, and the render-worker `src/sow_render_worker/arrangements.py:5`.
- `specs/song_arrangement_editor.md` title line → `# Song Arrangement Editor — issue #254 (implemented via #282–#287)`.

## Critical files & anchors

- `delivery/render-worker/src/sow_render_worker/arrangements.py` — `_is_number` guard; step 1.
- `delivery/webapp/src/lib/db/songsets.ts` — `updateSongsetItem` (~L1439), `updateSongArrangement` (~L1567), `getSongsetRenderMetrics` (~L1714); steps 2–3.
- `delivery/webapp/src/app/api/render-jobs/route.ts` — inner catch around `createRenderJob` (~L118); step 4.
- `delivery/webapp/src/lib/audio/arrangement-preview.ts` — `applyElementsAt`/`wireGraph`/RMS check/failPlayback (~L455–616); step 5.
- `delivery/webapp/src/hooks/useArrangementPreview.ts` — teardown effects (~L336) and `play()` (~L233); step 6.

## Verification

1. Worker: `cd delivery/render-worker && uv run --extra dev pytest tests/test_arrangements.py -v` — the new huge-int test passes (raises `ValueError` with the repair-or-reset message, not `OverflowError`); then `uv run --extra dev pytest tests/ -v` all green.
2. Webapp: `cd delivery/webapp && pnpm test` all green (2930 passed at the time of writing), including:
   - laid-out-timeline admission cases (1501 s rejected, exactly-1500 s across a crossfade admitted) and the concurrent-save serialization case;
   - 409 mapping test for the re-validation race, including `createRenderJob` not being called after the admission read rejects;
   - fallback hard-boundary and buffering-inconclusive preview tests, plus close→reopen idle reset;
   - sheet over-limit negative assertion (amber counter, no destructive alert) and the code-point label tests (lib parser and route boundary: 100 emoji accepted, 101 rejected).
3. New-behavior proof (throwaway, no repo file changes): from `delivery/webapp`, run `pnpm exec vitest run src/test/lib/arrangements.test.ts src/test/api/songsets/arrangement-persistence.test.ts src/test/api/render-jobs/route.test.ts` and observe the previously-failing scenarios now pass.
4. Live-Neon transaction proof (throwaway script, deleted after the run): seed a temp Songset with two recordings that each admit 810 s alone, fire two concurrent `updateSongArrangement` calls, assert exactly one commit and one `SONGSET_TOO_LONG`, then delete the fixture. Observed: `{fulfilled: 1, rejected: ["SONGSET_TOO_LONG …"]}`, winner's row written, loser's row unchanged.
5. Browser smoke (dev server + headless Chrome): sheet opens prefilled, preview plays with an advancing position, close→reopen shows idle at 0 s, dirty Cancel raises the discard dialog, Save writes the canonical JSON and bumps both timestamps, reload shows the Arranged badge, Reset clears the row.
6. `pnpm typecheck && pnpm lint` clean (0 errors).
7. `git pull --rebase && git push && git status` shows "up to date with origin".
