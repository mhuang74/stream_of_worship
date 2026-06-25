# Enhance Android Worship Playback: Show Rendered Lyrics Video

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

---

## 1. Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Video player wiring | **In-process `ExoPlayer` for video; keep service `MediaController` for audio-only** | `MediaController` cannot attach a video surface. An in-process `ExoPlayer` directly attached to `PlayerView` renders the MP4 (with baked-in lyrics). Audio-only keeps lock-screen / background controls via the existing service path. |
| Facade reuse | Use existing `DirectPlayerFacade` (`Media3PlayerController.kt:106-169`) for video | It already exposes `playerView`/`playerViewState` correctly and wires `Player.Listener` events. Just needs a production-configured `ExoPlayer`. |
| ExoPlayer construction for video | New `VideoExoPlayerFactory` (thin wrapper around `ExoPlayer.Builder`) | Centralizes the video player config (`handleAudioBecomingNoisy`, `WAKE_MODE_NETWORK`, `setVideoScalingMode`). Mirrors `SowPlaybackService.onCreate()` config for parity. |
| Controller selection | Nav graph chooses `Media3PlayerController(player)` (video) vs `Media3PlayerController(context)` (audio) based on route `artifact` arg | Localizes the branch; `PlayerViewModel` + `PlayerScreen` stay artifact-agnostic. |
| External lyrics `Text` | **Replace with a pull-up Lyrics Sheet** mirroring webapp `LyricJumpList` | Lyrics are baked into the video, so a single current-line `Text` is redundant. Instead, mirror the webapp's `LyricJumpList` (`delivery/webapp/src/components/play/LyricJumpList.tsx`): a bottom sheet listing all chapters, current chapter expanded with all lines+timestamps, tap any line/chapter to seek. The baked-in video lyrics remain the primary on-screen view during playback. |
| Lyrics sheet implementation | Material3 `ModalBottomSheet` (already available via `androidx.compose.material3`) | No new dependency. Mirrors webapp "Pull Up Lyrics Sheet" affordance. Dismissal is native (drag down / scrim tap). |
| Fullscreen | **True immersive fullscreen** via `WindowInsetsControllerCompat` (core-ktx 1.15.0 already deps) | The current "fullscreen" only bumps PlayerView height to 420dp. Upgrade to hide system bars + fill screen. No new dependency. |
| Background audio handoff | On `ON_STOP` while video is playing, hand current position + media URL to `SowPlaybackService` (via `MediaController`) so audio continues in background; on `ON_START`, if service is playing, sync position back to video ExoPlayer | Keeps background-audio continuity (chosen answer). Video itself cannot render while backgrounded — audio continues. |
| Lyrics sheet ↔ video | Sheet is an overlay above the video (and above controls). Opening the sheet does NOT pause video; it's a navigation aid. | Matches webapp behavior (`LyricJumpList` is a non-modal overlay that seeks on tap). |
| Lyrics sheet data source | Reuse `PlaybackManifest` already loaded by `PlayerViewModel` (same `chapters.json` the webapp uses) | No new fetch. `PlaybackChapter`/`PlaybackLine` already in ms. |
| `collectAsStateWithLifecycle` | **Do NOT** switch to it | `lifecycle-runtime-compose` is NOT a declared dep (`build.gradle.kts` line 122 only has `lifecycle-runtime-ktx`). Stick with `collectAsState` to avoid adding a dependency. |
| Tests | Robolectric unit tests for ViewModel + a new `PlayerScreen` test asserting the sheet shows/hides; `Media3PlayerControllerTest` extended for the video facade path | Matches existing test patterns (`PlayerViewModelTest`, `Media3PlayerControllerTest`, `PlayerScreenTest`). |

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

**Why a factory (not inline in composable):** testable in isolation; the `ExoPlayer.Builder` config is identical to `SowPlaybackService.onCreate()` (lines 23-30) minus the `MediaSession` wrapping, which the service owns exclusively for audio-only.

---

### Phase 2: Nav graph wires the correct facade by artifact type

**File:** `delivery/android/app/src/main/java/org/streamofworship/android/core/navigation/SowNavGraph.kt` (lines 118-135)

Currently:
```kotlin
val context = LocalContext.current.applicationContext
val mediaController = remember(jobId, context) { Media3PlayerController(context) }
```

Change so the controller depends on `artifact`:

```kotlin
val context = LocalContext.current.applicationContext
val mediaController =
    remember(jobId, context, artifact) {
        if (artifact == PlaybackArtifact.Video) {
            val exoPlayer = VideoExoPlayerFactory.create(context)
            Media3PlayerController(exoPlayer)   // DirectPlayerFacade path — surface works
        } else {
            Media3PlayerController(context)     // ServiceMediaControllerFacade path — background audio
        }
    }
```

**Lifecycle note:** `Media3PlayerController.release()` already calls `player.release()` which for `DirectPlayerFacade` releases the `ExoPlayer` (`Media3PlayerController.kt:138-142`). The existing `DisposableEffect` in `PlayerScreen.kt:68-73` calls `media3Controller?.release()` on dispose, so the in-process ExoPlayer is released when leaving the screen. ✅ No leak.

**Imports to add:** `org.streamofworship.android.feature.player.VideoExoPlayerFactory` (already same package as `Media3PlayerController`).

---

### Phase 3: Verify `PlayerView` surface rendering works with the in-process player

**No code change required** — this phase is verification. With Phase 2, when `artifact == Video`:
- `Media3PlayerController.playerView` returns the in-process `ExoPlayer` (`Media3PlayerController.kt:30-31` + `DirectPlayerFacade.playerView`, line 107).
- `playerViewState` emits the `ExoPlayer` immediately (`DirectPlayerFacade.playerViewState = MutableStateFlow(playerView)`, line 110).
- `PlayerScreen.kt:90-106` already assigns `videoPlayer` to `PlayerView.player` via `update = { it.player = videoPlayer }`.
- `PlayerView` attaches its surface to the real `ExoPlayer` → video frames render. ✅

> ⚠️ Caveat to verify during implementation: `PlayerViewModel.load()` calls `controller.setMedia(url, isVideo)` where `setMedia` ignores the `isVideo` flag (`Media3PlayerController.kt:45-51`). For `DirectPlayerFacade`, `setMedia` does `playerView.setMediaItem(MediaItem.fromUri(url))` (line 119) — this is correct for both audio and video. No change needed.

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

#### 5a. Remove the redundant single-line `Text`
Delete lines 107-108 (the `Text(state.currentChapter?.title ...)` + `Text(state.currentLine?.text ...)` pair). The song title still shows in the sheet; the baked-in video lyrics are the primary view.

Keep a compact "now playing" label OR rely on the sheet for chapter info. Decision: keep a single small `Text(state.currentChapter?.title ?: "Rendered worship set")` as a header (so when the sheet is closed there's still context), but remove the `currentLine` `Text`.

```kotlin
Text(
    state.currentChapter?.title ?: "Rendered worship set",
    style = MaterialTheme.typography.titleLarge,
)
// (removed) Text(state.currentLine?.text ?: "", ...)
```

#### 5b. Add the Lyrics toggle button + sheet
In the controls `Row` (lines 113-133), add a "Lyrics" `IconButton` (e.g. `Icons.Outlined.Subtitles` / `Icons.Outlined.Lyrics`) before the fullscreen button:

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
            onJumpToChapter = { chapter ->
                viewModel.jumpToChapter(chapter)
                showLyricsSheet = false   // mirror webapp: jump dismisses sheet
            },
            onJumpToLine = { line ->
                viewModel.jumpToLine(line)
                showLyricsSheet = false
            },
            onDismiss = { showLyricsSheet = false },
        )
    }
}
```

**Webapp parity note:** webapp does NOT auto-dismiss on jump (it keeps the sheet open so users can jump again). Replicate that — do NOT dismiss on tap. Let the user dismiss via scrim/drag. Update: remove `showLyricsSheet = false` from the jump handlers; let the sheet stay open. The sheet's `positionMillis` updates via the 500ms ticker so the current-line highlight follows playback.

**Test tag:** `"player-lyrics-sheet"`, `"player-lyrics-toggle"`.

#### 5c. Mirror webapp: keep chapter jump-list?
The webapp `LyricJumpList` IS the chapter jump list. The existing `LazyColumn` jump-list in `PlayerScreen.kt:134-140` is now redundant with the sheet. **Remove it** to match webapp (which has no separate static jump list). Decision: remove lines 134-140.

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

LaunchedEffect(state.isFullscreen, state.artifact) {
    val controller = view?.let { WindowInsetsControllerCompat(activity!!.window, it) } ?: return@LaunchedEffect
    if (state.isFullscreen && state.artifact == PlaybackArtifact.Video) {
        controller.hide(WindowInsetsCompat.Type.systemBars())
        controller.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
    } else {
        controller.show(WindowInsetsCompat.Type.systemBars())
    }
}
```

Add helper `Context.findActivity(): ComponentActivity?` (walks `ContextWrapper` chain — standard snippet). Put in `core/util/ActivityExt.kt` or inline.

#### 6b. Fullscreen layout
When `isFullscreen && artifact == Video`, render the `PlayerView` as a **fullscreen overlay** (Box filling the whole screen, black background, video centered) instead of inside the padded `Column`. Use a `Box` overlay via `Popup` or a conditional layout:

```kotlin
if (state.isFullscreen && state.artifact == PlaybackArtifact.Video && media3Controller != null) {
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

### Phase 7: Background audio handoff (video → service)

**Files:** `PlayerScreen.kt`, `PlayerViewModel.kt`, new `BackgroundHandoffController.kt`

When the user backgrounds the app mid-video, audio continues via `SowPlaybackService`; on return, the video ExoPlayer syncs to the service's position.

#### 7a. New: `BackgroundHandoffController`
**New file:** `delivery/android/app/src/main/java/org/streamofworship/android/feature/player/BackgroundHandoffController.kt`

```kotlin
class BackgroundHandoffController(
    private val appContext: Context,
) {
    /** Hand off current playback to the service so audio continues in background. */
    suspend fun handoff(mediaUrl: String, positionMillis: Long, play: Boolean) {
        val token = SessionToken(appContext, ComponentName(appContext, SowPlaybackService::class.java))
        val controller = MediaController.Builder(appContext, token).buildAsync().await()
        controller.setMediaItem(MediaItem.fromUri(mediaUrl))
        controller.prepare()
        controller.seekTo(positionMillis.coerceAtLeast(0L))
        if (play) controller.play()
        // Do NOT release the controller here — keep it bound so the session stays alive.
        // It will be released on [resume] or when the service stops.
    }

    /** On return, read the service position and stop service playback. */
    suspend fun resume(): Long? {
        val token = SessionToken(appContext, ComponentName(appContext, SowPlaybackService::class.java))
        val controller = runCatching { MediaController.Builder(appContext, token).buildAsync().await() }.getOrNull() ?: return null
        val pos = controller.currentPosition
        controller.pause()
        MediaController.releaseFuture(/* the future */)   // see note below
        return pos.takeIf { it > 0 }
    }
}
```

> ⚠️ Lifecycle nuance: `MediaController` futures must be released. Hold the future as a field, release on `resume()`. Keep this in a `remember`-scoped object in the composable.

#### 7b. Lifecycle observer in `PlayerScreen`
Use `androidx.lifecycle.LifecycleEventObserver` via `LocalLifecycleOwner`:

```kotlin
val lifecycleOwner = LocalLifecycleOwner.current
val handoff = remember(context) { BackgroundHandoffController(context.applicationContext) }
var handedOff by remember { mutableStateOf(false) }

DisposableEffect(lifecycleOwner) {
    val observer = LifecycleEventObserver { _, event ->
        when (event) {
            Lifecycle.Event.ON_STOP -> {
                if (state.artifact == PlaybackArtifact.Video && state.isPlaying) {
                    val url = state.mediaUrl ?: return@LifecycleEventObserver
                    viewModel.pause()  // pause the in-process ExoPlayer
                    launchScope.launch { handoff.handoff(url, state.positionMillis, play = true); handedOff = true }
                }
            }
            Lifecycle.Event.ON_START -> {
                if (handedOff) {
                    launchScope.launch {
                        handoff.resume()?.let { viewModel.seekTo(it) }
                        handedOff = false
                    }
                }
            }
            else -> Unit
        }
    }
    lifecycleOwner.lifecycle.addObserver(observer)
    onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
}
```

`launchScope` = a `rememberCoroutineScope()`. `viewModel.pause()` / `viewModel.seekTo()` already exist.

#### 7c. Why audio-only does NOT need this
Audio uses `ServiceMediaControllerFacade` directly, so the service owns the ExoPlayer — backgrounding works automatically. Only the video path (in-process ExoPlayer, no service) needs handoff.

**Complexity flag:** This is the most involved phase. If time-constrained, Phase 7 can be shipped behind a simple "pause on background" fallback (drop the handoff; just `viewModel.pause()` on `ON_STOP` during video). Mark 7a/7b as the target, with the fallback documented.

---

### Phase 8: Tests

#### 8a. `LyricsSheetTest.kt` (new)
**New file:** `delivery/android/app/src/test/java/org/streamofworship/android/feature/player/LyricsSheetTest.kt`

Robolectric + Compose UI test (`createAndroidComposeRule`):
- Renders `LyricsSheet` with a 2-chapter manifest; assert both chapter titles display.
- Set `positionMillis` inside chapter 2; assert chapter 2's lines render and chapter 1's lines do NOT.
- Assert current line is highlighted (test tag `player-lyrics-current-line`).
- Tap a line → assert `onJumpToLine` invoked with that line.
- Tap a chapter header → assert `onJumpToChapter` invoked.

#### 8b. `PlayerScreenTest.kt` (extend)
**File:** `delivery/android/app/src/test/java/org/streamofworship/android/feature/player/PlayerScreenTest.kt`
- Existing test passes `media3Controller = null`, so `PlayerView` is skipped and lyrics render as text. After Phase 5 removes the `currentLine` `Text`, **update the existing assertion** at line 54 (`onNodeWithText("耶和華是我的牧者")`) — it will now be in the lyrics sheet, not always-visible. Either: open the sheet then assert, OR keep a chapter-title label and assert that instead.
- Add test: "lyrics toggle button shows sheet with all chapters" — click `"player-lyrics-toggle"`, assert `"player-lyrics-sheet"` displays.
- Add test: "video artifact with media3Controller renders PlayerView" — pass a `DirectPlayerFacade`-backed controller, assert `"player-video-view"` is displayed.

#### 8c. `Media3PlayerControllerTest.kt` (extend)
**File:** `delivery/android/app/src/test/java/org/streamofworship/android/feature/player/Media3PlayerControllerTest.kt`
- Add: in-process video path exposes a non-null `playerView` (already covered by existing `"direct player facade exposes the underlying player view"` test at lines 57-69 — verify it still passes with `VideoExoPlayerFactory`-built player).
- Add: `setMedia` with `isVideo=true` on `DirectPlayerFacade` sets `MediaItem.fromUri(url)` (assert via the fake facade — already covered).

#### 8d. `PlayerViewModelTest.kt`
No changes needed — the ViewModel is artifact-agnostic; jump helpers already tested. Verify `jumpToChapter`/`jumpToLine` still pass (they do; lines 47-65 exercise `nextChapter`/`previousChapter` which call `seekTo`).

---

## 3. Files Modified / Created

| File | Change Type | Phase |
|------|-------------|-------|
| `feature/player/VideoExoPlayerFactory.kt` | **New** | 1 |
| `core/navigation/SowNavGraph.kt` | Edit: branch controller by artifact (lines 118-135) | 2 |
| `feature/player/LyricsSheet.kt` | **New** | 4 |
| `feature/player/PlayerScreen.kt` | Edit: remove redundant lyrics Text, add lyrics toggle + sheet, immersive fullscreen, handoff observer | 5, 6, 7 |
| `feature/player/BackgroundHandoffController.kt` | **New** | 7 |
| `core/util/ActivityExt.kt` (or inline helper) | **New** (optional) | 6 |
| `test/.../feature/player/LyricsSheetTest.kt` | **New** | 8 |
| `test/.../feature/player/PlayerScreenTest.kt` | Edit: update assertion, add toggle/video tests | 8 |
| `test/.../feature/player/Media3PlayerControllerTest.kt` | Edit: verify video facade path | 8 |

**Total: 4 new prod files, 1 new test file, 4 edited files.**

---

## 4. Implementation Order

1. **Phase 1** (`VideoExoPlayerFactory`) — standalone, no deps.
2. **Phase 2** (nav graph branch) — depends on Phase 1. **This alone fixes the blank-video bug.** Ship-able independently as a minimal fix.
3. **Phase 3** (verify) — manual run on device/emulator with a rendered video job.
4. **Phase 4** (`LyricsSheet`) — standalone UI component; depends on existing `PlaybackManifest`/`PlaybackChapter`/`PlaybackLine` models.
5. **Phase 5** (wire sheet into `PlayerScreen`, remove redundant Text) — depends on Phase 4.
6. **Phase 6** (immersive fullscreen) — depends on Phase 2 (video renders). Independent of 4/5.
7. **Phase 7** (background handoff) — depends on Phase 2. Most complex; can be deferred behind a pause-on-background fallback.
8. **Phase 8** (tests) — incrementally with each phase (LyricsSheet test after 4, screen tests after 5/6, controller test after 2).

Phases 4, 5, 6 are independent of each other once Phase 2 lands; they can be parallelized.

---

## 5. What This Does NOT Change

- **`SowPlaybackService`** — untouched. Still owns the audio ExoPlayer + MediaSession for audio-only playback and lock-screen controls.
- **`ServiceMediaControllerFacade`** — untouched. Still used for audio-only.
- **Render worker / webapp** — no changes. The MP4 already has lyrics baked in; the bug is purely client-side rendering.
- **`PlaybackApi` / `HttpPlaybackRepository` / signed-URL flow** — untouched. The signed R2 URL for `output.mp4` is already fetched correctly (`renderedVideoUrl`).
- **`chapters.json`** model & fetch — untouched. Reused by the new sheet.
- **`PlaybackWakeLock`** — untouched (still guards video playback CPU; the in-process ExoPlayer benefits from it the same way).
- **Audio-only playback UX** — unchanged; still uses the service path with background controls.

---

## 6. Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| In-process `ExoPlayer` for video is killed when the Activity is destroyed mid-stream (e.g. during configuration change) | `remember(jobId, context, artifact)` holds it; `DisposableEffect` releases on dispose. Config changes: Compose `remember` survives recomposition but NOT Activity recreation — acceptable; user re-enters the screen. For rotation, consider `rememberSaveable` for `positionMillis` (already in `PlayerUiState`) + re-seek on recreate. |
| Background handoff (`Phase 7`) is complex and may race with service binding | Gate behind a feature; if `MediaController` future fails to connect within a timeout, fall back to `viewModel.pause()`. Always stop the in-process player before starting service playback to avoid double-audio. |
| `ModalBottomSheet` current version in compose-bom `2024.12.01` is stable but `rememberModalBottomSheetState` API may differ slightly | Use the stable `ModalBottomSheet` signature (`onDismissRequest` + `sheetContent`). Avoid experimental skip-partially-expanded variants. |
| Removing the static chapter jump-list (`Phase 5c`) changes UX for audio-only playback (no quick chapter nav) | Audio-only users still get chapters via the sheet. Alternatively keep the jump-list only when `artifact == Audio`. Recommend: remove for both (webapp parity), but revisit if user feedback objects. |
| `findActivity()` context walk can return null in some embedded contexts | Defensive null check; immersive is a no-op if activity is null (fallback to current height-only behavior). |
| Video MP4 is large; signed-URL expiry (1h) during a long fullscreen session | Already handled by existing `ExpiredSignedUrl` banner + `Retry` (`PlayerScreen.kt:162-169`, `PlayerViewModel.kt:122-128`). Re-fetches on retry. No change. |

---

## 7. Verification

After implementation:

1. **Video renders:** Navigate to a completed video render job (`RenderJob.mp4R2Key != null`) → Player screen → `PlayerView` shows the video with baked-in lyrics (not blank).
2. **Audio-only still works:** Navigate to an audio-only render job → no `PlayerView`, controls + chapter title show, background audio continues when app is backgrounded.
3. **Lyrics sheet:** Tap "Lyrics" button → `ModalBottomSheet` opens, lists all chapters, current chapter's lines visible, current line highlighted. Tap a line → playback seeks, sheet stays open, highlight moves. Tap scrim/drag down → sheet closes.
4. **Immersive fullscreen:** Tap fullscreen → system bars hide, video fills screen, an exit affordance restores bars. Works only for video artifact.
5. **Background handoff:** Start a video, press Home → audio continues (notification shows transport controls). Return to app → video seeks to where audio was and resumes.
6. **Back navigation:** Back button from fullscreen exits fullscreen first (if in fullscreen) OR pops the screen (if not). Confirm via `BackHandler`.
7. **Robolectric tests pass:** `./gradlew testDebugUnitTest` green; new `LyricsSheetTest` covers expand/collapse + tap-to-seek.

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
- Dependencies confirmed available (no new deps): `material3` (`ModalBottomSheet`) at `build.gradle.kts:116`; `core-ktx:1.15.0` (`WindowInsetsControllerCompat`) at line 120.
