---
phase: 05-performance-production-readiness
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - services/qwen3/src/sow_qwen3/main.py
  - services/qwen3/src/sow_qwen3/config.py
autonomous: true

must_haves:
  truths:
    - "Service loads Qwen3 model once at startup (not per-request)"
    - "Service starts gracefully even if model loading fails (health check returns 503)"
    - "Model initialization blocks startup until complete or failed"
    - "Concurrency limit is set to 2-3 for production"
    - "Health check returns 503 when model not ready"
  artifacts:
    - path: "services/qwen3/src/sow_qwen3/main.py"
      provides: "FastAPI lifespan event for model loading"
      contains: "@asynccontextmanager"
      contains: "lifespan"
    - path: "services/qwen3/src/sow_qwen3/config.py"
      provides: "Production settings including MAX_CONCURRENT"
      contains: "MAX_CONCURRENT"
    - path: "services/qwen3/src/sow_qwen3/workers/aligner.py"
      provides: "Singleton model wrapper with initialization"
      contains: "initialize()"
  key_links:
    - from: "main.py"
      to: "workers/aligner.py"
      via: "Qwen3AlignerWrapper initialization"
      pattern: "Qwen3AlignerWrapper\\("
    - from: "main.py"
      to: "routes/health.py"
      via: "set_aligner() call"
      pattern: "set_aligner\\("
---

<objective>
Verify and enhance model singleton cache with graceful failure handling

Purpose: Ensure the Qwen3 service loads the model exactly once at startup and handles failures gracefully without crashing the service. Increase concurrency limit for production throughput.
Output: Verified single model loading at startup with graceful failure handling
</objective>

<execution_context>
@/home/mhuang/.claude/get-shit-done/workflows/execute-plan.md
@/home/mhuang/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@services/qwen3/src/sow_qwen3/main.py
@services/qwen3/src/sow_qwen3/workers/aligner.py
@services/qwen3/src/sow_qwen3/routes/health.py
</context>

<tasks>

<task type="auto">
  <name>Verify model singleton loading at startup</name>
  <files>
    services/qwen3/src/sow_qwen3/main.py
    services/qwen3/src/sow_qwen3/workers/aligner.py
  </files>
  <action>
    Verify existing model loading implementation follows singleton pattern:

    1. Confirm main.py uses global aligner instance initialized in lifespan:
       - Check for global aligner variable
       - Check lifespan() calls aligner.initialize() during startup
       - Check aligner.cleanup() during shutdown
       - Verify aligner is set in health router via set_aligner()

    2. Confirm aligner.py loads model only once:
       - Check initialize() method loads model and sets _ready=True
       - Verify align() method checks _ready before processing
       - Verify model is stored in _model instance variable
       - Confirm no re-initialization in align()

    3. Check error handling in startup:
       - Verify exceptions during model loading propagate without crashing service
       - Confirm service continues startup lifecycle even if model fails to load

    The existing implementation should already meet PERFS-01 requirements. If any missing, add appropriate code to ensure:
    - Model loads once in lifespan
    - Startup blocks until model load completes
    - Service continues if model load fails (returns 503 on health check)
    - Model is not reloaded per request

    Note: Do not change the implementation if it already meets requirements. Only add if gaps exist.
  </action>
  <verify>grep -n "aligner.initialize()" services/qwen3/src/sow_qwen3/main.py shows model loaded in startup, grep -n "self._ready" services/qwen3/src/sow_qwen3/workers/aligner.py shows ready state tracking</verify>
  <done>Model singleton loading verified: loads once at startup, blocks until complete, service continues on failure</done>
</task>

<task type="auto">
  <name>Set concurrency limit to 2-3 for production</name>
  <files>services/qwen3/src/sow_qwen3/config.py</files>
  <action>
    Update MAX_CONCURRENT setting in pydantic-settings for production throughput:

    In services/qwen3/src/sow_qwen3/config.py:
    1. Change MAX_CONCURRENT default from 1 to 2
    2. Add comment explaining the tradeoff (2 allows concurrent processing, balances memory usage)

    Implementation:
    ```python
    # Concurrency
    MAX_CONCURRENT: int = 2  # Max concurrent alignments (2=balance throughput/memory, 3=higher throughput if memory permits)
    ```

    This change implements the locked decision from CONTEXT.md: "Limited concurrency: 2-3 concurrent alignment requests"
  </action>
  <verify>grep "MAX_CONCURRENT.*2" services/qwen3/src/sow_qwen3/config.py shows concurrency set to 2</verify>
  <done>Concurrency limit set to 2 for production throughput (configurable via SOW_QWEN3_MAX_CONCURRENT env var)</done>
</task>

</tasks>

<verification>
Verify model singleton and production settings:

1. Model loads once at startup:
```bash
# Check model loading code exists
grep -n "aligner.initialize()" services/qwen3/src/sow_qwen3/main.py
grep -n "self._ready" services/qwen3/src/sow_qwen3/workers/aligner.py
```

2. Concurrency set to 2:
```bash
grep "MAX_CONCURRENT" services/qwen3/src/sow_qwen3/config.py
```

3. Service can verify via startup logs (manual check):
```bash
cd services/qwen3 && uv run uvicorn sow_qwen3.main:app --reload
# Logs should show: "Loading Qwen3ForcedAligner model..." then "Qwen3ForcedAligner loaded and ready"
```
</verification>

<success_criteria>
1. Model loads once at startup verified in code
2. Startup blocks until model load completes (or fails gracefully)
3. Service continues if model load fails (health check returns 503)
4. MAX_CONCURRENT set to 2 for production (configurable via env var)
5. No per-request model loading in align() method
6. Health check correctly reflects model ready state
</success_criteria>

<output>
After completion, create `.planning/phases/05-performance-production-readiness/05-performance-production-readiness-01-SUMMARY.md`
</output>
