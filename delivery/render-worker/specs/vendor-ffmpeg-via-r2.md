# Spec: Vendor johnvansickle FFmpeg Binary via R2

## Problem

The render worker's `Dockerfile` downloads FFmpeg at build time from `johnvansickle.com`. This external network dependency is flaky in CI and has already required two hot-fix commits:

| Commit | Fix |
|--------|-----|
| `9527f7c` | Add `User-Agent` header to ffmpeg download |
| `098965d` | Use `python3` instead of `curl` for download |

The GitHub Actions `deploy-render-worker` job fails intermittently with build-time download errors (timeouts, 403s, connection resets). Each failure blocks deployment and requires manual retry.

## Solution Overview

Download the johnvansickle release build once, store it in Cloudflare R2 (the project's existing S3-compatible storage), and fetch it from R2 during Docker builds. R2 is reliable, has no egress fees, and is already authenticated in CI.

This approach:
- Eliminates the flaky `johnvansickle.com` build-time dependency as the primary source
- Uses the exact same binary that currently works in production (zero runtime risk)
- Leverages existing infrastructure (R2 bucket, credentials, AWS CLI patterns)
- Keeps `tar` and `xz` in the image (still needed to extract the `.tar.xz`)
- Retains johnvansickle.com as a fallback for resilience

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

The `.sha256` file contains the SHA256 checksum of the `.tar.xz` for integrity verification during builds.

### One-Time Upload Process

```bash
FFMPEG_VERSION=7.0.2

# Download from johnvansickle (one time)
curl -fsSL -o ffmpeg-release-amd64-static-${FFMPEG_VERSION}.tar.xz \
  https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz

# Compute SHA256 checksum
sha256sum ffmpeg-release-amd64-static-${FFMPEG_VERSION}.tar.xz > ffmpeg-release-amd64-static-${FFMPEG_VERSION}.tar.xz.sha256

# Upload both files to R2
aws s3 cp ffmpeg-release-amd64-static-${FFMPEG_VERSION}.tar.xz \
  s3://$SOW_R2_BUCKET/build-dependencies/ffmpeg/ \
  --endpoint-url=$SOW_R2_ENDPOINT_URL

aws s3 cp ffmpeg-release-amd64-static-${FFMPEG_VERSION}.tar.xz.sha256 \
  s3://$SOW_R2_BUCKET/build-dependencies/ffmpeg/ \
  --endpoint-url=$SOW_R2_ENDPOINT_URL
```

---

## Dockerfile Changes

### `Dockerfile` (Production)

Replace the `python3 urllib` download block with `aws s3 cp` from R2, using BuildKit secrets for credential security, with johnvansickle.com as fallback:

```dockerfile
FROM public.ecr.aws/lambda/python:3.11

RUN yum install -y google-noto-sans-cjk-fonts tar xz awscli \
    && yum clean all \
    && rm -rf /var/cache/yum

COPY fonts/ /usr/share/fonts/truetype/vendor/

ARG FFMPEG_VERSION=7.0.2
ARG R2_BUCKET
ARG R2_ENDPOINT_URL

# Download ffmpeg from R2 with fallback to johnvansickle.com
# Credentials are passed via BuildKit secret to avoid leaking in image layers
RUN --mount=type=secret,id=r2_credentials \
    set -eux; \
    # Load R2 credentials from BuildKit secret \
    set -a; . /run/secrets/r2_credentials; set +a; \
    \
    # Try R2 first \
    if aws s3 cp s3://${R2_BUCKET}/build-dependencies/ffmpeg/ffmpeg-release-amd64-static-${FFMPEG_VERSION}.tar.xz /tmp/ffmpeg.tar.xz \
         --endpoint-url=${R2_ENDPOINT_URL} 2>/dev/null; then \
      echo "Downloaded ffmpeg from R2"; \
      \
      # Verify checksum if available on R2 \
      if aws s3 cp s3://${R2_BUCKET}/build-dependencies/ffmpeg/ffmpeg-release-amd64-static-${FFMPEG_VERSION}.tar.xz.sha256 /tmp/ffmpeg.tar.xz.sha256 \
           --endpoint-url=${R2_ENDPOINT_URL} 2>/dev/null; then \
        cd /tmp && sha256sum -c ffmpeg.tar.xz.sha256; \
        rm -f /tmp/ffmpeg.tar.xz.sha256; \
      fi; \
    else \
      # Fallback to johnvansickle.com \
      echo "R2 download failed, falling back to johnvansickle.com"; \
      curl -fsSL -o /tmp/ffmpeg.tar.xz \
        https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz; \
    fi; \
    \
    # Extract binaries \
    tar -xJ --strip-components=1 -C /usr/local/bin \
      --wildcards "ffmpeg-*-amd64-static/ffmpeg" "ffmpeg-*-amd64-static/ffprobe" \
      -f /tmp/ffmpeg.tar.xz; \
    rm -f /tmp/ffmpeg.tar.xz; \
    chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe; \
    ffmpeg -version && ffprobe -version

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY src/sow_render_worker/ ${LAMBDA_TASK_ROOT}/sow_render_worker/

CMD ["sow_render_worker.lambda_handler.handler"]
```

### `Dockerfile.dev` (Local Development)

Same pattern, replacing the `curl | tar` block:

```dockerfile
FROM public.ecr.aws/lambda/python:3.11

RUN yum install -y google-noto-sans-cjk-fonts tar xz awscli \
    && yum clean all \
    && rm -rf /var/cache/yum

COPY fonts/ /usr/share/fonts/truetype/vendor/

ARG FFMPEG_VERSION=7.0.2
ARG R2_BUCKET
ARG R2_ENDPOINT_URL

# Download ffmpeg from R2 with fallback to johnvansickle.com
# Credentials are passed via BuildKit secret to avoid leaking in image layers
RUN --mount=type=secret,id=r2_credentials \
    set -eux; \
    # Load R2 credentials from BuildKit secret \
    set -a; . /run/secrets/r2_credentials; set +a; \
    \
    # Try R2 first \
    if aws s3 cp s3://${R2_BUCKET}/build-dependencies/ffmpeg/ffmpeg-release-amd64-static-${FFMPEG_VERSION}.tar.xz /tmp/ffmpeg.tar.xz \
         --endpoint-url=${R2_ENDPOINT_URL} 2>/dev/null; then \
      echo "Downloaded ffmpeg from R2"; \
      \
      # Verify checksum if available on R2 \
      if aws s3 cp s3://${R2_BUCKET}/build-dependencies/ffmpeg/ffmpeg-release-amd64-static-${FFMPEG_VERSION}.tar.xz.sha256 /tmp/ffmpeg.tar.xz.sha256 \
           --endpoint-url=${R2_ENDPOINT_URL} 2>/dev/null; then \
        cd /tmp && sha256sum -c ffmpeg.tar.xz.sha256; \
        rm -f /tmp/ffmpeg.tar.xz.sha256; \
      fi; \
    else \
      # Fallback to johnvansickle.com \
      echo "R2 download failed, falling back to johnvansickle.com"; \
      curl -fsSL -o /tmp/ffmpeg.tar.xz \
        https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz; \
    fi; \
    \
    # Extract binaries \
    tar -xJ --strip-components=1 -C /usr/local/bin \
      --wildcards "ffmpeg-*-amd64-static/ffmpeg" "ffmpeg-*-amd64-static/ffprobe" \
      -f /tmp/ffmpeg.tar.xz; \
    rm -f /tmp/ffmpeg.tar.xz; \
    chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe; \
    ffmpeg -version && ffprobe -version

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

CMD ["sow_render_worker.lambda_handler.handler"]
```

### Key Points

- `tar` and `xz` are still required (to extract the `.tar.xz`), so they remain in `yum install`
- `awscli` is added to `yum install` because the AWS CLI is **not** pre-installed in `public.ecr.aws/lambda/python:3.11`
- The binary itself is identical to the current one when fetched from R2 — zero runtime risk
- BuildKit secrets (`--mount=type=secret`) prevent R2 credentials from appearing in Docker image layers or history
- The secret file format is `KEY=value` lines (e.g., `AWS_ACCESS_KEY_ID=...`) which are sourced as environment variables
- `FFMPEG_VERSION` is parameterized as an `ARG` to make future updates easier — only one line changes per version bump
- johnvansickle.com is retained as a fallback if R2 is unavailable, preserving build resilience
- Checksum verification is performed when the `.sha256` file is available on R2

---

## CI/CD Workflow Updates

### `.github/workflows/deploy.yml`

Enable BuildKit and pass R2 credentials via secret mount:

```yaml
- name: Build, tag, and push image to Amazon ECR
  id: build-image
  env:
    ECR_REGISTRY: ${{ steps.login-ecr.outputs.registry }}
    ECR_REPOSITORY: sow-render-worker
    IMAGE_TAG: ${{ github.sha }}
    DOCKER_BUILDKIT: 1
  run: |
    # Create BuildKit secret file with R2 credentials
    mkdir -p /tmp/secrets
    cat > /tmp/secrets/r2_credentials <<EOF
    AWS_ACCESS_KEY_ID=${{ secrets.SOW_R2_ACCESS_KEY_ID }}
    AWS_SECRET_ACCESS_KEY=${{ secrets.SOW_R2_SECRET_ACCESS_KEY }}
    EOF
    
    docker build \
      --secret id=r2_credentials,src=/tmp/secrets/r2_credentials \
      --build-arg R2_BUCKET=${{ secrets.SOW_R2_BUCKET }} \
      --build-arg R2_ENDPOINT_URL=${{ secrets.SOW_R2_ENDPOINT_URL }} \
      -t $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG .
    docker push $ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG
    
    # Clean up secret file
    rm -f /tmp/secrets/r2_credentials
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

Update `docker-compose.yml` to enable BuildKit and pass the secret file:

```yaml
services:
  render-worker:
    build:
      context: .
      dockerfile: Dockerfile
      secrets:
        - r2_credentials
      args:
        R2_BUCKET: ${SOW_R2_BUCKET}
        R2_ENDPOINT_URL: ${SOW_R2_ENDPOINT_URL}
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

secrets:
  r2_credentials:
    file: ./secrets/r2_credentials
```

### Local Secret File Setup

Developers must create a local secret file before building:

```bash
mkdir -p delivery/render-worker/secrets
cat > delivery/render-worker/secrets/r2_credentials <<EOF
AWS_ACCESS_KEY_ID=${SOW_R2_ACCESS_KEY_ID}
AWS_SECRET_ACCESS_KEY=${SOW_R2_SECRET_ACCESS_KEY}
EOF
```

> **Note:** The `secrets/` directory is already ignored by the project's `.gitignore` (or should be added if not). This file contains sensitive credentials and must never be committed.

### Build Command

```bash
# BuildKit is required for secret mounts
DOCKER_BUILDKIT=1 docker compose up --build
```

---

## Testing Strategy

### Phase 1: Binary Validation

1. Upload the binary and checksum to R2
2. Verify `aws s3 cp` works from local machine
3. Create local `secrets/r2_credentials` file
4. Build `Dockerfile` locally with BuildKit:
   ```bash
   DOCKER_BUILDKIT=1 docker build \
     --secret id=r2_credentials,src=secrets/r2_credentials \
     --build-arg R2_BUCKET=$SOW_R2_BUCKET \
     --build-arg R2_ENDPOINT_URL=$SOW_R2_ENDPOINT_URL \
     -t sow-render-worker-test .
   ```
5. Run the existing feature checklist:
   ```bash
   docker run --rm --entrypoint "" sow-render-worker-test ffmpeg -version
   docker run --rm --entrypoint "" sow-render-worker-test ffmpeg -encoders | grep -E "libx264|libmp3lame|aac"
   docker run --rm --entrypoint "" sow-render-worker-test ffmpeg -filters | grep -E "loudnorm|amix|afade|adelay|asetpts|color"
   docker run --rm --entrypoint "" sow-render-worker-test ffprobe -version
   ```

### Phase 2: Integration Validation

1. Run `PYTHONPATH=src pytest tests/ -v`
2. Run `test_docker.py` (may need to set `DOCKER_BUILDKIT=1` in environment or update test to pass secrets)
3. Run a functional smoke test (encode 1-second test video)

### Phase 3: CI Validation

1. Push branch to trigger `deploy-render-worker` job
2. Verify build succeeds and primarily uses R2 (not fallback)
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

3. **Root cause**: Since the binary is identical to the current working one when fetched from R2, runtime failures are extremely unlikely. Any issues would be in the build process (R2 auth, AWS CLI availability, `tar`/`xz` presence, BuildKit configuration).

---

## Documentation Updates

### `delivery/render-worker/README.md`

- Remove references to `johnvansickle.com` as the primary download source
- Document that ffmpeg is sourced from R2 (`build-dependencies/ffmpeg/`) with johnvansickle.com as fallback
- Document the manual update process (see below)
- Update the "Prerequisites" section to note:
  - R2 credentials are required for Docker builds
  - BuildKit must be enabled (`DOCKER_BUILDKIT=1`)
  - A `secrets/r2_credentials` file is required for local builds
- Add a "Build Secrets" section explaining the `secrets/r2_credentials` file format

### `delivery/render-worker/.env.example`

- Add a comment noting R2 credentials are also used at Docker build time

### `.gitignore`

- Ensure `delivery/render-worker/secrets/` is ignored (add if not present)

---

## Future Update Process (Manual)

When a new johnvansickle release is available:

1. Download the new release binary
2. Verify checksums if available on johnvansickle site
3. Compute SHA256 checksum:
   ```bash
   sha256sum ffmpeg-release-amd64-static-X.Y.Z.tar.xz > ffmpeg-release-amd64-static-X.Y.Z.tar.xz.sha256
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

- **BuildKit secrets**: R2 credentials are passed via `--mount=type=secret` and are never persisted in image layers or Docker history. The secret file is sourced as environment variables only during the specific `RUN` step.
- **Secret file cleanup**: In CI, the temporary secret file is deleted immediately after the `docker build` command. In local dev, the `secrets/r2_credentials` file is gitignored.
- **Binary integrity**: SHA256 checksums are stored alongside the binary in R2 and verified during builds. This detects corruption or tampering.
- **R2 permissions**: The R2 access key used in CI should have minimal permissions (`s3:GetObject` on `build-dependencies/ffmpeg/*` only).
- **Fallback security**: When falling back to johnvansickle.com, the binary is downloaded over HTTPS. No checksum verification is performed in fallback mode (johnvansickle does not provide SHA256 checksums programmatically).

---

## Decisions Made

| Question | Decision | Rationale |
|----------|----------|-----------|
| BuildKit secrets vs ARG build args | **BuildKit secrets** | Credentials do not appear in image layers or history. More secure despite slightly more complex syntax. |
| Fallback to johnvansickle.com | **Yes, keep fallback** | Preserves build resilience if R2 is temporarily unavailable. Fallback is logged so CI failures are visible. |
| SHA256 checksum verification | **Yes, verify checksum** | Detects corruption or tampering. Checksum file is stored alongside binary in R2. |
| FFmpeg version parameterization | **Yes, parameterize** | `ARG FFMPEG_VERSION=7.0.2` makes future updates a single-line change in both Dockerfiles. |
| AWS CLI availability | **Install via yum** | `awscli` is not pre-installed in `public.ecr.aws/lambda/python:3.11`. Added to `yum install` in both Dockerfiles. |
