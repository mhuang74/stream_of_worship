# Download Audio & Video from Songset KAB Menu — v2

**Date:** 2026-05-24
**Status:** Draft
**Supersedes:** v1 (2026-05-24)

---

## 1. Problem

Currently, downloading rendered artifacts to disk is only possible from the **Render Completed** screen (`RenderComplete.tsx`). Users must complete a render and stay on that screen to download MP3/MP4 files. There is no way to download artifacts from:

- **Songset Editor screen** — the primary prep workspace
- **Songset list page** — the overview of all songsets

The Play screen's "download for offline" only caches to browser Cache Storage for offline playback — it does not save a file to disk.

## 2. Goal

Add **"Download Audio"** and **"Download Video"** menu items to the KAB (kebab/overflow) menu on both:

1. **Songset Editor screen** (`SongsetEditor.tsx`)
2. **Songset list page** (`SongsetRow.tsx`)

### Design decisions (updated from v1)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Visibility | Always visible, **disabled** when no completed render exists | Communicates feature availability; avoids "where's download?" confusion |
| Scope | Both Editor and List page KAB menus | Consistent UX across both entry points |
| Download mechanism | **Direct URL navigation** with `Content-Disposition: attachment` | Avoids buffering entire file in JS memory; browser handles download with native progress bar |
| Signed URL | Existing `GET /api/signed-url` with `contentDisposition` param | R2 returns `Content-Disposition: attachment; filename="..."` header — browser triggers file save |
| Progress indication | `toast.loading()` from sonner | Visible after KAB menu closes; no component state needed |
| Error handling | Caller-only toasts; `downloadArtifact()` is toast-free | Eliminates double-toast bug from v1 |
| Filename sanitization | Slugify: lowercase, hyphens, strip unsafe chars | Safe across OSes; works for Chinese names (e.g. "何等恩典" → "何等恩典", "Sunday Worship" → "sunday-worship") |
| Abort support | `AbortController` on signed URL fetch | Prevents state updates on unmounted components |
| Prop optionality | `onDownloadAudio?` / `onDownloadVideo?` (optional on both Editor and Row) | Consistent; safe if rendered from other contexts |

## 3. Implementation Steps

### Step 1: Create shared download utility

**File:** `webapp/src/lib/download.ts` (NEW)

```typescript
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
  window.location.href = url;
}
```

**Design notes:**
- `sanitizeFilename` strips filesystem-unsafe chars, replaces whitespace with hyphens, collapses repeated hyphens, trims leading/trailing hyphens, lowercases. Chinese characters pass through unchanged (they are safe in filenames on modern OSes).
- `downloadArtifact` is intentionally minimal — no toasts, no blob handling, no state. The signed URL carries `Content-Disposition: attachment; filename="..."` so the browser downloads the file and stays on the current page.
- No `revokeObjectURL` concern — no blob URLs are created.

### Step 2: Refactor `RenderComplete.tsx` to use shared utility

**File:** `webapp/src/components/render/RenderComplete.tsx`

**Current state (lines 50-88):** Three `isDownloading*` booleans + inline `handleDownload` using fetch-blob-createObjectURL pattern.

**Changes:**
1. Import `sanitizeFilename`, `downloadArtifact` from `@/lib/download`
2. Remove `isDownloadingAudio`, `isDownloadingVideo`, `isDownloadingChapters` state variables (lines 50-52)
3. Remove the inline `handleDownload` function (lines 54-88)
4. Replace each download button's `onClick` with a new handler that:
   - Shows `toast.loading("Preparing download...")` with a stored `toastId`
   - Fetches a signed URL with `contentDisposition` param
   - Calls `downloadArtifact(url)` on success
   - Dismisses loading toast → shows success or error toast
5. Remove `Loader2` spinner from download buttons (the loading toast replaces it)
6. Remove `Download` icon import (no longer used in buttons — the browser's download bar provides feedback)

**New handler pattern:**

```typescript
const handleDownloadFile = async (
  fileType: "audio" | "video" | "json",
  extension: string,
) => {
  const toastId = toast.loading("Preparing download...");
  const controller = new AbortController();

  try {
    const filename = sanitizeFilename(songsetName);
    const disposition = `attachment; filename="${filename}.${extension}"`;
    const res = await fetch(
      `/api/signed-url?renderJobId=${encodeURIComponent(jobId)}` +
        `&fileType=${fileType}` +
        `&contentDisposition=${encodeURIComponent(disposition)}`,
      { signal: controller.signal }
    );
    if (!res.ok) throw new Error("Failed to get download URL");
    const { url } = await res.json();

    downloadArtifact(url);
    toast.success("Download started", { id: toastId });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") return;
    toast.error("Download failed", { id: toastId });
  }
};
```

**Button changes** — each button becomes:

```tsx
{hasAudio && (
  <Button
    variant="outline"
    className="w-full justify-start gap-3"
    onClick={() => handleDownloadFile("audio", "mp3")}
  >
    <Music className="size-4" />
    <span className="flex-1 text-left">Download Audio (MP3)</span>
  </Button>
)}
```

(Same pattern for Video and Chapters buttons.)

**File:** `webapp/src/app/songsets/[id]/render/page.tsx`

The parent currently pre-fetches signed URLs without `contentDisposition` (lines 192-205) and passes them as `mp3Url`/`mp4Url`/`chaptersUrl` props. After this refactor, `RenderComplete` fetches its own signed URLs on demand, so:

1. Remove the `fetchSignedUrl` helper and the signed URL pre-fetching logic (lines 190-205)
2. Remove `mp3Url`, `mp4Url`, `chaptersUrl` from the `JobData` interface (lines 40-42)
3. Remove `mp3Url`, `mp4Url`, `chaptersUrl` from the `RenderComplete` props spread (lines 304-306)
4. Update `RenderCompleteProps` to remove `mp3Url?`, `mp4Url?`, `chaptersUrl?` — replace with just `jobId` (already present) and `songsetName` (already present)

### Step 3: Add `latestRenderJobId` to list page data flow

**File:** `webapp/src/components/songset/SongsetList.tsx`
- Add `latestRenderJobId: string | null` to the `Songset` interface (after line 31)

**File:** `webapp/src/app/songsets/page.tsx`
- Add `latestRenderJobId: songset.latestRenderJobId` to the transform at line 53-63

**File:** `webapp/src/components/songset/SongsetRow.tsx`
- Add `latestRenderJobId: string | null` to `SongsetRowProps` (after line 44)

### Step 4: Add download menu items to `SongsetEditor.tsx`

**File:** `webapp/src/components/songset/SongsetEditor.tsx`

**Props changes** — add to `SongsetEditorProps` (after line 68):
```typescript
onDownloadAudio?: () => void;
onDownloadVideo?: () => void;
```

**No state changes** — no `isDownloadingAudio`/`isDownloadingVideo` needed. The loading toast is visible after the menu closes.

**Import changes:**
- Add `FileAudio`, `FileVideo` from `lucide-react`
- `Loader2` already imported (line 43) — no longer needed for download spinners but keep for other uses

**Menu changes** — insert after "Share" (line 278-281), before the separator (line 282):

```tsx
<DropdownMenuItem
  onClick={onDownloadAudio}
  disabled={!songset.latestRenderJobId}
>
  <FileAudio className="size-4 mr-2" />
  Download Audio
</DropdownMenuItem>
<DropdownMenuItem
  onClick={onDownloadVideo}
  disabled={!songset.latestRenderJobId}
>
  <FileVideo className="size-4 mr-2" />
  Download Video
</DropdownMenuItem>
<DropdownMenuSeparator />
```

**Final menu order:**
1. Render
2. Play
3. — separator —
4. Edit description
5. Duplicate
6. Share
7. Download Audio
8. Download Video
9. — separator —
10. Delete

### Step 5: Add download menu items to `SongsetRow.tsx`

**File:** `webapp/src/components/songset/SongsetRow.tsx`

**Props changes** — add to `SongsetRowProps` (after line 50):
```typescript
onDownloadAudio?: () => void;
onDownloadVideo?: () => void;
```

**No state changes** — same rationale as Step 4.

**Import changes:**
- Add `FileAudio`, `FileVideo` from `lucide-react`
- `Loader2` is not needed for download items (no spinner state)

**Menu changes** — insert after "Share" (line 156-158), before the separator (line 160):

```tsx
<DropdownMenuItem
  onClick={onDownloadAudio}
  disabled={!latestRenderJobId}
>
  <FileAudio className="size-4 mr-2" />
  Download Audio
</DropdownMenuItem>
<DropdownMenuItem
  onClick={onDownloadVideo}
  disabled={!latestRenderJobId}
>
  <FileVideo className="size-4 mr-2" />
  Download Video
</DropdownMenuItem>
<DropdownMenuSeparator />
```

**Final menu order:**
1. Rename
2. Duplicate
3. — separator —
4. Render
5. Play
6. Share
7. Download Audio
8. Download Video
9. — separator —
10. Delete

### Step 6: Wire download handlers in `songsets/[id]/page.tsx`

**File:** `webapp/src/app/songsets/[id]/page.tsx`

Add two handler functions:

```typescript
import { downloadArtifact, sanitizeFilename } from "@/lib/download";

const handleDownloadAudio = useCallback(async () => {
  if (!songset?.latestRenderJobId) return;

  const toastId = toast.loading("Preparing download...");
  const controller = new AbortController();

  try {
    const filename = sanitizeFilename(songset.name);
    const disposition = `attachment; filename="${filename}.mp3"`;
    const res = await fetch(
      `/api/signed-url?renderJobId=${encodeURIComponent(songset.latestRenderJobId)}` +
        `&fileType=audio` +
        `&contentDisposition=${encodeURIComponent(disposition)}`,
      { signal: controller.signal }
    );
    if (!res.ok) throw new Error("Failed to get download URL");
    const { url } = await res.json();

    downloadArtifact(url);
    toast.success("Download started", { id: toastId });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") return;
    toast.error("Failed to download audio", { id: toastId });
  }
}, [songset?.latestRenderJobId, songset?.name]);

const handleDownloadVideo = useCallback(async () => {
  if (!songset?.latestRenderJobId) return;

  const toastId = toast.loading("Preparing download...");
  const controller = new AbortController();

  try {
    const filename = sanitizeFilename(songset.name);
    const disposition = `attachment; filename="${filename}.mp4"`;
    const res = await fetch(
      `/api/signed-url?renderJobId=${encodeURIComponent(songset.latestRenderJobId)}` +
        `&fileType=video` +
        `&contentDisposition=${encodeURIComponent(disposition)}`,
      { signal: controller.signal }
    );
    if (!res.ok) throw new Error("Failed to get download URL");
    const { url } = await res.json();

    downloadArtifact(url);
    toast.success("Download started", { id: toastId });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") return;
    toast.error("Failed to download video", { id: toastId });
  }
}, [songset?.latestRenderJobId, songset?.name]);
```

Pass to `SongsetEditor`:
```tsx
<SongsetEditor
  ...
  onDownloadAudio={handleDownloadAudio}
  onDownloadVideo={handleDownloadVideo}
/>
```

### Step 7: Wire download handlers in `songsets/page.tsx`

**File:** `webapp/src/app/songsets/page.tsx`

Add two handler functions:

```typescript
import { downloadArtifact, sanitizeFilename } from "@/lib/download";

const handleDownloadAudio = useCallback(async (id: string) => {
  const songset = songsets.find((s) => s.id === id);
  if (!songset?.latestRenderJobId) return;

  const toastId = toast.loading("Preparing download...");
  const controller = new AbortController();

  try {
    const filename = sanitizeFilename(songset.name);
    const disposition = `attachment; filename="${filename}.mp3"`;
    const res = await fetch(
      `/api/signed-url?renderJobId=${encodeURIComponent(songset.latestRenderJobId)}` +
        `&fileType=audio` +
        `&contentDisposition=${encodeURIComponent(disposition)}`,
      { signal: controller.signal }
    );
    if (!res.ok) throw new Error("Failed to get download URL");
    const { url } = await res.json();

    downloadArtifact(url);
    toast.success("Download started", { id: toastId });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") return;
    toast.error("Failed to download audio", { id: toastId });
  }
}, [songsets]);

const handleDownloadVideo = useCallback(async (id: string) => {
  const songset = songsets.find((s) => s.id === id);
  if (!songset?.latestRenderJobId) return;

  const toastId = toast.loading("Preparing download...");
  const controller = new AbortController();

  try {
    const filename = sanitizeFilename(songset.name);
    const disposition = `attachment; filename="${filename}.mp4"`;
    const res = await fetch(
      `/api/signed-url?renderJobId=${encodeURIComponent(songset.latestRenderJobId)}` +
        `&fileType=video` +
        `&contentDisposition=${encodeURIComponent(disposition)}`,
      { signal: controller.signal }
    );
    if (!res.ok) throw new Error("Failed to get download URL");
    const { url } = await res.json();

    downloadArtifact(url);
    toast.success("Download started", { id: toastId });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") return;
    toast.error("Failed to download video", { id: toastId });
  }
}, [songsets]);
```

Pass to `SongsetList`:
```tsx
<SongsetList
  ...
  onDownloadAudio={handleDownloadAudio}
  onDownloadVideo={handleDownloadVideo}
/>
```

### Step 8: Thread callbacks through `SongsetList.tsx`

**File:** `webapp/src/components/songset/SongsetList.tsx`

- Add to `SongsetListProps`:
  ```typescript
  onDownloadAudio?: (id: string) => void;
  onDownloadVideo?: (id: string) => void;
  ```

- Destructure in component function
- Pass to `SongsetRow` in the render loop (line 238-248):
  ```tsx
  <SongsetRow
    key={songset.id}
    {...songset}
    onRender={() => onRender?.(songset.id)}
    onPlay={() => onPlay?.(songset.id)}
    onRetry={() => onRetry?.(songset.id)}
    onRename={() => openRenameDialog(songset.id, songset.name)}
    onDuplicate={() => onDuplicate?.(songset.id)}
    onShare={() => onShare?.(songset.id)}
    onDownloadAudio={() => onDownloadAudio?.(songset.id)}
    onDownloadVideo={() => onDownloadVideo?.(songset.id)}
    onDelete={() => openDeleteDialog(songset.id)}
  />
  ```

## 4. File Change Summary

| File | Action | Description |
|------|--------|-------------|
| `webapp/src/lib/download.ts` | **NEW** | `sanitizeFilename()` + `downloadArtifact()` utilities |
| `webapp/src/components/render/RenderComplete.tsx` | MODIFY | Remove fetch-blob pattern, use `downloadArtifact()` + `toast.loading()`, remove `isDownloading*` states |
| `webapp/src/app/songsets/[id]/render/page.tsx` | MODIFY | Remove pre-fetched signed URLs; `RenderComplete` now fetches on demand |
| `webapp/src/components/songset/SongsetEditor.tsx` | MODIFY | Add optional download props, add menu items (no state changes) |
| `webapp/src/components/songset/SongsetRow.tsx` | MODIFY | Add `latestRenderJobId` + download props, add menu items (no state changes) |
| `webapp/src/components/songset/SongsetList.tsx` | MODIFY | Add `latestRenderJobId` to `Songset` type, add callback props, thread to `SongsetRow` |
| `webapp/src/app/songsets/[id]/page.tsx` | MODIFY | Add download handlers, pass to `SongsetEditor` |
| `webapp/src/app/songsets/page.tsx` | MODIFY | Add `latestRenderJobId` to data flow, add download handlers, pass to `SongsetList` |

## 5. No New API Endpoints

The existing `GET /api/signed-url` endpoint already supports:
- `renderJobId` + `fileType=audio` → signed URL for `renders/{jobId}/output.mp3`
- `renderJobId` + `fileType=video` → signed URL for `renders/{jobId}/output.mp4`
- `contentDisposition` → sets `ResponseContentDisposition` on the signed URL

No backend changes required.

## 6. v1 → v2 Changes Summary

| Concern | v1 Approach | v2 Approach |
|---------|-------------|-------------|
| **Double error toasts** (#1) | `downloadArtifact()` shows toast + caller shows toast | `downloadArtifact()` is toast-free; caller owns all toasts |
| **Invisible spinner** (#2) | `isDownloadingAudio`/`isDownloadingVideo` state + spinner in menu | `toast.loading("Preparing download...")` — visible after menu closes |
| **Large files in memory** (#3) | `fetch → blob → createObjectURL → <a click>` | `window.location.href = signedUrl` with `Content-Disposition: attachment` |
| **revokeObjectURL timing** (#4) | Called immediately after `link.click()` | Eliminated — no blob URLs created |
| **Filename sanitization** (#5) | `name.replace(/\s+/g, "_")` | `sanitizeFilename()` — slugify, strip unsafe chars, lowercase |
| **No AbortController** (#6) | None | `AbortController` on signed URL fetch; abort silently on unmount |
| **Required vs optional props** (#7) | Required on `SongsetEditor`, optional on `SongsetRow` | Optional on both |

## 7. Edge Cases

| Case | Behavior |
|------|----------|
| No completed render (`latestRenderJobId` is null) | Both download items disabled (grayed out) |
| Render completed but MP4 not generated (audio-only render) | "Download Video" enabled but click fails at signed URL fetch (R2 key not found → 404 from API → toast error). See §8 for future improvement. |
| Stale artifacts (songset modified after render) | Download still works — user gets the old render. The stale banner already warns them. |
| Signed URL fetch in progress | `toast.loading()` visible; menu item not disabled (menu is already closed). AbortController prevents stale updates if component unmounts. |
| Signed URL expired (unlikely — 1hr default) | `fetch()` to `/api/signed-url` succeeds (generates fresh URL); the signed URL itself is newly minted. No issue. |
| Browser blocks navigation to signed URL | Rare (popup blocker won't block `window.location.href` to same-tab). If it occurs, user sees error toast from the catch block. |

## 8. Future Improvements (out of scope)

- **Per-artifact availability check:** Currently we only check `latestRenderJobId !== null`. For full accuracy, the songset API could return `hasAudio`/`hasVideo` booleans (derived from `mp3R2Key`/`mp4R2Key` on the render job), and we'd disable "Download Video" when only audio was rendered. This requires an API change and is deferred.
- **Share page download:** The share API already returns `allowDownload` and signed URLs. Adding download-to-file on the public share page is a separate feature.
- **Chapters JSON download:** Could add "Download Chapters" as a third menu item, but low priority.
- **Download progress bar:** `window.location.href` relies on the browser's built-in download progress. For a custom progress UI, we'd need to fall back to the fetch-blob approach (with streaming) for large files. This is a significant complexity increase and is deferred.
