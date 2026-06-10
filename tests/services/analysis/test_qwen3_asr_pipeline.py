from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from sow_analysis.models import LrcOptions
from sow_analysis.services.canonical_snap import snap_qwen3_asr_to_canonical
from sow_analysis.services.qwen3_asr_client import (
    Qwen3AsrClient,
    Qwen3AsrResult,
    Qwen3AsrSegment,
    Qwen3AsrWord,
)
from sow_analysis.storage.cache import CacheManager
from sow_analysis.workers.lrc import generate_lrc_from_qwen3_asr


def _qwen_result() -> Qwen3AsrResult:
    return Qwen3AsrResult(
        segments=[
            Qwen3AsrSegment("我要看见", 0.0, 2.0),
            Qwen3AsrSegment("如同摩西看见你的荣耀", 2.0, 5.0),
            Qwen3AsrSegment("我要看见", 5.0, 7.0),
        ],
        words=[],
        text="我要看见如同摩西看见你的荣耀我要看见",
        raw_response={"ok": True},
        model="qwen3-asr-flash",
        region="intl",
        mode="direct",
    )


def test_canonical_snap_preserves_repeated_lines():
    result = _qwen_result()
    snapped = snap_qwen3_asr_to_canonical(
        result,
        "我要看見\n如同摩西看見祢的榮耀\n我要看見",
        threshold=0.5,
    )

    assert [p.text for p in snapped] == [
        "我要看見",
        "如同摩西看見祢的榮耀",
        "我要看見",
    ]


def test_qwen_client_parses_direct_segments_and_words():
    client = Qwen3AsrClient(api_key="test")
    result = client._parse_result(
        {
            "choices": [
                {
                    "message": {
                        "content": [
                            {
                                "type": "audio_transcription",
                                "audio_transcription_results": {
                                    "sentences": [
                                        {
                                            "text": "我要看見",
                                            "begin_time": 1000,
                                            "end_time": 3000,
                                            "words": [
                                                {
                                                    "text": "我要",
                                                    "begin_time": 1000,
                                                    "end_time": 1800,
                                                },
                                                {
                                                    "text": "看見",
                                                    "begin_time": 1800,
                                                    "end_time": 3000,
                                                },
                                            ],
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                }
            ]
        },
        model="qwen3-asr-flash",
        mode="direct",
    )

    assert result.segments[0].text == "我要看見"
    assert result.segments[0].start == 1.0
    assert [w.text for w in result.words] == ["我要", "看見"]


def test_qwen_client_always_converts_dashscope_milliseconds_to_seconds():
    client = Qwen3AsrClient(api_key="test")

    assert client._ms_to_seconds(500) == 0.5
    assert client._ms_to_seconds(1000) == 1.0
    assert client._ms_to_seconds(1050000) == 1050.0


def test_qwen_client_flattens_filetrans_transcript_sentences():
    client = Qwen3AsrClient(api_key="test")
    result = client._parse_result(
        {
            "transcripts": [
                {
                    "channel_id": 0,
                    "sentences": [
                        {
                            "text": "我要看見",
                            "begin_time": 500,
                            "end_time": 1500,
                            "words": [
                                {"text": "我要", "begin_time": 500, "end_time": 900},
                                {"text": "看見", "begin_time": 900, "end_time": 1500},
                            ],
                        }
                    ],
                }
            ]
        },
        model="qwen3-asr-flash-filetrans",
        mode="filetrans",
    )

    assert result.segments[0].text == "我要看見"
    assert result.segments[0].start == 0.5
    assert [word.text for word in result.words] == ["我要", "看見"]


def test_qwen_client_routes_to_filetrans_over_direct_size_limit(tmp_path: Path):
    audio_path = tmp_path / "large.mp3"
    with audio_path.open("wb") as file:
        file.truncate((10 * 1024 * 1024) + 1)

    assert Qwen3AsrClient(api_key="test")._choose_mode(audio_path) == "filetrans"


def test_qwen_client_routes_to_filetrans_over_direct_duration_limit(tmp_path: Path):
    audio_path = tmp_path / "long.mp3"
    audio_path.write_bytes(b"small")
    fake_librosa = SimpleNamespace(get_duration=lambda path: 301.0)

    with patch.dict("sys.modules", {"librosa": fake_librosa}):
        assert Qwen3AsrClient(api_key="test")._choose_mode(audio_path) == "filetrans"


def test_qwen_client_routes_to_direct_for_small_short_audio(tmp_path: Path):
    audio_path = tmp_path / "short.mp3"
    audio_path.write_bytes(b"small")
    fake_librosa = SimpleNamespace(get_duration=lambda path: 300.0)

    with patch.dict("sys.modules", {"librosa": fake_librosa}):
        assert Qwen3AsrClient(api_key="test")._choose_mode(audio_path) == "direct"


def test_canonical_snap_does_not_bias_ordered_search_to_final_line():
    result = Qwen3AsrResult(
        segments=[
            Qwen3AsrSegment("final", 0.0, 1.0),
            Qwen3AsrSegment("fina", 1.0, 2.0),
        ],
        words=[],
        text="finalfina",
        raw_response={"ok": True},
        model="qwen3-asr-flash",
        region="intl",
        mode="direct",
    )

    snapped = snap_qwen3_asr_to_canonical(result, "fina\nfinal", threshold=0.8)

    assert [phrase.text for phrase in snapped] == ["final", "fina"]


@pytest.mark.asyncio
async def test_qwen_lrc_alignment_uses_snapped_phrases(tmp_path: Path):
    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"fake")
    cache = CacheManager(tmp_path / "cache")
    cache_key = "qwen-cache-key"
    cache.save_qwen3_asr_transcription(cache_key, _qwen_result().to_cache_payload())
    output_path = tmp_path / "lyrics.lrc"

    aligned = [
        {"time_seconds": 0.0, "text": "我要看見"},
        {"time_seconds": 2.0, "text": "如同摩西看見祢的榮耀"},
        {"time_seconds": 5.0, "text": "我要看見"},
    ]

    async def fake_llm_align(lyrics_text, phrases, llm_model, max_retries=3, prompt_builder=None):
        assert [p.text for p in phrases] == [
            "我要看見",
            "如同摩西看見祢的榮耀",
            "我要看見",
        ]
        from sow_analysis.workers.lrc import LRCLine

        return [LRCLine(**item) for item in aligned]

    with patch("sow_analysis.workers.lrc._llm_align", new=AsyncMock(side_effect=fake_llm_align)):
        path, line_count, phrases = await generate_lrc_from_qwen3_asr(
            audio_path,
            "我要看見\n如同摩西看見祢的榮耀\n我要看見",
            LrcOptions(qwen3_asr_min_usable_segments=3, qwen3_asr_snap_threshold=0.5),
            output_path,
            cache_key,
            cache,
            dashscope_semaphore=__import__("asyncio").Semaphore(1),
        )

    assert path == output_path
    assert line_count == 3
    assert [p.text for p in phrases] == [item["text"] for item in aligned]
    assert "[00:02.00] 如同摩西看見祢的榮耀" in output_path.read_text(encoding="utf-8")
