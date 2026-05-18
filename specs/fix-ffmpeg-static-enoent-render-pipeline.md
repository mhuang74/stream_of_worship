# Fix: ffmpeg-static ENOENT in Render Pipeline

## Problem

Render pipeline (both MP3 and MP4) fails with:

```
spawn /ROOT/node_modules/.pnpm/ffmpeg-static@5.3.0/node_modules/ffmpeg-static/ffmpeg ENOENT
```

**Root cause:** `ffmpeg-static`'s post-install script failed to download the ffmpeg binary during `pnpm install`, but `ffmpeg-static`'s `index.js` returns the expected path as a string regardless of whether the binary actually exists on disk. This means:

1. `ffmpegStatic` resolves to a truthy path string (e.g., `.../ffmpeg-static/ffmpeg`) even though the binary file is missing
2. The fallback `?? "ffmpeg"` in `video-engine.ts:94` never triggers because `ffmpegStatic` is truthy
3. `spawn()` fails with ENOENT when trying to execute the non-existent file

**Confirmed locally:** The binary is missing at the pnpm store path. System ffmpeg exists at `/opt/homebrew/bin/ffmpeg` but is never reached due to the truthy-but-invalid `ffmpegStatic` value.

## Affected Files

| File | Issue |
|------|-------|
| `webapp/src/lib/render/video-engine.ts:11,94` | Imports `ffmpeg-static`, uses `ffmpegStatic ?? "ffmpeg"` fallback that never triggers |
| `webapp/src/lib/render/audio-engine.ts:9,14-16` | Imports `ffmpeg-static`, sets `ffmpegPath` without existence check |
| `webapp/next.config.ts:5` | `serverExternalPackages` missing `ffmpeg-static`, causing Next.js bundling issues |

## Implementation Plan

### Step 1: Add `ffmpeg-static` to `serverExternalPackages`

**File:** `webapp/next.config.ts`

Add `ffmpeg-static` to the `serverExternalPackages` list so Next.js doesn't attempt to bundle the native module (which breaks `__dirname` resolution that `ffmpeg-static` relies on to find its binary).

```typescript
serverExternalPackages: ["fastembed", "@anush008/tokenizers", "ffmpeg-static"],
```

### Step 2: Validate ffmpeg-static path in `video-engine.ts`

**File:** `webapp/src/lib/render/video-engine.ts`

Add `existsSync` check so the fallback to system `"ffmpeg"` actually works when the `ffmpeg-static` binary is missing.

**Changes:**

1. Add import at top of file:
```typescript
import { existsSync } from "fs";
```

2. Replace line 94:
```typescript
// Before:
this.ffmpegPath = options.ffmpegPath ?? ffmpegStatic ?? "ffmpeg";

// After:
this.ffmpegPath = options.ffmpegPath ?? (ffmpegStatic && existsSync(ffmpegStatic) ? ffmpegStatic : "ffmpeg");
```

This ensures that if `ffmpeg-static` returns a path to a non-existent file, we fall back to the system `ffmpeg` binary (available at `/opt/homebrew/bin/ffmpeg` on this dev machine).

### Step 3: Validate ffmpeg-static path in `audio-engine.ts`

**File:** `webapp/src/lib/render/audio-engine.ts`

Same pattern — guard against a non-existent path.

**Changes:**

1. Add import at top of file:
```typescript
import { existsSync } from "fs";
```

2. Replace lines 14-16:
```typescript
// Before:
if (ffmpegStatic) {
  ffmpeg.setFfmpegPath(ffmpegStatic);
}

// After:
if (ffmpegStatic && existsSync(ffmpegStatic)) {
  ffmpeg.setFfmpegPath(ffmpegStatic);
}
```

Without this check, `fluent-ffmpeg` will try to spawn the non-existent binary and fail with the same ENOENT error during the audio mixing phase.

### Step 4: Re-install ffmpeg-static binary locally

Run from `webapp/` directory:

```bash
pnpm install
```

This re-triggers the `ffmpeg-static` post-install script (`install.js`) which downloads the platform-specific ffmpeg binary from GitHub releases. If the download succeeds, `ffmpeg-static` will work directly without the system fallback.

If the download fails again (network/proxy issues), Steps 2-3 ensure the fallback to system `ffmpeg` at `/opt/homebrew/bin/ffmpeg` works correctly.

### Step 5: Verify both render paths work

1. **MP3-only render:** Create a render job with `videoEnabled: false`. The pipeline should complete the `mixing_audio` phase using `AudioEngine` → `fluent-ffmpeg` → system ffmpeg.

2. **MP3+MP4 render:** Create a render job with `videoEnabled: true`. The pipeline should complete both `mixing_audio` (via `AudioEngine`) and `encoding_video` (via `VideoEngine` → `spawn()`) phases.

## Verification Checklist

- [ ] `webapp/next.config.ts` includes `ffmpeg-static` in `serverExternalPackages`
- [ ] `video-engine.ts` validates `ffmpegStatic` path with `existsSync` before using it
- [ ] `audio-engine.ts` validates `ffmpegStatic` path with `existsSync` before using it
- [ ] `pnpm install` in `webapp/` completes successfully (ideally with ffmpeg binary downloaded)
- [ ] MP3-only render job completes without ENOENT error
- [ ] MP3+MP4 render job completes without ENOENT error
- [ ] `pnpm lint` passes in `webapp/`
