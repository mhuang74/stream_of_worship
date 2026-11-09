# Arrangement Editor: interactive preview timeline + editable component bounds

## Context

The Song Arrangement Editor sheet (`ArrangementEditorSheet.tsx`) renders the preview rail's timeline as a non-interactive `role="img"` strip of anonymous colored blocks with a thin playhead, and each component's source range is read-only. Users need (a) the strip to name each component, show progress inside the active component, jump playback to a component's start on click, and scrub the playhead by dragging; and (b) to correct a component's inaccurate start/end time. Bounds corrections must persist on the Songset Item only — never written back to `recordings.sections`/the song catalog.

Today the server (`src/lib/arrangements.ts` + the Python worker mirror) rejects any Step whose bounds differ from freshly beat-snapped analysis by more than `1e-6`. Bounds edits therefore require an explicit persisted marker so untouched Steps keep the stale-analysis guarantee.

Decided (user-confirmed): per-Step `manualBounds` flag; bounds edited in a popover with **only** ±0.10 s stepper arrows (no free text entry), plus a preview button beside each timestamp that auditions the boundary without the lead-in.

## Approach

### 1. Persisted contract: optional `manualBounds` on a Step (`src/lib/arrangements.ts`)

1. Extend `ArrangementStep` (currently lines 20–25) with an optional literal:
   ```ts
   /** Present only when a user moved these bounds in the editor. Such Steps are
    * exempt from the beat-snap equality check but still need a matching
    * sectionIndex/label and a valid in-range span. */
   manualBounds?: true;
   ```
2. `isValidStep` (lines 243–260): replace `Object.keys(step).length === 4` with a key-set check that accepts exactly the four base keys (`sectionIndex`, `label`, `startSeconds`, `endSeconds`) **or** those four plus `manualBounds`, and requires `step.manualBounds === true` when the key is present (`false`, `1`, `"true"` are malformed). Keep every existing field/range check unchanged.
3. `validateSongArrangement` (lines 303–350): keep the `expectedByIndex` lookup and the `expected.label !== step.label` check for every Step; wrap only the two `Math.abs(...) > VALIDATION_TOLERANCE_SECONDS` comparisons in `if (step.manualBounds !== true) { ... }`. The final range check (`startSeconds < 0 || endSeconds > durationSeconds || startSeconds >= endSeconds`) keeps applying to **all** Steps, manual included. Update the function's comment: manual Steps are the user's explicit cut, so analysis drift no longer invalidates them; unflagged Steps still fail stale.
4. `parseSongArrangement` needs no change beyond `isValidStep` (its `{ ...(step as ArrangementStep) }` copy already preserves the flag). `buildSnappedSteps` never emits the flag.

### 2. Wire schema (`src/app/api/songsets/[id]/items/[itemId]/arrangement/route.ts`)

`arrangementStepSchema` (lines 16–27) stays a `z.strictObject`; add
`manualBounds: z.literal(true).optional()` after `endSeconds`. Nothing else in the route changes: `updateSongArrangement` re-serializes `validated.arrangement`, so the flag round-trips into `songset_items.arrangement_steps` with no DB migration.

### 3. Python worker mirror (`delivery/render-worker/src/sow_render_worker/arrangements.py`)

1. `ArrangementStep` dataclass: add `manual_bounds: bool = False` after `end_seconds` (defaulted, so existing keyword constructions keep working; `duration_seconds` is unchanged).
2. `_is_valid_step_payload`: replace the exact `set(value.keys()) != {...}` test with
   ```python
   _STEP_KEYS = frozenset({"sectionIndex", "label", "startSeconds", "endSeconds"})
   keys = set(value.keys())
   if keys not in (_STEP_KEYS, _STEP_KEYS | {"manualBounds"}):
       return False
   if "manualBounds" in value and value["manualBounds"] is not True:
       return False
   ```
3. `parse_song_arrangement`: set `manual_bounds=payload.get("manualBounds") is True`.
4. `validate_arrangement`: keep the index/label checks for every Step; wrap only the `abs(...) > VALIDATION_TOLERANCE_SECONDS` comparison in `if not step.manual_bounds:`. The existing range check stays unconditional.

Audio slicing, `remap_lrc_to_clips`, chapters, and video already consume `start_seconds`/`end_seconds` directly, so manual bounds render correctly with no further worker change. `getItemDurationSeconds`/`getSongsetDurationSeconds`/`buildArrangementTimeline` in TS likewise already derive everything from Step bounds.

### 4. Sheet: editable bounds popover (`src/components/songset/ArrangementEditorSheet.tsx`)

Module-scope additions in the sheet file (editor-only concerns; keep them out of `lib/arrangements.ts`):

```ts
const BOUNDS_STEP_SECONDS = 0.1;
const BOUNDS_AUDITION_SECONDS = 3;
function roundToTenths(seconds: number): number { return Math.round(seconds * 10) / 10; }
function formatTimecodeTenths(seconds: number): string {
  const total = Math.max(0, roundToTenths(seconds));
  const minutes = Math.floor(total / 60);
  return `${minutes}:${(total - minutes * 60).toFixed(1).padStart(4, "0")}`;
}
/** Next Step for a nudge, or null when the nudge would violate 0 <= start < end <= duration. */
function nudgeBoundsStep(
  step: ArrangementStep,
  field: "start" | "end",
  deltaSeconds: number,
  durationSeconds: number | null
): ArrangementStep | null;
```

`nudgeBoundsStep` returns `null` when `durationSeconds == null`, when `start < 0`, when `end > durationSeconds`, or when `start >= end` after rounding; otherwise `{ ...step, startSeconds, endSeconds, manualBounds: true }` with the edited field set to `roundToTenths(value + deltaSeconds)` and the other field untouched. The first nudge therefore quantizes a 6-decimal snapped bound onto the 0.1 s grid; subsequent nudges move exactly 0.1 s.

**`SortableStepBlock` changes**

- New props: `manualBounds: boolean`, `detectedBounds: ArrangementStep | null`, `recordingDurationSeconds: number | null`, `onNudgeBounds(id, field, deltaSeconds)`, `onResetBounds(id)`, `onAuditionBounds(id, field)`, `onBoundsSessionEnd()`.
- Replace the static range `<span>` (lines 240–242) with a `Popover` (`@/components/ui/popover`) whose trigger is a `<button type="button">` carrying `ml-auto`, `font-mono text-[11px] tabular-nums`, the text `formatTimecodeTenths(start)–formatTimecodeTenths(end)`, a `Pencil` icon (`size-3`, `aria-hidden`), `aria-label={t("arrangement.bounds.editAria").replace("{label}", displayLabel)}`, `data-testid={`arrangement-bounds-trigger-${entry.id}`}`, `disabled={disabled || recordingDurationSeconds == null}`, and `text-primary` when `manualBounds`. Local `const [boundsOpen, setBoundsOpen] = useState(false)`; `onOpenChange={(open) => { setBoundsOpen(open); if (!open) onBoundsSessionEnd(); }}`.
- `PopoverContent` (`align="end"`, `sideOffset={4}`, `className="w-72 space-y-2"`, `data-testid={`arrangement-bounds-popover-${entry.id}`}`) contains:
  - header row: `displayLabel` + `t("arrangement.bounds.detected").replace("{range}", detectedBounds ? `${formatTimecodeTenths(detectedBounds.startSeconds)}–${formatTimecodeTenths(detectedBounds.endSeconds)}` : "—")`;
  - one row per field (`start`, `end`) with `data-testid={`arrangement-bounds-${field}-${entry.id}`}`: field label (`arrangement.bounds.start` / `.end`), a `ChevronLeft` button (`size="icon-sm"`, `onClick={() => onNudgeBounds(entry.id, field, -BOUNDS_STEP_SECONDS)}`, `data-testid={`arrangement-bounds-${field}-earlier-${entry.id}`}`), an `<output className="min-w-[3.5rem] text-center font-mono text-xs tabular-nums">` showing `formatTimecodeTenths(value)`, a `ChevronRight` button (`+BOUNDS_STEP_SECONDS`, `data-testid={`arrangement-bounds-${field}-later-${entry.id}`}`), and a `Play` button (`onClick={() => onAuditionBounds(entry.id, field)}`, `data-testid={`arrangement-bounds-${field}-audition-${entry.id}`}`, `aria-label` = `arrangement.bounds.auditionStartAria` for `start`, `arrangement.bounds.auditionEndAria` for `end`);
  - each arrow is `disabled` exactly when `nudgeBoundsStep(entry.step, field, delta, recordingDurationSeconds) === null`;
  - `t("arrangement.bounds.auditionNote")` and `t("arrangement.bounds.manualNote")` paragraphs (`text-[11px] text-muted-foreground`);
  - footer: `RotateCcw` + `t("arrangement.bounds.reset")` (`data-testid={`arrangement-bounds-reset-${entry.id}`}`, `disabled={!manualBounds || detectedBounds === null}`, `onClick={() => onResetBounds(entry.id)}`) and a `t("arrangement.bounds.done")` button closing the popover.
  - No free text input, no Apply/Cancel: every arrow press commits immediately.

**Sheet-level handlers** (add beside `commitDraft`, lines 738–754)

- `const boundsSessionRef = useRef<string | null>(null);` — id of the Step whose edit session already pushed one history entry.
- `commitBoundsChange(id, next)`: `const push = boundsSessionRef.current !== id; if (push) boundsSessionRef.current = id; commitDraft(next, push);` — one undo restores the whole 0.1 s-nudge session instead of one entry per press.
- `nudgeBound(id, field, delta)`: bail when `isSaving`; find the draft index; `const nextStep = nudgeBoundsStep(step, field, delta, item.recording?.durationSeconds ?? null)`; return when `null`; otherwise `commitBoundsChange(id, draft.map((entry, i) => (i === index ? { ...entry, step: nextStep } : entry)))`.
- `resetBounds(id)`: find the index; `const detected = snappedByIndex.get(step.sectionIndex)`; return when missing; commit a Step rebuilt as `{ sectionIndex: detected.sectionIndex, label: detected.label, startSeconds: detected.startSeconds, endSeconds: detected.endSeconds }` (the flag is dropped by construction) via `commitBoundsChange`.
- `auditionBound(id, field)`: bail when `isSaving`; take `const clip = songTimelineItem?.clips[index]`; return when missing; `const span = Math.min(BOUNDS_AUDITION_SECONDS, clip.endSeconds - clip.startSeconds)`; `const start = field === "start" ? clip.startSeconds : clip.endSeconds - span`; then `setPlaybackWindow({ startSeconds: start, endSeconds: start + span })` and `startWindow(start)`. The window never includes the lead-in because `clip.startSeconds >= songStartSeconds`.
- `handleBoundsSessionEnd()`: `boundsSessionRef.current = null`.
- Pass to each block: `manualBounds={entry.step.manualBounds === true}`, `detectedBounds={snappedByIndex.get(entry.step.sectionIndex) ?? null}`, `recordingDurationSeconds={item.recording?.durationSeconds ?? null}`, the four callbacks, and `disabled={isSaving}` as today.
- `draftKey` (lines 126–130): append the flag so a flag-only change is dirty-aware: `` `${step.sectionIndex}:${step.startSeconds}:${step.endSeconds}:${step.manualBounds ? 1 : 0}` ``.
- Keep the block header's time display on tenths (the `formatTimecode` helper stays for durations, elapsed/total, and the add dialog).

Because `validateDraftProblem` already round-trips the draft through `validateSongArrangement`, a saved arrangement that is `stale` only because its bounds drifted can now be repaired by nudging a bound (which sets `manualBounds`) — intended, and it re-enables Save/Preview without Reset.

### 5. Sheet: interactive preview timeline (same file, strip at lines 1166–1206)

1. Active-component derivation (lines 616–632): compute `activeClipIndex` when `preview.status === "playing" || preview.status === "paused"` (not `"playing"` only), so a paused/seeked playhead still shows which component it sits in. Leave `activeClip`, `activeLocalSeconds`, `activeLineIndex`, `activeLabel` as they are.
2. `handlePreviewToggle` (lines 676–708): drop the `preview.status === "paused" &&` term from `resumable`; keep `position > windowStartSeconds + 0.25 && position < windowEndSeconds - 0.25`. `preview.play()` already honours a pre-play `seek` through the hook's pending-seek path, so scrubbing while idle then pressing Play resumes at the scrubbed position.
3. New refs/state near `playbackWindow`: `const stripRef = useRef<HTMLDivElement | null>(null)`, `const [scrubbing, setScrubbing] = useState(false)`, `const scrubResumeRef = useRef(false)`.
4. `seekToClientX(clientX)`: measure `stripRef.current.getBoundingClientRect()`; bail when `width <= 0` or `windowTotalSeconds <= 0`; `const fraction = Math.min(Math.max((clientX - rect.left) / rect.width, 0), 1)`; `preview.seek(windowStartSeconds + fraction * windowTotalSeconds)`.
5. `handleScrubStart(event)`: bail when `isSaving || !isPreviewAvailable || windowTotalSeconds <= 0`; `scrubResumeRef.current = preview.status === "playing"`; if true `preview.pause()`; `setPlaybackWindow({ startSeconds: windowStartSeconds, endSeconds: windowEndSeconds })`; `setScrubbing(true)`; `event.currentTarget.setPointerCapture(event.pointerId)`; `seekToClientX(event.clientX)`. `handleScrubMove`: only while `scrubbing`, `seekToClientX`. `handleScrubEnd`: only while `scrubbing`; `setScrubbing(false)`; release the capture when `hasPointerCapture(pointerId)`; if `scrubResumeRef.current` then `scrubResumeRef.current = false; void preview.play().catch(() => {})`. Pausing during the drag avoids re-anchoring the transport on every pointer move; the release resumes playback from the new position.
6. `handleScrubKeyDown(event)`: `ArrowLeft` −1 s, `ArrowRight` +1 s, `Home` 0, `End` `windowTotalSeconds`; `event.preventDefault()`; set `playbackWindow` to the song window; `preview.seek(windowStartSeconds + clamp(target, 0, windowTotalSeconds))`.
7. `jumpToClip(clipIndex)`: bail when `isSaving`; `const clip = songTimelineItem?.clips[clipIndex]`; return when missing; `setPlaybackWindow({ startSeconds: windowStartSeconds, endSeconds: windowEndSeconds })`; `preview.seek(clip.startSeconds)` — playback state is preserved (playing keeps playing, paused stays paused at the component start).
8. Strip markup: the container becomes
   ```tsx
   <div ref={stripRef} role="group" aria-label={t("arrangement.preview.timelineAria")}
        data-testid="arrangement-preview-timeline"
        className="relative h-12 touch-none overflow-hidden rounded-lg border border-border/70 bg-muted/40 select-none"
        onPointerDown={handleScrubStart} onPointerMove={handleScrubMove}
        onPointerUp={handleScrubEnd} onPointerCancel={handleScrubEnd}>
   ```
   (drop `role="img"`). Keep the lead-in band and its geometry.
9. Clip blocks become `<button type="button">` at the same `left`/`width` percentages (`inset-y-1.5`, `overflow-hidden`, `flex items-center`, `px-1`, `text-left`), `onClick={() => jumpToClip(clipIndex)}`, `data-testid="arrangement-preview-clip"`, `data-clip-index={clipIndex}`, `data-active={clipIndex === activeClipIndex ? "true" : undefined}`, `aria-label={t("arrangement.preview.jumpToComponent").replace("{label}", labelForDraftIndex(clipIndex) ?? "")}`. Base color `bg-primary/20` (active `bg-primary/35`); when the clip is active, render an `aria-hidden` progress overlay `absolute inset-y-0 left-0 bg-primary/70` with `width: `${clamp((position - clip.startSeconds) / max(clip.endSeconds - clip.startSeconds, 0.001), 0, 1) * 100}%``. Inside, a `relative block w-full truncate text-[10px] leading-tight font-medium` span with the component name. No `stopPropagation` on pointer events: a drag may start on a block (it scrubs), and a plain click additionally fires the block's jump.
10. Playhead becomes the slider thumb (same `left` percentage):
    ```tsx
    <div role="slider" tabIndex={0} aria-orientation="horizontal"
         aria-label={t("arrangement.preview.scrubAria")}
         aria-valuemin={0} aria-valuemax={Math.round(windowTotalSeconds)}
         aria-valuenow={Math.round(windowPositionSeconds)}
         aria-valuetext={`${formatTimecode(windowPositionSeconds)} / ${formatTimecode(windowTotalSeconds)}`}
         onKeyDown={handleScrubKeyDown}
         data-testid="arrangement-preview-playhead"
         className="absolute inset-y-0 w-3 -translate-x-1/2 cursor-ew-resize touch-none focus-visible:outline-none"
         style={{ left: `${(windowPositionSeconds / windowTotalSeconds) * 100}%` }}>
      <span aria-hidden="true" className="absolute inset-y-0 left-1/2 w-0.5 -translate-x-1/2 bg-foreground/80" />
    </div>
    ```
    (A 12 px hit area around the 2 px line; pointerdown bubbles to the strip, so the thumb needs no own pointer handler.)

### 6. Messages (`src/lib/i18n/messages/arrangement.ts`)

Add these keys to **both** the `en` and `zh-Hant` maps (the `bundle()` type makes a missing zh-Hant key a compile error):

| key | en | zh-Hant |
| --- | --- | --- |
| `arrangement.bounds.editAria` | `Edit the start and end time of {label}` | `編輯「{label}」的開始與結束時間` |
| `arrangement.bounds.start` | `Start` | `開始` |
| `arrangement.bounds.end` | `End` | `結束` |
| `arrangement.bounds.detected` | `Detected {range}` | `偵測值 {range}` |
| `arrangement.bounds.decreaseAria` | `Move 0.1 seconds earlier` | `提前 0.1 秒` |
| `arrangement.bounds.increaseAria` | `Move 0.1 seconds later` | `延後 0.1 秒` |
| `arrangement.bounds.auditionStartAria` | `Listen from the start time` | `試聽開始時間` |
| `arrangement.bounds.auditionEndAria` | `Listen up to the end time` | `試聽結束時間` |
| `arrangement.bounds.auditionNote` | `Auditions play 3 seconds inside this component, without the lead-in.` | `試聽只播放這個段落內的 3 秒，不含前導。` |
| `arrangement.bounds.manualNote` | `Edited bounds apply to this Songset only; the song catalog is unchanged.` | `調整後的時間只套用於這份歌單，不會修改歌曲目錄。` |
| `arrangement.bounds.reset` | `Reset to detected` | `還原偵測值` |
| `arrangement.bounds.done` | `Done` | `完成` |
| `arrangement.preview.jumpToComponent` | `Jump to {label}` | `跳到「{label}」` |
| `arrangement.preview.scrubAria` | `Preview position` | `預覽位置` |

### 7. Contract docs

- `specs/song_arrangement_editor.md`: in the wire contract add the optional Step field `"manualBounds": true` with the rule that flagged Steps need only a matching `sectionIndex`/`label` plus `0 <= startSeconds < endSeconds <= recordingDuration` (untouched Steps still require exact snapped bounds), and that the value is stored on the Songset Item only — `recordings.sections`/`beats` are never written. In §5.9 replace "a non-interactive timeline strip" with the interactive behaviour (named blocks, click-to-jump, drag/keyboard scrub, 3 s boundary auditions) and note the bounds popover with 0.1 s steppers.
- `CONTEXT.md` under **Arrangement Step**: one sentence — a Step's bounds may be manually adjusted in the editor; the adjustment belongs to that Songset Item and never modifies the catalog Recording's analysis.

## Critical files & anchors

- `delivery/webapp/src/components/songset/ArrangementEditorSheet.tsx` — `SortableStepBlock` header 222–279, `draftKey` 126–130, `commitDraft` 738–754, `handlePreviewToggle` 676–708, active-clip derivation 616–632, timeline strip 1166–1206.
- `delivery/webapp/src/lib/arrangements.ts` — `ArrangementStep` 20–25, `isValidStep` 243–260, `validateSongArrangement` 303–350.
- `delivery/webapp/src/app/api/songsets/[id]/items/[itemId]/arrangement/route.ts` — `arrangementStepSchema` 16–27.
- `delivery/render-worker/src/sow_render_worker/arrangements.py` — `ArrangementStep` 50–60, `_is_valid_step_payload` 232–247, `parse_song_arrangement` 250–279, `validate_arrangement` 282–332.
- `delivery/webapp/src/lib/i18n/messages/arrangement.ts` — preview keys 76–98 (en) / 189–205 (zh-Hant).

## Verification

1. Contract tests, from `delivery/webapp`:
   ```bash
   pnpm test -- src/test/lib/arrangements.test.ts src/test/components/songset/ArrangementEditorSheet.test.tsx \
     src/test/api/songsets/arrangement.test.ts src/test/api/songsets/arrangement-persistence.test.ts
   pnpm typecheck && pnpm lint
   ```
   Add/keep these behaviours:
   - `arrangements.test.ts`: parses a Step with `manualBounds: true`; rejects `manualBounds: false`, `1`, and a 5th unknown key; accepts an in-range manual Step whose bounds differ from snapped; rejects a manual Step with `startSeconds < 0`, `endSeconds > durationSeconds`, `startSeconds >= endSeconds`, an unknown `sectionIndex`, or a mismatched `label`; an unflagged Step with drifted bounds still returns `ARRANGEMENT_STALE`.
   - `arrangement.test.ts` (route): a body with `manualBounds: true` reaches `updateSongArrangement` and returns 200; `manualBounds: false` returns 400.
   - `arrangement-persistence.test.ts`: saving manual bounds persists `"manualBounds":true` in `arrangement_steps`; after stored beats change, a manual-bounds arrangement still saves (no 409) while the existing unflagged-beats-drift case still returns `ARRANGEMENT_STALE`.
   - `ArrangementEditorSheet.test.tsx`: stub `Element.prototype.setPointerCapture`/`releasePointerCapture` and `getBoundingClientRect` (jsdom lacks them). Then: increasing Start by one press raises that Step's `startSeconds` by 0.1, sets `manualBounds`, and shows the unsaved status; the Start/Earlier arrow is disabled at 0 and the End/Later arrow at the recording duration; the Start audition calls `startAtSeconds` with the component's clip start (single item, 0–10 s first Step → `0`) and the End audition with `clip.end - 3` (`7`), both `>= songStartSeconds`; Reset-to-detected restores the snapped values and clears `manualBounds`; clicking `arrangement-preview-clip` with `data-clip-index="1"` calls `preview.seek(10)`; a pointerdown/move/up sequence across the strip seeks to the matching fraction of the window.
2. Worker tests, from `delivery/render-worker`:
   ```bash
   uv run --extra dev pytest tests/test_arrangements.py -v
   uv run --extra dev pytest tests/ -v
   ```
   `test_arrangements.py`: `parse_song_arrangement` accepts `manualBounds: true` and rejects `false`/`1`; `validate_arrangement` accepts in-range manual bounds, rejects out-of-range/unknown-index/wrong-label manual Steps, and still rejects unflagged drifted bounds. `test_pipeline.py`/`test_audio_engine.py` must stay green (they consume bounds directly).
3. Real UI proof, from `delivery/webapp` (reuse a dev server already on 8080; otherwise `pnpm dev:https`). In cert-tolerant headless Chrome on CDP 9222 per `AGENTS.md`, sign in with `SOW_WEBAPP_TESTUSER_LOGIN`/`SOW_WEBAPP_TESTUSER_PASSWORD`, open a Songset, choose Arrange:
   - every preview block shows its component label; clicking the second block moves the playhead to that block's left edge and playback continues from there; dragging the playhead repositions audio and pauses-then-resumes exactly once;
   - opening a component's range popover, pressing Later on Start twice moves the start 0.2 s and the header shows the new `m:ss.s` plus the edited marker; pressing Reset restores the detected range;
   - pressing the Start audition button: inspect `document.querySelectorAll('audio')` in `tab.run` — the element whose `currentTime` is being driven is the component's source start (or `sourceEnd - 3` for the End audition), and no lead-in audio is scheduled;
   - Save, reopen the sheet: the edited bounds and marker persist; reload the page: still present. Then confirm the catalog Recording is untouched: `recording.sections`/`beats` in the Songset editor API response are byte-identical to before the edit, and the song's analysis in the catalog view is unchanged.
   - Also check 390 CSS px width: the popover fits, the strip labels truncate instead of overflowing, and the footer still does not overlap the list.

## Assumptions & contingencies

- Radix `Popover` inside the `Sheet` (both portalled) may misbehave under the sheet's focus trap. If it does, replace the popover with an inline expanding panel below the block header driven by `const [boundsOpenId, setBoundsOpenId] = useState<string | null>(null)` at sheet level, keeping every field, callback, testid, and disable rule above unchanged.
- A Step nudged back to its exact detected value keeps `manualBounds: true` until Reset-to-detected is used; this is accepted (the flag is sticky by design and only Reset clears it).
- `manualBounds` is not a render-worker-only concern: the flag must round-trip through `updateSongArrangement` unchanged. If `validateSongArrangement` ever re-orders Step keys, JSON key order changes are irrelevant to both validators (they compare key sets, not text).
