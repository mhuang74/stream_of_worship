#!/usr/bin/env bash
# Build the Android debug APK and install it on a connected device (default: Pixel 6).
#
# Usage:
#   scripts/deploy_debug.sh                 # auto-detect first connected device
#   scripts/deploy_debug.sh <serial>        # target a specific device serial
#   scripts/deploy_debug.sh --run           # also launch the app's main activity after install
#   scripts/deploy_debug.sh --reinstall     # uninstall first (clears app data), then install
#   scripts/deploy_debug.sh --no-tests      # skip testDebugUnitTest during build
#   USER_ID=10 scripts/deploy_debug.sh      # override the target Android user/profile (default 10)
#   ADB=/path/to/adb scripts/deploy_debug.sh
#
# The debug build's API base URL is taken from SOW_ANDROID_API_BASE_URL (same env
# var the GitHub Actions workflow uses). It defaults to the deployed webapp:
#   https://stream-of-worship-webapp.vercel.app
# Override per-invocation if you need to target a local backend, e.g.
#   SOW_ANDROID_API_BASE_URL=http://10.0.2.2:8080 scripts/deploy_debug.sh
DEFAULT_API_BASE_URL="https://stream-of-worship-webapp.vercel.app"
#
# Exit codes: 0 = success, non-zero = failure at the corresponding step.
set -euo pipefail

# Resolve repository paths regardless of where the script is invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANDROID_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# Locate adb: honor $ADB, else /opt/platform-tools/adb, else PATH.
ADB="${ADB:-}"
if [[ -z "$ADB" ]]; then
  if [[ -x "/opt/platform-tools/adb" ]]; then
    ADB="/opt/platform-tools/adb"
  else
    ADB="$(command -v adb)" || {
      echo "ERROR: adb not found. Set ADB=/path/to/adb or add it to PATH." >&2
      exit 1
    }
  fi
fi

# Defaults that may be overridden by flags.
DEVICE_SERIAL=""
LAUNCH_AFTER=false
REINSTALL=false
GRADLE_FLAGS=()

usage() {
  sed -n '2,12p' "$0"
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --run) LAUNCH_AFTER=true; shift ;;
    --reinstall) REINSTALL=true; shift ;;
    --no-tests) GRADLE_FLAGS+=(--exclude-task testDebugUnitTest); shift ;;
    -*) echo "Unknown flag: $1" >&2; exit 2 ;;
    *) DEVICE_SERIAL="$1"; shift ;;
  esac
done

debug_id() {
  if [[ -n "$DEVICE_SERIAL" ]]; then
    echo "$DEVICE_SERIAL"
  else
    "$ADB" devices -l | awk '/device usb:/ {print $1; exit}'
  fi
}

echo "==> Locating connected device"
SERIAL="$(debug_id)"
if [[ -z "$SERIAL" ]]; then
  echo "ERROR: No device found via $ADB. Run 'adb devices' to verify USB debugging." >&2
  exit 3
fi
echo "    Device serial: $SERIAL"
adevice() { "$ADB" -s "$SERIAL" "$@"; }

echo "==> Building debug APK (./gradlew assembleDebug)"
# The debug build variant reads `sow.apiBaseUrl.debug` (see app/build.gradle.kts
# :27). We pass it explicitly so the APK always targets the configured webapp
# origin, regardless of whatever is pinned in gradle.properties.
API_BASE_URL="${SOW_ANDROID_API_BASE_URL:-$DEFAULT_API_BASE_URL}"
case "$API_BASE_URL" in
  http://*|https://*) ;;
  *)
    echo "ERROR: SOW_ANDROID_API_BASE_URL must start with http:// or https:// (got: $API_BASE_URL)" >&2
    exit 6
    ;;
esac
echo "    API base URL: $API_BASE_URL"
(
  cd "$ANDROID_DIR"
  ./gradlew assembleDebug "${GRADLE_FLAGS[@]}" "-Psow.apiBaseUrl.debug=$API_BASE_URL"
)

APK="$ANDROID_DIR/app/build/outputs/apk/debug/app-debug.apk"
if [[ ! -f "$APK" ]]; then
  echo "ERROR: Built APK not found at expected path: $APK" >&2
  exit 4
fi
echo "    APK: $APK ($(du -h "$APK" | cut -f1))"

# Android multi-user mode distinguishes isolated test profiles from the primary
# user (user 0). Always target USER_ID so installs land in the test profile and
# never accidentally overwrite or leak into the primary user's app state.
USER_ID="${USER_ID:-10}"

if $REINSTALL; then
  echo "==> Uninstalling org.streamofworship.android.debug from user $USER_ID (clearing app data)"
  adevice uninstall --user "$USER_ID" org.streamofworship.android.debug >/dev/null 2>&1 || true
fi

echo "==> Installing on device ($SERIAL) for user $USER_ID"
if ! adevice install --user "$USER_ID" -r "$APK"; then
  echo "ERROR: install for user $USER_ID failed." >&2
  echo "       Verify the user exists: $ADB -s $SERIAL shell pm list users" >&2
  echo "       Try $0 --reinstall to clear stale data, or override with USER_ID=<n>." >&2
  exit 5
fi

if $LAUNCH_AFTER; then
  echo "==> Launching app (org.streamofworship.android.debug) as user $USER_ID"
  # `--user <id>` + monkey launches the registered LAUNCHER activity in the
  # target work profile without hard-coding the activity component name.
  adevice shell --user "$USER_ID" monkey -p org.streamofworship.android.debug \
    -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1 || true
fi

echo "==> Done"
