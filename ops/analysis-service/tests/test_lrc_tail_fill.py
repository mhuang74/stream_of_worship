"""Unit tests for tail-coverage fill (YouTube transcript path)."""

from unittest.mock import AsyncMock

import pytest

from sow_analysis.config import settings
from sow_analysis.models import LrcOptions
from sow_analysis.workers.lrc import (
    LRCLine,
    WhisperPhrase,
    fill_uncovered_tail,
    last_lyric_timestamp,
    merge_tail_lines,
    should_fill_tail,
    tail_fill_threshold_seconds,
)


def _line(t: float, text: str) -> LRCLine:
    return LRCLine(time_seconds=t, text=text)


class TestLastLyricTimestamp:
    """Tests for last_lyric_timestamp()."""

    def test_ignores_empty_placeholder_lines(self):
        lines = [_line(10.0, "one"), _line(14.0, ""), _line(16.0, "   "), _line(12.0, "two")]
        assert last_lyric_timestamp(lines) == 12.0

    def test_returns_none_for_all_empty(self):
        assert last_lyric_timestamp([_line(10.0, ""), _line(20.0, " ")]) is None

    def test_returns_none_for_empty_list(self):
        assert last_lyric_timestamp([]) is None


class TestTailFillThresholdSeconds:
    """Tests for tail_fill_threshold_seconds()."""

    def test_absolute_clause_wins_for_long_songs(self):
        # 300 s * 0.15 = 45 s > 30 s -> threshold stays 30 s.
        assert tail_fill_threshold_seconds(300.0) == pytest.approx(30.0)

    def test_fraction_clause_wins_for_short_songs(self):
        assert tail_fill_threshold_seconds(150.0) == pytest.approx(22.5)


class TestShouldFillTail:
    """Tests for should_fill_tail()."""

    def _lines(self) -> list:
        return [_line(10.0, "one"), _line(270.0, "我願跟隨一生活出祢旨意")]

    def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.setattr(settings, "SOW_LRC_TAIL_FILL_ENABLED", False)
        assert should_fill_tail(self._lines(), 300.0) is None

    def test_missing_duration_returns_none(self):
        assert should_fill_tail(self._lines(), None) is None

    def test_nonpositive_duration_returns_none(self):
        assert should_fill_tail(self._lines(), 0.0) is None

    def test_no_nonempty_line_returns_none(self):
        assert should_fill_tail([_line(10.0, "")], 300.0) is None

    def test_gap_below_absolute_threshold_returns_none(self):
        # gap 29.9 <= 30.0
        lines = [_line(10.0, "one"), _line(270.1, "end")]
        assert should_fill_tail(lines, 300.0) is None

    def test_gap_just_above_absolute_threshold_fires(self):
        lines = [_line(10.0, "one"), _line(269.9, "end")]
        assert should_fill_tail(lines, 300.0) == pytest.approx(30.1)

    def test_fraction_rule_fires(self):
        # 150 s duration -> threshold min(30, 22.5) = 22.5; gap 23.0 > 22.5.
        lines = [_line(5.0, "one"), _line(127.0, "end")]
        assert should_fill_tail(lines, 150.0) == pytest.approx(23.0)

    def test_fraction_rule_not_triggered_below(self):
        lines = [_line(5.0, "one"), _line(128.0, "end")]
        assert should_fill_tail(lines, 150.0) is None


class TestMergeTailLines:
    """Tests for merge_tail_lines()."""

    def test_real_case_two_appended_one_dup_dropped(self):
        existing = [
            _line(250.0, "前一句"),
            _line(257.76, "我願跟隨一生活出祢旨意"),
        ]
        recovered = [
            _line(266.28, "我願跟隨一生活出祢旨意"),
            _line(278.12, "我願跟隨一生活出祢旨意"),
            _line(281.08, "我願跟隨一生活出祢旨意"),
        ]
        merged, count = merge_tail_lines(existing, recovered, 257.76, 3.0)
        assert count == 2
        assert [ln.time_seconds for ln in merged[2:]] == [266.28, 278.12]
        assert all(ln.text == "我願跟隨一生活出祢旨意" for ln in merged[2:])

    def test_blank_text_recovered_line_skipped(self):
        existing = [_line(10.0, "one")]
        recovered = [_line(20.0, "  "), _line(25.0, "two")]
        merged, count = merge_tail_lines(existing, recovered, 10.0, 3.0)
        assert count == 1
        assert merged[-1].time_seconds == 25.0

    def test_line_at_or_before_last_ts_skipped(self):
        existing = [_line(10.0, "one")]
        recovered = [_line(10.0, "echo"), _line(9.0, "before"), _line(30.0, "after")]
        merged, count = merge_tail_lines(existing, recovered, 10.0, 3.0)
        assert count == 1
        assert merged[-1].text == "after"

    def test_different_text_near_existing_line_kept(self):
        existing = [_line(10.0, "one")]
        recovered = [_line(11.0, "different line")]
        merged, count = merge_tail_lines(existing, recovered, 10.0, 3.0)
        assert count == 1
        assert merged[-1].text == "different line"

    def test_kept_lines_participate_in_dup_check(self):
        existing = [_line(10.0, "one")]
        recovered = [_line(20.0, "chorus"), _line(21.5, "chorus")]
        merged, count = merge_tail_lines(existing, recovered, 10.0, 3.0)
        # Both kept: 20.0 kept first, 21.5 is 1.5 s apart (< 3 s window) with
        # identical text -> dropped as duplicate emission of one repeat.
        assert count == 1
        assert merged[-1].time_seconds == 20.0

    def test_repeats_3s_apart_both_kept(self):
        existing = [_line(10.0, "one")]
        recovered = [_line(20.0, "chorus"), _line(23.5, "chorus")]
        merged, count = merge_tail_lines(existing, recovered, 10.0, 3.0)
        assert count == 2


class TestFillUncoveredTail:
    """Async tests for fill_uncovered_tail() orchestration."""

    async def test_offsets_and_merge_window(self, monkeypatch, tmp_path):
        existing = [_line(250.0, "前一句"), _line(257.76, "我願跟隨一生活出祢旨意")]
        lyrics = "前一句\n我願跟隨一生活出祢旨意"
        options = LrcOptions(use_qwen3_asr=False, duration_seconds=327.12)

        slice_calls = {}

        async def fake_slice(audio_path, start_seconds, out_path):
            slice_calls["start"] = start_seconds
            return 60.0

        async def fake_align(lyrics_text, phrases, llm_model, **kwargs):
            # Clip-relative times as an LLM aligning a clip would return them;
            # +242.76 (clip start) they land at 266.28 / 278.12 / 281.08.
            return [
                LRCLine(time_seconds=23.52, text="我願跟隨一生活出祢旨意"),
                LRCLine(time_seconds=35.36, text="我願跟隨一生活出祢旨意"),
                LRCLine(time_seconds=38.32, text="我願跟隨一生活出祢旨意"),
            ]

        monkeypatch.setattr("sow_analysis.workers.lrc._slice_audio_tail", fake_slice)
        monkeypatch.setattr("sow_analysis.workers.lrc._llm_align", fake_align)
        monkeypatch.setattr(
            "sow_analysis.workers.lrc._run_whisper_transcription",
            AsyncMock(
                return_value=[
                    WhisperPhrase(text="我願跟隨一生活出祢旨意", start=23.52, end=28.0),
                    WhisperPhrase(text="我願跟隨一生活出祢旨意", start=35.36, end=40.0),
                    WhisperPhrase(text="我願跟隨一生活出祢旨意", start=38.32, end=42.0),
                ]
            ),
        )

        cache_manager = _FakeCacheManager()
        merged, count = await fill_uncovered_tail(
            audio_path=tmp_path / "audio.mp3",
            lyrics_text=lyrics,
            options=options,
            existing_lines=existing,
            duration_seconds=327.12,
            content_hash="abc123",
            temp_dir=tmp_path,
            cache_manager=cache_manager,
            dashscope_semaphore=_FakeSemaphore(),
            local_model_semaphore=None,
            resolved_language="zh",
        )

        # Clip starts with the configured 15 s lead-in before the last lyric line.
        assert slice_calls["start"] == pytest.approx(257.76 - 15.0)
        # Offsets applied relative to clip start, then merged with the dup window:
        # 266.28 and 278.12 appended; 281.08 dropped (2.96 s < 3.0 s dup window).
        appended = merged[2:]
        assert count == 2
        assert [ln.time_seconds for ln in appended] == [
            pytest.approx(266.28),
            pytest.approx(278.12),
        ]
        assert all(ln.text == "我願跟隨一生活出祢旨意" for ln in appended)
        assert cache_manager.saved_whisper is True

    async def test_too_few_phrases_skips_merge(self, monkeypatch, tmp_path):
        existing = [_line(10.0, "one")]
        options = LrcOptions(use_qwen3_asr=False, qwen3_asr_min_usable_segments=3)
        # Tail fill uses its own minimum (default 1) so a single recovered repeat
        # still merges; raise it here to exercise the skip path.
        monkeypatch.setattr(settings, "SOW_LRC_TAIL_MIN_USABLE_PHRASES", 2)

        async def fake_slice(audio_path, start_seconds, out_path):
            return 60.0

        async def fake_align(*args, **kwargs):
            raise AssertionError("_llm_align must not be called")

        monkeypatch.setattr("sow_analysis.workers.lrc._slice_audio_tail", fake_slice)
        monkeypatch.setattr("sow_analysis.workers.lrc._llm_align", fake_align)
        monkeypatch.setattr(
            "sow_analysis.workers.lrc._run_whisper_transcription",
            AsyncMock(return_value=[WhisperPhrase(text="one", start=1.0, end=2.0)]),
        )

        merged, count = await fill_uncovered_tail(
            audio_path=tmp_path / "audio.mp3",
            lyrics_text="one",
            options=options,
            existing_lines=existing,
            duration_seconds=120.0,
            content_hash="abc123",
            temp_dir=tmp_path,
            cache_manager=_FakeCacheManager(),
            dashscope_semaphore=_FakeSemaphore(),
        )

        assert count == 0
        assert merged == existing

    async def test_junk_qwen3_result_falls_back_to_whisper(self, monkeypatch, tmp_path):
        """A valid-but-junk 1-segment Qwen3 result must not short-circuit the merge;
        the Worker falls back to Whisper (mirrors Stage 2 ASR fallback)."""
        existing = [_line(257.76, "我願跟隨一生活出祢旨意")]
        options = LrcOptions(use_qwen3_asr=True, qwen3_asr_min_usable_segments=3)

        async def fake_slice(audio_path, start_seconds, out_path):
            return 60.0

        whisper_calls = {}

        async def fake_align(lyrics_text, phrases, llm_model, **kwargs):
            whisper_calls["prompt_builder"] = kwargs.get("prompt_builder")
            return [
                LRCLine(time_seconds=23.52, text="我願跟隨一生活出祢旨意"),
                LRCLine(time_seconds=35.36, text="我願跟隨一生活出祢旨意"),
                LRCLine(time_seconds=38.32, text="我願跟隨一生活出祢旨意"),
            ]

        monkeypatch.setattr("sow_analysis.workers.lrc._slice_audio_tail", fake_slice)
        monkeypatch.setattr("sow_analysis.workers.lrc._llm_align", fake_align)
        monkeypatch.setattr(
            "sow_analysis.workers.lrc._run_whisper_transcription",
            AsyncMock(
                return_value=[
                    # Clip-relative; +242.76 → 266.28 / 278.12 / 281.08 absolute.
                    WhisperPhrase(text="我願跟隨一生活出祢旨意", start=23.52, end=28.0),
                    WhisperPhrase(text="我願跟隨一生活出祢旨意", start=35.36, end=40.0),
                    WhisperPhrase(text="我願跟隨一生活出祢旨意", start=38.32, end=42.0),
                ]
            ),
        )

        import sow_analysis.workers.lrc as lrc_mod
        from sow_analysis.services.qwen3_asr_client import Qwen3AsrResult, Qwen3AsrSegment

        junk = Qwen3AsrResult(
            segments=[Qwen3AsrSegment(text="摘下星星证明。", start=0.46, end=3.10)],
            words=[],
            text="摘下星星证明。",
            raw_response={},
            model="qwen3-asr-flash",
            region="intl",
            mode="direct",
        )

        class JunkClient:
            async def transcribe(self, audio_path, context=""):
                return junk

        cache_manager = _FakeCacheManager()
        merged, count = await fill_uncovered_tail(
            audio_path=tmp_path / "audio.mp3",
            lyrics_text="我願跟隨一生活出祢旨意",
            options=options,
            existing_lines=existing,
            duration_seconds=327.12,
            content_hash="abc123",
            temp_dir=tmp_path,
            cache_manager=cache_manager,
            dashscope_semaphore=_FakeSemaphore(),
            resolved_language="zh",
            qwen3_client=JunkClient(),
        )

        # Whisper was used (prompt_builder = the Whisper alignment prompt, not Qwen3's);
        # junk clip-relative segment was filtered by the too-few-phrases fallback, and
        # the whisper phrases were aligned and appended (2 kept, 1 dup-window drop,
        # mirroring test_offsets_and_merge_window).
        assert count == 2
        assert [ln.time_seconds for ln in merged[1:]] == [
            pytest.approx(266.28),
            pytest.approx(278.12),
        ]
        assert whisper_calls["prompt_builder"] is lrc_mod._build_alignment_prompt


class TestRealCacheRoundTrip:
    """WhisperPhrase-serialization bug regression: fill_uncovered_tail must save
    dicts into the real CacheManager (json.dumps chokes on dataclasses). Driving
    the real function keeps the conversion under test — handing pre-built dicts
    to CacheManager here would pass even if the bug reverted."""

    async def test_whisper_cache_round_trip(self, monkeypatch, tmp_path):
        from sow_analysis.storage.cache import CacheManager
        from sow_analysis.workers.lrc import build_whisper_transcription_cache_key

        async def fake_slice(audio_path, start_seconds, out_path):
            return 60.0

        async def fake_align(lyrics_text, phrases, llm_model, **kwargs):
            # Clip-relative; +242.76 → 266.28 / 278.12 / 281.08 absolute.
            return [
                LRCLine(time_seconds=23.52, text="我願跟隨一生活出祢旨意"),
                LRCLine(time_seconds=35.36, text="我願跟隨一生活出祢旨意"),
                LRCLine(time_seconds=38.32, text="我願跟隨一生活出祢旨意"),
            ]

        monkeypatch.setattr("sow_analysis.workers.lrc._slice_audio_tail", fake_slice)
        monkeypatch.setattr("sow_analysis.workers.lrc._llm_align", fake_align)
        monkeypatch.setattr(
            "sow_analysis.workers.lrc._run_whisper_transcription",
            AsyncMock(
                return_value=[
                    WhisperPhrase(text="我願跟隨一生活出祢旨意", start=23.52, end=28.0),
                    WhisperPhrase(text="我願跟隨一生活出祢旨意", start=35.36, end=40.0),
                    WhisperPhrase(text="我願跟隨一生活出祢旨意", start=38.32, end=42.0),
                ]
            ),
        )

        options = LrcOptions(use_qwen3_asr=False, whisper_model="large-v3")
        cache_manager = CacheManager(cache_dir=str(tmp_path / "cache"))
        existing = [_line(250.0, "前一句"), _line(257.76, "我願跟隨一生活出祢旨意")]

        merged, count = await fill_uncovered_tail(
            audio_path=tmp_path / "audio.mp3",
            lyrics_text="前一句\n我願跟隨一生活出祢旨意",
            options=options,
            existing_lines=existing,
            duration_seconds=327.12,
            content_hash="abc123",
            temp_dir=tmp_path,
            cache_manager=cache_manager,
            dashscope_semaphore=_FakeSemaphore(),
            resolved_language="zh",
        )
        assert count == 2

        # Rebuild the key exactly as fill_uncovered_tail does
        # (clip_start = 257.76 − 15.0 = 242.76 → tail-v2-ns0.60+242.8).
        key = build_whisper_transcription_cache_key(
            content_hash="abc123",
            lyrics_text="前一句\n我願跟隨一生活出祢旨意",
            stem_kind="full_mix",
            model="large-v3",
            language="zh",
            range_suffix=(
                f"tail-v2-ns{settings.SOW_LRC_TAIL_WHISPER_MAX_NO_SPEECH_PROB:.2f}+242.8"
            ),
        )
        loaded = cache_manager.get_whisper_transcription(key)
        assert loaded is not None and len(loaded) == 3
        assert loaded[0]["text"] == "我願跟隨一生活出祢旨意"
        assert loaded[0]["start"] == pytest.approx(23.52)


class _FakeCacheManager:
    """Minimal CacheManager stand-in covering the whisper tail path."""

    def __init__(self):
        self.saved_whisper = False

    def get_whisper_transcription(self, content_hash: str):
        return None

    def save_whisper_transcription(self, content_hash: str, phrases):
        self.saved_whisper = True
        return None

    def get_qwen3_asr_transcription(self, cache_key: str):
        return None

    def save_qwen3_asr_transcription(self, cache_key: str, payload):
        return None


class _FakeSemaphore:
    def __aenter__(self):
        return self

    def __aexit__(self, *exc):
        return False
