#!/usr/bin/env bash
set -euo pipefail

FFMPEG_VERSION="${1:?Usage: $0 <ffmpeg_version>}"
R2_BUCKET="${2:-}"
R2_ENDPOINT_URL="${3:-}"
R2_ACCESS_KEY_ID="${4:-}"
R2_SECRET_ACCESS_KEY="${5:-}"

ARCHIVE="/tmp/ffmpeg.tar.xz"
CHECKSUM="/tmp/ffmpeg.tar.xz.sha256"

downloaded_from=""

if [ -n "${R2_BUCKET}" ] && [ -n "${R2_ENDPOINT_URL}" ] && [ -n "${R2_ACCESS_KEY_ID}" ] && [ -n "${R2_SECRET_ACCESS_KEY}" ]; then
    if python3 -c "
import sys, hashlib, boto3

version, bucket, endpoint, access_key, secret_key, archive, checksum_path = sys.argv[1:]
key = f'build-dependencies/ffmpeg/ffmpeg-release-amd64-static-{version}.tar.xz'
checksum_key = f'{key}.sha256'

s3 = boto3.client('s3',
    endpoint_url=endpoint,
    aws_access_key_id=access_key,
    aws_secret_access_key=secret_key,
    region_name='auto')

try:
    s3.download_file(bucket, key, archive)
    print('Downloaded ffmpeg from R2')
except Exception as e:
    print(f'R2 download failed: {e}', file=sys.stderr)
    sys.exit(2)

try:
    s3.download_file(bucket, checksum_key, checksum_path)
except Exception as e:
    print(f'Checksum not available on R2: {e}', file=sys.stderr)
    sys.exit(1)

# Expected file format (produced by 'sha256sum --tag <file>'):
#     SHA256 (ffmpeg-release-amd64-static-7.0.2.tar.xz) = <64-hex-hash>
# The '--tag' (BSD) form is used so the expected hash can be extracted by
# splitting on '=' regardless of the locally-downloaded filename
# (/tmp/ffmpeg.tar.xz differs from the .sha256's embedded name under
# 'sha256sum -c'). If the staged file on R2 is regenerated without '--tag',
# this verification will fail loudly (build aborts, does NOT silently fall
# back) -- see scripts/upload-ffmpeg-to-r2.sh which enforces '--tag'.
try:
    with open(checksum_path, 'r') as f:
        expected = f.read().split('=')[-1].strip()
    h = hashlib.sha256()
    with open(archive, 'rb') as f:
        while chunk := f.read(8192):
            h.update(chunk)
    actual = h.hexdigest()
    if expected != actual:
        print(f'ERROR: SHA256 mismatch! expected={expected} actual={actual}', file=sys.stderr)
        sys.exit(1)
    print('SHA256 checksum verified')
except Exception as e:
    print(f'ERROR: Checksum verification failed: {e}', file=sys.stderr)
    sys.exit(1)
" "${FFMPEG_VERSION}" "${R2_BUCKET}" "${R2_ENDPOINT_URL}" "${R2_ACCESS_KEY_ID}" "${R2_SECRET_ACCESS_KEY}" "${ARCHIVE}" "${CHECKSUM}"; then
        downloaded_from="r2"
        rm -f "${CHECKSUM}"
    else
        rc=$?
        if [ "$rc" -eq 2 ]; then
            echo "Falling back to johnvansickle.com"
            curl --max-time 60 -fsSL -o "${ARCHIVE}" \
              https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
            downloaded_from="johnvansickle"
        else
            echo "ERROR: R2 download succeeded but verification failed. Aborting build."
            exit 1
        fi
    fi
else
    echo "R2 credentials not fully provided. Falling back to johnvansickle.com"
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
