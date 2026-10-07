"""Tests for lab/poc-scripts/mine_lrc_ground_truth.py (Phase 0b mining logic).

Covers the pure helpers: LRC parsing, timing-shift stats, edit classification,
and the pairing-rule constants. R2/DB access is not exercised here — the
production run against live data is the smoke test.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from mine_lrc_ground_truth import (
    HUMAN_SOURCES,
    backup_ts,
    classify_edits,
    is_ms_epoch,
    parse_lrc_lines,
    timing_shift_stats,
)


class TestBackupTs:
    def test_ms_key(self):
        assert backup_ts("abc123/lyrics.backup.1791358556951.lrc") == "1791358556951"

    def test_is_ms_epoch(self):
        assert is_ms_epoch("1791358556951") is True
        # analysis-service loop-time bug values (uptime ms, not wall clock)
        assert is_ms_epoch("274768205") is False

    def test_loop_time_key(self):
        assert backup_ts("abc123/lyrics.backup.274768205.lrc") == "274768205"


class TestParseLrcLines:
    def test_basic_parse(self):
        rows = parse_lrc_lines("[00:15.05]我的耶穌\n[01:02.5]第二行\n")
        assert rows == [
            {"time_s": 15.05, "text": "我的耶穌"},
            {"time_s": 62.5, "text": "第二行"},
        ]

    def test_blank_placeholder_line_kept(self):
        # ADR-0008 gap placeholder: timestamped empty text
        rows = parse_lrc_lines("[00:15.05]第一行\n[01:07.45]\n[01:19.55]第二行")
        assert len(rows) == 3
        assert rows[1] == {"time_s": 67.45, "text": ""}

    def test_metadata_tags_dropped(self):
        rows = parse_lrc_lines("[ti:歌名]\n[ar:測試]\n[00:15.05]我的耶穌")
        assert len(rows) == 1
        assert rows[0]["text"] == "我的耶穌"

    def test_leading_space_after_bracket(self):
        # legacy uploads wrote "[00:15.05] text" (space after bracket)
        rows = parse_lrc_lines("[00:15.05] 我的耶穌")
        assert rows == [{"time_s": 15.05, "text": "我的耶穌"}]


class TestTimingShiftStats:
    def test_uniform_offset_recovered(self):
        before = [{"time_s": 10.0, "text": "同"}, {"time_s": 20.0, "text": "二"}]
        after = [{"time_s": 11.5, "text": "同"}, {"time_s": 21.5, "text": "二"}]
        stats = timing_shift_stats(before, after)
        assert stats["matched_lines"] == 2
        assert stats["all_zero"] is False
        assert stats["shift_seconds_min"] == pytest.approx(1.5)
        assert stats["shift_seconds_max"] == pytest.approx(1.5)

    def test_untouched_lines_zero_shift(self):
        rows = [{"time_s": 10.0, "text": "同"}, {"time_s": 20.0, "text": "二"}]
        stats = timing_shift_stats(rows, rows)
        assert stats["all_zero"] is True
        assert stats["matched_lines"] == 2

    def test_duplicate_texts_matched_by_occurrence(self):
        # repeated chorus lines align by occurrence index — full coverage
        before = [
            {"time_s": 10.0, "text": "副歌"},
            {"time_s": 60.0, "text": "副歌"},
        ]
        after = [
            {"time_s": 12.0, "text": "副歌"},
            {"time_s": 62.0, "text": "副歌"},
        ]
        stats = timing_shift_stats(before, after)
        assert stats["matched_lines"] == 2
        assert stats["total_sung_before"] == 2
        assert stats["all_zero"] is False

    def test_duplicate_texts_zero_shift(self):
        rows = [
            {"time_s": 10.0, "text": "副歌"},
            {"time_s": 60.0, "text": "副歌"},
        ]
        stats = timing_shift_stats(rows, rows)
        assert stats["matched_lines"] == 2
        assert stats["all_zero"] is True

    def test_no_common_texts(self):
        before = [{"time_s": 10.0, "text": "舊"}]
        after = [{"time_s": 10.0, "text": "新"}]
        assert timing_shift_stats(before, after) == {"matched_lines": 0}


class TestClassifyEdits:
    def test_no_change(self):
        rows = parse_lrc_lines("[00:10.0]第一行\n[00:20.0]第二行")
        edits = classify_edits(rows, rows)
        assert edits["sung_lines_before"] == 2
        assert edits["sung_lines_after"] == 2
        assert edits["dropped_texts"] == []
        assert edits["added_texts"] == []
        assert edits["count_changes"] == {}
        assert edits["line_splits_or_merges"] is False
        assert edits["timing"]["all_zero"] is True

    def test_content_only_retime(self):
        before = parse_lrc_lines("[00:10.0]第一行")
        after = parse_lrc_lines("[00:12.0]第一行")
        edits = classify_edits(before, after)
        assert edits["added_texts"] == []
        assert edits["dropped_texts"] == []
        assert edits["timing"]["shift_seconds_mean"] == pytest.approx(2.0)

    def test_line_merge_detected(self):
        before = parse_lrc_lines("[00:10.0]祢與我同坐席 傾聽我心意\n[00:18.0]我的耶穌")
        after = parse_lrc_lines("[00:10.0]祢與我同坐席 傾聽我心意 我的耶穌")
        edits = classify_edits(before, after)
        assert edits["line_splits_or_merges"] is True
        assert edits["sung_lines_before"] == 2
        assert edits["sung_lines_after"] == 1

    def test_net_zero_split_not_flagged(self):
        # one line split into two: the pieces reconstruct the original line
        before = parse_lrc_lines("[00:10.0]祢與我同坐席 傾聽我心意 我的耶穌")
        after = parse_lrc_lines("[00:10.0]祢與我同坐席 傾聽我心意\n[00:18.0]我的耶穌")
        edits = classify_edits(before, after)
        assert edits["line_splits_or_merges"] is True

    def test_repeat_trim_not_flagged_as_split(self):
        # repeated chorus block trimmed: counts change but no reconstruction
        before = parse_lrc_lines("[00:10.0]副歌行\n[00:20.0]副歌行\n[00:30.0]副歌行")
        after = parse_lrc_lines("[00:10.0]副歌行\n[00:20.0]副歌行")
        edits = classify_edits(before, after)
        assert edits["line_splits_or_merges"] is False
        assert edits["count_changes"]["副歌行"] == {"before": 3, "after": 2}
        assert edits["sung_lines_before"] == 3
        assert edits["sung_lines_after"] == 2

    def test_placeholder_insert_detected(self):
        before = parse_lrc_lines("[00:10.0]第一行\n[00:30.0]第二行")
        after = parse_lrc_lines("[00:10.0]第一行\n[00:22.0]\n[00:30.0]第二行")
        edits = classify_edits(before, after)
        assert edits["blank_lines_before"] == 0
        assert edits["blank_lines_after"] == 1
        assert edits["added_texts"] == []

    def test_respacing_only_not_flagged(self):
        # whole ladder re-spaced (multi-space -> single-space): texts differ
        # as strings but normalize equal → re-spacing, not a split/merge
        before = parse_lrc_lines("[00:10.0]禱告   凡事謝恩\n[00:20.0]神在這裡   喜樂無止盡")
        after = parse_lrc_lines("[00:10.0]禱告 凡事謝恩\n[00:20.0]神在這裡 喜樂無止盡")
        edits = classify_edits(before, after)
        assert edits["dropped_texts"] == ["禱告   凡事謝恩", "神在這裡   喜樂無止盡"]
        assert edits["added_texts"] == ["禱告 凡事謝恩", "神在這裡 喜樂無止盡"]
        assert edits["line_splits_or_merges"] is False

    def test_short_phrase_overlap_not_flagged(self):
        # "大聲讚美" appears inside the dropped "不停讚美祢 大聲讚美祢" but the
        # added texts do NOT reconstruct it → repeated-block rewrite, not a split
        before = parse_lrc_lines("[00:10.0]不停讚美祢 大聲讚美祢")
        after = parse_lrc_lines("[00:10.0]我要讚美 不停讚美\n[00:20.0]大聲讚美")
        edits = classify_edits(before, after)
        assert edits["line_splits_or_merges"] is False

    def test_text_correction_detected(self):
        before = parse_lrc_lines("[00:10.0]我讚美讚美 不停讚美")
        after = parse_lrc_lines("[00:10.0]我要讚美 不停讚美")
        edits = classify_edits(before, after)
        assert edits["dropped_texts"] == ["我讚美讚美 不停讚美"]
        assert edits["added_texts"] == ["我要讚美 不停讚美"]

    def test_no_duplicate_added_texts(self):
        # a line added twice appears once in added_texts, with count delta
        before = parse_lrc_lines("[00:10.0]第一行")
        after = parse_lrc_lines("[00:10.0]第一行\n[00:20.0]新句\n[00:30.0]新句")
        edits = classify_edits(before, after)
        assert edits["added_texts"] == ["新句"]
        assert edits["count_changes"]["新句"] == {"before": 0, "after": 2}


class TestReconstructs:
    def test_real_split_two_pieces(self):
        from mine_lrc_ground_truth import _reconstructs

        assert (
            _reconstructs(
                "祢與我同坐席 傾聽我心意 我的耶穌", ["祢與我同坐席 傾聽我心意", "我的耶穌"]
            )
            is True
        )

    def test_real_merge_reading_order(self):
        # pieces must appear in reading order for a real merge
        from mine_lrc_ground_truth import _reconstructs

        assert (
            _reconstructs(
                "祢與我同坐席 傾聽我心意 我的耶穌",
                ["祢與我同坐席 傾聽我心意", "我的耶穌"],
            )
            is True
        )

    def test_single_identical_piece_is_respacing_not_split(self):
        # same line re-spaced (multi-space → single-space): one piece equals
        # the target after space-stripping — not a split/merge
        from mine_lrc_ground_truth import _reconstructs

        assert _reconstructs("禱告   凡事謝恩", ["禱告 凡事謝恩"]) is False

    def test_sub_phrase_overlap_does_not_reconstruct(self):
        from mine_lrc_ground_truth import _reconstructs

        assert _reconstructs("不停讚美祢 大聲讚美祢", ["大聲讚美", "我要讚美 不停讚美"]) is False

    def test_empty_target(self):
        from mine_lrc_ground_truth import _reconstructs

        assert _reconstructs("", ["x"]) is False
        assert _reconstructs("   ", ["x"]) is False


class TestHumanSources:
    def test_spec_values(self):
        # the spec's pairing rule: only these successor sources are pairable
        assert HUMAN_SOURCES == {"manual_upload", "llm_edit"}
