# Fix: Render Button Double-Submit & Missing Toast (v2)

**Status:** Draft  
**Created:** 2026-05-30  
**Supersedes:** fix-render-button-double-submit-and-toast.md  
**Severity:** High (user confusion + backend load spike risk)

## Problem

When clicking "Start Render" on the Render screen (`/songsets/[id]/render`), three issues combine to create a dangerous UX:

1. **No toast visible** — The `<Toaster />` component from sonner is never rendered in the root layout, so `toast.error()` calls execute silently. Users see zero feedback on 400 errors.
2. **No submit guard** — The `RenderForm` component supports an `isSubmitting` prop that disables the button and shows "Starting...", but the parent `RenderPage` never tracks or passes this state. The button stays fully clickable during the async fetch, allowing rapid re-clicks.
3. **No server-side idempotency** — The API route creates a new `render_jobs` row on every POST with no check for existing active jobs for the same songset+user. Multiple clicks = multiple queued jobs = wasted render worker capacity.

## Fix 1: Add `<Toaster />` to Root Layout

**File:** `webapp/src/app/layout.tsx`

Add the `<Toaster />` component inside `<body>`:

```diff
+ import { Toaster } from "@/components/ui/sonner"

  export default function RootLayout({ children }) {
    return (
      <html ...>
        <body ...>
          <GlobalAudioPlayer>
            <Header />
            <main ...>{children}</main>
            <BottomNav />
          </GlobalAudioPlayer>
+         <Toaster />
        </body>
      </html>
    )
  }
```

**Note:** The `<Toaster />` must be placed **outside** `<GlobalAudioPlayer>` to avoid any context/z-index issues. It is a portal-based component that renders toasts in a fixed position overlay.

**Verification:** After this change, any `toast.error()` / `toast.success()` call anywhere in the app will render a visible notification. Confirm by triggering a 400 error and seeing the toast appear.

## Fix 2: Add `isSubmitting` State to RenderPage

**File:** `webapp/src/app/songsets/[id]/render/page.tsx`

### 2a. Add state variable

```diff
  const [isCancelling, setIsCancelling] = useState(false)
+ const [isSubmitting, setIsSubmitting] = useState(false)
```

### 2b. Wrap `handleSubmit` with submitting state

```diff
  const handleSubmit = useCallback(
    async (formData: RenderFormData) => {
+     setIsSubmitting(true)
      try {
        const response = await fetch("/api/render-jobs", { ... })
        // ... existing logic ...
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Failed to start render")
      } finally {
+       setIsSubmitting(false)
      }
    },
-   [songsetId, router]
+   [songsetId, router, setIsSubmitting]
  )
```

### 2c. Pass `isSubmitting` to `RenderForm`

```diff
  <RenderForm
    songsetId={songsetId}
    markedLineCount={songset.markedLineCount}
    songsetName={songset.name}
    songTitles={songset.songTitles}
    initialData={initialData}
    onSubmit={handleSubmit}
    onCancel={() => router.push(`/songsets/${songsetId}`)}
+   isSubmitting={isSubmitting}
  />
```

**Effect:** While the fetch is in-flight, the "Start Render" button becomes disabled (opacity-50) and text changes to "Starting...". The Cancel button is also disabled. This prevents rapid re-clicks.

**Verification:** Click "Start Render" — button should immediately show "Starting..." and become unclickable. On error, it should revert to "Start Render" and be re-enabled.

## Fix 3: Server-Side Idempotency Guard

**File:** `webapp/src/app/api/render-jobs/route.ts`

Before creating a new render job, check if there's already an active (`queued` or `running`) job for the same songset + user **created within the last 20 minutes** (Lambda timeout is 15 min; 20 min adds margin for clock skew and dispatch latency). Jobs older than 20 minutes in `running` status are considered stale/orphaned and do not block new submissions.

### 3a. Add staleness-filtered query in route handler (after auth check, before `createRenderJob`)

```diff
+ import { and, eq, or, gte } from "drizzle-orm";
+ import { renderJobs } from "@/db/schema";

  export async function POST(request: NextRequest) {
    try {
      // ... auth + validation ...

+     const twentyMinutesAgo = new Date(Date.now() - 20 * 60 * 1000);
+     const activeJob = await db.query.renderJobs.findFirst({
+       where: and(
+         eq(renderJobs.songsetId, parsed.data.songsetId),
+         eq(renderJobs.userId, Number(session.user.id)),
+         or(eq(renderJobs.status, "queued"), eq(renderJobs.status, "running")),
+         gte(renderJobs.createdAt, twentyMinutesAgo)
+       ),
+     });

+     if (activeJob) {
+       return NextResponse.json(
+         {
+           error: "A render job is already in progress for this songset",
+           jobId: activeJob.id,
+           estimatedTotalSeconds: activeJob.estimatedTotalSeconds,
+           config: {
+             audioEnabled: activeJob.audioEnabled,
+             videoEnabled: activeJob.videoEnabled,
+           },
+         },
+         { status: 409 }
+       );
+     }

      const job = await createRenderJob(Number(session.user.id), parsed.data);
      // ...
```

### 3b. Catch unique constraint violation from DB index

Wrap `createRenderJob` in a try/catch for the partial unique index violation. If the insert fails due to the unique constraint, return 409 instead of 500:

```diff
-     const job = await createRenderJob(Number(session.user.id), parsed.data);
+     let job;
+     try {
+       job = await createRenderJob(Number(session.user.id), parsed.data);
+     } catch (err) {
+       if (err instanceof Error && err.message.includes("uq_render_jobs_active_per_songset_user")) {
+         return NextResponse.json(
+           { error: "A render job is already in progress for this songset" },
+           { status: 409 }
+         );
+       }
+       throw err;
+     }
```

This catches the TOCTOU race where two concurrent POSTs both pass the `findFirst` check before either inserts.

### 3c. DB partial unique index migration

**File:** New migration in `webapp/drizzle/`

```sql
CREATE UNIQUE INDEX CONCURRENTLY uq_render_jobs_active_per_songset_user
  ON render_jobs ("songsetId", "userId")
  WHERE status IN ('queued', 'running');
```

This provides true DB-level concurrency safety. The application-level check (3a) handles the common case and returns a rich 409 with job details. The index catches the rare race condition.

### 3d. Handle 409 in the frontend

**File:** `webapp/src/app/songsets/[id]/render/page.tsx`

```diff
  if (!response.ok) {
    if (response.status === 401) {
      router.push("/login")
      return
    }
+   if (response.status === 409) {
+     const data = await response.json()
+     if (data.jobId) {
+       setJobId(data.jobId)
+       if (data.estimatedTotalSeconds) {
+         setEstimatedMinutes(Math.ceil(data.estimatedTotalSeconds / 60))
+       }
+       setScreenState("submitted")
+       const configSummary = []
+       if (data.config?.audioEnabled) configSummary.push("audio")
+       if (data.config?.videoEnabled) configSummary.push("video")
+       toast.info(`A render job is already in progress (${configSummary.join(" + ")})`)
+     } else {
+       toast.error(data.error || "A render job is already in progress")
+     }
+     return
+   }
    const errorData = await response.json()
    throw new Error(errorData.error || "Failed to create render job")
  }
```

**Effect:** If a user somehow bypasses the client-side guard (e.g., two browser tabs), the server rejects the duplicate with 409. The frontend gracefully transitions to the "submitted" screen showing the existing job's progress with its estimate, and the toast tells the user what config is already queued.

**Verification:** Open two tabs for the same songset render page. Click "Start Render" in both. The second should get 409 and transition to the submitted screen with the existing job's estimate visible, instead of creating a duplicate job.

## Files Changed Summary

| File | Change |
|------|--------|
| `webapp/src/app/layout.tsx` | Add `<Toaster />` import + render |
| `webapp/src/app/songsets/[id]/render/page.tsx` | Add `isSubmitting` state, wrap fetch, pass prop, handle 409 with config + estimate |
| `webapp/src/app/api/render-jobs/route.ts` | Add staleness-filtered active-job check (20 min), enrich 409 response, catch unique constraint violation |
| `webapp/drizzle/...` | New migration: partial unique index on `(songsetId, userId) WHERE status IN ('queued','running')` |

## Testing

1. **Toast visibility:** Trigger a 400 (e.g., songset exceeding max songs) — error toast must appear
2. **Button disable:** Click "Start Render" — button must immediately disable and show "Starting..."
3. **Re-enable on error:** On 400/500 response — button must re-enable
4. **409 idempotency:** With an active job, POST again — should get 409, frontend transitions to submitted screen with estimate visible
5. **409 config toast:** The toast should show the existing job's config (e.g., "A render job is already in progress (audio + video)")
6. **Stale job bypass:** A `running` job older than 20 minutes should NOT block a new submission
7. **Concurrent POST safety:** Two simultaneous POSTs should result in only one job (second gets 409 from unique index)
8. **Happy path:** Normal render submission still works end-to-end
