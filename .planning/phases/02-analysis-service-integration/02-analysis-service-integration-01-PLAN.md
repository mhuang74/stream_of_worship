---
phase: 02-analysis-service-integration
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - services/analysis/src/sow_analysis/services/qwen3_client.py
  - services/analysis/src/sow_analysis/services/__init__.py
  - services/analysis/src/sow_analysis/models.py
autonomous: true

must_haves:
  truths:
    - "Analysis Service has Qwen3Client HTTP client for calling Qwen3 service"
    - "LrcOptions has use_qwen3 flag (default: true when available)"
  artifacts:
    - path: "services/analysis/src/sow_analysis/services/qwen3_client.py"
      provides: "HTTP client for Qwen3 align endpoint"
      min_lines: 30
      exports: ["Qwen3Client", "AlignRequest", "AlignResponse"]
    - path: "services/analysis/src/sow_analysis/models.py"
      provides: "LrcOptions with use_qwen3 flag"
      contains: "use_qwen3"
  key_links:
    - from: "services/analysis/src/sow_analysis/services/qwen3_client.py"
      to: "http://qwen3:8000/api/v1/align"
      via: "httpx.AsyncClient POST request"
      pattern: "httpx.*POST.*align"
---

# Objective

Create the foundational HTTP client for Qwen3 service integration and add the use_qwen3 configuration flag to LrcOptions.

Purpose: Enable Analysis Service to make HTTP requests to the Qwen3 Alignment Service for timestamp refinement of LRC files generated via Whisper transcription.

Output:
- Qwen3Client HTTP client module with async align() method
- Updated LrcOptions dataclass with use_qwen3 field

<execution_context>
@/home/mhuang/.claude/get-shit-done/workflows/execute-plan.md
@/home/mhuang/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md

# Reference: Phase 1 Qwen3 Service API contract
@services/qwen3/src/sow_qwen3/models.py

# Reference: Existing LrcOptions model (add use_qwen3 field)
@services/analysis/src/sow_analysis/models.py

# Reference: OpenAI client pattern (for HTTP calling)
@services/analysis/src/sow_analysis/workers/lrc.py
</context>

<tasks>

<task type="auto">
  <name>Create Qwen3Client HTTP client</name>
  <files>services/analysis/src/sow_analysis/services/qwen3_client.py, services/analysis/src/sow_analysis/services/__init__.py</files>
  <action>
    Create new file `services/analysis/src/sow_analysis/services/qwen3_client.py` with:

    1. Pydantic models matching Qwen3 API contract:
       - `AlignRequest` (audio_url: str, lyrics_text: str, output_format: str = "lrc")
       - `AlignResponse` (output: str)

    2. `Qwen3Client` class with:
       - `__init__(base_url: str, api_key: Optional[str] = None)` - Store base URL and optional API key
       - `async def align(audio_url: str, lyrics_text: str, output_format: str = "lrc") -> str` - Make POST request to /api/v1/align
         - Use httpx.AsyncClient for async HTTP calls (already in analysis service dependencies)
         - Set Authorization header if api_key is provided: {"Authorization": f"Bearer {self.api_key}"}
         - Return response.text (direct LRC content)
         - Raise `Qwen3ClientError` on HTTP errors (custom exception at module level)

    3. Custom exception `Qwen3ClientError(Exception)`

    4. Update `services/analysis/src/sow_analysis/services/__init__.py` to export: Qwen3Client, AlignRequest, AlignResponse

    5. In the services/__init__.py file, if it doesn't exist yet, create it to export the new client classes.

    IMPORTANT: Follow existing LLM calling pattern from lrc.py (use loop.run_in_executor, handle exceptions properly).
  </action>
  <verify>
    Verify file exists with:
    - `ls services/analysis/src/sow_analysis/services/qwen3_client.py`
    - `grep -c "class Qwen3Client" services/analysis/src/sow_analysis/services/qwen3_client.py`
    - `grep -c "AlignRequest\|AlignResponse" services/analysis/src/sow_analysis/services/qwen3_client.py`
    - `grep -c "httpx.AsyncClient" services/analysis/src/sow_analysis/services/qwen3_client.py`
  </verify>
  <done>
    Qwen3Client module exists with align() method, Pydantic models match Qwen3 API contract, exception handling defined.
  </done>
</task>

<task type="auto">
  <name>Add use_qwen3 flag to LrcOptions</name>
  <files>services/analysis/src/sow_analysis/models.py</files>
  <action>
    Modify `LrcOptions` dataclass in `services/analysis/src/sow_analysis/models.py`:

    Add new field `use_qwen3: bool = True` with default value True.

    Place this field after `force_whisper` field, maintaining Pydantic model structure.

    Keep all existing fields unchanged: whisper_model, llm_model, use_vocals_stem, language, force, force_whisper.

    This flag will be checked in LRC worker to decide whether to call Qwen3 service for timestamp refinement (Whisper path) or skip it (YouTube path).
  </action>
  <verify>
    Verify flag exists:
    - `grep "use_qwen3.*bool.*True" services/analysis/src/sow_analysis/models.py`
  </verify>
  <done>
    LrcOptions has use_qwen3 field with default True, accessible from Admin CLI via LrcOptions model.
  </done>
</task>

</tasks>

<verification>

After plan completion, verify:
1. Qwen3Client can be imported and instantiated
2. LrcOptions has use_qwen3 field
3. No linting errors in modified files

Run: `PYTHONPATH=services/analysis/src uv run --extra test python -c "from sow_analysis.services import Qwen3Client, AlignRequest, AlignResponse; from sow_analysis.models import LrcOptions; print('Imports successful')"`

</verification>

<success_criteria>

Plan is successful when:
- Qwen3Client module exists with async align() method calling http://qwen3:8000/api/v1/align
- AlignRequest and AlignResponse Pydantic models match Qwen3 API contract
- Qwen3ClientError exception defined
- LrcOptions has use_qwen3: bool = True field
- All imports verified without errors

</success_criteria>

<output>

After completion, create `.planning/phases/02-analysis-service-integration/02-analysis-service-integration-01-SUMMARY.md`

</output>
