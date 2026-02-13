---
phase: 02-analysis-service-integration
plan: 03
type: execute
wave: 1
depends_on: []
files_modified:
  - services/analysis/docker-compose.yml
autonomous: true

must_haves:
  truths:
    - "Qwen3 service is included in docker-compose.yml with proper networking"
    - "Analysis and Qwen3 services can communicate via common Docker network"
  artifacts:
    - path: "services/analysis/docker-compose.yml"
      provides: "Docker compose orchestration for analysis + qwen3 services"
      contains: "qwen3"
  key_links:
    - from: "services/analysis/docker-compose.yml"
      to: "services/qwen3/"
      via: "Dockerfile build context for qwen3 service"
      pattern: "qwen3.*build.*context.*../qwen3"
    - from: "analysis service container"
      to: "qwen3 service container"
      via: "Docker bridge network on qwen3:8000"
      pattern: "qwen3:8000"
---

# Objective

Add Qwen3 Alignment Service to the Analysis Service's docker-compose.yml configuration with proper networking and environment variables.

Purpose: Enable co-deployment of Analysis Service and Qwen3 Alignment Service as interconnected Docker containers, allowing Analysis Service to call Qwen3 for LRC timestamp refinement.

Output:
- Extended docker-compose.yml with qwen3 service definition
- Common Docker network for service-to-service communication
- Environment variable configuration for Qwen3 service

<execution_context>
@/home/mhuang/.claude/get-shit-done/workflows/execute-plan.md
@/home/mhuang/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md

# Reference: Existing Analysis Service docker-compose.yml
@services/analysis/docker-compose.yml

# Reference: Qwen3 service docker-compose.yml (copy structure)
@services/qwen3/docker-compose.yml

# Reference: Qwen3 service Dockerfile
@services/qwen3/Dockerfile

# Reference: Phase 1 Summary for qwen3 service configuration
@.planning/phases/01-qwen3-service-foundation/01-qwen3-service-foundation-04-SUMMARY.md
</context>

<tasks>

<task type="auto">
  <name>Add Qwen3 service to docker-compose.yml</name>
  <files>services/analysis/docker-compose.yml</files>
  <action>
    Modify `services/analysis/docker-compose.yml` to add qwen3 service:

    1. Add Qwen3 service configuration after analysis-dev service (before volumes section):

    ```yaml
    # Qwen3 Alignment Service for LRC timestamp refinement
    qwen3:
      build:
        context: ../qwen3
        dockerfile: Dockerfile
        args:
          - TARGETPLATFORM=${TARGETPLATFORM:-linux/amd64}
          - DEV_MODE=false
      ports:
        - "8001:8000"  # Different port to avoid conflict with analysis service
      environment:
        SOW_QWEN3_MODEL_PATH: /models/qwen3-forced-aligner
        SOW_QWEN3_DEVICE: ${SOW_QWEN3_DEVICE:-auto}
        SOW_QWEN3_DTYPE: ${SOW_QWEN3_DTYPE:-float32}
        SOW_QWEN3_MAX_CONCURRENT: ${SOW_QWEN3_MAX_CONCURRENT:-1}
        SOW_QWEN3_CACHE_DIR: /cache
        SOW_QWEN3_API_KEY: ${SOW_QWEN3_API_KEY:-}
        # R2 credentials (reuse from common env)
        SOW_QWEN3_R2_BUCKET: ${SOW_R2_BUCKET}
        SOW_QWEN3_R2_ENDPOINT_URL: ${SOW_R2_ENDPOINT_URL}
        SOW_QWEN3_R2_ACCESS_KEY_ID: ${SOW_R2_ACCESS_KEY_ID}
        SOW_QWEN3_R2_SECRET_ACCESS_KEY: ${SOW_R2_SECRET_ACCESS_KEY}
      volumes:
        - qwen3-cache:/cache
        - ${SOW_QWEN3_MODEL_VOLUME}:/models/qwen3-forced-aligner:ro
      deploy:
        resources:
          limits:
            memory: 8g
            cpus: '4'

    # Qwen3 Development mode with code mount
    qwen3-dev:
      build:
        context: ../qwen3
        dockerfile: Dockerfile
        args:
          - TARGETPLATFORM=${TARGETPLATFORM:-linux/amd64}
          - DEV_MODE=true
      ports:
        - "8001:8000"
      environment:
        SOW_QWEN3_MODEL_PATH: /models/qwen3-forced-aligner
        SOW_QWEN3_DEVICE: ${SOW_QWEN3_DEVICE:-auto}
        SOW_QWEN3_DTYPE: ${SOW_QWEN3_DTYPE:-float32}
        SOW_QWEN3_MAX_CONCURRENT: ${SOW_QWEN3_MAX_CONCURRENT:-1}
        SOW_QWEN3_CACHE_DIR: /cache
        SOW_QWEN3_API_KEY: ${SOW_QWEN3_API_KEY:-}
        DEV_MODE: "true"
        # R2 credentials (reuse from common env)
        SOW_QWEN3_R2_BUCKET: ${SOW_R2_BUCKET}
        SOW_QWEN3_R2_ENDPOINT_URL: ${SOW_R2_ENDPOINT_URL}
        SOW_QWEN3_R2_ACCESS_KEY_ID: ${SOW_R2_ACCESS_KEY_ID}
        SOW_QWEN3_R2_SECRET_ACCESS_KEY: ${SOW_R2_SECRET_ACCESS_KEY}
      volumes:
        - qwen3-cache:/cache
        - ${SOW_QWEN3_MODEL_VOLUME}:/models/qwen3-forced-aligner:ro
        - ../qwen3/src:/workspace/src:ro
      deploy:
        resources:
          limits:
            memory: 8g
            cpus: '4'
    ```

    2. Add qwen3-cache volume definition to the volumes section (after analysis-cache):
    ```yaml
    volumes:
      analysis-cache:
      qwen3-cache:
    ```

    3. Analysis service automatically uses qwen3:8000 (internal Docker network) by default via SOW_QWEN3_BASE_URL.

    IMPORTANT: Port 8001 used externally to avoid conflict with analysis service:8000, but internally services communicate on default 8000.

    Note: Requires SOW_QWEN3_MODEL_VOLUME environment variable to point to pre-downloaded Qwen3 model directory.
  </action>
  <verify>
    Verify service added:
    - `grep -c "qwen3:" services/analysis/docker-compose.yml | grep -E "^[2-4]$"`
    - `grep "context: ../qwen3" services/analysis/docker-compose.yml`
    - `grep "qwen3-cache:" services/analysis/docker-compose.yml`
  </verify>
  <done>
    Qwen3 service added to docker-compose.yml with build configuration, port mapping (8001:8000), environment variables, volume mounts, resource limits (8GB RAM, 4 CPUs), and cache volume.
  </done>
</task>

</tasks>

<verification>

After plan completion, verify:
1. docker-compose.yml has qwen3 service definition with proper build context
2. qwen3-cache volume defined
3. Port 8001 mapped externally (no conflict with analysis:8000)
4. R2 credentials passed to qwen3 service

Run: `docker compose -f services/analysis/docker-compose.yml config | grep -A 20 "qwen3:"`

</verification>

<success_criteria>

Plan is successful when:
- Qwen3 service definition exists in docker-compose.yml with build context ../qwen3
- Port 8001:8000 mapped to avoid port conflict
- Qwen3 service environment variables configured (SOW_QWEN3_*, R2 credentials)
- qwen3-cache volume defined
- Analysis service can reach qwen3 service via qwen3:8000 on Docker network
- docker compose config validates without errors

</success_criteria>

<output>

After completion, create `.planning/phases/02-analysis-service-integration/02-analysis-service-integration-03-SUMMARY.md`

</output>
