"""Unit tests for _resolve_lyrics_text helper."""

import json

from stream_of_worship.admin.commands.audio import _resolve_lyrics_text
from stream_of_worship.admin.db.models import Recording, Song


def _make_song(lyrics_raw="flat lyrics line 1\nflat lyrics line 2") -> Song:
    return Song(
        id="song_0001",
        title="Test Song",
        source_url="https://sop.org/song/123",
        scraped_at="2024-01-15T10:30:00",
        lyrics_raw=lyrics_raw,
    )


def _make_recording(structured_lyrics=None) -> Recording:
    return Recording(
        content_hash="abc123",
        hash_prefix="abc123",
        original_filename="test.mp3",
        file_size_bytes=1000,
        imported_at="2024-01-15T10:30:00",
        structured_lyrics=structured_lyrics,
    )


class TestResolveLyricsText:
    """Tests for _resolve_lyrics_text."""

    def test_prefers_structured_lyrics(self):
        """Recording with structured_lyrics JSON returns flattened tagged text."""
        structured = {
            "sections": [
                {"label": "verse", "raw_label": "Verse", "lines": ["Line 1", "Line 2"]},
                {"label": "chorus", "raw_label": "Chorus", "lines": ["Line 3"]},
            ],
            "preamble_lines": [],
        }
        recording = _make_recording(structured_lyrics=json.dumps(structured))
        song = _make_song()

        result = _resolve_lyrics_text(song, recording)

        assert result is not None
        assert "[Verse]" in result
        assert "Line 1" in result
        assert "[Chorus]" in result
        assert "Line 3" in result
        assert "flat lyrics" not in result

    def test_falls_back_to_lyrics_raw(self):
        """Recording with structured_lyrics=None returns song.lyrics_raw."""
        recording = _make_recording(structured_lyrics=None)
        song = _make_song()

        result = _resolve_lyrics_text(song, recording)

        assert result == "flat lyrics line 1\nflat lyrics line 2"

    def test_malformed_json_falls_back(self):
        """Recording with malformed JSON structured_lyrics falls back to lyrics_raw."""
        recording = _make_recording(structured_lyrics="{invalid json}")
        song = _make_song()

        result = _resolve_lyrics_text(song, recording)

        assert result == "flat lyrics line 1\nflat lyrics line 2"

    def test_empty_sections_falls_back(self):
        """Recording with structured_lyrics but empty sections falls back."""
        structured = {"sections": [], "preamble_lines": []}
        recording = _make_recording(structured_lyrics=json.dumps(structured))
        song = _make_song()

        result = _resolve_lyrics_text(song, recording)

        assert result == "flat lyrics line 1\nflat lyrics line 2"

    def test_both_empty_returns_none(self):
        """Both structured_lyrics and lyrics_raw empty → returns None."""
        recording = _make_recording(structured_lyrics=None)
        song = _make_song(lyrics_raw=None)

        result = _resolve_lyrics_text(song, recording)

        assert result is None

    def test_structured_lyrics_none_sections_key_falls_back(self):
        """structured_lyrics JSON without 'sections' key falls back."""
        structured = {"preamble_lines": ["some preamble"]}
        recording = _make_recording(structured_lyrics=json.dumps(structured))
        song = _make_song()

        result = _resolve_lyrics_text(song, recording)

        assert result == "flat lyrics line 1\nflat lyrics line 2"
