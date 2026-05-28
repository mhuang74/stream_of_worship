# Simplify Render Progress & Add Email Notification

## Problem

1. **Render Progress screen is fragile on mobile**: SSE connections drop when the phone locks; polling resumes but shows stale data. Users feel compelled to keep checking the screen.
2. **Songset List shows stuck 0%**: `RenderStateButton` displays `Rendering... 0%` because `percentComplete` is deprecated and no longer written by the pipeline. The percentage prop is meaningless.
3. **No notification on completion/failure**: Users must manually check back. If render fails, they may not discover it for hours.
4. **No songset size limit**: Users can add unlimited songs, risking Lambda timeout (15 min) on large renders.

## Decisions

| Decision | Choice |
|----------|--------|
| Max songset limit | **5 songs / 25 min total audio** |
| Email service | **Resend** (free tier: 100 emails/day) |
| Render Progress screen | **Simplify to static message** with estimated time + cancel |
| Songset List status | **Text badge** (no percentage) |

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
// After songset lookup in createRenderJob, add validation:
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
import { Mail, Clock, X } from "lucide-react"

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
        <div className="flex items-center gap-2 text-muted-foreground">
          <Mail className="size-4" />
          <span>We'll email you when rendering is done.</span>
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

## Phase 4: Email Notification via Resend

### Setup

1. Add `resend` package: `cd webapp && pnpm add resend`
2. Add env vars: `RESEND_API_KEY`, `RESEND_FROM_ADDRESS` (e.g., `Stream of Worship <noreply@streamofworship.com>`)
3. Create `webapp/src/lib/email/client.ts`:

```typescript
import { Resend } from "resend"

const resend = new Resend(process.env.RESEND_API_KEY)

const FROM = process.env.RESEND_FROM_ADDRESS ?? "Stream of Worship <noreply@streamofworship.com>"

export async function sendRenderCompletionEmail(
  to: string,
  songsetName: string,
  songsetId: string,
) {
  await resend.emails.send({
    from: FROM,
    to,
    subject: `Render complete: ${songsetName}`,
    html: `
      <h2>Your render is ready!</h2>
      <p>The songset <strong>${songsetName}</strong> has been rendered successfully.</p>
      <p><a href="${process.env.NEXT_PUBLIC_APP_URL}/songsets/${songsetId}">View songset</a></p>
    `,
  })
}

export async function sendRenderFailureEmail(
  to: string,
  songsetName: string,
  songsetId: string,
  errorMessage: string,
) {
  await resend.emails.send({
    from: FROM,
    to,
    subject: `Render failed: ${songsetName}`,
    html: `
      <h2>Render failed</h2>
      <p>The render for <strong>${songsetName}</strong> failed.</p>
      <p><strong>Error:</strong> ${errorMessage}</p>
      <p><a href="${process.env.NEXT_PUBLIC_APP_URL}/songsets/${songsetId}">View songset</a></p>
    `,
  })
}
```

### Where to send emails

**Option A (Recommended): Render worker sends email directly**

The render worker already has DB access and can look up the user's email. Add email sending at the end of `pipeline.py`:

```python
# In execute_render_pipeline(), after complete_render_job():
if job.video_enabled or job.audio_enabled:
    _send_notification_email(conn, user_id, songset_name, job_id, success=True)

# In the except block, after fail_render_job():
_send_notification_email(conn, user_id, songset_name, job_id, success=False, error_message=str(e))
```

The worker calls a new HTTP endpoint on the webapp to trigger the email (since the worker is Python/Lambda and doesn't have the Resend Node.js SDK):

```python
import urllib.request

def _send_notification_email(conn, user_id, songset_name, songset_id, success, error_message=None):
    # Look up user email from DB
    with conn.cursor() as cur:
        cur.execute("SELECT email FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        if not row or not row["email"]:
            return
    
    # Call webapp internal API to send email
    webhook_url = os.environ.get("SOW_WEBAPP_INTERNAL_URL", "")
    if not webhook_url:
        return
    
    payload = {
        "email": row["email"],
        "songsetName": songset_name,
        "songsetId": songset_id,
        "success": success,
    }
    if error_message:
        payload["errorMessage"] = error_message
    
    urllib.request.urlopen(
        urllib.request.Request(
            f"{webhook_url}/api/internal/send-render-notification",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.environ.get('SOW_INTERNAL_API_KEY', '')}"},
        )
    )
```

**Option B: Webapp polls for completed/failed jobs and sends email**

Add a cron job or Next.js middleware that periodically checks for newly completed/failed jobs and sends emails. This is more complex and adds latency.

**We go with Option A** — the worker notifies the webapp immediately upon completion/failure.

### New internal API endpoint

`webapp/src/app/api/internal/send-render-notification/route.ts`:

```typescript
import { NextRequest, NextResponse } from "next/server"

const INTERNAL_API_KEY = process.env.SOW_INTERNAL_API_KEY

export async function POST(request: NextRequest) {
  const authHeader = request.headers.get("authorization")
  if (authHeader !== `Bearer ${INTERNAL_API_KEY}`) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 })
  }

  const body = await request.json()
  const { email, songsetName, songsetId, success, errorMessage } = body

  if (success) {
    await sendRenderCompletionEmail(email, songsetName, songsetId)
  } else {
    await sendRenderFailureEmail(email, songsetName, songsetId, errorMessage ?? "Unknown error")
  }

  return NextResponse.json({ ok: true })
}
```

### New env vars

| Variable | Description | Example |
|----------|-------------|---------|
| `RESEND_API_KEY` | Resend API key | `re_xxxxx` |
| `RESEND_FROM_ADDRESS` | Sender email | `Stream of Worship <noreply@streamofworship.com>` |
| `SOW_INTERNAL_API_KEY` | Shared secret for worker→webapp calls | Random string |
| `SOW_WEBAPP_INTERNAL_URL` | Webapp base URL for internal calls | `https://streamofworship.com` |

### Render worker env vars (add to `services/render-worker/`)

| Variable | Description |
|----------|-------------|
| `SOW_WEBAPP_INTERNAL_URL` | Webapp base URL |
| `SOW_INTERNAL_API_KEY` | Shared secret |

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
| `services/render-worker/src/sow_render_worker/pipeline.py` | Add songset size guard, add notification webhook call on completion/failure |
| `webapp/src/lib/constants.ts` | **New file** — `SONGSET_MAX_SONGS`, `SONGSET_MAX_DURATION_SECONDS` |
| `webapp/src/lib/email/client.ts` | **New file** — Resend email client |
| `webapp/src/app/api/internal/send-render-notification/route.ts` | **New file** — Internal endpoint for worker→webapp email trigger |
| `webapp/src/components/render/RenderSubmitted.tsx` | **New file** — Simplified render submitted card |
| `webapp/src/components/songset/RenderStatusBadge.tsx` | **New file** — Status badge component |

### Test updates

- Remove/update tests for deleted `RenderProgress.tsx` and SSE events endpoint
- Add tests for `RenderStatusBadge`
- Add tests for `RenderSubmitted`
- Add tests for songset size limit enforcement (API + DB layer)
- Add tests for email notification internal endpoint

---

## Implementation Order

1. **Phase 1**: Songset size limit (constants, API validation, DB check, UI enforcement, worker guard)
2. **Phase 2**: Simplify render progress screen (new `RenderSubmitted`, update render page, delete SSE endpoint)
3. **Phase 3**: Status badge (new `RenderStatusBadge`, update `SongsetRow`/`SongsetEditor`, delete `RenderStateButton`)
4. **Phase 4**: Email notification (Resend setup, email client, internal API endpoint, worker webhook calls)
5. **Phase 5**: Cleanup (delete old files, update tests, lint, typecheck)
6. Push

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Resend API key leaked | Store in env vars only, never commit. Add to `.env.example` without real value |
| Internal API key leaked | Same as above. Rotate if compromised |
| Email delivery failures | Non-blocking — log error but don't fail the render. User can still check status on songset list |
| Worker can't reach webapp | `SOW_WEBAPP_INTERNAL_URL` must be reachable from Lambda. Fallback: log warning, skip email |
| Users with no email in DB | Skip notification silently. Better Auth requires email for signup, so this should be rare |
| Songset size limit too restrictive | 5 songs / 25 min covers typical worship sets. Can increase later if needed |
