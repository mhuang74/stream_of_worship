from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)


def _snake_to_camel(name: str) -> str:
    components = name.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


def dataclass_to_camel_case_dict(obj: Any) -> dict | list | str | int | float | bool | None:
    if is_dataclass(obj) and not isinstance(obj, type):
        result = {}
        for f in fields(obj):
            value = getattr(obj, f.name)
            result[_snake_to_camel(f.name)] = dataclass_to_camel_case_dict(value)
        return result
    elif isinstance(obj, tuple):
        return [dataclass_to_camel_case_dict(item) for item in obj]
    elif isinstance(obj, list):
        return [dataclass_to_camel_case_dict(item) for item in obj]
    else:
        return obj


@dataclass(frozen=True)
class ChapterLine:
    text: str
    start_seconds: float

    def to_camel_case_dict(self) -> dict:
        return dataclass_to_camel_case_dict(self)


@dataclass(frozen=True)
class Chapter:
    position: int
    song_title: str
    start_seconds: float
    end_seconds: float
    lines: tuple[ChapterLine, ...] = field(default_factory=tuple)

    def to_camel_case_dict(self) -> dict:
        return dataclass_to_camel_case_dict(self)


@dataclass(frozen=True)
class ChaptersManifest:
    chapters: tuple[Chapter, ...] = field(default_factory=tuple)
    total_duration_seconds: float = 0.0
    generated_at: str = ""

    def to_camel_case_dict(self) -> dict:
        return dataclass_to_camel_case_dict(self)


class SegmentInfo(Protocol):
    item: object
    start_time_seconds: float
    duration_seconds: float


class LrcDownloadCallback(Protocol):
    def __call__(self, hash_prefix: str) -> str | None: ...


class LyricsCallback(Protocol):
    async def __call__(self, hash_prefix: str, start_seconds: float) -> list[ChapterLine]: ...


def build_chapters_from_segments(
    segments: list[SegmentInfo],
    get_lyrics: Callable[[str, float], list[ChapterLine] | object],
) -> list[Chapter]:
    def _build_chapter(i: int, segment: SegmentInfo) -> Chapter:
        start_seconds = segment.start_time_seconds
        end_seconds = start_seconds + segment.duration_seconds
        item = segment.item
        song_title = getattr(item, "song_title", None) or getattr(item, "song_id", None) or f"Song {i + 1}"
        hash_prefix = getattr(item, "recording_hash_prefix", None)
        if hash_prefix:
            result = get_lyrics(hash_prefix, start_seconds)
            lines = result
        else:
            lines = []

        return Chapter(
            position=i + 1,
            song_title=song_title,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            lines=tuple(lines),
        )

    return [_build_chapter(i, seg) for i, seg in enumerate(segments)]


def generate_chapters_manifest(
    segments: list[SegmentInfo],
    download_lrc: Callable[[str], str | None | object],
    total_duration_seconds: float,
) -> ChaptersManifest:
    from .lrc_parser import parse_lrc

    def get_lyrics(
        hash_prefix: str, start_seconds: float
    ) -> list[ChapterLine]:
        try:
            lrc_content = download_lrc(hash_prefix)
            if lrc_content:
                local_lyrics = parse_lrc(lrc_content)
                return [
                    ChapterLine(
                        text=line.text,
                        start_seconds=start_seconds + line.time_seconds,
                    )
                    for line in local_lyrics
                ]
        except Exception:
            logger.warning("Failed to parse LRC for hash_prefix %s", hash_prefix, exc_info=True)
            pass
        return []

    chapters = build_chapters_from_segments(segments, get_lyrics)
    return ChaptersManifest(
        chapters=tuple(chapters),
        total_duration_seconds=total_duration_seconds,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def chapters_to_ffmpeg_metadata(manifest: ChaptersManifest) -> str:
    lines: list[str] = [";FFMETADATA1"]
    for chapter in manifest.chapters:
        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append(f"START={int(chapter.start_seconds * 1000)}")
        lines.append(f"END={int(chapter.end_seconds * 1000)}")
        lines.append(f"title={chapter.song_title}")
    return "\n".join(lines)


def find_chapter_at_time(manifest: ChaptersManifest, position_seconds: float) -> int:
    for i, chapter in enumerate(manifest.chapters):
        if position_seconds >= chapter.start_seconds and position_seconds < chapter.end_seconds:
            return i
    if manifest.chapters:
        last_chapter = manifest.chapters[-1]
        if position_seconds == last_chapter.end_seconds:
            return len(manifest.chapters) - 1
    return -1


def get_song_title_at_time(manifest: ChaptersManifest, position_seconds: float) -> str | None:
    chapter_index = find_chapter_at_time(manifest, position_seconds)
    if chapter_index >= 0:
        return manifest.chapters[chapter_index].song_title
    return None


def get_lyric_at_time(manifest: ChaptersManifest, position_seconds: float) -> str | None:
    chapter_index = find_chapter_at_time(manifest, position_seconds)
    if chapter_index < 0:
        return None
    chapter = manifest.chapters[chapter_index]
    for i in range(len(chapter.lines) - 1, -1, -1):
        if position_seconds >= chapter.lines[i].start_seconds:
            return chapter.lines[i].text
    return None


def parse_chapters_manifest(json_str: str) -> ChaptersManifest:
    parsed = json.loads(json_str)
    if not isinstance(parsed.get("chapters"), list):
        raise ValueError("Invalid chapters manifest: chapters must be an array")
    if not isinstance(parsed.get("totalDurationSeconds"), (int, float)):
        raise ValueError("Invalid chapters manifest: totalDurationSeconds must be a number")
    if not isinstance(parsed.get("generatedAt"), str):
        raise ValueError("Invalid chapters manifest: generatedAt must be a string")

    chapters: list[Chapter] = []
    for chapter_data in parsed["chapters"]:
        if (
            not isinstance(chapter_data.get("position"), (int, float))
            or not isinstance(chapter_data.get("songTitle"), str)
            or not isinstance(chapter_data.get("startSeconds"), (int, float))
            or not isinstance(chapter_data.get("endSeconds"), (int, float))
            or not isinstance(chapter_data.get("lines"), list)
        ):
            raise ValueError("Invalid chapter structure")

        chapter_lines: list[ChapterLine] = []
        for line_data in chapter_data["lines"]:
            if (
                not isinstance(line_data.get("text"), str)
                or not isinstance(line_data.get("startSeconds"), (int, float))
            ):
                raise ValueError("Invalid chapter line structure")
            chapter_lines.append(
                ChapterLine(
                    text=line_data["text"],
                    start_seconds=float(line_data["startSeconds"]),
                )
            )

        chapters.append(
            Chapter(
                position=int(chapter_data["position"]),
                song_title=chapter_data["songTitle"],
                start_seconds=float(chapter_data["startSeconds"]),
                end_seconds=float(chapter_data["endSeconds"]),
                lines=tuple(chapter_lines),
            )
        )

    return ChaptersManifest(
        chapters=tuple(chapters),
        total_duration_seconds=float(parsed["totalDurationSeconds"]),
        generated_at=parsed["generatedAt"],
    )
