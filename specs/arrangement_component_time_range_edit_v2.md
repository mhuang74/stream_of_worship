# Arrangement Editor v2: reviewed bounds editing + three-pass transition preview

**Status:** approved specification, not yet implemented. This is the implementation
contract for a separately authorized task.

**Supersedes:** `specs/arrangement_component_time_range_edit.md`. That document is
left unmodified and was never implemented (no `manualBounds` / `manual_bounds`
symbol exists anywhere in `delivery/`), so no migration path from a shipped v1
behaviour is required.

## Context

Revise the arrangement-component time-range work in place of v1. Let the current
editor add an Intro Step inferred from the first LRC line, and persist
user-corrected bounds on the Songset Item only with explicit Apply/Cancel review.
Replace the anonymous, single-component preview with adjacent-component
transition review played three passes total, plus optional previous-song context
and a clearly labelled next-song Opening tail.

The existing whole-Songset Web Audio transport already seeks, plays across item
boundaries, and reapplies crossfade gains after a seek. Reuse it; range limits and
the three-pass counter belong to `ArrangementEditorSheet`, not a second audio
engine.

## Approach

### 1. Accept inferred Intro and manual bounds in the Songset-only contract

1. In `delivery/webapp/src/lib/arrangements.ts`, extend `ArrangementStep` with:
   ```ts
   /** Null only for a Songset-local LRC-inferred Intro; detected sections use
    * their zero-based Recording section index. */
   sectionIndex: number | null;
   /** Required for inferred Intro and user-adjusted bounds; skips only
    * beat-snap equality, never range validation. */
   manualBounds?: true;
   ```
   Reserve the exact shape
   `{sectionIndex:null,label:"intro",startSeconds:0,endSeconds:firstLrcTime,manualBounds:true}`
   for the editor-generated Intro. After its bounds are edited, only
   `endSeconds` may differ from the inferred timestamp; Start remains fixed at
   `0:00.0` with no stepper or editable field for a null-index Intro.
   `TimelineClip.sectionIndex` also becomes `number | null`; the untouched
   full-Recording synthetic clip remains `-1` and is never confused with an
   Intro Step.
2. Change `isValidStep` to accept exactly `{sectionIndex,label,startSeconds,endSeconds}`
   or those keys plus `manualBounds`. When present, require literal
   `manualBounds === true`; reject `false`, `1`, `"true"`, and every other
   additional key. Accept nonnegative integer indices or `null`; for `null`
   require `label === "intro"`, `startSeconds === 0`, and `manualBounds === true`.
   Preserve all other label, finite-number, and `0 <= start < end` checks.
   `parseSongArrangement` already preserves optional fields through its object
   spread.
3. In `validateSongArrangement`, handle `sectionIndex === null` first: require the
   structural rules above and the universal `endSeconds <= durationSeconds`; do
   not consult detected sections or re-fetch/revalidate LRC at save/render. The
   persisted Songset Item owns this explicit cut, so later lyric changes do not
   silently rewrite or invalidate it. For nonnegative indices retain
   `sectionIndex`/`label` checks, skipping only snapped-bound equality when
   `manualBounds === true`. Keep all range checks unconditional. Update comments to
   distinguish Songset-local Intro/manual cuts from unflagged stale Steps.
4. In the arrangement API route, change the schema's index to
   `z.number().int().nonnegative().nullable()` and add
   `manualBounds: z.literal(true).optional()`. Keep `z.strictObject`; the
   cross-field Intro/manual rules remain in `validateSongArrangement`, which the
   update path already calls. No DB migration or Analysis Service schema change is
   needed.
5. Mirror the contract in `delivery/render-worker/src/sow_render_worker/arrangements.py`:
   - use `section_index: int | None` and trailing `manual_bounds: bool = False`;
   - accept exactly the base key set or base keys plus `manualBounds`, requiring
     literal `True` when present;
   - parse `None` without `int(...)` and set
     `manual_bounds=payload.get("manualBounds") is True`;
   - validate a null-index Step only as exact label `intro`, start `0`, manual
     flag, and positive in-range end; retain detected index/label checks for other
     Steps and skip only their snapped-bound equality when manual.
6. `buildSnappedSteps` never emits null indices or the flag. Timeline, audio
   slicing, LRC remapping, chapters, and video continue consuming source bounds
   directly. Do not write the inferred Intro to `recordings.sections`, beats,
   SongComponent, or any Analysis Service data.

### 2. Offer the LRC-inferred Intro in the current song's component picker

1. Reuse the existing `useSongLyrics(item.recording?.contentHash)` result and
   `parseLRC`; no second lyrics request is needed. Derive `firstLrcTimeSeconds`
   from the first parsed line whose `text.trim().length > 0`; blank-text
   instrumental gap markers are not a sung first lyric and must not truncate the
   inferred Intro. Offer **Intro (inferred from lyrics)** only when lyrics loaded
   successfully and `3 < firstLrcTimeSeconds <= recordingDurationSeconds`. The
   threshold is strict: exactly 3 seconds is excluded as likely noise.
2. Add the Intro row at the top of the existing Add Component dialog, before
   detected occurrences. Its subtitle is **0:00.0–first lyric at {timecode}** and
   its testid is `arrangement-add-inferred-intro`. While lyrics are loading show a
   disabled loading row; when unavailable/invalid, the timestamp is at most
   3 seconds, or it exceeds Recording duration, omit the row and show no error
   because detected components remain usable.
3. Adding it appends exactly
   `{sectionIndex:null,label:"intro",startSeconds:0,endSeconds:firstLrcTimeSeconds,manualBounds:true}`
   through the existing `commitDraft`, so repeat, reorder, delete, undo, preview,
   Save, and Render behave like other Steps. Display its localized name as
   **Intro (inferred)** without changing the persisted English `intro` label.
   Count repetitions with a separate nullable-index counter; never merge it with
   detected section 0.
4. Make `occurrenceByIndex`, `stepCountBySectionIndex`, `snappedByIndex`,
   `data-section-index`, and add-section callbacks null-safe. Use draft ordinal
   testids for block actions. For a null-index Step, `labelForDraftIndex` returns
   the localized inferred-Intro label and its bounds Reset is **Reset to
   inferred**, restoring the raw LRC-derived `{0,firstLrcTime}` values. Detected
   Steps retain Reset to detected. If lyrics become unavailable in a later editor
   session, an already saved Intro remains valid, editable, previewable, and
   renderable, but its Reset-to-inferred button is disabled.
5. A new untouched draft still contains detected sections only; do not
   auto-insert Intro. The user explicitly adds it. A saved arrangement containing
   it initializes through the same parser as every other Step.

### 3. Make bounds editing a temporary, reviewable transaction

Work primarily in `delivery/webapp/src/components/songset/ArrangementEditorSheet.tsx`.
Use the existing Base UI `Popover`, `Input`, and `Button` primitives; do not
introduce a Radix dependency.

1. Add editor-only constants/helpers:
   ```ts
   const BOUNDS_STEP_SECONDS = 0.1;
   const BOUNDS_AUDITION_SECONDS = 3;
   const TRANSITION_REVIEW_PASSES = 3;
   const SCRUB_CLICK_TOLERANCE_PX = 5;

   function roundToTenths(seconds: number): number;
   function formatTimecodeTenths(seconds: number): string; // m:ss.s
   function parseTimecodeTenths(value: string): number | null;
   function candidateBounds(
     step: ArrangementStep,
     startSeconds: number,
     endSeconds: number,
     manual: boolean,
   ): ArrangementStep;
   ```
   `parseTimecodeTenths` trims outer whitespace and accepts only
   `/^(\d+):([0-5]\d)\.(\d)$/`; no signed, locale-formatted, free-floating, or
   fractional-second precision beyond tenths. `formatTimecodeTenths` uses the same
   shape. `candidateBounds` preserves `sectionIndex`/`label`, sets both bounds, and
   includes `manualBounds: true` only when `manual` is true.
2. Give each Step's range a button showing `m:ss.s–m:ss.s`, a pencil, and a
   persistent visible **Edited** badge when `manualBounds === true`; color alone is
   not the marker. Use stable testids based on the draft ordinal
   (`arrangement-bounds-trigger-${index}`,
   `arrangement-bounds-popover-${index}`), not the module-global draft id.
3. Hold one sheet-level bounds session so only one editor is open:
   ```ts
   interface BoundsPreviewOverride {
     id: string;
     step: ArrangementStep;
   }
   const [boundsEditorId, setBoundsEditorId] = useState<string | null>(null);
   const [boundsPreviewOverride, setBoundsPreviewOverride] =
     useState<BoundsPreviewOverride | null>(null);
   const [boundsEditorInvalid, setBoundsEditorInvalid] = useState(false);
   ```
   The popover owns the opening Step, `startText`, `endText`, `hasLocalEdits`, and
   `resetToDetected: ArrangementStep | null`. Opening initializes both strings from
   the opening Step, sets `hasLocalEdits=false`, and sets `resetToDetected=null`.
   Opening another Step discards the previous unapplied session. Popover Escape,
   outside dismissal, Cancel, and closing the Sheet discard the temporary values
   without touching `draft` or history. Sheet-level dirty-close confirmation
   therefore remains about applied arrangement edits only.
4. On every field change, set `hasLocalEdits=true` and `resetToDetected=null`.
   Derive both numbers and one validation error immediately: malformed timecode,
   start below zero, end above Recording duration, or start not before end. Use
   `aria-invalid`, associate the error with both fields, and show it in the
   popover. Disable Apply, both audition buttons, and all steppers while either
   value is invalid. The main transport is disabled while a bounds popover is open;
   only its audition actions may play. Locking the open Step's own row must not
   reach into the popover: pass the save-time lock to the Step's toolbar and a
   separate bounds lock to the popover, or the transaction's own steppers, Apply,
   and auditions arrive disabled and the editor is unusable.
5. Each Start/End row contains an `Input` (`type="text"`, `inputMode="decimal"`,
   `autoComplete="off"`), −0.1 s and +0.1 s buttons, and an Audition button. A
   stepper uses the parsed current value, adds/subtracts `0.1`, rounds to tenths,
   updates only that field, marks local edits, and clears reset mode. Disable a
   stepper when its candidate would violate the same range rules. No keyboard
   autorepeat implementation or draft update occurs per press.
6. For a null-index Intro, show Start as a fixed `0:00.0` output and provide only
   End input/steppers/Audition. **Reset to inferred** restores the raw LRC-derived
   endpoint and retains `manualBounds:true`; unlike detected-section Reset, it never
   drops the required flag. For a detected Step, **Reset to detected** writes the
   detected range into the two temporary fields, sets `hasLocalEdits=true` and
   `resetToDetected` to the raw detected Step (without `manualBounds`), sends that
   raw Step as the preview override, and does not mutate `draft`. For an inferred
   Intro, the equivalent `resetToDetected` value is the raw inferred Step with
   `sectionIndex:null`, `label:"intro"`, and `manualBounds:true`. The raw
   six-decimal values are retained for validation even though the fields display
   tenths; rounding Reset to tenths would make an unflagged Step stale. Any later
   field/stepper change clears reset mode and resumes using parsed tenths. Reset is
   disabled only when detected bounds are unavailable.
7. Apply is disabled until `hasLocalEdits` is true and both fields validate. On
   Apply:
   - use the raw `resetToDetected` Step when non-null; otherwise build the
     candidate from parsed fields with `manual=true`;
   - close the bounds session and clear its preview override;
   - call the existing `commitDraft` exactly once with that Step replaced.

   This creates one history entry, stops active preview, and recomputes dirty state
   by comparing the new `draftKey` with `initialDraftRef`; applying a Reset that
   returns to the opening saved arrangement must clear dirty. Extend `draftKey` with
   `:${step.manualBounds ? 1 : 0}` so flag-only changes are significant. Remove the
   v1 `boundsSessionRef`/per-nudge history-session design entirely. Cancel never
   creates history or dirty state.
8. Make unapplied bounds audible without playing the old timeline. Whenever a valid
   temporary candidate changes, call the sheet with that candidate; the sheet stops
   current preview, clears `playbackWindow`, and stores it in
   `boundsPreviewOverride`. An invalid candidate stops preview, clears the
   override, and makes preview inactive until valid again. Build `previewDraft` by
   replacing the matching entry with `boundsPreviewOverride.step` when present,
   otherwise use `draft`; feed `previewDraft` to the existing `previewItems` →
   `buildArrangementTimeline` path. `isPreviewAvailable` must also require
   `!boundsEditorInvalid`; the popover reports that flag on every edit and on close
   resets it to false. Thus Audition always uses the currently displayed candidate,
   and normal transport remains disabled until Apply/Cancel.
9. Audition is a separate one-shot action, not a three-pass review. For the
   candidate's clip in the override timeline:
   - Start: play from `clip.startSeconds` through
     `min(clip.startSeconds + 3, clip.endSeconds)`.
   - End: play from `max(clip.startSeconds, clip.endSeconds - 3)` through
     `clip.endSeconds`.

   These inside-boundary snippets intentionally do not include pre-roll/out-roll or
   adjacent-song context. Set a non-looping `playbackWindow` with pass count 1 and
   use `startAtSeconds`. At its end, pause. Starting another audition replaces the
   previous window. Changing either field stops the audition before the new
   candidate becomes audible.

### 4. Select an adjacent component pair and review it exactly three times

1. Replace single-component Play behavior with pair selection:
   ```ts
   interface TransitionReviewSelection { firstClipIndex: number; }
   const [selectedPairIndex, setSelectedPairIndex] = useState(0);
   const [reviewPass, setReviewPass] = useState(1);
   const [reviewCompleted, setReviewCompleted] = useState(false);
   ```
   A pair is draft indices `i` and `i+1`; `0 <= i < draft.length - 1`. The last
   Step has no transition action. After reorder, deletion, duplication, undo, or
   Apply, stop preview and clamp the selection to
   `max(0, draft.length - 2)`; reset pass/completion. There is no arbitrary From/To
   selector, single-component full-play action, persistent loop toggle, or fourth
   automatic pass.
2. In each non-last component header, replace the existing Play/Pause control with
   a clearly labelled **Review transition ×3** action naming both components. On
   click, select pair `i`, reset to pass 1, build its window, and start playback.
   The main rail Play button uses the selected pair and the same label; when paused
   mid-review it resumes without resetting the pass. The last component's bounds
   Audition controls remain available for short checks.
3. Derive the exact pair from `songTimelineItem.clips`: start = first clip start,
   core end = second clip end. Clips tile continuously inside the item, so this
   covers both components and their join with no extra transport API.
4. At `position >= playbackWindow.endSeconds - 0.02` while playing:
   - pass 1 or 2 of a transition review: increment the pass and call
     `preview.seek(playbackWindow.startSeconds)` without pausing or stopping;
   - pass 3: call `preview.pause()`, retain the end position, and mark the review
     completed.

   Guard the boundary handling by the current playback-window identity and pass so
   one tick cannot increment twice. The existing 25 ms transport ticker and sheet
   effect are acceptable; no session-level loop is needed.
5. Pause/resume preserves position, pass, and completion state. Play after
   completion restarts at pass 1 from the window start. Stop tears down and resets
   pass/completion. Choosing another pair, scrubbing, changing either context
   toggle/duration, applying/cancelling bounds, or any other draft mutation resets
   the next review to pass 1. Scrubbing while playing retains playing state;
   scrubbing while paused/idle does not start audio.

### 5. Make adjacent-song context explicit and truthful

1. Default every pair review to exact component bounds. Add two opt-in Switch
   controls using `@/components/ui/switch`:
   - **Include previous-song tail**, enabled only when pair index is 0 and a
     previous item exists;
   - **Include next-song Opening**, enabled only when the pair ends at the last
     current-item Step and a next item exists.

   Both default off. Enabling one uses a default of 10 s; expose the existing
   Slider pattern for 0–30 whole seconds. A zero-second enabled context adds no
   duration and may be switched off without changing the selected pair.
2. Previous context starts at `max(0, songStartSeconds - seconds)`. This preserves
   the existing preceding-item tail, gap, and incoming crossfade behavior. Replace
   the old always-visible lead-in control with this opt-in control; no separate
   full-song window remains.
3. Next Opening is a duration-bounded context, not a claim that the next item has
   an Intro component. Do not fetch its LRC. Measure from the next item's actual
   timeline start, which may overlap the current item during a crossfade, and set
   `windowEnd = min(nextItemStart + nextOpeningSeconds, nextTimelineItem.startSeconds + nextTimelineItem.durationSeconds)`.
   It may span more than one saved next-item Step when the selected duration
   requires it. `actualOpeningSeconds = max(0, windowEnd - nextItemStart)` drives
   the displayed duration. Disable the toggle when the next item has no clips. The
   next item uses its saved arrangement; only the current item carries unsaved
   edits.
4. Before Play, show a summary derived from timeline data, not source-label
   assumptions:
   - exact pair: **Verse 1 → Chorus 1 · 3 passes**;
   - incoming context:
     **{Previous song title} · {its final clip label} → {Current song title} · {first pair label}**;
   - outgoing context:
     **{Current song title} · {last pair label} → {Next song title} · Opening — first {actualOpeningSeconds} s**.

   Resolve current-item component labels from draft Steps. For a previous sibling's
   tail, parse its saved arrangement and label the actual final clip; use
   **Full song** for an untouched full-Recording clip. For the next sibling, always
   use **Opening** in the pre-play summary, not Intro or an assumed component.
   During playback, identify the actual active saved next-item Step when one
   exists; for an untouched next item keep the **Opening** label throughout the
   bounded tail and do not fetch or infer its LRC. The guaranteed Song 2 pre-play
   copy is **Song 2 · Opening — first 10 s**, so the user knows exactly what time
   range will play without inventing catalog component data. Show the three-pass
   count separately.
5. During playback, derive the active item and clip from the whole timeline for
   both `playing` and `paused`. The phase/status shows
   **Playing: {song title} · {component label}** or **Paused: …**. During an
   overlapping crossfade where both items have active clips, show
   **Crossfade: {song A} · {component A} → {song B} · {component B}**. Highlight the
   current-item draft block only when its clip is active; for a next/previous item,
   highlight the corresponding timeline band and show its song/component in the
   status rather than attributing it to the current draft.
6. The timeline displays named current-item Step bands, a distinct previous-tail
   band, and a next-item band labelled **{next song title} · Opening**. The
   next-song acceptance case must visibly identify that Opening before playback and
   mark it active while its audio plays. The status region uses
   `role="status"`/`aria-live="polite"` for pass and item/component changes, not
   25 ms timecode updates.

### 6. Make scrubbing and component selection coexist

1. Convert the strip to an interactive group with named component buttons and a
   keyboard-accessible playhead slider. Retain absolute timeline geometry and add
   an active-component progress overlay. A paused or scrubbed position still
   derives active clip/lyrics.
2. Use explicit tap-versus-drag handling; do not rely on a captured pointer
   producing a normal button click:
   - on pointer down, record pointer id, starting client X/Y, whether playback was
     active, and `event.target.closest('[data-clip-index]')`; capture on the strip
     but do not pause or seek yet;
   - until movement exceeds `SCRUB_CLICK_TOLERANCE_PX` in either axis, do not seek
     or pause;
   - on the first movement beyond the threshold, pause if playback was active at
     pointer down; then scrub to the pointer's clamped fraction of the **current
     playback window**, never widening it to the whole song;
   - on pointer up with no threshold crossing and an originating non-final clip
     index, select that clip's pair and seek to its first clip start; do not
     perform a fractional seek or an extra click jump, and preserve the
     playing/paused state;
   - render the final clip as a labelled non-button band because it has no outgoing
     component pair; it remains reachable through scrubbing and the preceding pair;
   - on a drag, suppress the subsequent native click, reset pass/completion, and
     resume only if playback was active at drag start. Pointer cancel never resumes
     automatically. Pointer taps are handled on pointer up; clip `onClick` handles
     only keyboard-generated clicks (`event.detail === 0`) so the same gesture
     cannot run twice.
3. Clicking a component band outside an active scrub selects that pair, resets
   pass/completion, and seeks to its start while preserving playing/paused state. It
   does not silently change the window to song end. The next Play reviews the newly
   selected pair three times.
4. Keep the playhead `role="slider"` with `tabIndex=0`, min 0, max rounded window
   duration, current value, and `m:ss / m:ss` value text. ArrowLeft/Right seek ±1 s
   clamped to the current window; Home/End seek to its boundaries. Every keyboard
   seek resets the pass and retains the current playing/paused state. Do not
   register a second global Space shortcut.
5. At 390 CSS px, use vertically stacked full-width popover fields/actions, 44 px
   minimum touch targets for steppers/audition/transport, truncated timeline
   labels, and no horizontal overflow. The preview rail remains reachable below the
   score on mobile; no hover-only behavior.

### 7. Add bilingual UX copy and align the contract documentation

1. Add every new string to both maps in
   `delivery/webapp/src/lib/i18n/messages/arrangement.ts`; `bundle()` enforces
   matching keys. Replace the old Play Section copy with transition-review copy.
   Required English/zh-Hant strings:
   - `arrangement.bounds.edited`: `Edited` / `已調整`
   - `arrangement.bounds.apply`: `Apply bounds` / `套用時間`
   - `arrangement.bounds.cancel`: `Cancel` / `取消`
   - `arrangement.bounds.invalid`:
     `Use m:ss.s, keep start before end, and stay within the recording.` /
     `請使用 分:秒.十分之一秒 格式，開始須早於結束，且不得超出錄音範圍。`
   - `arrangement.bounds.auditionNote`:
     `Play 3 seconds inside the candidate boundary. Apply or Cancel before using transition review.` /
     `試聽候選邊界內的 3 秒。套用或取消後才能使用交接預覽。`
   - `arrangement.preview.reviewTransition`: `Review transition ×3` / `交接預覽（3 次）`
   - `arrangement.preview.reviewTransitionAria`:
     `Review {fromLabel} to {toLabel}, three passes` /
     `預覽「{fromLabel}」到「{toLabel}」的交接，共 3 次`
   - `arrangement.preview.pass`: `Pass {current} of 3` / `第 {current} 次，共 3 次`
   - `arrangement.preview.completed`: `Review complete · 3 passes` / `預覽完成，共 3 次`
   - `arrangement.preview.includePrevious`:
     `Include previous-song tail` / `包含前一首歌尾段`
   - `arrangement.preview.includeNext`:
     `Include next-song Opening` / `包含下一首歌開頭`
   - `arrangement.preview.nextOpening`:
     `Next: {song} · Opening — first {seconds} s` /
     `下一首：{song}・開頭，播放 {seconds} 秒`
   - `arrangement.preview.opening`: `Opening` / `開頭`
   - `arrangement.preview.fullSong`: `Full song` / `整首歌`
   - `arrangement.intro.inferred`:
     `Intro (inferred from lyrics)` / `前奏（根據歌詞推斷）`
   - `arrangement.intro.inferredShort`: `Intro (inferred)` / `前奏（推斷）`
   - `arrangement.intro.firstLyric`:
     `0:00.0–first lyric at {timecode}` / `0:00.0 至第一句歌詞 {timecode}`
   - `arrangement.intro.reset`: `Reset to inferred` / `還原推斷值`
   - `arrangement.preview.playingAt`:
     `Playing: {song} · {label}` / `正在播放：{song}・{label}`
   - `arrangement.preview.pausedAt`:
     `Paused: {song} · {label}` / `已暫停：{song}・{label}`
   - `arrangement.preview.crossfade`:
     `Crossfade: {fromSong} · {fromLabel} → {toSong} · {toLabel}` /
     `交叉淡化：{fromSong}・{fromLabel} → {toSong}・{toLabel}`

   Reuse v1's bounds labels, detected/reset text, stepper/audition labels, timeline
   aria labels, and English/Traditional Chinese translations where still
   applicable; remove obsolete immediate-edit/Done wording rather than leaving
   unused keys.
2. Update `specs/song_arrangement_editor.md`: document nullable `sectionIndex` for
   the Songset-local inferred Intro plus optional `manualBounds`; replace
   song-only/non-interactive preview language with Apply/Cancel bounds review,
   adjacent-pair three-pass review, opt-in previous tail/next Opening, truthful
   labels, and scrubbing. Add under `CONTEXT.md` → **Arrangement Step** that an
   inferred Intro uses a null section index and starts at zero through the first
   LRC timestamp, and both it and manual bounds belong only to the Songset Item,
   never catalog Recording analysis.

## Critical files & anchors

- `delivery/webapp/src/components/songset/ArrangementEditorSheet.tsx` — `draftKey`
  ~126, `SortableStepBlock` ~170–330, preview inputs ~526–632, window/transport
  ~639–754, strip/rail ~1160–1320. Contains both new UX flows; no v1
  immediate-nudge handlers survive.
- `delivery/webapp/src/lib/arrangements.ts` — `ArrangementStep` ~20, `isValidStep`
  ~243, `validateSongArrangement` ~316, `buildArrangementTimeline` ~464. Nullable
  Intro/manual validation and clip identity converge here.
- `delivery/webapp/src/hooks/useArrangementPreview.ts` — `seek` ~320,
  `startAtSeconds` ~367. Reuse pending seek and live re-anchoring; looping stays in
  the sheet.
- `delivery/render-worker/src/sow_render_worker/arrangements.py` —
  `ArrangementStep` ~50, `_is_valid_step_payload` ~223, `validate_arrangement`
  ~282. Must mirror the TS manual-bounds contract.
- `delivery/webapp/src/test/components/songset/ArrangementEditorSheet.test.tsx` —
  preview mock ~13–30 and fixtures ~55–145. Extend this harness for temporary
  bounds, three-pass review, pointer gestures, and next-item labels.

## Verification

1. From `delivery/webapp`, run:
   ```bash
   pnpm test -- src/test/lib/arrangements.test.ts src/test/components/songset/ArrangementEditorSheet.test.tsx src/test/api/songsets/arrangement.test.ts src/test/api/songsets/arrangement-persistence.test.ts
   pnpm typecheck && pnpm lint
   ```
   Contract cases: literal `manualBounds:true` parses and permits in-range
   nonsnapped bounds; a null-index `intro` from 0 with the flag parses and permits
   any positive in-range end without LRC at validation; null with wrong label,
   nonzero start, missing/false flag, negative/other indices, or extra keys fails;
   false/1/unknown fifth keys fail; manual detected index/label/range violations and
   unflagged drift still fail; route returns 200 for true and 400 for false;
   persistence retains nullable index and flag while catalog analysis is untouched.
2. Extend the sheet tests with observable cases:
   - first LRC timestamps `3.00`, `3.01`, and `12.40` respectively omit, offer, and
     offer the inferred Intro; adding the `12.40` case appends
     `{sectionIndex:null,label:"intro",startSeconds:0,endSeconds:12.4,manualBounds:true}`,
     and its Start is fixed at zero;
   - saved inferred Intro remains valid without lyrics; its Reset restores the saved
     fixture's raw first-LRC endpoint while retaining the flag;
   - enter `0:12.3`/`0:28.7`, audition, then Cancel: `startAtSeconds` uses a timeline
     built from those candidate bounds, while `draft` and history remain unchanged;
   - two +0.1 presses then Apply produce exactly one history entry and one undo
     restores both fields; Reset-to-detected followed by Apply clears the flag, and
     Reset back to initial values clears dirty;
   - malformed/reversed/out-of-duration input disables Apply, steppers, and
     auditions; arrow boundaries are disabled at 0 and Recording duration;
   - selecting pair 0 starts at clip 0 and its window ends at clip 1 end. Simulated
     end positions cause seeks to start after passes 1 and 2, then pause after pass
     3 with no fourth seek; pause/resume retains pass 2, while scrub resets to pass
     1;
   - a 2 px pointer movement then release on a clip selects without fractional seek;
     a 20 px drag seeks and emits no clip click/jump; starting a drag during pair
     review never widens its window;
   - with next Opening enabled on the final pair, the pre-play summary says
     `Song 2 · Opening — first 10 s`; moving the mocked playhead ten seconds into
     Song 2 marks its Opening band active, and a shorter next item clamps the window
     to that item's end;
   - pause-state lyric/component highlighting remains active, and keyboard
     Home/End/arrows stay within the selected review window.

   Stub pointer capture and strip `getBoundingClientRect` locally in this test file.
3. From `delivery/render-worker`, run:
   ```bash
   uv run --extra dev pytest tests/test_arrangements.py -v
   uv run --extra dev pytest tests/ -v
   ```
   Add true/false/1 parsing, nullable-index Intro acceptance and malformed
   null-index rejection, manual in-range acceptance, manual detected
   index/label/range rejection, and unflagged drift rejection. Add one
   pipeline/audio fixture proving a null-index Intro renders source 0 through its
   saved endpoint. Existing LRC/chapter/video suites must remain green without
   filter changes.
4. Real UI proof from `delivery/webapp`: reuse the HTTPS dev server on 8080 or run
   `pnpm dev:https`; use cert-tolerant headless Chrome on CDP 9222 and
   `SOW_WEBAPP_TESTUSER_LOGIN`/`SOW_WEBAPP_TESTUSER_PASSWORD` as documented in
   `AGENTS.md`. Open a Songset with at least two items and choose Arrange:
   - change Start from its opening value to `0:12.3`, audition it, Cancel, and
     verify the header/draft did not change; repeat and Apply, then verify one Undo
     restores it;
   - start the first pair and observe status pass 1/3 → 2/3 → 3/3 → complete, with
     audible return to the pair start exactly twice; Pause in pass 2 and Resume
     without restarting the count;
   - for a current Recording whose first LRC line is at 12.4 s, add Intro
     (inferred), verify its fixed 0–12.4 range, review Intro → next component three
     times, Save/reopen, and confirm the null-index Step persists; verify a separate
     3.0 s fixture offers no Intro;
   - select the final pair, enable next-song Opening at 10 s, and verify before Play
     that the rail says **Song 2 · Opening — first 10 s**, then its Opening
     band/audio becomes active at the expected position; verify playback stops at
     the configured Opening end and never claims Song 2 has a stored Intro
     component;
   - repeat with previous-song tail on the first pair, including a crossfade, and
     verify both song/component names;
   - tap a component and drag across it: tap selects/seeks to its start, drag scrubs
     without a release jump, and neither changes the selected window;
   - Save, reopen, and reload: bounds and the visible Edited badge persist; the
     editor API's `recording.sections`/`beats` are byte-identical to before editing
     and the catalog view is unchanged;
   - at 390 CSS px and desktop width, verify focus order, Escape/Cancel behavior,
     touch targets, label truncation, and absence of footer/popover clipping.

## Assumptions & contingencies

- Three passes means the selected pair is heard three times total, with exactly two
  automatic returns to its start. Pause alone preserves the pass; other review
  interruptions reset it.
- If the Base UI popover's focus containment fails inside the Sheet, use one
  sheet-owned inline expanding panel below the selected block with identical
  temporary state, controls, callbacks, validation, and testids; do not revert to
  immediate draft mutations.
- Inferred Intro is offered only when the first LRC timestamp is strictly greater
  than 3 seconds and within Recording duration. It belongs to the current Songset
  Item; later LRC edits do not rewrite or revalidate a saved Intro.
- Next Opening is a duration-bounded slice of the next item's saved timeline, not
  an inferred or persisted component. If that item has no clips, the next-context
  toggle is disabled and exact current-item pair review remains available.
