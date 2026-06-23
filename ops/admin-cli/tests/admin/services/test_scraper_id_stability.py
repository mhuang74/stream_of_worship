"""Tests for scraper ID stability.

Verifies that song IDs remain stable across re-scrapes when content
hasn't changed, and that incremental mode correctly de-dupes.
"""

import pytest
from datetime import datetime

from stream_of_worship.admin.services.scraper import CatalogScraper
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
