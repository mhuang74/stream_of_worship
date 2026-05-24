# PR #70 Code Review Fixes

## Overview

Address 3 inline review comments from gemini-code-assist on PR #70 (feat(webapp): add download audio/video to songset KAB menus).

## Review Comments Summary

### Comment 1 — `webapp/src/lib/download.ts:11-13`
**Issue:** `window.location.href` can cause unexpected navigation if the server doesn't return `Content-Disposition: attachment`.  
**Priority:** Medium  
**Fix:** Replace with hidden anchor element approach (as suggested by reviewer).

### Comment 2 — `webapp/src/app/songsets/[id]/page.tsx:340-364`
**Issue:** 
- (a) `AbortController` is instantiated but never aborted — dead code
- (b) Download logic is duplicated across audio/video handlers and across multiple files  
**Priority:** Medium  
**Fix:** Extract a shared `fetchSignedUrlAndDownload()` utility in `lib/download.ts` that handles the signed URL fetch + download trigger. Remove AbortController and AbortError check. Simplify all call sites.

### Comment 3 — `webapp/src/components/render/RenderComplete.tsx:49-74`
**Issue:**
- (a) `handleDownloadFile` not wrapped in `useCallback`
- (b) Same redundant AbortController  
**Priority:** Medium  
**Fix:** Wrap in `useCallback`, remove AbortController, use the new shared utility.

---

## Implementation Plan

### Step 1: Update `webapp/src/lib/download.ts`

Add `fetchSignedUrlAndDownload()` function and fix `downloadArtifact()`:

```ts
export function sanitizeFilename(name: string): string {
  return name
    .trim()
    .replace(/[/\\:*?"<>|#]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-|-$/g, "")
    .toLowerCase();
}

export function downloadArtifact(url: string): void {
  const link = document.createElement("a");
  link.href = url;
  link.style.display = "none";
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}

export async function fetchSignedUrlAndDownload(
  renderJobId: string,
  fileType: "audio" | "video" | "json",
  filename: string,
  extension: string,
): Promise<void> {
  const disposition = `attachment; filename="${filename}.${extension}"`;
  const res = await fetch(
    `/api/signed-url?renderJobId=${encodeURIComponent(renderJobId)}` +
      `&fileType=${fileType}` +
      `&contentDisposition=${encodeURIComponent(disposition)}`
  );
  if (!res.ok) throw new Error("Failed to get download URL");
  const { url } = await res.json();
  downloadArtifact(url);
}
```

### Step 2: Update `webapp/src/app/songsets/[id]/page.tsx`

Simplify `handleDownloadAudio` and `handleDownloadVideo`:

```ts
import { downloadArtifact, sanitizeFilename, fetchSignedUrlAndDownload } from "@/lib/download";

// Handle download audio
const handleDownloadAudio = useCallback(async () => {
  if (!songset?.latestRenderJobId) return;
  const toastId = toast.loading("Preparing download...");
  try {
    await fetchSignedUrlAndDownload(
      songset.latestRenderJobId,
      "audio",
      sanitizeFilename(songset.name),
      "mp3"
    );
    toast.success("Download started", { id: toastId });
  } catch {
    toast.error("Failed to download audio", { id: toastId });
  }
}, [songset]);

// Handle download video
const handleDownloadVideo = useCallback(async () => {
  if (!songset?.latestRenderJobId) return;
  const toastId = toast.loading("Preparing download...");
  try {
    await fetchSignedUrlAndDownload(
      songset.latestRenderJobId,
      "video",
      sanitizeFilename(songset.name),
      "mp4"
    );
    toast.success("Download started", { id: toastId });
  } catch {
    toast.error("Failed to download video", { id: toastId });
  }
}, [songset]);
```

### Step 3: Update `webapp/src/app/songsets/page.tsx`

Same simplification for the list page's `handleDownloadAudio` and `handleDownloadVideo`:

```ts
import { downloadArtifact, sanitizeFilename, fetchSignedUrlAndDownload } from "@/lib/download";

const handleDownloadAudio = useCallback(async (id: string) => {
  const songset = songsets.find((s) => s.id === id);
  if (!songset?.latestRenderJobId) return;
  const toastId = toast.loading("Preparing download...");
  try {
    await fetchSignedUrlAndDownload(
      songset.latestRenderJobId,
      "audio",
      sanitizeFilename(songset.name),
      "mp3"
    );
    toast.success("Download started", { id: toastId });
  } catch {
    toast.error("Failed to download audio", { id: toastId });
  }
}, [songsets]);

const handleDownloadVideo = useCallback(async (id: string) => {
  const songset = songsets.find((s) => s.id === id);
  if (!songset?.latestRenderJobId) return;
  const toastId = toast.loading("Preparing download...");
  try {
    await fetchSignedUrlAndDownload(
      songset.latestRenderJobId,
      "video",
      sanitizeFilename(songset.name),
      "mp4"
    );
    toast.success("Download started", { id: toastId });
  } catch {
    toast.error("Failed to download video", { id: toastId });
  }
}, [songsets]);
```

### Step 4: Update `webapp/src/components/render/RenderComplete.tsx`

Wrap `handleDownloadFile` in `useCallback` and use shared utility:

```ts
import { useCallback } from "react"
import { sanitizeFilename, downloadArtifact, fetchSignedUrlAndDownload } from "@/lib/download"

const handleDownloadFile = useCallback(async (
  fileType: "audio" | "video" | "json",
  extension: string,
) => {
  const toastId = toast.loading("Preparing download...");
  try {
    await fetchSignedUrlAndDownload(jobId, fileType, sanitizeFilename(songsetName), extension);
    toast.success("Download started", { id: toastId });
  } catch {
    toast.error("Download failed", { id: toastId });
  }
}, [jobId, songsetName]);
```

### Step 5: Update test mock in `webapp/src/test/components/render/RenderComplete.test.tsx`

Add `fetchSignedUrlAndDownload` to the mock:

```ts
vi.mock("@/lib/download", () => ({
  sanitizeFilename: (name: string) => name.toLowerCase().replace(/\s+/g, "-"),
  downloadArtifact: vi.fn(),
  fetchSignedUrlAndDownload: vi.fn(),
}))
```

---

## Verification

1. Run `pnpm lint` from `webapp/`
2. Run `pnpm test` from `webapp/`
3. Run `pnpm build` from `webapp/`

---

## PR Comment Replies

After implementing, reply to each comment:

### Reply to Comment 1 (download.ts:11-13)
```
Fixed in [commit hash]. Replaced `window.location.href` with hidden anchor element approach as suggested.
```

### Reply to Comment 2 (songsets/[id]/page.tsx:340-364)
```
Fixed in [commit hash]. 
- Removed redundant AbortController and AbortError check
- Extracted shared `fetchSignedUrlAndDownload()` utility in `lib/download.ts`
- Simplified all download handlers to use the new utility
```

### Reply to Comment 3 (RenderComplete.tsx:49-74)
```
Fixed in [commit hash].
- Wrapped `handleDownloadFile` in `useCallback`
- Removed redundant AbortController and AbortError check
- Now uses shared `fetchSignedUrlAndDownload()` utility
```
