# Spec: Migrate Render Worker to Pre-Built Static FFmpeg

## Problem

The render worker's `Dockerfile` downloads FFmpeg at build time from `johnvansickle.com`. This external network dependency is **flaky in CI** and has already required two hot-fix commits:

| Commit | Fix |
|--------|-----|
| `9527f7c` | Add `User-Agent` header to ffmpeg download |
| `098965d` | Use `python3` instead of `curl` for download |

The GitHub Actions `deploy-render-worker` job fails intermittently with build-time download errors (timeouts, 403s, connection resets). Each failure blocks deployment and requires manual retry.

### Current Dockerfile (excerpt)

```dockerfile
RUN python3 -c "import urllib.request; req = urllib.request.Request(
    'https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz',
    headers={'User-Agent': '...'});
    open('/tmp/ffmpeg.tar.xz', 'wb').write(urllib.request.urlopen(req, timeout=120).read())" \
    && tar -xJ --strip-components=1 -C /usr/local/bin \
    --wildcards 'ffmpeg-*-amd64-static/ffmpeg' 'ffmpeg-*-amd64-static/ffprobe' \
    -f /tmp/ffmpeg.tar.xz \
    && rm /tmp/ffmpeg.tar.xz \
    && chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe \
    && ffmpeg -version && ffprobe -version
```

## Goals

1. **Eliminate build-time network dependency** for FFmpeg — no more external downloads during `docker build`
2. **Use a pre-built, truly static FFmpeg binary** that works reliably inside the `public.ecr.aws/lambda/python:3.11` (Amazon Linux 2) base image
3. **Pin to a specific FFmpeg version** for reproducible builds
4. **Ensure all required codecs/filters are present** — the render worker depends on `libx264`, `libmp3lame`, `aac`, `loudnorm`, `amix`, `afade`, `adelay`, `asetpts`, `lavfi`/`color`, rawvideo input, and `-map_metadata`
5. **Update both `Dockerfile` and `Dockerfile.dev`** for consistency
6. **Verify via existing test suite** before merging — `test_docker.py` must pass, and all unit tests must remain green

## Design Decisions

- **Image choice**: `mwader/static-ffmpeg:7.1` — hardened static PIE binaries with no external libc dependencies. Explicitly documented as "can be used with any base image." Alpine-based `mlupin/ffmpeg:7.0-alpine` was evaluated and **rejected** because (a) the image returns 404 on Docker Hub, and (b) musl→glibc binary copies are a known source of runtime failures.
- **Version pinning**: `7.1` — stable release, close to the "latest" currently fetched from johnvansickle. `8.1.1` (latest) is also available if an upgrade is desired.
- **No source code changes** — the Python code already discovers `ffmpeg`/`ffprobe` via `shutil.which()` and accepts custom paths via constructor args. Only the Docker build layer changes.
- **Cleanup**: Remove `tar` and `xz` from `yum install` in both Dockerfiles — no longer needed.

## FFmpeg Feature Checklist

The render worker uses the following FFmpeg capabilities. All must be present in the new binary:

| Feature | Used In | Verification Command |
|---------|---------|---------------------|
| `ffprobe` (JSON output) | `audio_engine.get_audio_info()` | `ffprobe -version` |
| `libx264` encoder | `video_engine.get_video_codec_args()` | `ffmpeg -encoders \| grep libx264` |
| `libmp3lame` encoder | `audio_engine.concatenate_audio_files()` | `ffmpeg -encoders \| grep libmp3lame` |
| `aac` encoder | `video_engine.encode_video_with_ffmpeg()`, `generate_blank_video()` | `ffmpeg -encoders \| grep aac` |
| `loudnorm` filter | `audio_engine.build_ffmpeg_filter_complex()` | `ffmpeg -filters \| grep loudnorm` |
| `amix` filter | `audio_engine.build_ffmpeg_filter_complex()` | `ffmpeg -filters \| grep amix` |
| `afade` filter | `audio_engine.build_ffmpeg_filter_complex()` | `ffmpeg -filters \| grep afade` |
| `adelay` filter | `audio_engine.build_ffmpeg_filter_complex()` | `ffmpeg -filters \| grep adelay` |
| `asetpts` filter | `audio_engine.build_ffmpeg_filter_complex()` | `ffmpeg -filters \| grep asetpts` |
| `lavfi` / `color` source | `video_engine.generate_blank_video()` | `ffmpeg -filters \| grep color` |
| `rawvideo` demuxer | `video_engine.encode_video_with_ffmpeg()` | `ffmpeg -demuxers \| grep rawvideo` |
| `-map_metadata` | `video_engine.inject_chapters()` | N/A (core CLI option) |

## Migration Plan

### Phase 1: Local Binary Validation

**Goal**: Verify `mwader/static-ffmpeg:7.1` binary runs in the Lambda base image and supports all required features.

**Steps**:

1. Create temporary `Dockerfile.test`:
   ```dockerfile
   FROM public.ecr.aws/lambda/python:3.11
   COPY --from=mwader/static-ffmpeg:7.1 /ffmpeg /usr/local/bin/ffmpeg
   COPY --from=mwader/static-ffmpeg:7.1 /ffprobe /usr/local/bin/ffprobe
   RUN chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe
   CMD ["ffmpeg", "-version"]
   ```

2. Build and run basic checks:
   ```bash
   cd delivery/render-worker
   docker build -f Dockerfile.test -t sow-ffmpeg-test .
   docker run --rm sow-ffmpeg-test
   docker run --rm --entrypoint "" sow-ffmpeg-test ffmpeg -encoders | grep -E "libx264|libmp3lame|aac"
   docker run --rm --entrypoint "" sow-ffmpeg-test ffmpeg -filters | grep -E "loudnorm|amix|afade|adelay|asetpts|color"
   docker run --rm --entrypoint "" sow-ffmpeg-test ffprobe -version
   ```

3. Functional smoke test — encode a 1-second test video:
   ```bash
   docker run --rm --entrypoint "" sow-ffmpeg-test \
     bash -c "ffmpeg -f lavfi -i color=c=red:s=320x240:d=1 -c:v libx264 -f mp4 -y /tmp/test.mp4 && \
              ffprobe -v quiet -print_format json -show_format -show_streams /tmp/test.mp4"
   ```

**Success criteria**:
- Build completes with zero network access for ffmpeg
- All required encoders and filters are present
- Smoke test produces valid MP4 and JSON probe output

---

### Phase 2: Integration Validation

**Goal**: Verify the existing test suite passes with the new ffmpeg source.

**Steps**:

1. Create `Dockerfile.test-integration` (mirrors production but uses static ffmpeg):
   ```dockerfile
   FROM public.ecr.aws/lambda/python:3.11
   RUN yum install -y google-noto-sans-cjk-fonts \
       && yum clean all && rm -rf /var/cache/yum
   COPY fonts/ /usr/share/fonts/truetype/vendor/
   COPY --from=mwader/static-ffmpeg:7.1 /ffmpeg /usr/local/bin/ffmpeg
   COPY --from=mwader/static-ffmpeg:7.1 /ffprobe /usr/local/bin/ffprobe
   RUN chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe \
       && ffmpeg -version && ffprobe -version
   COPY requirements.txt ${LAMBDA_TASK_ROOT}/
   RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt
   COPY src/sow_render_worker/ ${LAMBDA_TASK_ROOT}/sow_render_worker/
   CMD ["sow_render_worker.lambda_handler.handler"]
   ```

2. Build the test image:
   ```bash
   cd delivery/render-worker
   docker build -f Dockerfile.test-integration -t sow-render-worker-test .
   ```

3. Run Docker-specific tests manually (since `test_docker.py` targets `Dockerfile` by default):
   ```bash
   docker run --rm --entrypoint "" sow-render-worker-test ffmpeg -version
   docker run --rm --entrypoint "" sow-render-worker-test python -c \
     "from sow_render_worker.lambda_handler import handler; print('OK')"
   docker run --rm --entrypoint "" sow-render-worker-test python -c \
     "from PIL import ImageFont; f = ImageFont.truetype('/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc', 24); print('OK')"
   ```

4. Run the full pytest suite locally (requires local ffmpeg or run inside container):
   ```bash
   PYTHONPATH=src pytest tests/ -v
   ```

**Success criteria**:
- `test_docker.py` equivalent checks pass (build, handler import, ffmpeg availability, CJK fonts)
- All unit tests pass with no regressions
- No changes to Python source code required

---

### Phase 3: Update Production Dockerfiles

**Goal**: Replace download-based ffmpeg with `COPY --from=mwader/static-ffmpeg:7.1`.

**Files to modify**:
- `delivery/render-worker/Dockerfile`
- `delivery/render-worker/Dockerfile.dev`

**Changes for `Dockerfile`**:
```dockerfile
FROM public.ecr.aws/lambda/python:3.11

RUN yum install -y google-noto-sans-cjk-fonts \
    && yum clean all \
    && rm -rf /var/cache/yum

COPY fonts/ /usr/share/fonts/truetype/vendor/

# Use pre-built static ffmpeg (replaces johnvansickle.com download)
COPY --from=mwader/static-ffmpeg:7.1 /ffmpeg /usr/local/bin/ffmpeg
COPY --from=mwader/static-ffmpeg:7.1 /ffprobe /usr/local/bin/ffprobe

RUN chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe \
    && ffmpeg -version && ffprobe -version

COPY requirements.txt ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

COPY src/sow_render_worker/ ${LAMBDA_TASK_ROOT}/sow_render_worker/

CMD ["sow_render_worker.lambda_handler.handler"]
```

**Changes for `Dockerfile.dev`**:
Same pattern — replace the `curl | tar` block with `COPY --from=mwader/static-ffmpeg:7.1`. Remove `tar` and `xz` from `yum install`.

**Key improvements**:
- No external network access required during build for ffmpeg
- Deterministic, pinned ffmpeg version (`7.1`)
- Faster builds (no 120s download timeout)
- Eliminates entire class of flaky CI failures

---

### Phase 4: CI / GitHub Actions Validation

**Goal**: Verify the deploy workflow succeeds end-to-end.

**Steps**:

1. Push the branch to trigger the `deploy-render-worker` job in `.github/workflows/deploy.yml`
2. Monitor the GitHub Actions run:
   - `docker build` step completes successfully
   - Image pushes to ECR
   - Lambda update succeeds
3. Verify Lambda function health after deployment:
   ```bash
   aws lambda get-function --function-name sow-render-worker
   ```
4. (Optional) Trigger a test render job via the webapp or direct SQS message to confirm the worker processes jobs correctly

**Success criteria**:
- GitHub Actions `deploy-render-worker` job passes on first attempt
- No "download timeout", "connection reset", or 403 errors
- Lambda function status is `Active` after update
- Test render job completes successfully (audio mix + video encode + upload)

---

### Phase 5: Cleanup and Documentation

**Steps**:

1. Remove temporary test files:
   - `delivery/render-worker/Dockerfile.test`
   - `delivery/render-worker/Dockerfile.test-integration`

2. Update `delivery/render-worker/README.md`:
   - Remove references to `johnvansickle.com` download
   - Document that ffmpeg is sourced from `mwader/static-ffmpeg:7.1`
   - Note the pinned version and rationale

3. Update `report/current_impl_status.md` if applicable (per project AGENTS.md guidelines)

4. Commit with message:
   ```
   fix(render-worker): use pre-built static ffmpeg in Docker image

   Replace runtime download from johnvansickle.com with COPY --from
   mwader/static-ffmpeg:7.1. This eliminates flaky build-time network
   dependencies that were causing GitHub Actions deploy failures.

   - Remove tar/xz from yum install (no longer needed)
   - Pin to ffmpeg 7.1 for reproducible builds
   - Update both Dockerfile and Dockerfile.dev
   ```

## Rollback Plan

If the deployed Lambda fails after migration:

1. **Immediate**: The previous ECR image tag (`latest` or a prior SHA) can be re-deployed via:
   ```bash
   aws lambda update-function-code \
     --function-name sow-render-worker \
     --image-uri $ECR_REGISTRY/sow-render-worker:<previous-tag>
   ```
2. **Git**: Revert the commit and re-run the deploy workflow
3. **Root cause**: Check CloudWatch logs for ffmpeg runtime errors (e.g., missing codecs). The `mwader/static-ffmpeg` image is widely used and statically linked, so runtime failures are unlikely unless a required codec was omitted from the build.

## Open Questions

1. **FFmpeg version**: Pin to `7.1` (stable, close to current johnvansickle "latest") or `8.1.1` (latest stable)?
2. **Test strategy**: `test_docker.py` builds from `Dockerfile` directly. Should the test be parameterized to accept a Dockerfile path, or should we temporarily rename the test Dockerfile during validation?
3. **Rollback readiness**: Is there a known-good ECR image tag available for immediate rollback if needed?
