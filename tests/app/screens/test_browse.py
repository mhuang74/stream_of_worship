"""Tests for BrowseScreen.

Tests UI interactions and search functionality.
"""

import pytest

from stream_of_worship.app.screens.browse import BrowseScreen


class TestParseSearchQuery:
    """Tests for _parse_search_query method."""

    @pytest.fixture
    def browse_screen(self):
        """Create a BrowseScreen instance with mocked dependencies."""
        from unittest.mock import MagicMock

        state = MagicMock()
        catalog = MagicMock()
        songset_client = MagicMock()

        screen = BrowseScreen(state, catalog, songset_client)
        return screen

    def test_default_search_title_only(self, browse_screen):
        """Default search (no field specifier) searches title only."""
        query, field = browse_screen._parse_search_query("讚美")

        assert query == "讚美"
        assert field == "title"

    def test_field_at_beginning_with_space(self, browse_screen):
        """Field specifier at beginning: 'field:all 讚美'."""
        query, field = browse_screen._parse_search_query("field:all 讚美")

        assert query == "讚美"
        assert field == "all"

    def test_field_at_end_with_space(self, browse_screen):
        """Field specifier at end: '讚美 field:all'."""
        query, field = browse_screen._parse_search_query("讚美 field:all")

        assert query == "讚美"
        assert field == "all"

    def test_field_at_beginning_with_colon(self, browse_screen):
        """Field specifier with colon separator: 'field:all:讚美'."""
        query, field = browse_screen._parse_search_query("field:all:讚美")

        assert query == "讚美"
        assert field == "all"

    def test_field_at_beginning_lyrics(self, browse_screen):
        """Field specifier for lyrics: 'field:lyrics 讚美'."""
        query, field = browse_screen._parse_search_query("field:lyrics 讚美")

        assert query == "讚美"
        assert field == "lyrics"

    def test_field_at_end_lyrics(self, browse_screen):
        """Field specifier for lyrics at end: '讚美 field:lyrics'."""
        query, field = browse_screen._parse_search_query("讚美 field:lyrics")

        assert query == "讚美"
        assert field == "lyrics"

    def test_field_at_beginning_composer(self, browse_screen):
        """Field specifier for composer: 'field:composer 周杰伦'."""
        query, field = browse_screen._parse_search_query("field:composer 周杰伦")

        assert query == "周杰伦"
        assert field == "composer"

    def test_field_at_end_composer(self, browse_screen):
        """Field specifier for composer at end: '周杰伦 field:composer'."""
        query, field = browse_screen._parse_search_query("周杰伦 field:composer")

        assert query == "周杰伦"
        assert field == "composer"

    def test_field_at_beginning_title(self, browse_screen):
        """Explicit title field: 'field:title 讚美'."""
        query, field = browse_screen._parse_search_query("field:title 讚美")

        assert query == "讚美"
        assert field == "title"

    def test_field_at_end_title(self, browse_screen):
        """Explicit title field at end: '讚美 field:title'."""
        query, field = browse_screen._parse_search_query("讚美 field:title")

        assert query == "讚美"
        assert field == "title"

    def test_empty_query(self, browse_screen):
        """Empty query returns empty string with title field."""
        query, field = browse_screen._parse_search_query("")

        assert query == ""
        assert field == "title"

    def test_whitespace_only_query(self, browse_screen):
        """Whitespace-only query is stripped."""
        query, field = browse_screen._parse_search_query("   ")

        assert query == ""
        assert field == "title"

    def test_query_with_multiple_spaces(self, browse_screen):
        """Query with multiple spaces is handled correctly."""
        query, field = browse_screen._parse_search_query("讚美  神   field:all")

        assert query == "讚美  神"
        assert field == "all"

    def test_field_with_trailing_colon_at_end(self, browse_screen):
        """Field specifier with trailing colon at end: '讚美 field:all:'."""
        query, field = browse_screen._parse_search_query("讚美 field:all:")

        assert query == "讚美"
        assert field == "all"

    def test_field_only_no_query(self, browse_screen):
        """Field specifier alone with no query: 'field:all'."""
        query, field = browse_screen._parse_search_query("field:all")

        assert query == ""
        assert field == "all"

    def test_partial_field_prefix_in_query(self, browse_screen):
        """Query containing 'field:' as part of search term, not specifier."""
        query, field = browse_screen._parse_search_query(" battlefield ")

        assert query == "battlefield"
        assert field == "title"

    def test_mixed_chinese_english_query(self, browse_screen):
        """Mixed Chinese-English query with field specifier."""
        query, field = browse_screen._parse_search_query("field:all Amazing 讚美")

        assert query == "Amazing 讚美"
        assert field == "all"

    def test_mixed_chinese_english_query_at_end(self, browse_screen):
        """Mixed query with field specifier at end."""
        query, field = browse_screen._parse_search_query("Amazing 讚美 field:all")

        assert query == "Amazing 讚美"
        assert field == "all"
