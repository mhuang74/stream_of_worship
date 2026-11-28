#!/usr/bin/env bash
# Mirror the pinned FFmpeg static build into this repo's releases so CI never
# depends on johnvansickle.com staying reachable.
set -euo pipefail

VERSION="${1:?Usage: $0 <ffmpeg_version>}"
RELEASE_TAG="${FFMPEG_RELEASE_TAG:-ffmpeg-static}"
SLUG="${FFMPEG_RELEASE_SLUG:-mhuang74/stream_of_worship}"
UPSTREAM="https://johnvansickle.com/ffmpeg/releases"
ASSET="ffmpeg-${VERSION}-amd64-static.tar.xz"

work=$(mktemp -d)
trap 'rm -rf "${work}"' EXIT

echo "[1/3] Downloading ${ASSET} from ${UPSTREAM}..."
curl --connect-timeout 15 --max-time 300 --retry 2 -fsSL -o "${work}/${ASSET}" "${UPSTREAM}/${ASSET}"

echo "[2/3] SHA256: $(sha256sum "${work}/${ASSET}" | awk '{print $1}')"

if gh release view "${RELEASE_TAG}" --repo "${SLUG}" >/dev/null 2>&1; then
    gh release upload "${RELEASE_TAG}" "${work}/${ASSET}" --repo "${SLUG}" --clobber
else
    gh release create "${RELEASE_TAG}" "${work}/${ASSET}" --repo "${SLUG}" --latest=false \
        --title "Pinned FFmpeg static builds" \
        --notes "Immutable FFmpeg static builds consumed by CI (delivery/render-worker/scripts/install-ffmpeg-ci.sh). One asset per pinned version; never delete an asset a workflow pins."
fi

echo "[3/3] Published: https://github.com/${SLUG}/releases/download/${RELEASE_TAG}/${ASSET}"
