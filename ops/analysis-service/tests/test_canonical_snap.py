"""Unit tests for canonical_snap._normalize (script normalization)."""

from sow_analysis.services.canonical_snap import _normalize


class TestNormalizeNiPreservation:
    """The worship honorific 祢 must render as 祢, never 禰."""

    def test_preserves_ni(self):
        assert _normalize("祢就是唯一") == "祢就是唯一"

    def test_normalizes_legacy_mei_to_ni(self):
        assert _normalize("祢就是唯一 禰是主") == "祢就是唯一祢是主"

    def test_idempotent_on_traditional(self):
        assert _normalize("詞曲：祢就是唯一 點亮") == "詞曲祢就是唯一點亮"
