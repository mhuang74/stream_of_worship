#!/usr/bin/env bash
set -euo pipefail

FFMPEG_VERSION="${1:?Usage: $0 <ffmpeg_version>}"
JOHNVANSICKLE_BASE_URL="https://johnvansickle.com/ffmpeg/releases"
R2_PREFIX="build-dependencies/ffmpeg"

ARCHIVE="ffmpeg-release-amd64-static-${FFMPEG_VERSION}.tar.xz"
CHECKSUM="${ARCHIVE}.sha256"

WORK_DIR=$(mktemp -d)
trap 'rm -rf "${WORK_DIR}"' EXIT

echo "=== Upload FFmpeg ${FFMPEG_VERSION} to R2 ==="

echo "[1/4] Downloading from johnvansickle.com..."
curl -fsSL -o "${WORK_DIR}/${ARCHIVE}" \
  "${JOHNVANSICKLE_BASE_URL}/ffmpeg-release-amd64-static.tar.xz"
echo "  Downloaded: ${ARCHIVE}"

echo "[2/4] Computing SHA256 checksum (--tag format)..."
sha256sum --tag "${WORK_DIR}/${ARCHIVE}" > "${WORK_DIR}/${CHECKSUM}"
echo "  Checksum: $(cat "${WORK_DIR}/${CHECKSUM}")"

echo "[3/4] Uploading to R2..."
AWS_ACCESS_KEY_ID="${SOW_R2_ACCESS_KEY_ID:?SOW_R2_ACCESS_KEY_ID not set}" \
AWS_SECRET_ACCESS_KEY="${SOW_R2_SECRET_ACCESS_KEY:?SOW_R2_SECRET_ACCESS_KEY not set}" \
AWS_DEFAULT_REGION=auto \
aws s3 cp "${WORK_DIR}/${ARCHIVE}" \
  "s3://${SOW_R2_BUCKET:?SOW_R2_BUCKET not set}/${R2_PREFIX}/" \
  --endpoint-url="${SOW_R2_ENDPOINT_URL:?SOW_R2_ENDPOINT_URL not set}"

AWS_ACCESS_KEY_ID="${SOW_R2_ACCESS_KEY_ID}" \
AWS_SECRET_ACCESS_KEY="${SOW_R2_SECRET_ACCESS_KEY}" \
AWS_DEFAULT_REGION=auto \
aws s3 cp "${WORK_DIR}/${CHECKSUM}" \
  "s3://${SOW_R2_BUCKET}/${R2_PREFIX}/" \
  --endpoint-url="${SOW_R2_ENDPOINT_URL}"

echo "[4/5] Verifying upload..."
AWS_ACCESS_KEY_ID="${SOW_R2_ACCESS_KEY_ID}" \
AWS_SECRET_ACCESS_KEY="${SOW_R2_SECRET_ACCESS_KEY}" \
AWS_DEFAULT_REGION=auto \
aws s3 ls "s3://${SOW_R2_BUCKET}/${R2_PREFIX}/" \
  --endpoint-url="${SOW_R2_ENDPOINT_URL}"

echo ""
echo "=== Done! ==="
echo "R2 location: s3://${SOW_R2_BUCKET}/${R2_PREFIX}/"
echo "Files uploaded:"
echo "  - ${ARCHIVE}"
echo "  - ${CHECKSUM}"
echo ""
echo "To use in Docker build, pass R2 credentials as build args:"
echo "  docker build \\"
echo "    --build-arg R2_BUCKET=\$SOW_R2_BUCKET \\"
echo "    --build-arg R2_ENDPOINT_URL=\$SOW_R2_ENDPOINT_URL \\"
echo "    --build-arg R2_ACCESS_KEY_ID=\$SOW_R2_ACCESS_KEY_ID \\"
echo "    --build-arg R2_SECRET_ACCESS_KEY=\$SOW_R2_SECRET_ACCESS_KEY \\"
echo "    -t sow-render-worker services/render-worker/"
