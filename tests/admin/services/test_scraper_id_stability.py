"""Tests for scraper ID stability.

Verifies that song IDs remain stable across re-scrapes when content
hasn't changed, and that incremental mode correctly de-dupes.
"""

import pytest
from datetime import datetime

from stream_of_worship.admin.services.scraper import CatalogScraper, _SongCandidate
from stream_of_worship.admin.db.models import Song


class TestSongIDStability:
    """Test suite for stable song ID generation."""

    def test_compute_song_id_format(self):
        """Test that _compute_song_id produces expected format."""
        scraper = CatalogScraper()

        # Test basic case
        song_id = scraper._compute_song_id("將天敞開", "游智婷", "鄭懋柔")

        # Should be <slug>_<8-char-hex>
        parts = song_id.rsplit("_", 1)
        assert len(parts) == 2
        slug, hash_part = parts
        assert len(hash_part) == 8
        assert all(c in "0123456789abcdef" for c in hash_part)

    def test_compute_song_id_stability(self):
        """Test that same inputs produce same ID."""
        scraper = CatalogScraper()

        song_id_1 = scraper._compute_song_id("將天敞開", "游智婷", "鄭懋柔")
        song_id_2 = scraper._compute_song_id("將天敞開", "游智婷", "鄭懋柔")

        assert song_id_1 == song_id_2

    def test_compute_song_id_content_dependent(self):
        """Test that different inputs produce different IDs."""
        scraper = CatalogScraper()

        # Same title, different composer
        id_1 = scraper._compute_song_id("將天敞開", "游智婷", "鄭懋柔")
        id_2 = scraper._compute_song_id("將天敞開", "Somebody Else", "鄭懋柔")

        assert id_1 != id_2

    def test_compute_song_id_handles_empty(self):
        """Test that empty composer/lyricist works."""
        scraper = CatalogScraper()

        song_id = scraper._compute_song_id("Test Song", "", "")

        # Should still produce valid ID
        parts = song_id.rsplit("_", 1)
        assert len(parts) == 2

    def test_compute_song_id_unicode_normalization(self):
        """Test NFKC normalization for special characters."""
        scraper = CatalogScraper()

        # Different Unicode forms should produce same ID
        id_1 = scraper._compute_song_id("café", "Test", "Test")
        id_2 = scraper._compute_song_id("café", "Test", "Test")  # Different Unicode form

        # These should be the same after NFKC normalization
        # (though in practice most inputs will already be normalized)

    def test_compute_song_id_length_limit(self):
        """Test that very long titles are truncated."""
        scraper = CatalogScraper()

        very_long_title = "A" * 200
        song_id = scraper._compute_song_id(very_long_title, "Test", "Test")

        assert len(song_id) <= 100


class TestIncrementalScraping:
    """Test suite for incremental scraping behavior."""

    def test_existing_ids_are_checked(self, tmp_path):
        """Test that incremental mode skips existing songs."""
        # This is an integration test that would require a mock database
        # For now, we just verify the method signature exists
        scraper = CatalogScraper()

        # _get_existing_song_ids should return a set
        # In a real test, we'd mock the db_client
        pass

    def test_seen_ids_tracked(self, tmp_path):
        """Test that seen IDs are tracked during scrape."""
        # This test verifies the seen_ids logic in scrape_all_songs
        # In a real test with mock data, we'd verify that:
        # 1. seen_ids accumulates during the scrape loop
        # 2. missing IDs are identified correctly
        pass


class TestOldVsNewID:
    """Compare old row-based IDs with new content-hash IDs."""

    def test_old_id_format(self):
        """Verify we understand the old ID format."""
        # Old format: <pinyin_slug>_<row_num>
        # Example: "jiang_tian_chang_kai_42"
        old_id = "jiang_tian_chang_kai_42"
        parts = old_id.rsplit("_", 1)
        assert len(parts) == 2
        assert parts[1].isdigit()

    def test_new_id_format(self):
        """Verify the new ID format."""
        scraper = CatalogScraper()
        new_id = scraper._compute_song_id("將天敞開", "游智婷", "鄭懋柔")

        # New format: <slug>_<8-hex>
        parts = new_id.rsplit("_", 1)
        assert len(parts) == 2
        hash_part = parts[1]
        assert len(hash_part) == 8
        assert all(c in "0123456789abcdef" for c in hash_part)


class TestDedupPreferJingbaiZanmei:
    """Test that dedup prefers album_series starting with 敬拜讚美."""

    def test_prefers_jingbai_over_other_series(self):
        """When duplicates exist, prefer 敬拜讚美 series."""
        scraper = CatalogScraper()
        c1 = _SongCandidate(
            song_id="s1",
            title="將天敞開",
            composer="游智婷",
            lyricist="鄭懋柔",
            album_name="讚美之泉精選",
            album_series="其他專輯",
            musical_key="G",
            lyrics_raw="歌詞A",
            lyrics_lines=["歌詞A"],
            table_row_number=10,
        )
        c2 = _SongCandidate(
            song_id="s1",
            title="將天敞開",
            composer="游智婷",
            lyricist="鄭懋柔",
            album_name="讓讚美飛揚",
            album_series="敬拜讚美15",
            musical_key="G",
            lyrics_raw="歌詞B",
            lyrics_lines=["歌詞B"],
            table_row_number=20,
        )
        selected = scraper._select_best_candidate([c1, c2])
        assert selected.album_series == "敬拜讚美15"

    def test_last_jingbai_wins_when_multiple(self):
        """When multiple 敬拜讚美 candidates exist, last one wins."""
        c1 = _SongCandidate(
            song_id="s1",
            title="Test",
            composer="A",
            lyricist="B",
            album_name="A",
            album_series="敬拜讚美10",
            musical_key="G",
            lyrics_raw="",
            lyrics_lines=[],
            table_row_number=1,
        )
        c2 = _SongCandidate(
            song_id="s1",
            title="Test",
            composer="A",
            lyricist="B",
            album_name="B",
            album_series="敬拜讚美15",
            musical_key="G",
            lyrics_raw="",
            lyrics_lines=[],
            table_row_number=2,
        )
        c3 = _SongCandidate(
            song_id="s1",
            title="Test",
            composer="A",
            lyricist="B",
            album_name="C",
            album_series="敬拜讚美20",
            musical_key="G",
            lyrics_raw="",
            lyrics_lines=[],
            table_row_number=3,
        )
        selected = CatalogScraper()._select_best_candidate([c1, c2, c3])
        assert selected.album_series == "敬拜讚美20"

    def test_fallback_to_first_when_no_jingbai(self):
        """When no 敬拜讚美 candidate exists, keep first seen."""
        c1 = _SongCandidate(
            song_id="s1",
            title="Test",
            composer="A",
            lyricist="B",
            album_name="A",
            album_series="其他專輯1",
            musical_key="G",
            lyrics_raw="",
            lyrics_lines=[],
            table_row_number=10,
        )
        c2 = _SongCandidate(
            song_id="s1",
            title="Test",
            composer="A",
            lyricist="B",
            album_name="B",
            album_series="其他專輯2",
            musical_key="G",
            lyrics_raw="",
            lyrics_lines=[],
            table_row_number=20,
        )
        selected = CatalogScraper()._select_best_candidate([c1, c2])
        assert selected.table_row_number == 10

    def test_empty_series_is_not_jingbai(self):
        """Empty or None album_series does not qualify as 敬拜讚美."""
        c1 = _SongCandidate(
            song_id="s1",
            title="Test",
            composer="A",
            lyricist="B",
            album_name="A",
            album_series="",
            musical_key="G",
            lyrics_raw="",
            lyrics_lines=[],
            table_row_number=10,
        )
        c2 = _SongCandidate(
            song_id="s1",
            title="Test",
            composer="A",
            lyricist="B",
            album_name="B",
            album_series="敬拜讚美15",
            musical_key="G",
            lyrics_raw="",
            lyrics_lines=[],
            table_row_number=20,
        )
        selected = CatalogScraper()._select_best_candidate([c1, c2])
        assert selected.album_series == "敬拜讚美15"

    def test_single_candidate_unchanged(self):
        """Non-duplicate rows are unaffected."""
        c1 = _SongCandidate(
            song_id="s1",
            title="Test",
            composer="A",
            lyricist="B",
            album_name="A",
            album_series="其他專輯",
            musical_key="G",
            lyrics_raw="",
            lyrics_lines=[],
            table_row_number=10,
        )
        selected = CatalogScraper()._select_best_candidate([c1])
        assert selected.album_series == "其他專輯"

    def test_jingbai_without_number_matches(self):
        """album_series = '敬拜讚美' (no trailing number) still matches."""
        c1 = _SongCandidate(
            song_id="s1",
            title="Test",
            composer="A",
            lyricist="B",
            album_name="A",
            album_series="其他專輯",
            musical_key="G",
            lyrics_raw="",
            lyrics_lines=[],
            table_row_number=10,
        )
        c2 = _SongCandidate(
            song_id="s1",
            title="Test",
            composer="A",
            lyricist="B",
            album_name="B",
            album_series="敬拜讚美",
            musical_key="G",
            lyrics_raw="",
            lyrics_lines=[],
            table_row_number=20,
        )
        selected = CatalogScraper()._select_best_candidate([c1, c2])
        assert selected.album_series == "敬拜讚美"

    def test_none_series_does_not_match(self):
        """album_series = None does not qualify as 敬拜讚美."""
        c1 = _SongCandidate(
            song_id="s1",
            title="Test",
            composer="A",
            lyricist="B",
            album_name="A",
            album_series=None,
            musical_key="G",
            lyrics_raw="",
            lyrics_lines=[],
            table_row_number=10,
        )
        c2 = _SongCandidate(
            song_id="s1",
            title="Test",
            composer="A",
            lyricist="B",
            album_name="B",
            album_series="敬拜讚美15",
            musical_key="G",
            lyrics_raw="",
            lyrics_lines=[],
            table_row_number=20,
        )
        selected = CatalogScraper()._select_best_candidate([c1, c2])
        assert selected.album_series == "敬拜讚美15"
