---
phase: 04-testing-validation
plan: 01
subsystem: qwen3-service
tags: [unit-tests, lrc-refinement, testing]
---

# Phase 04 Plan 01: map_segments_to_lines Unit Tests Summary

Comprehensive unit test suite for the `map_segments_to_lines()` function that character-level alignment segments are correctly mapped to original lyric lines, especially for worship songs with repeated choruses.

---

## Tasks Completed

| Task | Name | Commit | Files |
| ---- | ---- | ---- | ----- |
| 1 | Set up pytest in qwen3 service | f124057 | services/qwen3/pyproject.toml |
| 2 | Create unit tests for map_segments_to_lines() | 18e5371 | services/qwen3/tests/test_map_segments_to_lines.py |

---

## Deliverables

### 1. pytest Configuration (Task 1)
- Added pytest dependencies as dev extras: `pytest>=7.4.0`, `pytest-asyncio>=0.23.0`
- Configured pytest with `testpaths = ["tests"]` and `asyncio_mode = "auto"`

### 2. Comprehensive Test Suite (Task 2)
Created 34 test cases in `services/qwen3/tests/test_map_segments_to_lines.py`:

| Test Class | Description | Tests |
| ---------- | ----------- | ----- |
| TestNormalizeText | Tests for normalize_text() helper (whitespace/punctuation removal) | 10 |
| TestSimpleMapping | Basic mapping scenarios | 3 |
| TestRepeatedChorus | Repeated chorus scenarios (critical for worship songs) | 3 |
| TestEmptyLines | Empty line handling with previous time fallback | 4 |
| TestLineNotFound | Line not found with interpolation fallback | 4 |
| TestNoOverlappingSegments | Cases where no segments overlap | 1 |
| TestEmptyInput | Empty segment/line inputs | 3 |
| TestChineseWorshipLyrics | Integration tests with realistic Chinese worship songs | 3 |
| TestSegmentTimingPrecision | Fractional timestamp handling | 2 |

---

## Test Coverage Highlights

### Repeated Chorus Testing
- **test_repeated_chorus_same_text_different_times**: Verifies same chorus text at different times gets correct per-line timestamps
- **test_repeated_chorus_character_overlap**: Tests character-level overlap in repeated sections
- **test_chorus_repeated_with_verse**: Tests common worship song pattern (chorus-verse-chorus)

### Edge Cases
- Empty lines receive timestamp from previous line (or 0.0 for first)
- Lines not found in aligned text use proportional interpolation
- Empty segments and empty original_lines handled gracefully
- Fractional timestamps preserved for precision

### Chinese Language Support
- Tests for Chinese punctuation removal (。，！？、；：「」『』)
- Integration tests with realistic Chinese worship song lyrics
- Complex repetition patterns common in worship music

## Deviations from Plan

None - plan executed exactly as written.

---

## Key Technical Decisions

1. **Test organization**: Used pytest class-based organization for logical grouping
2. **Coverage focus**: Prioritized repeated chorus scenarios as noted in Memory and critical for worship songs
3. **No mocks needed**: Test pure function behavior without external dependencies
4. **Chinese character handling**: Confirmed `normalize_text()` only removes whitespace/punctuation, not Traditional/Simplified conversion

---

## Metrics

- **Duration**: 322 seconds (~5.4 minutes)
- **Tests created**: 34 test cases
- **Test file size**: 471 lines
- **Pass rate**: 100% (34/34 passed)

---

## Files Affected

| File | Change | Lines |
| ---- | ---- | ----- |
| services/qwen3/pyproject.toml | Added pytest config and dev extras | +5 |
| services/qwen3/tests/test_map_segments_to_lines.py | Created new test file | +471 |

---

## Next Steps

These tests provide the foundation for Phase 4 testing and validation. Future tests may cover:
- Integration tests with Qwen3ForcedAligner
- End-to-end API endpoint testing
- Analysis Service fallback testing

---

## Self-Check: PASSED

- [x] All tasks executed (2/2)
- [x] Each task committed individually
- [x] Tests file created (services/qwen3/tests/test_map_segments_to_lines.py)
- [x] All 34 tests pass (verified via pytest)
- [x] pytest dependency configured (verified in pyproject.toml)
- [x] SUMMARY.md created

Commit verification:
```bash
git log --oneline --all | grep -q "f124057" && echo "FOUND: f124057" || echo "MISSING: f124057"
git log --oneline --all | grep -q "18e5371" && echo "FOUND: 18e5371" || echo "MISSING: 18e5371"
```

File verification:
```bash
[ -f "services/qwen3/tests/test_map_segments_to_lines.py" ] && echo "FOUND: test file" || echo "MISSING: test file"
```
