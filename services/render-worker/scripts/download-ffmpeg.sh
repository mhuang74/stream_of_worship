#!/usr/bin/env bash
set -euo pipefail

FFMPEG_VERSION="${1:?Usage: $0 <ffmpeg_version>}"
R2_BUCKET="${2:?R2_BUCKET required}"
R2_ENDPOINT_URL="${3:?R2_ENDPOINT_URL required}"
R2_ACCESS_KEY_ID="${4:?R2_ACCESS_KEY_ID required}"
R2_SECRET_ACCESS_KEY="${5:?R2_SECRET_ACCESS_KEY required}"

ARCHIVE="/tmp/ffmpeg.tar.xz"
CHECKSUM="/tmp/ffmpeg.tar.xz.sha256"
R2_KEY="build-dependencies/ffmpeg/ffmpeg-release-amd64-static-${FFMPEG_VERSION}.tar.xz"
R2_CHECKSUM_KEY="build-dependencies/ffmpeg/ffmpeg-release-amd64-static-${FFMPEG_VERSION}.tar.xz.sha256"

downloaded_from=""

if python3 -c "
import boto3, sys
s3 = boto3.client('s3',
    endpoint_url='${R2_ENDPOINT_URL}',
    aws_access_key_id='${R2_ACCESS_KEY_ID}',
    aws_secret_access_key='${R2_SECRET_ACCESS_KEY}',
    region_name='auto')
try:
    s3.download_file('${R2_BUCKET}', '${R2_KEY}', '${ARCHIVE}')
    print('Downloaded ffmpeg from R2')
except Exception as e:
    print(f'R2 download failed: {e}', file=sys.stderr)
    sys.exit(1)
"; then
    downloaded_from="r2"

    if python3 -c "
import boto3, sys
s3 = boto3.client('s3',
    endpoint_url='${R2_ENDPOINT_URL}',
    aws_access_key_id='${R2_ACCESS_KEY_ID}',
    aws_secret_access_key='${R2_SECRET_ACCESS_KEY}',
    region_name='auto')
try:
    s3.download_file('${R2_BUCKET}', '${R2_CHECKSUM_KEY}', '${CHECKSUM}')
except Exception as e:
    print(f'Checksum not available: {e}', file=sys.stderr)
    sys.exit(1)
"; then
        expected=$(awk -F'= ' '{print $2}' "${CHECKSUM}" | tr -d '[:space:]')
        actual=$(sha256sum "${ARCHIVE}" | awk '{print $1}')
        if [ "$expected" != "$actual" ]; then
            echo "ERROR: SHA256 mismatch! expected=$expected actual=$actual"
            exit 1
        fi
        echo "SHA256 checksum verified"
        rm -f "${CHECKSUM}"
    fi
else
    echo "Falling back to johnvansickle.com"
    curl --max-time 60 -fsSL -o "${ARCHIVE}" \
      https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
    downloaded_from="johnvansickle"
fi

tar -xJ --strip-components=1 -C /usr/local/bin \
  --wildcards "ffmpeg-*-amd64-static/ffmpeg" "ffmpeg-*-amd64-static/ffprobe" \
  -f "${ARCHIVE}"
rm -f "${ARCHIVE}"
chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe
ffmpeg -version && ffprobe -version
echo "FFmpeg installed successfully (source: ${downloaded_from})"
