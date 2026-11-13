from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sow_render_worker.audio_engine import AudioClipInfo


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

#: Beats of source audio a lyric onset may sit outside a clip's bounds.
#: Section bounds are beat-snapped while LRC onsets come from forced
#: alignment, so a pickup phrase can start up to a beat or two before the
#: section it is sung into.
LYRIC_BOUNDARY_TOLERANCE_BEATS = 2.0
#: Tolerance when the item has no usable tempo; the pre-tempo fixed bound.
FALLBACK_LYRIC_BOUNDARY_TOLERANCE_SECONDS = 0.5
_OWNER_EPSILON = 1e-9

SourceWindow = tuple[float, float]


def lyric_boundary_tolerance(tempo_bpm: float | None) -> float:
    """Two beats at ``tempo_bpm``, or the fixed fallback without a usable tempo."""
    if tempo_bpm is None or not math.isfinite(tempo_bpm) or tempo_bpm <= 0:
        return FALLBACK_LYRIC_BOUNDARY_TOLERANCE_SECONDS
    return (LYRIC_BOUNDARY_TOLERANCE_BEATS * 60.0) / tempo_bpm


def _nearest_start_owner(
    windows: Sequence[SourceWindow],
    time_seconds: float,
    tolerance: float = FALLBACK_LYRIC_BOUNDARY_TOLERANCE_SECONDS,
) -> int:
    """
    Index of the window that owns ``time_seconds``, or -1.

    Mirrors the webapp's ``nearestStartOwnerIndex``: a window is a candidate
    when the time falls inside ``[start - tolerance, end + tolerance)``; among
    candidates the nearest *start* wins, ties resolving to the greater start,
    then the greater end, then the lowest index. Zero-length windows are never
    candidates. ``tolerance`` is the tempo-derived bound
    :func:`lyric_boundary_tolerance` supplies; the default is the no-tempo
    fallback.
    """
    best = -1
    best_distance = float("inf")
    for index, (start, end) in enumerate(windows):
        if not end > start:
            continue
        if time_seconds < start - tolerance:
            continue
        if time_seconds >= end + tolerance:
            continue
        distance = abs(time_seconds - start)
        if best == -1 or distance < best_distance - _OWNER_EPSILON:
            best = index
            best_distance = distance
            continue
        if abs(distance - best_distance) > _OWNER_EPSILON:
            continue
        current_start, current_end = windows[best]
        if start > current_start + _OWNER_EPSILON:
            best = index
        elif (
            abs(start - current_start) <= _OWNER_EPSILON
            and end > current_end + _OWNER_EPSILON
        ):
            best = index
    return best


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
        lines.append(LRCLine(time_seconds=time_seconds, text=text))
    lines.sort(key=lambda line: line.time_seconds)
    return lines


def remap_lrc_to_clips(
    local_lines: list[LRCLine],
    clips: tuple[AudioClipInfo, ...],
    title: str,
    tempo_bpm: float | None = None,
) -> list[GlobalLRCLine]:
    """
    Remap local LRC lines through arranged clips onto the item timeline.

    Ownership is decided globally by :func:`_nearest_start_owner` over every
    clip's source window, exactly as the webapp's ``linesForSourceRange`` does:
    a line is a candidate for every window whose bounds it is within
    ``lyric_boundary_tolerance(tempo_bpm)`` (two beats at the Recording's tempo)
    of, and the window with the nearest start keeps it. A pickup sung just
    before a Step therefore lands on that Step even when the section between
    them was omitted from the arrangement, and a line just after a Step's end
    still belongs to that Step (its phrase is sung into that section, and the
    cut may clip its audio). Consecutive clips tile their shared boundary: no
    line is emitted twice and none disappears.

    A line's global timestamp is the clip's target start plus its offset into
    the source range, clamped to ``[clip.start_time_seconds,
    clip.start_time_seconds + span - 0.001]``: a pre-start pickup lands on the
    clip's first frame rather than the preceding item, and a late line within
    tolerance never highlights inside the next Item. Repeated steps repeat their
    lyrics at new target times; lines from omitted source ranges never appear.
    Blank Gap Placeholder lines inside a clip's window are retained.

    Lines are stably sorted by global timestamp (per-clip output is already
    ordered, and clips are processed in arrangement order).
    """
    windows: list[SourceWindow] = [
        (clip.source_start_seconds, clip.source_end_seconds)
        for clip in clips
        if clip.source_end_seconds > clip.source_start_seconds
    ]
    tolerance = lyric_boundary_tolerance(tempo_bpm)
    owners = [_nearest_start_owner(windows, line.time_seconds, tolerance) for line in local_lines]
    remapped: list[GlobalLRCLine] = []
    for clip in clips:
        span = clip.source_end_seconds - clip.source_start_seconds
        if not span > 0:
            continue
        for line, owner in zip(local_lines, owners, strict=True):
            if owner < 0:
                continue
            owner_start, owner_end = windows[owner]
            if not (
                math.isclose(owner_start, clip.source_start_seconds, abs_tol=1e-9)
                and math.isclose(owner_end, clip.source_end_seconds, abs_tol=1e-9)
            ):
                continue
            global_time = max(
                clip.start_time_seconds,
                clip.start_time_seconds + line.time_seconds - clip.source_start_seconds,
            )
            # A late line within tolerance is owned by this clip; keep it inside
            # the clip's own target window so the highlight never leaks into the
            # next Item.
            global_time = min(global_time, clip.start_time_seconds + span - 0.001)
            remapped.append(
                GlobalLRCLine(
                    text=line.text,
                    local_time_seconds=line.time_seconds,
                    global_time_seconds=global_time,
                    title=title,
                )
            )
    remapped.sort(key=lambda line: line.global_time_seconds)
    return remapped


def estimate_last_lyric_duration(
    song_lyrics: list[GlobalLRCLine],
    tempo_bpm: float | None = None,
) -> float:
    if not song_lyrics:
        return 5.0

    last_lyric_index = -1
    for i in range(len(song_lyrics) - 1, -1, -1):
        if song_lyrics[i].text.strip():
            last_lyric_index = i
            break

    if last_lyric_index < 0:
        return 5.0

    last_lyric = song_lyrics[last_lyric_index]

    for i in range(last_lyric_index - 1, -1, -1):
        if song_lyrics[i].text == last_lyric.text:
            if i + 1 < len(song_lyrics):
                duration = (
                    song_lyrics[i + 1].global_time_seconds - song_lyrics[i].global_time_seconds
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
