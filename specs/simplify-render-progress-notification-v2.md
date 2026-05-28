# Simplify Render Progress & Notification v2

## Problem

1. **Render Progress screen is fragile on mobile**: SSE connections drop when the phone locks; polling resumes but shows stale data. Users feel compelled to keep checking the screen.
2. **Songset List shows stuck 0%**: `RenderStateButton` displays `Rendering... 0%` because `percentComplete` is deprecated and no longer written by the pipeline. The percentage prop is meaningless.
3. **No songset size limit**: Users can add unlimited songs, risking Lambda timeout (15 min) on large renders.
4. **Render time estimates are too high**: Default render ratios predate the frame cache optimization. Recent renders show actual ratios of ~0.45–0.50 vs. defaults of 0.65–0.80.

## Decisions

| Decision | Choice |
|----------|--------|
| Max songset limit | **5 songs / 25 min total audio** |
| Render Progress screen | **Simplify to static message** with estimated time + cancel |
| Songset List status | **Text badge** (no percentage) |
| Email notification | **Not in v2** — deferred to future phase |
| Default render ratios | **Updated** to reflect frame cache performance |

---

## Phase 1: Songset Size Limit (5 songs / 25 min)

### Constants

New file: `webapp/src/lib/constants.ts`

```typescript
export const SONGSET_MAX_SONGS = 5;
export const SONGSET_MAX_DURATION_SECONDS = 1500; // 25 minutes
```

### Server-side enforcement: `webapp/src/app/api/render-jobs/route.ts`

In `POST` handler, after `createRenderJob` but before `dispatchToRenderWorker`, validate the songset's item count and total duration. If exceeded, return 400 with a clear message.

```typescript
const items = await db.query.songsetItems.findMany({
  where: eq(songsetItems.songsetId, input.songsetId),
});

if (items.length > SONGSET_MAX_SONGS) {
  return NextResponse.json(
    { error: `Songset exceeds maximum of ${SONGSET_MAX_SONGS} songs` },
    { status: 400 }
  );
}

const totalDuration = items.reduce((sum, item) => sum + (item.recording?.durationSeconds ?? 0), 0);
if (totalDuration > SONGSET_MAX_DURATION_SECONDS) {
  return NextResponse.json(
    { error: `Songset exceeds maximum duration of ${Math.floor(SONGSET_MAX_DURATION_SECONDS / 60)} minutes` },
    { status: 400 }
  );
}
```

### Server-side enforcement: `webapp/src/lib/db/songsets.ts`

In `addSongsetItem()`, before inserting, check current item count:

```typescript
const currentCount = await db.query.songsetItems.findMany({
  where: eq(songsetItems.songsetId, songsetId),
  columns: { id: true },
});

if (currentCount.length >= SONGSET_MAX_SONGS) {
  throw new Error(`Songset already has maximum of ${SONGSET_MAX_SONGS} songs`);
}
```

### UI enforcement: `webapp/src/components/songset/SongsetEditor.tsx`

- When `items.length >= SONGSET_MAX_SONGS`, hide the FAB (add songs button) and show a small text: "Maximum 5 songs reached"
- When total duration >= 25 min, show a warning badge near the song count

### UI enforcement: `webapp/src/components/songset/BrowseSheet.tsx`

- When the songset already has 5 songs, disable the "Add" buttons in the browse sheet and show "Songset full" message

### Render worker enforcement: `services/render-worker/src/sow_render_worker/pipeline.py`

Add a guard at the start of `execute_render_pipeline()` after fetching items:

```python
MAX_SONGSET_ITEMS = 5
MAX_SONGSET_DURATION_SECONDS = 1500

total_duration = sum(item.duration_seconds or 0 for item in items)
if len(items) > MAX_SONGSET_ITEMS or total_duration > MAX_SONGSET_DURATION_SECONDS:
    raise ValueError(
        f"Songset exceeds limit: {len(items)} songs / {total_duration:.0f}s "
        f"(max {MAX_SONGSET_ITEMS} songs / {MAX_SONGSET_DURATION_SECONDS}s)"
    )
```

This is a defense-in-depth check — the webapp should already prevent this, but the worker should also reject oversized jobs.

---

## Phase 2: Simplify Render Progress Screen

### Replace `RenderProgress.tsx` with `RenderSubmitted.tsx`

The current component (364 lines) with SSE, polling, stale detection, phase indicators, and progress bars is replaced with a simple static card:

```tsx
// webapp/src/components/render/RenderSubmitted.tsx
"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Clock, X } from "lucide-react"

interface RenderSubmittedProps {
  estimatedMinutes: number
  onCancel: () => void
  isCancelling?: boolean
}

export function RenderSubmitted({
  estimatedMinutes,
  onCancel,
  isCancelling = false,
}: RenderSubmittedProps) {
  return (
    <Card className="w-full">
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Render Started</CardTitle>
          <Button
            variant="ghost"
            size="icon"
            onClick={onCancel}
            disabled={isCancelling}
            aria-label="Cancel render"
          >
            <X className="size-4" />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-2 text-muted-foreground">
          <Clock className="size-4" />
          <span>Estimated time: ~{estimatedMinutes} minutes</span>
        </div>
        <p className="text-sm text-muted-foreground">
          You can leave this page. Check your songset later for the result.
        </p>
        <Button
          variant="outline"
          className="w-full"
          onClick={onCancel}
          disabled={isCancelling}
        >
          Cancel Render
        </Button>
      </CardContent>
    </Card>
  )
}
```

**v2 change from v1**: Removed the `Mail` icon and "We'll email you when rendering is done" line. No email notification in this version.

### Update render page to use `RenderSubmitted`

The page that currently shows `RenderProgress` should instead:
1. After render job creation, compute `estimatedMinutes` from `estimatedTotalSeconds` returned by the API
2. Show `RenderSubmitted` with the estimate
3. Provide a cancel button that calls `DELETE /api/render-jobs/[id]`
4. After cancelling or on back navigation, return to the songset editor

### Remove SSE endpoint

`webapp/src/app/api/render-jobs/[id]/events/route.ts` — **Delete this file**. SSE is no longer needed since the progress screen is static. The `GET /api/render-jobs/[id]` endpoint remains for checking job status (used by the songset list).

### Keep `GET /api/render-jobs/[id]`

This endpoint is still needed for:
- Cancel flow (checking if job is still cancellable)
- Songset list status computation
- Future use (e.g., pull-to-refresh on songset list)

---

## Phase 3: Songset List — Replace Progress with Status Badge

### Replace `RenderStateButton` with `RenderStatusBadge`

New component: `webapp/src/components/songset/RenderStatusBadge.tsx`

```tsx
"use client"

import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { CheckCircle2, Loader2, AlertCircle, RefreshCw } from "lucide-react"

export type RenderState = "unrendered" | "rendering" | "fresh" | "stale" | "failed"

interface RenderStatusBadgeProps {
  state: RenderState
  className?: string
}

const STATE_CONFIG: Record<RenderState, {
  label: string
  variant: "default" | "secondary" | "destructive" | "outline"
  icon: React.ComponentType<{ className?: string }>
  iconClass: string
}> = {
  unrendered: {
    label: "Not rendered",
    variant: "outline",
    icon: RefreshCw,
    iconClass: "",
  },
  rendering: {
    label: "Rendering",
    variant: "secondary",
    icon: Loader2,
    iconClass: "animate-spin",
  },
  fresh: {
    label: "Rendered",
    variant: "default",
    icon: CheckCircle2,
    iconClass: "",
  },
  stale: {
    label: "Needs re-render",
    variant: "outline",
    icon: RefreshCw,
    iconClass: "",
  },
  failed: {
    label: "Render failed",
    variant: "destructive",
    icon: AlertCircle,
    iconClass: "",
  },
}

export function RenderStatusBadge({ state, className }: RenderStatusBadgeProps) {
  const config = STATE_CONFIG[state]
  const Icon = config.icon

  return (
    <Badge variant={config.variant} className={cn("gap-1", className)}>
      <Icon className={cn("size-3", config.iconClass)} />
      {config.label}
    </Badge>
  )
}
```

### Update `SongsetRow.tsx`

Replace `RenderStateButton` with `RenderStatusBadge` in the metadata row area. Move action buttons (Render, Play, Retry) to the dropdown menu only. The row shows:

```
[Songset Name]                    [⋮]
[🎵 5 songs] [⏱ 20:30] [Updated May 28]
[✓ Rendered] [Offline] [Artifacts out of date]
```

The badge replaces the button. Render/Play/Retry actions remain in the dropdown menu (already present).

### Update `SongsetEditor.tsx`

Replace `RenderStateButton` in the app bar with `RenderStatusBadge`. Keep the Render/Play/Retry actions in the overflow dropdown menu.

### Remove `renderProgress` prop

Remove `renderProgress?: number` from:
- `SongsetList.tsx` (Songset interface)
- `SongsetRow.tsx` (SongsetRowProps)
- `SongsetEditor.tsx` (SongsetEditorProps)
- All page components that pass this prop

The `progress` prop on `RenderStateButton` is also removed since the badge doesn't show percentages.

---

## Phase 4: Update Default Render Ratios

The frame cache optimization has significantly reduced render times. Historical data from the `render_jobs` table shows:

| Profile | Old Default | Historical Avg | Recent (last 2) | New Default |
|---------|-------------|---------------|-----------------|-------------|
| 720p_video | 0.8 | 0.495 (3 jobs) | — | **0.5** |
| 720p_audio | 0.4 | no data | — | 0.4 (unchanged) |
| 1080p_video | 0.65 | 0.645 (13 jobs) | 0.481, 0.443 | **0.5** |
| 1080p_audio | 0.4 | no data | — | 0.4 (unchanged) |

### Change: `services/render-worker/src/sow_render_worker/pipeline.py`

```python
DEFAULT_RENDER_RATIOS: dict[str, float] = {
    "720p_video": 0.5,
    "720p_audio": 0.4,
    "1080p_video": 0.5,
    "1080p_audio": 0.4,
}
```

The adaptive `get_render_ratio()` function already uses historical data when ≥3 completed jobs exist, so these defaults primarily affect cold starts and new resolution combos. But updating them makes the initial estimate more accurate for users before historical data accumulates.

---

## Phase 5: Cleanup

### Delete files

| File | Reason |
|------|--------|
| `webapp/src/components/render/RenderProgress.tsx` | Replaced by `RenderSubmitted.tsx` |
| `webapp/src/app/api/render-jobs/[id]/events/route.ts` | SSE no longer needed |
| `webapp/src/components/songset/RenderStateButton.tsx` | Replaced by `RenderStatusBadge.tsx` |

### Update files

| File | Change |
|------|--------|
| `webapp/src/components/songset/SongsetRow.tsx` | Replace `RenderStateButton` with `RenderStatusBadge`, remove `renderProgress` prop |
| `webapp/src/components/songset/SongsetEditor.tsx` | Replace `RenderStateButton` with `RenderStatusBadge`, remove `renderProgress` prop, add max song limit UI |
| `webapp/src/components/songset/SongsetList.tsx` | Remove `renderProgress` from `Songset` interface |
| `webapp/src/components/songset/BrowseSheet.tsx` | Disable add when songset is full |
| `webapp/src/lib/db/songsets.ts` | Add item count check in `addSongsetItem()` |
| `webapp/src/app/api/render-jobs/route.ts` | Add songset size validation before dispatch |
| `webapp/src/lib/render/job-manager.ts` | Remove `percentComplete` and `estimatedSecondsLeft` from `RenderJob` interface (deprecated columns remain in DB for backward compat) |
| `services/render-worker/src/sow_render_worker/pipeline.py` | Add songset size guard, update `DEFAULT_RENDER_RATIOS` |
| `webapp/src/lib/constants.ts` | **New file** — `SONGSET_MAX_SONGS`, `SONGSET_MAX_DURATION_SECONDS` |
| `webapp/src/components/render/RenderSubmitted.tsx` | **New file** — Simplified render submitted card |
| `webapp/src/components/songset/RenderStatusBadge.tsx` | **New file** — Status badge component |

### Test updates

- Remove/update tests for deleted `RenderProgress.tsx` and SSE events endpoint
- Add tests for `RenderStatusBadge`
- Add tests for `RenderSubmitted`
- Add tests for songset size limit enforcement (API + DB layer)

---

## Implementation Order

1. **Phase 1**: Songset size limit (constants, API validation, DB check, UI enforcement, worker guard)
2. **Phase 2**: Simplify render progress screen (new `RenderSubmitted`, update render page, delete SSE endpoint)
3. **Phase 3**: Status badge (new `RenderStatusBadge`, update `SongsetRow`/`SongsetEditor`, delete `RenderStateButton`)
4. **Phase 4**: Update default render ratios (single constant change in pipeline.py)
5. **Phase 5**: Cleanup (delete old files, update tests, lint, typecheck)
6. Push

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Songset size limit too restrictive | 5 songs / 25 min covers typical worship sets. Can increase later if needed |
| Render ratio defaults too low | Conservative at 0.5; adaptive `get_render_ratio()` overrides with real data after 3 jobs. If estimate is low, user just waits a bit longer — no functional harm |
| Users miss real-time progress | Static message clearly states "You can leave this page." Badge on songset list shows rendering state |
| SSE removal breaks existing bookmarks | Render page redirects to songset editor which shows the badge. No 404 since the page route stays, just the component changes |

---

## Deferred to Future Phase

- **Email notification on completion/failure** (was Phase 4 in v1) — requires Resend setup, internal API endpoint, worker webhook calls, new env vars
- **Push notification** — alternative to email for mobile users
