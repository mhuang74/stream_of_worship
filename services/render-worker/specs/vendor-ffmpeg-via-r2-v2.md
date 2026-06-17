# Spec v2: Vendor johnvansickle FFmpeg Binary via R2

## Changes from v1

| Area | v1 | v2 | Rationale |
|------|----|----|-----------|
| S3 download tool | `awscli` via yum | `python3 + boto3` | Avoids ~120MB image bloat; boto3 is already a runtime dependency |
| Multi-stage build | Not discussed | No (keep simple) | tar/xz overhead (~5MB) is acceptable; complexity not worth it |
| Fallback timeout | None | `curl --max-time 60` | Prevents hung fallback from blocking CI |
| SHA256 verification | `sha256sum -c` (broken) | `sha256sum --tag` + explicit comparison | Fixes filename mismatch when downloaded file is `/tmp/ffmpeg.tar.xz` |
| Dockerfile duplication | Full RUN block in both Dockerfiles | Shared `scripts/download-ffmpeg.sh` | Single source of truth, reduces drift |
| Credential passing | BuildKit secrets | Build args (env vars via aws-actions) | Simpler CI; acceptable for private ECR |
| R2 error visibility | `2>/dev/null` | Log errors before fallback | Makes R2 auth failures visible in CI logs |

---

## Problem

The render worker's `Dockerfile` downloads FFmpeg at build time from `johnvansickle.com`. This external network dependency is flaky in CI and has already required two hot-fix commits:

| Commit | Fix |
|--------|-----|
| `9527f7c` | Add `User-Agent` header to ffmpeg download |
| `098965d` | Use `python3` instead of `curl` for download |

The GitHub Actions `deploy-render-worker` job fails intermittently with build-time download errors (timeouts, 403s, connection resets). Each failure blocks deployment and requires manual retry.

---

## Solution Overview

Download the johnvansickle release build once, store it in Cloudflare R2 (the project's existing S3-compatible storage), and fetch it from R2 during Docker builds using `python3 + boto3`. R2 is reliable, has no egress fees, and is already authenticated in CI.

This approach:
- Eliminates the flaky `johnvansickle.com` build-time dependency as the primary source
- Uses the exact same binary that currently works in production (zero runtime risk)
- Leverages existing infrastructure (R2 bucket, credentials, boto3)
- Avoids adding `awscli` to the image (~120MB savings vs v1 spec)
- Keeps `tar` and `xz` in the image (still needed to extract the `.tar.xz`)
- Retains johnvansickle.com as a timed fallback for resilience

---

## Binary Storage

### Location

Store the binary in the existing R2 bucket under a dedicated prefix:

```
s3://<SOW_R2_BUCKET>/build-dependencies/ffmpeg/
```

### Naming Convention

```
ffmpeg-release-amd64-static-7.0.2.tar.xz
ffmpeg-release-amd64-static-7.0.2.tar.xz.sha256
```

The `.sha256` file uses `sha256sum --tag` format (BSD-style) so verification works regardless of the downloaded filename.

### One-Time Upload Process

```bash
FFMPEG_VERSION=7.0.2

# Download from johnvansickle (one time)
curl -fsSL -o ffmpeg-release-amd64-static-${FFMPEG_VERSION}.tar.xz \
  https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz

# Compute SHA256 checksum (BSD --tag format for portability)
sha256sum --tag ffmpeg-release-amd64-static-${FFMPEG_VERSION}.tar.xz \
  > ffmpeg-release-amd64-static-${FFMPEG_VERSION}.tar.xz.sha256

# Upload both files to R2
aws s3 cp ffmpeg-release-amd64-static-${FFMPEG_VERSION}.tar.xz \
  s3://$SOW_R2_BUCKET/build-dependencies/ffmpeg/ \
  --endpoint-url=$SOW_R2_ENDPOINT_URL

aws s3 cp ffmpeg-release-amd64-static-${FFMPEG_VERSION}.tar.xz.sha256 \
  s3://$SOW_R2_BUCKET/build-dependencies/ffmpeg/ \
  --endpoint-url=$SOW_R2_ENDPOINT_URL
```

---

## Shared Download Script

### `scripts/download-ffmpeg.sh`

Both `Dockerfile` and `Dockerfile.dev` use this script to avoid duplication:

```bash
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

# Try R2 first using python3 + boto3
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

    # Verify checksum if available on R2
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
        # --tag format: SHA256 (filename) = <hash>
        # Extract hash and compare
        expected=$(awk -F'= ' '{print $2}' "${CHECKSUM}" | tr -d '[:space:]]')
        actual=$(sha256sum "${ARCHIVE}" | awk '{print $1}')
        if [ "$expected" != "$actual" ]; then
            echo "ERROR: SHA256 mismatch! expected=$expected actual=$actual"
            exit 1
        fi
        echo "SHA256 checksum verified"
        rm -f "${CHECKSUM}"
    fi
else
    # Fallback to johnvansickle.com with timeout
    echo "Falling back to johnvansickle.com"
    curl --max-time 60 -fsSL -o "${ARCHIVE}" \
      https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
    downloaded_from="johnvansickle"
fi

# Extract binaries
tar -xJ --strip-components=1 -C /usr/local/bin \
  --wildcards "ffmpeg-*-amd64-static/ffmpeg" "ffmpeg-*-amd64-static/ffprobe" \
  -f "${ARCHIVE}"
rm -f "${ARCHIVE}"
chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe
ffmpeg -version && ffprobe -version
echo "FFmpeg installed successfully (source: ${downloaded_from})"
```

---

## Dockerfile Changes

### `Dockerfile` (Production)

```dockerfile
FROM public.ecr.aws/lambda/python:3.11

RUN yum install -y google-noto-sans-cjk-fonts tar xz \
    && yum clean all \
    && rm -rf /var/cache/yum

COPY fonts/ /usr/share/fonts/truetype/vendor/

# Install boto3 early for R2 download (also installed later via requirements.txt)
RUN pip install --no-cache-dir boto3

ARG FFMPEG_VERSION=7.0.2
ARG R2_BUCKET
ARG R2_ENDPOINT_URL
ARG R2_ACCESS_KEY_ID
ARG R2_SECRET_ACCESS_KEY

# Download ffmpeg from R2 with fallback to johnvansickle.com
COPY scripts/download-ffmpeg.sh /tmp/download-ffmpeg.sh
RUN chmod +x /tmp/download-ffmpeg.sh \
    && /tmp/download-ffmpeg.sh "${FFMPEG_VERSION}" "${R2_BUCKET}" "${R2_ENDPOINT_URL}" "${R2_ACCESS_KEY_ID}" "${R2_SECRET_ACCESS_KEY}" \
    && rm -f /tmp/download-ffmpeg.sh

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY src/sow_render_worker/ ${LAMBDA_TASK_ROOT}/sow_render_worker/

CMD ["sow_render_worker.lambda_handler.handler"]
```

### `Dockerfile.dev` (Local Development)

```dockerfile
FROM public.ecr.aws/lambda/python:3.11

RUN yum install -y google-noto-sans-cjk-fonts tar xz \
    && yum clean all \
    && rm -rf /var/cache/yum

COPY fonts/ /usr/share/fonts/truetype/vendor/

# Install boto3 early for R2 download (also installed later via requirements.txt)
RUN pip install --no-cache-dir boto3

ARG FFMPEG_VERSION=7.0.2
ARG R2_BUCKET
ARG R2_ENDPOINT_URL
ARG R2_ACCESS_KEY_ID
ARG R2_SECRET_ACCESS_KEY

# Download ffmpeg from R2 with fallback to johnvansickle.com
COPY scripts/download-ffmpeg.sh /tmp/download-ffmpeg.sh
RUN chmod +x /tmp/download-ffmpeg.sh \
    && /tmp/download-ffmpeg.sh "${FFMPEG_VERSION}" "${R2_BUCKET}" "${R2_ENDPOINT_URL}" "${R2_ACCESS_KEY_ID}" "${R2_SECRET_ACCESS_KEY}" \
    && rm -f /tmp/download-ffmpeg.sh

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

CMD ["sow_render_worker.lambda_handler.handler"]
```

### Key Points

- `tar` and `xz` are still required (to extract the `.tar.xz`), so they remain in `yum install`
- `awscli` is **not** installed — `python3 + boto3` handles the R2 download instead, saving ~120MB
- `boto3` is installed early via `pip install` before the ffmpeg download, then again via `requirements.txt` (pip skips already-installed packages)
- The binary itself is identical to the current one when fetched from R2 — zero runtime risk
- R2 credentials are passed as build args (visible in `docker history` but acceptable for private ECR)
- `FFMPEG_VERSION` is parameterized as an `ARG` to make future updates a single-line change
- johnvansickle.com is retained as a timed fallback (`--max-time 60`) if R2 is unavailable
- R2 download errors are logged (not silenced) so CI failures are diagnosible
- SHA256 verification uses `--tag` format to avoid filename mismatch issues

---

## CI/CD Workflow Updates

### `.github/workflows/deploy.yml`

Update the `deploy-render-worker` job to pass R2 credentials as build args:

```yaml
- name: Build, tag, and push image to Amazon ECR
  id: build-image
  env:
    ECR_REGISTRY: ${{ steps.login-ecr.outputs.registry }}
    ECR_REPOSITORY: sow-render-worker
    IMAGE_TAG: ${{ github.sha }}
  run: |
    docker build \
      --build-arg R2_BUCKET=${{ secrets.SOW_R2_BUCKET }} \
      --build-arg R2_ENDPOINT_URL=${{ secrets.SOW_R2_ENDPOINT_URL }} \
      --build-arg R2_ACCESS_KEY_ID=${{ secrets.SOW_R2_ACCESS_KEY_ID }} \
      --build-arg R2_SECRET_ACCESS_KEY=${{ secrets.SOW_R2_SECRET_ACCESS_KEY }} \
      -t $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG .
    docker push $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG
    echo "image=$ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG" >> $GITHUB_OUTPUT
```

### Required Secrets

The following secrets must be configured in GitHub (they likely already exist for the render worker):
- `SOW_R2_BUCKET`
- `SOW_R2_ENDPOINT_URL`
- `SOW_R2_ACCESS_KEY_ID`
- `SOW_R2_SECRET_ACCESS_KEY`

---

## Local Development Workflow

### Docker Compose

Update `docker-compose.yml` to pass R2 build args:

```yaml
services:
  render-worker:
    build:
      context: .
      dockerfile: Dockerfile
      args:
        R2_BUCKET: ${SOW_R2_BUCKET}
        R2_ENDPOINT_URL: ${SOW_R2_ENDPOINT_URL}
        R2_ACCESS_KEY_ID: ${SOW_R2_ACCESS_KEY_ID}
        R2_SECRET_ACCESS_KEY: ${SOW_R2_SECRET_ACCESS_KEY}
    ports:
      - "9000:8080"
    environment:
      AWS_LAMBDA_FUNCTION_TIMEOUT: "900"
      AWS_LAMBDA_FUNCTION_MEMORY_SIZE: "3072"
      SOW_DATABASE_URL: ${SOW_DATABASE_URL}
      SOW_R2_BUCKET: ${SOW_R2_BUCKET}
      SOW_R2_ENDPOINT_URL: ${SOW_R2_ENDPOINT_URL}
      SOW_R2_ACCESS_KEY_ID: ${SOW_R2_ACCESS_KEY_ID}
      SOW_R2_SECRET_ACCESS_KEY: ${SOW_R2_SECRET_ACCESS_KEY}
      SOW_AWS_REGION: ${SOW_AWS_REGION:-us-west-2}
      SOW_SQS_QUEUE_URL: ${SOW_SQS_QUEUE_URL}
      SOW_FRAME_CACHE_ENABLED: ${SOW_FRAME_CACHE_ENABLED:-true}
      SOW_FADE_ALPHA_STEPS: ${SOW_FADE_ALPHA_STEPS:-16}
      SOW_MAX_CACHE_ENTRIES: ${SOW_MAX_CACHE_ENTRIES:-}
```

### Build Command

```bash
docker compose up --build
```

No BuildKit secrets or special setup needed — R2 credentials come from the `.env` file via docker compose variable substitution.

---

## Testing Strategy

### Phase 1: Binary Validation

1. Upload the binary and checksum to R2
2. Verify `aws s3 cp` works from local machine
3. Build `Dockerfile` locally:
   ```bash
   docker build \
     --build-arg R2_BUCKET=$SOW_R2_BUCKET \
     --build-arg R2_ENDPOINT_URL=$SOW_R2_ENDPOINT_URL \
     --build-arg R2_ACCESS_KEY_ID=$SOW_R2_ACCESS_KEY_ID \
     --build-arg R2_SECRET_ACCESS_KEY=$SOW_R2_SECRET_ACCESS_KEY \
     -t sow-render-worker-test .
   ```
4. Run the existing feature checklist:
   ```bash
   docker run --rm --entrypoint "" sow-render-worker-test ffmpeg -version
   docker run --rm --entrypoint "" sow-render-worker-test ffmpeg -encoders | grep -E "libx264|libmp3lame|aac"
   docker run --rm --entrypoint "" sow-render-worker-test ffmpeg -filters | grep -E "loudnorm|amix|afade|adelay|asetpts|color"
   docker run --rm --entrypoint "" sow-render-worker-test ffprobe -version
   ```

### Phase 2: Integration Validation

1. Run `PYTHONPATH=src pytest tests/ -v`
2. Run a functional smoke test (encode 1-second test video)

### Phase 3: CI Validation

1. Push branch to trigger `deploy-render-worker` job
2. Verify build succeeds and primarily uses R2 (check logs for "Downloaded ffmpeg from R2")
3. Verify Lambda update succeeds

---

## Rollback Plan

If the deployed Lambda fails:

1. **Immediate**: Re-deploy the previous ECR image tagged with the prior Git SHA:
   ```bash
   aws lambda update-function-code \
     --function-name sow-render-worker \
     --image-uri $ECR_REGISTRY/sow-render-worker:<previous-sha>
   ```

2. **Git**: Revert the commit and re-run the deploy workflow

3. **Root cause**: Since the binary is identical to the current working one when fetched from R2, runtime failures are extremely unlikely. Any issues would be in the build process (R2 auth, boto3 availability, `tar`/`xz` presence, build arg passing).

---

## Documentation Updates

### `services/render-worker/README.md`

- Remove references to `johnvansickle.com` as the primary download source
- Document that ffmpeg is sourced from R2 (`build-dependencies/ffmpeg/`) with johnvansickle.com as timed fallback
- Document the shared `scripts/download-ffmpeg.sh` script
- Update the "Prerequisites" section to note:
  - R2 credentials are required for Docker builds (passed as build args)
  - A `.env` file with R2 credentials is required for local builds
- Add a "Build Args" section documenting the four R2 build args

### `services/render-worker/.env.example`

- Add a comment noting R2 credentials are also used at Docker build time (via build args)

### `.gitignore`

- Ensure `services/render-worker/secrets/` is ignored (add if not present) — only needed if any local secret files are created in the future

---

## Future Update Process (Manual)

When a new johnvansickle release is available:

1. Download the new release binary
2. Verify checksums if available on johnvansickle site
3. Compute SHA256 checksum (BSD --tag format):
   ```bash
   sha256sum --tag ffmpeg-release-amd64-static-X.Y.Z.tar.xz \
     > ffmpeg-release-amd64-static-X.Y.Z.tar.xz.sha256
   ```
4. Upload both files to R2:
   ```bash
   aws s3 cp ffmpeg-release-amd64-static-X.Y.Z.tar.xz \
     s3://$SOW_R2_BUCKET/build-dependencies/ffmpeg/ \
     --endpoint-url=$SOW_R2_ENDPOINT_URL

   aws s3 cp ffmpeg-release-amd64-static-X.Y.Z.tar.xz.sha256 \
     s3://$SOW_R2_BUCKET/build-dependencies/ffmpeg/ \
     --endpoint-url=$SOW_R2_ENDPOINT_URL
   ```
5. Update the `FFMPEG_VERSION` ARG in both `Dockerfile` and `Dockerfile.dev`:
   ```dockerfile
   ARG FFMPEG_VERSION=X.Y.Z
   ```
6. Run the full test suite
7. Commit and deploy

---

## Security Considerations

- **Build args**: R2 credentials are passed as build args and are visible in `docker history`. This is acceptable because the ECR repository is private and the image is only deployed to Lambda. BuildKit secrets were considered but rejected for simplicity.
- **Binary integrity**: SHA256 checksums are stored alongside the binary in R2 and verified during builds. This detects corruption or tampering.
- **R2 permissions**: The R2 access key used in CI should have minimal permissions (`s3:GetObject` on `build-dependencies/ffmpeg/*` only).
- **Fallback security**: When falling back to johnvansickle.com, the binary is downloaded over HTTPS with a 60-second timeout. No checksum verification is performed in fallback mode (johnvansickle does not provide SHA256 checksums programmatically).
- **Script cleanup**: The `download-ffmpeg.sh` script is removed from the image after execution (`rm -f /tmp/download-ffmpeg.sh`).

---

## Decisions Made

| Question | Decision | Rationale |
|----------|----------|-----------|
| awscli vs python3+boto3 | **python3 + boto3** | Avoids ~120MB image bloat; boto3 is already a runtime dependency |
| Multi-stage build | **No** | tar/xz overhead (~5MB) is acceptable; complexity not worth it |
| Credential passing | **Build args** | Simpler CI workflow; acceptable for private ECR Lambda images |
| Fallback timeout | **60 seconds** | Prevents hung fallback from blocking CI |
| SHA256 format | **--tag (BSD-style)** | Avoids filename mismatch with `sha256sum -c` |
| Shared script | **Yes** | Reduces drift between Dockerfile and Dockerfile.dev |
| R2 error visibility | **Log errors** | Makes R2 auth failures visible in CI logs instead of silencing them |
| FFmpeg version parameterization | **Yes, parameterize** | `ARG FFMPEG_VERSION=7.0.2` makes future updates a single-line change |
| Fallback to johnvansickle.com | **Yes, keep fallback** | Preserves build resilience if R2 is temporarily unavailable |
