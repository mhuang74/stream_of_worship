---
phase: 01-qwen3-service-foundation
plan: 01
type: execute
wave: 1
depends_on: []
files_modified: [services/qwen3/pyproject.toml, services/qwen3/src/sow_qwen3/config.py, services/qwen3/src/sow_qwen3/__init__.py, services/qwen3/src/sow_qwen3/main.py]
autonomous: true

must_haves:
  truths:
    - "pyproject.toml includes fastapi, uvicorn, pydantic-settings, qwen-asr, pydub, boto3"
    - "config.py uses pydantic-settings.BaseSettings with SOW_QWEN3_ prefix"
    - "main.py has FastAPI app with lifespan context manager"
  artifacts:
    - path: "services/qwen3/pyproject.toml"
      provides: "Package dependencies and configuration"
      contains: "qwen-asr", "fastapi", "pydantic-settings"
    - path: "services/qwen3/src/sow_qwen3/config.py"
      provides: "Environment-based configuration"
      contains: "Settings", "SOW_QWEN3_"
    - path: "services/qwen3/src/sow_qwen3/main.py"
      provides: "FastAPI application entry point"
      contains: "@asynccontextmanager", "lifespan", "FastAPI"
  key_links:
    - from: "services/qwen3/pyproject.toml"
      to: "services/qwen3/src/sow_qwen3/main.py"
      via: "uvicorn entry point"
      pattern: "sow-qwen3 = .*main:main"
---

<objective>
Create the Qwen3 Alignment Service project structure, dependencies, and configuration.

Purpose: Establish the foundation for a standalone FastAPI microservice that will load the Qwen3ForcedAligner model and expose an alignment API. Following the existing Analysis Service pattern ensures consistency across the codebase.

Output: services/qwen3/ package directory with pyproject.toml, config.py, __init__.py, and main.py skeleton.
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

# Reference patterns from existing Analysis Service
@/home/mhuang/Development/stream_of_worship/services/analysis/pyproject.toml
@/home/mhuang/Development/stream_of_worship/services/analysis/src/sow_analysis/config.py
@/home/mhuang/Development/stream_of_worship/services/analysis/src/sow_analysis/main.py
</context>

<tasks>

<task type="auto">
  <name>Task 1: Create project structure and pyproject.toml</name>
  <files>services/qwen3/pyproject.toml, services/qwen3/src/sow_qwen3/__init__.py</files>
  <action>
Create the services/qwen3/ directory structure and pyproject.toml:

1. Create directory: services/qwen3/src/sow_qwen3/

2. Create services/qwen3/pyproject.toml with:
   - Package name: sow-qwen3
   - Version: 0.1.0
   - Python: >=3.11,<3.12
   - Dependencies: fastapi>=0.109.0, uvicorn[standard]>=0.27.0, pydantic>=2.0.0, pydantic-settings>=2.0.0, qwen-asr>=0.1.0, pydub>=0.25.0, boto3>=1.34.0
   - Optional dependencies: service (python-multipart>=0.0.6)
   - Scripts: sow-qwen3 = "sow_qwen3.main:main"
   - Build system: setuptools with src layout

3. Create services/qwen3/src/sow_qwen3/__init__.py with __version__ = "0.1.0"

Following the Analysis Service pyproject.toml pattern (services/analysis/pyproject.toml) but with qwen-asr instead of allin1/madmom.

DO NOT include: allin1, madmom, demucs, librosa, faster-whisper, openai, aiosqlite, youtube-transcript-api (those are for Analysis Service only).
  </action>
  <verify>
cd services/qwen3 && uv export && grep -q "qwen-asr" && grep -q "fastapi" services/qwen3/pyproject.toml
  </verify>
  <done>
pyproject.toml exists with correct dependencies, uv install succeeds, src/sow_qwen3/__init__.py has __version__
  </done>
</task>

<task type="auto">
  <name>Task 2: Create config.py with pydantic-settings</name>
  <files>services/qwen3/src/sow_qwen3/config.py</files>
  <action>
Create services/qwen3/src/sow_qwen3/config.py using pydantic-settings pattern from Analysis Service:

Class Settings with:
- model_config: SettingsConfigDict(env_file=".env", case_sensitive=True, env_prefix="SOW_QWEN3_")
- MODEL_PATH: Path = Path("/models/qwen3-forced-aligner")
- DEVICE: str = "auto" (auto/mps/cuda/cpu)
- DTYPE: str = "float32" (bfloat16/float16/float32)
- MAX_CONCURRENT: int = 1 (max concurrent alignments)
- R2_BUCKET: str = "" (for audio download)
- R2_ENDPOINT_URL: str = ""
- R2_ACCESS_KEY_ID: str = ""
- R2_SECRET_ACCESS_KEY: str = ""
- API_KEY: str = "" (for API authentication)
- CACHE_DIR: Path = Path("/cache")

Export settings = Settings()

DO NOT include: SOW_LLM_*, SOW_WHISPER_*, SOW_DEMUCS_* (Analysis Service specific).
  </action>
  <verify>
python3 -c "from pathlib import Path; from pydantic_settings import BaseSettings, SettingsConfigDict; assert 'Settings' in open('services/qwen3/src/sow_qwen3/config.py').read() and 'SOW_QWEN3_' in open('services/qwen3/src/sow_qwen3/config.py').read() and 'DEVICE' in open('services/qwen3/src/sow_qwen3/config.py').read()" 2>&1 || grep -q "DEVICE.*auto" services/qwen3/src/sow_qwen3/config.py
  </verify>
  <done>
config.py exists, has Settings class with all required fields, env_prefix is SOW_QWEN3_
  </done>
</task>

<task type="auto">
  <name>Task 3: Create main.py with FastAPI app and lifespan</name>
  <files>services/qwen3/src/sow_qwen3/main.py</files>
  <action>
Create services/qwen3/src/sow_qwen3/main.py following Analysis Service pattern:

1. Import: asyncio, logging, contextlib.asynccontextmanager, fastapi.FastAPI

2. Configure logging to INFO level with timestamp format

3. Import: __version__, config.settings

4. Create global variable: aligner: Qwen3AlignerWrapper | None = None (placeholder for next plan)

5. Define @asynccontextmanager async function lifespan(app: FastAPI):
   - Startup: Initialize aligner (will be implemented in next plan), for now add comment "# Initialize aligner (implemented in plan 02)"
   - Yield
   - Shutdown: Clean up aligner, add comment "# Clean up aligner (implemented in plan 02)"

6. Create FastAPI app with:
   - title: "Stream of Worship Qwen3 Alignment Service"
   - version: __version__
   - lifespan: lifespan

7. Add route include placeholder: "# Include routers (implemented in plans 02-03)"

8. Add root endpoint GET / returning {"message": "Stream of Worship Qwen3 Alignment Service", "version": __version__}

9. Define main() function with uvicorn.run("sow_qwen3.main:app", host="0.0.0.0", port=8000, reload=False)

10. Add if __name__ == "__main__": main()

DO NOT include: JobQueue, background tasks (Analysis Service specific).
  </action>
  <verify>
cd services/qwen3 && PYTHONPATH=src python3 -c "from sow_qwen3.main import app; assert app.title == 'Stream of Worship Qwen3 Alignment Service'"
  </verify>
  <done>
main.py exists, FastAPI app created with lifespan, root endpoint returns correct message
  </done>
</task>

</tasks>

<verification>
- Verify uv install succeeds with no dependency conflicts
- Verify main.py imports work: PYTHONPATH=services/qwen3/src python3 -c "from sow_qwen3.main import app"
- Verify FastAPI app starts: uvicorn sow_qwen3.main:app --host 127.0.0.1 --port 8000 (test start/stop)
</verification>

<success_criteria>
- services/qwen3/pyproject.toml created with all required dependencies
- services/qwen3/src/sow_qwen3/config.py created with Settings class and SOW_QWEN3_ prefix
- services/qwen3/src/sow_qwen3/main.py created with FastAPI app and lifespan
- FastAPI app imports and starts successfully (no runtime errors on import or startup)
- All files follow Analysis Service patterns for consistency
</success_criteria>

<output>
After completion, create `.planning/phases/01-qwen3-service-foundation/01-qwen3-service-foundation-01-SUMMARY.md`
</output>
