# Implementation Plan: render-worker follow-ups (ffmpeg-via-R2 v2)

Review follow-ups to the v2 spec implementation (commits `f9eba26` + `b185701`).
Four focused, independent conventional-commit work items. Recommended landing order
given below; items are non-overlapping and may be reordered, except that item 3
should be landed last / only after one confirmed successful R2 build.

Locus for all work: `services/render-worker/` + `.github/workflows/`.

Reference spec: `services/render-worker/specs/vendor-ffmpeg-via-r2-v2.md`.

---

## Item 1 — Encoder/filter/ffprobe assertions in `test_docker.py`

**Goal:** encode the spec's "Phase 1 feature checklist" into the test suite.

**File:** `services/render-worker/tests/test_docker.py`

**Adds three tests** to the existing `TestDockerBuild` class (mirroring
`test_ffmpeg_available_in_container`'s `subprocess.run([docker, run, --rm,
--entrypoint, "", IMAGE, ...])` pattern):

```python
def test_ffprobe_available_in_container(self):
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "", self.IMAGE_NAME,
         "ffprobe", "-version"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"ffprobe not found:\n{result.stderr}"
    assert "ffprobe version" in result.stderr or "ffprobe version" in result.stdout

def test_ffmpeg_encoders_available(self):
    # Required for the project's audio/video pipeline
    expected_encoders = ("libx264", "libmp3lame", "aac")
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "", self.IMAGE_NAME,
         "ffmpeg", "-hide_banner", "-encoders"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"ffmpeg -encoders failed:\n{result.stderr}"
    for enc in expected_encoders:
        assert enc in result.stdout, f"missing encoder {enc!r} in ffmpeg -encoders output"

def test_ffmpeg_filters_available(self):
    # Used by audio_engine.py (amix, afade, adelay, loudnorm, asetpts) and
    # video_engine.py (color). Listed in spec Phase 1 feature checklist.
    expected_filters = ("loudnorm", "amix", "afade", "adelay", "asetpts", "color")
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "", self.IMAGE_NAME,
         "ffmpeg", "-hide_banner", "-filters"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"ffmpeg -filters failed:\n{result.stderr}"
    for flt in expected_filters:
        assert flt in result.stdout, f"missing filter {flt!r} in ffmpeg -filters output"
```

**Notes:**

- `-hide_banner` keeps assertions robust by shrinking incidental output; the
  codec/filter names remain present.
- pytest runs alphabetically; the existing `test_cjk_fonts_available_in_container`
  similarly depends on a built image, so no fixture wiring needed.
- These tests require Docker and the existing built image `sow-render-worker-test`.
  Respect the existing `SKIP_DOCKER_TESTS=1` skip gate — they're inside
  `TestDockerBuild` so they inherit it. ✓
- The element list comes directly from the spec ("Phase 1 feature checklist"
  section). Worth a short comment cross-referencing the spec so future maintainers
  keep it in sync.

**Commit:** `test(render-worker): assert ffmpeg encoders/filters and ffprobe in container`

---

## Item 2 — One-time upload script (or README section)

**Goal:** remove the hidden assumption that "the binary is already in R2." Make
refresh discoverable and reproducible.

**Recommended:** add both a script (`scripts/upload-ffmpeg-to-r2.sh`) **and** a README
subsection that points to it — single source of truth in the script, README just
references it, mirroring how `download-ffmpeg.sh` is documented today.

**New file:** `services/render-worker/scripts/upload-ffmpeg-to-r2.sh`

```bash
#!/usr/bin/env bash
# One-time / on-update upload of the johnvansickle FFmpeg static build to R2.
# Run locally with R2 credentials available (NOT in CI; this is a manual step).
# See README.md "Maintaining the FFmpeg Binary in R2" and
# specs/vendor-ffmpeg-via-r2-v2.md "One-Time Upload Process".
set -euo pipefail

FFMPEG_VERSION="${1:?Usage: $0 <ffmpeg_version>}"
# R2 creds as env vars (not args) to avoid leaking into shell history / process args.
: "${SOW_R2_BUCKET:?SOW_R2_BUCKET required}"
: "${SOW_R2_ENDPOINT_URL:?SOW_R2_ENDPOINT_URL required}"
: "${SOW_R2_ACCESS_KEY_ID:?SOW_R2_ACCESS_KEY_ID required}"
: "${SOW_R2_SECRET_ACCESS_KEY:?SOW_R2_SECRET_ACCESS_KEY required}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

ARCHIVE_NAME="ffmpeg-release-amd64-static-${FFMPEG_VERSION}.tar.xz"
ARCHIVE="${TMP}/${ARCHIVE_NAME}"
R2_PREFIX="build-dependencies/ffmpeg"

# awscli is required for this manual operator script (boto3 is used at *build*
# time where awscli would bloat the image; this script does not run in the image).
command -v aws >/dev/null || { echo "aws CLI required (pip install awscli)"; exit 1; }

echo "Downloading from johnvansickle.com -> ${ARCHIVE}"
curl -fL --retry 3 -o "${ARCHIVE}" \
  https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz

# BSD --tag form embeds the basename:  "SHA256 (<name>) = <hash>"
# download-ffmpeg.sh relies on this format to extract the expected hash.
# Run from inside $TMP so the embedded basename matches the R2 side naming.
cd "${TMP}" && sha256sum --tag "${ARCHIVE_NAME}" > "${ARCHIVE_NAME}.sha256"

echo "Uploading to s3://${SOW_R2_BUCKET}/${R2_PREFIX}/"
aws s3 cp "${ARCHIVE}"        "s3://${SOW_R2_BUCKET}/${R2_PREFIX}/" --endpoint-url="${SOW_R2_ENDPOINT_URL}"
aws s3 cp "${ARCHIVE}.sha256" "s3://${SOW_R2_BUCKET}/${R2_PREFIX}/" --endpoint-url="${SOW_R2_ENDPOINT_URL}"

echo "Done. Update FFMPEG_VERSION ARG in Dockerfile/Dockerfile.dev if changed."
echo "Verify a build uses R2 by checking logs for: Downloaded ffmpeg from R2"
```

**README.md adds a new subsection** under "Build Args" (or after it) titled
**"Maintaining the FFmpeg Binary in R2"**:

````markdown
### Maintaining the FFmpeg Binary in R2

When a new johnvansickle release should be vendored:

```bash
cd services/render-worker
# export SOW_R2_* credentials (or source from services/render-worker/.env)
./scripts/upload-ffmpeg-to-r2.sh 7.0.2
```

This downloads from johnvansickle.com, computes the BSD `--tag` SHA256 (so
`download-ffmpeg.sh` can verify regardless of the local filename), and uploads
both objects to:

    s3://${SOW_R2_BUCKET}/build-dependencies/ffmpeg/ffmpeg-release-amd64-static-7.0.2.tar.xz
    s3://${SOW_R2_BUCKET}/build-dependencies/ffmpeg/ffmpeg-release-amd64-static-7.0.2.tar.xz.sha256

Then bump `ARG FFMPEG_VERSION=` in `Dockerfile` and `Dockerfile.dev` if the
version changed, and run the full test suite.
````

**Verification:**

- `aws s3 ls s3://$SOW_R2_BUCKET/build-dependencies/ffmpeg/ --endpoint-url=$SOW_R2_ENDPOINT_URL`
  lists both objects with the versioned keys.
- Local clean build with R2 creds prints `Downloaded ffmpeg from R2`.

**Commit:** `chore(render-worker): add manual ffmpeg upload-to-r2 script + README section`

---

## Item 3 — Deploy workflow must not deploy on silent fallback

**Goal:** surface a forgotten upload or R2 misconfiguration by failing CI when the
build fell back to johnvansickle.

**File:** `.github/workflows/deploy.yml` — the `deploy-render-worker` job's
`Build, tag, and push image to Amazon ECR` step.

**Tweaks in that single `run:` block:**

1. Add `--progress=plain` so BuildKit emits the `RUN` step's stdout (our
   `echo "Downloaded ffmpeg from R2"` marker) verbatim instead of collapsing it
   behind a progress bar.
2. Capture combined stdout+stderr to a logfile via `tee`, then grep for the
   marker; fail if absent.
3. Guard the marker check so it only runs when R2 creds were actually passed
   (i.e., when fallback is genuinely the "silent failure" case, not "we didn't
   even try"). If creds are absent, the script already prints
   `R2 credentials not fully provided. Falling back...` — that should *definitely*
   fail CI for a deployed build.

```yaml
      - name: Build, tag, and push image to Amazon ECR
        id: build-image
        env:
          ECR_REGISTRY: ${{ steps.login-ecr.outputs.registry }}
          ECR_REPOSITORY: sow-render-worker
          IMAGE_TAG: ${{ github.sha }}
          R2_BUCKET: ${{ secrets.SOW_R2_BUCKET }}
          R2_ENDPOINT_URL: ${{ secrets.SOW_R2_ENDPOINT_URL }}
          R2_ACCESS_KEY_ID: ${{ secrets.SOW_R2_ACCESS_KEY_ID }}
          R2_SECRET_ACCESS_KEY: ${{ secrets.SOW_R2_SECRET_ACCESS_KEY }}
        run: |
          set -euo pipefail
          BUILD_LOG="$(mktemp)"
          docker build \
            --progress=plain \
            --build-arg R2_BUCKET="$R2_BUCKET" \
            --build-arg R2_ENDPOINT_URL="$R2_ENDPOINT_URL" \
            --build-arg R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" \
            --build-arg R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY" \
            -t "$ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG" . 2>&1 | tee "$BUILD_LOG"

          # R2 creds must be present in a deployed build; fallback would mean
          # we're shipping an image built from the flaky primary the v2 spec
          # set out to eliminate.
          for k in R2_BUCKET R2_ENDPOINT_URL R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY; do
            [ -n "${!k}" ] || { echo "::error::Secret $k is empty; refusing to deploy a fallback-built image."; exit 1; }
          done

          if ! grep -q "Downloaded ffmpeg from R2" "$BUILD_LOG"; then
            echo "::error::FFmpeg was NOT fetched from R2 — build fell back to johnvansickle.com. Aborting deploy to surface the issue (see log for 'R2 download failed:' cause)."
            exit 1
          fi
          echo "Verified: FFmpeg sourced from R2."

          docker push "$ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG"
          echo "image=$ECR_REGISTRY/$ECR_REPOSITORY:$IMAGE_TAG" >> "$GITHUB_OUTPUT"
```

**Trade-off to surface to reviewer:** the spec explicitly retains the
johnvansickle fallback "to preserve build resilience if R2 is temporarily
unavailable." Hard-failing the deploy on a successful-but-fallback build
*removes* that resilience at deploy time. That is what the follow-up item asks
for, and it is defensible because shipping a fallback-built image hides the real
signal — but flag it. A softer alternative (if the maintainer prefers) is to keep
the build & push but post a `::warning::` + a GitHub step/job summary annotation
so the issue is visible without blocking a critical deploy during a genuine R2
outage. Hard-fail (item 3 as specified) is recommended only if R2 has been
observed reliable; otherwise the warning variant is safer. Either way, item 3
makes the silent failure loud.

**Subtle correctness detail:** `download-ffmpeg.sh` prints
`Downloaded ffmpeg from R2` from inside a `python3 -c` heredoc executed by a
`RUN`. With `--progress=plain` BuildKit streams `RUN` stdout to the log; without
it the marker is often suppressed (progress-bar mode buffers/discards). So
`--progress=plain` is load-bearing for the grep — call it out in the commit
message.

**Verification:**

- Temporarily set `SOW_R2_BUCKET=""` (or point at a non-existent object) → CI
  build step fails with the `::error::` annotation and "R2 download failed:"
  context.
- With valid creds + uploaded binary → build step logs
  `Verified: FFmpeg sourced from R2.` and proceeds to push + Lambda update.

**Commit:** `ci(render-worker): fail deploy when ffmpeg build silently falls back to johnvansickle`

---

## Item 4 — Document the expected checksum file format

**Goal:** make the `download-ffmpeg.sh` contract on the `.sha256` file explicit so
future R2 uploads don't silently break verification.

**File:** `services/render-worker/scripts/download-ffmpeg.sh`

**Add a comment block** immediately above the

```python
    try:
        with open(checksum_path, 'r') as f:
            expected = f.read().split('=')[-1].strip()
```

line (inside the Python heredoc — Python comments in a heredoc are fine):

```python
    # Expected file format (produced by `sha256sum --tag <file>`):
    #     SHA256 (ffmpeg-release-amd64-static-7.0.2.tar.xz) = <64-hex-hash>
    # The `--tag` (BSD) form is used so the expected hash can be extracted by
    # splitting on '=' regardless of the locally-downloaded filename
    # (/tmp/ffmpeg.tar.xz differs from the .sha256's embedded name under
    # `sha256sum -c`). If the staged file on R2 is regenerated without `--tag`,
    # this verification will fail loudly (build aborts, does NOT silently fall
    # back) — see scripts/upload-ffmpeg-to-r2.sh which enforces `--tag`.
    try:
        with open(checksum_path, 'r') as f:
            expected = f.read().split('=')[-1].strip()
```

**Note:** the current `split('=')[-1].strip()` tolerates both BSD `--tag`
(`... = <hash>`) and GNU plain (`<hash>  filename`) outputs — the latter splits on
no `=`, so `[-1]` is the whole line and `strip()` keeps it, which would yield
`<hash>  filename`, not the hash. So rather than claim portability, the comment
correctly narrows the contract to `--tag`. If robustness against plain-GNU format
is desired, replace with a stricter parse, e.g.
`re.fullmatch(r'[0-9a-f]{64}', token)`. Optional — call it out.

**Commit:** `docs(render-worker): document sha256 --tag checksum contract in download-ffmpeg.sh`

---

## Sequencing & rollout

| Step | Item | Commit prefix | Depends on |
|------|------|---------------|------------|
| 1 | (4) comment in `download-ffmpeg.sh` | `docs:` | none |
| 2 | (2) upload script + README section | `chore:` | none (but logically pairs with 4) |
| 3 | (1) test assertions | `test:` | none |
| 4 | (3) deploy workflow log assertion | `ci:` | ideally after confirming one successful R2 build so item 3 doesn't immediately turn CI red |

**Pre-flight before landing item 3:** run the deploy workflow once on a feature
branch with valid R2 creds and the uploaded binary, and confirm
`Downloaded ffmpeg from R2` appears in the `--progress=plain` log. Otherwise item 3
will block the next main deploy as intended.

**No-rollback concerns:** all four are additive (new tests, new script, new
comment, a stricter CI gate). Reverting any of them is trivial.

---

## Out of scope (raised during review, not part of these four items)

- `test_docker_compose_config_valid` does not validate `docker-compose.dev.yml`;
  consider `docker compose -f docker-compose.dev.yml config --quiet`.
- `.env.example` does not document `SOW_FADE_ALPHA_STEPS` /
  `SOW_FRAME_CACHE_ENABLED` / `SOW_MAX_CACHE_ENTRIES` (compose-only vars).
- No standalone unit tests for `download-ffmpeg.sh`'s exit-code-2 branch and
  no-creds branch (currently only exercised end-to-end via Docker, which needs
  real R2 creds to hit the primary path).
