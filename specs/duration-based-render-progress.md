# Duration-Based Render Progress Estimation

## Problem

The current render progress UI has two major issues:

1. **"Overall progress" is jumpy and misleading.** Progress is tracked via fixed phase milestones (5%, 30%, 60%, 80%, 95%) with micro-progress callbacks only in `mixing_audio`, `encoding_video`, and `uploading` phases. The `rendering_frames` phase (60-80%) has no sub-progress during `videoEngine.initialize()`, causing the progress bar to stall for long periods at 60-70% then jump forward.

2. **"Estimated remaining" is wildly inaccurate.** The estimate uses linear extrapolation: `(elapsedSeconds / percentComplete) * (100 - percentComplete)`. When progress is stalled at a fixed percentage, elapsed time grows while percentComplete stays flat, causing the estimate to *increase* over time. When progress jumps, the estimate suddenly drops. This is the classic failure mode of rate-based extrapolation on non-linear progress.

## Solution

Replace the jumpy phase-based progress and rate-based ETA with a **duration-based estimate**. Since render time is roughly proportional to total audio duration (with some variation by resolution and video/audio mode), we can:

1. Compute `estimatedTotalSeconds` at pipeline start based on total song duration × render ratio
2. Drive the progress bar as `elapsedSeconds / estimatedTotalSeconds` — smooth and monotonically increasing
3. Learn the render ratio from historical completed jobs, improving accuracy over time

### UI Before

```
Step 2 of 5  [●●○○○]
Overall progress                    25%
[████░░░░░░░░░░░░░░░░░░░░░░]
Elapsed          Estimated remaining
30s              2m 0s
```

### UI After

```
Step 2 of 5  [●●○○○]
[████████░░░░░░░░░░░░░░░░░]  30s / ~3m 0s
```

The progress bar smoothly fills based on elapsed time vs estimated total. No jumpy micro-progress, no wildly inaccurate "remaining" estimate. The ratio improves over time as more renders complete.

---

## Phase 0: Determine Render Ratio (Benchmarks)

Before implementing, we need empirical data to set default render ratios.

**Approach:** Run test renders with 2, 3, 5, and 8 songs of varying durations. Measure actual render time vs total audio duration. Compute the ratio for each configuration and extrapolate.

**Test matrix:**

| Config | Songs | Durations (approx) | Total Audio | Expected Render Time |
|--------|-------|---------------------|-------------|---------------------|
| A | 2 | 3m + 4m | 7m | ? |
| B | 3 | 3m + 4m + 5m | 12m | ? |
| C | 5 | 2m + 3m + 4m + 5m + 6m | 20m | ? |
| D | 8 | 2m + 2.5m + 3m + 3.5m + 4m + 4.5m + 5m + 6m | 30.5m | ? |

Run each config at both 720p and 1080p, with video enabled and audio-only. This gives us 4×3 = 12 data points to establish baseline ratios.

**If no historical jobs exist yet**, use conservative defaults:

```typescript
const DEFAULT_RENDER_RATIOS = {
  "720p_video": 1.0,   // render takes ~as long as audio duration
  "720p_audio": 0.3,   // audio-only is much faster
  "1080p_video": 1.5,  // 1080p video is slower
  "1080p_audio": 0.3,  // audio-only same regardless of resolution
};
```

These will be refined by the historical learning system as jobs complete.

---

## Phase 1: Schema Changes

### `src/db/schema.ts` — Add two new columns to `renderJobs`

| Column | Type | Purpose |
|--------|------|---------|
| `estimatedTotalSeconds` | `real("estimated_total_seconds")` | Pre-computed estimated total render time |
| `totalDurationSeconds` | `real("total_duration_seconds")` | Total audio duration of the songset (for historical ratio computation) |

Keep existing `percentComplete`, `estimatedSecondsLeft`, `elapsedSeconds` columns. No migration breakage — old columns remain in DB but are no longer written by the pipeline or displayed in the UI.

### Migration

Run `npx drizzle-kit generate` to create the migration for the two new columns.

---

## Phase 2: Render Ratio Module

### New file: `src/lib/render/render-ratio.ts`

**`getRenderRatio(resolution: string, videoEnabled: boolean): Promise<number>`**

Queries completed render jobs and computes the average ratio of `actualElapsedSeconds / totalDurationSeconds` for matching resolution + video mode. Falls back to `DEFAULT_RENDER_RATIOS` if fewer than 3 historical jobs exist for the given config.

**Query logic:**

```sql
SELECT AVG(
  EXTRACT(EPOCH FROM (completed_at - created_at)) / total_duration_seconds
) AS ratio
FROM render_jobs
WHERE status = 'completed'
  AND total_duration_seconds IS NOT NULL
  AND total_duration_seconds > 0
  AND resolution = $1
  AND video_enabled = $2
```

**Fallback logic:**

- If 0-2 historical jobs: use `DEFAULT_RENDER_RATIOS[configKey]`
- If 3+ historical jobs: use computed average
- If computed average is unreasonably high (>5.0) or low (<0.05): fall back to default (guards against outliers from very short songs or crashed jobs)

No separate `updateRenderRatio` function is needed — the ratio is computed on-the-fly from completed jobs each time a new render starts.

---

## Phase 3: Pipeline Changes

### `src/lib/render/pipeline.ts`

1. **Remove** `computeTimerValues()` function (lines 64-74)
2. **Remove** `percent` field from `PHASES` array — keep only `phase` for step indicator
3. **After fetching songset items** (line 109), compute:
   - `totalDurationSeconds` = sum of all items' `durationSeconds` (null fallback to 0)
   - `estimatedTotalSeconds` = `totalDurationSeconds * await getRenderRatio(job.resolution, job.videoEnabled)`
4. **Store** both values in the job via `updateRenderProgress` at pipeline start
5. **On each progress update**, compute `elapsedSeconds = (Date.now() - pipelineStartTime) / 1000` and store it
6. **Remove** all `percentComplete` and `estimatedSecondsLeft` calculations from progress updates
7. **Keep** phase/phaseIndex updates for the step indicator
8. **On completion**, `completeRenderJob` already stores `completedAt`; the actual elapsed time is derivable from `completedAt - createdAt`

**Updated progress update calls** (example):

```typescript
// Before:
await updateRenderProgress(jobId, userId, {
  phase: PHASES[0].phase,
  phaseIndex: 0,
  totalPhases: PHASES.length,
  percentComplete: PHASES[0].percent,
  ...computeTimerValues(pipelineStartTime, PHASES[0].percent),
});

// After:
await updateRenderProgress(jobId, userId, {
  phase: PHASES[0].phase,
  phaseIndex: 0,
  totalPhases: PHASES.length,
  estimatedTotalSeconds,
  totalDurationSeconds,
  elapsedSeconds: (Date.now() - pipelineStartTime) / 1000,
});
```

**Remove micro-progress callbacks** from `audioEngine.generateSongsetAudio`, `videoEngine.generateVideo`, and `uploader.uploadRenderArtifacts`. These callbacks were only used to update `percentComplete` and `estimatedSecondsLeft`, which are no longer needed. The phase transitions still happen at the right times.

> **Note:** We could keep the micro-progress callbacks for future use (e.g., within-phase progress indicators), but for now they add complexity without benefit. Remove them and re-add later if needed.

---

## Phase 4: Job Manager Changes

### `src/lib/render/job-manager.ts`

1. **Add** `estimatedTotalSeconds: number | null` and `totalDurationSeconds: number | null` to `RenderJob` interface
2. **Add** `estimatedTotalSeconds?: number` and `totalDurationSeconds?: number` to `RenderProgress` partial update type
3. **Update** `mapRowToRenderJob` to map the new columns
4. **Update** `updateRenderProgress` to handle the new fields
5. **Update** `completeRenderJob` to compute and store final `elapsedSeconds` from `createdAt` to now (currently `elapsedSeconds` is only updated during progress updates, not at completion)

---

## Phase 5: SSE Event Changes

### `src/app/api/render-jobs/[id]/events/route.ts`

Update `SSEEvent` interface:

```typescript
export interface SSEEvent {
  phase: RenderPhase;
  phaseIndex: number;
  totalPhases: number;
  estimatedTotalSeconds: number;
  elapsedSeconds: number;
}
```

**Removed:** `percentComplete`, `estimatedSecondsLeft`
**Added:** `estimatedTotalSeconds`

The front-end computes `percentComplete = Math.min(100, (elapsedSeconds / estimatedTotalSeconds) * 100)`.

**Update polling logic:**

- Initial event: read `estimatedTotalSeconds` and `elapsedSeconds` from job row
- Progress events: same
- Final event (completed): set `estimatedTotalSeconds` to actual elapsed (since we now know the real duration), `elapsedSeconds` to actual elapsed

---

## Phase 6: Front-End Component Changes

### `src/components/render/RenderProgress.tsx`

1. **Update** `RenderProgressData` interface to match new SSE event shape (remove `percentComplete`/`estimatedSecondsLeft`, add `estimatedTotalSeconds`)
2. **Compute** `percentComplete` client-side: `Math.min(100, (elapsedSeconds / estimatedTotalSeconds) * 100)`
3. **Remove** "Estimated remaining" label and value
4. **Remove** "Overall progress" label
5. **Keep** progress bar, now driven by `elapsed / estimatedTotal` — smooth and monotonically increasing
6. **Change** time display from 2-column grid to single row showing elapsed vs estimated total:
   - Format: `30s / ~3m 0s` (elapsed / estimated total)
   - Use `formatDuration()` for both values, prefix estimated total with `~`
7. **Keep** step indicator (phase dots + "Step X of Y") unchanged
8. **Keep** cancel button unchanged

**Updated layout:**

```tsx
{/* Progress bar */}
<div className="space-y-2">
  <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
    <div
      className="h-full rounded-full bg-primary transition-all duration-1000"
      style={{ width: `${percentComplete}%` }}
    />
  </div>
  <div className="flex items-center justify-between text-sm text-muted-foreground">
    <span>{formatDuration(elapsedSeconds)}</span>
    <span>~{formatDuration(estimatedTotalSeconds)}</span>
  </div>
</div>
```

Note: `transition-all duration-1000` instead of `duration-300` — since progress is now time-based and smooth, a longer transition duration prevents visual jitter from polling intervals.

---

## Phase 7: Test Updates

### `src/test/db/schema.test.ts`

Add assertions for `estimated_total_seconds` and `total_duration_seconds` columns in the "has status tracking columns" test.

### `src/test/components/render/RenderProgress.test.tsx`

- Remove assertion for "Overall progress" text (line 78)
- Remove assertion for "Estimated remaining" text (line 84)
- Update SSE event data shape in tests (remove `percentComplete`/`estimatedSecondsLeft`, add `estimatedTotalSeconds`)
- Remove assertion for "25%" text (line 128) — percentage is no longer displayed as text
- Add assertions for elapsed/total time display

### `src/test/api/render-jobs/events.test.ts`

- Update `mockQueuedJob` and `mockRunningJob` with `estimatedTotalSeconds` and `totalDurationSeconds` fields
- Remove `percentComplete` and `estimatedSecondsLeft` from mock objects
- Update SSE event assertions to check for `estimatedTotalSeconds` instead of `percentComplete`/`estimatedSecondsLeft`

### `src/test/api/render-jobs/[id].test.ts`

- Update `mockJob` with new fields
- Update completed job assertions (remove `percentComplete: 100` check, or keep it since the DB column still exists)

### `src/test/api/render-jobs/route.test.ts`

- Update mock job objects with `estimatedTotalSeconds` and `totalDurationSeconds` fields

### `src/test/lib/render/pipeline.test.ts`

- Update `mockJob` with new fields
- Verify that `updateRenderProgress` is called with `estimatedTotalSeconds` and `totalDurationSeconds`
- Remove assertions about `percentComplete` values in progress updates

### `src/test/lib/render/job-manager.test.ts`

- Update mock objects with new fields
- Add test for `estimatedTotalSeconds` and `totalDurationSeconds` in progress updates

---

## Phase 8: New Tests

### `src/test/lib/render/render-ratio.test.ts`

Test the new `getRenderRatio` module:

- Returns default ratio when no historical jobs exist
- Returns default ratio when <3 historical jobs exist
- Returns computed average when 3+ historical jobs exist
- Falls back to default when computed average is unreasonably high (>5.0)
- Falls back to default when computed average is unreasonably low (<0.05)
- Correctly differentiates by resolution and videoEnabled

---

## Files Changed Summary

| File | Action |
|------|--------|
| `src/db/schema.ts` | Add `estimatedTotalSeconds`, `totalDurationSeconds` columns |
| `src/lib/render/render-ratio.ts` | **New file** — render ratio computation with historical learning |
| `src/lib/render/pipeline.ts` | Remove `computeTimerValues`, remove `percentComplete`/`estimatedSecondsLeft` from progress updates, add duration estimation at pipeline start |
| `src/lib/render/job-manager.ts` | Add new fields to interfaces, update mapping and progress updates |
| `src/app/api/render-jobs/[id]/events/route.ts` | Update SSE event shape |
| `src/components/render/RenderProgress.tsx` | Simplify UI — remove "Overall progress" label and "Estimated remaining", show elapsed/total |
| `src/test/db/schema.test.ts` | Add column assertions |
| `src/test/components/render/RenderProgress.test.tsx` | Update assertions |
| `src/test/api/render-jobs/events.test.ts` | Update mock data and assertions |
| `src/test/api/render-jobs/[id].test.ts` | Update mock data |
| `src/test/api/render-jobs/route.test.ts` | Update mock data |
| `src/test/lib/render/pipeline.test.ts` | Update mock data and assertions |
| `src/test/lib/render/job-manager.test.ts` | Update mock data, add new field tests |
| `src/test/lib/render/render-ratio.test.ts` | **New file** — render ratio tests |

---

## Implementation Order

1. Phase 0: Run benchmark renders to determine baseline ratios
2. Phase 1: Schema changes + migration
3. Phase 2: Render ratio module (`render-ratio.ts`)
4. Phase 3: Pipeline changes
5. Phase 4: Job manager changes
6. Phase 5: SSE event changes
7. Phase 6: Front-end component changes
8. Phase 7: Update existing tests
9. Phase 8: Add new tests for render ratio
10. Run full test suite, lint, typecheck
11. Push
