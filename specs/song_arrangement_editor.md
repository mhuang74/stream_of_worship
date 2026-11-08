# Song Arrangement Editor — issue #254

## Context

Add a webapp editor that shortens, lengthens, repeats, and reorders detected sections of each Recording inside a Songset. Users audition a draft in a whole-Songset browser mix before explicitly saving; saving persists only the Song Arrangement definition and marks previous Renders stale. A later explicit Render produces the arranged MP3, lyric video, and chapters. Arrangements belong to Songset Items, survive owner duplication, and flow through existing read-only sharing.

## Behavior and domain contract

- **Song Arrangement**: a Songset Item’s ordered, repeatable sequence of Arrangement Steps. **Arrangement Step**: one use of a detected Recording section in that sequence. Add these two definitions to `CONTEXT.md` under “Songset & Transitions”; avoid “song version,” “section sequence,” and standalone ambiguous “arrangement.” SongComponent remains the distinct admin-side analysis concept.
- Reorder, omit, repeat, and arbitrarily reorder Steps. An untouched item has no custom Song Arrangement (`NULL`). Reset restores `NULL`; new items also start as `NULL`. No catalog Recording or reusable song variant is created.
- Boundaries come from `recordings.sections` (`[{label,start,end}]`, seconds relative to `{hashPrefix}/audio.mp3`) and `recordings.beats` (seconds). Structured lyrics/LRC supply text, not section boundaries.
- Snap every section start and end independently to the nearest detected beat, then clamp to `[0, recordings.duration_seconds]`. Ties choose the earlier beat (`<`, not `<=`, when replacing the nearest candidate). Round snapped values to six decimal places. Custom arrangements contain only selected Steps: do not fill analysis gaps.
- Join Steps inside an item directly, without gaps or fades. Gaps/crossfades remain only between Songset Items and retain current worker semantics.
- A bottom-sheet editor owns one item’s local draft. Preview combines that draft with the current saved arrangements/transitions of all other items. Save atomically replaces that item’s arrangement; Cancel discards the draft. No automatic Render or persisted preview artifact.
- Preview is a near-render Web Audio mix of the whole Songset, including selected clips, inter-item gaps, and existing linear crossfade envelopes. It does not reproduce FFmpeg loudness normalization or final encoding. Existing key-shift/tempo fields remain unapplied because the worker also ignores them.
- Retain five Songset Items and 1500 seconds of selected audio, excluding inter-item gaps/crossfades as today. Repeating Steps does not increase song count. All editor/list/share/render duration displays and checks use arranged durations.
- Missing or changed analysis preserves the saved Arrangement but blocks preview and new Render; the editor opens far enough to explain the problem and allow Reset. Do not silently fall back to the original Recording. Existing completed Renders remain playable historical artifacts.
- Existing token Share remains read-only and serves the latest Render. Owner Duplicate copies arrangements. Do not add recipient cloning or an Android arrangement editor; Android item APIs and existing Render playback remain unchanged.
- Arrangement preview requires online access to source Recordings. Existing completed Offline Copies remain unchanged, and preview bytes do not enter offline Render caches.

## Persistence and wire contract

Add nullable `songset_items.arrangement_steps` TEXT containing JSON:

```json
{
  "version": 1,
  "steps": [
    {"sectionIndex": 0, "label": "intro", "startSeconds": 0.0, "endSeconds": 4.5},
    {"sectionIndex": 2, "label": "chorus", "startSeconds": 30.0, "endSeconds": 45.0},
    {"sectionIndex": 2, "label": "chorus", "startSeconds": 30.0, "endSeconds": 45.0}
  ]
}
```

`sectionIndex` is the zero-based index into the Recording’s parsed sections array. Duplicate indices are legal and array order is authoritative. `label` is required display metadata, a string of 1–100 characters. A custom arrangement has 1–64 Steps; never truncate a longer sequence. Require exactly the shown fields, integer `version: 1`, integer nonnegative `sectionIndex`, and finite numeric timestamps (JSON booleans/null/strings are not numbers). Require `0 <= startSeconds < endSeconds <= recordingDuration`. `NULL` means original full Recording; no legacy backfill.

At save, the server independently rebuilds every expected Step from the current `sectionIndex`, source label/timestamps, and beats. It accepts only when label and both saved bounds match the freshly snapped values within `0.000001` seconds. Thus client-forged arbitrary cuts and stale analysis are rejected. At render, the worker repeats this validation against current analysis; malformed JSON, missing analysis, index/label/bounds mismatch, or an invalid range fails the Render with a repair-or-reset error rather than rendering original audio.

## Approach

### 1. Establish one arrangement/timing vocabulary

1. Create `delivery/webapp/src/lib/arrangements.ts`; no equivalent webapp utility exists. Export:
   - `RecordingSection { label: string; start: number; end: number }`
   - `ArrangementStep { sectionIndex: number; label: string; startSeconds: number; endSeconds: number }`
   - `SongArrangement { version: 1; steps: ArrangementStep[] }`
   - `parseRecordingSections(value: string | null): RecordingSection[] | null`
   - `parseRecordingBeats(value: string | null): number[]` (empty means unavailable)
   - `buildSnappedSteps(sections, beats, durationSeconds): ArrangementStep[] | null`
   - `parseSongArrangement(value: string | null): SongArrangement | null` for structural parsing only; distinguish SQL NULL from malformed JSON with a separate `isPresent` input or result object.
   - `validateSongArrangement(value, sections, beats, durationSeconds): {ok: true; arrangement: SongArrangement | null} | {ok: false; code: "ANALYSIS_UNAVAILABLE" | "ARRANGEMENT_INVALID" | "ARRANGEMENT_STALE"}`.
   - `getItemDurationSeconds(item): number | null` and `getSongsetDurationSeconds(items): number | null`. Valid custom duration is the sum of Step lengths; untouched duration is Recording duration. Return `null` when any present arrangement is structurally invalid rather than presenting original duration as arranged duration.
   - `calculateGapSeconds(item)` and `buildArrangementTimeline(items): ArrangementTimeline`. The latter returns per-item `startSeconds`, `durationSeconds`, `gapBeforeSeconds`, `crossfadeSeconds`, and ordered `clips[{recordingHashPrefix, sectionIndex, sourceStartSeconds, sourceEndSeconds, startSeconds, endSeconds}]`.
2. Parsing beats sorts finite in-duration values and removes duplicates before nearest-beat selection. Invalid/missing beats or no valid section makes `buildSnappedSteps` return `null`. Sections need not cover the Recording and may touch/overlap; each still needs `0 <= start < end <= duration` and a nonempty label. A snap that collapses a section to zero length makes arrangement unavailable.
3. Mirror the worker gap math exactly: default 2 beats; `60000 / tempoBpm` when BPM is positive, otherwise 1000 ms/beat; enabled nonzero crossfade makes gap zero. For item zero, start/gap/crossfade are zero. Otherwise `crossfade = min(requestedSeconds, incomingArrangedDuration)`, `start = max(0, previousEnd + gap - crossfade)`. Each item’s clips start at its item start plus cumulative selected durations. The incoming item owns fade-in; the next item’s crossfade controls the previous item’s fade-out.
4. Add `delivery/render-worker/src/sow_render_worker/arrangements.py`, the small Python mirror required by the separate package boundary. Define immutable `ArrangementStep`, strict JSON parsing, nearest-beat snapping with the same six-decimal/earlier-tie rules, and `validate_arrangement(raw, sections_json, beats_json, recording_duration) -> tuple[ArrangementStep, ...] | None`. Raise `ValueError("Song arrangement is unavailable or stale; repair the Recording analysis or reset the arrangement")` for a present invalid value. Keep NULL distinct from malformed JSON. No equivalent worker parser exists.
5. Add focused behavior tests in `src/test/lib/arrangements.test.ts` and `delivery/render-worker/tests/test_arrangements.py`: nearest/tied beats, unsorted beats, collapsed ranges, repeated indices, strict fields, NULL versus malformed JSON, stale label/index/bounds, 64/65 Steps, arranged totals, short-incoming-item crossfade clamping, and direct internal joins. Use the same numeric fixture in both languages to prove identical snapped output and timeline inputs.

### 2. Persist, validate, copy, and expose arrangements

1. In `delivery/webapp/src/db/schema.ts`, add `arrangementSteps: text("arrangement_steps")` to `songsetItems`. From `delivery/webapp`, run `pnpm exec drizzle-kit generate --name add_songset_item_arrangements` to create migration 0027 and snapshots. Apply to the development database with `pnpm exec drizzle-kit migrate` before UI verification. Do not backfill existing rows.
2. Extend `SongsetItemDetail` and `SongsetItemRecording` in `src/lib/db/songsets.ts` with:
   - item `arrangementSteps: string | null` (raw persisted JSON preserves invalid data for diagnostics/duplication), `arrangement: SongArrangement | null`, `arrangementInvalid: boolean`;
   - recording `sections: RecordingSection[] | null`, `beats: number[]`.
   Select/group by `songsetItems.arrangementSteps`, `recordings.sections`, and `recordings.beats` in `getSongsetEditorData`, `getRenderPageData`, `getSongsetPublicView`, and the legacy `getSongset` used by duplication. Parse through `arrangements.ts`, never ad hoc `JSON.parse` in components.
3. Add `updateSongArrangement(itemId, songsetId, userId, arrangement: SongArrangement | null): Promise<SongsetItemDetail | null>` in `db/songsets.ts`. Load the owned item, its current Recording analysis, and all sibling items. `NULL` is always a valid Reset, even when analysis is missing. For non-NULL, call `validateSongArrangement`; reject stale/unavailable data. Calculate the prospective whole-Songset selected-audio total using the draft for this item and saved values for siblings; reject over 1500 seconds. Serialize canonical JSON, then update `{arrangementSteps, updatedAt: now}` and separately `{songsets.updatedAt: now}`. Both Save and Reset must bump the item timestamp because `mapRenderStateFromSnapshot` uses it to mark Renders stale.
4. Create `src/app/api/songsets/[id]/items/[itemId]/arrangement/route.ts` with `PUT`. Authenticate with `auth.api.getSession`; validate body `{arrangement: SongArrangement | null}` using a strict Zod schema matching the wire contract, then call `updateSongArrangement`. Responses: 401 unauthenticated, 400 malformed schema/JSON, 404 unowned/missing item, 409 `code: "ARRANGEMENT_ANALYSIS_UNAVAILABLE" | "ARRANGEMENT_STALE"` for analysis failures, 422 `code: "SONGSET_TOO_LONG"` for the duration cap, 200 updated item on success. Return the same serialized detail shape as the editor API. Reset succeeds without analysis.
5. Keep arrangement writes out of generic transition PATCH. In `updateSongsetItem`, when `recordingHashPrefix` changes to a different value, also set `arrangementSteps: null` and `updatedAt: new Date()` so bounds can never leak across Recordings. Preserve the arrangement when unrelated transition fields change. Add `arrangementSteps: item.arrangementSteps` verbatim to `duplicateSongset.itemsToInsert`; fresh item IDs and positions remain as today.
6. Replace full-Recording duration calculations with the shared helpers in `getSongsetEditorData`, `getRenderPageData`, `getSongset`, and `getSongsetPublicView`. For `listSongsetSummaries`, aggregate each item’s `arrangement_steps` and original duration as JSON, then calculate arranged totals in TypeScript after the query; do not cast untrusted JSON in SQL where malformed data could break every list. If any arrangement is structurally invalid, expose `durationSeconds: null` rather than a misleading original duration. Public per-item durations and total duration follow the same rule.
7. Add a shared server read/validation helper in `db/songsets.ts`, `getSongsetRenderMetrics(songsetId, userId)`, returning owned visible items, song count, and arranged selected-audio duration or throwing typed `SongsetRenderValidationError(code, message)`. It must run full analysis validation for every non-NULL arrangement. Use it in both `POST src/app/api/render-jobs/route.ts` and `createRenderJob` in `src/lib/render/job-manager.ts`; this also keeps the early route query owner-scoped. Map analysis failures to 409 and over-limit to the existing 400 response. Store the arranged duration in `render_jobs.songsetDurationSeconds`; song count remains item count.

### 3. Render selected audio and rebuild lyrics on the new timeline

1. Extend worker `SongsetItem` in `audio_engine.py` with `arrangement_steps: tuple[ArrangementStep, ...] | None = None`. Add:
   ```python
   @dataclass(frozen=True)
   class AudioClipInfo:
       audio_path: str
       source_start_seconds: float
       source_end_seconds: float
       start_time_seconds: float

       @property
       def duration_seconds(self) -> float: ...
   ```
   Add `clips: tuple[AudioClipInfo, ...]` to `AudioSegmentInfo`; migrate all constructors/tests rather than retaining a second whole-song path. `SongsetItem.duration_seconds` now means arranged duration for that item.
2. In `pipeline.fetch_songset_items`, SELECT `si.arrangement_steps`, `r.sections`, and `r.beats` in addition to current columns. For each row, validate a present arrangement against current analysis using `arrangements.py`; set `duration_seconds` to the sum of selected Steps, otherwise retain full Recording duration. The existing five-item/1500-second check and ETA now automatically use arranged durations. Missing/deleted Recording checks stay intact.
3. In `generate_songset_audio`, download each Recording once, derive either one full-file clip or the saved Steps, and calculate the item timeline before clips. Emit repeated source ranges as separate `AudioClipInfo` entries even when they point to the same downloaded file. Keep gap/crossfade calculations and progress phases unchanged except that durations are arranged. `calculate_total_duration` consumes the same arranged durations.
4. Rework `build_ffmpeg_filter_complex` and `concatenate_audio_files` to flatten item clips as FFmpeg inputs. For each clip use `atrim=start=<sourceStart>:end=<sourceEnd>`, `asetpts=PTS-STARTPTS`; concatenate an item’s clip streams with `concat=n=<stepCount>:v=0:a=1` (one clip can bypass concat), then apply the existing item-level `afade`/`adelay` and final `amix`/`loudnorm`. This gives direct internal joins, one input per repeated occurrence, and existing inter-item overlap behavior. Do not insert `acrossfade` between Steps or change final encoding/normalization.
5. Add `remap_lrc_to_clips(local_lines, clips, title)` in worker `lrc_parser.py`. For every clip, include each LRC line whose original timestamp satisfies `sourceStart <= line.time_seconds < sourceEnd`; emit `global_time_seconds = clip.start_time_seconds + line.time_seconds - sourceStart`, preserve original text/local timestamp, and retain blank Gap Placeholders. Repeated Steps therefore repeat their lyrics at new target times; omitted/out-of-range lines never appear. Sort the combined lines by global time with stable ordering.
6. Use that helper in both `chapters.generate_chapters_manifest` and `VideoEngine.generate_video`, replacing full-LRC `start + line.time` offsets. Chapter start/end remain the item segment start/end (first clip target start through last clip target end), and chapter lines are that item’s remapped lines. Video `all_lyrics` uses exactly the same remapped lines, so repeated lyrics appear in frames and `chapters.json`. Keep the existing chapters/MP4 JSON schemas unchanged; controller, Projection, Share, Offline Copy, and Android require no migration.
7. Update all worker fixtures/callers identified by `rg "SongsetItem\\(|AudioSegmentInfo\\(" delivery/render-worker`. Add behavior tests:
   - `test_audio_engine.py`: repeated/reordered clips have correct source/target times; internal gaps are zero; inter-item gaps/crossfades are unchanged; short arranged incoming duration clamps crossfade; FFmpeg command/filter contains trims and per-item concat.
   - `test_pipeline.py`: NULL renders full duration, valid arrangement renders selected duration, stale/malformed arrangement raises the explicit repair-or-reset error, arranged duration enforces 1500 seconds.
   - `test_chapters.py` and `test_video_engine.py`: a repeated section duplicates only its in-range lyric lines at the second target, an omitted section contributes none, and a boundary timestamp at `end` belongs to the next clip.

### 4. Add same-origin source audio for browser mixing

1. Create `src/app/api/recordings/[hashPrefix]/audio/route.ts`; existing signed R2 URLs cannot support browser `fetch`/Web Audio because the bucket is not CORS-configured. Authenticate the session and load a nondeleted Recording with `hashPrefix` and visibility `published` or `review`; otherwise return the same 401/404 policy as signed URLs.
2. Mint a 60-second R2 URL for `{hashPrefix}/audio.mp3`, forward the incoming `Range` header, and stream `r2Response.body`. Mirror `/api/r2/artifact/[...path]/route.ts`: propagate status 200/206, `Content-Length`, `Content-Range`, `Accept-Ranges: bytes`, `Content-Type: audio/mpeg`, and `Cache-Control: private, max-age=3600`; return 502 for upstream failure and 503 when R2 is unconfigured. Do not buffer the MP3 in Next.js and do not route it through the service-worker Render artifact cache.
3. Add route tests for auth/visibility, Range forwarding and 206 headers, upstream errors, and body streaming. The browser mixer uses `/api/recordings/${hashPrefix}/audio`, never a cross-origin R2 URL.

### 5. Implement the draft editor and whole-Songset preview

1. Extend `ApiSongsetItem`, `transformItems`, `SongListItem`, and the server mapping in `app/songsets/[id]/page.tsx` with arrangement/analysis fields. `SongListItem.recording.durationSeconds` remains original duration; add `arrangedDurationSeconds` for row and total display. Show an “Arranged” badge when non-NULL and use arranged duration in `SongList` and `SongsetEditor`’s limit calculation. Invalid arrangements show the analysis warning rather than original duration as though current.
2. Add `onEditArrangement(itemId)` through `SongsetEditor` and `SongList`. Place an accessible Arrange button on each editable row, including mobile (do not copy the desktop-only transition-button visibility). If analysis is unavailable and no arrangement exists, disable it with explanatory text. If a saved arrangement is invalid/stale, keep it enabled so the user can open the explanation and Reset.
3. Create `src/components/songset/ArrangementEditorSheet.tsx`, mounted by `SongsetEditorClient` beside the existing dynamically loaded sheets. Use the existing `Sheet`/`SheetContent side="bottom"` pattern from `BrowseSheet`, with a 90vh scrollable body and fixed footer. Props include the selected item, all current items, `onSave(arrangement | null)`, and `onOpenChange`.
4. On open, initialize the draft from the saved arrangement or `buildSnappedSteps` over every source section. Display a source-section palette and the ordered draft. Source labels include per-label occurrence numbers derived from source order (for example Verse 1, Verse 2). Palette Add appends a Step; each draft occurrence has Duplicate and Remove; removing one occurrence leaves other occurrences available. Use the existing `@dnd-kit/core`, `@dnd-kit/sortable`, PointerSensor/KeyboardSensor, `sortableKeyboardCoordinates`, `verticalListSortingStrategy`, and `arrayMove` pattern from `SongList` for draft reorder. Empty drafts and >64 Steps disable Preview/Save with inline validation. Reset sets the saved value to NULL after confirmation; it is not merely selecting every source section.
5. Any draft mutation stops active preview and marks the sheet dirty. Closing/Cancel with unsaved changes opens an AlertDialog offering Keep editing or Discard; closing after Save needs no prompt. Save calls the arrangement PUT once, updates only that item plus arranged total in client state, calls the existing `markStale()`, closes on success, and keeps the draft open with a translated error on failure. Do not autosave or enqueue Render.
6. Create `src/lib/audio/arrangement-preview.ts` and `src/hooks/useArrangementPreview.ts`; there is no existing Web Audio scheduler. Use **streaming media-element sources**, not `decodeAudioData` of full Recordings (25 minutes of stereo 44.1kHz PCM alone is about 529 MB). Create one `<audio preload="auto">`, `MediaElementAudioSourceNode`, and `GainNode` per Songset Item, all connected to one `AudioContext.destination`. This bounds elements to five and supports overlapping items.
7. Build the preview timeline with `buildArrangementTimeline` using the current draft for the selected item and saved values for siblings. Reject any invalid sibling arrangement instead of substituting original audio. Load all required same-origin audio metadata before enabling playback. Create the item elements/nodes at preview start and retain them for that playback session; create the `MediaElementAudioSourceNode` exactly once per element. Set initial gain to 1 when no incoming crossfade applies. Otherwise schedule `cancelScheduledValues(contextStart)`, `setValueAtTime(0, contextStart)`, and `linearRampToValueAtTime(1, contextStart + incomingCrossfadeSeconds)`. For each item with a successor crossfade, also schedule `setValueAtTime(1, itemEnd - successorCrossfadeSeconds)` and `linearRampToValueAtTime(0, itemEnd)`; cancel/rebuild these ramps on every seek/resume so pre-existing automation cannot accumulate.
8. Drive transport from a monotonic context-time origin, not `HTMLAudioElement.currentTime` totals: `timelinePosition = context.currentTime - playStartedAtContextTime + playStartedAtTimelineSeconds`, clamped to total duration. Use a 25 ms scheduler timer plus media `ended`/`error` handling. At each tick, determine the active clip for each item; when a direct Step boundary is crossed, assign `audio.currentTime = nextClip.sourceStartSeconds + max(0, timelinePosition - nextClip.startTimeSeconds)`, pause at item end, and start future items at their item start after awaiting the user-gesture-unlocked `AudioContext.resume()`. Start elements with `audio.play()` from timer callbacks and catch rejected play promises into the preview error state. There is intentionally no native Web Audio scheduled `play()` API: media-element `currentTime` switches and play/pause are the accepted near-render approximation, while gain ramps remain sample-scheduled. Internal joins never change gain.
9. Provide Play/Pause, Stop, a seek slider, elapsed/total time, and per-song “Start here” controls. Pause captures the timeline position and pauses all elements; resume and seek pause current elements, recompute every clip/item active at the requested timeline time, set source offsets correctly, reset gain automation relative to the new context origin, and resume future scheduling. Gap seeks may have no active element. Stop, draft changes, sheet close/unmount, and new preview attempts clear timers/listeners, pause elements, disconnect nodes, and close the owned AudioContext. Starting preview stops the global `AudioPlayerContext`; arrangement playback never calls `markSongCompleted` and never masquerades as a `song` track.
10. Add an `arrangement` message bundle under `src/lib/i18n/messages/`, register it in `messages.ts`, and supply every label/error in English and Traditional Chinese. Include boundary/snap explanation, original/arranged durations, preview loading/failure, analysis repair/reset, dirty-close confirmation, and accessible drag/duplicate/remove/preview controls.
11. Add component/hook tests with mocked media elements and a fake AudioContext: initial full draft, omit/repeat/reorder, reset NULL, dirty Cancel, successful and failed Save, analysis-disabled behavior, pause/resume/seek source offsets, draft-change cancellation, crossfade ramp values, and no Completion write. These tests assert observable scheduling/state, not source wiring.

## Critical files & anchors

- `delivery/webapp/src/lib/arrangements.ts` (new): canonical parsing, beat snapping, validation, durations, and preview timeline; all other webapp layers reuse it.
- `delivery/webapp/src/lib/db/songsets.ts` (`getSongsetEditorData`, `updateSongsetItem`, `duplicateSongset`, list/read helpers): ownership, timestamp staleness, Recording swaps, duration and duplication semantics converge here.
- `delivery/render-worker/src/sow_render_worker/audio_engine.py` (`generate_songset_audio`, `build_ffmpeg_filter_complex`): arranged clip timeline and authoritative FFmpeg trim/crossfade behavior.
- `delivery/render-worker/src/sow_render_worker/lrc_parser.py` plus its two consumers (`chapters.py`, `video_engine.py`): one source-range-to-target-timeline lyric remap prevents audio/video/chapter divergence.
- `delivery/webapp/src/hooks/useArrangementPreview.ts` (new): streaming Web Audio transport, direct Step switches, pause/seek cleanup; do not modify the global song player to emulate this.

## Verification

1. Run focused webapp tests from `delivery/webapp`:
   ```bash
   pnpm test -- src/test/lib/arrangements.test.ts src/test/api/songsets/items.test.ts src/test/api/render-jobs/route.test.ts
   pnpm test
   pnpm typecheck
   pnpm lint
   ```
   New-behavior proof: saving a repeated Step persists identical source ranges twice and bumps item `updatedAt`; a second save after changing stored beats returns 409; changing Recording clears arrangement; duplicate preserves identical raw arrangement; a 1501-second selected total is rejected while an arrangement with six Songs’ worth of Steps but five items still passes song-count validation.
2. Run worker tests from `delivery/render-worker`:
   ```bash
   uv run --extra dev pytest tests/test_arrangements.py tests/test_audio_engine.py tests/test_chapters.py tests/test_video_engine.py tests/test_pipeline.py -v
   uv run --extra dev pytest tests/ -v
   ```
   New-behavior proof uses one source LRC at 1, 11, 21 seconds and Steps `[10,20)`, `[0,10)`, `[10,20)`: remapped lyric text must occur in order “line at 11, line at 1, line at 11,” with no full-LRC offset residue.
3. Exercise the actual FFmpeg path with a throwaway local fixture (remove it afterward): generate a 30-second stereo MP3 with distinguishable tones at 0–10, 10–20, and 20–30 seconds; construct one `SongsetItem` with Steps `[10,20)`, `[0,10)`, `[10,20)` and call real `generate_songset_audio` using a fake fetcher returning that file. Run `ffprobe` on `output.mp3`: expected duration 30 seconds. Analyze each 10-second third (tone frequency or RMS fingerprint): expected order is middle, beginning, middle, proving trim/repeat/reorder rather than inspecting filter strings. Feed the same clips plus an LRC through real chapter generation and assert the three repeated/remapped lyric groups.
4. Apply migration 0027 to the development database and run the actual webapp on port 8080 with `/opt/sow/.env`. Identify a visible Recording with non-null sections/beats, create a test Songset containing it, and use `SOW_WEBAPP_TESTUSER_LOGIN` / `SOW_WEBAPP_TESTUSER_PASSWORD` to sign in through the real UI. Reuse a running dev server; otherwise start `pnpm dev:https` from `delivery/webapp`.
5. In cert-tolerant headless Chrome (`--ignore-certificate-errors`, CDP port 9222), open the Songset editor and choose Arrange. Remove one section, duplicate a chorus, and drag it before another section. Start whole-Songset preview: observe playing state, progressing timeline, correct current song/clip sequence, direct repeats, pause/resume, and seeking into the repeated clip. Inspect the actual media elements in `tab.run` to confirm their `currentTime` source offsets match the draft and that the proxy returns 206 for Range requests. Cancel and reopen: original saved sequence returns. Repeat edits, Save, reload: sequence and arranged duration persist and the existing Render becomes stale. Duplicate the Songset and confirm the sequence persists. Reset, reload, and confirm original full-Recording behavior returns.
6. Verify failure behavior in the browser with missing-beats fixture data: Arrange is unavailable for an untouched item; for an existing stale arrangement, Preview and new Render are blocked with the repair-or-reset message while Reset remains available. Test at 390 CSS px and desktop width: palette and sequence remain scrollable, all row/editor actions are reachable, and no fixed footer overlaps the draft list.
