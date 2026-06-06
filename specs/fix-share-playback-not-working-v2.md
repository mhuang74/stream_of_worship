# Fix: Share URL Worship Playback Not Working (v2)

**Date:** 2026-06-06
**Status:** Ready for Implementation
**Supersedes:** `fix-share-playback-not-working.md`
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
| Projection `autoPlay` | No change — keep current behavior (no autoPlay, unmuted) | Adding `autoPlay` without `muted` is blocked by browsers; adding `muted` silences the projection screen. The controller page provides play/pause controls, making autoPlay unnecessary. |
| Controller auto-fullscreen | Disabled for share flow via `autoFullscreen={false}` prop | Share link recipients are casual viewers; auto-fullscreen is jarring. Authenticated flow keeps auto-fullscreen. |
| Prop naming | Rename `songsetId` → `playerId` in ControllerPlayer | Share flow passes a token, not a songset ID; generic name avoids confusion |
| Presentation API / Cast to TV | Out of scope for this fix | The codebase has no sender-side Presentation API implementation. Adding a Cast button is a separate enhancement. The share controller page will not pass Presentation API props. |
| Chapters data source | Fetch `playback.chaptersUrl` directly from client | Signed URL already provided by API; no new proxy endpoint needed |
| Audio-only fallback | Keep existing `/share/[token]/play/audio` page | Already works with `autoPlay` + native `<audio controls>` |
| Controller "Back" button | Navigate to `/share/[token]` (share landing) | Natural "go back" for share users who aren't authenticated |

---

## 2. Implementation Phases

### Phase 1: Rename `songsetId` → `playerId` in ControllerPlayer

**File:** `webapp/src/components/play/ControllerPlayer.tsx`

**Problem:** The prop `songsetId` is misleading when the share flow passes a share token as its value. Future maintainers will be confused seeing a UUID token in a prop named `songsetId`.

**Changes to `ControllerPlayerProps`:**

```tsx
// Before:
export interface ControllerPlayerProps {
  songsetId: string;
  videoSrc: string;
  chapters: Chapter[];
  isPresentationActive?: boolean;
  onPresentationConnect?: () => void;
  onPresentationDisconnect?: () => void;
  className?: string;
}

// After:
export interface ControllerPlayerProps {
  playerId: string;
  videoSrc: string;
  chapters: Chapter[];
  isPresentationActive?: boolean;
  onPresentationConnect?: () => void;
  onPresentationDisconnect?: () => void;
  className?: string;
}
```

**Changes to destructuring:**

```tsx
// Before:
const { songsetId, videoSrc, chapters, ... } = props;

// After:
const { playerId, videoSrc, chapters, ... } = props;
```

**Changes to `handleExit` (also incorporates `exitRoute` from Phase 2):**

```tsx
// Before:
const handleExit = useCallback(() => {
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  }
  router.push(`/songsets/${songsetId}/play`);
}, [router, songsetId]);

// After:
const handleExit = useCallback(() => {
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  }
  router.push(exitRoute ?? `/songsets/${playerId}/play`);
}, [router, playerId, exitRoute]);
```

**Update call site in authenticated controller page:**

**File:** `webapp/src/app/songsets/[id]/play/controller/page.tsx`

```tsx
// Before:
<ControllerPlayer
  songsetId={songsetId}
  videoSrc={videoUrl}
  chapters={chapters}
  isPresentationActive={isPresentationActive}
  onPresentationConnect={handlePresentationConnect}
  onPresentationDisconnect={handlePresentationDisconnect}
/>

// After:
<ControllerPlayer
  playerId={songsetId}
  videoSrc={videoUrl}
  chapters={chapters}
  isPresentationActive={isPresentationActive}
  onPresentationConnect={handlePresentationConnect}
  onPresentationDisconnect={handlePresentationDisconnect}
/>
```

---

### Phase 2: Add `exitRoute` and `autoFullscreen` props to ControllerPlayer

**File:** `webapp/src/components/play/ControllerPlayer.tsx`

**Problem 1:** The `handleExit` callback hardcodes navigation to `/songsets/${playerId}/play`, which is wrong for the share flow.

**Problem 2:** ControllerPlayer auto-requests fullscreen on mount (lines 339-366). This is appropriate for authenticated worship leaders but jarring for casual share link recipients who may just want an in-page video player.

**Changes to `ControllerPlayerProps`:**

```tsx
export interface ControllerPlayerProps {
  playerId: string;
  videoSrc: string;
  chapters: Chapter[];
  isPresentationActive?: boolean;
  onPresentationConnect?: () => void;
  onPresentationDisconnect?: () => void;
  exitRoute?: string;
  autoFullscreen?: boolean;
  className?: string;
}
```

**Changes to `handleExit`:**

```tsx
const handleExit = useCallback(() => {
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  }
  router.push(exitRoute ?? `/songsets/${playerId}/play`);
}, [router, playerId, exitRoute]);
```

**Changes to fullscreen logic:**

```tsx
// Before (lines 339-366): auto-requests fullscreen unconditionally
useEffect(() => {
  const requestFullscreen = async () => {
    try {
      await videoRef.current?.requestFullscreen();
    } catch { ... }
  };
  requestFullscreen();
}, [videoRef]);

// After: only auto-request if autoFullscreen is true
useEffect(() => {
  if (!autoFullscreen) return;
  const requestFullscreen = async () => {
    try {
      await videoRef.current?.requestFullscreen();
    } catch { ... }
  };
  requestFullscreen();
}, [videoRef, autoFullscreen]);
```

**Default values:** `autoFullscreen` defaults to `true` (backward compatible — authenticated flow keeps current behavior). `exitRoute` defaults to `undefined` (falls back to `/songsets/${playerId}/play`).

**No changes needed to authenticated controller call site** — defaults match current behavior.

---

### Phase 3: Create Share Controller Page

**New file:** `webapp/src/app/share/[token]/play/controller/page.tsx`

This page mirrors the authenticated controller but uses the public share API. No Presentation API props are passed (Cast to TV is out of scope for this fix).

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
| `playerId` prop | `songsetId` | `token` |
| `exitRoute` prop | Not set (default) | `/share/${token}` |
| `autoFullscreen` prop | Not set (default `true`) | `false` |
| Presentation API props | Passed (currently dead code) | Not passed |

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
      playerId={token}
      videoSrc={videoUrl}
      chapters={chapters}
      exitRoute={`/share/${token}`}
      autoFullscreen={false}
    />
  );
}
```

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

**Why:** Users need playback controls. The controller page provides full controls (play/pause, seek, volume, lyric jump list, keyboard shortcuts, media session). The projection page is only useful when cast from a controller via Presentation API (out of scope for this fix).

---

## 3. Files Modified

| File | Change Type | Phase |
|------|-------------|-------|
| `webapp/src/components/play/ControllerPlayer.tsx` | Edit: rename `songsetId` → `playerId`, add `exitRoute` and `autoFullscreen` props | 1, 2 |
| `webapp/src/app/songsets/[id]/play/controller/page.tsx` | Edit: update `songsetId` → `playerId` prop name | 1 |
| `webapp/src/app/share/[token]/play/controller/page.tsx` | **New file**: share controller page | 3 |
| `webapp/src/app/share/[token]/page.tsx` | Edit: navigate to `/play/controller` | 4 |

**Total: 3 files edited, 1 new file**

---

## 4. Implementation Order

1. **Phase 1** (rename `songsetId` → `playerId`) — refactor, needed before Phase 2
2. **Phase 2** (add `exitRoute` + `autoFullscreen` props) — depends on Phase 1
3. **Phase 3** (share controller page) — depends on Phases 1 and 2
4. **Phase 4** (landing page navigation) — depends on Phase 3

Phases 1 and 2 can be combined into a single edit of `ControllerPlayer.tsx`.

---

## 5. What This Does NOT Change

- **ProjectionPlayer** (`webapp/src/components/play/ProjectionPlayer.tsx`) — no changes. Adding `autoPlay` without `muted` is blocked by browsers; adding `muted` silences the projection screen. The controller page provides play/pause controls, making autoPlay unnecessary.
- **Audio-only page** (`/share/[token]/play/audio`) — already works, no changes needed
- **Projection page** (`/share/[token]/play/projection`) — still accessible via direct URL; will be used when Cast to TV is implemented in a future enhancement
- **Share API** (`/api/share/[token]`) — no changes; already returns all needed data including `chaptersUrl`
- **Authenticated controller** (`/songsets/[id]/play/controller`) — only prop rename (`songsetId` → `playerId`); behavior unchanged
- **Presentation API** — no sender-side implementation added; Cast to TV is a separate enhancement

---

## 6. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| `autoFullscreen={false}` — share users miss fullscreen option | ControllerPlayer still has a fullscreen button in its controls bar. Users can manually enter fullscreen. |
| Chapters signed URL expired (1-hour TTL) | Non-fatal: wrapped in try/catch, playback works without chapters. If the video URL also expires, the error state shows a "Go Back" button. Users can return to the landing page and re-click "Start Worship" to get fresh URLs. |
| `playerId` rename breaks external consumers | `ControllerPlayer` is only used in two places (auth'd controller page, share controller page). Both are updated in this change. |
| Double fetch of `/api/share/{token}` (landing page + controller page) | Minor inefficiency. Next.js App Router `router.push` does not support passing arbitrary state. The fetch is fast and the data is small. Consistent with how the authenticated controller page fetches its data. |
| Projection page still has no autoPlay | By design: `autoPlay` without `muted` is blocked by browsers. The projection page is not the primary share entry point anymore (controller page is). When Cast to TV is implemented, the controller will send play commands via Presentation API. |

---

## 7. Verification

After implementation, verify:

1. **Share landing page** → click "Start Worship" → navigates to `/share/[token]/play/controller`
2. **Controller page** → video plays with full controls (play/pause, seek, volume, lyric jump list)
3. **Controller page** → does NOT auto-enter fullscreen (unlike authenticated flow)
4. **Controller page** → fullscreen button still works when manually clicked
5. **Controller page** → chapters load and lyric jump list shows song boundaries
6. **Controller page** → "Back" button navigates to `/share/[token]` (share landing)
7. **Audio-only share** → still navigates to `/share/[token]/play/audio` with native controls
8. **Authenticated flow** → unchanged; `/songsets/{id}/play/controller` still auto-fullscreens and uses default exit route

```bash
cd webapp && pnpm lint
cd webapp && pnpm test
```
