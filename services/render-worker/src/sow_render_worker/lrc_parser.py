from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class LRCLine:
    time_seconds: float
    text: str


@dataclass(frozen=True)
class GlobalLRCLine:
    text: str
    local_time_seconds: float
    global_time_seconds: float
    title: str


_LRC_PATTERN = re.compile(r"\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)")
_VALID_LRC_PATTERN = re.compile(r"\[\d{2}:\d{2}\.\d{2,3}\]")


def parse_lrc(lrc_content: str) -> list[LRCLine]:
    lines: list[LRCLine] = []
    for raw_line in lrc_content.split("\n"):
        match = _LRC_PATTERN.match(raw_line.strip())
        if not match:
            continue
        minutes = int(match.group(1))
        seconds = int(match.group(2))
        ms_str = match.group(3)
        milliseconds = int(ms_str.ljust(3, "0")[:3])
        text = match.group(4).strip()
        time_seconds = minutes * 60 + seconds + milliseconds / 1000.0
        if text:
            lines.append(LRCLine(time_seconds=time_seconds, text=text))
    lines.sort(key=lambda l: l.time_seconds)
    return lines


def convert_to_global_timeline(
    local_lines: list[LRCLine],
    segment_start_seconds: float,
    title: str,
) -> list[GlobalLRCLine]:
    return [
        GlobalLRCLine(
            text=line.text,
            local_time_seconds=line.time_seconds,
            global_time_seconds=segment_start_seconds + line.time_seconds,
            title=title,
        )
        for line in local_lines
    ]


def estimate_last_lyric_duration(
    song_lyrics: list[GlobalLRCLine],
    tempo_bpm: float | None = None,
) -> float:
    if not song_lyrics:
        return 5.0

    last_lyric = song_lyrics[-1]

    for i in range(len(song_lyrics) - 2, -1, -1):
        if song_lyrics[i].text == last_lyric.text:
            if i + 1 < len(song_lyrics):
                duration = (
                    song_lyrics[i + 1].global_time_seconds
                    - song_lyrics[i].global_time_seconds
                )
                return max(3.0, duration)

    char_count = 0.0
    for char in last_lyric.text:
        code = ord(char)
        if (0x4E00 <= code <= 0x9FFF) or (0x3400 <= code <= 0x4DBF) or (0x3000 <= code <= 0x303F):
            char_count += 1.0
        else:
            char_count += 0.5

    bpm = tempo_bpm if tempo_bpm and tempo_bpm > 0 else 70.0
    beats_per_beat = 60.0 / bpm
    duration = char_count * 2 * beats_per_beat

    return max(3.0, duration)


def find_current_lyric_index(
    lyrics: list[GlobalLRCLine],
    current_time_seconds: float,
) -> int:
    current_index = -1
    for i, line in enumerate(lyrics):
        if line.global_time_seconds <= current_time_seconds:
            current_index = i
        else:
            break
    return current_index


def group_lyrics_by_song(
    lyrics: list[GlobalLRCLine],
) -> dict[str, list[GlobalLRCLine]]:
    grouped: dict[str, list[GlobalLRCLine]] = {}
    for line in lyrics:
        grouped.setdefault(line.title, []).append(line)
    return grouped


def is_valid_lrc(lrc_content: str) -> bool:
    return bool(_VALID_LRC_PATTERN.search(lrc_content))


def get_lyrics_time_range(
    lyrics: list[LRCLine],
) -> dict[str, float] | None:
    if not lyrics:
        return None
    return {
        "first_time": lyrics[0].time_seconds,
        "last_time": lyrics[-1].time_seconds,
    }
