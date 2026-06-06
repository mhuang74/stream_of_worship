# Fix: Share URL Worship Playback Not Working

**Date:** 2026-06-06
**Status:** Ready for Implementation
**Bug:** Clicking "Start Worship" on a shared URL navigates to a projection page with no playback controls, no autoPlay, and no way to interact with the video.

---

## 0. Problem Statement

When a user opens a share link (e.g., `/share/f5LnfiQqL6_rd2ZHeHqHY9Ty`) and clicks "Start Worship", they are taken to `/share/[token]/play/projection` which renders a `<ProjectionPlayer>` component. This component:

1. **Has no `autoPlay`** — the `<video>` element loads but never starts playing
2. **Has no controls** — no play/pause, seek, volume, or song navigation
3. **Has no controller page** — the Presentation API receiver on the projection page never receives commands because there's no `/share/[token]/play/controller/` page to send them
4. **Never loads chapters** — the API returns `playback.chaptersUrl` but it's never fetched or used

The result: a black screen with only a fading song title overlay. The video sits on frame 0 with no user interaction possible.

---

## 1. Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Player style for share | Controller-style page (reuse `ControllerPlayer`) | Full playback controls, lyric jump list, keyboard shortcuts, media session — matches authenticated flow |
| Default navigation target | `/share/[token]/play/controller` (not `/projection`) | Users need controls; projection is only useful when cast from controller |
| Presentation API support | Yes, full support | Share users should be able to cast to TV, same as authenticated flow |
| Chapters data source | Fetch `playback.chaptersUrl` directly from client | Signed URL already provided by API; no new proxy endpoint needed |
| Projection autoPlay | Add `autoPlay` to `<video>` | Ensures video starts immediately when cast from controller, avoids dead screen |
| Audio-only fallback | Keep existing `/share/[token]/play/audio` page | Already works with `autoPlay` + native `<audio controls>` |
| Controller "Back" button | Navigate to `/share/[token]` (share landing) | Natural "go back" for share users who aren't authenticated |

---

## 2. Implementation Phases

### Phase 1: Add `autoPlay` to ProjectionPlayer

**File:** `webapp/src/components/play/ProjectionPlayer.tsx`

**Change:** Add `autoPlay` attribute to the `<video>` element (line 128-134):

```tsx
// Before:
<video
  ref={videoRef}
  src={videoSrc}
  className="w-full h-full object-cover"
  playsInline
  aria-label="Projection video"
/>

// After:
<video
  ref={videoRef}
  src={videoSrc}
  className="w-full h-full object-cover"
  playsInline
  autoPlay
  aria-label="Projection video"
/>
```

**Why:** When the projection page is opened (either directly or via Presentation API cast), the video should start playing immediately. The controller page manages play/pause via Presentation API commands, but autoPlay ensures the video doesn't sit frozen on frame 0.

---

### Phase 2: Create Share Controller Page

**New file:** `webapp/src/app/share/[token]/play/controller/page.tsx`

This page mirrors the authenticated controller at `webapp/src/app/songsets/[id]/play/controller/page.tsx` but uses the public share API instead of authenticated endpoints.

**Data flow:**

1. Fetch `GET /api/share/{token}` — public, no auth required
2. Validate `data.playback.mp4Url` exists (throw error if not)
3. Fetch chapters from `data.playback.chaptersUrl` (signed R2 URL, fetch directly)
4. Normalize chapters via `normalizeChaptersManifest()`
5. Render `<ControllerPlayer>` with video URL and chapters

**Key differences from authenticated controller:**

| Aspect | Auth'd Controller | Share Controller |
|--------|-------------------|-------------------|
| Data source | `/api/songsets/{id}` + `/api/render-jobs/{id}` + `/api/signed-url` | `/api/share/{token}` (single fetch) |
| Chapters source | `/api/r2/artifact/{jobId}/chapters.json` (proxy) | `data.playback.chaptersUrl` (direct signed URL) |
| Auth required | Yes (401 → redirect to login) | No (public endpoint) |
| `songsetId` prop | Used for navigation (`/songsets/{id}/play`) | Not needed; use `token` for navigation |
| Exit navigation | `/songsets/{id}/play` | `/share/{token}` |
| Presentation API | Supported | Supported (same hooks) |

**Implementation:**

```tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { ControllerPlayer } from "@/components/play/ControllerPlayer";
import type { Chapter } from "@/lib/render/chapters";
import { normalizeChaptersManifest } from "@/lib/render/chapters";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

export default function ShareControllerPage() {
  const params = useParams();
  const router = useRouter();
  const token = params.token as string;

  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [songsetName, setSongsetName] = useState<string>("");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isPresentationActive, setIsPresentationActive] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadData() {
      try {
        setIsLoading(true);
        setError(null);

        const res = await fetch(`/api/share/${token}`);
        if (!res.ok) {
          const data = await res.json();
          throw new Error(data.error ?? "This link is no longer available");
        }

        const data = await res.json();
        if (cancelled) return;

        if (!data.playback?.mp4Url) {
          throw new Error("No video available for this share");
        }

        setVideoUrl(data.playback.mp4Url);
        setSongsetName(data.songset?.name ?? "");

        // Fetch chapters from signed URL
        if (data.playback.chaptersUrl) {
          try {
            const chaptersRes = await fetch(data.playback.chaptersUrl);
            if (chaptersRes.ok) {
              const chaptersData = await chaptersRes.json();
              const manifest = normalizeChaptersManifest(chaptersData);
              if (!cancelled) {
                setChapters(manifest.chapters);
              }
            }
          } catch (e) {
            console.error("Failed to load chapters:", e);
            // Non-fatal: playback works without chapters
          }
        }
      } catch (err) {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : "Failed to load player";
          setError(message);
          toast.error(message);
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    if (token) {
      loadData();
    }

    return () => {
      cancelled = true;
    };
  }, [token]);

  // Listen for Presentation API messages
  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (event.data?.type === "presentation") {
        switch (event.data.action) {
          case "connected":
            setIsPresentationActive(true);
            toast.success("Connected to projection screen");
            break;
          case "disconnected":
            setIsPresentationActive(false);
            toast.info("Disconnected from projection screen");
            break;
        }
      }
    };

    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, []);

  const handlePresentationConnect = useCallback(() => {
    setIsPresentationActive(true);
  }, []);

  const handlePresentationDisconnect = useCallback(() => {
    setIsPresentationActive(false);
  }, []);

  if (isLoading) {
    return (
      <div className="fixed inset-0 bg-black flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="size-8 animate-spin text-white" />
          <p className="text-white/70">Loading player...</p>
        </div>
      </div>
    );
  }

  if (error || !videoUrl) {
    return (
      <div className="fixed inset-0 bg-black flex items-center justify-center p-4">
        <div className="text-center">
          <p className="text-white mb-4">
            {error || "Failed to load player"}
          </p>
          <button
            onClick={() => router.push(`/share/${token}`)}
            className="px-4 py-2 bg-primary text-white rounded-lg"
          >
            Go Back
          </button>
        </div>
      </div>
    );
  }

  return (
    <ControllerPlayer
      songsetId={token}  // Used for navigation; share flow uses token as identifier
      videoSrc={videoUrl}
      chapters={chapters}
      isPresentationActive={isPresentationActive}
      onPresentationConnect={handlePresentationConnect}
      onPresentationDisconnect={handlePresentationDisconnect}
    />
  );
}
```

**Note on `songsetId` prop:** The `ControllerPlayer` component uses `songsetId` only for the exit navigation (`router.push(/songsets/${songsetId}/play)`). We need to modify `ControllerPlayer` to accept a custom exit route (see Phase 3).

---

### Phase 3: Modify ControllerPlayer to Support Custom Exit Route

**File:** `webapp/src/components/play/ControllerPlayer.tsx`

**Problem:** The `handleExit` callback (line 263-270) hardcodes navigation to `/songsets/${songsetId}/play`:

```tsx
const handleExit = useCallback(() => {
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  }
  router.push(`/songsets/${songsetId}/play`);
}, [router, songsetId]);
```

**Solution:** Add an optional `exitRoute` prop. When provided, navigate to it instead of the default songset play route.

**Changes to `ControllerPlayerProps`:**

```tsx
export interface ControllerPlayerProps {
  songsetId: string;
  videoSrc: string;
  chapters: Chapter[];
  isPresentationActive?: boolean;
  onPresentationConnect?: () => void;
  onPresentationDisconnect?: () => void;
  exitRoute?: string;  // NEW: custom exit navigation route
  className?: string;
}
```

**Changes to `handleExit`:**

```tsx
const handleExit = useCallback(() => {
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  }
  router.push(exitRoute ?? `/songsets/${songsetId}/play`);
}, [router, songsetId, exitRoute]);
```

**Usage in share controller page:**

```tsx
<ControllerPlayer
  songsetId={token}
  videoSrc={videoUrl}
  chapters={chapters}
  exitRoute={`/share/${token}`}
  isPresentationActive={isPresentationActive}
  onPresentationConnect={handlePresentationConnect}
  onPresentationDisconnect={handlePresentationDisconnect}
/>
```

**Backward compatibility:** All existing call sites (authenticated controller page) don't pass `exitRoute`, so they continue using the default `/songsets/${songsetId}/play`.

---

### Phase 4: Update Share Landing Page Navigation

**File:** `webapp/src/app/share/[token]/page.tsx`

**Change:** Update `handlePlay` (line 115-123) to navigate to the controller page instead of the projection page:

```tsx
// Before:
const handlePlay = () => {
  if (!shareData?.playback.mp4Url && !shareData?.playback.mp3Url) return;
  setIsStarting(true);
  router.push(
    shareData.playback.mp4Url
      ? `/share/${token}/play/projection`
      : `/share/${token}/play/audio`
  );
};

// After:
const handlePlay = () => {
  if (!shareData?.playback.mp4Url && !shareData?.playback.mp3Url) return;
  setIsStarting(true);
  router.push(
    shareData.playback.mp4Url
      ? `/share/${token}/play/controller`
      : `/share/${token}/play/audio`
  );
};
```

**Why:** Users need playback controls. The controller page provides full controls (play/pause, seek, volume, lyric jump list, keyboard shortcuts, media session). The projection page is only useful when cast from the controller via Presentation API.

---

## 3. Files Modified

| File | Change Type | Phase |
|------|-------------|-------|
| `webapp/src/components/play/ProjectionPlayer.tsx` | Edit: add `autoPlay` to `<video>` | 1 |
| `webapp/src/app/share/[token]/play/controller/page.tsx` | **New file**: share controller page | 2 |
| `webapp/src/components/play/ControllerPlayer.tsx` | Edit: add `exitRoute` prop | 3 |
| `webapp/src/app/share/[token]/page.tsx` | Edit: navigate to `/play/controller` | 4 |

**Total: 3 files edited, 1 new file**

---

## 4. Implementation Order

1. **Phase 1** (autoPlay) — independent, smallest change, fixes the immediate "video won't play" symptom
2. **Phase 3** (ControllerPlayer exitRoute prop) — needed before Phase 2
3. **Phase 2** (share controller page) — depends on Phase 3 for custom exit route
4. **Phase 4** (landing page navigation) — depends on Phase 2 for the controller page to exist

Phases 1 and 3 can be done in parallel since they're independent.

---

## 5. What This Does NOT Change

- **Audio-only page** (`/share/[token]/play/audio`) — already works, no changes needed
- **Projection page** (`/share/[token]/play/projection`) — only adds `autoPlay`; still used when casting from controller via Presentation API
- **Share API** (`/api/share/[token]`) — no changes; already returns all needed data including `chaptersUrl`
- **Authenticated controller** (`/songsets/[id]/play/controller`) — no changes; `exitRoute` prop is optional with backward-compatible default
- **ControllerPlayer component** — minimal change (one optional prop); all existing behavior preserved

---

## 6. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| `autoPlay` blocked by browser autoplay policy | `ControllerPlayer` already handles play failure with `toast.error("Failed to start playback")`. The `autoPlay` on ProjectionPlayer is a best-effort enhancement; Presentation API `play` command is the primary trigger. |
| Chapters fetch fails (CORS, expired URL) | Non-fatal: wrapped in try/catch, playback works without chapters. Signed URLs have 1-hour expiry. |
| `ControllerPlayer` exit route change breaks existing flow | `exitRoute` is optional with default value matching current behavior. All existing call sites unchanged. |
| Share controller page accessible without auth | By design — the share API is public. The controller page only uses the public `/api/share/{token}` endpoint. |
| `songsetId` prop used as token in share flow | The prop is only used for navigation (now overridden by `exitRoute`) and as a React key. Using the share token as the value is safe. |

---

## 7. Verification

After implementation, verify:

1. **Share landing page** → click "Start Worship" → navigates to `/share/[token]/play/controller`
2. **Controller page** → video plays with full controls (play/pause, seek, volume, lyric jump list)
3. **Controller page** → chapters load and lyric jump list shows song boundaries
4. **Controller page** → "Back" button navigates to `/share/[token]` (share landing)
5. **Controller page** → Presentation API "Cast to TV" opens projection page on second screen
6. **Projection page** → video auto-plays when opened (directly or via cast)
7. **Audio-only share** → still navigates to `/share/[token]/play/audio` with native controls
8. **Authenticated flow** → unchanged; `/songsets/{id}/play/controller` still works as before

```bash
cd webapp && pnpm lint
cd webapp && pnpm test
```
