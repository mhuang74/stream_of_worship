# Update AGENTS.md: Test Commands and Component List

> After the ops/delivery/lab repo reorganization (see
> `specs/ops-delivery-lab-reorganization-v2.md`), several test commands
> and the component list in `AGENTS.md` (and related per-component docs)
> are stale. This spec fixes them.

## Problem

`AGENTS.md` has the following discrepancies with the current repo state:

### 1. Missing component: `lab/legacy-cli-tui/`

AGENTS.md lists **seven** architecturally separate components (lines 91-128)
but the repo now has an **eighth**: `lab/legacy-cli-tui/` (package:
`sow-legacy-cli-tui`). It has its own `pyproject.toml`, `src/`, `tests/`
(with `unit/` and `integration/` subdirs), and `uv.lock`. No test command
is documented for it.

### 2. Analysis Service test command is wrong (AGENTS.md line 46)

**Current:**
```bash
cd ops/analysis-service && PYTHONPATH=src pytest tests/ -v
```

**Problems:**
- The analysis-service now uses `uv` (has `uv.lock`), so bare `pytest`
  won't resolve dependencies.
- `PYTHONPATH=src` is redundant — `pyproject.toml` already sets
  `pythonpath = ["src", "../admin-cli/src"]`.
- The test extra is called `dev`, not `test`.

### 3. Render Worker test command should use `uv` (AGENTS.md line 49)

**Current:**
```bash
cd delivery/render-worker && PYTHONPATH=src pytest tests/ -v
```

**Problems:**
- The render-worker now has `uv.lock` and `pyproject.toml` with a `dev`
  extra.
- `PYTHONPATH=src` is redundant — `pyproject.toml` already sets
  `pythonpath = ["src"]`.

### 4. Admin CLI test command has redundant `PYTHONPATH` and path (AGENTS.md line 40)

**Current:**
```bash
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest ops/admin-cli/tests -v
```

**Problems:**
- `PYTHONPATH=ops/admin-cli/src` is redundant — `pyproject.toml` sets
  `pythonpath = ["src"]`.
- `ops/admin-cli/tests` path is redundant — `pyproject.toml` sets
  `testpaths = ["tests"]`.
- No mention that integration tests (requiring Docker/testcontainers) are
  excluded by default via `addopts = "-m 'not integration'"`.

### 5. Lab app test command has redundant path (AGENTS.md line 43)

**Current:**
```bash
uv run --project lab/sow-app --extra test pytest lab/sow-app/tests -v
```

**Problem:** `lab/sow-app/tests` is redundant — `pyproject.toml` sets
`testpaths = ["tests"]`.

### 6. Render Worker Commands section has stale commands (AGENTS.md lines 70-81)

The "Render Worker Commands" section repeats the same stale
`PYTHONPATH=src pytest tests/ -v` commands and does not use `uv`.

### 7. No test command for `lab/legacy-cli-tui/`

The `lab/legacy-cli-tui/pyproject.toml` has a `test` extra and `tests/`
directory with `unit/` and `integration/` subdirs. No test command is
documented anywhere.

### 8. Architecture section doesn't mention `lab/legacy-cli-tui/` (AGENTS.md lines 91-128)

The "Architecture & Structure" section says "seven architecturally
separate components" and doesn't list `lab/legacy-cli-tui/`.

### 9. Per-component docs also have stale test commands

- `ops/analysis-service/DEVELOPER.md` lines 140-142: uses
  `--extra app --extra test` but neither `app` nor `test` extras exist
  (they're `service` and `dev`).
- `delivery/render-worker/README.md` lines 207-218: uses manual venv
  setup + `PYTHONPATH=src pytest tests/ -v` instead of `uv run`.

## Implementation Plan

### Step 1: Fix "Run Tests" section in AGENTS.md (lines 37-53)

Replace the entire "Run Tests" block with:

```bash
# Admin CLI + shared DB helpers
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest -v

# Lab app
uv run --project lab/sow-app --extra test pytest -v

# Analysis service
cd ops/analysis-service && uv run --extra dev pytest tests/ -v

# Render worker
cd delivery/render-worker && uv run --extra dev pytest tests/ -v

# Legacy CLI/TUI
uv run --project lab/legacy-cli-tui --extra test pytest -v

# Android app
cd delivery/android && ./gradlew testDebugUnitTest koverXmlReport
```

### Step 2: Fix "Render Worker Commands" section in AGENTS.md (lines 70-81)

Replace with:

```bash
# Run tests
cd delivery/render-worker && uv run --extra dev pytest tests/ -v

# Run specific test files
cd delivery/render-worker && uv run --extra dev pytest tests/test_pipeline.py -v
cd delivery/render-worker && uv run --extra dev pytest tests/test_video_engine.py -v

# Local development with Docker
docker compose up --build
```

### Step 3: Add `lab/legacy-cli-tui/` to Architecture section in AGENTS.md

Update the component count from "seven" to "eight" and add a new subsection
after section 4 (Lab User App):

```markdown
### 5. Legacy CLI/TUI (Deprecated)
- **Location:** `lab/legacy-cli-tui/` (Python package: `sow_legacy_cli_tui`)
- **Stack:** Textual TUI, pydub, miniaudio, ffmpeg-python, Pillow
- **Commands:** `uv run --project lab/legacy-cli-tui stream-of-worship --help`
```

Renumber the subsequent sections (Web App → 6, Android App → 7, Render
Worker → 8).

Also add to the component summary list (lines 125-128):

```markdown
- **Legacy CLI/TUI**: Deprecated CLI/TUI, predecessor to sow-app
```

### Step 4: Fix `ops/analysis-service/DEVELOPER.md` test command (lines 134-143)

Replace the "Unit Tests (Outside Docker)" block with:

```bash
# From project root
cd ops/analysis-service

# Run analysis service tests
uv run --extra dev pytest tests/ -v
```

### Step 5: Fix `delivery/render-worker/README.md` test commands (lines 204-224)

Replace the "Install Dependencies" + "Run Tests" + "Run a Single Test File"
block with:

```bash
### Run Tests

```bash
cd delivery/render-worker && uv run --extra dev pytest tests/ -v
```

### Run a Single Test File

```bash
cd delivery/render-worker && uv run --extra dev pytest tests/test_pipeline.py -v
```
```

Remove the manual venv setup instructions (lines 207-212) since `uv`
handles dependency resolution.

### Step 6: Add note about integration test exclusion for Admin CLI

In the AGENTS.md "Run Tests" section, add a comment after the Admin CLI
command:

```bash
# Admin CLI + shared DB helpers
# (integration tests requiring Docker/testcontainers are excluded by default)
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest -v
```

## Files Modified

| File | Changes |
|------|---------|
| `AGENTS.md` | Fix Run Tests section (lines 37-53), fix Render Worker Commands (lines 70-81), add legacy-cli-tui to Architecture section (lines 91-128), update component count |
| `ops/analysis-service/DEVELOPER.md` | Fix unit test command (lines 134-143) |
| `delivery/render-worker/README.md` | Fix test commands (lines 204-224), remove manual venv setup |

## Verification

After implementation, verify each command works:

```bash
# Admin CLI
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest -v

# Lab app
uv run --project lab/sow-app --extra test pytest -v

# Analysis service
cd ops/analysis-service && uv run --extra dev pytest tests/ -v

# Render worker
cd delivery/render-worker && uv run --extra dev pytest tests/ -v

# Legacy CLI/TUI
uv run --project lab/legacy-cli-tui --extra test pytest -v
```

## Assumptions

- The `uv` extra names in each `pyproject.toml` are correct as of the
  current repo state (`admin`/`test` for admin-cli, `test` for sow-app,
  `dev` for analysis-service and render-worker, `test` for legacy-cli-tui).
- Integration tests in admin-cli are intentionally excluded by default
  via `addopts = "-m 'not integration'"` and this behavior should be
  documented but not changed.
- The render-worker `requirements.txt` is retained for Docker builds;
  `uv` is used only for local development/testing.
