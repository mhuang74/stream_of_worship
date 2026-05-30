# Fix: Render Button Double-Submit & Missing Toast

**Status:** Draft  
**Created:** 2026-05-30  
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
    [songsetId, router]
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

Before creating a new render job, check if there's already an active (`queued` or `running`) job for the same songset + user. If so, return 409 Conflict with the existing job's ID.

### 3a. Add query in route handler (after auth check, before `createRenderJob`)

```diff
+ import { and, eq, or } from "drizzle-orm";
+ import { renderJobs } from "@/db/schema";

  export async function POST(request: NextRequest) {
    try {
      // ... auth + validation ...

+     const activeJob = await db.query.renderJobs.findFirst({
+       where: and(
+         eq(renderJobs.songsetId, parsed.data.songsetId),
+         eq(renderJobs.userId, Number(session.user.id)),
+         or(eq(renderJobs.status, "queued"), eq(renderJobs.status, "running"))
+       ),
+     });
+
+     if (activeJob) {
+       return NextResponse.json(
+         { error: "A render job is already in progress for this songset", jobId: activeJob.id },
+         { status: 409 }
+       );
+     }

      const job = await createRenderJob(Number(session.user.id), parsed.data);
      // ...
```

### 3b. Handle 409 in the frontend

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
+       setScreenState("submitted")
+       toast.info("A render job is already in progress")
+     } else {
+       toast.error(data.error || "A render job is already in progress")
+     }
+     return
+   }
    const errorData = await response.json()
    throw new Error(errorData.error || "Failed to create render job")
  }
```

**Effect:** If a user somehow bypasses the client-side guard (e.g., two browser tabs), the server rejects the duplicate with 409. The frontend gracefully transitions to the "submitted" screen showing the existing job's progress, rather than showing an error.

**Verification:** Open two tabs for the same songset render page. Click "Start Render" in both. The second should get 409 and transition to the submitted screen instead of creating a duplicate job.

## Files Changed Summary

| File | Change |
|------|--------|
| `webapp/src/app/layout.tsx` | Add `<Toaster />` import + render |
| `webapp/src/app/songsets/[id]/render/page.tsx` | Add `isSubmitting` state, wrap fetch, pass prop, handle 409 |
| `webapp/src/app/api/render-jobs/route.ts` | Add active-job check before `createRenderJob`, return 409 |

## Testing

1. **Toast visibility:** Trigger a 400 (e.g., songset exceeding max songs) — error toast must appear
2. **Button disable:** Click "Start Render" — button must immediately disable and show "Starting..."
3. **Re-enable on error:** On 400/500 response — button must re-enable
4. **409 idempotency:** With an active job, POST again — should get 409, frontend transitions to submitted screen
5. **Happy path:** Normal render submission still works end-to-end
