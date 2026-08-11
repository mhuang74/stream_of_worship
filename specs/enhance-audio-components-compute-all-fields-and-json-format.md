# Implementation Spec: Enhance Audio Components Compute Flags and JSON Format

> **Date:** 2026-08-11
> **Spec ID:** `enhance-audio-components-compute-all-fields-and-json-format`

---

## Problem Statement

The `sow-admin audio components` command currently requires users to manually specify multiple flags to enable advanced feature computation (snap to downbeat, energy roles, theme classification, posture classification). This is tedious for users wanting a "full" analysis. Furthermore, the default table output is often too wide for mobile SSH terminals, and there is no machine-readable output format available for integration or easier inspection of the 27 available `SongComponent` fields.

---

## Design Decisions

### 1. `--compute-all-fields` Flag
- **Rationale:** Provide a shortcut to enable all non-destructive, non-resource-intensive analysis flags.
- **Behavior:** Sets `snap_to_downbeat`, `energy_roles`, `classify_theme`, and `classify_posture` to `True`.
- **Exclusion:** `--use-stems` is explicitly excluded because Demucs source separation is a separate, significantly heavier computational process.
- **Precedence:** Overrides individual flags to `True` if enabled.

### 2. `--format` Option
- **Rationale:** Support both human-readable (table) and machine-readable (JSON) output.
- **Pattern:** Follow existing project patterns found in `maintenance.py` and `songset.py`.
- **JSON Implementation:**
    - **Single-song:** Output a JSON array of `SongComponent.to_dict()` objects.
    - **Batch:** Output a JSON array of results, each containing `song_id`, `status`, and (if successful) `components` list.
- **IO Strategy:** JSON output goes to `stdout` via `print()`. Progress/status updates in batch mode go to `stderr` via `console.print()`.

---

## Implementation Plan

### 1. Constants and Validation
**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

Define the following at the module level:
```python
COMPONENTS_FORMAT_VALUES = {"table", "json"}
```

### 2. Modify `components_recording` Function Signature
**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` (around line 2144)

- Add `--compute-all-fields: bool = False`
- Add `--format: str = "table"`

### 3. Logic Changes in `components_recording`

#### 3.1 Flag Override
At the start of the function, handle the `--compute-all-fields` logic:
```python
if compute_all_fields:
    snap_to_downbeat = True
    energy_roles = True
    classify_theme = True
    classify_posture = True
```

#### 3.2 Format Validation
Use the existing `_validate_choice` helper:
```python
fmt = _validate_choice(format, COMPONENTS_FORMAT_VALUES, "format")
```

#### 3.3 Output Handling - Single Song Mode
In the path where a single song is processed and components are retrieved:
- **Current:** Calls `_render_components_table(components, ...)`
- **New:**
```python
if fmt == "json":
    print(json.dumps([c.to_dict() for c in components], ensure_ascii=False, indent=2))
else:
    _render_components_table(components, ...)
```

#### 3.4 Output Handling - Batch Mode (`--stdin`)
In the batch processing loop:
- Change progress messages (e.g., "Processing song X...") to use `console.print(..., stderr=True)`.
- Collect results in a list:
```python
results = []
# ... inside loop ...
if success:
    results.append({
        "song_id": song_id,
        "status": "succeeded",
        "components": [c.to_dict() for c in components]
    })
else:
    results.append({
        "song_id": song_id,
        "status": "failed",
        "error": str(e)
    })
```
- After the loop, handle final output:
```python
if fmt == "json":
    print(json.dumps(results, ensure_ascii=False, indent=2))
else:
    # Render the existing summary table
```

#### 3.5 Output Handling - `no_wait` Mode
If `no_wait` is True and `fmt == "json"`, print the submission status as JSON:
```python
print(json.dumps({
    "status": "submitted",
    "song_id": song_id,
    "message": f"Job submitted for {song_id}"
}, ensure_ascii=False, indent=2))
```

---

## Testing Plan

### 1. Functional Tests
- **Compute All:** Run with `--compute-all-fields` and verify that the resulting `SongComponent` objects in the DB have populated theme, posture, and energy roles (even if individual flags were omitted).
- **Format Table:** Run with `--format table` (or default) and verify standard `rich` table output.
- **Format JSON (Single):** Run with `--format json` for one song. Verify that `stdout` contains a JSON array and that all 27 fields are present.
- **Format JSON (Batch):** Run with `--stdin` and `--format json`. Verify that `stdout` is a single JSON array containing result objects for all songs, and that `stderr` contains the processing progress.
- **`no_wait` JSON:** Run with `--no-wait --format json`. Verify the submission JSON object.

### 2. Edge Cases
- **Mixed Flags:** Pass `--compute-all-fields` AND `--use-stems`. Verify that stems are used while other "all" fields are also enabled.
- **Invalid Format:** Pass `--format xml`. Verify that `_validate_choice` raises an appropriate error.
- **Batch Failures:** Simulate a failure for one song in a batch and verify the JSON output contains `"status": "failed"` and the `error` field for that specific song.

---

## Files Affected

| File | Change |
|---|---|
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | Update `components_recording` signature, add flag logic, implement JSON output paths for single/batch/no-wait modes. |
