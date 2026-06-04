# Allow Re-render from Render Page (v2)

## Summary

When a songset has already been rendered, navigating to the render page
(`/songsets/[id]/render`) currently shows the `RenderComplete` screen with
download links and no way to change parameters and re-render. This plan changes
the behavior so the render page **always shows the parameter form** when a
previous render exists, with a collapsible "previously rendered" banner that
includes the timestamp, elapsed time, and parameter summary of the previous
render. A confirmation dialog is shown before submitting a re-render.

## Goals

- Always show the render parameter form when the user navigates to the render
  page, regardless of whether a previous render exists.
- Display a collapsible "previously rendered" banner (default expanded) with
  timestamp, elapsed time, and parameter summary when a completed render exists.
- Require confirmation before submitting a re-render when a previous render
  exists.
- Show a submission timestamp (absolute + relative) on the "submitted" screen
  so users know when their job was submitted.
- Preserve the "submitted" screen behavior for running/queued jobs (no form
  shown while a job is in progress).
- Keep the `RenderComplete` component for future use (landing page from render
  completion notification email), but stop rendering it from the render page.

## Non-Goals

- Do not change the "submitted" screen behavior for running/queued jobs (beyond
  adding the timestamp).
- Do not add client-side render time estimation (use existing server-side
  `estimatedTotalSeconds`).
- Do not change the songset detail page or list page render entry points.
- Do not modify the render-worker or backend API.
- Do not add download buttons to the banner (users download from the songset
  kebab menu).

## Current Behavior

In `webapp/src/app/songsets/[id]/render/page.tsx:128-133`, when a completed
render job exists and `renderState === "fresh"`, the page auto-redirects to the
`RenderComplete` screen:

```ts
} else if (job.status === "completed") {
  setJobId(job.id)
  setJobData(job)
  if (renderState === "fresh") {
    setScreenState("complete")
  }
}
```

This blocks the user from changing parameters and re-rendering.

## Implementation Steps

### Step 1: Add `date-fns` dependency

```bash
cd webapp && pnpm add date-fns
```

Used for `format()` (absolute time) and `formatDistanceToNow()` (relative time)
in the banner and submitted screen.

### Step 2: Extract `formatDuration` to shared utility

**File:** `webapp/src/lib/format.ts` (new file)

1. Move the `formatDuration` function from `RenderComplete.tsx:30-37` to this
   shared utility file.

2. Update `RenderComplete.tsx` to import from `@/lib/format`.

### Step 3: Add `AlertDialog` UI component

```bash
cd webapp && npx shadcn@latest add alert-dialog
```

Used for the re-render confirmation dialog. The project has `Dialog` but not
`AlertDialog`; the latter is more semantically appropriate for confirm/cancel
flows.

### Step 4: Add `previousRenderJob` prop and collapsible banner to `RenderForm.tsx`

**File:** `webapp/src/components/render/RenderForm.tsx`

1. Add a new prop to `RenderFormProps`:

   ```ts
   previousRenderJob?: {
     id: string
     createdAt: string
     elapsedSeconds?: number
     template: string
     fontFamily: string
     fontSizePreset: string
     includeTitleCard: boolean
     titleCardDurationSeconds?: number
   }
   ```

2. When `previousRenderJob` is provided, render a collapsible "previously
   rendered" banner at the top of the form (above the Output Options card),
   styled with a blue/info color scheme (not yellow like the marked-lines
   warning).

   **Banner content (expanded):**
   - Header row (always visible): `Info` icon + "Previously Rendered" title +
     collapse/expand chevron (`ChevronDown`/`ChevronUp` from lucide-react)
   - Timestamp line: `format(new Date(createdAt), 'p')` +
     `formatDistanceToNow(new Date(createdAt), { addSuffix: true })` — e.g.,
     "Rendered at 12:15 PM (2 hr ago)"
   - Elapsed time line (if `elapsedSeconds` available):
     `formatDuration(elapsedSeconds)` — e.g., "3m 42s"
   - Parameter summary (compact, labeled list):
     - Font: `{fontFamily label}`
     - Font Size: `{fontSizePreset label}`
     - Background: `{template label}`
     - Title Card: "On ({duration}s)" or "Off"
   - Use `FONT_FAMILIES` from `@/lib/constants` to resolve fontFamily value to
     display label; use `TEMPLATES` and `FONT_SIZES` constants already in the
     file for template/fontSize labels.

   **Banner content (collapsed):** Header row only.

3. Add state: `const [bannerExpanded, setBannerExpanded] = useState(true)`

4. Add imports: `Info`, `ChevronDown`, `ChevronUp` from lucide-react; `format`,
   `formatDistanceToNow` from date-fns; `formatDuration` from `@/lib/format`.

### Step 5: Add confirmation dialog to `RenderForm.tsx`

**File:** `webapp/src/components/render/RenderForm.tsx`

1. Add state: `const [showConfirmDialog, setShowConfirmDialog] = useState(false)`

2. Modify the form submit handler: when `previousRenderJob` exists, instead of
   calling `onSubmit` directly, set `showConfirmDialog` to `true`. When
   `previousRenderJob` is absent, call `onSubmit` directly (no change).

3. Add an `AlertDialog` component:
   - Trigger: controlled via `showConfirmDialog` state (not a trigger button).
   - Title: "Start New Render?"
   - Description: "A previous render exists for this songset. Starting a new
     render will not delete the previous output."
   - Cancel button: closes dialog.
   - Confirm button: calls `onSubmit(formData)` and closes dialog.

4. Add imports: `AlertDialog`, `AlertDialogContent`, `AlertDialogHeader`,
   `AlertDialogTitle`, `AlertDialogDescription`, `AlertDialogFooter`,
   `AlertDialogCancel`, `AlertDialogAction` from
   `@/components/ui/alert-dialog`.

### Step 6: Add `submittedAt` prop and timestamp to `RenderSubmitted.tsx`

**File:** `webapp/src/components/render/RenderSubmitted.tsx`

1. Add a new prop: `submittedAt?: string` (ISO timestamp)

2. When `submittedAt` is provided, display a timestamp line below the existing
   progress/status content:
   - `format(new Date(submittedAt), 'p')` +
     `formatDistanceToNow(new Date(submittedAt), { addSuffix: true })` — e.g.,
     "Submitted at 2:34 PM (3 min ago)"
   - Style: `text-sm text-muted-foreground`

3. Add imports: `format`, `formatDistanceToNow` from date-fns.

### Step 7: Update render page to remove "complete" screen, pass new props

**File:** `webapp/src/app/songsets/[id]/render/page.tsx`

1. Remove lines 131-133 that set `screenState` to `"complete"` when
   `renderState === "fresh"`:

   ```diff
   } else if (job.status === "completed") {
     setJobId(job.id)
     setJobData(job)
   - if (renderState === "fresh") {
   -   setScreenState("complete")
   - }
   }
   ```

2. Remove the `{screenState === "complete" && ...}` rendering block (lines
   322-334).

3. Remove the `RenderComplete` dynamic import (lines 20-22).

4. Remove the `handleDone` and `handleShare` callbacks (lines 243-249).

5. Simplify `RenderScreenState` type:

   ```ts
   type RenderScreenState = "form" | "submitted"
   ```

6. Pass `previousRenderJob` to `RenderForm` when `jobData` exists and the job
   status is `"completed"`:

   ```tsx
   <RenderForm
     songsetId={songsetId}
     markedLineCount={songset.markedLineCount}
     songsetName={songset.name}
     songTitles={songset.songTitles}
     initialData={initialData}
     onSubmit={handleSubmit}
     onCancel={() => router.push(`/songsets/${songsetId}`)}
     isSubmitting={isSubmitting}
     previousRenderJob={
       jobData && jobData.status === "completed"
         ? {
             id: jobData.id,
             createdAt: jobData.createdAt,
             elapsedSeconds: jobData.elapsedSeconds,
             template: jobData.template,
             fontFamily: jobData.fontFamily,
             fontSizePreset: jobData.fontSizePreset,
             includeTitleCard: jobData.includeTitleCard,
             titleCardDurationSeconds: jobData.titleCardDurationSeconds,
           }
         : undefined
     }
   />
   ```

7. Pass `submittedAt` to `RenderSubmitted`:

   ```tsx
   <RenderSubmitted
     estimatedMinutes={estimatedMinutes}
     onCancel={handleCancel}
     isCancelling={isCancelling}
     submittedAt={jobData?.createdAt}
   />
   ```

8. Update `RenderJobData` interface to include the new fields needed for
   `previousRenderJob`:

   ```ts
   interface RenderJobData {
     id: string
     status: string
     createdAt: string
     elapsedSeconds?: number
     template: string
     fontFamily: string
     fontSizePreset: string
     includeTitleCard: boolean
     titleCardDurationSeconds?: number
     mp3R2Key: string | null
     mp4R2Key: string | null
     chaptersR2Key: string | null
   }
   ```

   Note: `mp3R2Key`, `mp4R2Key`, `chaptersR2Key` are kept in the interface
   since they're returned by the API, but they're no longer consumed by the
   render page UI.

### Step 8: Keep `RenderComplete` component for future use

**File:** `webapp/src/components/render/RenderComplete.tsx`

- No changes to the component itself.
- It remains available for the future landing page (render completion
  notification email flow).
- Its `formatDuration` import is updated in Step 2.

## Acceptance Criteria

- Navigating to `/songsets/[id]/render` always shows the render parameter form,
  even when a previous render exists with `renderState === "fresh"`.
- When a previous completed render exists, a blue/info collapsible banner
  appears above the form (default expanded) with:
  - "Previously Rendered" title with collapse/expand toggle.
  - Timestamp: absolute + relative (e.g., "Rendered at 12:15 PM (2 hr ago)").
  - Elapsed time from the previous render.
  - Parameter summary: font, font size, background/template, title card status
    + duration.
- When no previous render exists, the form appears without the banner (current
  behavior).
- When a render job is running/queued, the "submitted" screen still shows
  (current behavior, unchanged).
- The "submitted" screen shows a submission timestamp (absolute + relative) when
  `submittedAt` is available.
- Clicking "Start Render" when a previous render exists shows a confirmation
  dialog before submitting.
- Clicking "Start Render" when no previous render exists submits directly (no
  confirmation).
- The `RenderComplete` component still exists and compiles but is not rendered
  from the render page.
- `date-fns` is added as a dependency and used for time formatting.
- `AlertDialog` UI component is installed via shadcn.

## Suggested Implementation Order

1. Add `date-fns` dependency.
2. Add `AlertDialog` UI component via shadcn.
3. Extract `formatDuration` to shared utility (`webapp/src/lib/format.ts`).
4. Update `RenderComplete.tsx` import.
5. Add `previousRenderJob` prop, collapsible banner, and confirmation dialog to
   `RenderForm.tsx`.
6. Add `submittedAt` prop and timestamp to `RenderSubmitted.tsx`.
7. Update render page (`page.tsx`): remove "complete" screen, pass
   `previousRenderJob` and `submittedAt`.
8. Test manually: render a songset, navigate back to the render page, verify the
   form shows with the banner and confirmation dialog.
9. Run lint and build.

## Verification Commands

```bash
cd webapp
pnpm lint
pnpm build
```

## Completion Checklist

- [ ] `date-fns` added as dependency
- [ ] `AlertDialog` UI component installed via shadcn
- [ ] `formatDuration` extracted to `webapp/src/lib/format.ts`
- [ ] `RenderComplete.tsx` updated to use shared `formatDuration`
- [ ] `RenderForm.tsx` has `previousRenderJob` prop and collapsible "previously
      rendered" banner with timestamp, elapsed time, and parameter summary
- [ ] `RenderForm.tsx` has confirmation dialog before re-render
- [ ] `RenderSubmitted.tsx` has `submittedAt` prop and submission timestamp
      display
- [ ] Render page no longer shows "complete" screen; always shows form
- [ ] `RenderScreenState` simplified to `"form" | "submitted"`
- [ ] `RenderComplete` component preserved for future use
- [ ] `pnpm lint` passes
- [ ] `pnpm build` passes
