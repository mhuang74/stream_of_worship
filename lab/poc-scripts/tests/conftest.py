from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from poc.songset_constructor.models import SongCandidate


@pytest.fixture
def synthetic_pool() -> list[SongCandidate]:
    return [
        SongCandidate(song_id="s1", title="赞美主", recording_hash_prefix="h001", tempo_bpm=124, musical_key="G", musical_mode="maj", key_confidence=0.9, phase=1, themes={"赞美": 1}, composer="A"),
        SongCandidate(song_id="s2", title="感恩的心", recording_hash_prefix="h002", tempo_bpm=112, musical_key="D", musical_mode="maj", key_confidence=0.9, phase=2, themes={"感恩": 1}, composer="B"),
        SongCandidate(song_id="s3", title="敬拜你", recording_hash_prefix="h003", tempo_bpm=98, musical_key="A", musical_mode="maj", key_confidence=0.9, phase=3, themes={"敬拜": 1}, composer="C"),
        SongCandidate(song_id="s4", title="十字架", recording_hash_prefix="h004", tempo_bpm=86, musical_key="E", musical_mode="min", key_confidence=0.9, phase=4, themes={"十字架": 1}, composer="D"),
        SongCandidate(song_id="s5", title="跟随主", recording_hash_prefix="h005", tempo_bpm=78, musical_key="B", musical_mode="min", key_confidence=0.9, phase=5, themes={"跟随": 1}, composer="E"),
        SongCandidate(song_id="s6", title="复兴", recording_hash_prefix="h006", tempo_bpm=82, musical_key="F#", musical_mode="min", key_confidence=0.9, phase=5, themes={"复兴": 1}, composer="F"),
    ]
