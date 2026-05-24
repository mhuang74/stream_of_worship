# Download Audio & Video from Songset KAB Menu

**Date:** 2026-05-24
**Status:** Draft

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

### Design decisions (confirmed with user)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Visibility | Always visible, **disabled** when no completed render exists | Communicates feature availability; avoids "where's download?" confusion |
| Scope | Both Editor and List page KAB menus | Consistent UX across both entry points |
| Download mechanism | Fetch-blob-`<a download>` (same as `RenderComplete.tsx`) | Reliable cross-browser, supports custom filename |
| API | Existing `GET /api/signed-url?renderJobId=<id>&fileType=audio\|video` | No new endpoints needed |

## 3. Implementation Steps

### Step 1: Create shared download utility

**File:** `webapp/src/lib/download.ts` (NEW)

Extract the fetch-blob-download pattern from `RenderComplete.tsx:54-88` into a reusable function:

```typescript
import { toast } from "sonner";

export async function downloadArtifact(
  url: string,
  filename: string,
  onLoading?: (loading: boolean) => void
): Promise<void> {
  onLoading?.(true);
  try {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error("Failed to download file");
    }

    const blob = await response.blob();
    const downloadUrl = window.URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(downloadUrl);

    toast.success(`Downloaded ${filename}`);
  } catch (error) {
    toast.error("Download failed");
    console.error("Download error:", error);
  } finally {
    onLoading?.(false);
  }
}
```

### Step 2: Refactor `RenderComplete.tsx` to use shared utility

**File:** `webapp/src/components/render/RenderComplete.tsx`

- Import `downloadArtifact` from `@/lib/download`
- Replace the inline `handleDownload` function (lines 54-88) with calls to `downloadArtifact`
- Remove the three `isDownloading*` state variables; replace with local state or pass `setIsDownloading*` as the `onLoading` callback

This is optional cleanup but keeps the download logic DRY.

### Step 3: Add `latestRenderJobId` to list page data flow

The list page currently drops `latestRenderJobId` from the API response. We need it to determine whether downloads are available.

**File:** `webapp/src/components/songset/SongsetList.tsx`
- Add `latestRenderJobId: string | null` to the `Songset` interface (line 21-32)

**File:** `webapp/src/app/songsets/page.tsx`
- Add `latestRenderJobId` to `ApiSongset` interface (line 8-18) — already present at line 16
- Pass `latestRenderJobId: songset.latestRenderJobId` in the transform at line 53-63

**File:** `webapp/src/components/songset/SongsetRow.tsx`
- Add `latestRenderJobId: string | null` to `SongsetRowProps` (line 34-53)

### Step 4: Add download menu items to `SongsetEditor.tsx`

**File:** `webapp/src/components/songset/SongsetEditor.tsx`

**Props changes:**
- Add to `SongsetEditorProps`:
  ```typescript
  onDownloadAudio: () => void;
  onDownloadVideo: () => void;
  ```

**State changes:**
- Add `isDownloadingAudio` and `isDownloadingVideo` boolean states

**Import changes:**
- Add `Download`, `FileAudio`, `FileVideo` from `lucide-react`
- Add `Loader2` (already imported)

**Menu changes** — insert after "Share" (line 278-280), before the separator (line 282):

```tsx
<DropdownMenuSeparator />
<DropdownMenuItem
  onClick={onDownloadAudio}
  disabled={!songset.latestRenderJobId || isDownloadingAudio}
>
  {isDownloadingAudio ? (
    <Loader2 className="size-4 mr-2 animate-spin" />
  ) : (
    <FileAudio className="size-4 mr-2" />
  )}
  Download Audio
</DropdownMenuItem>
<DropdownMenuItem
  onClick={onDownloadVideo}
  disabled={!songset.latestRenderJobId || isDownloadingVideo}
>
  {isDownloadingVideo ? (
    <Loader2 className="size-4 mr-2 animate-spin" />
  ) : (
    <FileVideo className="size-4 mr-2" />
  )}
  Download Video
</DropdownMenuItem>
```

The `disabled` condition uses `!songset.latestRenderJobId` — when there's no completed render, both items are grayed out. The `isDownloading*` states prevent double-clicks during an active download.

**Final menu order:**
1. Render
2. Play
3. — separator —
4. Edit description
5. Duplicate
6. Share
7. — separator —
8. Download Audio
9. Download Video
10. — separator —
11. Delete

### Step 5: Add download menu items to `SongsetRow.tsx`

**File:** `webapp/src/components/songset/SongsetRow.tsx`

**Props changes:**
- Add to `SongsetRowProps`:
  ```typescript
  onDownloadAudio?: () => void;
  onDownloadVideo?: () => void;
  ```

**State changes:**
- Add `isDownloadingAudio` and `isDownloadingVideo` boolean states

**Import changes:**
- Add `Download`, `FileAudio`, `FileVideo` from `lucide-react`
- Add `Loader2` (not currently imported)

**Menu changes** — insert after "Share" (line 156-159), before the separator (line 160):

```tsx
<DropdownMenuSeparator />
<DropdownMenuItem
  onClick={onDownloadAudio}
  disabled={!latestRenderJobId || isDownloadingAudio}
>
  {isDownloadingAudio ? (
    <Loader2 className="size-4 mr-2 animate-spin" />
  ) : (
    <FileAudio className="size-4 mr-2" />
  )}
  Download Audio
</DropdownMenuItem>
<DropdownMenuItem
  onClick={onDownloadVideo}
  disabled={!latestRenderJobId || isDownloadingVideo}
>
  {isDownloadingVideo ? (
    <Loader2 className="size-4 mr-2 animate-spin" />
  ) : (
    <FileVideo className="size-4 mr-2" />
  )}
  Download Video
</DropdownMenuItem>
```

**Final menu order:**
1. Rename
2. Duplicate
3. — separator —
4. Render
5. Play
6. Share
7. — separator —
8. Download Audio
9. Download Video
10. — separator —
11. Delete

### Step 6: Wire download handlers in `songsets/[id]/page.tsx`

**File:** `webapp/src/app/songsets/[id]/page.tsx`

Add two handler functions:

```typescript
const handleDownloadAudio = useCallback(async () => {
  if (!songset?.latestRenderJobId) return;

  try {
    const res = await fetch(
      `/api/signed-url?renderJobId=${songset.latestRenderJobId}&fileType=audio`
    );
    if (!res.ok) throw new Error("Failed to get download URL");
    const { url } = await res.json();

    const filename = `${songset.name.replace(/\s+/g, "_")}.mp3`;
    await downloadArtifact(url, filename);
  } catch {
    toast.error("Failed to download audio");
  }
}, [songset?.latestRenderJobId, songset?.name]);

const handleDownloadVideo = useCallback(async () => {
  if (!songset?.latestRenderJobId) return;

  try {
    const res = await fetch(
      `/api/signed-url?renderJobId=${songset.latestRenderJobId}&fileType=video`
    );
    if (!res.ok) throw new Error("Failed to get download URL");
    const { url } = await res.json();

    const filename = `${songset.name.replace(/\s+/g, "_")}.mp4`;
    await downloadArtifact(url, filename);
  } catch {
    toast.error("Failed to download video");
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

Add two handler functions that take a songset ID, look up the `latestRenderJobId`, fetch a signed URL, and download:

```typescript
const handleDownloadAudio = useCallback(async (id: string) => {
  const songset = songsets.find((s) => s.id === id);
  if (!songset?.latestRenderJobId) return;

  try {
    const res = await fetch(
      `/api/signed-url?renderJobId=${songset.latestRenderJobId}&fileType=audio`
    );
    if (!res.ok) throw new Error("Failed to get download URL");
    const { url } = await res.json();

    const filename = `${songset.name.replace(/\s+/g, "_")}.mp3`;
    await downloadArtifact(url, filename);
  } catch {
    toast.error("Failed to download audio");
  }
}, [songsets]);

const handleDownloadVideo = useCallback(async (id: string) => {
  const songset = songsets.find((s) => s.id === id);
  if (!songset?.latestRenderJobId) return;

  try {
    const res = await fetch(
      `/api/signed-url?renderJobId=${songset.latestRenderJobId}&fileType=video`
    );
    if (!res.ok) throw new Error("Failed to get download URL");
    const { url } = await res.json();

    const filename = `${songset.name.replace(/\s+/g, "_")}.mp4`;
    await downloadArtifact(url, filename);
  } catch {
    toast.error("Failed to download video");
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
| `webapp/src/lib/download.ts` | **NEW** | Shared `downloadArtifact()` utility |
| `webapp/src/components/render/RenderComplete.tsx` | MODIFY | Refactor to use shared utility (optional) |
| `webapp/src/components/songset/SongsetEditor.tsx` | MODIFY | Add download props, state, menu items |
| `webapp/src/components/songset/SongsetRow.tsx` | MODIFY | Add download props, state, menu items |
| `webapp/src/components/songset/SongsetList.tsx` | MODIFY | Add `latestRenderJobId` to `Songset` type, add callback props, thread to `SongsetRow` |
| `webapp/src/app/songsets/[id]/page.tsx` | MODIFY | Add download handlers, pass to `SongsetEditor` |
| `webapp/src/app/songsets/page.tsx` | MODIFY | Add `latestRenderJobId` to data flow, add download handlers, pass to `SongsetList` |

## 5. No New API Endpoints

The existing `GET /api/signed-url` endpoint already supports:
- `renderJobId` + `fileType=audio` → signed URL for `renders/{jobId}/output.mp3`
- `renderJobId` + `fileType=video` → signed URL for `renders/{jobId}/output.mp4`

No backend changes required.

## 6. Edge Cases

| Case | Behavior |
|------|----------|
| No completed render (`latestRenderJobId` is null) | Both download items disabled (grayed out) |
| Render completed but MP4 not generated (audio-only render) | "Download Video" disabled; "Download Audio" enabled. **Note:** Currently we only check `latestRenderJobId`, not individual artifact existence. For full accuracy, we'd need to also check `mp3R2Key`/`mp4R2Key` on the render job. See §7. |
| Stale artifacts (songset modified after render) | Download still works — user gets the old render. The stale banner already warns them. |
| Download in progress | Menu item shows spinner, disabled to prevent double-click |
| Signed URL expired (unlikely — 1hr default) | `fetch()` fails, toast shows "Download failed" |

## 7. Future Improvements (out of scope)

- **Per-artifact availability check:** Currently we only check `latestRenderJobId !== null`. For full accuracy, the songset API could return `hasAudio`/`hasVideo` booleans (derived from `mp3R2Key`/`mp4R2Key` on the render job), and we'd disable "Download Video" when only audio was rendered. This requires an API change and is deferred.
- **Share page download:** The share API already returns `allowDownload` and signed URLs. Adding download-to-file on the public share page is a separate feature.
- **Chapters JSON download:** Could add "Download Chapters" as a third menu item, but low priority.
