"""Tests for _cached_components_have_llm_fields helper."""

from stream_of_worship.admin.services.analysis import _cached_components_have_llm_fields


def _comp(role="none", theme=None, vocal_posture=None):
    d = {"role": role}
    if theme is not None:
        d["theme"] = theme
    if vocal_posture is not None:
        d["vocal_posture"] = vocal_posture
    return d


class TestCachedComponentsHaveLlmFields:
    def test_cached_components_have_llm_fields_all_populated(self):
        components = [
            _comp(role="entry", theme="感恩", vocal_posture="站立"),
            _comp(role="exit", theme="敬拜", vocal_posture="站立"),
        ]
        assert _cached_components_have_llm_fields(
            components, classify_theme=True, classify_vocal_posture=True
        )

    def test_cached_components_have_llm_fields_missing_theme(self):
        components = [
            _comp(role="entry", theme="感恩", vocal_posture="站立"),
            _comp(role="exit", theme=None, vocal_posture="站立"),
        ]
        assert not _cached_components_have_llm_fields(
            components, classify_theme=True, classify_vocal_posture=True
        )

    def test_cached_components_have_llm_fields_skips_non_essential(self):
        components = [
            _comp(role="entry", theme="感恩", vocal_posture="站立"),
            _comp(role="chorus", theme=None, vocal_posture=None),
        ]
        assert _cached_components_have_llm_fields(
            components, classify_theme=True, classify_vocal_posture=True
        )

    def test_cached_components_have_llm_fields_all_components_mode(self):
        components = [
            _comp(role="entry", theme="感恩", vocal_posture="站立"),
            _comp(role="chorus", theme=None, vocal_posture=None),
        ]
        assert not _cached_components_have_llm_fields(
            components,
            classify_theme=True,
            classify_vocal_posture=True,
            all_components=True,
        )
