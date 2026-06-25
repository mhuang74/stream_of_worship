# Enhance Android Worship Playback: Show Rendered Lyrics Video (v3)

**Date:** 2026-06-25
**Supersedes:** `specs/enhance-android-worship-playback-video-lyrics-v2.md`
**Component:** `delivery/android/` (feature/player)
**Mode:** Video-only playback; audio renders are download-only.

---

## 0. Bug Recap & Verified Root Cause

**Symptom:** `PlayerScreen` shows a blank `PlayerView`; only MP3 audio plays; lyrics render as a parallel `Text` below the empty video region.

**Verified root cause (against actual code in `Media3PlayerController.kt` + `PlayerScreen.kt`):**

- `SowNavGraph.kt:118-135` constructs `Media3PlayerController(context)`, which selects the `ServiceMediaControllerFacade` path (`Media3PlayerController.kt:179-305`). That facade exposes the bound **`MediaController`** as `PlayerViewHost.playerView` (lines 30-31, 217-218).
- `PlayerScreen.kt:58-59, 91-106` binds that `MediaController` to `PlayerView`.
- Media3's `MediaController` is a remote command forwarder; binding it as `PlayerView.player` is a **no-op for video surface rendering**. Audio decoding is forwarded to the service-side `ExoPlayer`, so audio plays; video frames never reach the surface → blank area.
- `DirectPlayerFacade` (`Media3PlayerController.kt:106-169`), wrapping an in-process `ExoPlayer`, already exists and *does* support `setVideoSurface` — it is the fix surface.

**Verified secondary issues:**

- `SowNavGraph.kt:118-135` uses `remember(...)` for `PlayerViewModel` → destroyed on Activity recreation; all playback state lost on rotation.
- `PlayerViewModel.kt:91` defines `load(artifact: PlaybackArtifact = defaultArtifact)`; `artifact` selects the video vs audio URL at lines 116-121.
- `PlayerViewModel.kt:106` calls `repository.chapters(renderJobId)` (HTTP/repository call returning `PlaybackManifest`). **Correction to v2:** it does **not** read a local `chapters.json` file.
- **No `BackHandler`** exists anywhere in `delivery/android/` (grep returned no matches).
- `RenderScreen.kt:142-143` `preferredPlaybackArtifact()` returns `Audio` when `mp4R2Key == null`.
- `PlaybackChapter.startMillis/title`, `PlaybackLine.startMillis/text` all exist (`data/playback/PlaybackModels.kt:44-68`).

**Verified dependency availability (no new deps needed):**

- `lifecycle-viewmodel-compose:2.8.7` (`delivery/android/app/build.gradle.kts:121`)
- `androidx.core:core-ktx:1.15.0` (provides `WindowInsetsControllerCompat`) (line 120)
- compose-bom `2024.12.01` (line 109); Media3 `1.5.1`; Robolectric `4.14.1` (line 143).
- `BackgroundHandoffController` does **not** exist and must not be introduced (v2 already removed it).

---

## 1. Design Decisions (v3)

| Decision | Choice | Rationale |
|---|---|---|
| Video player wiring | In-process `ExoPlayer` via existing `DirectPlayerFacade` | Re-uses `Media3PlayerController(player: Player)` constructor (line 20) — no new facade class. `setVideoSurface` works. |
| Audio playback in worship screen | **Removed.** Player screen is video-only. | Per decision. `SowPlaybackService` retained untouched + a `// TODO` future hook. |
| ViewModel scoping | `viewModel(key = jobId)` scoped to `NavBackStackEntry` | Survives rotation. `lifecycle-viewmodel-compose` already on classpath. |
| Rotation behavior | **Seek only, do not auto-resume.** Track `positionMillis` in the surviving VM; after rebind, seek and pause. User taps Play. | Avoids `wasPlaying` tracking and surprise autoplay. |
| `DisposableEffect` key | `media3Controller` (not `viewModel`) | The controller owns the native `ExoPlayer` and must be released when its reference changes. |
| Background behavior | Pause on `ON_STOP` via `LifecycleEventObserver` | No background-audio requirement for video. Replaces v2's complex handoff. |
| Offline playback | Reuse existing `PlayerViewModel.load()` offline-cache path (lines 104-105) | `DirectPlayerFacade.setMedia()` calls `MediaItem.fromUri()` which handles `file://`. Verify only. |
| RenderScreen audio-only | Hide Play; show **"Audio only"** chip beside Download | Communicates why Play is absent. |
| Lyrics surface | **Inline collapsible `LyricsPanel` below `PlayerView`, not a `ModalBottomSheet`** | Panel must never overlap the video. `Modifier.weight(1f)` occupies remaining vertical space; video height stays fixed. |
| Lyrics data source | Reuse `PlaybackManifest` from `repository.chapters()` | No new fetch. |
| Fullscreen | Immersive OS-level overlay via `WindowInsetsControllerCompat`; full-screen `Box` replaces inline layout | True immersive. |
| Back navigation | Add `BackHandler` — exits fullscreen first, else pops screen | None exists today; v2 verification claimed it but provided no code. |
| Lyrics in fullscreen | **Not available** — must exit fullscreen first | Fullscreen overlay has only an exit-fullscreen button. |
| Inline current-line `Text` | **Removed** (no caption). Chapter-title `Text` retained. | Lyrics visible via the panel. Declutters. |
| Static chapter `LazyColumn` jump-list | **Removed.** Replaced by the lyrics panel. | Declutters; parity with webapp `LyricJumpList`. |
| Video scaling | `VIDEO_SCALING_MODE_SCALE_TO_FIT` + `PlayerView.resizeMode = RESIZE_MODE_RESIZE` | Letterbox; baked-in lyrics never clipped. |
| Tests | Robolectric for VM/Controller/factory (existing pattern); **`AndroidJUnit4` instrumented** for `PlayerScreenTest` and `LyricsPanelTest` | `WindowInsetsControllerCompat` / Compose UI better validated instrumented. |

---

## 2. Implementation Phases

### Phase 1 — `VideoExoPlayerFactory` (production-configured in-process ExoPlayer)

**New file:** `delivery/android/app/src/main/java/org/streamofworship/android/feature/player/VideoExoPlayerFactory.kt`

```kotlin
package org.streamofworship.android.feature.player

import android.content.Context
import androidx.media3.common.C
import androidx.media3.exoplayer.ExoPlayer

/**
 * Builds a foreground ExoPlayer for video playback whose surface can be attached
 * to [androidx.media3.ui.PlayerView]. The surface is driven by the in-process
 * ExoPlayer directly (unlike a MediaController).
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

**Acceptance:** unit test asserts `durationMillis == 0` before media is set; `setMedia(file://...)` does not throw.

---

### Phase 2 — Nav graph: always wire in-process video player *(minimal shippable fix)*

**Edit:** `delivery/android/app/src/main/java/org/streamofworship/android/core/navigation/SowNavGraph.kt` (lines 118-135)

Change the `Media3PlayerController(context)` construction to build an in-process ExoPlayer:

```kotlin
val context = LocalContext.current.applicationContext
val mediaController =
    remember(jobId, context) {
        val exoPlayer = VideoExoPlayerFactory.create(context)
        Media3PlayerController(exoPlayer)   // DirectPlayerFacade path — surface works
    }
```

**Import to add:** `org.streamofworship.android.feature.player.VideoExoPlayerFactory`.

**Lifecycle note:** `Media3PlayerController.release()` already calls `player.release()` (`Media3PlayerController.kt:138-142`), which for `DirectPlayerFacade` releases the `ExoPlayer`. The existing `DisposableEffect` in `PlayerScreen.kt:68-73` calls `media3Controller?.release()` on dispose, so the in-process ExoPlayer is released when the user leaves the screen. ✅ No leak.

**Acceptance:** Navigating to a completed video render shows the rendered MP4 (with baked-in lyrics) in `PlayerView` — the area is no longer blank. **This alone fixes the blank-video bug and can ship as a minimal fix.**

---

### Phase 3 — ViewModel scoping & rotation survival (seek-only, no autoplay)

#### 3a. `SowNavGraph.kt` — switch to `viewModel()`

Replace `remember(...) { PlayerViewModel(...) }` with the `viewModel()` keyed to the back-stack entry so the `PlayerViewModel` survives configuration changes:

```kotlin
import androidx.lifecycle.viewmodel.compose.viewModel

composable(SowRoute.Player.pattern) { backStackEntry ->
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

> `viewModel(key = jobId)` scopes the instance to the `NavBackStackEntry` (`ViewModelStoreOwner`), so it survives configuration changes but is cleared when the user pops back.

#### 3b. `PlayerScreen.kt` (lines 68-73) — fix `DisposableEffect` key

Currently keyed on `viewModel`, which no longer changes on rotation → the effect would never re-fire, the old controller would leak, and the new controller would never be set up. Change the key to `media3Controller`:

```kotlin
DisposableEffect(media3Controller) {
    onDispose {
        wakeLock.release()
        media3Controller?.release()
    }
}
```

Rationale:
- Old controller released when a new one is created (rotation).
- `wakeLock.release()` still called on dispose (unchanged).
- `PlayerViewModel.onCleared()` (which also calls `controller.release()`) is NOT reached on rotation because the VM survives; the composable lifecycle is the sole owner of the in-process controller.

#### 3c. Auto-rebind media + seek after rotation (no autoplay)

After rotation, a brand-new `Media3PlayerController` + `ExoPlayer` is created, but the `PlayerViewModel` retains `mediaUrl` and `positionMillis`. Add a `LaunchedEffect` that re-binds media and restores position when the controller is new but the ViewModel already has a URL:

```kotlin
LaunchedEffect(media3Controller, state.mediaUrl) {
    val url = state.mediaUrl ?: return@LaunchedEffect
    val controller = media3Controller ?: return@LaunchedEffect
    // "No media loaded yet" proxy for a fresh controller.
    if (controller.durationMillis <= 0L) {
        controller.setMedia(url, isVideo = true)
        if (state.positionMillis > 0L) {
            controller.seekTo(state.positionMillis)
            // Do NOT call play(). Per v3 decision, user taps Play to resume.
        }
    }
}
```

> `durationMillis <= 0L` is a proxy for "no media loaded yet." After rotation, the new controller has no media → duration is 0. On fresh navigation, `viewModel.load()` also sets media (via `controller.setMedia`) before this `LaunchedEffect` typically runs, so the duration will be positive and the re-bind is skipped. The rebinding logic is idempotent (calling `setMedia` twice on the same URL is harmless).

**Acceptance:** Start a video, rotate the device → playback pauses; the view rebinds at the saved position; user taps Play to resume. `positionMillis` is preserved by the surviving `PlayerViewModel`.

---

### Phase 4 — Inline `LyricsPanel` component (replaces v2 `LyricsSheet`)

**New file:** `delivery/android/app/src/main/java/org/streamofworship/android/feature/player/LyricsPanel.kt`

A **non-modal inline collapsible panel** that lives in the `Column` *below* `PlayerView`. It expands with `Modifier.weight(1f)` to fill remaining vertical space (bounded by the parent) and **never overlaps the video** — its top edge is exactly the video's bottom edge. This satisfies "open to the bottom edge of the video screen, so as not to obstruct lyrics video playback."

**Behaviors (mirror webapp `delivery/webapp/src/components/play/LyricJumpList.tsx`):**

| Webapp behavior (`LyricJumpList.tsx`) | Android implementation |
|---|---|
| Toggle handle ("Lyrics" / "Tap to close") | `IconButton(Icons.Outlined.Subtitles, "Lyrics")` in the controls row of `PlayerScreen` toggles `lyricsExpanded`. |
| Single scrolling list of ALL chapters; current chapter expanded with lines | `LazyColumn` of all `PlaybackChapter`s. Always render chapter header. Render `PlaybackLine`s only for the current chapter (`state.currentChapter`). |
| Current line highlighted, past lines dimmed, future lines normal | `ContainerColor`/`ContentColor` per line based on `positionMillis` vs `line.startMillis`. Current = `primaryContainer`; past = `onSurface.copy(alpha=0.4f)`; future = `onSurfaceVariant`. |
| Tap chapter heading → `onJumpToChapter(index)` → seek | `OutlinedButton(onClick = { onJumpToChapter(chapter) })` — `jumpToChapter` already exists (`PlayerViewModel.kt:205-207`). |
| Tap lyric line → `onJumpToLine(...)` → seek | `TextButton(onClick = { onJumpToLine(line) })` — `jumpToLine` already exists (`PlayerViewModel.kt:209-211`). |
| Auto-scroll current line into view | `LazyListState` + `LaunchedEffect(currentLine)` → `listState.animateScrollToItem(currentChapterIndex)`. |
| Collapse | Same toggle button. (Optional swipe-down via `Modifier.nestedScroll`.) |

**Signature:**

```kotlin
@Composable
fun LyricsPanel(
    manifest: PlaybackManifest,
    positionMillis: Long,
    currentChapter: PlaybackChapter?,
    currentLine: PlaybackLine?,
    onJumpToChapter: (PlaybackChapter) -> Unit,
    onJumpToLine: (PlaybackLine) -> Unit,
    modifier: Modifier = Modifier,
)
```

**Helper:** `formatTime(millis: Long): String` → `m:ss` (mirrors webapp `LyricJumpList.tsx:109-114`). Put in the same file or reuse an existing formatter if present.

---

### Phase 5 — Wire `LyricsPanel` into `PlayerScreen`; remove redundant inline surfaces

**Edit:** `delivery/android/app/src/main/java/org/streamofworship/android/feature/player/PlayerScreen.kt`

#### 5a. Remove the inline current-line `Text` (line 108)
Per decision, remove `Text(state.currentLine?.text ?: "", ...)`. Keep the chapter-title `Text`:

```kotlin
Text(
    state.currentChapter?.title ?: "Rendered worship set",
    style = MaterialTheme.typography.titleLarge,
)
```

#### 5b. Remove the static chapter jump-list (lines 134-140)
The `LazyColumn` chapter jump-list is replaced by the expandable `LyricsPanel`.

#### 5c. Remove the `PlaybackArtifact.Video` conditional around `PlayerView`
Since `PlayerScreen` is now video-only, the `AndroidView` for `PlayerView` always renders when `media3Controller != null`:

```kotlin
AndroidView(
    factory = { PlayerView(it).apply {
        player = videoPlayer
        resizeMode = androidx.media3.ui.AspectRatioFrameLayout.RESIZE_MODE_RESIZE
    }},
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
```

`RESIZE_MODE_RESIZE` preserves aspect ratio and letterboxes so baked-in lyrics are never cropped.

#### 5d. Add the Lyrics toggle + panel
In the controls `Row` (lines 113-133), add a "Lyrics" `IconButton` before the fullscreen button:

```kotlin
var lyricsExpanded by remember { mutableStateOf(false) }

// in the Row:
IconButton(onClick = { lyricsExpanded = !lyricsExpanded }) {
    Icon(
        if (lyricsExpanded) Icons.Outlined.Subtitles else Icons.Outlined.Subtitles,
        contentDescription = "Lyrics",
        modifier = Modifier.testTag("player-lyrics-toggle"),
    )
}
```

Below the `PlayerView` (sibling inside the same `Column`), add:

```kotlin
if (lyricsExpanded) {
    val manifest = state.manifest
    if (manifest != null) {
        LyricsPanel(
            manifest = manifest,
            positionMillis = state.positionMillis,
            currentChapter = state.currentChapter,
            currentLine = state.currentLine,
            onJumpToChapter = viewModel::jumpToChapter,
            onJumpToLine = viewModel::jumpToLine,
            modifier = Modifier.weight(1f).fillMaxWidth().testTag("player-lyrics-panel"),
        )
    }
}
```

`Modifier.weight(1f)` ensures the panel consumes leftover vertical space without exceeding the screen — the video height stays fixed (220 dp portrait / 180 dp landscape) and the panel never intrudes on the video region.

**Test tags:** `player-lyrics-toggle`, `player-lyrics-panel`, `player-lyrics-current-line`, `player-lyrics-chapter-{index}`, `player-lyrics-line-{chapterIndex}-{lineIndex}`.

**Webapp parity note:** The panel stays expanded after a jump. The user dismisses by tapping the toggle again. `positionMillis` updates via the 500 ms ticker so the current-line highlight follows playback.

---

### Phase 6 — Immersive fullscreen for video + `BackHandler`

**Edit:** `feature/player/PlayerScreen.kt`

#### 6a. Activity lookup + system bars

```kotlin
val context = LocalContext.current
val activity = remember(context) { context.findActivity() }   // helper walks ContextWrapper

LaunchedEffect(state.isFullscreen) {
    val a = activity ?: return@LaunchedEffect
    val controller = WindowInsetsControllerCompat(a.window, a.window.decorView)
    if (state.isFullscreen) {
        controller.hide(WindowInsetsCompat.Type.systemBars())
        controller.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
    } else {
        controller.show(WindowInsetsCompat.Type.systemBars())
    }
}
```

Add helper `Context.findActivity(): ComponentActivity?` (walks `ContextWrapper` chain — standard snippet). Put in `core/util/ActivityExt.kt` or inline.

#### 6b. Fullscreen overlay layout

When `isFullscreen`, render the `PlayerView` as a **fullscreen overlay** (a `Box` filling the whole screen, black background, video centered). The overlay **entirely replaces** the inline `Column`:

```kotlin
if (state.isFullscreen && media3Controller != null) {
    Box(
        Modifier.fillMaxSize().background(Color.Black).testTag("player-fullscreen"),
        contentAlignment = Alignment.Center,
    ) {
        AndroidView(
            factory = { PlayerView(it).apply {
                player = videoPlayer
                useController = true
                resizeMode = androidx.media3.ui.AspectRatioFrameLayout.RESIZE_MODE_RESIZE
            }},
            update = { it.player = videoPlayer },
            modifier = Modifier.fillMaxSize(),
        )
        // Floating exit-fullscreen affordance:
        IconButton(
            onClick = { viewModel.toggleFullscreen() },
            modifier = Modifier.align(TopStart).padding(16.dp).testTag("player-fullscreen-exit"),
        ) {
            Icon(Icons.AutoMirrored.Outlined.ArrowBack, "Exit fullscreen")
        }
    }
    return   // skip the normal Column layout
}
```

**No Lyrics toggle in the overlay** — per decision, the user must exit fullscreen first to access the lyrics panel.

#### 6c. `BackHandler` (new — none exists anywhere in `delivery/android/` today)

```kotlin
import androidx.activity.compose.BackHandler

BackHandler(enabled = state.isFullscreen) {
    viewModel.toggleFullscreen()   // exit fullscreen first; does NOT pop
}
```

When not in fullscreen, `BackHandler(enabled = false)` is a no-op; default back navigates pop as before.

#### 6d. ViewModel
`toggleFullscreen` (`PlayerViewModel.kt:213-215`) is unchanged (flips `isFullscreen`).

**Acceptance:** Tap fullscreen → system bars hide, the overlay covers the screen, video fills it. Tap the exit button OR press back → bars restore. Press back again → the screen pops.

---

### Phase 7 — Pause on background

**Edit:** `feature/player/PlayerScreen.kt`

Since there is no requirement for background audio during video playback, pause when the app goes to the background:

```kotlin
val lifecycleOwner = LocalLifecycleOwner.current
DisposableEffect(lifecycleOwner) {
    val observer = LifecycleEventObserver { _, event ->
        if (event == Lifecycle.Event.ON_STOP && state.isPlaying) {
            viewModel.pause()
        }
    }
    lifecycleOwner.lifecycle.addObserver(observer)
    onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
}
```

> This replaces the complex `BackgroundHandoffController` from v1 of this lineage. No new files, no race conditions, no dual-player lifecycle management.

**Wake lock:** The existing `PlaybackWakeLock` is still acquired in `LaunchedEffect(state.isPlaying)` (lines 65-67) and released in `DisposableEffect` on dispose. Because the player pauses on `ON_STOP`, the wake lock is released naturally when playback stops.

---

### Phase 8 — RenderScreen: restrict Play to video; add "Audio only" chip

**Edit:** `delivery/android/app/src/main/java/org/streamofworship/android/feature/render/RenderScreen.kt`

Since audio-only playback is no longer supported in the worship screen, the Play button should not appear for audio-only renders.

#### 8a. `preferredPlaybackArtifact()` → nullable (lines 142-143)

```kotlin
private fun RenderJob.preferredPlaybackArtifact(): PlaybackArtifact? =
    if (mp4R2Key != null) PlaybackArtifact.Video else null
```

#### 8b. Guard `RenderForm.onReviewPrevious` (around line 122)

```kotlin
state.reviewableCompletedJob?.preferredPlaybackArtifact()?.let { artifact ->
    onPlay(job.id, artifact)
}
```

#### 8c. `RenderStatusPanel` (lines 317-326) — branch on `playArtifact`

```kotlin
val playArtifact = job.preferredPlaybackArtifact()
Row(...) {
    if (playArtifact != null) {
        Button(onClick = onPlay, ...) {
            Icon(Icons.Outlined.PlayArrow, contentDescription = null)
            Text("Play")
        }
    } else {
        // Audio-only render: explain to the user why Play is absent.
        AssistChip(
            onClick = {},
            label = { Text("Audio only") },
            leadingIcon = { Icon(Icons.Outlined.MusicNote, contentDescription = null) },
        )
    }
    OutlinedButton(onClick = onDownload, ...) {
        Icon(Icons.Outlined.Download, contentDescription = null)
        Text("Download")
    }
}
```

If "Audio only" fits without truncation on the target device widths, extend the label to "Audio only — download to listen" (mirrors the v2 risk-table note). Otherwise keep "Audio only" and rely on the Download button beside it.

**Acceptance:** Navigating to an audio-only render job shows a Download button + an "Audio only" chip; no Play button; the user cannot reach `PlayerScreen` with an audio-only artifact.

---

### Phase 9 — Offline playback verification (no new code)

The existing `PlayerViewModel.load()` (lines 102-138) already:

1. Checks `offlineCacheRepository?.getArtifact(renderJobId, OfflineArtifactKind.Video)`.
2. If `cached.isPlayableOffline`, calls `controller.setMedia(cached.localUri.orEmpty(), isVideo = true)`.
3. `DirectPlayerFacade.setMedia()` (line 118) calls `MediaItem.fromUri(url)`, which handles `file://` URIs natively.

**Verification step (manual):** Confirm a downloaded MP4 plays correctly through the in-process `ExoPlayer`. If `cached.localUri` points to a path that `ExoPlayer` cannot resolve, a `PlaybackException` surfaces through `PlayerEvent.Error` and is shown in the existing error UI.

---

### Phase 10 — Tests

#### 10a. `LyricsPanelTest.kt` (new)
**New file:** `delivery/android/app/src/test/java/org/streamofworship/android/feature/player/LyricsPanelTest.kt`

Runner: **`@RunWith(AndroidJUnit4::class)`** + `createAndroidComposeRule<ComponentActivity>()` (matches existing `PlayerScreenTest.kt:24-27`). Compose UI + fullscreen assertion at the composable level is better served instrumented.

Tests:
- Renders `LyricsPanel` with a 2-chapter manifest → both chapter titles display.
- Set `positionMillis` inside chapter 2 → chapter 2's lines render and chapter 1's lines do NOT.
- Current line has test tag `player-lyrics-current-line`.
- Tap a line → `onJumpToLine` invoked with that line.
- Tap a chapter header → `onJumpToChapter` invoked.

#### 10b. `PlayerScreenTest.kt` (extend)
Runner unchanged (`AndroidJUnit4`).

- Update/replace existing assertion at line ~54 (`onNodeWithText("耶和華是我的牧者")`) — the inline current-line `Text` is removed. Replace with: expand `player-lyrics-toggle` and assert the text appears inside `player-lyrics-panel`.
- Add: tap `player-lyrics-toggle` → `player-lyrics-panel` displays with all chapter titles.
- Add: pass a `DirectPlayerFacade`-backed controller → `player-video-view` is displayed; assert `resizeMode == RESIZE_MODE_RESIZE`.
- Add: when `state.isFullscreen = true` → `player-fullscreen` tag exists in the view hierarchy.
- Add: `BackHandler` test — fullscreen active → press back → `isFullscreen` flips to false (does NOT pop the screen).
- Skip asserting real window-inset visibility in Robolectric/instrumented unit tests (the OS-level bar visibility is covered by manual device verification #5/#6).

#### 10c. `Media3PlayerControllerTest.kt` (extend)
Runner unchanged (`RobolectricTestRunner`).

- Add: `VideoExoPlayerFactory.create(context)` returns a playable `ExoPlayer` that reports `durationMillis == 0` before media is set.
- Add: `DirectPlayerFacade.setMedia("file:///.../test.mp4", isVideo = true)` succeeds — `setMedia` + `prepare` does not throw (Robolectric cannot truly decode video).

#### 10d. `PlayerViewModelTest.kt` (extend)
Runner unchanged (plain JUnit4 + `kotlinx-coroutines-test`).

- Add: `load(artifact = Video)` with a cached offline artifact calls `controller.setMedia(localUri, true)`.
- Add: `load()` ignores the artifact parameter and always queries `OfflineArtifactKind.Video` (since audio-only playback is removed, `artifact` is vestigial after Phase 8). Either assert this behavior or document that the parameter is deprecated.
- Existing `jumpToChapter`/`jumpToLine` tests should still pass.

#### 10e. `RenderScreenTest.kt` (existing-conditional)
If a test for `RenderScreen` exists, add: an audio-only `RenderJob` shows the "Audio only" chip and no Play button. If no `RenderScreenTest.kt` exists, defer (instrumented test scope).

---

## 3. Files Modified / Created

| File | Change Type | Phase |
|------|-------------|-------|
| `feature/player/VideoExoPlayerFactory.kt` | **New** | 1 |
| `core/navigation/SowNavGraph.kt` | Edit: always create in-process player; use `viewModel()` for `PlayerViewModel` | 2, 3 |
| `feature/player/PlayerScreen.kt` | Edit: remove artifact conditionals, remove redundant current-line `Text` + jump-list, add lyrics toggle + panel, immersive fullscreen + `BackHandler`, fix `DisposableEffect` key, add pause-on-background, add media rebind on rotation, set `resizeMode` | 3, 5, 6, 7 |
| `feature/player/LyricsPanel.kt` | **New** | 4 |
| `feature/render/RenderScreen.kt` | Edit: nullable `preferredPlaybackArtifact`; "Audio only" `AssistChip` | 8 |
| `core/util/ActivityExt.kt` (or inline helper) | **New** (optional) | 6 |
| `test/.../feature/player/LyricsPanelTest.kt` | **New** | 10 |
| `test/.../feature/player/PlayerScreenTest.kt` | Edit | 10 |
| `test/.../feature/player/Media3PlayerControllerTest.kt` | Edit | 10 |
| `test/.../feature/player/PlayerViewModelTest.kt` | Edit | 10 |

**Total: 3 new prod files (1 optional util), 1 new test file, 4 edited files.**

---

## 4. Implementation Order

1. **Phase 1** (`VideoExoPlayerFactory`) — standalone, no deps.
2. **Phase 2** (nav graph always in-process) — depends on Phase 1. **This alone fixes the blank-video bug.** Shippable independently as a minimal fix.
3. **Phase 3** (ViewModel scoping + config-change rebinding, seek-only) — depends on Phase 2. Critical for rotation UX.
4. **Phase 8** (RenderScreen audio-only chip) — independent; can be done in parallel with 1-3.
5. **Phase 4** (`LyricsPanel`) — standalone UI component.
6. **Phase 5** (wire panel into `PlayerScreen`, remove redundant `Text` + jump-list) — depends on Phase 4.
7. **Phase 6** (immersive fullscreen + `BackHandler`) — depends on Phase 2 (video renders). Independent of 4/5.
8. **Phase 7** (pause on background) — trivial; depends on nothing.
9. **Phase 9** (offline verification) — manual test with a downloaded video.
10. **Phase 10** (tests) — incrementally alongside each phase.

Phases 4, 5, 6, 7 are mutually independent once Phase 2 lands; they can be parallelized.

---

## 5. What This Does NOT Change

- **`SowPlaybackService`** — untouched. Still owns the audio `ExoPlayer` + `MediaSession`, but `PlayerScreen` no longer connects to it. It remains available for any future background-audio use case; a `// TODO: unused by PlayerScreen after v3` comment marks its status.
- **`ServiceMediaControllerFacade`** — untouched. Still functional, just unused by `PlayerScreen` after this plan.
- **Render worker / webapp** — no changes. The MP4 already has lyrics baked in; the bug is purely client-side rendering.
- **`PlaybackApi` / `HttpPlaybackRepository` / signed-URL flow** — untouched.
- **`PlaybackManifest` / `PlaybackChapter` / `PlaybackLine` model** (`data/playback/PlaybackModels.kt:44-68`) — untouched. Reused by the new lyrics panel.
- **`PlaybackWakeLock`** — untouched (still guards playback CPU; the in-process `ExoPlayer` benefits from it).
- **Audio-only download flow** — untouched. Audio MP3s are still rendered and downloadable via `ShareScreen` / `DownloadManager`.
- **`Media3PlayerController(context)` constructor** — preserved. The `ServiceMediaControllerFacade` path still exists for any future feature that needs it.

---

## 6. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| In-process `ExoPlayer` for video is killed when the Activity is destroyed mid-stream (e.g., during configuration change) | **Mitigated by Phase 3:** `viewModel(key = jobId)` scopes the `PlayerViewModel` across config changes. The last playback position is saved in the surviving ViewModel state. A new `ExoPlayer` is created and seeks to that position on recreation. The user experiences a brief buffering pause, then taps Play to resume — not a restart from zero, not surprise autoplay. |
| `DisposableEffect` key change (`viewModel` → `media3Controller`) could cause the controller to be released at the wrong time | The new key is correct: the controller owns the native `ExoPlayer` resource and must be released when the controller reference changes (rotation). The `ViewModel` no longer calls `controller.release()` in `onCleared()` on rotation because it survives. The composable lifecycle is the sole owner of the in-process controller. |
| Inline `LyricsPanel` `Modifier.weight(1f)` could push the video off-screen on very small devices or in landscape | Verified against the layout: `PlayerView` height is fixed (220 dp portrait / 180 dp landscape) BEFORE the weighted panel; the panel only consumes leftover space. If `PlayerView` height + minimum panel height exceeds the screen, default the panel to collapsed in landscape. |
| Removing the static chapter jump-list changes UX for users who relied on it | The lyrics panel provides the same functionality (list of chapters, tap to jump) plus line-level navigation, so functionality is preserved. The interaction requires one extra tap to expand the panel, which matches the webapp affordance. |
| `findActivity()` context walk can return null in some embedded contexts | Defensive null check; immersive is a no-op if activity is null (fallback to the non-immersive inline layout). |
| Video MP4 is large; signed-URL expiry (1 h) during a long fullscreen session | Already handled by the existing `ExpiredSignedUrl` banner + `Retry` (`PlayerScreen.kt:162-169`, `PlayerViewModel.kt:122-128`). Re-fetches on retry. No change. |
| RenderScreen hides Play for audio-only renders, which may confuse users who previously played audio | The "Audio only" `AssistChip` beside the Download button explains why Play is absent. |
| Rotation `durationMillis <= 0L` proxy could false-positive if media genuinely has 0 duration | MP4 durations are > 0 once loaded; pre-load `durationMillis == 0` is the intended signal. If false positives appear during testing, add an explicit `hasMedia: Boolean` flag on the controller as a cleaner alternative. |
| Robolectric cannot fully simulate `WindowInsetsControllerCompat` bar visibility | Phase 10b asserts the fullscreen overlay hierarchy presence (`player-fullscreen`), not actual OS bar visibility. Real device verification covers the latter. |

---

## 7. Verification

After implementation:

1. **Video renders:** Navigate to a completed video render job (`RenderJob.mp4R2Key != null`) → Player screen → `PlayerView` shows the video with baked-in lyrics (not blank).
2. **Audio-only cannot play:** Navigate to a render job with only `mp3R2Key` → Render status shows a Download button + an "Audio only" chip; "Play" is not offered.
3. **Rotation survival (revised):** Start playing a video, rotate the device → the video rebinds at the saved position, **paused**; the user taps Play to resume (no autoplay).
4. **Lyrics panel:** Tap the Lyrics toggle → `LyricsPanel` expands below the video (never overlapping it); lists all chapters; the current chapter's lines are visible; the current line is highlighted. Tap a line → playback seeks; the panel stays expanded; the highlight moves. Tap the toggle again → collapses.
5. **Immersive fullscreen:** Tap fullscreen → system bars hide, the video fills the screen, and an exit affordance restores the bars.
6. **Back from fullscreen (new):** While in fullscreen, system/gesture back → exits fullscreen. While not in fullscreen, back → pops the screen.
7. **No lyrics in fullscreen:** In fullscreen the Lyrics toggle is not visible; the user must exit fullscreen to access the panel.
8. **Background pause:** Start a video, press Home → playback pauses. Return to the app → the video is paused at the same frame; tap Play to resume.
9. **Offline playback:** Download a video, turn off the network, navigate to Player → the video plays from local cache.
10. **Letterbox:** The video aspect ratio is preserved; baked-in lyrics are never cropped.
11. **Tests pass:**

```bash
cd delivery/android
./gradlew testDebugUnitTest koverXmlReport
./gradlew lintDebug
./gradlew assembleDebug
# PlayerScreen + LyricsPanel instrumented tests (device/emulator required):
./gradlew connectedDebugAndroidTest
```

---

## 8. References

- Root cause — `MediaController` cannot render a video surface: `Media3PlayerController.kt:179-305` (`ServiceMediaControllerFacade` exposes `MediaController` as `playerView`) + `PlayerScreen.kt:58-59, 91-106` (binds it to `PlayerView`).
- In-process `ExoPlayer` alternative (`DirectPlayerFacade`): `Media3PlayerController.kt:106-169`, constructed via `Media3PlayerController(player: Player)` (line 20).
- Render worker bakes lyrics into MP4 frames: `delivery/render-worker/src/sow_render_worker/frame_renderer.py`, `video_engine.py` (`encode_video_with_ffmpeg` pipes Pillow-painted RGB frames to FFmpeg).
- Webapp `LyricJumpList` (the model for the Android panel): `delivery/webapp/src/components/play/LyricJumpList.tsx`.
- Existing jump helpers in ViewModel: `PlayerViewModel.kt:205-211` (`jumpToChapter`, `jumpToLine`).
- Model: `data/playback/PlaybackModels.kt:44-68` (`PlaybackManifest`, `PlaybackChapter`, `PlaybackLine`).
- Existing test patterns: `PlayerViewModelTest.kt` (`FakePlayerController`, `FakePlaybackRepository`, plain JUnit4), `Media3PlayerControllerTest.kt` (`FakeMediaPlayerFacade`, in-process ExoPlayer, `RobolectricTestRunner`), `PlayerScreenTest.kt` (`AndroidJUnit4` + `createAndroidComposeRule`).
- Dependencies confirmed available (no new deps): `material3` (`ModalBottomSheet` no longer used; `AssistChip` available) at `build.gradle.kts:116`; `core-ktx:1.15.0` (`WindowInsetsControllerCompat`) at line 120; `lifecycle-viewmodel-compose:2.8.7` at line 121.
- v2 of this plan (for comparison, now superseded): `specs/enhance-android-worship-playback-video-lyrics-v2.md`.
- v1 of this plan (for comparison): `specs/enhance-android-worship-playback-video-lyrics.md`.

---

## 9. Changelog from v2 → v3

| Area | v2 | v3 |
|-----|-----|-----|
| **Rotation behavior** | Ambiguous — verification #3 said "resumes at the same position" (implied autoplay) but the code comment said seek-only | **Explicit: seek only, no autoplay.** User taps Play. Verification text updated. |
| **`BackHandler`** | Verification #8 claimed "back exits fullscreen first" but no code was provided | **Phase 6c adds `BackHandler(enabled = state.isFullscreen)`.** |
| **Lyrics surface** | `ModalBottomSheet` overlay | **Inline `LyricsPanel`** with `Modifier.weight(1f)` — never overlaps the video (per "open to the bottom edge of the video screen" decision). |
| **Lyrics in fullscreen** | Implied available via the overlay sheet | **Disabled.** Must exit fullscreen first. |
| **Inline current-line `Text`** | Removed, chapter-title retained | **Same — removed (no caption), chapter-title retained.** Made explicit. |
| **RenderScreen audio-only UX** | Hide Play only | **Hide Play + add "Audio only" `AssistChip`.** |
| **PlayerScreenTest runner** | 10a described `LyricsSheetTest` as "Robolectric + Compose UI test" but `PlayerScreenTest.kt` actually uses `AndroidJUnit4` | Corrected: **`AndroidJUnit4` instrumented** for both `PlayerScreenTest` and the new `LyricsPanelTest`; Robolectric retained for VM/Controller/factory. |
| **`chapters.json` claim** | "reads `chapters.json` (~106)" | Corrected: **`repository.chapters(renderJobId)`** (HTTP/repository call returning `PlaybackManifest`). |
| **Video scaling** | `VIDEO_SCALING_MODE_SCALE_TO_FIT` only | **+ `PlayerView.resizeMode = RESIZE_MODE_RESIZE`** (letterbox, no crop). |
| **New risk** | — | Inline `LyricsPanel` `weight(1f)` overflow on small/landscape devices — mitigated by fixed `PlayerView` height + collapsed-in-landscape default. |
| **New risk** | — | Rotation `durationMillis <= 0L` proxy false positives — mitigated via optional `hasMedia` flag if needed. |
| **Files** | `LyricsSheet.kt` (new) | **`LyricsPanel.kt`** (renamed; semantics changed). Otherwise file set identical. |
