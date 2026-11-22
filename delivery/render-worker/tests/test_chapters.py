import json
import dataclasses
from dataclasses import dataclass, fields as dc_fields
from pathlib import Path

import pytest

from sow_render_worker.audio_engine import AudioClipInfo
from sow_render_worker.chapters import (
    Chapter,
    ChapterLine,
    ChaptersManifest,
    _snake_to_camel,
    base_song_title,
    build_chapters_from_segments,
    chapter_titles,
    chapters_to_ffmpeg_metadata,
    dataclass_to_camel_case_dict,
    find_chapter_at_time,
    generate_chapters_manifest,
    get_lyric_at_time,
    get_song_title_at_time,
    parse_chapters_manifest,
)


@dataclass
class FakeItem:
    song_title: str | None
    song_id: str
    recording_hash_prefix: str | None
    # Mirrors `SongsetItem.tempo_bpm`; None keeps the pre-tempo 0.5 s lyric
    # ownership bound these cases were written against.
    tempo_bpm: float | None = None


@dataclass
class FakeSegment:
    item: FakeItem
    start_time_seconds: float
    duration_seconds: float
    clips: tuple[AudioClipInfo, ...] = ()

    def __post_init__(self) -> None:
        if not self.clips:
            self.clips = (
                AudioClipInfo(
                    audio_path="/tmp/audio.mp3",
                    source_start_seconds=0.0,
                    source_end_seconds=self.duration_seconds,
                    start_time_seconds=self.start_time_seconds,
                ),
            )


def _make_chapter_line(text: str, start: float) -> ChapterLine:
    return ChapterLine(text=text, start_seconds=start)


def _make_chapter(
    position: int,
    title: str,
    start: float,
    end: float,
    lines: list[ChapterLine] | None = None,
) -> Chapter:
    return Chapter(
        position=position,
        song_title=title,
        start_seconds=start,
        end_seconds=end,
        lines=tuple(lines) if lines else (),
    )


def _make_manifest(
    chapters: list[Chapter] | None = None,
    duration: float = 100.0,
    generated_at: str = "2024-01-01T00:00:00+00:00",
) -> ChaptersManifest:
    return ChaptersManifest(
        chapters=tuple(chapters) if chapters else (),
        total_duration_seconds=duration,
        generated_at=generated_at,
    )


class TestChapterLine:
    def test_creation(self):
        line = ChapterLine(text="Hello", start_seconds=5.0)
        assert line.text == "Hello"
        assert line.start_seconds == 5.0

    def test_frozen(self):
        line = ChapterLine(text="Hello", start_seconds=5.0)
        with pytest.raises(AttributeError):
            line.text = "Changed"

    def test_equality(self):
        a = ChapterLine(text="Hello", start_seconds=5.0)
        b = ChapterLine(text="Hello", start_seconds=5.0)
        assert a == b


class TestChapter:
    def test_creation(self):
        lines = (ChapterLine(text="Hi", start_seconds=1.0),)
        ch = Chapter(position=1, song_title="Song", start_seconds=0.0, end_seconds=30.0, lines=lines)
        assert ch.position == 1
        assert ch.song_title == "Song"
        assert ch.start_seconds == 0.0
        assert ch.end_seconds == 30.0
        assert len(ch.lines) == 1

    def test_default_empty_lines(self):
        ch = Chapter(position=1, song_title="Song", start_seconds=0.0, end_seconds=30.0)
        assert ch.lines == ()

    def test_frozen(self):
        ch = Chapter(position=1, song_title="Song", start_seconds=0.0, end_seconds=30.0)
        with pytest.raises(AttributeError):
            ch.song_title = "Changed"


class TestChaptersManifest:
    def test_creation(self):
        manifest = ChaptersManifest(
            chapters=(),
            total_duration_seconds=120.0,
            generated_at="2024-01-01T00:00:00Z",
        )
        assert manifest.total_duration_seconds == 120.0
        assert manifest.chapters == ()

    def test_defaults(self):
        manifest = ChaptersManifest()
        assert manifest.chapters == ()
        assert manifest.total_duration_seconds == 0.0
        assert manifest.generated_at == ""

    def test_frozen(self):
        manifest = ChaptersManifest()
        with pytest.raises(AttributeError):
            manifest.total_duration_seconds = 999.0


class TestBuildChaptersFromSegments:
    def test_basic_build(self):
        segments = [
            FakeSegment(FakeItem("Song A", "id1", "hash1"), 0.0, 30.0),
            FakeSegment(FakeItem("Song B", "id2", "hash2"), 30.0, 45.0),
        ]

        def get_lyrics(hash_prefix: str, segment: FakeSegment) -> list[ChapterLine]:
            return [ChapterLine(text="Line 1", start_seconds=segment.start_time_seconds + 5.0)]

        chapters = build_chapters_from_segments(segments, get_lyrics)
        assert len(chapters) == 2
        assert chapters[0].position == 1
        assert chapters[0].song_title == "Song A"
        assert chapters[0].start_seconds == 0.0
        assert chapters[0].end_seconds == 30.0
        assert len(chapters[0].lines) == 1
        assert chapters[1].position == 2
        assert chapters[1].start_seconds == 30.0
        assert chapters[1].end_seconds == 75.0

    def test_no_hash_prefix_empty_lines(self):
        segments = [
            FakeSegment(FakeItem("Song A", "id1", None), 0.0, 30.0),
        ]

        def get_lyrics(hash_prefix: str, segment: FakeSegment) -> list[ChapterLine]:
            return [ChapterLine(text="Should not appear", start_seconds=0.0)]

        chapters = build_chapters_from_segments(segments, get_lyrics)
        assert len(chapters) == 1
        assert chapters[0].lines == ()

    def test_async_get_lyrics(self):
        segments = [
            FakeSegment(FakeItem("Song A", "id1", "hash1"), 0.0, 30.0),
        ]

        def get_lyrics(hash_prefix: str, segment: FakeSegment) -> list[ChapterLine]:
            return [ChapterLine(text="Async line", start_seconds=segment.start_time_seconds + 3.0)]

        chapters = build_chapters_from_segments(segments, get_lyrics)
        assert len(chapters[0].lines) == 1
        assert chapters[0].lines[0].text == "Async line"

    def test_fallback_song_id_when_no_title(self):
        segments = [
            FakeSegment(FakeItem(None, "my-song-id", "hash1"), 0.0, 30.0),
        ]

        def get_lyrics(hash_prefix: str, segment: FakeSegment) -> list[ChapterLine]:
            return []

        chapters = build_chapters_from_segments(segments, get_lyrics)
        assert chapters[0].song_title == "my-song-id"

    def test_fallback_position_when_no_title_no_id(self):
        segments = [
            FakeSegment(FakeItem(None, "", "hash1"), 0.0, 30.0),
        ]

        def get_lyrics(hash_prefix: str, segment: FakeSegment) -> list[ChapterLine]:
            return []

        chapters = build_chapters_from_segments(segments, get_lyrics)
        assert chapters[0].song_title == "Song 1"

    def test_empty_segments(self):
        chapters = build_chapters_from_segments([], lambda h, s: [])
        assert chapters == []

    def test_repeated_song_titles_take_occurrence_ordinals(self):
        segments = [
            FakeSegment(FakeItem("A", "id1", "hash1"), 0.0, 30.0),
            FakeSegment(FakeItem("B", "id2", "hash2"), 30.0, 45.0),
            FakeSegment(FakeItem("A", "id1", "hash1"), 45.0, 15.0),
        ]

        chapters = build_chapters_from_segments(segments, lambda h, s: [])

        assert [chapter.song_title for chapter in chapters] == ["A", "B", "A (2)"]

    def test_distinct_titles_are_untouched(self):
        segments = [
            FakeSegment(FakeItem("A", "id1", "hash1"), 0.0, 30.0),
            FakeSegment(FakeItem("B", "id2", "hash2"), 30.0, 45.0),
        ]

        chapters = build_chapters_from_segments(segments, lambda h, s: [])

        assert [chapter.song_title for chapter in chapters] == ["A", "B"]

    def test_third_occurrence_suffix(self):
        segments = [
            FakeSegment(FakeItem("A", "id1", "hash1"), 0.0, 30.0),
            FakeSegment(FakeItem("A", "id1", "hash1"), 30.0, 30.0),
            FakeSegment(FakeItem("A", "id1", "hash1"), 60.0, 30.0),
        ]

        chapters = build_chapters_from_segments(segments, lambda h, s: [])

        assert [chapter.song_title for chapter in chapters] == ["A", "A (2)", "A (3)"]


class TestGenerateChaptersManifest:
    def test_basic_generation(self):
        segments = [
            FakeSegment(FakeItem("Song A", "id1", "hash1"), 0.0, 30.0),
            FakeSegment(FakeItem("Song B", "id2", "hash2"), 30.0, 45.0),
        ]

        lrc_map = {
            "hash1": "[00:05.00]Line 1\n[00:10.00]Line 2",
            "hash2": "[00:03.00]Line 3",
        }

        def download_lrc(hash_prefix: str) -> str | None:
            return lrc_map.get(hash_prefix)

        manifest = generate_chapters_manifest(segments, download_lrc, 75.0)
        assert manifest.total_duration_seconds == 75.0
        assert len(manifest.chapters) == 2
        assert manifest.generated_at != ""
        assert len(manifest.chapters[0].lines) == 2
        assert manifest.chapters[0].lines[0].text == "Line 1"
        assert manifest.chapters[0].lines[0].start_seconds == 5.0
        assert manifest.chapters[0].lines[1].text == "Line 2"
        assert manifest.chapters[0].lines[1].start_seconds == 10.0
        assert len(manifest.chapters[1].lines) == 1
        assert manifest.chapters[1].lines[0].text == "Line 3"
        assert manifest.chapters[1].lines[0].start_seconds == 33.0

    def test_repeated_step_duplicates_only_in_range_lines_at_second_target(self):
        clips = (
            AudioClipInfo("/tmp/a.mp3", 10.0, 20.0, 0.0),
            AudioClipInfo("/tmp/a.mp3", 0.0, 10.0, 10.0),
            AudioClipInfo("/tmp/a.mp3", 10.0, 20.0, 20.0),
        )
        segments = [
            FakeSegment(FakeItem("Song A", "id1", "hash1"), 0.0, 30.0, clips=clips),
        ]
        lrc = "[00:01.00]line at 1\n[00:11.00]line at 11\n[00:21.00]line at 21"

        manifest = generate_chapters_manifest(segments, lambda hash_prefix: lrc, 30.0)

        chapter = manifest.chapters[0]
        assert [(line.text, line.start_seconds) for line in chapter.lines] == [
            ("line at 11", 1.0),
            ("line at 1", 11.0),
            ("line at 11", 21.0),
        ]
        # Chapter spans the first clip's target start to the last clip's end.
        assert chapter.start_seconds == 0.0
        assert chapter.end_seconds == 30.0

    def test_omitted_section_contributes_no_lines(self):
        segments = [
            FakeSegment(
                FakeItem("Song A", "id1", "hash1"),
                0.0,
                10.0,
                clips=(AudioClipInfo("/tmp/a.mp3", 0.0, 10.0, 0.0),),
            ),
        ]
        lrc = "[00:01.00]kept\n[00:15.00]omitted"

        manifest = generate_chapters_manifest(segments, lambda hash_prefix: lrc, 10.0)

        assert [line.text for line in manifest.chapters[0].lines] == ["kept"]

    def test_boundary_timestamp_belongs_to_next_clip(self):
        segments = [
            FakeSegment(
                FakeItem("Song A", "id1", "hash1"),
                0.0,
                10.0,
                clips=(
                    AudioClipInfo("/tmp/a.mp3", 0.0, 5.0, 0.0),
                    AudioClipInfo("/tmp/a.mp3", 5.0, 10.0, 5.0),
                ),
            ),
        ]
        lrc = "[00:05.00]boundary"

        manifest = generate_chapters_manifest(segments, lambda hash_prefix: lrc, 10.0)

        lines = manifest.chapters[0].lines
        assert len(lines) == 1
        assert lines[0].text == "boundary"
        assert lines[0].start_seconds == 5.0

    def test_chapter_bounds_follow_first_and_last_clip_targets(self):
        segments = [
            FakeSegment(
                FakeItem("Song A", "id1", "hash1"),
                0.0,
                25.0,
                clips=(
                    AudioClipInfo("/tmp/a.mp3", 10.0, 20.0, 0.0),
                    AudioClipInfo("/tmp/a.mp3", 0.0, 5.0, 10.0),
                ),
            ),
        ]

        manifest = generate_chapters_manifest(segments, lambda hash_prefix: None, 15.0)

        assert manifest.chapters[0].start_seconds == 0.0
        assert manifest.chapters[0].end_seconds == 15.0

    def test_async_download_lrc(self):
        segments = [
            FakeSegment(FakeItem("Song A", "id1", "hash1"), 0.0, 30.0),
        ]

        def download_lrc(hash_prefix: str) -> str | None:
            return "[00:05.00]Async line"

        manifest = generate_chapters_manifest(segments, download_lrc, 30.0)
        assert len(manifest.chapters[0].lines) == 1
        assert manifest.chapters[0].lines[0].text == "Async line"

    def test_lrc_download_returns_none(self):
        segments = [
            FakeSegment(FakeItem("Song A", "id1", "hash1"), 0.0, 30.0),
        ]

        def download_lrc(hash_prefix: str) -> str | None:
            return None

        manifest = generate_chapters_manifest(segments, download_lrc, 30.0)
        assert manifest.chapters[0].lines == ()

    def test_lrc_download_raises_exception(self):
        segments = [
            FakeSegment(FakeItem("Song A", "id1", "hash1"), 0.0, 30.0),
        ]

        def download_lrc(hash_prefix: str) -> str | None:
            raise RuntimeError("Download failed")

        manifest = generate_chapters_manifest(segments, download_lrc, 30.0)
        assert manifest.chapters[0].lines == ()

    def test_no_hash_prefix(self):
        segments = [
            FakeSegment(FakeItem("Song A", "id1", None), 0.0, 30.0),
        ]

        def download_lrc(hash_prefix: str) -> str | None:
            return "[00:05.00]Should not appear"

        manifest = generate_chapters_manifest(segments, download_lrc, 30.0)
        assert manifest.chapters[0].lines == ()

    def test_generated_at_is_iso_format(self):
        segments = [FakeSegment(FakeItem("Song A", "id1", None), 0.0, 30.0)]
        manifest = generate_chapters_manifest(segments, lambda h: None, 30.0)
        assert "T" in manifest.generated_at


class TestChaptersToFFmpegMetadata:
    def test_basic_format(self):
        chapters = [
            _make_chapter(1, "Song A", 0.0, 30.0),
            _make_chapter(2, "Song B", 30.0, 75.0),
        ]
        manifest = _make_manifest(chapters, duration=75.0)
        result = chapters_to_ffmpeg_metadata(manifest)
        assert result.startswith(";FFMETADATA1")
        assert "[CHAPTER]" in result
        assert "TIMEBASE=1/1000" in result
        assert "START=0" in result
        assert "END=30000" in result
        assert "title=Song A" in result
        assert "START=30000" in result
        assert "END=75000" in result
        assert "title=Song B" in result

    def test_fractional_seconds_truncated(self):
        chapters = [
            _make_chapter(1, "Song", 1.5, 10.999),
        ]
        manifest = _make_manifest(chapters)
        result = chapters_to_ffmpeg_metadata(manifest)
        assert "START=1500" in result
        assert "END=10999" in result

    def test_empty_chapters(self):
        manifest = _make_manifest([])
        result = chapters_to_ffmpeg_metadata(manifest)
        assert result == ";FFMETADATA1"

    def test_chinese_title(self):
        chapters = [_make_chapter(1, "讚美之泉", 0.0, 30.0)]
        manifest = _make_manifest(chapters)
        result = chapters_to_ffmpeg_metadata(manifest)
        assert "title=讚美之泉" in result

    def test_multiple_chapters_order(self):
        chapters = [
            _make_chapter(1, "First", 0.0, 10.0),
            _make_chapter(2, "Second", 10.0, 20.0),
            _make_chapter(3, "Third", 20.0, 30.0),
        ]
        manifest = _make_manifest(chapters)
        result = chapters_to_ffmpeg_metadata(manifest)
        lines = result.split("\n")
        chapter_starts = [i for i, l in enumerate(lines) if l == "[CHAPTER]"]
        assert len(chapter_starts) == 3
        assert lines[chapter_starts[0] + 4] == "title=First"
        assert lines[chapter_starts[1] + 4] == "title=Second"
        assert lines[chapter_starts[2] + 4] == "title=Third"


class TestFindChapterAtTime:
    def test_within_first_chapter(self):
        chapters = [
            _make_chapter(1, "Song A", 0.0, 30.0),
            _make_chapter(2, "Song B", 30.0, 60.0),
        ]
        manifest = _make_manifest(chapters)
        assert find_chapter_at_time(manifest, 5.0) == 0

    def test_within_second_chapter(self):
        chapters = [
            _make_chapter(1, "Song A", 0.0, 30.0),
            _make_chapter(2, "Song B", 30.0, 60.0),
        ]
        manifest = _make_manifest(chapters)
        assert find_chapter_at_time(manifest, 45.0) == 1

    def test_at_chapter_boundary(self):
        chapters = [
            _make_chapter(1, "Song A", 0.0, 30.0),
            _make_chapter(2, "Song B", 30.0, 60.0),
        ]
        manifest = _make_manifest(chapters)
        assert find_chapter_at_time(manifest, 30.0) == 1

    def test_at_exact_end_of_last_chapter(self):
        chapters = [
            _make_chapter(1, "Song A", 0.0, 30.0),
            _make_chapter(2, "Song B", 30.0, 60.0),
        ]
        manifest = _make_manifest(chapters)
        assert find_chapter_at_time(manifest, 60.0) == 1

    def test_before_first_chapter(self):
        chapters = [_make_chapter(1, "Song A", 5.0, 30.0)]
        manifest = _make_manifest(chapters)
        assert find_chapter_at_time(manifest, 2.0) == -1

    def test_after_all_chapters(self):
        chapters = [_make_chapter(1, "Song A", 0.0, 30.0)]
        manifest = _make_manifest(chapters)
        assert find_chapter_at_time(manifest, 35.0) == -1

    def test_empty_chapters(self):
        manifest = _make_manifest([])
        assert find_chapter_at_time(manifest, 5.0) == -1

    def test_single_chapter_at_start(self):
        chapters = [_make_chapter(1, "Song A", 0.0, 30.0)]
        manifest = _make_manifest(chapters)
        assert find_chapter_at_time(manifest, 0.0) == 0

    def test_gap_between_chapters(self):
        chapters = [
            _make_chapter(1, "Song A", 0.0, 20.0),
            _make_chapter(2, "Song B", 30.0, 60.0),
        ]
        manifest = _make_manifest(chapters)
        assert find_chapter_at_time(manifest, 25.0) == -1


class TestGetSongTitleAtTime:
    def test_within_chapter(self):
        chapters = [
            _make_chapter(1, "Song A", 0.0, 30.0),
            _make_chapter(2, "Song B", 30.0, 60.0),
        ]
        manifest = _make_manifest(chapters)
        assert get_song_title_at_time(manifest, 5.0) == "Song A"
        assert get_song_title_at_time(manifest, 45.0) == "Song B"

    def test_between_chapters(self):
        chapters = [
            _make_chapter(1, "Song A", 0.0, 20.0),
            _make_chapter(2, "Song B", 30.0, 60.0),
        ]
        manifest = _make_manifest(chapters)
        assert get_song_title_at_time(manifest, 25.0) is None

    def test_empty_manifest(self):
        manifest = _make_manifest([])
        assert get_song_title_at_time(manifest, 5.0) is None


class TestGetLyricAtTime:
    def test_within_lyric_line(self):
        lines = [
            _make_chapter_line("First line", 5.0),
            _make_chapter_line("Second line", 10.0),
        ]
        chapters = [_make_chapter(1, "Song A", 0.0, 30.0, lines)]
        manifest = _make_manifest(chapters)
        assert get_lyric_at_time(manifest, 7.0) == "First line"
        assert get_lyric_at_time(manifest, 10.0) == "Second line"

    def test_before_any_lyric(self):
        lines = [
            _make_chapter_line("First line", 5.0),
        ]
        chapters = [_make_chapter(1, "Song A", 0.0, 30.0, lines)]
        manifest = _make_manifest(chapters)
        assert get_lyric_at_time(manifest, 2.0) is None

    def test_after_last_lyric(self):
        lines = [
            _make_chapter_line("First line", 5.0),
            _make_chapter_line("Second line", 10.0),
        ]
        chapters = [_make_chapter(1, "Song A", 0.0, 30.0, lines)]
        manifest = _make_manifest(chapters)
        assert get_lyric_at_time(manifest, 15.0) == "Second line"

    def test_between_chapters(self):
        chapters = [
            _make_chapter(1, "Song A", 0.0, 20.0),
            _make_chapter(2, "Song B", 30.0, 60.0),
        ]
        manifest = _make_manifest(chapters)
        assert get_lyric_at_time(manifest, 25.0) is None

    def test_chapter_with_no_lines(self):
        chapters = [_make_chapter(1, "Song A", 0.0, 30.0)]
        manifest = _make_manifest(chapters)
        assert get_lyric_at_time(manifest, 5.0) is None

    def test_chinese_lyrics(self):
        lines = [
            _make_chapter_line("讚美之泉", 5.0),
            _make_chapter_line("哈利路亞", 10.0),
        ]
        chapters = [_make_chapter(1, "Song A", 0.0, 30.0, lines)]
        manifest = _make_manifest(chapters)
        assert get_lyric_at_time(manifest, 7.0) == "讚美之泉"
        assert get_lyric_at_time(manifest, 12.0) == "哈利路亞"


class TestParseChaptersManifest:
    def test_valid_json(self):
        data = {
            "chapters": [
                {
                    "position": 1,
                    "songTitle": "Song A",
                    "startSeconds": 0.0,
                    "endSeconds": 30.0,
                    "lines": [
                        {"text": "Line 1", "startSeconds": 5.0},
                        {"text": "Line 2", "startSeconds": 10.0},
                    ],
                },
                {
                    "position": 2,
                    "songTitle": "Song B",
                    "startSeconds": 30.0,
                    "endSeconds": 60.0,
                    "lines": [],
                },
            ],
            "totalDurationSeconds": 60.0,
            "generatedAt": "2024-01-01T00:00:00Z",
        }
        manifest = parse_chapters_manifest(json.dumps(data))
        assert len(manifest.chapters) == 2
        assert manifest.chapters[0].song_title == "Song A"
        assert len(manifest.chapters[0].lines) == 2
        assert manifest.chapters[0].lines[0].text == "Line 1"
        assert manifest.chapters[0].lines[0].start_seconds == 5.0
        assert manifest.chapters[1].song_title == "Song B"
        assert manifest.total_duration_seconds == 60.0
        assert manifest.generated_at == "2024-01-01T00:00:00Z"

    def test_invalid_json(self):
        with pytest.raises(json.JSONDecodeError):
            parse_chapters_manifest("not json")

    def test_chapters_not_array(self):
        data = {"chapters": "not an array", "totalDurationSeconds": 60.0, "generatedAt": "2024-01-01"}
        with pytest.raises(ValueError, match="chapters must be an array"):
            parse_chapters_manifest(json.dumps(data))

    def test_missing_chapters_key(self):
        data = {"totalDurationSeconds": 60.0, "generatedAt": "2024-01-01"}
        with pytest.raises(ValueError, match="chapters must be an array"):
            parse_chapters_manifest(json.dumps(data))

    def test_invalid_chapter_structure(self):
        data = {
            "chapters": [{"position": "not a number"}],
            "totalDurationSeconds": 60.0,
            "generatedAt": "2024-01-01",
        }
        with pytest.raises(ValueError, match="Invalid chapter structure"):
            parse_chapters_manifest(json.dumps(data))

    def test_missing_song_title(self):
        data = {
            "chapters": [{"position": 1, "startSeconds": 0.0, "endSeconds": 30.0, "lines": []}],
            "totalDurationSeconds": 60.0,
            "generatedAt": "2024-01-01",
        }
        with pytest.raises(ValueError, match="Invalid chapter structure"):
            parse_chapters_manifest(json.dumps(data))

    def test_invalid_chapter_line(self):
        data = {
            "chapters": [
                {
                    "position": 1,
                    "songTitle": "Song A",
                    "startSeconds": 0.0,
                    "endSeconds": 30.0,
                    "lines": [{"text": 123}],
                }
            ],
            "totalDurationSeconds": 60.0,
            "generatedAt": "2024-01-01",
        }
        with pytest.raises(ValueError, match="Invalid chapter line structure"):
            parse_chapters_manifest(json.dumps(data))

    def test_missing_line_start_seconds(self):
        data = {
            "chapters": [
                {
                    "position": 1,
                    "songTitle": "Song A",
                    "startSeconds": 0.0,
                    "endSeconds": 30.0,
                    "lines": [{"text": "Hello"}],
                }
            ],
            "totalDurationSeconds": 60.0,
            "generatedAt": "2024-01-01",
        }
        with pytest.raises(ValueError, match="Invalid chapter line structure"):
            parse_chapters_manifest(json.dumps(data))

    def test_missing_total_duration(self):
        data = {
            "chapters": [],
            "generatedAt": "2024-01-01",
        }
        with pytest.raises(ValueError, match="totalDurationSeconds must be a number"):
            parse_chapters_manifest(json.dumps(data))

    def test_missing_generated_at(self):
        data = {
            "chapters": [],
            "totalDurationSeconds": 60.0,
        }
        with pytest.raises(ValueError, match="generatedAt must be a string"):
            parse_chapters_manifest(json.dumps(data))

    def test_integer_position_and_seconds(self):
        data = {
            "chapters": [
                {
                    "position": 1,
                    "songTitle": "Song A",
                    "startSeconds": 0,
                    "endSeconds": 30,
                    "lines": [{"text": "Hi", "startSeconds": 5}],
                }
            ],
            "totalDurationSeconds": 30,
            "generatedAt": "2024-01-01",
        }
        manifest = parse_chapters_manifest(json.dumps(data))
        assert manifest.chapters[0].position == 1
        assert manifest.chapters[0].start_seconds == 0.0
        assert manifest.chapters[0].lines[0].start_seconds == 5.0
        assert manifest.total_duration_seconds == 30.0

    def test_empty_chapters_array(self):
        data = {
            "chapters": [],
            "totalDurationSeconds": 0.0,
            "generatedAt": "2024-01-01",
        }
        manifest = parse_chapters_manifest(json.dumps(data))
        assert manifest.chapters == ()

    def test_roundtrip_with_ffmpeg_metadata(self):
        original_chapters = [
            _make_chapter(1, "Song A", 0.0, 30.0, [_make_chapter_line("Line 1", 5.0)]),
            _make_chapter(2, "Song B", 30.0, 60.0),
        ]
        original = _make_manifest(original_chapters, duration=60.0, generated_at="2024-01-01T00:00:00Z")
        ffmpeg_str = chapters_to_ffmpeg_metadata(original)
        assert ffmpeg_str.startswith(";FFMETADATA1")
        assert "title=Song A" in ffmpeg_str
        assert "title=Song B" in ffmpeg_str


class TestChaptersManifestRoundtrip:
    def test_serialize_and_parse_roundtrip(self):
        chapters = [
            Chapter(
                position=1,
                song_title="Song A",
                start_seconds=0.0,
                end_seconds=30.0,
                lines=(ChapterLine(text="Line 1", start_seconds=5.0),),
            ),
            Chapter(
                position=2,
                song_title="Song B",
                start_seconds=30.0,
                end_seconds=60.0,
                lines=(),
            ),
        ]
        original = ChaptersManifest(
            chapters=tuple(chapters),
            total_duration_seconds=60.0,
            generated_at="2024-01-01T00:00:00Z",
        )

        json_str = json.dumps(original.to_camel_case_dict())
        parsed = parse_chapters_manifest(json_str)

        assert len(parsed.chapters) == 2
        assert parsed.chapters[0].song_title == "Song A"
        assert parsed.chapters[0].start_seconds == 0.0
        assert parsed.chapters[0].end_seconds == 30.0
        assert len(parsed.chapters[0].lines) == 1
        assert parsed.chapters[0].lines[0].text == "Line 1"
        assert parsed.chapters[0].lines[0].start_seconds == 5.0
        assert parsed.chapters[1].song_title == "Song B"
        assert parsed.total_duration_seconds == 60.0
        assert parsed.generated_at == "2024-01-01T00:00:00Z"

    def test_to_camel_case_dict_produces_camel_case_keys(self):
        chapter = Chapter(
            position=1,
            song_title="Test Song",
            start_seconds=0.0,
            end_seconds=30.0,
            lines=(ChapterLine(text="Hello", start_seconds=5.0),),
        )
        manifest = ChaptersManifest(
            chapters=(chapter,),
            total_duration_seconds=30.0,
            generated_at="2024-01-01T00:00:00Z",
        )

        d = manifest.to_camel_case_dict()

        assert "chapters" in d
        assert "totalDurationSeconds" in d
        assert "generatedAt" in d
        assert "total_duration_seconds" not in d

        chapter_dict = d["chapters"][0]
        assert "position" in chapter_dict
        assert "songTitle" in chapter_dict
        assert "startSeconds" in chapter_dict
        assert "endSeconds" in chapter_dict
        assert "lines" in chapter_dict
        assert "song_title" not in chapter_dict
        assert "start_seconds" not in chapter_dict

        line_dict = chapter_dict["lines"][0]
        assert "text" in line_dict
        assert "startSeconds" in line_dict
        assert "start_seconds" not in line_dict

    def test_to_camel_case_dict_covers_all_fields(self):
        for cls in (ChapterLine, Chapter, ChaptersManifest):
            instance = cls.__new__(cls)
            for f in dc_fields(cls):
                default = f.default if f.default is not dataclasses.MISSING else (
                    f.default_factory() if f.default_factory is not dataclasses.MISSING else None
                )
                object.__setattr__(instance, f.name, default)

            camel_dict = instance.to_camel_case_dict()
            for f in dc_fields(cls):
                expected_key = _snake_to_camel(f.name)
                assert expected_key in camel_dict, (
                    f"{cls.__name__}.to_camel_case_dict() is missing field "
                    f"'{f.name}' (expected key '{expected_key}')"
                )


class TestChapterTitles:
    def test_repeated_song_occurrences_get_occurrence_ordinals(self):
        segments = [
            FakeSegment(FakeItem("Amazing Grace", "song-1", None), 0.0, 10.0),
            FakeSegment(FakeItem("Amazing Grace", "song-1", None), 10.0, 10.0),
        ]

        assert chapter_titles(segments) == ["Amazing Grace", "Amazing Grace (2)"]

    def test_generated_suffix_collision_in_occurrence_order(self):
        # The third Item's real title already looks like a generated label.
        segments = [
            FakeSegment(FakeItem("A", "song-1", None), 0.0, 10.0),
            FakeSegment(FakeItem("A", "song-1", None), 10.0, 10.0),
            FakeSegment(FakeItem("A (2)", "song-3", None), 20.0, 10.0),
        ]

        assert chapter_titles(segments) == ["A", "A (2)", "A (2) (2)"]

    def test_generated_suffix_collision_before_occurrence(self):
        segments = [
            FakeSegment(FakeItem("A", "song-1", None), 0.0, 10.0),
            FakeSegment(FakeItem("A (2)", "song-2", None), 10.0, 10.0),
            FakeSegment(FakeItem("A", "song-1", None), 20.0, 10.0),
        ]

        assert chapter_titles(segments) == ["A", "A (2)", "A (3)"]

    def test_base_song_title_falls_back_to_song_id_then_position(self):
        assert base_song_title(FakeItem(None, "song-9", None), 0) == "song-9"
        assert base_song_title(FakeItem(None, "", None), 4) == "Song 5"


class TestTitleCardOffset:
    """The lead-in silence is declared in the manifest, never re-times chapters."""

    def test_offset_serialises_and_round_trips(self):
        segments = [
            FakeSegment(FakeItem("Song A", "id1", "hash1"), 0.0, 30.0),
        ]
        manifest = generate_chapters_manifest(
            segments, lambda hash_prefix: "[00:03.00]Line 1", 33.0, offset_seconds=3.0
        )

        assert manifest.title_card_offset_seconds == 3.0
        # Chapter and line times already describe the padded file.
        assert manifest.chapters[0].start_seconds == 0.0
        assert manifest.chapters[0].lines[0].start_seconds == 3.0

        payload = manifest.to_camel_case_dict()
        assert payload["titleCardOffsetSeconds"] == 3.0

        parsed = parse_chapters_manifest(json.dumps(payload))
        assert parsed.title_card_offset_seconds == 3.0
        assert parsed.chapters[0].start_seconds == 0.0
        assert parsed.chapters[0].lines[0].start_seconds == 3.0

    def test_default_offset_is_zero(self):
        segments = [FakeSegment(FakeItem("Song A", "id1", "hash1"), 0.0, 30.0)]
        manifest = generate_chapters_manifest(segments, lambda hash_prefix: None, 30.0)
        assert manifest.title_card_offset_seconds == 0.0

    def test_manifest_without_offset_parses_as_zero(self):
        legacy = {
            "chapters": [
                {
                    "position": 1,
                    "songTitle": "Song A",
                    "startSeconds": 0.0,
                    "endSeconds": 30.0,
                    "lines": [],
                }
            ],
            "totalDurationSeconds": 30.0,
            "generatedAt": "2024-01-01T00:00:00Z",
        }
        parsed = parse_chapters_manifest(json.dumps(legacy))
        assert parsed.title_card_offset_seconds == 0.0


FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "webapp"
    / "src"
    / "test"
    / "fixtures"
    / "tc-songset.cross_language.json"
)


def _load_tc_fixture() -> dict:
    with FIXTURE_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _tc_segments(fixture: dict) -> list[FakeSegment]:
    segments: list[FakeSegment] = []
    for song, expected in zip(fixture["songs"], fixture["expectedTimeline"]["segments"]):
        target_start = expected["startSeconds"]
        clips: list[AudioClipInfo] = []
        for step in song["arrangement"]["steps"]:
            clips.append(
                AudioClipInfo(
                    audio_path="/tmp/fixture.mp3",
                    source_start_seconds=step["startSeconds"],
                    source_end_seconds=step["endSeconds"],
                    start_time_seconds=target_start,
                )
            )
            target_start += step["endSeconds"] - step["startSeconds"]
        assert target_start == pytest.approx(expected["endSeconds"])
        segments.append(
            FakeSegment(
                item=FakeItem(
                    song_title=song["title"],
                    song_id=song["songId"],
                    recording_hash_prefix=song["recording"]["hashPrefix"],
                    tempo_bpm=song["analysis"]["tempoBpm"],
                ),
                start_time_seconds=expected["startSeconds"],
                duration_seconds=expected["durationSeconds"],
                clips=tuple(clips),
            )
        )
    return segments


class TestGlueChapterTimeline:
    def test_b_chapter_starts_at_glue_end_and_no_chapter_covers_glue(self):
        fixture = _load_tc_fixture()
        timeline = fixture["expectedTimeline"]
        segments = _tc_segments(fixture)
        lrc_by_prefix = {
            song["recording"]["hashPrefix"]: song["lrc"] for song in fixture["songs"]
        }

        manifest = generate_chapters_manifest(
            segments,
            lambda hash_prefix: lrc_by_prefix.get(hash_prefix),
            timeline["totalSeconds"],
        )

        expected = timeline["chapters"]
        assert [
            (chapter.position, chapter.song_title, chapter.start_seconds, chapter.end_seconds)
            for chapter in manifest.chapters
        ] == [
            (c["position"], c["title"], c["startSeconds"], c["endSeconds"])
            for c in expected
        ]
        assert manifest.total_duration_seconds == timeline["totalSeconds"]

        window = timeline["glueWindows"][0]
        glue_midpoint = timeline["midpointFrame"]["timeSeconds"]
        assert window["startSeconds"] == expected[0]["endSeconds"]
        assert window["endSeconds"] == expected[1]["startSeconds"]
        assert expected[0]["endSeconds"] - expected[0]["startSeconds"] == 12.0
        assert (
            expected[1]["startSeconds"] - expected[0]["endSeconds"]
            == fixture["glue"][0]["stagedSeconds"]
        )

        assert find_chapter_at_time(manifest, glue_midpoint) == -1
        assert get_song_title_at_time(manifest, glue_midpoint) is None
        assert get_lyric_at_time(manifest, glue_midpoint) is None
        assert find_chapter_at_time(manifest, window["endSeconds"]) == 1

    def test_multi_glue_chapters_and_lyrics_shift_by_each_staged_window(self):
        segments = [
            FakeSegment(
                item=FakeItem("第一首", "song-a", "aaa", 120.0),
                start_time_seconds=0.0,
                duration_seconds=10.0,
                clips=(AudioClipInfo("/tmp/a.mp3", 0.0, 10.0, 0.0),),
            ),
            FakeSegment(
                item=FakeItem("第二首", "song-b", "bbb", 120.0),
                start_time_seconds=12.0,
                duration_seconds=13.0,
                clips=(AudioClipInfo("/tmp/b.mp3", 0.0, 13.0, 12.0),),
            ),
            FakeSegment(
                item=FakeItem("第三首", "song-c", "ccc", 120.0),
                start_time_seconds=28.0,
                duration_seconds=12.0,
                clips=(AudioClipInfo("/tmp/c.mp3", 0.0, 12.0, 28.0),),
            ),
        ]
        lrc_by_prefix = {
            "aaa": "[00:00.50]一",
            "bbb": "[00:00.50]二",
            "ccc": "[00:00.50]三",
        }

        manifest = generate_chapters_manifest(
            segments, lambda hash_prefix: lrc_by_prefix.get(hash_prefix), 40.0
        )

        assert [
            (chapter.song_title, chapter.start_seconds, chapter.end_seconds)
            for chapter in manifest.chapters
        ] == [
            ("第一首", 0.0, 10.0),
            ("第二首", 12.0, 25.0),
            ("第三首", 28.0, 40.0),
        ]
        assert find_chapter_at_time(manifest, 11.0) == -1
        assert find_chapter_at_time(manifest, 26.5) == -1
        assert get_song_title_at_time(manifest, 12.5) == "第二首"
        assert get_song_title_at_time(manifest, 28.5) == "第三首"
        assert manifest.chapters[1].lines[0].start_seconds == 12.5
        assert manifest.chapters[2].lines[0].start_seconds == 28.5
        assert manifest.total_duration_seconds == 40.0

    def test_lyric_timestamps_include_the_staged_glue(self):
        fixture = _load_tc_fixture()
        timeline = fixture["expectedTimeline"]
        segments = _tc_segments(fixture)
        lrc_by_prefix = {
            song["recording"]["hashPrefix"]: song["lrc"] for song in fixture["songs"]
        }

        manifest = generate_chapters_manifest(
            segments,
            lambda hash_prefix: lrc_by_prefix.get(hash_prefix),
            timeline["totalSeconds"],
        )

        for song in fixture["songs"]:
            chapter = next(
                chapter
                for chapter in manifest.chapters
                if chapter.song_title == song["title"]
            )
            assert [
                (line.text, line.start_seconds) for line in chapter.lines
            ] == [
                (line["text"], line["globalSeconds"]) for line in song["lyrics"]
            ]

        first_b_lyric = timeline["firstLyricGlobalSeconds"]["item-b"]
        b_first_source_start = fixture["songs"][1]["arrangement"]["steps"][0]["startSeconds"]
        assert first_b_lyric == (
            timeline["segments"][1]["startSeconds"]
            + fixture["songs"][1]["lyrics"][0]["localSeconds"]
            - b_first_source_start
        )
        assert get_lyric_at_time(manifest, first_b_lyric) == "祢的信實何等廣大"

    def test_mp4_metadata_matches_mp3_and_chapters_timeline(self):
        fixture = _load_tc_fixture()
        timeline = fixture["expectedTimeline"]
        segments = _tc_segments(fixture)

        manifest = generate_chapters_manifest(
            segments, lambda hash_prefix: None, timeline["totalSeconds"]
        )
        metadata = chapters_to_ffmpeg_metadata(manifest)

        assert "START=0\nEND=12000" in metadata
        assert "START=15000\nEND=33000" in metadata
        assert manifest.total_duration_seconds == 33.0
        assert manifest.chapters[-1].end_seconds == manifest.total_duration_seconds

    def test_without_glue_the_same_fixture_would_start_b_earlier(self):
        fixture = _load_tc_fixture()
        timeline = fixture["expectedTimeline"]
        comparison = timeline["ordinaryGapComparison"]
        ordinary_start = comparison["wouldBeStartSecondsWithoutGlue"]
        staged_start = timeline["segments"][1]["startSeconds"]

        assert staged_start - ordinary_start == (
            fixture["glue"][0]["stagedSeconds"] - comparison["ordinaryGapSeconds"]
        )
        assert staged_start == (
            timeline["segments"][0]["endSeconds"]
            + fixture["glue"][0]["stagedSeconds"]
        )

        # Rebuild B at the ordinary (non-Glue) start: only the junction shifts.
        ordinary_segments = _tc_segments(fixture)
        b_expected = timeline["segments"][1]
        shift = ordinary_start - b_expected["startSeconds"]
        b_source = ordinary_segments[1]
        ordinary_b = FakeSegment(
            item=b_source.item,
            start_time_seconds=ordinary_start,
            duration_seconds=b_source.duration_seconds,
            clips=tuple(
                AudioClipInfo(
                    clip.audio_path,
                    clip.source_start_seconds,
                    clip.source_end_seconds,
                    clip.start_time_seconds + shift,
                )
                for clip in b_source.clips
            ),
        )
        manifest = generate_chapters_manifest(
            [ordinary_segments[0], ordinary_b],
            lambda hash_prefix: None,
            comparison["wouldBeStartSecondsWithoutGlue"]
            + b_source.duration_seconds,
        )
        assert manifest.chapters[1].start_seconds == ordinary_start
        assert manifest.chapters[0].end_seconds == timeline["chapters"][0]["endSeconds"]
