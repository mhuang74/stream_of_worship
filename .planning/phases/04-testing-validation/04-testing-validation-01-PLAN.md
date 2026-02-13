---
phase: 04-testing-validation
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - services/qwen3/tests/test_map_segments_to_lines.py
  - services/qwen3/pyproject.toml
autonomous: true

must_haves:
  truths:
    - "map_segments_to_lines() correctly handles repeated chorus scenarios (worship songs)"
    - "map_segments_to_lines() correctly handles empty lines in original lyrics"
    - "map_segments_to_lines() correctly handles lines not found in aligned text"
    - "normalize_text() removes whitespace and common Chinese punctuation"
    - "All tests pass via pytest"
  artifacts:
    - path: "services/qwen3/tests/test_map_segments_to_lines.py"
      provides: "Unit tests for map_segments_to_lines()"
      min_lines: 150
    - path: "services/qwen3/pyproject.toml"
      provides: "pytest dependencies for qwen3 service"
      contains: "pytest"
  key_links:
    - from: "test_map_segments_to_lines.py"
      to: "routes/align.py"
      via: "import map_segments_to_lines"
      pattern: "from sow_qwen3.routes.align import map_segments_to_lines"
---

<objective>
Create comprehensive unit tests for map_segments_to_lines() function

Purpose: Verify that character-level alignment segments are correctly mapped to original lyric lines, especially for worship songs with repeated choruses
Output: test_map_segments_to_lines.py with full edge case coverage
</objective>

<execution_context>
@/home/mhuang/.claude/get-shit-done/workflows/execute-plan.md
@/home/mhuang/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@services/qwen3/src/sow_qwen3/routes/align.py
</context>

<tasks>

<task type="auto">
  <name>Set up pytest in qwen3 service</name>
  <files>services/qwen3/pyproject.toml</files>
  <action>
    Add pytest dependencies to qwen3 service pyproject.toml:
    - Add [project.optional-dependencies] with dev = ["pytest>=7.4.0", "pytest-asyncio>=0.23.0"]
    - Add pytest configuration section [tool.pytest.ini_options] with testpaths = ["tests"] and asyncio_mode = "auto"
  </action>
  <verify>grep pytest services/qwen3/pyproject.toml shows pytest dependency and config</verify>
  <done>qwen3 service has pytest configured for testing</done>
</task>

<task type="auto">
  <name>Create unit tests for map_segments_to_lines()</name>
  <files>services/qwen3/tests/test_map_segments_to_lines.py</files>
  <action>
    Create services/qwen3/tests/test_map_segments_to_lines.py with comprehensive test coverage:

    Import map_segments_to_lines from sow_qwen3.routes.align

    Test fixtures:
    - simple_segments: [(0.0, 2.0, "ABC"), (2.0, 4.0, "DEF")]
    - simple_lines: ["ABC", "DEF"]
    - repeated_chorus_segments: [(0.0, 2.0, "Chorus"), (2.0, 4.0, "Chorus"), (4.0, 6.0, "Chorus")]
    - repeated_chorus_lines: ["Chorus", "Chorus", "Chorus"]
    - empty_lines_segments: [(0.0, 2.0, "ABC"), (2.0, 4.0, "DEF")]
    - empty_lines: ["", "ABC", "", "DEF", ""]
    - not_found_segments: [(0.0, 2.0, "ABCDEF")]
    - not_found_lines: ["XYZ"]  # Different text

    Test cases:
    1. test_simple_mapping() - Basic mapping with non-repeated lines
    2. test_repeated_chorus() - Verify repeated chorus lines get correct timestamps
    3. test_empty_lines() - Verify empty lines handled gracefully (use previous end time)
    4. test_line_not_found() - Verify fallback interpolation when line not found in text
    5. test_no_overlapping_segments() - Verify interpolation when no segments overlap
    6. test_empty_segments() - Handle empty input segments
    7. test_empty_lines_input() - Handle empty original_lines list
    8. test_normalize_text() - Test helper function removes whitespace and punctuation

    For test_repeated_chorus():
    - Input segments should have same text at different time ranges
    - Output should have same text with correct per-line timestamps
    - Assert timestamps are chronologically ordered

    For test_empty_lines():
    - Empty lines should receive timestamp from previous line
    - First empty line should use 0.0
    - Verify output count matches input lines count
  </action>
  <verify>cd services/qwen3 && uv run pytest tests/test_map_segments_to_lines.py -v passes all tests</verify>
  <done>map_segments_to_lines() unit tests cover all edge cases including repeated chorus scenarios</done>
</task>

</tasks>

<verification>
Verify all tests pass:
```bash
cd services/qwen3 && PYTHONPATH=src uv run --extra dev pytest tests/test_map_segments_to_lines.py -v
```

Expected: All tests pass with no failures
</verification>

<success_criteria>
1. map_segments_to_lines() has comprehensive unit test coverage
2. Tests cover repeated chorus scenarios (critical for worship songs)
3. Tests cover edge cases: empty lines, not found, no overlap
4. normalize_text() is independently tested
5. All tests pass via pytest
</success_criteria>

<output>
After completion, create `.planning/phases/04-testing-validation/04-testing-validation-01-SUMMARY.md`
</output>
