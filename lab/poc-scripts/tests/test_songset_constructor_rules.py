from poc.songset_constructor.models import ConstructorConfig, SongCandidate
from poc.songset_constructor.music_rules import (
    build_transition,
    cfd,
    classify_themes,
    enrich_candidate,
    infer_phase,
    key_compatibility_score,
    suggest_key_shift,
)


def candidate(
    hash_prefix: str,
    key: str = "C",
    mode: str = "major",
    bpm: float = 100,
) -> SongCandidate:
    return SongCandidate(
        song_id=f"song-{hash_prefix}",
        title="全心赞美",
        recording_hash_prefix=hash_prefix,
        bpm=bpm,
        musical_key=key,
        musical_mode=mode,
    )


def test_cfd_normalizes_relative_major_minor() -> None:
    assert cfd("C", "major", "A", "minor") == 0
    assert cfd("C", "major", "G", "major") == 1
    assert cfd("C", "major", "D", "major") == 2
    assert cfd("C", "major", "Gb", "major") == 6
    assert key_compatibility_score(0) == 1.0
    assert key_compatibility_score(6) == 0.0


def test_suggest_key_shift_prefers_small_shift_to_compatible_key() -> None:
    source = candidate("a", key="C")
    target = candidate("b", key="Gb")
    shift, distance = suggest_key_shift(source, target)
    assert shift in {-2, -1, 1, 2}
    assert distance <= 2


def test_build_transition_warns_for_large_tempo_and_low_confidence_shift() -> None:
    source = candidate("a", key="C", bpm=120)
    target = candidate("b", key="Gb", bpm=90)
    target.key_confidence = 0.4
    transition = build_transition(source, target)
    assert transition.crossfade_enabled is True
    assert "tempo_delta_gt_15" in transition.warnings
    assert "low_key_confidence_for_transposition" in transition.warnings


def test_theme_classification_and_phase_inference() -> None:
    themes = classify_themes("献上感恩", "我献上赞美和感谢")
    assert "感恩" in themes
    assert infer_phase(["差遣"], bpm=82) == "Sending"
    assert infer_phase([], album_series="DEV", bpm=70) == "Response"


def test_enrich_candidate_defaults_unclassified_to_worship() -> None:
    song = candidate("x", key="D", bpm=88)
    song.title = "普通标题"
    enriched = enrich_candidate(song)
    assert enriched.inferred_themes == ["敬拜"]
    assert enriched.phase == "Worship"
