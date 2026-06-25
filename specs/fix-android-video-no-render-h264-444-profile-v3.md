# Android Video Blank Playback — H.264 High 4:4:4 Profile + Software Fallback Gap (v3)

## Status
Approved — pending implementation. User explicitly deferred trigger of re-render (will do via webapp UI themselves).

## Problem

The Android app (debug build on Pixel 6, package `org.streamofworship.android.debug`, observed PID 30492) renders blank video in **both** inline and fullscreen player modes. User reports "mostly black with a few lit pixels — hard to tell if anything is rendered" in both modes. This persists despite the prior PR "Android playback fallback error UI" and "Fix Android render video codec compatibility" claimed to add software-based video rendering as fallback.

## Evidence (captured via `adb logcat -s SowVideo:V`)

Sequence captured at reproduction (`renderJobId=ccWknb1cqyD9s1H5qvDJM artifact=Video`):

```
07:15:44.490  SowVideo: setMedia
07:15:44.510  SowVideo: setMedia              ← double-call (load() + rebind LaunchedEffect)
07:15:45.966  SowVideo: selectedVideoFormat=mime=video/avc codecs=avc1.F40028 size=1920x1080 frameRate=24.0 rotation=0 pixelRatio=1.0 bitrate=-1
07:15:46.023  SowVideo: videoDecoderInitialized name=c2.exynos.h264.decoder software=false
07:15:46.070  SowVideo: videoSize=1920x1072
07:15:46.071  SowVideo: renderedFirstFrame    ← ExoPlayer believes a frame was rendered
```

SurfaceFlinger confirms two concurrent `SurfaceView` layers exist under `MainActivity`:

| Layer ID | Display region | Size | Source buffer | Frame state |
|---|---|---|---|---|
| 65338 `89e39ff` (stale fullscreen) | `[0, 793, 1080, 1396]` | 1080 × 603 | 1920 × 1072 | buffer pinned, no longer advancing |
| 65346 `5637c8b` (current inline) | `[42, 339, 1038, 895]` | 996 × 548 | 1920 × 1072 | 24 Hz frame production requested, `frame=890` |

Pixel 6 hardware codec service `samsung.hardware.media.c2@1.2-service` (PID 1322) logs continuous errors at ~24 Hz (one per frame attempt) throughout the entire playback session:

```
D ExynosVideoDecoder: [MFC_Decoder_Dequeue_Outbuf] error type : 1
```

## Root Cause (proven)

Two compounding defects:

### 1. Source artifact uses H.264 High 4:4:4 Predictive profile

Codec string `avc1.F40028` decodes as:
- `F4` → `profile_idc=244` → **High 4:4:4 Predictive profile**
- `00` → constraint set flags = 0 (no constraint sets for High444)
- `28` → `level_idc=40` → Level 4.0

The Pixel 6's Exynos HW decoder (`c2.exynos.h264.decoder`) reports capability for this profile (so Media3 selects it) but cannot actually decode High444 macroblock layout to its configured output color format. The decoder accepts buffers and signals "first frame rendered" but produces only black/unmappable output, while continuously failing `[MFC_Decoder_Dequeue_Outbuf] error type : 1` per frame.

### 2. The "software fallback" claimed by prior PR was a no-op

Files implicated:

- `delivery/android/app/src/main/java/org/streamofworship/android/feature/player/VideoExoPlayerFactory.kt:30-33` sets flags:
  ```kotlin
  DefaultRenderersFactory(context.applicationContext)
      .setEnableDecoderFallback(true)
      .setExtensionRendererMode(DefaultRenderersFactory.EXTENSION_RENDERER_MODE_PREFER)
  ```
- `delivery/android/app/build.gradle.kts:126-128` declares only:
  ```
  androidx.media3:media3-exoplayer:1.5.1
  androidx.media3:media3-session:1.5.1
  androidx.media3:media3-ui:1.5.1
  ```
- `delivery/android/app/src/test/java/org/streamofworship/android/feature/player/VideoExoPlayerFactoryTest.kt:18-26` uses reflection to assert the two flag values are set on `DefaultRenderersFactory`, giving false confidence that "fallback is wired up."

`EXTENSION_RENDERER_MODE_PREFER` requires extension renderer classes on the classpath to actually prefer anything. `DefaultRenderersFactory` reflectively loads `androidx.media3.decoder.ffmpeg.LiboffmpegVideoRenderer` etc.; with no `media3-decoder-ffmpeg` artifact on the classpath, there is nothing to prefer. `setEnableDecoderFallback(true)` only allows Media3 to try **another platform `MediaCodec`** from the device's codec list — on Pixel 6, no platform codec handles High444 correctly, so this flag also fails to rescue playback.

**Result:** the Exynos HW codec is always selected, fails every frame, and there is no FFmpeg software path to fall back to.

### 3. Render-worker source emits correct profile; broken artifact predates the fix

The current render-worker at `delivery/render-worker/src/sow_render/video_engine.py:129-145` is correct:

```python
def get_video_codec_args(self, bitrate: str = "8000k") -> list[str]:
    return [
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-profile:v", "high",        # profile_idc=100, compatible
        "-pix_fmt", "yuv420p",       # 4:2:0 8-bit, incompatible with High444 anyway
        "-crf", "23",
        "-b:v", bitrate,
        "-movflags", "+faststart",
    ]
```

There is an existing anti-regression guard at `delivery/render-worker/tests/test_mp4_cast_compatibility.py:107-111`:

```python
def test_video_profile_is_android_compatible_yuv420p(self, sample_render_output: Path):
    data = self._ffprobe_streams(sample_render_output)
    video_stream = next(s for s in data["streams"] if s["codec_type"] == "video")
    assert video_stream["profile"] != "High 4:4:4 Predictive"
    assert video_stream["pix_fmt"] == "yuv420p"
```

Therefore the broken `output.mp4` for job `ccWknb1cqyD9s1H5qvDJM` sitting in R2 was produced by an **older, buggy version of the render-worker** before the High profile and anti-regression guard were added. Re-rendering the job with the current code will produce a correctly-decodable file.

## Plan (in scope — approved by user)

User chose "Primary + Android hardening" scope. User chose to trigger the re-render themselves via webapp UI; dev will only verify on device afterward.

### Phase 1 — Re-render source artifact (user-driven, no code change)

1. User triggers re-render of job `ccWknb1cqyD9s1H5qvDJM` via webapp UI.
2. New `output.mp4` is written to R2 with `-profile:v high -pix_fmt yuv420p` → codec string `avc1.640028` (profile_idc=100, High profile, Level 4.0).
3. Dev verifies on device:
   - `adb logcat -c`, refresh player on device
   - `adb logcat -d -s SowVideo:V`
   - Confirm: `codecs=avc1.640028` (NOT `avc1.F40028`)
   - Confirm: `videoDecoderInitialized name=c2.exynos.h264.decoder software=false`
   - Confirm: no recurring `[MFC_Decoder_Dequeue_Outbuf] error type : 1` from PID 1322
   - Capture `adb exec-out screencap -p` to confirm visible (non-black) frames

### Phase 2 — Android hardening (code changes)

#### Change 2.1 — Add FFmpeg software decoder extension dependency

**File:** `delivery/android/app/build.gradle.kts`

**Location:** After line 128 (`media3-ui:1.5.1`)

**Edit:**
```kotlin
implementation("androidx.media3:media3-exoplayer:1.5.1")
implementation("androidx.media3:media3-session:1.5.1")
implementation("androidx.media3:media3-ui:1.5.1")
implementation("androidx.media3:media3-decoder-ffmpeg:1.5.1")   // NEW
```

**Trade-off:** APK size +~10-15 MB. Pixel 6 playback of any future High444 (or other exotic codec) will transparently fall back to FFmpeg software decode because `EXTENSION_RENDERER_MODE_PREFER` at `VideoExoPlayerFactory.kt:33` now has an extension renderer to prefer. The `softwareDecoderActive` warning UI at `PlayerScreen.kt:381-394` will surface when fallback engages.

**Native ABI requirement:** FFmpeg extension includes native libraries; ABI filters in `app/build.gradle.kts` must accept `arm64-v8a` (Pixel 6 = Tensor G1, ARM64) and optionally `armeabi-v7a`/`x86_64` for emulators. Verify existing `ndk` / `abiFilters` config does not exclude these.

#### Change 2.2 — Rewrite `VideoExoPlayerFactoryTest` to assert real renderer registration

**File:** `delivery/android/app/src/test/java/org/streamofworship/android/feature/player/VideoExoPlayerFactoryTest.kt`

**Replace** the reflection-based flag checks at lines 18-26 with assertions that inspect the actual `Renderer` array registered on the `ExoPlayer` built by `VideoExoPlayerFactory.create(...)`:

- Build an `ExoPlayer` via `VideoExoPlayerFactory.create(context)`.
- Use `player.getRendererCount()` and `player.getRendererType(i)` to enumerate renderers, OR Reflect into `DefaultRenderersFactory` to assert that `extensionRendererMode == EXTENSION_RENDERER_MODE_PREFER` AND that the FFmpeg renderer class is present.
- Stronger assertion: assert at least one video `Renderer` is a `LibavcodecVideoRenderer` (class name `androidx.media3.decoder.ffmpeg.LiboffmpegVideoRenderer`). This guarantees the extension is actually on the classpath, not just the dependency declared.
- Keep the existing `setEnableDecoderFallback(true)` + `EXTENSION_RENDERER_MODE_PREFER` flag assertions as secondary assertions.
- Release the player in a `finally` block to avoid leaking the ExoPlayer instance across tests.

**Why:** the prior test asserted `factory.privateBoolean("enableDecoderFallback") == true` via reflection. This passed even when no FFmpeg extension was on the classpath, masking the actual bug. The new assertion must fail if the extension dep is ever removed from `build.gradle.kts`.

#### Change 2.3 — Null out `playerViewState` in `DirectPlayerFacade.release()`

**File:** `delivery/android/app/src/main/java/org/streamofworship/android/feature/player/Media3PlayerController.kt`

**Location:** `DirectPlayerFacade.release()` at lines 170-177.

**Current behavior:** `playerView.release()` is called, listeners removed, but `playerViewState.value` (a `MutableStateFlow<Player?>` initialized at line 135) is never nulled. The dead ExoPlayer reference lingers as `videoPlayer` in `PlayerScreen.kt:73-74` until the controller is re-keyed and replaced. This is inconsistent with `ServiceMediaControllerFacade.release()` at line 394 which does null the state.

**Edit:**
```kotlin
override fun release() {
    listenerAdapter?.let { playerView.removeListener(it) }
    diagnosticListener?.let { playerView.removeListener(it) }
    (playerView as? ExoPlayer)?.removeAnalyticsListener(analyticsListener)
    eventListener = null
    listenerAdapter = null
    playerView.release()
    (playerViewState as? MutableStateFlow)?.value = null   // NEW — mirrors ServiceMediaControllerFacade.release()
}
```

**Note on mutability:** `playerViewState` is typed `StateFlow<Player?>` (read-only) at line 55 and stored as `MutableStateFlow` at line 135. The cast is safe because `DirectPlayerFacade` owns the instance and initializes it synchronously. Alternatively, expose a `MutableStateFlow` private field directly to avoid the cast (cleaner; preferred during implementation).

### Phase 3 — Out of scope (explicit non-changes)

- **`SowPlayerView.kt:83` `setEnableComposeSurfaceSyncWorkaround(true)`**: was a red herring from initial layout-log interpretation. The actual codec failure explains blank video in both inline and fullscreen without requiring this hypothesis. Do not modify.
- **`VideoExoPlayerFactory.kt:32-33`**: the flag configuration (`setEnableDecoderFallback(true)`, `EXTENSION_RENDERER_MODE_PREFER`) is correct. The missing piece is the FFmpeg dependency on the classpath (Change 2.1), not the flag config. Do not modify.
- **Render-worker `video_engine.py:129-145`**: already correct. Do not modify.
- **Render-worker `tests/test_mp4_cast_compatibility.py:107-110`**: the test currently asserts `profile != "High 4:4:4 Predictive"` (blacklist). User explicitly declined the option to tighten this to `profile == "High"` (equality assertion) in this round. Leave as-is.

## Verification

After Phase 2 changes land and a debug APK is installed:

1. Run targeted unit tests for the modified files:
   ```bash
   cd delivery/android
   ./gradlew testDebugUnitTest --tests "org.streamofworship.android.feature.player.VideoExoPlayerFactoryTest"
   ./gradlew testDebugUnitTest --tests "org.streamofworship.android.feature.player.Media3PlayerControllerTest"
   ./gradlew testDebugUnitTest --tests "org.streamofworship.android.feature.player.PlayerViewModelTest"
   ```
2. Lint and assemble the debug APK to confirm FFmpeg native libs link cleanly:
   ```bash
   cd delivery/android
   ./gradlew lintDebug
   ./gradlew assembleDebug
   ```
3. Install on device and inspect APK size delta (~10-15 MB expected):
   ```bash
   /opt/platform-tools/adb install -r app/build/outputs/apk/debug/app-debug.apk
   ```
4. After user reports the re-render (Phase 1) complete, run the device-side verification enumerated in Phase 1 step 3.

## Risks / Mitigations

| Risk | Mitigation |
|---|---|
| FFmpeg native library bloat exceeds acceptable APK budget | Enable ABI splits or App Bundle delivery to ship only `arm64-v8a` for Pixel 6 in release builds. |
| FFmpeg extension at 1.5.1 pulls a different Media3 transitive than `media3-exoplayer:1.5.1` (version mismatch) | Pin all `androidx.media3:*` artifacts to the same `1.5.1` version. Verify via `./gradlew :app:dependencies` after the edit. |
| Some devices may still prefer the HW decoder even with FFmpeg present (EXTENSION_RENDERER_MODE_PREFER prefers extensions over platform, but only if init succeeds) | Acceptable: if FFmpeg init succeeds it is preferred; if it fails Media3 falls back to platform codec, which is the current behavior. |
| `Media3PlayerController.release()` cast to `MutableStateFlow` could fail if class is refactored to wrap the flow | Prefer private-field approach described in the plan note; add unit test asserting `release()` sets `playerViewState.value == null`. |
| Re-render produces same broken profile due to render-worker cache hit on stale output | User should verify via `ffprobe` on the new R2 artifact before device verification, OR check `selectedVideoFormat codecs=` in device logcat as primary verification. |

## Agent Instructions Recap

As required by `AGENTS.md` and `CLAUDE.md`:
- Use `uv`/`gradlew` for build/test invocations.
- Use `pathlib.Path` style for any file operations.
- Update `report/current_impl_status.md` and `graphify-out/` after Phase 2 completion.
- Session completion mandated: `git pull --rebase` → `git push` → `git status` shows up to date. Do NOT stop before push succeeds.
- Never commit unless user explicitly asks.
