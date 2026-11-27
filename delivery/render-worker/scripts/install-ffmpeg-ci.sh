#!/usr/bin/env bash
# Install the pinned FFmpeg static build for CI. Sources, in order:
#   1. this repo's release asset (same origin as actions/checkout, no expiry)
#   2. the upstream johnvansickle.com release
# The checksum is the integrity anchor: a mismatch aborts instead of installing.
set -euo pipefail

VERSION="${1:?Usage: $0 <ffmpeg_version> <sha256> [dest_dir]}"
SHA256="${2:?Usage: $0 <ffmpeg_version> <sha256> [dest_dir]}"
DEST="${3:-/usr/local/bin}"
SLUG="${FFMPEG_RELEASE_SLUG:-mhuang74/stream_of_worship}"
RELEASE_TAG="${FFMPEG_RELEASE_TAG:-ffmpeg-static}"
ASSET="ffmpeg-${VERSION}-amd64-static.tar.xz"
SOURCES=(
    "https://github.com/${SLUG}/releases/download/${RELEASE_TAG}/${ASSET}"
    "https://johnvansickle.com/ffmpeg/releases/${ASSET}"
)

work=$(mktemp -d)
trap 'rm -rf "${work}"' EXIT

archive=""
for url in "${SOURCES[@]}"; do
    echo "Trying ${url}"
    if curl --connect-timeout 10 --max-time 180 --retry 2 --retry-max-time 240 \
            -fsSL -o "${work}/${ASSET}" "${url}" &&
        printf '%s  %s\n' "${SHA256}" "${work}/${ASSET}" | sha256sum --check --status; then
        archive="${work}/${ASSET}"
        echo "Verified ${ASSET} from ${url}"
        break
    fi
    echo "::warning::FFmpeg source failed or did not match the pinned checksum: ${url}"
done
if [ -z "${archive}" ]; then
    echo "::error::Could not obtain an archive matching the pinned FFmpeg ${VERSION} checksum from any source."
    exit 1
fi

mkdir -p "${work}/bin"
tar -xJ --strip-components=1 -C "${work}/bin" \
    --wildcards "ffmpeg-${VERSION}-amd64-static/ffmpeg" "ffmpeg-${VERSION}-amd64-static/ffprobe" \
    -f "${archive}"
chmod +x "${work}/bin/ffmpeg" "${work}/bin/ffprobe"
# No pipeline here: `... | head -1 | grep -q` under `pipefail` would fail on the
# SIGPIPE ffmpeg gets once grep exits.
"${work}/bin/ffmpeg" -version > "${work}/version.txt"
grep -q "ffmpeg version ${VERSION}-static" "${work}/version.txt" || {
    echo "::error::Installed ffmpeg does not report version ${VERSION}-static."
    exit 1
}

mkdir -p "${DEST}"
if [ -w "${DEST}" ]; then
    cp "${work}/bin/"* "${DEST}/"
else
    sudo cp "${work}/bin/"* "${DEST}/"
fi
# Print the version of the copy just installed, not whatever PATH happens to hit.
"${DEST}/ffmpeg" -version && "${DEST}/ffprobe" -version
