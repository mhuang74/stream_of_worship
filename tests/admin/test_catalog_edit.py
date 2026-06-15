"""Tests for curated catalog edit helpers."""

from stream_of_worship.admin.services.catalog_edit import (
    build_song_from_review,
    compute_song_id,
    normalize_reviewed_data,
)
from stream_of_worship.admin.services.scraper import CatalogScraper


def test_compute_song_id_matches_scraper_helper():
    scraper = CatalogScraper()
    expected = scraper._compute_song_id("將天敞開", "游智婷", "鄭懋柔")
    assert compute_song_id("將天敞開", "游智婷", "鄭懋柔") == expected


def test_build_song_from_review_populates_lyrics_fields():
    reviewed = normalize_reviewed_data(
        {
            "title": "Here I Bow",
            "composer": "Brian & Jenn Johnson",
            "lyricist": "",
            "album_name": "After All These Years",
            "album_series": "",
            "musical_key": "",
            "source_url": "https://youtube.com/watch?v=test123",
            "lyrics_raw": "Line 1\n\n Line 2  \nLine 1",
        }
    )

    song = build_song_from_review(reviewed)

    assert song.title == "Here I Bow"
    assert song.lyrics_raw == "Line 1\nLine 2\nLine 1"
    assert song.lyrics_list == ["Line 1", "Line 2", "Line 1"]
    assert song.sections is not None
    assert song.id == compute_song_id("Here I Bow", "Brian & Jenn Johnson", None)


def test_build_song_from_review_stores_empty_lyrics_consistently():
    reviewed = normalize_reviewed_data(
        {
            "title": "Manual Song",
            "composer": "",
            "lyricist": "",
            "album_name": "",
            "album_series": "",
            "musical_key": "",
            "source_url": "https://example.com/song",
            "lyrics_raw": "",
        }
    )

    song = build_song_from_review(reviewed, existing_song_id="manual_song_12345678")

    assert song.id == "manual_song_12345678"
    assert song.lyrics_raw is None
    assert song.lyrics_lines is None
    assert song.sections is None
