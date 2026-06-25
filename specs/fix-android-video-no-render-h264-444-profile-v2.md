# Fix Android Worship Playback: H.264 High 4:4:4 Profile Not Rendered by Pixel 6 HW Decoder

**Status:** Implemented — v2 fallback/error handling
**Date:** 2026-06-26
**Scope:** `delivery/render-worker` (implemented primary fix) + `delivery/android` (implemented decoder fallback and UX hardening)
**Symptom:** In Worship Playback (video mode) on Android, the lyrics video does not render. In Full Screen mode only a few bright pixels ("colored dots") appear in the middle of the screen, as if the video were shrunk to near-zero dimensions. Audio of the render plays correctly.
**Current implementation note:** The active fix forces render-worker output to H.264 `yuv420p` with an explicit compatible profile request, enables Media3 decoder fallback, adds software-decoder warning detection, and surfaces retryable playback errors for decoder failures.

---

## 1. Root cause (verified via `adb logcat`)

The render worker's FFmpeg encoder args omit `-pix_fmt` / `-profile:v` for the output encoder:

`delivery/render-worker/src/sow_render_worker/video_engine.py:129-141`
```python
def get_video_codec_args(self, bitrate: str = "8000k") -> list[str]:
    return [
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "23",
        "-b:v", bitrate,
        "-movflags", "+faststart",
    ]
```

The pipeline pipes libx264 **`rgb24`** (4:4:4 chroma) input (`-pix_fmt rgb24` at `video_engine.py:322-323`). With no output `-pix_fmt` / `-profile:v` override, libx264 preserves the input chroma and emits **H.264 High 4:4:4 Predictive profile**, codec string `avc1.F40028` (profile_idc=244, level 4.0).

The Pixel 6's Tensor G1 / Exynos MFC hardware H.264 decoder cannot decode High 4:4:4 Predictive profile. Captured from the device:

```
MediaCodecInfo: NoSupport [codec.profileLevel, avc1.F40028, video/avc] [c2.exynos.h264.decoder, video/avc] [oriole, Pixel 6, Google, 37]
MediaCodecRenderer: Format exceeds selected codec's capabilities [id=1, mimeType=video/avc, codecs=avc1.F40028, res=1920x1080, color=NA/NA/NA/8/8, fps=24.0, c2.exynos.h264.decoder]
ExynosVideoDecoder: [MFC_Decoder_Dequeue_Outbuf] error type : 1   (repeats every frame)
```

ExoPlayer logs `Format exceeds selected codec's capabilities` but Media3 has **no automatic software-decoder fallback by default** — so it keeps feeding the HW decoder, which emits only stray garbage frames → the "few colored dots" symptom.

**Why audio still plays:** Audio uses a separate software decoder path (`c2.android.aac.decoder`, codec `mp4a.40.2`) that is always supported. The video track is the only one affected.

**Why desktop players (VLC/QuickTime) play the MP4 fine:** Desktop players software-decode High 4:4:4 Predictive profile without issue. The user verified the MP4 content is good on desktop, which correctly ruled out the render-worker content pipeline and the Android player layout — the issue is purely the codec profile compatibility.

### Other subsystems ruled out

- **Android player wiring** (`PlayerScreen.kt`, `Media3PlayerController.kt`, `VideoExoPlayerFactory.kt`, `SowNavGraph.kt`, `PlayerViewModel.kt`): single ExoPlayer instance is shared between the audio path and the `PlayerView` surface — confirmed by reading all files. The "two players" hypothesis is invalid.
- **Compose layout** (`PlayerScreen.kt:154-182` fullscreen, `:200-220` inline): correct `fillMaxSize()` + `AspectRatioFrameLayout.RESIZE_MODE_FIT`. No small fixed sizes, no `wrap_content` chains.
- **MP4 dimensions**: `ffprobe`-equivalent data from logcat shows `width=1920`, `height=1088` (1088 = 1080 padded to macroblock), SAR 1:1. Container is correct.
- **Webapp signed-URL flow**: API returns presigned R2 URL to `renders/{jobId}/output.mp4` with `Content-Type: video/mp4`. Correct.
- **Render-worker MP4 production** (`video_engine.py:313-337`, `frame_renderer.py`): frames produced at full 1920x1080, FFmpeg encodes from `rawvideo` pipe with `+faststart`. Correct.

---

## 2. Render-worker fix (primary)

### 2.1 Change to `get_video_codec_args`

`delivery/render-worker/src/sow_render_worker/video_engine.py:129-141`

```python
def get_video_codec_args(self, bitrate: str = "8000k") -> list[str]:
    return [
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-crf", "23",
        "-b:v", bitrate,
        "-movflags", "+faststart",
    ]
```

**Rationale:**

- `-pix_fmt yuv420p` forces 4:2:0 chroma subsampling on the *output* (the existing `-pix_fmt rgb24` at `video_engine.py:322-323` still correctly sets the *rawvideo input* format — these are two separate FFmpeg args, the first before `-i` (decoder format) and the second after `-i` (encoder format)).
- `-profile:v high` asks libx264 for a common hardware-decoder-compatible profile rather than allowing 4:4:4 profile inference.
- Resulting output is **H.264 4:2:0 (`yuv420p`)**, avoiding `avc1.F40028` / High 4:4:4 Predictive. Extremely simple outputs may probe as an even lower compatible profile such as Constrained Baseline.
- Universally supported by all Android HW decoders (Exynos/MFC, Adreno, Mali), iOS, Chromecast, browsers.

**Lambda memory impact:** The switch from `rgb24` (4:4:4) input / output to `yuv420p` (4:2:0) output **reduces** the encoder's chroma-plane memory footprint by ~50%. No increase in Lambda memory pressure; if anything, encoding marginal memory headroom improves slightly. No change to Lambda memory configuration is required.

**Single source reused:** `get_video_codec_args()` is called both by `encode_video_with_ffmpeg` (`video_engine.py:330`) and by `generate_blank_video` (`video_engine.py:561`). Both code paths inherit the fix.

### 2.2 Test updates

`delivery/render-worker/tests/test_video_engine.py`

Existing tests at lines 169, 186, 203, 210 call `engine.get_video_codec_args(...)` and assert args. Add assertions to each:

```python
args = engine.get_video_codec_args()
assert "-profile:v" in args
assert args[args.index("-profile:v") + 1] == "high"
assert "-pix_fmt" in args
assert args[args.index("-pix_fmt") + 1] == "yuv420p"
```

**Compatibility with existing `-pix_fmt` assertions (lines 337, 1131, 1209-1210):**

The existing test at line 1209-1210 reads:
```python
pix_fmt_idx = cmd.index("-pix_fmt")
assert cmd[pix_fmt_idx + 1] == "rgb24"
```

`cmd.index("-pix_fmt")` returns the **first** occurrence, which is the rawvideo input format arg (before `-i`), so `cmd[pix_fmt_idx + 1] == "rgb24"` **continues to pass**. The new `-pix_fmt yuv420p` is the second occurrence (after `-i`, configuring the encoder output). No existing test breaks.

### 2.3 Existing cast-compatibility test

`delivery/render-worker/tests/test_mp4_cast_compatibility.py` already exists and asserts cast-compatible profile flags. The new H.264 / `yuv420p` output strictly improves cast compatibility vs. the previous `avc1.F40028` / High-4:4:4-Predictive output. If any assertion in that file expects the old behavior, update it; otherwise it passes unchanged.

---

## 3. Android ExoPlayer defense-in-depth

### 3.1 Change to `VideoExoPlayerFactory`

`delivery/android/app/src/main/java/org/streamofworship/android/feature/player/VideoExoPlayerFactory.kt`

Replace the bare `ExoPlayer.Builder(context.applicationContext)` with a `DefaultRenderersFactory` that enables decoder fallback:

```kotlin
package org.streamofworship.android.feature.player

import android.content.Context
import androidx.annotation.OptIn
import androidx.media3.common.C
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer

@OptIn(UnstableApi::class)
object VideoExoPlayerFactory {
    fun create(context: Context): ExoPlayer {
        val renderersFactory =
            DefaultRenderersFactory(context.applicationContext)
                .setEnableDecoderFallback(true)
                .setExtensionRendererMode(DefaultRenderersFactory.EXTENSION_RENDERER_MODE_PREFER)
        return ExoPlayer.Builder(context.applicationContext, renderersFactory)
            .setHandleAudioBecomingNoisy(true)
            .build()
            .apply {
                setWakeMode(C.WAKE_MODE_NETWORK)
                setVideoScalingMode(C.VIDEO_SCALING_MODE_SCALE_TO_FIT)
            }
    }
}
```

**Rationale:**

- `setEnableDecoderFallback(true)` instructs Media3 to try the next available decoder when the first reports `NoSupport` for a given profile/level. Eliminates the silent-fail mode where the HW decoder accepts the codec but produces garbage frames.
- `EXTENSION_RENDERER_MODE_PREFER` is harmless unless the optional media3 FFmpeg extension artifact is added later. **⚠️ Operational note:** If the media3-exoplayer-ffmpeg extension is ever added to the Gradle dependencies, this flag will prefer the FFmpeg software decoder (~30 MB APK bloat) over the platform decoder. Any future addition of that extension must be paired with `EXTENSION_RENDERER_MODE_OFF` (or removal of this factory flag) to avoid regressing to heavy software decoding by default. We are **not** adding the extension in this fix.
- No new Gradle dependencies: `androidx.media3.exoplayer.DefaultRenderersFactory` is part of the existing `androidx.media3:media3-exoplayer:1.5.1` artifact already pinned in `app/build.gradle.kts:126-128`.
- Preserves all existing configuration: wake mode, audio-becoming-noisy handling, `SCALE_TO_FIT` scaling.

### 3.2 Decoder-fallback/battery UX wiring

Add a `Player.Listener` + `AnalyticsListener` in `Media3PlayerController` (or the class that wraps the `ExoPlayer` instance) to detect when video decoder fallback activates and surface a user-facing warning.

`delivery/android/app/src/main/java/org/streamofworship/android/feature/player/Media3PlayerController.kt`

**New behavior:**

1. Register an `AnalyticsListener` that watches `onVideoDecoderInitialized`. If the decoder name contains `c2.android.avc.decoder` (the platform software AVC decoder) or any non-hardware decoder string, emit a `PlaybackWarning.SoftwareDecoderActive` event to the `PlayerViewModel`.
2. The `PlayerViewModel` exposes a `softwareDecoderWarning: StateFlow<Boolean>` (or `SharedFlow<PlaybackWarning>`). When active, `PlayerScreen.kt` shows a non-blocking snackbar / banner: **"Video playback is using software decoding. Battery may drain faster."**
3. The warning auto-dismisses when the next item uses hardware decoding, or after 5 seconds.
4. The warning is **not** shown for the primary expected path (H.264 / `yuv420p` renders on a healthy device). It only appears for edge cases: old renders (`avc1.F40028`), very low-end devices that lack hardware support for the produced profile, or future unexpected profiles.

**Why this matters for battery:**
Software AVC decoding of 1080p@24fps on a mobile CPU can increase power draw by **3–5×** compared to hardware decoding, causing rapid battery drain and thermal throttling. The warning gives the user actionable context and avoids the perception that the app is "broken" when the device gets hot.

### 3.3 Playback error handling with retry

Add a `Player.Listener.onPlayerError` handler that catches `PlaybackException` and surfaces a retryable error UI instead of leaving the player in a broken state.

**Implementation sketch:**

1. In `Media3PlayerController`, add:
   ```kotlin
   override fun onPlayerError(error: PlaybackException) {
       val isDecoderError = error.errorCode == PlaybackException.ERROR_CODE_DECODER_INIT_FAILED
               || error.errorCode == PlaybackException.ERROR_CODE_DECODING_FAILED
       _playerState.value = if (isDecoderError) {
           PlayerState.Error.DecoderError(cause = error)
       } else {
           PlayerState.Error.Generic(cause = error)
       }
   }
   ```
2. `PlayerViewModel` exposes `errorState: StateFlow<PlayerState.Error?>`. `PlayerScreen.kt` observes it and shows:
   - **Title:** "Playback failed"
   - **Message:** "The video format is not supported on this device." (for decoder errors) or generic message.
   - **Actions:**
     - **Retry** — re-prepare the same media item (`player.prepare()`).
     - **Dismiss** — clear error and return to previous screen.
3. For a decoder error on a known `avc1.F40028` file, optionally change the message to "This song was rendered with an older format. Re-rendering may be required." (phrased gently; do not block on this copy).

**Why this matters for robustness and UX:**
Without error handling, ExoPlayer stops playback and leaves the UI frozen on the last frame or a black screen. Users have no feedback and cannot recover without force-stopping the app. A retryable error state turns a silent crash into a recoverable, diagnosable event.

### 3.4 No layout/`PlayerScreen.kt` change

Confirmed by reading `PlayerScreen.kt:154-182` (fullscreen) and `:200-220` (inline): layout is correct, `fillMaxSize()` + `RESIZE_MODE_FIT`. Do **not** touch `PlayerScreen.kt` for layout changes. The "few colored dots" symptom is a codec/decoder artifact, not a Compose sizing bug.

*Exception:* `PlayerScreen.kt` will need **minimal** additions to display the new snackbar (`softwareDecoderWarning`) and the error overlay (`errorState`). These are additive UI layers, not layout changes.

### 3.5 New unit tests

`delivery/android/app/src/test/java/org/streamofworship/android/feature/player/VideoExoPlayerFactoryTest.kt`

Add a Robolectric unit test mirroring the pattern used by the existing `Media3PlayerControllerTest.kt`:

- Verify `VideoExoPlayerFactory.create(context)` returns a non-null `ExoPlayer`.
- Verify that, when given an unsupported video format, the renderer factory uses fallback (via Robolectric shadows or by introspecting the renderers factory — Robolectric's `ShadowMediaCodec` can be set to deny support for `avc1.F40028` and assert that playback does not produce errors).

Simpler alternative (if the full fallback path is hard to exercise in Robolectric): assert that the `RenderersFactory` passed to `ExoPlayer.Builder` is a `DefaultRenderersFactory` and that its configuration reflects `setEnableDecoderFallback(true)` — requires either an introspectable test seam or reflection. Prefer the behavioral test if feasible; fall back to a structural test otherwise.

Update `delivery/android/app/src/test/java/org/streamofworship/android/feature/player/Media3PlayerControllerTest.kt` if it constructs `VideoExoPlayerFactory` indirectly and breaks due to the signature change (signature is unchanged — `create(context: Context): ExoPlayer` remains the same — so most likely no edits needed).

---

## 4. Verifications (to run after implementation)

| Component | Command | Expected |
|---|---|---|
| Render-worker unit tests | `cd delivery/render-worker && PYTHONPATH=src pytest tests/test_video_engine.py tests/test_mp4_cast_compatibility.py -v` | All pass; new `-profile:v` / `-pix_fmt yuv420p` assertions satisfied |
| Android unit tests | `cd delivery/android && ./gradlew testDebugUnitTest` | All pass; new `VideoExoPlayerFactoryTest` passes |
| Android lint | `cd delivery/android && ./gradlew lintDebug` | No new warnings |
| Render smoke test (optional, local) | Render a short job locally, run `ffprobe -show_streams` on the output | `codec_name=h264`, `pix_fmt=yuv420p`, and profile is not `High 4:4:4 Predictive` |
| On-device smoke test | `./gradlew assembleDebug` → `/opt/platform-tools/adb install -r ...app-debug.apk` → navigate to a **newly-rendered** Worship Playback (video) | Video fills the surface; no "colored dots"; audio still plays |
| On-device logcat re-check | `/opt/platform-tools/adb logcat -d \| grep -E 'NoSupport\|Format exceeds'` | `NoSupport` / `Format exceeds selected codec's capabilities` warnings gone for newly rendered jobs |
| Battery warning unit/UI tests | Emit `PlayerEvent.VideoDecoderChanged(..., softwareDecoderActive=true)` | Banner appears and view-model warning auto-dismisses after 5 seconds |
| Error UI unit/UI tests | Emit decoder `PlayerEvent.Error` | Error panel appears with Retry/Dismiss and retry re-prepares the current media |

---

## 5. Limitations, risks & out-of-scope items

### 5.1 Existing renders remain unplayable without re-rendering

Existing renders in R2 encoded in High 4:4:4 Predictive (`avc1.F40028`) will remain unplayable on Android after Fix A is deployed. Fix B's decoder fallback will try the next decoder, but the platform software AVC decoder (`c2.android.avc.decoder`) also lacks High-4:4:4-Predictive support; full software decode of that profile requires the optional media3 FFmpeg extension. **The new software-decoder battery warning (Section 3.2) will not fire for these files because fallback fails entirely**, and the error-handling overlay (Section 3.3) will show a decoder error instead.

**Operational note:** If old renders must play on Android, they need to be re-rendered after Fix A is deployed. A one-off admin/CLI batch re-render command is a separate undertaking and is out of scope.

### 5.2 Software decoding battery drain on edge-case devices

Fix B enables decoder fallback. On devices where the hardware decoder is absent or broken (unrelated to this bug), the platform will fall back to software decoding. This causes the battery warning (Section 3.2) to appear. This is acceptable behavior: the user is informed and playback continues. **No silent battery drain.**

### 5.3 Explicitly out of scope

| Item | Reason |
|---|---|
| Adding `media3-exoplayer-ffmpeg` artifact (~30 MB APK bloat) | Not needed once renders use 4:2:0 |
| Re-rendering existing completed render jobs in R2 | Track separately if desired |
| Webapp signed-URL flow or R2 client changes | Confirmed correct, not the cause |
| `PlayerScreen.kt` layout changes | Layout is correct; only additive snackbar/error-overlay UI |
| Video resolution / frame rate changes | 1080p / 24fps / `crf 23` preserved |
| Render-worker Lambda memory configuration | `yuv420p` reduces memory; current allocation is adequate |
| Post-encode ffprobe validation gate | Rely on unit tests to enforce the codec args contract |

---

## 6. Why this plan is high-confidence

- `adb logcat` on the failing device (Pixel 6, Android 17 / API 37) produced unambiguous evidence:
  - `MediaCodecInfo: NoSupport [codec.profileLevel, avc1.F40028, video/avc] [c2.exynos.h264.decoder]`
  - `MediaCodecRenderer: Format exceeds selected codec's capabilities [codecs=avc1.F40028, res=1920x1080]`
  - `ExynosVideoDecoder: [MFC_Decoder_Dequeue_Outbuf] error type : 1` repeating per frame
- The FFmpeg arg gap is the textbook cause of `avc1.F40028`: libx264 defaulting to 4:4:4 chroma when fed `rgb24` input without an explicit output `-pix_fmt yuv420p`.
- All other player subsystems (single ExoPlayer shared between audio and video surface, Compose layout, URL flow, MP4 container integrity verified on desktop) have been verified by reading the actual source files; none explain the symptom.
- User-confirmed constraints (audio plays fine, MP4 verified good on desktop, reproducible across all render jobs, physical Pixel 6 device, "few colored dots" not tiny text) are all consistent with the codec-profile root cause and inconsistent with alternative hypotheses (Compose sizing race, surface-attach race, blank-video fallback, MP4 content bug).

---

## 7. Implementation summary

1. Edit `delivery/render-worker/src/sow_render_worker/video_engine.py:129-141` → add `"-profile:v", "high"` and `"-pix_fmt", "yuv420p"` to the list returned by `get_video_codec_args()`.
2. Edit `delivery/render-worker/tests/test_video_engine.py` → add the four assertions (profile/pix_fmt) to each `get_video_codec_args` test case (around lines 169, 186, 203, 210).
3. Edit `delivery/android/app/src/main/java/org/streamofworship/android/feature/player/VideoExoPlayerFactory.kt` → use `DefaultRenderersFactory(context.applicationContext).setEnableDecoderFallback(true).setExtensionRendererMode(EXTENSION_RENDERER_MODE_PREFER)` and pass to `ExoPlayer.Builder`.
4. Add `delivery/android/app/src/test/java/org/streamofworship/android/feature/player/VideoExoPlayerFactoryTest.kt` (Robolectric) mirroring the `Media3PlayerControllerTest.kt` pattern.
5. Run the verification commands in Section 4.
6. Follow the repo's `AGENTS.md` session-completion protocol: `git pull --rebase` → `git push` → `git status` shows "up to date with origin" before stopping.
7. Add **decoder-fallback detection** in `Media3PlayerController.kt` via `AnalyticsListener` → emit software-decoder state to `PlayerViewModel`.
8. Add **battery warning UI** in `PlayerScreen.kt` (banner observing `softwareDecoderWarning`).
9. Add **playback error handling** in `Media3PlayerController.kt` via `Player.Listener.onPlayerError` → emit structured playback errors to `PlayerViewModel`.
10. Add **error panel/overlay UI** in `PlayerScreen.kt` (observing `playbackError`) with Retry and Dismiss actions.

---

## 8. Review dimensions checklist

| Dimension | Addressed in plan |
|---|---|
| **Memory pressure on Render Worker Lambda** | Yes — `yuv420p` reduces chroma memory vs. 4:4:4; Lambda memory config unchanged (Section 2.1) |
| **Playback compatibility on devices as old as Pixel 6** | Yes — H.264 `yuv420p` output avoids the unsupported High 4:4:4 Predictive profile seen on Pixel 6 (Section 2.1) |
| **Excessive battery usage on mobile** | Yes — software-decoder fallback triggers a user-facing battery warning (Section 3.2) |
| **Playback robustness and smoothness** | Yes — decoder fallback eliminates silent failures; error handling provides recoverable retry path (Sections 3.1, 3.3) |
| **UX experience** | Yes — battery warning snackbar + retryable error overlay instead of frozen screen (Sections 3.2, 3.3) |
| **Runtime issues** | Yes — `AnalyticsListener` for fallback detection + `Player.Listener` for error recovery (Sections 3.2, 3.3) |
| **Operational risks** | Yes — explicit warnings about FFmpeg extension future inclusion, old-render incompatibility documented, fallback battery behavior made visible (Sections 3.1, 5.1, 5.2) |
