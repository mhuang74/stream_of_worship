import json
from dataclasses import dataclass

import pytest

from sow_render_worker.chapters import (
    Chapter,
    ChapterLine,
    ChaptersManifest,
    build_chapters_from_segments,
    chapters_to_ffmpeg_metadata,
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


@dataclass
class FakeSegment:
    item: FakeItem
    start_time_seconds: float
    duration_seconds: float


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

        def get_lyrics(hash_prefix: str, start_seconds: float) -> list[ChapterLine]:
            return [ChapterLine(text="Line 1", start_seconds=start_seconds + 5.0)]

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

        def get_lyrics(hash_prefix: str, start_seconds: float) -> list[ChapterLine]:
            return [ChapterLine(text="Should not appear", start_seconds=0.0)]

        chapters = build_chapters_from_segments(segments, get_lyrics)
        assert len(chapters) == 1
        assert chapters[0].lines == ()

    def test_async_get_lyrics(self):
        segments = [
            FakeSegment(FakeItem("Song A", "id1", "hash1"), 0.0, 30.0),
        ]

        def get_lyrics(hash_prefix: str, start_seconds: float) -> list[ChapterLine]:
            return [ChapterLine(text="Async line", start_seconds=start_seconds + 3.0)]

        chapters = build_chapters_from_segments(segments, get_lyrics)
        assert len(chapters[0].lines) == 1
        assert chapters[0].lines[0].text == "Async line"

    def test_fallback_song_id_when_no_title(self):
        segments = [
            FakeSegment(FakeItem(None, "my-song-id", "hash1"), 0.0, 30.0),
        ]

        def get_lyrics(hash_prefix: str, start_seconds: float) -> list[ChapterLine]:
            return []

        chapters = build_chapters_from_segments(segments, get_lyrics)
        assert chapters[0].song_title == "my-song-id"

    def test_fallback_position_when_no_title_no_id(self):
        segments = [
            FakeSegment(FakeItem(None, "", "hash1"), 0.0, 30.0),
        ]

        def get_lyrics(hash_prefix: str, start_seconds: float) -> list[ChapterLine]:
            return []

        chapters = build_chapters_from_segments(segments, get_lyrics)
        assert chapters[0].song_title == "Song 1"

    def test_empty_segments(self):
        chapters = build_chapters_from_segments([], lambda h, s: [])
        assert chapters == []


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
