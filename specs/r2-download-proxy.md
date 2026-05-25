# R2 Download Proxy API Route

## Problem

The "Download for offline" button in `OfflineStatus.tsx` fails because:

1. **CORS**: The browser's `fetch()` to R2 signed URLs (`*.r2.cloudflarestorage.com`) is blocked by CORS — R2 doesn't return `Access-Control-Allow-Origin` headers for cross-origin requests.
2. **Stale cache entries**: Previous buggy code stored entries with relative URL keys (e.g., `http://localhost:8080/songsets/.../renders/.../output.mp3`) that are orphaned in the `"sow-artifacts"` cache.
3. **Dual cache key schemes**: `OfflineStatus.tsx` uses raw R2 keys while `artifact-cache.ts` uses synthetic keys (`/sow-artifact-cache/{jobId}/{type}`), both writing to the same cache.

## Solution: Server-side proxy route

Add a Next.js API route that streams R2 content through the app's own origin, eliminating CORS. The browser fetches from same-origin `/api/r2/artifact/...` instead of directly from R2.

## Files to Create/Modify

| # | File | Action | Purpose |
|---|------|--------|---------|
| 1 | `webapp/src/app/api/r2/artifact/[...path]/route.ts` | **Create** | Proxy route: streams R2 object to client |
| 2 | `webapp/src/components/play/OfflineStatus.tsx` | **Modify** | Use proxy URLs instead of signed URLs for caching |
| 3 | `webapp/src/lib/offline/artifact-cache.ts` | **Modify** | Use proxy URLs as fetch source; unify cache key scheme |
| 4 | `webapp/src/app/songsets/[id]/play/controller/page.tsx` | **Modify** | Use proxy URL for chapters JSON fetch |
| 5 | `webapp/src/lib/download.ts` | **Modify** | Use proxy URL for file downloads |
| 6 | `webapp/public/sw.js` | **Modify** | Add NetworkOnly rule for `/api/r2/` |
| 7 | `webapp/src/app/api/offline/cache/route.ts` | **Modify** | Return proxy URLs instead of signed R2 URLs |

## Step 1: Create proxy route

**File:** `webapp/src/app/api/r2/artifact/[...path]/route.ts`

**Endpoint:** `GET /api/r2/artifact/{renderJobId}/{filename}`

Examples:
- `/api/r2/artifact/Rlw5W9GMBjQ6fQ8QY5C-x/output.mp3`
- `/api/r2/artifact/Rlw5W9GMBjQ6fQ8QY5C-x/output.mp4`
- `/api/r2/artifact/Rlw5W9GMBjQ6fQ8QY5C-x/chapters.json`

**Logic:**

1. Auth check via `auth.api.getSession()`
2. Parse `renderJobId` and `filename` from the catch-all `[...path]` segments
3. Validate `filename` is one of: `output.mp3`, `output.mp4`, `chapters.json`
4. DB lookup: verify the render job exists and belongs to the authenticated user (`renderJobs.userId`)
5. Verify render job status is `completed`
6. Construct the R2 key: `renders/{renderJobId}/{filename}`
7. Generate a short-lived signed URL (60s expiry — only used server-side)
8. Fetch the object from R2 via the signed URL
9. Stream the response to the client with proper headers:
   - `Content-Type`: derived from filename (audio/mpeg, video/mp4, application/json)
   - `Content-Length`: from R2 response
   - `Accept-Ranges: bytes`: for video seeking
   - `Cache-Control: private, max-age=3600`
   - If `Range` header present, pass through to R2 and return `206 Partial Content`
   - If `download=1` query param present, add `Content-Disposition: attachment`

**Range request support** is critical for video playback seeking. The route must:
- Pass the `Range` header from the client request to the R2 signed URL fetch
- Return `206 Partial Content` with `Content-Range` header when R2 responds with a partial response
- Return `200` for full responses

**Streaming:** Use `ReadableStream` to pipe the R2 response body to the client without buffering the entire file in memory. This is important for large video files.

**Pseudocode:**

```typescript
import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { renderJobs } from "@/db/schema";
import { eq, and } from "drizzle-orm";
import { createR2ClientFromEnv } from "@/lib/r2/client";

const ALLOWED_FILES = ["output.mp3", "output.mp4", "chapters.json"] as const;
const CONTENT_TYPES: Record<string, string> = {
  "output.mp3": "audio/mpeg",
  "output.mp4": "video/mp4",
  "chapters.json": "application/json",
};

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  // 1. Auth check
  const session = await auth.api.getSession({ headers: request.headers });
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  // 2. Parse path segments
  const { path } = await params;
  if (path.length !== 2) {
    return NextResponse.json({ error: "Invalid path" }, { status: 400 });
  }
  const [renderJobId, filename] = path;

  // 3. Validate filename
  if (!ALLOWED_FILES.includes(filename as typeof ALLOWED_FILES[number])) {
    return NextResponse.json({ error: "Invalid file" }, { status: 400 });
  }

  // 4. DB lookup: verify ownership
  const job = await db.query.renderJobs.findFirst({
    where: and(
      eq(renderJobs.id, renderJobId),
      eq(renderJobs.userId, Number(session.user.id))
    ),
  });
  if (!job) {
    return NextResponse.json({ error: "Not found" }, { status: 404 });
  }

  // 5. Verify completed status
  if (job.status !== "completed") {
    return NextResponse.json({ error: "Render not complete" }, { status: 409 });
  }

  // 6. Construct R2 key
  const r2Key = `renders/${renderJobId}/${filename}`;

  // 7. Generate short-lived signed URL
  const r2Client = createR2ClientFromEnv();
  const { url: signedUrl } = await r2Client.generateSignedUrl(r2Key, "audio", {
    expiresInSeconds: 60,
  });

  // 8. Fetch from R2 with range support
  const rangeHeader = request.headers.get("range");
  const r2Headers: HeadersInit = {};
  if (rangeHeader) {
    r2Headers["Range"] = rangeHeader;
  }

  const r2Response = await fetch(signedUrl, { headers: r2Headers });

  if (!r2Response.ok && r2Response.status !== 206) {
    return NextResponse.json({ error: "Failed to fetch from storage" }, { status: 502 });
  }

  // 9. Build response headers
  const responseHeaders = new Headers();
  responseHeaders.set("Content-Type", CONTENT_TYPES[filename]);
  responseHeaders.set("Accept-Ranges", "bytes");
  responseHeaders.set("Cache-Control", "private, max-age=3600");

  const contentLength = r2Response.headers.get("content-length");
  if (contentLength) {
    responseHeaders.set("Content-Length", contentLength);
  }

  const contentRange = r2Response.headers.get("content-range");
  if (contentRange) {
    responseHeaders.set("Content-Range", contentRange);
  }

  // Download disposition
  const searchParams = request.nextUrl.searchParams;
  if (searchParams.get("download") === "1") {
    const songsetName = job.songsetName || "worship";
    const ext = filename.split(".").pop();
    const safeName = songsetName.toLowerCase().replace(/[^a-z0-9]/g, "-");
    responseHeaders.set(
      "Content-Disposition",
      `attachment; filename="${safeName}.${ext}"`
    );
  }

  // 10. Stream response
  const status = r2Response.status === 206 ? 206 : 200;
  return new NextResponse(r2Response.body, {
    status,
    headers: responseHeaders,
  });
}
```

## Step 2: Update OfflineStatus to use proxy URLs

**File:** `webapp/src/components/play/OfflineStatus.tsx`

Replace the current flow:
1. ~~Call `/api/offline/cache` to get signed URLs~~
2. ~~`fetch(signedUrl)` from R2~~

With:
1. Call `/api/offline/cache` to get **proxy URLs** (see Step 7)
2. `fetch(proxyUrl)` from same origin (no CORS)
3. Cache using the `artifact-cache.ts` key scheme: `/sow-artifact-cache/{renderJobId}/{type}`

Also:
- Remove debug `console.log` statements
- Add stale cache cleanup on mount: iterate cache keys, delete any that start with `http://localhost` or the app domain (leftover from the old buggy code)
- Use `artifact-cache.ts` functions (`cacheArtifacts`, `getArtifactCacheStatus`) instead of inline cache logic to unify the cache key scheme

**Stale cache cleanup (add to useEffect):**

```typescript
useEffect(() => {
  const cleanupStaleEntries = async () => {
    if (!("caches" in window)) return;
    try {
      const cache = await caches.open("sow-artifacts");
      const keys = await cache.keys();
      for (const key of keys) {
        // Delete entries keyed by relative URLs or app-domain URLs (old buggy format)
        if (key.url.includes("/songsets/") && key.url.includes("/renders/")) {
          await cache.delete(key);
        }
      }
    } catch {}
  };
  cleanupStaleEntries();
}, []);
```

## Step 3: Unify cache key scheme in artifact-cache.ts

**File:** `webapp/src/lib/offline/artifact-cache.ts`

The library already uses stable synthetic keys (`/sow-artifact-cache/{renderJobId}/{type}`). Update `cacheArtifacts()` to accept proxy URLs as the fetch source instead of signed R2 URLs. The `url` field in `CacheableArtifacts` will now be a proxy URL like `/api/r2/artifact/{renderJobId}/output.mp3`.

No change to the cache key scheme itself — it's already correct.

## Step 4: Update controller page chapters fetch

**File:** `webapp/src/app/songsets/[id]/play/controller/page.tsx`

Currently at line 101: `fetch(chaptersUrl)` where `chaptersUrl` is a signed R2 URL. This also has a CORS issue (though it may work incidentally if R2 has permissive CORS for GET). Change to use the proxy URL:

```
/api/r2/artifact/{renderJobId}/chapters.json
```

This eliminates the need to call `/api/signed-url?fileType=json` separately — the proxy route handles auth + R2 fetch internally.

## Step 5: Update download.ts

**File:** `webapp/src/lib/download.ts`

Currently `fetchSignedUrlAndDownload()` calls `/api/signed-url` to get a signed URL, then creates an `<a href={signedUrl}>` to trigger a browser download. The browser navigates directly to the R2 signed URL.

Change to use the proxy URL with `Content-Disposition: attachment`:
- `/api/r2/artifact/{renderJobId}/output.mp3?download=1`
- The proxy route adds `Content-Disposition: attachment; filename="..."` when `download=1` is present

This eliminates the need for the `/api/signed-url` call for downloads.

**New function:**

```typescript
export async function downloadArtifactViaProxy(
  renderJobId: string,
  fileType: "audio" | "video" | "json",
  filename: string,
  extension: string
): Promise<void> {
  const artifactFile =
    fileType === "audio" ? "output.mp3" :
    fileType === "video" ? "output.mp4" : "chapters.json";
  
  const proxyUrl = `/api/r2/artifact/${renderJobId}/${artifactFile}?download=1`;
  downloadArtifact(proxyUrl);
}
```

## Step 6: Service worker exclusion

**File:** `webapp/public/sw.js`

Add a `NetworkOnly` route for `/api/r2/` to prevent the service worker from caching large binary artifacts:

```js
// R2 proxy endpoint serves large binary files – never cache.
workbox.routing.registerRoute(
  ({ url }) => url.pathname.startsWith("/api/r2/"),
  new workbox.strategies.NetworkOnly()
);
```

Add this after the existing `/api/signed-url` NetworkOnly rule (around line 69).

## Step 7: Update /api/offline/cache to return proxy URLs

**File:** `webapp/src/app/api/offline/cache/route.ts`

Instead of generating signed R2 URLs and returning them, return proxy URLs:

```json
{
  "renderJobId": "Rlw5W9GMBjQ6fQ8QY5C-x",
  "mp3Url": "/api/r2/artifact/Rlw5W9GMBjQ6fQ8QY5C-x/output.mp3",
  "mp4Url": "/api/r2/artifact/Rlw5W9GMBjQ6fQ8QY5C-x/output.mp4",
  "chaptersUrl": "/api/r2/artifact/Rlw5W9GMBjQ6fQ8QY5C-x/chapters.json"
}
```

This eliminates the need to call `r2Client.generateSignedUrl()` in this route at all — the proxy route handles R2 access. The route still verifies auth and ownership, but no longer needs R2 credentials.

**Simplified route:**

```typescript
export async function GET(request: NextRequest) {
  const session = await auth.api.getSession({ headers: request.headers });
  if (!session?.user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const renderJobId = request.nextUrl.searchParams.get("renderJobId");
  if (!renderJobId) {
    return NextResponse.json({ error: "renderJobId required" }, { status: 400 });
  }

  const job = await db.query.renderJobs.findFirst({
    where: and(
      eq(renderJobs.id, renderJobId),
      eq(renderJobs.userId, Number(session.user.id))
    ),
  });

  if (!job) {
    return NextResponse.json({ error: "Render job not found" }, { status: 404 });
  }

  if (job.status !== "completed") {
    return NextResponse.json({ error: "Render job not complete" }, { status: 409 });
  }

  // Return proxy URLs instead of signed R2 URLs
  return NextResponse.json({
    renderJobId: job.id,
    mp3Url: job.mp3R2Key ? `/api/r2/artifact/${renderJobId}/output.mp3` : null,
    mp4Url: job.mp4R2Key ? `/api/r2/artifact/${renderJobId}/output.mp4` : null,
    chaptersUrl: job.chaptersR2Key ? `/api/r2/artifact/${renderJobId}/chapters.json` : null,
  });
}
```

## What NOT to change

- **Video/audio `src` attributes** in `ControllerPlayer`, `ProjectionPlayer`, and share pages: These use `<video src={signedUrl}>` and `<audio src={signedUrl}>`. Media elements are not subject to CORS restrictions for simple playback — they work fine with signed R2 URLs. Changing these to proxy URLs would add latency and server load for no benefit. Only change if we want to hide the R2 endpoint URL from the client (optional future enhancement).
- **`/api/signed-url` route**: Keep as-is. It's still used by the video/audio player pages for `<video src>` and `<audio src>`. The proxy route is specifically for `fetch()`-based access where CORS matters.

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Proxy adds latency for large files | Stream response (don't buffer); R2 signed URL is generated server-side with 60s expiry |
| Serverless function timeout on Vercel (10s for hobby, 60s for pro) | Vercel supports streaming responses; for hobby plan, large video files may timeout — consider keeping direct signed URLs for `<video src>` playback |
| Double bandwidth cost (R2 → server → client) | Only use proxy for `fetch()`-based access (offline caching, chapters JSON, downloads); keep direct signed URLs for media `src` attributes |
| Range request complexity | R2 supports range requests natively; pass through `Range` header and `Content-Range` response header |

## Testing Checklist

- [ ] Offline download button works without CORS errors
- [ ] Cached artifacts can be played offline
- [ ] Stale cache entries are cleaned up on mount
- [ ] Chapters JSON loads in controller page
- [ ] File downloads trigger browser save dialog with correct filename
- [ ] Video seeking works (range requests)
- [ ] Service worker doesn't cache `/api/r2/` responses
- [ ] Auth required for all proxy requests
- [ ] Ownership verified (can't access another user's artifacts)
