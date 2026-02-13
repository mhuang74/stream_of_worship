---
phase: 01-qwen3-service-foundation
plan: 02
type: execute
wave: 2
depends_on: ["01-qwen3-service-foundation-01"]
files_modified: [services/qwen3/src/sow_qwen3/workers/aligner.py, services/qwen3/src/sow_qwen3/routes/health.py, services/qwen3/src/sow_qwen3/main.py]
autonomous: true

must_haves:
  truths:
    - "Qwen3AlignerWrapper loads model at startup via lifespan"
    - "Health check returns 200 only when model is loaded and ready"
    - "Alignment uses asyncio.Semaphore for concurrency control"
    - "Model loading runs in thread pool to avoid blocking event loop"
  artifacts:
    - path: "services/qwen3/src/sow_qwen3/workers/aligner.py"
      provides: "Qwen3ForcedAligner wrapper with concurrency control"
      contains: "Qwen3AlignerWrapper", "initialize", "align", "Semaphore"
    - path: "services/qwen3/src/sow_qwen3/routes/health.py"
      provides: "Health check endpoint"
      contains: "health_check", "is_ready"
    - path: "services/qwen3/src/sow_qwen3/main.py"
      provides: "Lifespan that loads and cleans up model"
      contains: "lifespan", "aligner.initialize", "aligner.cleanup"
  key_links:
    - from: "services/qwen3/src/sow_qwen3/main.py"
      to: "services/qwen3/src/sow_qwen3/workers/aligner.py"
      via: "lifespan startup calls initialize()"
      pattern: "await aligner\.initialize\(\)"
    - from: "services/qwen3/src/sow_qwen3/main.py"
      to: "services/qwen3/src/sow_qwen3/routes/health.py"
      via: "health router includes"
      pattern: "app.include_router.*health"
    - from: "services/qwen3/src/sow_qwen3/routes/health.py"
      to: "services/qwen3/src/sow_qwen3/workers/aligner.py"
      via: "checks model ready state"
      pattern: "aligner\.is_ready"
---

<objective>
Implement the Qwen3AlignerWrapper for model loading with lifespan management and health check endpoint.

Purpose: Load the Qwen3-ForcedAligner-0.6B model at service startup using lifespan pattern, provide concurrency control via semaphore, and expose a health check endpoint that verifies model readiness. This ensures the service is stable, resource-efficient, and properly instrumented.

Output: Qwen3AlignerWrapper class with async initialization/cleanup, health check endpoint, and main.py integration.
</objective>

<execution_context>
@/home/mhuang/.claude/get-shit-done/workflows/execute-plan.md
@/home/mhuang/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/01-qwen3-service-foundation/01-qwen3-service-foundation-RESEARCH.md
@.planning/phases/01-qwen3-service-foundation/01-qwen3-service-foundation-CONTEXT.md

# Reference implementation from POC
@/home/mhuang/Development/stream_of_worship/poc/gen_lrc_qwen3.py

# Lifespan pattern from Analysis Service
@/home/mhuang/Development/stream_of_worship/services/analysis/src/sow_analysis/main.py
@/home/mhuang/Development/stream_of_worship/services/analysis/src/sow_analysis/routes/health.py

# Config from Plan 01
@services/qwen3/src/sow_qwen3/config.py
</context>

<tasks>

<task type="auto">
  <name>Task 1: Create Qwen3AlignerWrapper in workers/aligner.py</name>
  <files>services/qwen3/src/sow_qwen3/workers/aligner.py, services/qwen3/src/sow_qwen3/workers/__init__.py</files>
  <action>
Create services/qwen3/src/sow_qwen3/workers/ directory with aligner.py:

1. Create workers/__init__.py (empty or with exports)

2. Create workers/aligner.py with Qwen3AlignerWrapper class:

Class attributes:
- model_path: Path
- device: str
- dtype: str
- _model: Qwen3ForcedAligner | None = None
- _ready: bool = False
- _semaphore: asyncio.Semaphore

__init__: Accept model_path, device, max_concurrent (defaults to 1), create semaphore

initialize(): Async method
- Get event loop
- Define _load_model inner function that:
  - Imports torch, qwen_asr.Qwen3ForcedAligner
  - Auto-detect device if "auto": check torch.backends.mps.is_available(), torch.cuda.is_available()
  - Map dtype string to torch.dtype (bfloat16/float16/float32)
  - Load model: Qwen3ForcedAligner.from_pretrained(str(model_path), dtype=torch_dtype, device_map=device)
- Run _load_model in thread pool via loop.run_in_executor
- Set _model, _ready = True
- Log info message: "Qwen3ForcedAligner loaded and ready"

align(): Async method accepting audio_path, lyrics_text, language
- Check _ready, raise RuntimeError if False
- Acquire semaphore async with self._semaphore
- Define _call_align inner function that calls self._model.align(audio=str(audio_path), text=lyrics_text, language=language)
- Run in thread pool via loop.run_in_executor, return results

cleanup(): Async method
- Set _ready = False, _model = None
- Log info message

is_ready property: Return _ready

Following the RESEARCH.md Pattern 4 and POC gen_lrc_qwen3.py align_lyrics function.

DO NOT: Load model at module import time, use on_event decorators.
  </action>
  <verify>
PYTHONPATH=services/qwen3/src python3 -c "from sow_qwen3.workers.aligner import Qwen3AlignerWrapper; assert hasattr(Qwen3AlignerWrapper, 'initialize') and hasattr(Qwen3AlignerWrapper, 'align') and hasattr(Qwen3AlignerWrapper, 'is_ready')"
  </verify>
  <done>
Qwen3AlignerWrapper class created with initialize, align, cleanup, is_ready methods
  </done>
</task>

<task type="auto">
  <name>Task 2: Create health check endpoint in routes/health.py</name>
  <files>services/qwen3/src/sow_qwen3/routes/health.py, services/qwen3/src/sow_qwen3/routes/__init__.py</files>
  <action>
Create services/qwen3/src/sow_qwen3/routes/ directory with health.py:

1. Create routes/__init__.py (empty or with exports)

2. Create routes/health.py:

Import: logging, fastapi.APIRouter, fastapi.HTTPException

Import from .. import __version__, config.settings

Import from ..workers.aligner import get_aligner (will be created later, for now stub)

Define router = APIRouter()

Create function get_aligner() that returns a placeholder (will be implemented in main.py with global variable):
- For now, define get_aligner with docstring: """Dependency to get the aligner instance."""

@router.get("/health") async function health_check():
- Get aligner via get_aligner()
- If aligner is None or not aligner.is_ready: raise HTTPException(503, detail="Model not loaded")
- Return {"status": "healthy", "version": __version__, "model": "ready"}

Following the Analysis Service health.py pattern but for model readiness instead of R2/cache/LLM.

DO NOT include: check_r2_connection, check_llm_connection (Analysis Service specific).
  </action>
  <verify>
grep -q "health_check" services/qwen3/src/sow_qwen3/routes/health.py && grep -q "is_ready" services/qwen3/src/sow_qwen3/routes/health.py
  </verify>
  <done>
health.py exists with /health endpoint that checks model readiness
  </done>
</task>

<task type="auto">
  <name>Task 3: Integrate aligner into main.py lifespan and health router</name>
  <files>services/qwen3/src/sow_qwen3/main.py</files>
  <action>
Update services/qwen3/src/sow_qwen3/main.py to integrate aligner:

1. In imports: add from .workers.aligner import Qwen3AlignerWrapper

2. After imports, create global variable: aligner: Qwen3AlignerWrapper | None = None

3. In lifespan function startup:
   - Create Qwen3AlignerWrapper instance with model_path=settings.MODEL_PATH, device=settings.DEVICE, max_concurrent=settings.MAX_CONCURRENT
   - Call await aligner.initialize()
   - Log info message

4. In lifespan function shutdown:
   - If aligner: await aligner.cleanup()

5. After app creation, import and include health router:
   - from .routes import health
   - app.include_router(health.router, prefix="/api/v1")

6. In routes/health.py, update get_aligner():
   - Define global aligner variable imported from main
   - Make get_aligner() return the aligner instance
   - Use closure pattern: pass aligner to health module via set_aligner helper function

Alternative: Create set_aligner function in health.py that main.py calls during lifespan.

7. Update routes/__init__.py to export: from . import health

8. In main.py, after health router import, add: health.set_aligner(lambda: aligner)

Following the Analysis Service pattern for global state management (JobQueue + set_job_queue).
  </action>
  <verify>
cd services/qwen3 && PYTHONPATH=src python3 -c "from sow_qwen3.main import app; from fastapi.testclient import TestClient; tc = TestClient(app); assert '/api/v1/health' in [route.path for route in app.routes]"
  </verify>
  <done>
main.py lifespan loads and cleans up aligner, health router included, /api/v1/health endpoint accessible
  </done>
</task>

</tasks>

<verification>
- Verify Qwen3AlignerWrapper imports work: PYTHONPATH=services/qwen3/src python3 -c "from sow_qwen3.workers.aligner import Qwen3AlignerWrapper"
- Verify health router is included: check app.routes contains /api/v1/health
- Verify semantics: model loading in thread pool, semaphore for concurrency
</verification>

<success_criteria>
- Qwen3AlignerWrapper class created with initialize/cleanup/align methods and is_ready property
- main.py lifespan loads model at startup and cleans up at shutdown
- /api/v1/health endpoint returns 503 "Model not loaded" before model loads, 200 with "model": "ready" after
- Semaphore limits concurrent alignment requests to MAX_CONCURRENT
- No blocking operations in async context (model loading in thread pool)
</success_criteria>

<output>
After completion, create `.planning/phases/01-qwen3-service-foundation/01-qwen3-service-foundation-02-SUMMARY.md`
</output>
