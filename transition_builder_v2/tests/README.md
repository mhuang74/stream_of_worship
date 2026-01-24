# Transition Builder v2 Tests

This directory contains test suites for the Transition Builder v2 application.

## Test Files

- **`test_screens.py`** - Complete test suite including integration tests
- **`run_workflow_test.py`** - Standalone executable test for the full workflow
- **`conftest.py`** - Pytest configuration and fixtures

## Running Tests

### Option 1: Run All Tests with Pytest

Run the complete test suite:

```bash
cd transition_builder_v2
pytest tests/test_screens.py -v
```

Run specific test classes:

```bash
pytest tests/test_screens.py::TestFullWorkflow -v
```

Run a specific test:

```bash
pytest tests/test_screens.py::TestFullWorkflow::test_complete_workflow_preview_generate_output -v
```

### Option 2: Run Standalone Workflow Test

Execute the standalone workflow test script directly:

```bash
cd transition_builder_v2
python tests/run_workflow_test.py
```

Or make it executable and run:

```bash
cd transition_builder_v2
./tests/run_workflow_test.py
```

This will test the complete workflow:
1. Pick song A
2. Pick song A section
3. Pick song B
4. Pick song B section
5. Hit 't' to preview (focused preview)
6. Hit 'T' to generate (full transition)
7. Hit 'o' to output song set (full song output)

The standalone test provides detailed output showing each step and verification.

## Test Coverage

### TestFullWorkflow

Integration tests for the complete user workflow:

- **`test_complete_workflow_preview_generate_output`** - Tests the full workflow from song selection to final output
- **`test_workflow_with_custom_parameters`** - Tests workflow with custom gap, fade, and stem parameters
- **`test_workflow_seamless_transition`** - Tests workflow with gap=0 for seamless transitions

### Other Test Classes

- **`TestScreenNavigation`** - Tests screen switching (H, G keys)
- **`TestTransitionGeneration`** - Tests transition generation and metadata
- **`TestModifyMode`** - Tests modify mode functionality (M key)
- **`TestHistoryManagement`** - Tests history screen operations
- **`TestStateManagement`** - Tests application state management

## Test Requirements

The tests require:
- At least 2 songs in the catalog with analyzed sections
- Valid audio files for the songs
- Proper configuration in `config.json`

## Debugging Failed Tests

If tests fail, check:

1. **Config file**: Ensure `config.json` exists and has valid paths
2. **Song files**: Ensure songs have been analyzed and have sections
3. **Audio files**: Ensure audio files exist at specified paths
4. **Output directory**: Ensure the output directory is writable

To see detailed error output:

```bash
pytest tests/test_screens.py -v --tb=long
```

## Expected Output Files

After running the workflow test, you should see:

1. **Transition file**: `output_transitions/transition_gap_*.flac`
2. **Full song file**: `output_songs/songset_*.flac`
3. **Session log**: `session_logs/session_*.log` (if logging is enabled)

## Example Output

```
======================================================================
Testing Complete Workflow: Select → Preview → Generate → Output
======================================================================

✓ Loaded 5 songs from catalog
  - Song A: song1.flac (4 sections)
  - Song B: song2.flac (5 sections)

[Step 1-2] Selecting Song A and section...
  ✓ Selected: song1.flac - intro

[Step 3-4] Selecting Song B and section...
  ✓ Selected: song2.flac - chorus

[Step 5] Generating focused preview (t key)...
  ✓ Preview generated successfully

[Step 6] Generating full transition (Shift-T key)...
  ✓ Transition generated: transition_gap_song1_intro_song2_chorus_1.0beats.flac
    - Type: gap
    - Gap: 1.0 beats
    - File size: 2.34 MB

[Step 7] Generating full song output (o key)...
  ✓ Full song output generated: songset_song1_intro_to_song2_chorus.flac
    - Output type: full_song
    - Song A prefix sections: 0
    - Song B suffix sections: 4
    - Total duration: 234.5s
    - File size: 12.45 MB

======================================================================
FINAL VERIFICATION
======================================================================
  ✓ Transition file exists
  ✓ Transition is FLAC
  ✓ Full song file exists
  ✓ Full song is FLAC
  ✓ Full song in song_sets dir
  ✓ History has 2 items

======================================================================
✓ ALL TESTS PASSED
======================================================================
```
