"""Tests for the shared visibility badge/choice helpers (pure, no DB)."""

import pytest

from stream_of_worship.admin.services.visibility import (
    NO_VISIBILITY_BADGE,
    VISIBILITY_BADGES,
    format_visibility,
    visibility_alternatives,
    visibility_badge,
    visibility_label,
)


class TestVisibilityBadge:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            ("published", "●"),
            ("review", "◐"),
            ("hold", "○"),
            (None, "·"),
            ("", "·"),
            ("unknown", "·"),
        ],
    )
    def test_badge_glyph(self, status, expected):
        assert visibility_badge(status) == expected

    def test_legend_matches_audio_list(self):
        # The `audio list` legend renders these exact glyphs.
        assert VISIBILITY_BADGES == {"published": "●", "review": "◐", "hold": "○"}
        assert NO_VISIBILITY_BADGE == "·"


class TestFormatVisibility:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            ("published", "[green]●[/green]"),
            ("review", "[yellow]◐[/yellow]"),
            ("hold", "[dim]○[/dim]"),
            (None, "[dim]·[/dim]"),
            ("", "[dim]·[/dim]"),
            ("bogus", "[dim]·[/dim]"),
        ],
    )
    def test_rich_markup(self, status, expected):
        assert format_visibility(status) == expected


class TestVisibilityLabel:
    def test_review(self):
        assert visibility_label("review") == "◐ review"

    def test_unset_renders_none(self):
        assert visibility_label(None) == "· none"
        assert visibility_label("") == "· none"

    def test_published(self):
        assert visibility_label("published") == "● published"


class TestVisibilityAlternatives:
    @pytest.mark.parametrize(
        ("current", "expected"),
        [
            ("published", ["review", "hold"]),
            ("review", ["published", "hold"]),
            ("hold", ["published", "review"]),
            (None, ["published", "review", "hold"]),
            ("", ["published", "review", "hold"]),
        ],
    )
    def test_excludes_current(self, current, expected):
        assert visibility_alternatives(current) == expected

    def test_never_offers_current(self):
        for status in ("published", "review", "hold"):
            assert status not in visibility_alternatives(status)
