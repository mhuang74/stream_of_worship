#!/usr/bin/env bash
set -euo pipefail

FFMPEG_VERSION="${1:?Usage: $0 <ffmpeg_version>}"
R2_BUCKET="${2:-}"
R2_ENDPOINT_URL="${3:-}"
R2_ACCESS_KEY_ID="${4:-}"
R2_SECRET_ACCESS_KEY="${5:-}"
REQUIRE_R2="${6:-false}"

ARCHIVE="/tmp/ffmpeg.tar.xz"
CHECKSUM="/tmp/ffmpeg.tar.xz.sha256"
R2_KEY="build-dependencies/ffmpeg/ffmpeg-release-amd64-static-${FFMPEG_VERSION}.tar.xz"
R2_CHECKSUM_KEY="${R2_KEY}.sha256"

downloaded_from=""

print_expected_r2_keys() {
    echo "Expected R2 objects:"
    echo "  s3://${R2_BUCKET:-<SOW_R2_BUCKET>}/${R2_KEY}"
    echo "  s3://${R2_BUCKET:-<SOW_R2_BUCKET>}/${R2_CHECKSUM_KEY}"
}

if [ -n "${R2_BUCKET}" ] && [ -n "${R2_ENDPOINT_URL}" ] && [ -n "${R2_ACCESS_KEY_ID}" ] && [ -n "${R2_SECRET_ACCESS_KEY}" ]; then
    if python3 -c "
import sys, hashlib, boto3

bucket, endpoint, access_key, secret_key, key, checksum_key, archive, checksum_path = sys.argv[1:]

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
" "${R2_BUCKET}" "${R2_ENDPOINT_URL}" "${R2_ACCESS_KEY_ID}" "${R2_SECRET_ACCESS_KEY}" "${R2_KEY}" "${R2_CHECKSUM_KEY}" "${ARCHIVE}" "${CHECKSUM}"; then
        downloaded_from="r2"
        rm -f "${CHECKSUM}"
    else
        rc=$?
        if [ "$rc" -eq 2 ]; then
            if [ "${REQUIRE_R2}" = "true" ]; then
                echo "ERROR: R2 FFmpeg download is required for this build; refusing johnvansickle fallback."
                print_expected_r2_keys
                exit 1
            fi
            echo "Falling back to johnvansickle.com"
            curl --max-time 60 -fsSL -o "${ARCHIVE}" \
              https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
            downloaded_from="johnvansickle"
        else
            echo "ERROR: R2 download succeeded but verification failed. Aborting build."
            print_expected_r2_keys
            exit 1
        fi
    fi
else
    if [ "${REQUIRE_R2}" = "true" ]; then
        echo "ERROR: R2 credentials not fully provided and R2 FFmpeg download is required."
        print_expected_r2_keys
        exit 1
    fi
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
