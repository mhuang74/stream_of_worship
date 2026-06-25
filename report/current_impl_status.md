# Current Implementation Status

## 2026-06-25

- Implemented `specs/enhance-android-worship-playback-video-lyrics-v3.md` (Android worship playback: rendered lyrics video + immersive UI).
  - Phase 1: New `feature/player/VideoExoPlayerFactory.kt` — production-configured in-process `ExoPlayer` whose surface can be attached to `PlayerView` (fixes the blank-video bug where `MediaController` cannot render a video surface).
  - Phase 2-3: `core/navigation/SowNavGraph.kt` now wires `VideoExoPlayerFactory` -> `Media3PlayerController(player)` (DirectPlayerFacade) and scopes `PlayerViewModel` to the `NavBackStackEntry` via `viewModel(key = jobId)` so it survives configuration changes (seek-only, no autoplay on rotation). New `LaunchedEffect` rebinds media + restores position when a fresh controller is created.
  - Phase 4-5: New `feature/player/LyricsPanel.kt` — inline collapsible webapp-parity panel (lists all chapters, expands only the current chapter's lines, current line highlighted, past dimmed, future muted; tap chapter/line to seek; auto-scrolls to the current chapter). Wired into `PlayerScreen.kt` replacing the redundant inline current-line `Text` and the static chapter jump-list; video height stays fixed (220 dp portrait / 180 dp landscape) and `Modifier.weight(1f)` consumes leftover space without overlapping the video.
  - Phase 6: `PlayerScreen.kt` — immersive fullscreen overlay via `WindowInsetsControllerCompat` (system bars hide, BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE); a full-screen `Box` replaces the inline `Column`. New `core/util/ActivityExt.kt` walks the `ContextWrapper` chain to find the hosting `Activity` defensively. New `BackHandler` exits fullscreen first and does not pop the screen. No lyrics toggle in the overlay — user must exit fullscreen first.
  - Phase 7: `PlayerScreen.kt` pause-on-background via `LifecycleEventObserver` on `ON_STOP` (no background-audio requirement for video-only playback; replaces the v1 `BackgroundHandoffController` entirely).
  - Phase 8: `feature/render/RenderScreen.kt` — `preferredPlaybackArtifact()` is now nullable (Video only when `mp4R2Key != null`); the Render status panel shows an "Audio only" `AssistChip` + Download button for audio-only renders and offers no Play route.
  - Phase 10: New `LyricsPanelTest.kt` (5 Robolectric/AndroidJUnit4 Compose tests), extended `PlayerScreenTest.kt` (lyrics expand, DirectPlayerFacade-backed `player-video-view` rendered, fullscreen overlay + BackHandler back-press exits fullscreen without popping), extended `Media3PlayerControllerTest.kt` (factory returns zero-duration player before media; `DirectPlayerFacade.setMedia(file://...)` succeeds), extended `PlayerViewModelTest.kt` (Video artifact + cached offline artifact calls `setMedia(localUri, true)`; documents that the `artifact` parameter is vestigial after Phase 8).
  - Spec compliance notes: the spec's `RESIZE_MODE_RESIZE` constant does not exist in Media3; `RESIZE_MODE_FIT` is used instead (matching the spec's stated intent — letterbox, never crop). The spec's `viewModel.pause()` reference required adding a public `pause()` method to `PlayerViewModel` (was previously only reachable through `playPause()`).
  - Acceptance: `./gradlew testDebugUnitTest koverXmlReport lintDebug assembleDebug` all green for `delivery/android`.

- Completed the native Android delivery app implementation under `delivery/android`.
- The Android app is a standalone Kotlin/Jetpack Compose Gradle project that uses the existing Next.js webapp JSON APIs for Better Auth sessions, songsets, song search, render jobs, signed URL playback, sharing, settings, and offline artifact downloads.
- Android does not connect directly to PostgreSQL, Cloudflare R2, or AWS SQS; those remain owned by the webapp, render worker, and backing services.
- The Android implementation includes focused JVM/Robolectric coverage for config, API clients, auth/session handling, songset workflows, render polling, playback/share/settings, offline download state, and UI behavior.
- Acceptance validation completed in the Android project with unit tests, Kover coverage, lint, debug assembly, and graphify refresh.
- Added `delivery/android/README.md` with prerequisites, API base URL setup, emulator and physical-device networking notes, Better Auth/local-origin troubleshooting, signed URL playback notes, offline download notes, and release build guidance.
- Addressed follow-up PR #116 review feedback for Android render replacement validation, songset delete rollback pagination totals, and explicit description clearing from the native client; refreshed focused regression tests and graphify output.

- Completed the Consolidated Chromecast Projection v3 work (Cast SDK + AirPlay + Presentation API fallback) from `.dex/plan.md`. Tasks 1-12 are all done:
  - Ambient `.d.ts` for Cast SDK + Presentation API (Task 1); ref-counted Cast SDK loader singleton with unmount safety (Task 2); `useCastTransport` hook + `dispatchCast` with latest-wins seek debounce, extrapolated disconnect-resume, buffering tracking, and `/api/log-client-error` telemetry (Tasks 3, 8).
  - Presentation API split into `usePresentationSender`/`usePresentationReceiver` with a JSON validator (Task 4); controller pages wired Cast + Presentation fallback, dropped the dead `window.message` listener (Task 5); `ControllerPlayer` hardened with buffering chip, diagnostic bottom sheet, tap-to-resume, stale prompt, iPhone AirPlay hint (Task 6); `PrePlayCard` no longer owns Cast/Presentation detection (Task 6b).
  - R2 signed URL expiry raised to 14400s for Cast/share playback via `cast=true` query param and the share-token route, keeping the session/ownership auth path (Task 7).
  - Render worker appends `-movflags +faststart` for Cast-compatible progressive playback, with an `ffprobe` pipeline test asserting `moov` precedes `mdat` (Task 9).
  - Docs rewritten for Default Media Receiver as the only v3 mode, iPhone AirPlay fallback, 4-hour URL policy, pre-service network test, Presentation API dev-only label, faststart requirement, and a 10-point Live-Service Go/No-Go Checklist (Task 10). Acceptance criteria verified in Task 11; Task 12 confirmed docs cover all user-facing changes.

## 2026-06-23

- Completed the ops/delivery/lab repository reorganization from `specs/ops-delivery-lab-reorganization-v2.md`.
- Active backend/admin code now lives under `ops/admin-cli`; shared DB/auth/schema helpers, including the former `stream_of_worship.app.db`, are owned by `stream_of_worship.db.app`.
- Analysis Service moved to `ops/analysis-service`; Web App moved to `delivery/webapp`; Render Worker moved to `delivery/render-worker`.
- Deprecated/experimental code moved under `lab/`: `lab/sow-app`, `lab/legacy-cli-tui`, and `lab/poc-scripts`.
- Root Python project metadata and root `uv.lock` were removed. Python subprojects now own their project metadata and lockfiles.

Canonical commands:

```bash
uv run --project ops/admin-cli --extra admin sow-admin --help
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest ops/admin-cli/tests -v
cd ops/analysis-service && docker compose up -d
pnpm --filter sow-webapp dev
cd delivery/webapp && pnpm dev
cd delivery/render-worker && docker compose up --build
uv run --project lab/sow-app sow-app --help
uv run --project lab/legacy-cli-tui stream-of-worship --help
uv run --project lab/poc-scripts python -c "from stream_of_worship.db.app.read_client import ReadOnlyClient; print('poc db ok')"
```
