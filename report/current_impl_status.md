# Current Implementation Status

## 2026-06-25

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
