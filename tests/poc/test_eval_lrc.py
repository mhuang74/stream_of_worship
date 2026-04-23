"""Tests for poc/eval_lrc.py LRC evaluation script."""

import sys
from pathlib import Path

import pytest

# Add poc and src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "poc"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from eval_lrc import (
    PinyinWord,
    DiffEntry,
    EvaluationResult,
    EvaluationStats,
    EvaluationScores,
    chinese_to_pinyin,
    normalize_pinyin,
    parse_enhanced_lrc_line,
    interpolate_word_times,
    parse_lrc_file,
    LRCWord,
    align_sequences,
    calculate_text_score,
    calculate_timing_score,
    calculate_final_score,
    evaluate_lrc,
    format_diff_report,
    format_json_report,
)


# --------------------------------------------------------------------------
# Pinyin Conversion Tests
# --------------------------------------------------------------------------


class TestChineseToPinyin:
    """Tests for chinese_to_pinyin function."""

    def test_single_character(self):
        """Test single Chinese character."""
        result = chinese_to_pinyin("我")
        assert result == ["wo"]

    def test_multiple_characters(self):
        """Test multiple Chinese characters."""
        result = chinese_to_pinyin("我爱你")
        assert result == ["wo", "ai", "ni"]

    def test_mixed_content(self):
        """Test Chinese with non-Chinese characters."""
        result = chinese_to_pinyin("我love你")
        assert result == ["wo", "ni"]

    def test_empty_string(self):
        """Test empty string."""
        result = chinese_to_pinyin("")
        assert result == []

    def test_no_chinese(self):
        """Test string with no Chinese characters."""
        result = chinese_to_pinyin("hello world 123")
        assert result == []

    def test_worship_song_lyrics(self):
        """Test typical worship song lyrics."""
        result = chinese_to_pinyin("全心赞美")
        assert result == ["quan", "xin", "zan", "mei"]


class TestNormalizePinyin:
    """Tests for normalize_pinyin function."""

    def test_lowercase(self):
        """Test uppercase to lowercase."""
        assert normalize_pinyin("WO") == "wo"

    def test_strip_whitespace(self):
        """Test whitespace stripping."""
        assert normalize_pinyin("  wo  ") == "wo"

    def test_combined(self):
        """Test combined normalization."""
        assert normalize_pinyin("  Wo  ") == "wo"


# --------------------------------------------------------------------------
# LRC Parsing Tests
# --------------------------------------------------------------------------


class TestParseEnhancedLrcLine:
    """Tests for parse_enhanced_lrc_line function."""

    def test_standard_lrc_line(self):
        """Test standard LRC format."""
        result = parse_enhanced_lrc_line("[00:12.50] 我爱你")
        assert result is not None
        line_start, raw_text, words = result
        assert line_start == 12.5
        assert raw_text == "我爱你"
        assert len(words) == 1
        assert words[0].text == "我爱你"
        assert words[0].time_seconds == 12.5

    def test_three_digit_milliseconds(self):
        """Test LRC with three-digit milliseconds."""
        result = parse_enhanced_lrc_line("[00:12.500] 测试")
        assert result is not None
        line_start, raw_text, words = result
        assert line_start == 12.5

    def test_word_level_timestamps(self):
        """Test word-level LRC format."""
        result = parse_enhanced_lrc_line("[00:12.50]我<00:12.80>爱<00:13.10>你")
        assert result is not None
        line_start, raw_text, words = result
        assert line_start == 12.5
        assert raw_text == "我爱你"
        assert len(words) == 3
        assert words[0].text == "我"
        assert words[0].time_seconds == 12.5
        assert words[1].text == "爱"
        assert words[1].time_seconds == 12.8
        assert words[2].text == "你"
        assert words[2].time_seconds == 13.1

    def test_invalid_line(self):
        """Test invalid LRC line returns None."""
        assert parse_enhanced_lrc_line("not a valid line") is None
        assert parse_enhanced_lrc_line("") is None
        assert parse_enhanced_lrc_line("[invalid] text") is None

    def test_empty_content(self):
        """Test LRC line with empty content."""
        result = parse_enhanced_lrc_line("[00:05.00]")
        assert result is not None
        line_start, raw_text, words = result
        assert line_start == 5.0
        assert raw_text == ""
        assert len(words) == 0


class TestInterpolateWordTimes:
    """Tests for interpolate_word_times function."""

    def test_single_word(self):
        """Test single word returns start time."""
        words = interpolate_word_times(["我"], 10.0, 15.0)
        assert len(words) == 1
        assert words[0].text == "我"
        assert words[0].time_seconds == 10.0

    def test_multiple_words_equal_length(self):
        """Test multiple words of equal length."""
        words = interpolate_word_times(["我", "爱", "你"], 10.0, 13.0)
        assert len(words) == 3
        assert words[0].time_seconds == 10.0
        # Each character is 1/3 of duration
        assert abs(words[1].time_seconds - 11.0) < 0.01
        assert abs(words[2].time_seconds - 12.0) < 0.01

    def test_empty_list(self):
        """Test empty word list."""
        words = interpolate_word_times([], 10.0, 15.0)
        assert len(words) == 0

    def test_varying_word_lengths(self):
        """Test words with varying lengths get proportional times."""
        words = interpolate_word_times(["我", "爱你"], 10.0, 13.0)  # 1 + 2 = 3 chars
        assert len(words) == 2
        assert words[0].time_seconds == 10.0
        # First word is 1/3, second is 2/3
        assert abs(words[1].time_seconds - 11.0) < 0.01


class TestParseLrcFile:
    """Tests for parse_lrc_file function."""

    def test_simple_lrc_content(self):
        """Test parsing simple LRC content."""
        content = """[00:10.00] 我
[00:11.00] 爱
[00:12.00] 你"""
        result = parse_lrc_file(content)
        assert len(result) == 3
        assert result[0].pinyin == "wo"
        assert result[1].pinyin == "ai"
        assert result[2].pinyin == "ni"

    def test_multiline_lrc(self):
        """Test parsing LRC with multi-character lines."""
        content = """[00:10.00] 我爱你
[00:15.00] 永远"""
        result = parse_lrc_file(content)
        # First line has 3 characters, second has 2
        assert len(result) == 5
        pinyins = [w.pinyin for w in result]
        assert pinyins == ["wo", "ai", "ni", "yong", "yuan"]

    def test_empty_lines_skipped(self):
        """Test that empty lines are handled."""
        content = """[00:10.00] 我
[00:11.00]
[00:12.00] 你"""
        result = parse_lrc_file(content)
        assert len(result) == 2


# --------------------------------------------------------------------------
# Alignment Tests
# --------------------------------------------------------------------------


class TestAlignSequences:
    """Tests for align_sequences function."""

    def test_perfect_match(self):
        """Test perfectly matching sequences."""
        lrc = [
            PinyinWord("我", "wo", 10.0),
            PinyinWord("爱", "ai", 11.0),
            PinyinWord("你", "ni", 12.0),
        ]
        audio = [
            PinyinWord("我", "wo", 10.1),
            PinyinWord("爱", "ai", 11.1),
            PinyinWord("你", "ni", 12.1),
        ]
        diff = align_sequences(lrc, audio)

        assert len(diff) == 3
        assert all(d.op == "equal" for d in diff)

    def test_missing_word(self):
        """Test word missing in audio (delete)."""
        lrc = [
            PinyinWord("我", "wo", 10.0),
            PinyinWord("爱", "ai", 11.0),
            PinyinWord("你", "ni", 12.0),
        ]
        audio = [
            PinyinWord("我", "wo", 10.1),
            PinyinWord("你", "ni", 12.1),
        ]
        diff = align_sequences(lrc, audio)

        ops = [d.op for d in diff]
        assert "equal" in ops
        assert "delete" in ops

    def test_extra_word(self):
        """Test extra word in audio (insert)."""
        lrc = [
            PinyinWord("我", "wo", 10.0),
            PinyinWord("你", "ni", 12.0),
        ]
        audio = [
            PinyinWord("我", "wo", 10.1),
            PinyinWord("爱", "ai", 11.1),
            PinyinWord("你", "ni", 12.1),
        ]
        diff = align_sequences(lrc, audio)

        ops = [d.op for d in diff]
        assert "equal" in ops
        assert "insert" in ops


# --------------------------------------------------------------------------
# Scoring Tests
# --------------------------------------------------------------------------


class TestCalculateTextScore:
    """Tests for calculate_text_score function."""

    def test_perfect_match(self):
        """Test perfect match gives 100."""
        diff = [
            DiffEntry(op="equal"),
            DiffEntry(op="equal"),
            DiffEntry(op="equal"),
        ]
        score = calculate_text_score(diff)
        assert score == 100.0

    def test_all_missing(self):
        """Test all missing gives 0."""
        diff = [
            DiffEntry(op="delete"),
            DiffEntry(op="delete"),
        ]
        score = calculate_text_score(diff)
        assert score == 0.0

    def test_mixed_results(self):
        """Test mixed match/missing/extra."""
        diff = [
            DiffEntry(op="equal"),
            DiffEntry(op="equal"),
            DiffEntry(op="delete"),
            DiffEntry(op="insert"),
        ]
        score = calculate_text_score(diff)
        # 2 matched, 1 deleted, 1 inserted
        # Precision = 2/3, Recall = 2/3
        # F1 = 2 * (2/3) * (2/3) / (4/3) = 8/9 / 4/3 = 8/12 = 2/3
        expected = (2 / 3) * 100
        assert abs(score - expected) < 0.1


class TestCalculateTimingScore:
    """Tests for calculate_timing_score function."""

    def test_perfect_timing(self):
        """Test perfect timing gives 100."""
        diff = [
            DiffEntry(op="equal", time_diff=0.0),
            DiffEntry(op="equal", time_diff=0.0),
        ]
        score, rms, max_err = calculate_timing_score(diff, threshold_ms=500.0)
        assert score == 100.0
        assert rms == 0.0
        assert max_err == 0.0

    def test_some_timing_error(self):
        """Test timing error reduces score."""
        diff = [
            DiffEntry(op="equal", time_diff=0.1),  # 100ms error
            DiffEntry(op="equal", time_diff=-0.1),  # 100ms error
        ]
        score, rms, max_err = calculate_timing_score(diff, threshold_ms=500.0)
        # RMS = 100ms
        # Score = 100 - (100/500)*100 = 80
        assert abs(score - 80.0) < 0.1
        assert abs(rms - 100.0) < 0.1
        assert abs(max_err - 100.0) < 0.1

    def test_threshold_exceeded(self):
        """Test threshold exceeded gives 0."""
        diff = [
            DiffEntry(op="equal", time_diff=0.5),  # 500ms error
        ]
        score, rms, max_err = calculate_timing_score(diff, threshold_ms=500.0)
        assert score == 0.0

    def test_no_matched_words(self):
        """Test no matched words gives 100 (nothing to compare)."""
        diff = [
            DiffEntry(op="delete"),
            DiffEntry(op="insert"),
        ]
        score, rms, max_err = calculate_timing_score(diff)
        assert score == 100.0


class TestCalculateFinalScore:
    """Tests for calculate_final_score function."""

    def test_default_weights(self):
        """Test default weights (0.6 text, 0.4 timing)."""
        score = calculate_final_score(100.0, 100.0)
        assert score == 100.0

        score = calculate_final_score(80.0, 70.0)
        # 80*0.6 + 70*0.4 = 48 + 28 = 76
        assert abs(score - 76.0) < 0.1

    def test_custom_weights(self):
        """Test custom weights."""
        score = calculate_final_score(100.0, 50.0, text_weight=0.8, timing_weight=0.2)
        # 100*0.8 + 50*0.2 = 80 + 10 = 90
        assert abs(score - 90.0) < 0.1


# --------------------------------------------------------------------------
# Full Evaluation Tests
# --------------------------------------------------------------------------


class TestEvaluateLrc:
    """Tests for evaluate_lrc function."""

    def test_perfect_match(self):
        """Test perfect match evaluation."""
        lrc = [
            PinyinWord("我", "wo", 10.0),
            PinyinWord("爱", "ai", 11.0),
            PinyinWord("你", "ni", 12.0),
        ]
        audio = [
            PinyinWord("我", "wo", 10.0),
            PinyinWord("爱", "ai", 11.0),
            PinyinWord("你", "ni", 12.0),
        ]
        result = evaluate_lrc(lrc, audio)

        assert result.success
        assert result.stats.matched_count == 3
        assert result.stats.missing_count == 0
        assert result.stats.extra_count == 0
        assert result.scores.text_accuracy == 100.0
        assert result.scores.final_score == 100.0

    def test_with_errors(self):
        """Test evaluation with some errors."""
        lrc = [
            PinyinWord("我", "wo", 10.0),
            PinyinWord("爱", "ai", 11.0),
            PinyinWord("你", "ni", 12.0),
        ]
        audio = [
            PinyinWord("我", "wo", 10.2),  # 200ms error
            PinyinWord("你", "ni", 12.0),
        ]
        result = evaluate_lrc(lrc, audio)

        assert result.success
        assert result.stats.matched_count == 2
        assert result.stats.missing_count == 1
        assert result.stats.extra_count == 0


# --------------------------------------------------------------------------
# Report Formatting Tests
# --------------------------------------------------------------------------


class TestFormatDiffReport:
    """Tests for format_diff_report function."""

    def test_successful_report(self):
        """Test formatting successful report."""
        stats = EvaluationStats(
            lrc_word_count=100,
            audio_word_count=98,
            matched_count=95,
            missing_count=5,
            extra_count=3,
            rms_error_ms=50.0,
            max_error_ms=150.0,
        )
        scores = EvaluationScores(
            text_accuracy=92.0,
            timing_accuracy=90.0,
            final_score=91.2,
            text_weight=0.6,
            timing_weight=0.4,
        )
        result = EvaluationResult(success=True, stats=stats, scores=scores)

        report = format_diff_report(result, song_title="Test Song", song_id="test_123")

        assert "=== LRC Evaluation Report ===" in report
        assert "Test Song" in report
        assert "test_123" in report
        assert "92.0" in report
        assert "90.0" in report
        assert "91.2" in report

    def test_error_report(self):
        """Test formatting error report."""
        result = EvaluationResult(success=False, error_message="File not found")

        report = format_diff_report(result)

        assert "Error: File not found" in report


class TestFormatJsonReport:
    """Tests for format_json_report function."""

    def test_successful_json_report(self):
        """Test JSON formatting of successful report."""
        import json

        stats = EvaluationStats(
            lrc_word_count=100,
            audio_word_count=98,
            matched_count=95,
            missing_count=5,
            extra_count=3,
            rms_error_ms=50.0,
            max_error_ms=150.0,
        )
        scores = EvaluationScores(
            text_accuracy=92.0,
            timing_accuracy=90.0,
            final_score=91.2,
            text_weight=0.6,
            timing_weight=0.4,
        )
        result = EvaluationResult(success=True, stats=stats, scores=scores)

        report = format_json_report(result, song_id="test_123")
        data = json.loads(report)

        assert data["success"] is True
        assert data["song_id"] == "test_123"
        assert data["scores"]["final_score"] == 91.2
        assert data["stats"]["matched_count"] == 95

    def test_error_json_report(self):
        """Test JSON formatting of error report."""
        import json

        result = EvaluationResult(success=False, error_message="File not found")

        report = format_json_report(result)
        data = json.loads(report)

        assert data["success"] is False
        assert data["error"] == "File not found"
