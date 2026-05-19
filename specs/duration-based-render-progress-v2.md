# Duration-Based Render Progress Estimation (v2)

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

## Changes from v1

| # | Issue | v1 | v2 |
|---|-------|----|----|
| 1 | `totalDurationSeconds` from nullable metadata | Sum of `item.durationSeconds` (nullable, may be 0) | Use ffprobe-derived `audioResult.totalDurationSeconds`; update job row after audio mixing phase |
| 2 | Ratio includes queue wait time | `completed_at - created_at` | New `startedAt` column; ratio uses `completed_at - started_at` |
| 3 | `elapsedSeconds` stale at completion | Derive from `createdAt` | Derive from `startedAt`; `completeRenderJob` writes final `elapsedSeconds` |
| 4 | Progress bar stalls at 100% | `Math.min(100, ...)` cap | Dynamic estimate adjustment when elapsed exceeds estimate; bar caps at 99% until completion |
| 5 | Concurrent renders skew ratio | Not addressed | Documented as known limitation; `AVG` naturally smooths over mixed-load data |
| 6 | SSE terminal events for failed/cancelled | Only specified for completed | Specify `estimatedTotalSeconds`/`elapsedSeconds` for all terminal states; add `status` field to SSE event |
| 7 | CSS transition too slow | `duration-1000` | `duration-500` — matches 1s polling without perceptible lag |
| 8 | Conservative defaults | `720p_video: 1.0` | `720p_video: 1.5` — overestimate slightly so bar rarely hits 100% early on |
| 9 | Cancelled→failed bug | Not addressed | Fix: `catch` block checks `job.status === "cancelled"` before calling `failRenderJob` |
| 10 | Unknown resolution crashes ratio | No fallback | `getRenderRatio` returns most conservative default if config key not found |
| 11 | Deprecated columns confusing | Silent deprecation | `@deprecated` JSDoc on schema columns; migration comment |
| 12 | PostgreSQL-specific `EXTRACT(EPOCH)` | Used without comment | Keep (app is firmly Neon Postgres) but add code comment noting the dependency |

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

**If no historical jobs exist yet**, use conservative defaults (overestimate slightly — better to fill slowly than stall at 100%):

```typescript
const DEFAULT_RENDER_RATIOS: Record<string, number> = {
  "720p_video": 1.5,   // overestimate slightly
  "720p_audio": 0.5,
  "1080p_video": 2.0,
  "1080p_audio": 0.5,
};
```

These will be refined by the historical learning system as jobs complete.

---

## Phase 1: Schema Changes

### `src/db/schema.ts` — Add three new columns to `renderJobs`

| Column | Type | Purpose |
|--------|------|---------|
| `estimatedTotalSeconds` | `real("estimated_total_seconds")` | Pre-computed estimated total render time |
| `totalDurationSeconds` | `real("total_duration_seconds")` | Total audio duration of the songset (ffprobe-derived, for historical ratio computation) |
| `startedAt` | `timestamp("started_at", { withTimezone: true })` | When the pipeline actually began executing (vs `createdAt` which is when the job was queued) |

Mark existing columns as deprecated:

```typescript
/** @deprecated No longer written by pipeline. Retained for historical data only. */
percentComplete: real("percent_complete").default(0),
/** @deprecated No longer written by pipeline. Retained for historical data only. */
estimatedSecondsLeft: real("estimated_seconds_left"),
```

Keep existing `percentComplete`, `estimatedSecondsLeft`, `elapsedSeconds` columns. No migration breakage — old columns remain in DB but are no longer written by the pipeline or displayed in the UI.

### Migration

Run `npx drizzle-kit generate` to create the migration for the three new columns. Add a comment in the migration file noting that `percent_complete` and `estimated_seconds_left` are deprecated.

---

## Phase 2: Render Ratio Module

### New file: `src/lib/render/render-ratio.ts`

**`getRenderRatio(resolution: string, videoEnabled: boolean): Promise<number>`**

Queries completed render jobs and computes the average ratio of `actualRenderSeconds / totalDurationSeconds` for matching resolution + video mode. Falls back to `DEFAULT_RENDER_RATIOS` if fewer than 3 historical jobs exist for the given config.

**Query logic** (uses `started_at` to exclude queue wait time):

```sql
-- PostgreSQL-specific: EXTRACT(EPOCH FROM) is Neon/PG-only
SELECT AVG(
  EXTRACT(EPOCH FROM (completed_at - started_at)) / total_duration_seconds
) AS ratio
FROM render_jobs
WHERE status = 'completed'
  AND started_at IS NOT NULL
  AND total_duration_seconds IS NOT NULL
  AND total_duration_seconds > 0
  AND resolution = $1
  AND video_enabled = $2
```

**Fallback logic:**

- If 0-2 historical jobs: use `DEFAULT_RENDER_RATIOS[configKey]`
- If 3+ historical jobs: use computed average
- If computed average is unreasonably high (>5.0) or low (<0.05): fall back to default (guards against outliers from very short songs or crashed jobs)
- If config key not found in `DEFAULT_RENDER_RATIOS` (e.g. unknown resolution): return the most conservative (highest) default value

```typescript
function getDefaultRatio(resolution: string, videoEnabled: boolean): number {
  const key = `${resolution}_${videoEnabled ? "video" : "audio"}`;
  if (key in DEFAULT_RENDER_RATIOS) return DEFAULT_RENDER_RATIOS[key];
  return Math.max(...Object.values(DEFAULT_RENDER_RATIOS));
}
```

No separate `updateRenderRatio` function is needed — the ratio is computed on-the-fly from completed jobs each time a new render starts.

**Known limitation:** When multiple renders run concurrently, each competes for CPU/FFmpeg, causing renders to take longer than the historical ratio predicts. The `AVG` computation naturally smooths over mixed-load data, but the progress bar may still underestimate during concurrent load. This is acceptable for now.

---

## Phase 3: Pipeline Changes

### `src/lib/render/pipeline.ts`

1. **Remove** `computeTimerValues()` function (lines 64-74)
2. **Remove** `percent` field from `PHASES` array — keep only `phase` for step indicator
3. **At pipeline start** (preparing phase), set `startedAt = new Date()` and store it
4. **After fetching songset items**, compute an initial estimate:
   - `totalDurationSeconds` = sum of all items' `durationSeconds` (null fallback to 0)
   - `estimatedTotalSeconds` = `totalDurationSeconds * await getRenderRatio(job.resolution, job.videoEnabled)`
   - If `totalDurationSeconds === 0` (all items have null duration), set `estimatedTotalSeconds = 0` — the front-end will show an indeterminate progress bar
5. **Store** `estimatedTotalSeconds`, `totalDurationSeconds`, and `startedAt` in the job via `updateRenderProgress` at pipeline start
6. **After audio mixing completes**, update with the ffprobe-derived ground truth:
   - `totalDurationSeconds` = `audioResult.totalDurationSeconds` (from ffprobe, always accurate)
   - `estimatedTotalSeconds` = `totalDurationSeconds * await getRenderRatio(job.resolution, job.videoEnabled)` (recomputed with accurate duration)
7. **On each progress update**, compute `elapsedSeconds = (Date.now() - pipelineStartTime) / 1000` and store it
8. **Remove** all `percentComplete` and `estimatedSecondsLeft` calculations from progress updates
9. **Keep** phase/phaseIndex updates for the step indicator
10. **On completion**, `completeRenderJob` stores final `elapsedSeconds` derived from `startedAt` to now (not `createdAt` — see Phase 4)
11. **Fix cancelled→failed bug**: in the `catch` block, check if the job was cancelled before calling `failRenderJob`

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
  startedAt: new Date(),
  elapsedSeconds: 0,
});
```

**Audio mixing completion update** (new):

```typescript
// After audioEngine.generateSongsetAudio completes:
const accurateTotalDuration = audioResult.totalDurationSeconds;
const accurateEstimatedTotal = accurateTotalDuration * await getRenderRatio(job.resolution, job.videoEnabled);
await updateRenderProgress(jobId, userId, {
  totalDurationSeconds: accurateTotalDuration,
  estimatedTotalSeconds: accurateEstimatedTotal,
  elapsedSeconds: (Date.now() - pipelineStartTime) / 1000,
});
```

**Fix cancelled→failed bug** (in the catch block):

```typescript
} catch (err) {
  const currentJob = await getRenderJob(jobId, userId);
  if (currentJob?.status === "cancelled") {
    return;
  }
  await failRenderJob(jobId, userId, err instanceof Error ? err.message : "Unknown error");
}
```

**Remove micro-progress callbacks** from `audioEngine.generateSongsetAudio`, `videoEngine.generateVideo`, and `uploader.uploadRenderArtifacts`. These callbacks were only used to update `percentComplete` and `estimatedSecondsLeft`, which are no longer needed. The phase transitions still happen at the right times.

> **Note:** We could keep the micro-progress callbacks for future use (e.g., within-phase progress indicators), but for now they add complexity without benefit. Remove them and re-add later if needed.

---

## Phase 4: Job Manager Changes

### `src/lib/render/job-manager.ts`

1. **Add** `estimatedTotalSeconds: number | null`, `totalDurationSeconds: number | null`, and `startedAt: Date | null` to `RenderJob` interface
2. **Add** `estimatedTotalSeconds?: number`, `totalDurationSeconds?: number`, and `startedAt?: Date` to `RenderProgress` partial update type
3. **Update** `mapRowToRenderJob` to map the new columns
4. **Update** `updateRenderProgress` to handle the new fields (including `startedAt`)
5. **Update** `completeRenderJob` to compute and store final `elapsedSeconds` from `startedAt` to now (not `createdAt` — `startedAt` excludes queue wait time). Also store `completedAt` as before.

```typescript
// In completeRenderJob:
const job = await getRenderJob(id, userId);
if (!job) return null;
const finalElapsedSeconds = job.startedAt
  ? (now.getTime() - job.startedAt.getTime()) / 1000
  : null;

const [updated] = await db
  .update(renderJobs)
  .set({
    status: "completed",
    phase: "completed",
    phaseIndex: TOTAL_PHASES,
    percentComplete: 100,
    elapsedSeconds: finalElapsedSeconds,
    mp3R2Key: output.mp3R2Key ?? null,
    mp4R2Key: output.mp4R2Key ?? null,
    chaptersR2Key: output.chaptersR2Key ?? null,
    completedAt: now,
    updatedAt: now,
  })
  .where(and(eq(renderJobs.id, id), eq(renderJobs.userId, userId)))
  .returning();
```

---

## Phase 5: SSE Event Changes

### `src/app/api/render-jobs/[id]/events/route.ts`

Update `SSEEvent` interface — add `status` and `errorMessage` so the client can distinguish terminal states:

```typescript
export interface SSEEvent {
  phase: RenderPhase;
  phaseIndex: number;
  totalPhases: number;
  estimatedTotalSeconds: number;
  elapsedSeconds: number;
  status: "queued" | "running" | "completed" | "failed" | "cancelled";
  errorMessage?: string;
}
```

**Removed:** `percentComplete`, `estimatedSecondsLeft`
**Added:** `estimatedTotalSeconds`, `status`, `errorMessage`

The front-end computes `percentComplete` client-side (see Phase 6).

**Update polling logic:**

- **Initial event:** read `estimatedTotalSeconds`, `elapsedSeconds`, `status` from job row
- **Progress events:** same
- **Terminal event (completed):** `estimatedTotalSeconds` = actual elapsed (since we now know the real duration), `elapsedSeconds` = actual elapsed, `status = "completed"`
- **Terminal event (failed):** `estimatedTotalSeconds` unchanged, `elapsedSeconds` = actual elapsed, `status = "failed"`, `errorMessage` included
- **Terminal event (cancelled):** `estimatedTotalSeconds` unchanged, `elapsedSeconds` = actual elapsed, `status = "cancelled"`

---

## Phase 6: Front-End Component Changes

### `src/components/render/RenderProgress.tsx`

1. **Update** `RenderProgressData` interface to match new SSE event shape (remove `percentComplete`/`estimatedSecondsLeft`, add `estimatedTotalSeconds`, `status`, `errorMessage`)
2. **Compute** `percentComplete` client-side with dynamic estimate adjustment:

```typescript
let percentComplete: number;
let displayEstimatedTotal: number;

if (estimatedTotalSeconds <= 0) {
  // No duration info available — show indeterminate bar
  percentComplete = 0;
  displayEstimatedTotal = 0;
} else if (elapsedSeconds > estimatedTotalSeconds) {
  // Elapsed exceeded estimate — re-estimate to avoid stalling at 100%
  displayEstimatedTotal = elapsedSeconds * 1.1;
  percentComplete = Math.min(99, (elapsedSeconds / displayEstimatedTotal) * 100);
} else {
  displayEstimatedTotal = estimatedTotalSeconds;
  percentComplete = (elapsedSeconds / estimatedTotalSeconds) * 100;
}

// On completion, force 100%
if (status === "completed") {
  percentComplete = 100;
}
```

3. **Remove** "Estimated remaining" label and value
4. **Remove** "Overall progress" label
5. **Keep** progress bar, now driven by `elapsed / estimatedTotal` — smooth and monotonically increasing
6. **Change** time display from 2-column grid to single row showing elapsed vs estimated total:
   - Format: `30s / ~3m 0s` (elapsed / estimated total)
   - Use `formatDuration()` for both values, prefix estimated total with `~`
   - If `estimatedTotalSeconds <= 0`: show only elapsed time (no estimate available yet)
7. **Keep** step indicator (phase dots + "Step X of Y") unchanged
8. **Keep** cancel button unchanged
9. **Handle** terminal states from SSE `status` field:
   - `completed`: show 100% bar, display final time
   - `failed`: show error message from `errorMessage` field
   - `cancelled`: show cancelled state

**Updated layout:**

```tsx
{/* Progress bar */}
<div className="space-y-2">
  <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
    <div
      className="h-full rounded-full bg-primary transition-all duration-500"
      style={{ width: `${percentComplete}%` }}
    />
  </div>
  <div className="flex items-center justify-between text-sm text-muted-foreground">
    <span>{formatDuration(elapsedSeconds)}</span>
    {displayEstimatedTotal > 0 && (
      <span>~{formatDuration(displayEstimatedTotal)}</span>
    )}
  </div>
</div>
```

Note: `transition-all duration-500` instead of `duration-300` or `duration-1000` — since progress is now time-based and smooth, a moderate transition duration prevents visual jitter from 1-second polling intervals without creating perceptible lag.

---

## Phase 7: Test Updates

### `src/test/db/schema.test.ts`

Add assertions for `estimated_total_seconds`, `total_duration_seconds`, and `started_at` columns in the "has status tracking columns" test.

### `src/test/components/render/RenderProgress.test.tsx`

- Remove assertion for "Overall progress" text (line 78)
- Remove assertion for "Estimated remaining" text (line 84)
- Update SSE event data shape in tests (remove `percentComplete`/`estimatedSecondsLeft`, add `estimatedTotalSeconds`/`status`)
- Remove assertion for "25%" text (line 128) — percentage is no longer displayed as text
- Add assertions for elapsed/total time display
- Add test for dynamic estimate adjustment (elapsed > estimatedTotal)
- Add test for indeterminate state (estimatedTotalSeconds = 0)
- Add test for terminal states (completed, failed, cancelled) from SSE `status` field

### `src/test/api/render-jobs/events.test.ts`

- Update `mockQueuedJob` and `mockRunningJob` with `estimatedTotalSeconds`, `totalDurationSeconds`, and `startedAt` fields
- Remove `percentComplete` and `estimatedSecondsLeft` from mock objects
- Update SSE event assertions to check for `estimatedTotalSeconds`, `status` instead of `percentComplete`/`estimatedSecondsLeft`
- Add test for failed job terminal event (includes `status: "failed"`, `errorMessage`)
- Add test for cancelled job terminal event (includes `status: "cancelled"`)

### `src/test/api/render-jobs/[id].test.ts`

- Update `mockJob` with new fields
- Update completed job assertions (remove `percentComplete: 100` check, or keep it since the DB column still exists)

### `src/test/api/render-jobs/route.test.ts`

- Update mock job objects with `estimatedTotalSeconds`, `totalDurationSeconds`, and `startedAt` fields

### `src/test/lib/render/pipeline.test.ts`

- Update `mockJob` with new fields
- Verify that `updateRenderProgress` is called with `estimatedTotalSeconds`, `totalDurationSeconds`, and `startedAt`
- Verify that `updateRenderProgress` is called after audio mixing with ffprobe-derived `totalDurationSeconds`
- Remove assertions about `percentComplete` values in progress updates
- Add test for cancelled→failed bug fix (cancelled job should NOT be overwritten to "failed")

### `src/test/lib/render/job-manager.test.ts`

- Update mock objects with new fields
- Add test for `estimatedTotalSeconds` and `totalDurationSeconds` in progress updates
- Add test for `completeRenderJob` computing `elapsedSeconds` from `startedAt` (not `createdAt`)
- Add test for `completeRenderJob` when `startedAt` is null (fallback behavior)

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
- Returns most conservative default for unknown resolution (e.g. "4k")
- Query uses `started_at` (not `created_at`) to exclude queue wait time

---

## Files Changed Summary

| File | Action |
|------|--------|
| `src/db/schema.ts` | Add `estimatedTotalSeconds`, `totalDurationSeconds`, `startedAt` columns; add `@deprecated` JSDoc to `percentComplete`, `estimatedSecondsLeft` |
| `src/lib/render/render-ratio.ts` | **New file** — render ratio computation with historical learning |
| `src/lib/render/pipeline.ts` | Remove `computeTimerValues`, remove `percentComplete`/`estimatedSecondsLeft` from progress updates, add duration estimation at pipeline start, update after audio mixing, fix cancelled→failed bug |
| `src/lib/render/job-manager.ts` | Add new fields to interfaces, update mapping and progress updates, `completeRenderJob` computes `elapsedSeconds` from `startedAt` |
| `src/app/api/render-jobs/[id]/events/route.ts` | Update SSE event shape (add `status`, `errorMessage`, `estimatedTotalSeconds`; remove `percentComplete`, `estimatedSecondsLeft`) |
| `src/components/render/RenderProgress.tsx` | Simplify UI — remove "Overall progress" label and "Estimated remaining", show elapsed/total, dynamic estimate adjustment, handle terminal states |
| `src/test/db/schema.test.ts` | Add column assertions |
| `src/test/components/render/RenderProgress.test.tsx` | Update assertions, add dynamic estimate and terminal state tests |
| `src/test/api/render-jobs/events.test.ts` | Update mock data and assertions, add terminal event tests |
| `src/test/api/render-jobs/[id].test.ts` | Update mock data |
| `src/test/api/render-jobs/route.test.ts` | Update mock data |
| `src/test/lib/render/pipeline.test.ts` | Update mock data and assertions, add cancelled→failed fix test |
| `src/test/lib/render/job-manager.test.ts` | Update mock data, add new field tests, add `startedAt`-based elapsed test |
| `src/test/lib/render/render-ratio.test.ts` | **New file** — render ratio tests |

---

## Implementation Order

1. Phase 0: Run benchmark renders to determine baseline ratios
2. Phase 1: Schema changes + migration (three new columns + deprecation comments)
3. Phase 2: Render ratio module (`render-ratio.ts`)
4. Phase 3: Pipeline changes (including cancelled→failed bug fix)
5. Phase 4: Job manager changes
6. Phase 5: SSE event changes
7. Phase 6: Front-end component changes
8. Phase 7: Update existing tests
9. Phase 8: Add new tests for render ratio
10. Run full test suite, lint, typecheck
11. Push
