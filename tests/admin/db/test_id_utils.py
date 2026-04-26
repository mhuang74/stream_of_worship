"""Tests for id_utils module."""

import pytest

from stream_of_worship.admin.db.id_utils import compute_new_song_id, _normalize


class TestNormalizeFunction:
    """Tests for _normalize helper function."""

    def test_normalize_empty_string(self):
        assert _normalize("") == ""

    def test_normalize_none(self):
        assert _normalize(None) == ""

    def test_normalize_whitespace(self):
        assert _normalize("  test  ") == "test"

    def test_normalize_nfkc(self):
        assert _normalize("Ｔｅｓｔ") == "test"


class TestComputeNewSongId:
    """Tests for compute_new_song_id function."""

    def test_compute_new_song_id_english_title(self):
        """Test with English title."""
        song_id = compute_new_song_id("Amazing Grace", "John Newton", "")

        assert song_id.startswith("amazing_grace_")
        assert len(song_id.split("_")[1]) == 8  # 8-hex-hash

        assert compute_new_song_id("Amazing Grace", "John Newton", "") == compute_new_song_id(
            "Amazing Grace", "John Newton", ""
        )

    def test_compute_new_song_id_chinese_title(self):
        """Test with Chinese title converted to pinyin."""
        song_id = compute_new_song_id("奇妙恩典", "牛顿", "作者")

        assert song_id.startswith("qi_miao_en_dian_") or song_id.startswith("qi_miao_en_dian_")
        assert len(song_id.split("_")[1]) == 8

    def test_compute_new_song_id_none_composer_lyricist(self):
        """Test with None composer/lyricist."""
        song_id = compute_new_song_id("Test Song", None, None)

        assert "test_song_" in song_id
        assert len(song_id) <= 100

    def test_compute_new_song_id_idempotent(self):
        """Test that same inputs produce same output."""
        id1 = compute_new_song_id("Test Song", "Composer", "Lyricist")
        id2 = compute_new_song_id("Test Song", "Composer", "Lyricist")

        assert id1 == id2

    def test_compute_new_song_id_different_title(self):
        """Test that different titles produce different IDs."""
        id1 = compute_new_song_id("Song One", "Composer", "Lyricist")
        id2 = compute_new_song_id("Song Two", "Composer", "Lyricist")

        assert id1 != id2

    def test_compute_new_song_id_truncation(self):
        """Test truncation for very long titles."""
        long_title = "a" * 200
        song_id = compute_new_song_id(long_title, "Composer", "Lyricist")

        assert len(song_id) <= 100

    def test_compute_new_song_id_hash_deterministic(self):
        """Test that hash is deterministic."""
        hash1 = compute_new_song_id("Test", "A", "B").split("_")[1]
        hash2 = compute_new_song_id("Test", "A", "B").split("_")[1]

        assert hash1 == hash2 == "8" * 8

    def test_compute_new_song_id_hash_different_for_different_content(self):
        """Test that hash differs for different content."""
        id1 = compute_new_song_id("Song", "C1", "L1")
        id2 = compute_new_song_id("Song", "C2", "L2")
        id3 = compute_new_song_id("Song", "C1", "L2")

        assert id1 != id2
        assert id1 != id3

    def test_compute_new_song_id_special_characters_removed(self):
        """Test special characters removed from slug."""
        song_id = compute_new_song_id("Test!@#Song!", "C", "L")

        assert song_id.startswith("testsong_")
        assert "!" not in song_id
        assert "@" not in song_id
        assert "#" not in song_id
