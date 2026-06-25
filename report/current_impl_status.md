# Current Implementation Status

## 2026-06-25

- Completed the native Android delivery app implementation under `delivery/android`.
- The Android app is a standalone Kotlin/Jetpack Compose Gradle project that uses the existing Next.js webapp JSON APIs for Better Auth sessions, songsets, song search, render jobs, signed URL playback, sharing, settings, and offline artifact downloads.
- Android does not connect directly to PostgreSQL, Cloudflare R2, or AWS SQS; those remain owned by the webapp, render worker, and backing services.
- The Android implementation includes focused JVM/Robolectric coverage for config, API clients, auth/session handling, songset workflows, render polling, playback/share/settings, offline download state, and UI behavior.
- Acceptance validation completed in the Android project with unit tests, Kover coverage, lint, debug assembly, and graphify refresh.
- Added `delivery/android/README.md` with prerequisites, API base URL setup, emulator and physical-device networking notes, Better Auth/local-origin troubleshooting, signed URL playback notes, offline download notes, and release build guidance.

Canonical Android commands:

```bash
cd delivery/android && ./gradlew testDebugUnitTest koverXmlReport
cd delivery/android && ./gradlew lintDebug
cd delivery/android && ./gradlew assembleDebug
cd delivery/android && ./gradlew assembleRelease -Psow.apiBaseUrl.release=https://app.example.com
```

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
