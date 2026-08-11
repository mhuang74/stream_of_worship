# Implementation Spec: Enhance Audio Components Compute Flags and JSON Format (v2)

> **Date:** 2026-08-11
> **Spec ID:** `enhance-audio-components-compute-all-fields-and-json-format-v2`

---

## Changes from v1
This is a revised version of `enhance-audio-components-compute-all-fields-and-json-format.md`. Key revisions include:
- **stdout Purity:** Introduced `progress_console = Console(stderr=True)` to prevent status/progress messages from polluting JSON output.
- **Bug Fixes:** Resolved the `--no-wait` batch mode bug where successful submissions were marked as failures.
- **Python Conventions:** Corrected `_validate_choice` usage (call for side effect, not assignment) and renamed parameter to `format_` to avoid shadowing built-ins.
- **Strict JSON Logic:** Defined explicit behavior for empty component lists (`[]`) and removed DB reloading in JSON mode for consistency.
- **Exit Codes:** Defined a clear exit code policy for both single-song and batch operations.

---

## Problem Statement

The `sow-admin audio components` command currently requires users to manually specify multiple flags to enable advanced feature computation. This is tedious for users wanting a "full" analysis. Furthermore, the default table output is often too wide for mobile SSH terminals, and there is no machine-readable output format available for integration or easier inspection of the 27 available `SongComponent` fields.

---

## Design Decisions

### 1. `--compute-all-fields` Flag
- **Behavior:** Sets `snap_to_downbeat`, `energy_roles`, `classify_theme`, and `classify_posture` to `True`.
- **Exclusion:** `--use-stems` is explicitly excluded from the shortcut but can be combined with it.
- **Precedence:** Overrides individual flags to `True` if enabled.

### 2. `--format` Option
- **Parameter Name:** Use `format_` (trailing underscore) in Python code to match the project convention in `maintenance.py`.
- **Validation:** Call `_validate_choice(format_, COMPONENTS_FORMAT_VALUES, "--format")` for its side effect.
- **JSON Implementation:** Output `SongComponent.to_dict()` results as JSON arrays to `stdout`.

### 3. IO Strategy & stdout Purity
- **Console Routing:** To ensure `stdout` contains ONLY valid JSON when `--format json` is used, all human-facing progress/error messages must be routed to `stderr`.
- **Implementation:** Introduce a module-level `progress_console = Console(stderr=True)` in `audio.py`. This console will be passed to `_submit_component_analysis_job` when JSON format is selected.

### 4. Error & Exit Code Policy
- **Single Song Mode:** 
    - Exit 0: Success (including cases where 0 components were extracted).
    - Exit 1: Any failure during submission or analysis.
- **Batch Mode:**
    - Exit 0: Every song in the batch successfully submitted or processed.
    - Exit 1: At least one song failed (validation failure or analysis error).

### 5. `--no-wait` & Empty Result Handling
- **Batch + No-Wait:** A successful job submission is a success. Emit `{"song_id": sid, "status": "submitted"}` and do not mark as failed.
- **Empty Results:** If analysis succeeds but no components are extracted (`[]` returned), JSON mode must print `[]` and exit 0. Do NOT reload from DB.

---

## Implementation Plan

### 1. Constants and Module-level State
**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`
- Define `COMPONENTS_FORMAT_VALUES = {"table", "json"}`.
- Add `progress_console = Console(stderr=True)` following the pattern in `recover_visibility.py:38`.

### 2. Modify `components_recording` Signature
- Add `compute_all_fields: bool = typer.Option(False, "--compute-all-fields", help="...")`.
- Add `format_: str = typer.Option("table", "--format", help="table|json")`.

### 3. Logic Implementation

#### 3.1 Flag Override & Validation
- If `compute_all_fields` is True: set `snap_to_downbeat`, `energy_roles`, `classify_theme`, and `classify_posture` to `True`.
- Execute `_validate_choice(format_, COMPONENTS_FORMAT_VALUES, "--format")`.

#### 3.2 Routing Progress Messages
- If `format_ == "json"`, pass `progress_console` as the `console` argument to `_submit_component_analysis_job` (and use it for any other `console.print` calls in the function).
- Otherwise, use the standard `console`.

#### 3.3 Output - Single Song Mode
- **If `no_wait` is True:**
    - JSON: Print `{"status": "submitted", "song_id": song_id, ...}` to stdout.
    - Table: Standard `[cyan]Job submitted...[/cyan]` message.
- **If waiting:**
    - On failure (`result is None`): Exit 1. (Error is already in stderr via `progress_console`).
    - On empty results (`result == []`): JSON: print `[]` to stdout; Table: print yellow "No components extracted..." message. Exit 0.
    - On success (`result` is list): JSON: print `json.dumps([c.to_dict() for c in result], ...)` to stdout; Table: render table using `result`. Exit 0.
    - **Constraint:** In JSON mode, do NOT call `db_client.get_song_components()`.

#### 3.4 Output - Batch Mode (`--stdin`)
- Initialize `results = []`.
- For each song:
    - If validation fails (no recording/audio/etc): Append `{"song_id": sid, "status": "failed", "error": "<reason>"}`.
    - Call `_submit_component_analysis_job`.
    - **If `no_wait` is True:** If submission succeeds, append `{"song_id": sid, "status": "submitted"}`. If it throws, append `{"song_id": sid, "status": "failed", "error": str(e)}`.
    - **If waiting:** If `result is None`, append `{"song_id": sid, "status": "failed", "error": "..."}`. If `result` is list (including `[]`), append `{"song_id": sid, "status": "succeeded", "components": [c.to_dict() for c in result]}`.
- **Final Stage:**
    - JSON: `print(json.dumps(results, ensure_ascii=False, indent=2))`.
    - Table: Render summary table.
    - Exit: Exit 1 if any entry has `status == "failed"`, else Exit 0.

---

## Testing Plan

### 1. Functional Tests
- **Compute All:** Verify `SongComponent` fields (theme, posture, energy) are populated in DB when using `--compute-all-fields`.
- **Format JSON (Single):** Verify `stdout` is a JSON array of components; verify all 27 fields are present.
- **Format JSON (Batch):** Verify `stdout` is a JSON array of status objects; verify `stderr` contains the progress logs.
- **No-Wait JSON (Single & Batch):** Verify the `"status": "submitted"` output and that it is treated as a success.

### 2. Edge Cases & Regression
- **Mixed Flags:** Test `--compute-all-fields` with `--use-stems`.
- **Invalid Format:** Test `--format xml` raises `typer.Exit(1)`.
- **Empty-Components (Single):** Verify output is `[]` and exit code is 0.
- **Empty-Components (Batch):** Verify status is `"succeeded"` and `components` is `[]`.
- **Batch Partial Failure:** Verify that one failure in a batch causes the final exit code to be 1.
- **Stdout Purity:** Use a tool like `SOW_SURE_STDOUT` or simple redirection to verify `stdout` contains ONLY valid JSON in JSON mode, with all logs moving to `stderr`.

---

## Files Affected

| File | Change |
|---|---|
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | Add `COMPONENTS_FORMAT_VALUES`, `progress_console`. Update `components_recording` signature and logic. Implement JSON routing and exit code policies. |
