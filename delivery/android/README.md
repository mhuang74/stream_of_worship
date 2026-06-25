# Stream of Worship Android App

Native Android client for the Stream of Worship delivery workflows. The app uses
the existing Next.js webapp as its JSON API backend; it does not connect directly
to PostgreSQL, Cloudflare R2, or AWS SQS.

## Features

- Better Auth email/password login, registration, session restore, and sign-out
  through the webapp auth APIs.
- Songset list/detail workflows with create, duplicate, delete, song search,
  add/remove/reorder, description editing, and transition parameter editing.
- Render submission and status polling for audio and video jobs, including
  completed artifact size and availability states.
- Media3 playback for rendered MP4/MP3 artifacts with signed URL refresh,
  chapter and lyric navigation, fullscreen video, media controls, and wake-lock
  handling during playback.
- Share-token creation, Android share/view intents, user settings editing, and
  offline artifact downloads tracked in app-private metadata.

## Prerequisites

- Android Studio with JDK 17 support
- Android SDK 35 and an emulator or physical device running Android 8.0+ (API 26+)
- The webapp dependencies installed with `pnpm install`
- A reachable Stream of Worship webapp instance with database, R2, and render queue
  settings configured for the workflows you want to test

For local backend setup, see [delivery/webapp/README.md](../webapp/README.md).

## API Base URL

The Android app reads its API base URL from Gradle properties in
`delivery/android/gradle.properties`:

```properties
sow.apiBaseUrl.debug=http://10.0.2.2:8080
sow.apiBaseUrl.staging=https://staging.streamofworship.local
sow.apiBaseUrl.release=https://app.streamofworship.local
```

Override these per command when needed:

```bash
./gradlew assembleDebug -Psow.apiBaseUrl.debug=http://10.0.2.2:8080
./gradlew assembleStaging -Psow.apiBaseUrl.staging=https://staging.example.com
./gradlew assembleRelease -Psow.apiBaseUrl.release=https://app.example.com
```

The value must include `http://` or `https://`. Trailing slashes are normalized
by the app.

## GitHub Actions APK

The `Android App Build` workflow creates an installable debug APK and uploads it
as the `sow-android-debug-apk` artifact.

Before running the workflow, set the repository variable
`SOW_ANDROID_API_BASE_URL` to the deployed webapp origin Android should call.
Use the same origin as `NEXT_PUBLIC_BASE_URL`, for example:

```text
https://your-app.vercel.app
```

To install a workflow-built APK:

1. Run the `Android App Build` workflow from GitHub Actions, or push a change
   under `delivery/android/`.
2. Open the completed workflow run and download the `sow-android-debug-apk`
   artifact.
3. Unzip the artifact.
4. Open `app-debug.apk` on the Android device and allow Android's "install
   unknown apps" prompt for the app used to open it.

## Local Webapp Networking

Start the webapp on all interfaces:

```bash
pnpm --filter sow-webapp dev
```

The webapp dev script listens on `0.0.0.0:8080`.

- Android emulator to local webapp: keep the default
  `sow.apiBaseUrl.debug=http://10.0.2.2:8080`.
- Physical Android device to local webapp: use your computer's LAN IP, for
  example `-Psow.apiBaseUrl.debug=http://192.168.1.25:8080`.
- HTTPS tunnel or staging backend: point the matching Gradle property at the
  tunnel or staging URL.

For local auth, set the webapp `BETTER_AUTH_URL` to the externally reachable
webapp origin that matches the URL Android is using where possible. The webapp
also allows private-network request origins from `192.168.*`, `10.*`, and
`172.16.*` through `delivery/webapp/src/lib/auth.ts`.

## Build and Test

Run commands from `delivery/android`:

```bash
./gradlew testDebugUnitTest
./gradlew koverXmlReport
./gradlew lintDebug
./gradlew assembleDebug
```

The acceptance pass for the Android implementation used:

```bash
./gradlew testDebugUnitTest koverXmlReport
./gradlew lintDebug
./gradlew assembleDebug
```

## Release Build Notes

- Set `sow.apiBaseUrl.release` to the production webapp origin before building.
- `release` currently uses the default Android Gradle release build type with
  minification disabled. Add a signing configuration before distributing outside
  local testing.
- Keep the release API URL on HTTPS. Better Auth uses secure cookies in
  production, and signed media URLs should not be sent over plain HTTP.
- Validate login, songset editing, render submission, signed URL playback, share,
  and offline download flows against the target backend before publishing.

## Troubleshooting

### Better Auth Cookies

- Android stores Better Auth cookies through the app's OkHttp cookie jar and
  clears them on sign-out or a 401 response.
- If login succeeds but the next API call is unauthenticated, verify that Android
  is calling the same host and scheme for every request. Cookies minted for one
  host are not sent to another host.
- For production-style HTTPS backends, make sure the server `BETTER_AUTH_URL`
  matches the public origin and that the device clock is correct.

### Local Network Trusted Origins

- The webapp's Better Auth config trusts private LAN origins matching
  `192.168.*`, `10.*`, and `172.16.*`.
- If auth requests fail only from a tunnel, custom domain, or non-private
  hostname, add that origin to the webapp trusted origins configuration before
  testing Android against it.
- For a physical device, both the phone and the development machine must be on
  the same network, and the OS firewall must allow inbound traffic to port 8080.

### Signed URL Playback

- Rendered audio/video playback uses `/api/signed-url` for files owned by the
  authenticated user, then Media3 streams the returned URL.
- A 403 or 404 usually means the render job does not belong to the signed-in
  user, the artifact is not present in R2, or the signed URL expired before
  playback started. Refresh the render/job screen and retry playback to mint a
  fresh URL.
- Source recording previews require published recordings with a valid
  `hashPrefix`; draft recordings are intentionally unavailable to app users.

### Offline Downloads

- Completed render artifacts are tracked in app-private offline metadata and
  downloaded with the Android download scheduler.
- If a download remains queued, confirm that the device has network access and
  that battery/data saver settings are not blocking background work.
- If cached playback is unavailable, reopen the render or share screen to refresh
  artifact metadata and signed URLs, then prepare the download again.
