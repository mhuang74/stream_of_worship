# Allow Re-render from Render Page

## Summary

When a songset has already been rendered, navigating to the render page
(`/songsets/[id]/render`) currently shows the `RenderComplete` screen with
download links and no way to change parameters and re-render. This plan changes
the behavior so the render page **always shows the parameter form** when a
previous render exists, with a reminder banner that includes download links for
the previous render output.

## Goals

- Always show the render parameter form when the user navigates to the render
  page, regardless of whether a previous render exists.
- Display a "previously rendered" banner with download links when a completed
  render exists, so users can grab previous output without leaving the form.
- Allow users to change any parameter (font, template, resolution, etc.) and
  re-render at any time.
- Preserve the "submitted" screen behavior for running/queued jobs (no form
  shown while a job is in progress).
- Keep the `RenderComplete` component for future use (landing page from render
  completion notification email), but stop rendering it from the render page.

## Non-Goals

- Do not change the "submitted" screen behavior for running/queued jobs.
- Do not add client-side render time estimation (use existing server-side
  `estimatedTotalSeconds`).
- Do not change the songset detail page or list page render entry points.
- Do not modify the render-worker or backend API.

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

### Step 1: Remove auto-redirect to "complete" screen in render page

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

2. Remove the `RenderScreenState` value `"complete"` and the
   `{screenState === "complete" && ...}` rendering block (lines 322-334).

3. Remove the `handleDone` and `handleShare` callbacks (lines 243-249) since
   they are only used by `RenderComplete`.

4. Remove the dynamic import of `RenderComplete` (lines 20-22).

5. Simplify `RenderScreenState` type to:

   ```ts
   type RenderScreenState = "form" | "submitted"
   ```

6. Pass `jobData` as a new `previousRenderJob` prop to `RenderForm` when it
   exists (i.e., when a completed render is available).

### Step 2: Add `previousRenderJob` prop to RenderForm

**File:** `webapp/src/components/render/RenderForm.tsx`

1. Add a new prop to `RenderFormProps`:

   ```ts
   previousRenderJob?: {
     id: string
     mp3R2Key: string | null
     mp4R2Key: string | null
     chaptersR2Key: string | null
     elapsedSeconds?: number
   }
   ```

2. When `previousRenderJob` is provided, render a "previously rendered" banner
   at the top of the form (above the Output Options card), styled similarly to
   the existing "marked lines" warning banner but using a blue/info color scheme
   instead of yellow.

   Banner content:
   - Info icon (`Info` from lucide-react)
   - Title: "Previously Rendered"
   - Subtitle: "This songset has already been rendered. You can change
     parameters and re-render, or download the previous output."
   - Download buttons row (only show buttons for available files):
     - Audio (MP3) — if `mp3R2Key` is not null
     - Video (MP4) — if `mp4R2Key` is not null
     - Chapters (JSON) — if `chaptersR2Key` is not null
   - Each download button uses `fetchSignedUrlAndDownload` from
     `@/lib/download` and `sanitizeFilename` for the filename, same pattern
     as `RenderComplete.tsx`.
   - Elapsed time display if `elapsedSeconds` is available (reuse
     `formatDuration` helper from `RenderComplete.tsx` — extract to a shared
     utility or inline a simple version).

3. Add necessary imports: `fetchSignedUrlAndDownload`, `sanitizeFilename` from
   `@/lib/download`; `Download`, `Music`, `Video`, `FileJson` icons from
   lucide-react; `toast` from sonner.

### Step 3: Extract `formatDuration` to shared utility

**File:** `webapp/src/lib/format.ts` (new file)

1. Move the `formatDuration` function from `RenderComplete.tsx:30-37` to a
   shared utility file so both `RenderForm` and `RenderComplete` can use it.

2. Update `RenderComplete.tsx` to import from the shared location.

### Step 4: Keep RenderComplete component for future use

**File:** `webapp/src/components/render/RenderComplete.tsx`

- No changes needed to the component itself.
- It remains available for the future landing page (render completion
  notification email flow).
- Update its `formatDuration` import to use the shared utility from Step 3.

## Acceptance Criteria

- Navigating to `/songsets/[id]/render` always shows the render parameter form,
  even when a previous render exists with `renderState === "fresh"`.
- When a previous completed render exists, a blue/info banner appears above the
  form with:
  - "Previously Rendered" title and explanation text.
  - Download buttons for each available output file (audio, video, chapters).
  - Elapsed time from the previous render.
- When no previous render exists, the form appears without the banner (current
  behavior).
- When a render job is running/queued, the "submitted" screen still shows
  (current behavior, unchanged).
- The `RenderComplete` component still exists and compiles but is not rendered
  from the render page.
- Download buttons in the banner work identically to the ones in the old
  `RenderComplete` screen (same `fetchSignedUrlAndDownload` flow).

## Suggested Implementation Order

1. Extract `formatDuration` to shared utility.
2. Update `RenderComplete.tsx` import.
3. Add `previousRenderJob` prop and banner to `RenderForm.tsx`.
4. Update render page (`page.tsx`) to remove "complete" screen, pass
  `previousRenderJob` to form.
5. Test manually: render a songset, then navigate back to the render page and
  verify the form shows with the banner and download links.
6. Run lint and build.

## Verification Commands

```bash
cd webapp
pnpm lint
pnpm build
```

## Completion Checklist

- [ ] `formatDuration` extracted to shared utility
- [ ] `RenderComplete.tsx` updated to use shared `formatDuration`
- [ ] `RenderForm.tsx` has `previousRenderJob` prop and "previously rendered"
      banner with download links
- [ ] Render page no longer shows "complete" screen; always shows form
- [ ] `RenderScreenState` simplified to `"form" | "submitted"`
- [ ] `RenderComplete` component preserved for future use
- [ ] `pnpm lint` passes
- [ ] `pnpm build` passes
