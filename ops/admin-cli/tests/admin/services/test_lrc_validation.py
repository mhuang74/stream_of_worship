"""Unit tests for LRC editor validation and quality checks."""

import pytest

from stream_of_worship.admin.services.lrc_parser import (
    LRCLine,
    LRCPreservedLine,
    serialize_lrc,
)
from stream_of_worship.admin.editor.validation import (
    ValidationError,
    ValidationWarning,
    ValidationResult,
    validate_lrc,
)


def _make_lines(timestamps_texts):
    return [
        LRCLine(time_seconds=ts, text=txt, raw_timestamp=f"[00:00.00]")
        for ts, txt in timestamps_texts
    ]


class TestMonotonicTimestamps:
    def test_monotonic_passes(self):
        lines = _make_lines([(0.0, "A"), (10.0, "B"), (20.0, "C")])
        result = validate_lrc(lines)
        assert result.can_upload
        assert not any(e.code == "non_monotonic" for e in result.errors)

    def test_non_monotonic_blocked(self):
        lines = _make_lines([(0.0, "A"), (20.0, "B"), (10.0, "C")])
        result = validate_lrc(lines)
        assert not result.can_upload
        assert any(e.code == "non_monotonic" for e in result.errors)

    def test_equal_timestamps_pass_monotonic(self):
        lines = _make_lines([(10.0, "A"), (10.0, "B")])
        result = validate_lrc(lines)
        assert not any(e.code == "non_monotonic" for e in result.errors)


class TestAllZeroDraft:
    def test_all_zero_blocked(self):
        lines = _make_lines([(0.0, "A"), (0.0, "B"), (0.0, "C")])
        result = validate_lrc(lines)
        assert not result.can_upload
        assert any(e.code == "all_zero_draft" for e in result.errors)

    def test_one_nonzero_passes(self):
        lines = _make_lines([(0.0, "A"), (10.0, "B"), (0.0, "C")])
        result = validate_lrc(lines)
        assert not any(e.code == "all_zero_draft" for e in result.errors)

    def test_empty_lines_ignored(self):
        lines = _make_lines([(0.0, ""), (0.0, ""), (0.0, "")])
        result = validate_lrc(lines)
        assert not any(e.code == "all_zero_draft" for e in result.errors)


class TestDuplicateTimestamps:
    def test_duplicate_warns(self):
        lines = _make_lines([(10.0, "A"), (10.0, "B")])
        result = validate_lrc(lines)
        assert any(w.code == "duplicate_timestamp" for w in result.warnings)

    def test_no_duplicate_no_warning(self):
        lines = _make_lines([(10.0, "A"), (20.0, "B")])
        result = validate_lrc(lines)
        assert not any(w.code == "duplicate_timestamp" for w in result.warnings)


class TestDurationSanity:
    def test_short_duration_warns(self):
        lines = _make_lines([(5.0, "A")])
        result = validate_lrc(lines, audio_duration_seconds=300.0)
        assert any(w.code == "short_duration" for w in result.warnings)

    def test_long_duration_warns(self):
        lines = _make_lines([(350.0, "A")])
        result = validate_lrc(lines, audio_duration_seconds=300.0)
        assert any(w.code == "long_duration" for w in result.warnings)

    def test_reasonable_duration_no_warning(self):
        lines = _make_lines([(150.0, "A")])
        result = validate_lrc(lines, audio_duration_seconds=300.0)
        assert not any(w.code in ("short_duration", "long_duration") for w in result.warnings)

    def test_no_audio_duration_skips_check(self):
        lines = _make_lines([(5.0, "A")])
        result = validate_lrc(lines, audio_duration_seconds=None)
        assert not any(w.code in ("short_duration", "long_duration") for w in result.warnings)


class TestPreservationCheck:
    def test_no_drop_passes(self):
        original = [LRCPreservedLine(raw="[ti:Title]", tag="ti", value="Title")]
        current = [LRCPreservedLine(raw="[ti:Title]", tag="ti", value="Title")]
        result = validate_lrc(
            _make_lines([(10.0, "A")]),
            preserved_lines=current,
            original_preserved_lines=original,
        )
        assert not any(e.code == "content_dropped" for e in result.errors)

    def test_unknown_content_dropped_blocked(self):
        original = [
            LRCPreservedLine(raw="[ti:Title]", tag="ti", value="Title"),
            LRCPreservedLine(raw="some unknown line"),
        ]
        current = [LRCPreservedLine(raw="[ti:Title]", tag="ti", value="Title")]
        result = validate_lrc(
            _make_lines([(10.0, "A")]),
            preserved_lines=current,
            original_preserved_lines=original,
        )
        assert any(e.code == "content_dropped" for e in result.errors)

    def test_no_original_preserved_skips(self):
        result = validate_lrc(
            _make_lines([(10.0, "A")]),
            original_preserved_lines=None,
        )
        assert not any(e.code == "content_dropped" for e in result.errors)


class TestDiffGeneration:
    def test_diff_present_when_original_provided(self):
        original = serialize_lrc(_make_lines([(10.0, "Old")]))
        lines = _make_lines([(10.0, "New")])
        result = validate_lrc(lines, original_serialized=original)
        assert result.diff != ""
        assert "-Old" in result.diff or "New" in result.diff

    def test_no_diff_when_original_absent(self):
        lines = _make_lines([(10.0, "A")])
        result = validate_lrc(lines)
        assert result.diff == ""
