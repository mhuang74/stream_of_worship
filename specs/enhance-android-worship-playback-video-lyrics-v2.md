# Enhance Android Worship Playback: Show Rendered Lyrics Video (v2)

**Date:** 2026-06-25
**Status:** Ready for Implementation
**Component:** `delivery/android/` (feature/player)
**Bug:** The worship playback screen's video area is always blank. Only MP3 audio plays, and lyrics appear as external `Text` below the (empty) video region — the rendered `output.mp4` (with lyrics baked into its frames) is never displayed.

---

## 0. Problem Statement & Root Cause

### Symptom
On the Android Player screen (`PlayerScreen.kt`), when navigating to a video render job:
1. The `PlayerView` region renders nothing (black/blank).
2. Audio plays correctly.
3. The current lyric line shows as a `Text` composable below the blank video area, driven by `chapters.json` timestamps.

### Root Cause
The render worker produces `output.mp4` with lyrics **baked into the video frames** via Pillow/FFmpeg (`delivery/render-worker/src/sow_render_worker/frame_renderer.py`, `video_engine.py`). The MP4 is a perfectly playable video with visible lyrics.

The Android app **never renders the video surface** because of how the player is wired:

- `SowPlaybackService` (`feature/player/SowPlaybackService.kt:21-44`) owns the `ExoPlayer` inside a `MediaSessionService`.
- `PlayerScreen` connects to it through `ServiceMediaControllerFacade` → `MediaController` (`Media3PlayerController.kt:179-305`).
- `playerViewState` then exposes the **`MediaController`** as the `Player` to attach to `PlayerView` (`PlayerScreen.kt:58-59, 90-106`).

**Media3's `MediaController` is a remote *command* forwarder. It does NOT implement video surface rendering** — `PlayerView.setPlayer(controller)` attaches a surface that the `MediaController` cannot drive (it's a no-op for video). Commands like `setMediaItem`/`play`/`seekTo` are forwarded to the service-side `ExoPlayer`, so **audio** decodes and plays fine, but **video frames are never rendered** → blank area.

This is a well-documented Media3 constraint: video surface attachment requires the *actual* `ExoPlayer` instance (in-process), not a `MediaController`.

### Why lyrics show as text outside the video
`PlayerViewModel.load()` fetches `chapters.json` (`PlayerViewModel.kt:106`) and `PlayerScreen.kt:107-108` renders `state.currentLine?.text` as a `Text`. This is a parallel timed-lyrics path, independent of the video. Because the video never renders, the user sees only this text + blank video.

### Additional architectural issue discovered
`SowNavGraph.kt` instantiates `PlayerViewModel` using plain `remember()` (line 125). Because `PlayerViewModel` extends `androidx.lifecycle.ViewModel`, it **should** survive configuration changes (screen rotation), but `remember()` destroys it on Activity recreation. This means all playback state — including `positionMillis` — is lost on rotation. The project already declares `androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7` (line 121 of `build.gradle.kts`), so `viewModel()` is available but unused.

---

## 1. Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **Video player wiring** | **In-process `ExoPlayer` for video only** | `MediaController` cannot attach a video surface. A `DirectPlayerFacade`-backed in-process `ExoPlayer` directly attached to `PlayerView` renders the MP4 (with baked-in lyrics). |
| **Audio playback in worship screen** | **Removed** | Per clarifying discussion: audio renders are for download only. There is no use-case for audio-only playback in the Worship Playback screen. The `PlayerScreen` becomes video-only. |
| **ViewModel scoping** | **Use `viewModel()` instead of `remember()`** | `lifecycle-viewmodel-compose` is already on the classpath. Proper `ViewModelStoreOwner` scoping lets `PlayerViewModel` survive configuration changes so playback state (position, manifest) is retained across rotation. |
| **Config change resilience** | **Save position, rebind media, seek on recreate** | When the in-process `ExoPlayer` is released during rotation, the last position is preserved in the (now surviving) `PlayerViewModel`. After recreation, the new player is seeded with the same media URI and seeks to the saved position. |
| **Background behavior** | **Pause on `ON_STOP`** | No requirement for background audio continuation during video playback. A simple `LifecycleEventObserver` that calls `viewModel.pause()` on `ON_STOP` eliminates the complex handoff controller entirely. |
| **Offline playback** | **Reuse existing `OfflineArtifactMetadata` path** | `PlayerViewModel.load()` already checks `offlineCacheRepository.getArtifact(renderJobId, OfflineArtifactKind.Video)` and uses `cached.localUri`. `MediaItem.fromUri()` handles both `https://` and `file://` URIs, so no new code is needed — only verification. |
| **RenderScreen Play button** | **Only when video exists** | `RenderJob.preferredPlaybackArtifact()` currently returns `Audio` when no video key exists. For video-only playback, the Play button must be hidden/disabled unless `mp4R2Key != null`. |
| **Lyrics display** | **Replace redundant `Text` with a bottom Lyrics Sheet** | Lyrics are baked into the video. A single current-line `Text` is redundant. Instead, a Material3 `ModalBottomSheet` mirrors the webapp `LyricJumpList`: all chapters listed, current chapter expanded with lines+timestamps, tap to seek. |
| **Lyrics sheet data source** | **Reuse `PlaybackManifest`** | Same `chapters.json` model already loaded by `PlayerViewModel`. No new fetch. |
| **Lyrics sheet ↔ video** | **Overlay, non-modal behavior** | Opening the sheet does NOT pause video. Tapping a line/chapter seeks playback. The sheet stays open (webapp parity). |
| **Fullscreen** | **True immersive fullscreen** | `WindowInsetsControllerCompat` hides system bars and the video fills the screen. Only for video (now the only artifact). |
| **External lyrics `Text` and jump-list** | **Remove both** | The inline `currentLine` `Text` and the static `LazyColumn` chapter jump-list are redundant with the lyrics sheet. Removing them declutters the screen and matches webapp parity. |
| **Tests** | **Robolectric unit tests** for ViewModel, Controller, LyricsSheet, and PlayerScreen | Matches existing patterns. No new test infrastructure needed. |

---

## 2. Implementation Phases

### Phase 1: `VideoExoPlayerFactory` — production-configured in-process ExoPlayer

**New file:** `delivery/android/app/src/main/java/org/streamofworship/android/feature/player/VideoExoPlayerFactory.kt`

```kotlin
package org.streamofworship.android.feature.player

import android.content.Context
import androidx.media3.common.C
import androidx.media3.exoplayer.ExoPlayer

/**
 * Builds a foreground ExoPlayer for video playback whose surface can be attached
 * to a [androidx.media3.ui.PlayerView]. Mirrors the audio config used by
 * [SowPlaybackService] so video + audio paths behave consistently.
 */
object VideoExoPlayerFactory {
    fun create(context: Context): ExoPlayer =
        ExoPlayer
            .Builder(context.applicationContext)
            .setHandleAudioBecomingNoisy(true)
            .build()
            .apply {
                setWakeMode(C.WAKE_MODE_NETWORK)
                setVideoScalingMode(C.VIDEO_SCALING_MODE_SCALE_TO_FIT)
            }
}
```

**Why a factory (not inline in composable):** testable in isolation; the `ExoPlayer.Builder` config is identical to `SowPlaybackService.onCreate()` (lines 23-30) minus the `MediaSession` wrapping, which the service owns exclusively for audio background playback (now unused by `PlayerScreen`).

---

### Phase 2: Nav graph — always wire in-process video player

**File:** `delivery/android/app/src/main/java/org/streamofworship/android/core/navigation/SowNavGraph.kt` (lines 118-135)

Currently:
```kotlin
val context = LocalContext.current.applicationContext
val mediaController = remember(jobId, context) { Media3PlayerController(context) }
```

Change to always create an in-process video player:

```kotlin
val context = LocalContext.current.applicationContext
val mediaController =
    remember(jobId, context) {
        val exoPlayer = VideoExoPlayerFactory.create(context)
        Media3PlayerController(exoPlayer)   // DirectPlayerFacade path — surface works
    }
```

**Lifecycle note:** `Media3PlayerController.release()` already calls `player.release()` which for `DirectPlayerFacade` releases the `ExoPlayer` (`Media3PlayerController.kt:138-142`). The existing `DisposableEffect` in `PlayerScreen.kt:68-73` calls `media3Controller?.release()` on dispose, so the in-process ExoPlayer is released when leaving the screen. ✅ No leak.

**Imports to add:** `org.streamofworship.android.feature.player.VideoExoPlayerFactory`.

---

### Phase 3: Fix ViewModel scoping for config-change survival

**Files:** `SowNavGraph.kt`, `PlayerViewModel.kt`, `PlayerScreen.kt`

#### 3a. Switch to `viewModel()` in `SowNavGraph`

Replace `remember()` with `viewModel()` so `PlayerViewModel` survives rotation.

```kotlin
import androidx.lifecycle.viewmodel.compose.viewModel

composable(SowRoute.Player.pattern) { backStackEntry -
    val jobId = backStackEntry.arguments?.getString("jobId").orEmpty()
    val dependencies = rememberSongsetsDependencies(authController)
    val context = LocalContext.current.applicationContext
    val mediaController = remember(jobId, context) {
        val exoPlayer = VideoExoPlayerFactory.create(context)
        Media3PlayerController(exoPlayer)
    }
    val viewModel = viewModel(key = jobId) {
        PlayerViewModel(
            renderJobId = jobId,
            repository = dependencies.playbackRepository,
            controller = mediaController,
            offlineCacheRepository = dependencies.offlineCacheRepository,
        )
    }
    PlayerScreen(viewModel = viewModel, media3Controller = mediaController, onBack = { navController.popBackStack() })
}
```

> Using `viewModel(key = jobId)` scopes the instance to the back-stack entry (the `NavBackStackEntry` implements `ViewModelStoreOwner`), so it survives configuration changes but is cleared when the user pops back.

#### 3b. Fix `DisposableEffect` dependency in `PlayerScreen`

**File:** `PlayerScreen.kt` lines 68-73

Currently:
```kotlin
DisposableEffect(viewModel) {
    onDispose {
        wakeLock.release()
        media3Controller?.release()
    }
}
```

When using `viewModel()`, the `ViewModel` no longer changes on rotation, so this `DisposableEffect` won't fire — the old controller leaks and the new controller never gets set up. Change the key to `media3Controller`:

```kotlin
DisposableEffect(media3Controller) {
    onDispose {
        wakeLock.release()
        media3Controller?.release()
    }
}
```

This ensures:
- Old controller is released when a new one is created (rotation).
- `wakeLock.release()` is called on dispose (unchanged behavior).
- `PlayerViewModel.onCleared()` (which also calls `controller.release()`) is NOT reached on rotation; the controller is released by the composable lifecycle instead, which is correct because the controller owns the in-process `ExoPlayer`.

#### 3c. Auto-rebind media + seek after rotation

**File:** `PlayerScreen.kt`

After rotation, a brand-new `Media3PlayerController` + `ExoPlayer` is created, but the `PlayerViewModel` retains `mediaUrl` and `positionMillis`. Add a `LaunchedEffect` that re-binds media and restores position when the controller is new but the ViewModel already has a URL:

```kotlin
LaunchedEffect(media3Controller, state.mediaUrl) {
    val url = state.mediaUrl ?: return@LaunchedEffect
    val controller = media3Controller ?: return@LaunchedEffect
    // Avoid rebinding if this controller already has media (e.g., fresh navigation)
    if (controller.durationMillis <= 0L) {
        controller.setMedia(url, isVideo = true)
        if (state.positionMillis > 0L) {
            controller.seekTo(state.positionMillis)
        }
    }
}
```

> `durationMillis <= 0L` is a proxy for "no media loaded yet." After rotation, the new controller has no media → duration is 0. On fresh navigation, `viewModel.load()` also sets media (via `controller.setMedia`) before this `LaunchedEffect` typically runs, so the duration will be positive and the re-bind is skipped. The exact ordering is safe because `load()` is triggered by `LaunchedEffect(viewModel)` and runs concurrently; the rebinding logic above is idempotent (calling `setMedia` twice on the same URL is harmless).

**Alternative safety valve:** In `PlayerViewModel.load()`, after `controller.setMedia(url, true)`, call `controller.prepare()` and then `controller.playIfPreviouslyPlaying()` — but tracking "was playing" adds state. The simpler `LaunchedEffect` above is sufficient.

---

### Phase 4: Lyrics Sheet component (mirrors webapp `LyricJumpList`)

**New file:** `delivery/android/app/src/main/java/org/streamofworship/android/feature/player/LyricsSheet.kt`

A Material3 `ModalBottomSheet` that mirrors `delivery/webapp/src/components/play/LyricJumpList.tsx`. Key behaviors to replicate:

| Webapp behavior (`LyricJumpList.tsx`) | Android implementation |
|---|---|
| Handle bar always visible ("Lyrics" / "Tap to close"), toggles sheet (lines 147-183) | A "Lyrics" `IconButton` in the controls row of `PlayerScreen` toggles `showSheet = true`. (Compose `ModalBottomSheet` has no persistent peek; use a button affordance instead of a draggable handle.) |
| Single scrolling list of ALL chapters; current chapter expanded with lines (lines 195-273) | `LazyColumn` of all `PlaybackChapter`s. Always render chapter header. Render `PlaybackLine`s only for the current chapter (`state.currentChapter`). |
| Current line highlighted, past lines dimmed, future lines normal (lines 243-256) | `ContainerColor`/`ContentColor` per line based on `positionMillis` vs `line.startMillis`. Current = `primaryContainer`; past = `onSurface.copy(alpha=0.4f)`; future = `onSurfaceVariant`. |
| Tap chapter heading → `onJumpToChapter(index)` → `handleSeek(chapter.startSeconds)` (lines 210-212, 483-493) | `OutlinedButton(onClick = { viewModel.jumpToChapter(chapter) })` — `jumpToChapter` already exists (`PlayerViewModel.kt:205-207`) and seeks to `chapter.startMillis`. ✅ |
| Tap lyric line → `onJumpToLine(chapterIndex, lineIndex)` → `handleSeek(line.startSeconds)` (lines 257-259, 495-508) | `TextButton(onClick = { viewModel.jumpToLine(line) })` — `jumpToLine` already exists (`PlayerViewModel.kt:209-211`) and seeks to `line.startMillis`. ✅ |
| Dismiss: tap backdrop / swipe down (lines 278-296, 81-107) | `ModalBottomSheet` handles natively: scrim tap + drag down call `onDismissRequest`. |
| Chapter header: music icon + title + `start - end` timestamp range + pulse dot for current (lines 214-235) | `ListItem` with leading `Icons.Outlined.MusicNote`, title `chapter.title`, supporting `formatTime(start) - formatTime(end)`, trailing `Icon` pulse for current. |
| Line row: text + `startSeconds` timestamp (lines 261-264) | `ListItem` with `line.text` (title) + `formatTime(line.startMillis)` (supporting). |

**Signature:**
```kotlin
@OptIn(UnstableApi::class)
@Composable
fun LyricsSheet(
    manifest: PlaybackManifest,
    positionMillis: Long,
    onJumpToChapter: (PlaybackChapter) -> Unit,
    onJumpToLine: (PlaybackLine) -> Unit,
    onDismiss: () -> Unit,
)
```

**Helper:** `formatTime(millis: Long): String` → `m:ss` (mirrors webapp `formatTime`, `LyricJumpList.tsx:109-114`). Put in same file or reuse if a time formatter exists.

**Auto-scroll current line into view (recommended enhancement over webapp):** `LazyListState` `LaunchedEffect(currentLine)` calls `listState.animateScrollToItem(currentChapterIndex)`. Optional; mark as nice-to-have.

---

### Phase 5: Wire `LyricsSheet` into `PlayerScreen`

**File:** `delivery/android/app/src/main/java/org/streamofworship/android/feature/player/PlayerScreen.kt`

#### 5a. Remove redundant single-line `Text` and static jump-list
Delete:
- Lines 107-108 (the `Text(state.currentChapter?.title ...)` + `Text(state.currentLine?.text ...)` pair).
- Lines 134-140 (the `LazyColumn` chapter jump-list).

Keep a compact "now playing" label so context exists when the sheet is closed:

```kotlin
Text(
    state.currentChapter?.title ?: "Rendered worship set",
    style = MaterialTheme.typography.titleLarge,
)
```

#### 5b. Remove `PlaybackArtifact.Video` conditional around `PlayerView`
Since `PlayerScreen` is now video-only, the `AndroidView` for `PlayerView` is always rendered (when `media3Controller != null`):

```kotlin
// Remove: if (state.artifact == PlaybackArtifact.Video && media3Controller != null) {
AndroidView(
    factory = { PlayerView(it).apply { player = videoPlayer } },
    update = { it.player = videoPlayer },
    modifier =
        Modifier
            .fillMaxWidth()
            .height(
                when {
                    state.isFullscreen -> 420.dp
                    isLandscape -> 180.dp
                    else -> 220.dp
                },
            )
            .testTag("player-video-view"),
)
// Remove closing brace
```

#### 5c. Add the Lyrics toggle button + sheet
In the controls `Row` (lines 113-133), add a "Lyrics" `IconButton` before the fullscreen button:

```kotlin
var showLyricsSheet by remember { mutableStateOf(false) }

// in the Row:
IconButton(onClick = { showLyricsSheet = true }) {
    Icon(Icons.Outlined.Subtitles, contentDescription = "Lyrics")
}

// after the Column (sibling, so it overlays):
if (showLyricsSheet) {
    val manifest = state.manifest ?: return@Column
    ModalBottomSheet(onDismissRequest = { showLyricsSheet = false }) {
        LyricsSheet(
            manifest = manifest,
            positionMillis = state.positionMillis,
            onJumpToChapter = { viewModel.jumpToChapter(it) },
            onJumpToLine = { viewModel.jumpToLine(it) },
            onDismiss = { showLyricsSheet = false },
        )
    }
}
```

**Webapp parity note:** The sheet stays open after a jump. The user dismisses via scrim tap or drag-down. The sheet's `positionMillis` updates via the 500ms ticker so the current-line highlight follows playback.

**Test tag:** `"player-lyrics-sheet"`, `"player-lyrics-toggle"`.

---

### Phase 6: True immersive fullscreen for video

**File:** `delivery/android/app/src/main/java/org/streamofworship/android/feature/player/PlayerScreen.kt`

Replace the height-only fullscreen (lines 90-106, 213-215 in ViewModel) with an OS-level immersive overlay.

#### 6a. Add a fullscreen composable state
In `PlayerScreen`, read the `Activity` from `LocalContext` (cast to `ComponentActivity`) and use `WindowInsetsControllerCompat`:

```kotlin
val context = LocalContext.current
val activity = remember(context) { context.findActivity() }   // helper walks ContextWrapper
val view = remember { (activity?.window?.decorView) }

LaunchedEffect(state.isFullscreen) {
    val controller = view?.let { WindowInsetsControllerCompat(activity!!.window, it) } ?: return@LaunchedEffect
    if (state.isFullscreen) {
        controller.hide(WindowInsetsCompat.Type.systemBars())
        controller.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
    } else {
        controller.show(WindowInsetsCompat.Type.systemBars())
    }
}
```

Add helper `Context.findActivity(): ComponentActivity?` (walks `ContextWrapper` chain — standard snippet). Put in `core/util/ActivityExt.kt` or inline.

#### 6b. Fullscreen layout
When `isFullscreen`, render the `PlayerView` as a **fullscreen overlay** (Box filling the whole screen, black background, video centered) instead of inside the padded `Column`. Use a conditional layout:

```kotlin
if (state.isFullscreen && media3Controller != null) {
    // Full-screen overlay
    Box(
        Modifier.fillMaxSize().background(Color.Black).testTag("player-fullscreen"),
        contentAlignment = Alignment.Center,
    ) {
        AndroidView(
            factory = { PlayerView(it).apply { player = videoPlayer; useController = true } },
            update = { it.player = videoPlayer },
            modifier = Modifier.fillMaxSize(),
        )
        // Floating exit-fullscreen affordance:
        IconButton(onClick = { viewModel.toggleFullscreen() }, modifier = Modifier.align(TopStart).padding(16.dp)) {
            Icon(Icons.AutoMirrored.Outlined.ArrowBack, "Exit fullscreen")
        }
    }
    return   // skip the normal Column layout
}
```

When NOT fullscreen, render the inline `PlayerView` (current code, height 220dp portrait / 180dp landscape).

#### 6c. ViewModel change
`toggleFullscreen` (`PlayerViewModel.kt:213-215`) stays as-is (flips `isFullscreen`). No change.

---

### Phase 7: Pause on background

**File:** `PlayerScreen.kt`

Since there is no requirement for background audio during video playback, the simplest and most robust behavior is to pause when the app goes to the background.

```kotlin
val lifecycleOwner = LocalLifecycleOwner.current
DisposableEffect(lifecycleOwner) {
    val observer = LifecycleEventObserver { _, event -
        if (event == Lifecycle.Event.ON_STOP && state.isPlaying) {
            viewModel.pause()
        }
    }
    lifecycleOwner.lifecycle.addObserver(observer)
    onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
}
```

> This replaces the complex `BackgroundHandoffController` from v1 of this plan. No new files. No race conditions. No dual-player lifecycle management.

**Note on wake lock:** The existing `PlaybackWakeLock` is still acquired in `LaunchedEffect(state.isPlaying)` (line 65-67) and released in `DisposableEffect` on dispose. Because the player pauses on `ON_STOP`, the wake lock is released naturally when playback stops.

---

### Phase 8: RenderScreen — restrict Play button to video-only

**File:** `delivery/android/app/src/main/java/org/streamofworship/android/feature/render/RenderScreen.kt`

Since audio-only playback is not supported in the worship screen, the Play button should not appear for audio-only renders.

#### 8a. Change `preferredPlaybackArtifact()` to nullable
```kotlin
private fun RenderJob.preferredPlaybackArtifact(): PlaybackArtifact? =
    if (mp4R2Key != null) PlaybackArtifact.Video else null
```

#### 8b. Guard Play buttons
In `RenderForm.onReviewPrevious` (line 122) and `RenderStatusPanel` (lines 317-324), only invoke `onPlay` when the artifact is non-null:

```kotlin
// In RenderForm.onReviewPrevious:
state.reviewableCompletedJob?.preferredPlaybackArtifact()?.let { artifact -
    onPlay(job.id, artifact)
}

// In RenderStatusPanel:
if (job.hasPlayableArtifacts) {
    // ... artifact label ...
    val playArtifact = job.preferredPlaybackArtifact()
    if (playArtifact != null) {
        Row(...) {
            Button(onClick = onPlay, ...) { /* Play */ }
            OutlinedButton(onClick = onDownload, ...) { /* Download */ }
        }
    } else {
        // Only audio exists — show Download only
        OutlinedButton(onClick = onDownload, modifier = Modifier.fillMaxWidth()) {
            Icon(Icons.Outlined.Download, contentDescription = null)
            Text("Download")
        }
    }
}
```

This prevents the user from navigating to `PlayerScreen` with an audio-only artifact.

---

### Phase 9: Offline playback verification

**No new code required** — the existing `PlayerViewModel.load()` (lines 102-138) already:
1. Checks `offlineCacheRepository?.getArtifact(renderJobId, OfflineArtifactKind.Video)`.
2. If `cached.isPlayableOffline`, calls `controller.setMedia(cached.localUri.orEmpty(), isVideo = true)`.
3. `DirectPlayerFacade.setMedia()` (line 118) calls `MediaItem.fromUri(url)`, which handles `file://` URIs natively.

**Verification step:** Confirm that a downloaded MP4 plays correctly through the in-process `ExoPlayer`. If `cached.localUri` points to a path that `ExoPlayer` cannot resolve, a `PlaybackException` surfaces through `PlayerEvent.Error` and is shown in the UI.

---

### Phase 10: Tests

#### 10a. `LyricsSheetTest.kt` (new)
**New file:** `delivery/android/app/src/test/java/org/streamofworship/android/feature/player/LyricsSheetTest.kt`

Robolectric + Compose UI test (`createAndroidComposeRule`):
- Renders `LyricsSheet` with a 2-chapter manifest; assert both chapter titles display.
- Set `positionMillis` inside chapter 2; assert chapter 2's lines render and chapter 1's lines do NOT.
- Assert current line is highlighted (test tag `player-lyrics-current-line`).
- Tap a line → assert `onJumpToLine` invoked with that line.
- Tap a chapter header → assert `onJumpToChapter` invoked.

#### 10b. `PlayerScreenTest.kt` (extend)
**File:** `delivery/android/app/src/test/java/org/streamofworship/android/feature/player/PlayerScreenTest.kt`
- Update existing assertion at line 54 (`onNodeWithText("耶和華是我的牧者")`) — the old `currentLine` `Text` is removed. Replace with: open the lyrics sheet (`player-lyrics-toggle`) and assert the text appears inside the sheet (`player-lyrics-sheet`).
- Add test: "lyrics toggle button shows sheet with all chapters" — click `"player-lyrics-toggle"`, assert `"player-lyrics-sheet"` displays.
- Add test: "video renders PlayerView when media3Controller is provided" — pass a `DirectPlayerFacade`-backed controller, assert `"player-video-view"` is displayed. (The old audio-only path no longer exists.)
- Add test: "fullscreen toggle hides system bars" — with Robolectric, assert the fullscreen overlay (`player-fullscreen`) is added to the view hierarchy when fullscreen state is true.

#### 10c. `Media3PlayerControllerTest.kt` (extend)
**File:** `delivery/android/app/src/test/java/org/streamofworship/android/feature/player/Media3PlayerControllerTest.kt`
- Add: `VideoExoPlayerFactory.create(context)` builds a playable `ExoPlayer` that reports `durationMillis == 0` before media is set.
- Add: `setMedia` with a `file://` URI on `DirectPlayerFacade` succeeds (no crash). Robolectric cannot truly play video, but `setMedia` + `prepare` should not throw.

#### 10d. `PlayerViewModelTest.kt` (extend)
**File:** `delivery/android/app/src/test/java/org/streamofworship/android/feature/player/PlayerViewModelTest.kt`
- Add: `load()` with cached offline artifact calls `controller.setMedia(localUri, true)`.
- Add: `load()` ignores the passed artifact type and always loads video (since the `artifact` parameter is now vestigial, or verify that `OfflineArtifactKind.Video` is always queried).
- Existing `jumpToChapter`/`jumpToLine` tests should still pass.

---

## 3. Files Modified / Created

| File | Change Type | Phase |
|------|-------------|-------|
| `feature/player/VideoExoPlayerFactory.kt` | **New** | 1 |
| `core/navigation/SowNavGraph.kt` | Edit: always create in-process player; use `viewModel()` for `PlayerViewModel` | 2, 3 |
| `feature/player/PlayerScreen.kt` | Edit: remove artifact conditionals, remove redundant lyrics Text + jump-list, add lyrics toggle + sheet, immersive fullscreen, fix `DisposableEffect` key, add pause-on-background, add media rebind on rotation | 3, 5, 6, 7 |
| `feature/player/LyricsSheet.kt` | **New** | 4 |
| `feature/render/RenderScreen.kt` | Edit: hide Play button for audio-only renders | 8 |
| `core/util/ActivityExt.kt` (or inline helper) | **New** (optional) | 6 |
| `test/.../feature/player/LyricsSheetTest.kt` | **New** | 10 |
| `test/.../feature/player/PlayerScreenTest.kt` | Edit: update assertion, add toggle/video/fullscreen tests | 10 |
| `test/.../feature/player/Media3PlayerControllerTest.kt` | Edit: verify `VideoExoPlayerFactory` + file URI path | 10 |

**Total: 3 new prod files, 1 new test file, 4 edited files.**
(v1 of this plan had 4 new prod files, 1 optional util file, 1 new test file, 4 edited files — this version removes `BackgroundHandoffController.kt` entirely.)

---

## 4. Implementation Order

1. **Phase 1** (`VideoExoPlayerFactory`) — standalone, no deps.
2. **Phase 2** (nav graph always in-process) — depends on Phase 1. **This alone fixes the blank-video bug.** Ship-able independently as a minimal fix.
3. **Phase 3** (ViewModel scoping + config-change rebinding) — depends on Phase 2. Critical for rotation UX.
4. **Phase 8** (RenderScreen Play restriction) — independent; can be done in parallel with 1-3.
5. **Phase 4** (`LyricsSheet`) — standalone UI component.
6. **Phase 5** (wire sheet into `PlayerScreen`, remove redundant Text/jump-list) — depends on Phase 4.
7. **Phase 6** (immersive fullscreen) — depends on Phase 2 (video renders). Independent of 4/5.
8. **Phase 7** (pause on background) — trivial; depends on nothing.
9. **Phase 9** (offline verification) — manual test with a downloaded video.
10. **Phase 10** (tests) — incrementally with each phase.

Phases 4, 5, 6 are independent of each other once Phase 2 lands; they can be parallelized.

---

## 5. What This Does NOT Change

- **`SowPlaybackService`** — untouched. Still owns the audio ExoPlayer + MediaSession, but `PlayerScreen` no longer connects to it. It remains available for any future background-audio use case (e.g., a dedicated audio player screen or ministry tool).
- **`ServiceMediaControllerFacade`** — untouched. Still functional, just unused by `PlayerScreen` after this plan.
- **Render worker / webapp** — no changes. The MP4 already has lyrics baked in; the bug is purely client-side rendering.
- **`PlaybackApi` / `HttpPlaybackRepository` / signed-URL flow** — untouched.
- **`chapters.json`** model & fetch — untouched. Reused by the new lyrics sheet.
- **`PlaybackWakeLock`** — untouched (still guards playback CPU; the in-process ExoPlayer benefits from it).
- **Audio-only download flow** — untouched. Audio MP3s are still rendered and downloadable via `ShareScreen` / `DownloadManager`.
- **`Media3PlayerController(context)` constructor** — preserved. The `ServiceMediaControllerFacade` path still exists for any future feature that needs it.

---

## 6. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| In-process `ExoPlayer` for video is killed when the Activity is destroyed mid-stream (e.g., during configuration change) | **Mitigated by Phase 3:** `viewModel()` scopes the `PlayerViewModel` across config changes. The last playback position is saved in the surviving ViewModel state. A new `ExoPlayer` is created and seeks to that position on recreation. The user experiences a brief buffering pause, not a restart from zero. |
| `DisposableEffect` key change (`viewModel` → `media3Controller`) could cause the controller to be released at the wrong time | The new key is correct: the controller owns the native `ExoPlayer` resource and must be released when the controller reference changes (rotation). The `ViewModel` no longer calls `controller.release()` in `onCleared()` on rotation because it survives. The composable lifecycle is the sole owner of the in-process controller. |
| `ModalBottomSheet` current version in compose-bom `2024.12.01` is stable but `rememberModalBottomSheetState` API may differ slightly | Use the stable `ModalBottomSheet` signature (`onDismissRequest` + `sheetContent`). Avoid experimental skip-partially-expanded variants. |
| Removing the static chapter jump-list changes UX for users who relied on it | The bottom sheet provides the same functionality (list of chapters, tap to jump) plus line-level navigation, so functionality is preserved. The interaction requires one extra tap to open the sheet, which matches the webapp affordance. |
| `findActivity()` context walk can return null in some embedded contexts | Defensive null check; immersive is a no-op if activity is null (fallback to current height-only behavior). |
| Video MP4 is large; signed-URL expiry (1h) during a long fullscreen session | Already handled by existing `ExpiredSignedUrl` banner + `Retry` (`PlayerScreen.kt:162-169`, `PlayerViewModel.kt:122-128`). Re-fetches on retry. No change. |
| RenderScreen hides Play button for audio-only renders, which may confuse users who previously played audio | Add a label or tooltip on the Download button: "Audio only — download to listen". The UI should communicate why Play is absent. |

---

## 7. Verification

After implementation:

1. **Video renders:** Navigate to a completed video render job (`RenderJob.mp4R2Key != null`) → Player screen → `PlayerView` shows the video with baked-in lyrics (not blank).
2. **Audio-only cannot play:** Navigate to a render job with only `mp3R2Key` → Render status shows "Download" button only; "Play" is not offered.
3. **Rotation survival:** Start playing a video, rotate the device → playback pauses briefly, resumes at the same position (not from 0:00).
4. **Lyrics sheet:** Tap "Lyrics" button → `ModalBottomSheet` opens, lists all chapters, current chapter's lines visible, current line highlighted. Tap a line → playback seeks, sheet stays open, highlight moves. Tap scrim/drag down → sheet closes.
5. **Immersive fullscreen:** Tap fullscreen → system bars hide, video fills screen, an exit affordance restores bars.
6. **Background pause:** Start a video, press Home → playback pauses. Return to app → video is paused at the same frame; tap Play to resume.
7. **Offline playback:** Download a video, turn off network, navigate to Player → video plays from local cache.
8. **Back navigation:** Back button from fullscreen exits fullscreen first (if in fullscreen) OR pops the screen (if not). Confirm via `BackHandler`.
9. **Robolectric tests pass:** `./gradlew testDebugUnitTest` green; new `LyricsSheetTest` covers expand/collapse + tap-to-seek.

```bash
cd delivery/android
./gradlew testDebugUnitTest koverXmlReport
./gradlew lintDebug
./gradlew assembleDebug
```

---

## 8. References

- Root cause — `MediaController` cannot render video surfaces: see `Media3PlayerController.kt:179-305` (`ServiceMediaControllerFacade` exposes `MediaController` as `playerView`) + `PlayerScreen.kt:90-106` (attaches it to `PlayerView`).
- Render worker bakes lyrics into MP4 frames: `delivery/render-worker/src/sow_render_worker/frame_renderer.py`, `video_engine.py` (`encode_video_with_ffmpeg` pipes Pillow-painted RGB frames to FFmpeg).
- Webapp `LyricJumpList` (the model for the Android sheet): `delivery/webapp/src/components/play/LyricJumpList.tsx`.
- Existing jump helpers in ViewModel: `PlayerViewModel.kt:205-211` (`jumpToChapter`, `jumpToLine`).
- Existing test patterns: `PlayerViewModelTest.kt` (`FakePlayerController`, `FakePlaybackRepository`), `Media3PlayerControllerTest.kt` (`FakeMediaPlayerFacade`, in-process ExoPlayer test).
- Dependencies confirmed available (no new deps): `material3` (`ModalBottomSheet`) at `build.gradle.kts:116`; `core-ktx:1.15.0` (`WindowInsetsControllerCompat`) at line 120; `lifecycle-viewmodel-compose:2.8.7` at line 121.
- v1 of this plan (for comparison): `specs/enhance-android-worship-playback-video-lyrics.md`

---

## 9. Changelog from v1

| Area | v1 | v2 |
|------|-----|-----|
| **Audio playback** | Supported audio-only via `ServiceMediaControllerFacade` with background handoff | **Removed.** Worship screen is video-only. Audio is download-only. |
| **Background behavior** | Complex `BackgroundHandoffController` to handoff video→service so audio continues | **Replaced with simple pause-on-background.** |
| **Config changes** | Accepted recreation/loss of playback on rotation | **Fixed.** Use `viewModel()` + auto-rebind media + seek to saved position. |
| **Files added** | `VideoExoPlayerFactory`, `LyricsSheet`, `BackgroundHandoffController`, `ActivityExt` | **Removed `BackgroundHandoffController`.** Added `ActivityExt` (still optional). |
| **RenderScreen** | Unchanged | **Added Phase 8:** Hide Play button for audio-only renders. |
| **Offline support** | Mentioned only for streaming URL | **Explicitly verified:** Existing `cached.localUri` path works with `DirectPlayerFacade`. |
| **PlayerScreen conditionals** | `if (state.artifact == PlaybackArtifact.Video)` around PlayerView | **Removed.** PlayerView always renders since the screen is video-only. |
| **Riskiest phase** | Phase 7 (Background Handoff) — complex, raced, fallback suggested | **Eliminated.** Pause-on-background is trivial. |
| **Test changes** | Tests for audio facade path, handoff simulation | **Replaced** with video-only tests, rotation survival tests, file-URI tests. |
