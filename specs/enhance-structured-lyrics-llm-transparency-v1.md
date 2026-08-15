# Implementation Plan: Enhance Structured Lyrics LLM Transparency (v1)

> **Date:** 2026-08-15
> **Branch:** TBD
> **Spec ID:** `enhance-structured-lyrics-llm-transparency-v1`
> **Component:** Analysis Service (`ops/analysis-service/`)
> **Related:**
> - `specs/enhance-analysis-service-hang-debug-logging-v1.md` (step-level observability)
> - `specs/component-identification-structured-lyrics-v2.md` (structured lyrics alignment origin)
> - `specs/llm-rate-limit-retry-v4.md` (unified `call_llm_with_retry`)

---

## Problem

When the `structured_lyrics` segmentation mode runs, the LLM alignment step is a
black box. The logs show only:

```
INFO - [job_xxx] Structured lyrics LLM alignment completed in 15.98s (0 components)
WARNING - [job_xxx] segmentation_mode='structured_lyrics' requested but no components found; returning empty
```

There is **zero visibility** into:
1. What was sent to the LLM (model, system prompt, user message, few-shot
   examples, LRC content)
2. What the LLM returned (response text, token usage, finish reason)
3. **Why** parsing produced zero components — was it a JSON decode failure?
   Invalid labels? Out-of-range line numbers? Overlapping sections? An empty
   `sections` array?

The same opacity applies to the sibling LLM whole-song segmentation path in
`section_segmenter.py` (`segment_song`, `_sanity_check_llm`,
`_corrective_segmentation_call`), which has the same request/response/parse
flow and the same "returns empty list" silent failure mode.

### Evidence (from production logs)

```
analysis-dev-1  | 2026-08-15 02:38:44,065 - sow_analysis.workers.components - INFO - [job_747c1aa8dd63] Structured lyrics LLM alignment completed in 15.98s (0 components)
analysis-dev-1  | 2026-08-15 02:38:44,065 - sow_analysis.workers.components - WARNING - [job_747c1aa8dd63] segmentation_mode='structured_lyrics' requested but no components found; returning empty
```

The job took 15.98s on the LLM call, got a response, but produced 0 components.
The response was either unparseable or all sections were rejected — but no log
distinguishes these cases.

---

## Goal

Add DEBUG-level transparency to both LLM segmentation paths
(`structured_lyrics_aligner.py` and `section_segmenter.py`) so that an operator
can set `LOGLEVEL=DEBUG` and immediately see:
- The full LLM request (model, messages with few-shot summarized and LRC verbatim)
- The full LLM response (content text + token metadata + finish reason)
- A **parse-failure breakdown** explaining exactly why zero sections survived
  parsing

**Out of scope:**
- `classifier.py` theme/posture classification LLM calls (separate concern,
  covered by `enhance-analysis-service-hang-debug-logging-v1.md`)
- `lrc.py` YouTube transcript LLM correction calls
- `youtube_transcript.py` LLM retry calls

---

## Clarification decisions

| Question | Decision |
|----------|----------|
| Log level | **DEBUG** — off by default, opt-in via `LOGLEVEL=DEBUG`. Keeps production INFO logs clean. |
| Response scope | **Content + metadata** — log response text plus `prompt_tokens`, `completion_tokens`, `total_tokens`, `finish_reason`, `model`, `id`. |
| Parse-failure diagnostics | **Yes, add breakdown** — log specific rejection reasons when parsing returns None/empty. |
| Request truncation | **Summarize few-shot, full LRC** — few-shot examples as one-line summary; full numbered LRC and structured lyrics sections logged verbatim (the actual alignment input). |
| Module scope | **`structured_lyrics_aligner.py` + `section_segmenter.py`** — both LLM segmentation paths. |

---

## Critical files

### Modified files

| File | Changes |
|------|---------|
| `ops/analysis-service/src/sow_analysis/workers/structured_lyrics_aligner.py` | Add DEBUG logging in `align_structured_lyrics()` (request/response), enhance `_parse_alignment_json()` to return rejection breakdown |
| `ops/analysis-service/src/sow_analysis/workers/section_segmenter.py` | Add DEBUG logging in `segment_song()`, `_sanity_check_llm()`, `_corrective_segmentation_call()` (request/response), enhance `_parse_segmenter_json()` to return rejection breakdown |
| `ops/analysis-service/tests/test_structured_lyrics_aligner.py` | Add tests for DEBUG logging and parse-failure breakdown |
| `ops/analysis-service/tests/test_section_segmenter.py` | Add tests for DEBUG logging and parse-failure breakdown (if test file exists; otherwise create) |

### No new files

No new modules or config settings needed. DEBUG logging is controlled by the
existing `LOGLEVEL` environment variable (already wired in `logging_config.py`).

---

## Design

### 1. LLM request logging (DEBUG)

**Applies to:** `align_structured_lyrics()`, `segment_song()`,
`_sanity_check_llm()`, `_corrective_segmentation_call()`

Before each `call_llm_with_retry(_call, ...)` invocation, log the request at
DEBUG:

```python
logger.debug(
    "LLM request [%s]: model=%s, system_prompt=%d chars, "
    "user_message=%d chars (few_shot: %d examples, ~%d chars; "
    "numbered_lrc: %d lines, %d chars; structured_sections: %d chars)",
    description,
    model,
    len(system_prompt),
    len(user_message),
    len(few_shot),
    sum(len(json.dumps(ex, ensure_ascii=False)) for ex in few_shot),
    n_lrc_lines,
    len(numbered_lrc),
    len(structured_text),
)
logger.debug("LLM request [%s] user message:\n%s", description, user_message)
```

**For `structured_lyrics_aligner.py`:**
- Log the full `user_message` verbatim (contains numbered LRC + structured
  sections — the actual alignment input)
- Log few-shot as a summary line (count + total char size), not verbatim
- Log system prompt character count (system prompt is a static constant, not
  worth dumping every time)

**For `section_segmenter.py`:**
- Same pattern for `segment_song()` (primary segmentation call)
- For `_sanity_check_llm()`: log the full prompt (it's short — numbered LRC +
  proposed sections JSON)
- For `_corrective_segmentation_call()`: log the full prompt (numbered LRC +
  rejected sections + rationale)

### 2. LLM response logging (DEBUG)

**Applies to:** all four LLM call sites

After `call_llm_with_retry` returns, log the response at DEBUG. Since
`call_llm_with_retry` returns only the response text (not the full SDK response
object), the `_call()` closure must be modified to capture the full response
and log metadata.

**Approach:** Modify each `_call()` closure to capture the `resp` object and
log metadata before returning the text:

```python
def _call() -> str:
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
        response_format={"type": "json_object"},
        max_tokens=settings.SOW_LLM_SEGMENTATION_MAX_TOKENS,
    )
    usage = resp.usage
    logger.debug(
        "LLM response [%s]: model=%s, finish_reason=%s, "
        "prompt_tokens=%s, completion_tokens=%s, total_tokens=%s, "
        "response_id=%s, content_length=%d chars",
        description,
        resp.model,
        resp.choices[0].finish_reason,
        usage.prompt_tokens if usage else "N/A",
        usage.completion_tokens if usage else "N/A",
        usage.total_tokens if usage else "N/A",
        resp.id,
        len(resp.choices[0].message.content or ""),
    )
    logger.debug("LLM response [%s] content:\n%s", description, resp.choices[0].message.content or "")
    return resp.choices[0].message.content or ""
```

**Note:** `resp.model` may differ from the requested `model` (e.g., provider
aliases). Logging the actual response model helps diagnose model-mismatch
issues.

**For `_sanity_check_llm()` and `_corrective_segmentation_call()`:** Same
pattern. The `description` strings ("LLM segmentation sanity check" / "LLM
segmentation corrective call") already exist and will be reused as log labels.

### 3. Parse-failure breakdown for `_parse_alignment_json()`

**Current behavior:** Returns `None` silently when parsing fails or all
sections are rejected.

**New behavior:** Return a tuple `(Optional[list[Section]], str)` where the
second element is a human-readable breakdown of rejection reasons. Callers log
it at WARNING when sections is None.

**Detailed breakdown logic:**

```python
def _parse_alignment_json(
    response_text: str, n_lines: int
) -> tuple[Optional[list[Section]], str]:
    """Parse alignment JSON, returning sections and a failure breakdown.

    Returns:
        (sections, breakdown) where sections is None if parsing failed,
        and breakdown is a human-readable string explaining the failure
        or success summary.
    """
    try:
        data = json.loads(response_text)
    except (json.JSONDecodeError, TypeError) as e:
        return None, f"JSON decode failed: {e}"

    if not isinstance(data, dict):
        return None, f"Response is not a JSON object (type={type(data).__name__})"

    sections_list = data.get("sections")
    if not isinstance(sections_list, list):
        return None, "'sections' key missing or not a list"
    if not sections_list:
        return None, "'sections' array is empty"

    # Track rejection reasons
    rejected_reasons: list[str] = []
    raw_sections: list[Section] = []

    for i, raw in enumerate(sections_list):
        if not isinstance(raw, dict):
            rejected_reasons.append(f"section[{i}]: not a dict")
            continue
        label = str(raw.get("label", "")).lower().strip()
        original_label = label
        if label not in _VALID_LABELS:
            label = _LABEL_NORMALIZATION_MAP.get(label, label)
        if label not in _VALID_LABELS:
            rejected_reasons.append(
                f"section[{i}]: invalid label '{original_label}'"
            )
            continue
        try:
            line_start = int(raw["line_start"])
            line_end = int(raw["line_end"])
            confidence = float(raw.get("confidence", 0.5))
        except (KeyError, TypeError, ValueError) as e:
            rejected_reasons.append(
                f"section[{i}] label='{label}': missing/invalid "
                f"line_start/line_end/confidence ({e})"
            )
            continue
        if not (1 <= line_start <= line_end <= n_lines):
            rejected_reasons.append(
                f"section[{i}] label='{label}': out of range "
                f"(line_start={line_start}, line_end={line_end}, n_lines={n_lines})"
            )
            continue
        raw_sections.append(
            Section(label=label, line_start=line_start, line_end=line_end,
                    confidence=max(0.0, min(1.0, confidence)),
                    rationale=raw.get("rationale"))
        )

    if not raw_sections:
        return None, f"All {len(sections_list)} sections rejected: {'; '.join(rejected_reasons)}"

    raw_sections.sort(key=lambda s: s.line_start)

    accepted: list[Section] = []
    seen_ranges: set[tuple[int, int]] = set()
    overlap_rejects = 0
    dup_rejects = 0
    for sec in raw_sections:
        overlaps = False
        for acc in accepted:
            if sec.line_start <= acc.line_end and sec.line_end >= acc.line_start:
                overlaps = True
                break
        if overlaps:
            overlap_rejects += 1
            rejected_reasons.append(
                f"section label='{sec.label}' lines={sec.line_start}-{sec.line_end}: overlaps existing"
            )
            continue
        if (sec.line_start, sec.line_end) in seen_ranges:
            dup_rejects += 1
            rejected_reasons.append(
                f"section label='{sec.label}' lines={sec.line_start}-{sec.line_end}: duplicate range"
            )
            continue
        accepted.append(sec)
        seen_ranges.add((sec.line_start, sec.line_end))

    if not accepted:
        return None, (
            f"All {len(raw_sections)} post-sort sections rejected "
            f"(overlaps={overlap_rejects}, duplicates={dup_rejects}): "
            f"{'; '.join(rejected_reasons)}"
        )

    breakdown = (
        f"Parsed {len(accepted)} sections from {len(sections_list)} raw "
        f"({len(rejected_reasons)} rejected"
        + (f": {'; '.join(rejected_reasons)}" if rejected_reasons else "")
        + ")"
    )
    return accepted, breakdown
```

**Caller change in `align_structured_lyrics()`:**

```python
text = await call_llm_with_retry(_call, description="LLM structured lyrics alignment")
_numbered, n_lines = _render_numbered_lrc(lrc_content)
sections, parse_breakdown = _parse_alignment_json(text, n_lines)
if sections is None:
    logger.warning(
        "Structured lyrics alignment parse failed: %s", parse_breakdown
    )
    return []
# ... rest of function
```

Also add a DEBUG log after successful parse:
```python
logger.debug("Structured lyrics alignment parse: %s", parse_breakdown)
```

### 4. Parse-failure breakdown for `_parse_segmenter_json()`

**Current behavior:** Returns `None` silently. Unlike `_parse_alignment_json`,
this parser is **strict** — it returns `None` on the *first* invalid section
rather than skipping.

**New behavior:** Same tuple pattern `(Optional[list[Section]], str)`.

```python
def _parse_segmenter_json(
    response_text: str, n_lines: int
) -> tuple[Optional[list[Section]], str]:
    try:
        data = json.loads(response_text)
    except (json.JSONDecodeError, TypeError) as e:
        return None, f"JSON decode failed: {e}"
    if not isinstance(data, dict):
        return None, f"Response is not a JSON object (type={type(data).__name__})"
    sections_list = data.get("sections")
    if not isinstance(sections_list, list):
        return None, "'sections' key missing or not a list"
    if not sections_list:
        return None, "'sections' array is empty"

    sections: list[Section] = []
    prev_end = 0
    seen_ranges: set[tuple[int, int]] = set()
    for i, raw in enumerate(sections_list):
        if not isinstance(raw, dict):
            return None, f"section[{i}]: not a dict"
        label = str(raw.get("label", "")).lower()
        if label not in _VALID_LABELS:
            return None, f"section[{i}]: invalid label '{label}'"
        try:
            line_start = int(raw["line_start"])
            line_end = int(raw["line_end"])
            confidence = float(raw.get("confidence", 0.5))
        except (KeyError, TypeError, ValueError) as e:
            return None, f"section[{i}] label='{label}': missing/invalid line_start/line_end/confidence ({e})"
        if not (1 <= line_start <= line_end <= n_lines):
            return None, f"section[{i}] label='{label}': out of range (line_start={line_start}, line_end={line_end}, n_lines={n_lines})"
        if line_start <= prev_end:
            return None, f"section[{i}] label='{label}': overlap (line_start={line_start} <= prev_end={prev_end})"
        if line_start > prev_end + 1:
            return None, f"section[{i}] label='{label}': gap (line_start={line_start} > prev_end+1={prev_end + 1})"
        if (line_start, line_end) in seen_ranges:
            return None, f"section[{i}] label='{label}': duplicate range ({line_start}-{line_end})"
        prev_end = line_end
        seen_ranges.add((line_start, line_end))
        sections.append(
            Section(label=label, line_start=line_start, line_end=line_end,
                    confidence=max(0.0, min(1.0, confidence)),
                    rationale=raw.get("rationale"))
        )
    if not sections:
        return None, "No valid sections after parsing"
    return sections, f"Parsed {len(sections)} sections (strict mode)"
```

**Caller changes:**
- `segment_song()` line 639: `sections, parse_breakdown = _parse_segmenter_json(text, n_lines)`
  → log warning on None, debug on success
- `_corrective_segmentation_call()` line 600: same pattern

### 5. No config changes needed

DEBUG logging is already controlled by the existing `LOGLEVEL` environment
variable. No new config setting is needed. Operators set `LOGLEVEL=DEBUG` in
the Docker environment to see the LLM transparency logs.

### 6. No schema or API changes

This is a logging-only change. No changes to:
- `ComponentAnalysisJobRequest` / `ComponentAnalysisOptions` (models.py)
- `ComponentResult` / `JobResult` (models.py)
- HTTP API endpoints
- R2 `components.json` format
- `COMPONENT_SCHEMA_VERSION`

---

## Changes by file

### `structured_lyrics_aligner.py`

| Line(s) | Change |
|---------|--------|
| 150-224 | Rewrite `_parse_alignment_json()` to return `tuple[Optional[list[Section]], str]` with rejection breakdown |
| 285-286 | Add DEBUG log of request summary (model, message sizes, few-shot summary, LRC line count) |
| 285-286 | Add DEBUG log of full user message (numbered LRC + structured sections verbatim) |
| 287-295 | Modify `_call()` closure to capture `resp` object and log response metadata + content at DEBUG |
| 301-303 | Change to unpack tuple from `_parse_alignment_json`, log WARNING with breakdown on None, DEBUG on success |

### `section_segmenter.py`

| Line(s) | Change |
|---------|--------|
| 152-194 | Rewrite `_parse_segmenter_json()` to return `tuple[Optional[list[Section]], str]` with rejection breakdown |
| 622-637 | Add DEBUG request/response logging in `segment_song()` `_call()` closure |
| 639-641 | Change to unpack tuple from `_parse_segmenter_json`, log WARNING with breakdown on None, DEBUG on success |
| 515-531 | Add DEBUG request/response logging in `_sanity_check_llm()` `_call()` closure, log parse failure |
| 532-538 | Change `json.loads` to include breakdown message on failure |
| 584-600 | Add DEBUG request/response logging in `_corrective_segmentation_call()` `_call()` closure |
| 600 | Change to unpack tuple from `_parse_segmenter_json`, log WARNING with breakdown on None |

---

## Testing notes

### Unit tests for parse-failure breakdown

**`test_structured_lyrics_aligner.py`:**

```python
class TestParseAlignmentJsonBreakdown:
    def test_json_decode_failure_returns_breakdown(self):
        sections, breakdown = _parse_alignment_json("not json", 10)
        assert sections is None
        assert "JSON decode failed" in breakdown

    def test_empty_sections_returns_breakdown(self):
        sections, breakdown = _parse_alignment_json('{"sections": []}', 10)
        assert sections is None
        assert "empty" in breakdown

    def test_invalid_label_rejected_with_reason(self):
        resp = json.dumps({"sections": [{"label": "solo", "line_start": 1, "line_end": 3}]})
        sections, breakdown = _parse_alignment_json(resp, 10)
        assert sections is None
        assert "invalid label" in breakdown
        assert "solo" in breakdown

    def test_out_of_range_rejected_with_reason(self):
        resp = json.dumps({"sections": [{"label": "verse", "line_start": 5, "line_end": 15}]})
        sections, breakdown = _parse_alignment_json(resp, 10)
        assert sections is None
        assert "out of range" in breakdown
        assert "n_lines=10" in breakdown

    def test_overlap_rejected_with_reason(self):
        resp = json.dumps({"sections": [
            {"label": "verse", "line_start": 1, "line_end": 5, "confidence": 0.9},
            {"label": "chorus", "line_start": 3, "line_end": 8, "confidence": 0.9},
        ]})
        sections, breakdown = _parse_alignment_json(resp, 10)
        assert len(sections) == 1  # first accepted, second rejected
        assert "overlaps" in breakdown

    def test_successful_parse_returns_breakdown(self):
        resp = json.dumps({"sections": [
            {"label": "verse", "line_start": 1, "line_end": 3, "confidence": 0.9},
            {"label": "chorus", "line_start": 4, "line_end": 8, "confidence": 0.9},
        ]})
        sections, breakdown = _parse_alignment_json(resp, 10)
        assert len(sections) == 2
        assert "Parsed 2 sections" in breakdown
```

**`test_section_segmenter.py`** (mirror tests for strict parser):

```python
class TestParseSegmenterJsonBreakdown:
    def test_json_decode_failure_returns_breakdown(self):
        sections, breakdown = _parse_segmenter_json("not json", 10)
        assert sections is None
        assert "JSON decode failed" in breakdown

    def test_strict_mode_returns_on_first_failure(self):
        resp = json.dumps({"sections": [
            {"label": "verse", "line_start": 1, "line_end": 3},
            {"label": "solo", "line_start": 4, "line_end": 8},  # invalid label
            {"label": "chorus", "line_start": 9, "line_end": 10},
        ]})
        sections, breakdown = _parse_segmenter_json(resp, 10)
        assert sections is None
        assert "section[1]" in breakdown
        assert "invalid label" in breakdown

    def test_gap_rejected_with_reason(self):
        resp = json.dumps({"sections": [
            {"label": "verse", "line_start": 1, "line_end": 3},
            {"label": "chorus", "line_start": 6, "line_end": 8},  # gap at 4-5
        ]})
        sections, breakdown = _parse_segmenter_json(resp, 10)
        assert sections is None
        assert "gap" in breakdown
```

### Unit tests for DEBUG logging

```python
class TestLlmRequestResponseLogging:
    def test_request_logged_at_debug(self, caplog):
        # Mock call_llm_with_retry to return a fixed response
        # Verify DEBUG log contains model, message sizes, few-shot summary
        ...

    def test_response_logged_at_debug(self, caplog):
        # Mock OpenAI client to return a canned response with usage metadata
        # Verify DEBUG log contains prompt_tokens, completion_tokens, finish_reason
        ...

    def test_parse_failure_logged_at_warning(self, caplog):
        # Mock LLM to return an unparseable response
        # Verify WARNING log contains the breakdown string
        ...
```

### Verification commands

```bash
cd ops/analysis-service

# Run existing tests to ensure no regressions from tuple return change
uv run --extra dev pytest tests/test_structured_lyrics_aligner.py -v

# Run section_segmenter tests
uv run --extra dev pytest tests/test_section_segmenter.py -v

# Run all tests
uv run --extra dev pytest tests/ -v
```

### Manual verification with DEBUG logging

```bash
# In docker-compose.override.yml or environment:
LOGLEVEL=DEBUG

# Run a component analysis job with structured_lyrics mode
# Verify logs show:
# 1. LLM request summary (model, sizes, few-shot count)
# 2. LLM request user message (numbered LRC + structured sections verbatim)
# 3. LLM response metadata (tokens, finish_reason, model)
# 4. LLM response content (raw JSON)
# 5. Parse breakdown (accepted count + rejection reasons, or failure breakdown)
```

---

## Migration notes

- **No data migration** — logging-only change, no schema/API changes
- **No config migration** — uses existing `LOGLEVEL`
- **Return type change** — `_parse_alignment_json` and `_parse_segmenter_json`
  change from `Optional[list[Section]]` to
  `tuple[Optional[list[Section]], str]`. All callers (4 sites total) must be
  updated. No external consumers — these are private module functions.

---

## Rollout strategy

1. Implement changes in `structured_lyrics_aligner.py` and
   `section_segmenter.py`
2. Update/add tests in `test_structured_lyrics_aligner.py` and
   `test_section_segmenter.py`
3. Run `uv run --extra dev pytest tests/ -v` to verify no regressions
4. Deploy to dev, set `LOGLEVEL=DEBUG`, run a component analysis job with
   `segmentation_mode=structured_lyrics`
5. Verify the DEBUG logs appear and are actionable
6. Deploy to production (DEBUG logs are dormant unless `LOGLEVEL=DEBUG` is set)
