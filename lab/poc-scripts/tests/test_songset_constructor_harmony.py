from poc.songset_constructor.rules.harmony import (
    cfd,
    key_compatibility_score,
    normalize_key,
    suggest_key_shift,
)


def test_normalize_key_handles_enharmonic_and_modes():
    assert normalize_key("Db minor") == ("C#", "min")
    assert normalize_key("Bbmaj") == ("Bb", "maj")


def test_cfd_uses_relative_major_for_minor_keys():
    assert cfd("A", "min", "C", "maj") == 0
    assert cfd("G", "maj", "D", "maj") == 1


def test_suggest_key_shift_prefers_zero_when_compatible():
    assert suggest_key_shift("G", "maj", "D", "maj") == (0, 1)
    shift, distance = suggest_key_shift("C", "maj", "F#", "maj")
    assert shift in {-2, -1, 1, 2}
    assert distance < cfd("C", "maj", "F#", "maj")


def test_key_compatibility_monotonic():
    assert key_compatibility_score(0) > key_compatibility_score(2) > key_compatibility_score(5)
