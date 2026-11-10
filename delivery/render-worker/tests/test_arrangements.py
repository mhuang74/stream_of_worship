from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sow_render_worker.arrangements import (
    ARRANGEMENT_UNAVAILABLE_OR_STALE_MESSAGE,
    MAX_ARRANGEMENT_STEPS,
    ArrangementStep,
    RecordingSection,
    build_snapped_steps,
    parse_recording_beats,
    parse_recording_sections,
    parse_song_arrangement,
    validate_arrangement,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "webapp"
    / "src"
    / "test"
    / "fixtures"
    / "arrangements.cross_language.json"
)


@pytest.fixture(scope="module")
def cross_language_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def arrangement_json(steps: list[ArrangementStep]) -> str:
    entries = []
    for step in steps:
        payload: dict[str, Any] = {
            "sectionIndex": step.section_index,
            "label": step.label,
            "startSeconds": step.start_seconds,
            "endSeconds": step.end_seconds,
        }
        if step.manual_bounds:
            payload["manualBounds"] = True
        entries.append(payload)
    return json.dumps({"version": 1, "steps": entries})


def fixture_snapped_steps(fixture: dict[str, Any]) -> list[ArrangementStep]:
    sections = parse_recording_sections(json.dumps(fixture["sections"]))
    beats = parse_recording_beats(json.dumps(fixture["beats"]))
    steps = build_snapped_steps(sections, beats, fixture["durationSeconds"])
    assert steps is not None
    return steps


def assert_snapped_steps_match(
    actual: list[ArrangementStep] | None, expected: list[dict[str, Any]]
) -> None:
    assert actual is not None
    assert len(actual) == len(expected)
    for step, wanted in zip(actual, expected):
        assert step.section_index == wanted["sectionIndex"]
        assert step.label == wanted["label"]
        assert abs(step.start_seconds - wanted["startSeconds"]) <= 1e-9
        assert abs(step.end_seconds - wanted["endSeconds"]) <= 1e-9


def test_shared_cross_language_fixture_snaps_identically(
    cross_language_fixture: dict[str, Any],
) -> None:
    steps = fixture_snapped_steps(cross_language_fixture)
    assert_snapped_steps_match(steps, cross_language_fixture["expectedSteps"])


def test_shared_fixture_half_up_rounding_boundary(
    cross_language_fixture: dict[str, Any],
) -> None:
    steps = fixture_snapped_steps(cross_language_fixture)
    tag = next(step for step in steps if step.label == "tag")
    assert abs(tag.start_seconds - 2.000001) <= 1e-9
    assert abs(tag.end_seconds - 22.0) <= 1e-9


def test_shared_fixture_validates_saved_arrangement(
    cross_language_fixture: dict[str, Any],
) -> None:
    expected = cross_language_fixture["expectedSteps"]
    saved = tuple(
        ArrangementStep(
            section_index=entry["sectionIndex"],
            label=entry["label"],
            start_seconds=entry["startSeconds"],
            end_seconds=entry["endSeconds"],
        )
        for entry in expected
    )
    validated = validate_arrangement(
        arrangement_json(list(saved)),
        json.dumps(cross_language_fixture["sections"]),
        json.dumps(cross_language_fixture["beats"]),
        cross_language_fixture["durationSeconds"],
    )
    assert validated is not None
    assert_snapped_steps_match(list(validated), expected)
    total = sum(step.end_seconds - step.start_seconds for step in validated)
    expected_total = sum(entry["endSeconds"] - entry["startSeconds"] for entry in expected)
    assert abs(total - expected_total) <= 1e-9


def test_arrangement_step_exposes_duration_seconds() -> None:
    step = ArrangementStep(section_index=2, label="chorus", start_seconds=30.0, end_seconds=37.5)
    assert step.duration_seconds == 7.5


# ---------------------------------------------------------------------------
# Recording analysis parsing
# ---------------------------------------------------------------------------


def test_parse_recording_sections_returns_none_for_null_or_unreadable() -> None:
    assert parse_recording_sections(None) is None
    assert parse_recording_sections("not json") is None
    assert parse_recording_sections("{}") is None
    assert parse_recording_sections(json.dumps([{"label": "a"}])) is None
    assert parse_recording_sections(json.dumps([{"label": "", "start": 0, "end": 1}])) is None
    assert parse_recording_sections(json.dumps([{"label": "a", "start": "0", "end": 1}])) is None


def test_parse_recording_sections_allows_extra_keys_and_overlap() -> None:
    value = json.dumps(
        [
            {"label": "intro", "start": 0, "end": 2, "duration": 2},
            {"label": "verse", "start": 2, "end": 4},
            {"label": "tag", "start": 1, "end": 3},
        ]
    )
    assert parse_recording_sections(value) == [
        RecordingSection(label="intro", start=0.0, end=2.0),
        RecordingSection(label="verse", start=2.0, end=4.0),
        RecordingSection(label="tag", start=1.0, end=3.0),
    ]


def test_parse_recording_beats_empty_means_unavailable() -> None:
    assert parse_recording_beats(None) == []
    assert parse_recording_beats("nope") == []
    assert parse_recording_beats("{}") == []
    assert parse_recording_beats("[]") == []


def test_parse_recording_beats_sorts_dedupes_and_drops_non_finite() -> None:
    assert parse_recording_beats("[4,1,1,3,130,-2,1e999,2.5]") == [
        -2.0,
        1.0,
        2.5,
        3.0,
        4.0,
        130.0,
    ]
    assert parse_recording_beats("[0,10]") == [0.0, 10.0]


# ---------------------------------------------------------------------------
# Snapping
# ---------------------------------------------------------------------------


def test_build_snapped_steps_nearest_beats() -> None:
    steps = build_snapped_steps([{"label": "a", "start": 1.2, "end": 3.4}], [1, 2, 3, 4], 10)
    assert steps == [ArrangementStep(section_index=0, label="a", start_seconds=1.0, end_seconds=3.0)]


def test_build_snapped_steps_ties_choose_earlier_beat() -> None:
    steps = build_snapped_steps([{"label": "a", "start": 11, "end": 13}], [10, 12], 20)
    assert steps == [ArrangementStep(section_index=0, label="a", start_seconds=10.0, end_seconds=12.0)]


def test_build_snapped_steps_handles_unsorted_duplicate_beats() -> None:
    steps = build_snapped_steps([{"label": "a", "start": 0.9, "end": 3.1}], [4, 3, 1, 2, 1, 3], 10)
    assert steps == [ArrangementStep(section_index=0, label="a", start_seconds=1.0, end_seconds=3.0)]


def test_build_snapped_steps_collapsed_range_is_unavailable() -> None:
    assert build_snapped_steps([{"label": "a", "start": 30.1, "end": 30.2}], [30, 31], 60) is None


def test_build_snapped_steps_requires_beats_and_valid_sections() -> None:
    assert build_snapped_steps([{"label": "a", "start": 1, "end": 2}], [], 10) is None
    assert build_snapped_steps(None, [1, 2], 10) is None
    assert build_snapped_steps([], [1, 2], 10) is None
    assert build_snapped_steps([{"label": "a", "start": 2, "end": 2}], [1, 2], 10) is None


def test_build_snapped_steps_skips_invalid_sections_keeping_indices() -> None:
    steps = build_snapped_steps(
        [
            {"label": "", "start": 1, "end": 2},
            {"label": "good", "start": 1, "end": 3},
            {"label": "out-of-range", "start": 5, "end": 99},
        ],
        [1, 2, 3],
        10,
    )
    assert steps == [ArrangementStep(section_index=1, label="good", start_seconds=1.0, end_seconds=3.0)]


def test_build_snapped_steps_clamps_bounds() -> None:
    steps = build_snapped_steps([{"label": "a", "start": 0.4, "end": 9.6}], [0, 10], 10)
    assert steps == [ArrangementStep(section_index=0, label="a", start_seconds=0.0, end_seconds=10.0)]


def test_build_snapped_steps_ignores_out_of_duration_beats() -> None:
    steps = build_snapped_steps([{"label": "a", "start": 0.4, "end": 9.6}], [-5, 0, 10, 130], 10)
    assert steps == [ArrangementStep(section_index=0, label="a", start_seconds=0.0, end_seconds=10.0)]


def test_build_snapped_steps_supports_touching_and_overlapping_sections() -> None:
    steps = build_snapped_steps(
        [
            {"label": "a", "start": 0, "end": 2},
            {"label": "b", "start": 2, "end": 4},
            {"label": "c", "start": 1, "end": 3},
        ],
        [0, 1, 2, 3, 4],
        10,
    )
    assert [(step.start_seconds, step.end_seconds) for step in steps] == [
        (0.0, 2.0),
        (2.0, 4.0),
        (1.0, 3.0),
    ]


# ---------------------------------------------------------------------------
# Persisted arrangement parsing: NULL vs malformed JSON
# ---------------------------------------------------------------------------


def test_parse_song_arrangement_null_is_distinct_from_malformed() -> None:
    assert parse_song_arrangement(None) is None
    for malformed in ("not json", "null", "[]", "{}"):
        with pytest.raises(ValueError):
            parse_song_arrangement(malformed)


def test_parse_song_arrangement_allows_repeated_indices() -> None:
    steps = (
        ArrangementStep(0, "intro", 0.0, 4.5),
        ArrangementStep(0, "intro", 0.0, 4.5),
    )
    assert parse_song_arrangement(arrangement_json(list(steps))) == steps


def test_parse_song_arrangement_strict_top_level_fields() -> None:
    step = {"sectionIndex": 0, "label": "a", "startSeconds": 0, "endSeconds": 1}
    for payload in (
        {"version": 2, "steps": [step]},
        {"version": "1", "steps": [step]},
        {"version": 1, "steps": [step], "extra": True},
        {"version": 1, "steps": []},
        {"version": 1, "steps": [step] * (MAX_ARRANGEMENT_STEPS + 1)},
    ):
        with pytest.raises(ValueError):
            parse_song_arrangement(json.dumps(payload))
    parsed = parse_song_arrangement(json.dumps({"version": 1, "steps": [step] * MAX_ARRANGEMENT_STEPS}))
    assert parsed is not None and len(parsed) == MAX_ARRANGEMENT_STEPS


def test_parse_song_arrangement_strict_step_fields() -> None:
    bad_steps = [
        {"sectionIndex": -1, "label": "a", "startSeconds": 0, "endSeconds": 1},
        {"sectionIndex": 1.5, "label": "a", "startSeconds": 0, "endSeconds": 1},
        {"sectionIndex": "0", "label": "a", "startSeconds": 0, "endSeconds": 1},
        {"sectionIndex": True, "label": "a", "startSeconds": 0, "endSeconds": 1},
        {"sectionIndex": 0, "label": "", "startSeconds": 0, "endSeconds": 1},
        {"sectionIndex": 0, "label": "a" * 101, "startSeconds": 0, "endSeconds": 1},
        {"sectionIndex": 0, "label": "a", "startSeconds": "0", "endSeconds": 1},
        {"sectionIndex": 0, "label": "a", "startSeconds": 0, "endSeconds": None},
        {"sectionIndex": 0, "label": "a", "startSeconds": -1, "endSeconds": 1},
        {"sectionIndex": 0, "label": "a", "startSeconds": 2, "endSeconds": 1},
        {"sectionIndex": 0, "label": "a", "startSeconds": 0, "endSeconds": 1, "extra": 1},
    ]
    for step in bad_steps:
        with pytest.raises(ValueError):
            parse_song_arrangement(json.dumps({"version": 1, "steps": [step]}))


def test_parse_song_arrangement_accepts_inferred_intro_and_manual_bounds() -> None:
    intro = {
        "sectionIndex": None,
        "label": "intro",
        "startSeconds": 0,
        "endSeconds": 12.4,
        "manualBounds": True,
    }
    assert parse_song_arrangement(
        json.dumps({"version": 1, "steps": [intro]})
    ) == (ArrangementStep(None, "intro", 0.0, 12.4, True),)

    manual = {
        "sectionIndex": 0,
        "label": "intro",
        "startSeconds": 0.5,
        "endSeconds": 4.0,
        "manualBounds": True,
    }
    assert parse_song_arrangement(
        json.dumps({"version": 1, "steps": [manual]})
    ) == (ArrangementStep(0, "intro", 0.5, 4.0, True),)


def test_parse_song_arrangement_rejects_manual_flag_spellings() -> None:
    base = {"sectionIndex": 0, "label": "intro", "startSeconds": 0, "endSeconds": 1}
    for value in (False, 1, "true", None):
        with pytest.raises(ValueError):
            parse_song_arrangement(
                json.dumps({"version": 1, "steps": [{**base, "manualBounds": value}]})
            )


def test_parse_song_arrangement_rejects_malformed_inferred_intro() -> None:
    bad_intros = [
        # the label is what names an Intro
        {"sectionIndex": None, "label": "chorus", "startSeconds": 0, "endSeconds": 12.4, "manualBounds": True},
        # an Intro always runs from zero
        {"sectionIndex": None, "label": "intro", "startSeconds": 2, "endSeconds": 12.4, "manualBounds": True},
        # the flag is required: an unflagged null index is meaningless
        {"sectionIndex": None, "label": "intro", "startSeconds": 0, "endSeconds": 12.4},
    ]
    for step in bad_intros:
        with pytest.raises(ValueError):
            parse_song_arrangement(json.dumps({"version": 1, "steps": [step]}))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

SECTIONS = [
    {"label": "intro", "start": 0.4, "end": 4.3},
    {"label": "verse", "start": 4.6, "end": 30.1},
]
BEATS = [0, 4.5, 30]
DURATION = 60.0
SNAPPED = [
    ArrangementStep(0, "intro", 0.0, 4.5),
    ArrangementStep(1, "verse", 4.5, 30.0),
]


def test_validate_arrangement_null_is_valid_even_without_analysis() -> None:
    assert validate_arrangement(None, None, None, None) is None


def test_validate_arrangement_rejects_malformed_and_structural_problems() -> None:
    for raw in ("not json", "{}", arrangement_json([SNAPPED[0]] * 65)):
        with pytest.raises(ValueError, match="repair the Recording analysis or reset"):
            validate_arrangement(raw, json.dumps(SECTIONS), json.dumps(BEATS), DURATION)


def test_parse_song_arrangement_rejects_huge_integer_section_index() -> None:
    big = json.loads("[1" + "0" * 400 + "]")[0]
    step = {"sectionIndex": big, "label": "a", "startSeconds": 0, "endSeconds": 1}
    with pytest.raises(ValueError, match="repair the Recording analysis or reset"):
        validate_arrangement(json.dumps({"version": 1, "steps": [step]}), None, None, None)


def test_validate_arrangement_rejects_missing_or_collapsed_analysis() -> None:
    raw = arrangement_json(SNAPPED)
    for sections_json, beats_json in (
        (None, json.dumps(BEATS)),
        (json.dumps(SECTIONS), None),
        (json.dumps(SECTIONS), "[]"),
        (json.dumps([{"label": "intro", "start": 30.1, "end": 30.2}]), json.dumps([30, 31])),
    ):
        with pytest.raises(ValueError, match="repair the Recording analysis or reset"):
            validate_arrangement(raw, sections_json, beats_json, DURATION)


def test_validate_arrangement_accepts_matching_and_repeated_steps() -> None:
    repeated = [SNAPPED[0], SNAPPED[0], SNAPPED[1]]
    validated = validate_arrangement(
        arrangement_json(repeated), json.dumps(SECTIONS), json.dumps(BEATS), DURATION
    )
    assert validated == tuple(repeated)


def test_validate_arrangement_rejects_stale_label_index_and_bounds() -> None:
    raw_sections = json.dumps(SECTIONS)
    raw_beats = json.dumps(BEATS)
    stale_cases = [
        [ArrangementStep(0, "wrong", 0.0, 4.5)],
        [ArrangementStep(99, "intro", 0.0, 4.5)],
        [ArrangementStep(0, "intro", 0.4, 4.3)],
        [ArrangementStep(0, "intro", 0.0, DURATION + 1)],
    ]
    for steps in stale_cases:
        with pytest.raises(ValueError, match="repair the Recording analysis or reset"):
            validate_arrangement(arrangement_json(steps), raw_sections, raw_beats, DURATION)


def test_validate_arrangement_tolerance_absorbs_float_noise_only() -> None:
    noisy = [ArrangementStep(0, "intro", 0.0 + 5e-7, 4.5)]
    assert validate_arrangement(
        arrangement_json(noisy), json.dumps(SECTIONS), json.dumps(BEATS), DURATION
    ) is not None
    too_far = [ArrangementStep(0, "intro", 0.0 + 2e-6, 4.5)]
    with pytest.raises(ValueError, match="repair the Recording analysis or reset"):
        validate_arrangement(
            arrangement_json(too_far), json.dumps(SECTIONS), json.dumps(BEATS), DURATION
        )


def test_validate_arrangement_enforces_64_step_maximum() -> None:
    steps64 = [SNAPPED[0]] * MAX_ARRANGEMENT_STEPS
    assert validate_arrangement(
        arrangement_json(steps64), json.dumps(SECTIONS), json.dumps(BEATS), DURATION
    ) == tuple(steps64)
    with pytest.raises(ValueError):
        validate_arrangement(
            arrangement_json([SNAPPED[0]] * (MAX_ARRANGEMENT_STEPS + 1)),
            json.dumps(SECTIONS),
            json.dumps(BEATS),
            DURATION,
        )


def test_validate_arrangement_accepts_manual_bounds_that_drift_from_snapped() -> None:
    raw_sections = json.dumps(SECTIONS)
    raw_beats = json.dumps(BEATS)
    manual = [ArrangementStep(0, "intro", 0.3, 4.2, True)]
    assert validate_arrangement(
        arrangement_json(manual), raw_sections, raw_beats, DURATION
    ) == tuple(manual)
    # The same bounds without the flag are a forged cut, not an edit.
    unflag = [ArrangementStep(0, "intro", 0.3, 4.2)]
    with pytest.raises(ValueError, match="repair the Recording analysis or reset"):
        validate_arrangement(arrangement_json(unflag), raw_sections, raw_beats, DURATION)


def test_validate_arrangement_rejects_manual_steps_with_stale_index_label_or_range() -> None:
    raw_sections = json.dumps(SECTIONS)
    raw_beats = json.dumps(BEATS)
    for steps in (
        [ArrangementStep(99, "intro", 0.3, 4.2, True)],
        [ArrangementStep(0, "wrong", 0.3, 4.2, True)],
        [ArrangementStep(0, "intro", 0.3, DURATION + 1, True)],
        [ArrangementStep(0, "intro", 4.2, 0.3, True)],
    ):
        with pytest.raises(ValueError, match="repair the Recording analysis or reset"):
            validate_arrangement(arrangement_json(steps), raw_sections, raw_beats, DURATION)


def test_validate_arrangement_intro_only_needs_no_analysis() -> None:
    intro = [ArrangementStep(None, "intro", 0.0, 12.4, True)]
    # LRC is never re-fetched or re-validated: an Intro-only arrangement
    # validates with no sections or beats at all.
    assert validate_arrangement(arrangement_json(intro), None, None, DURATION) == tuple(intro)
    out_of_range = [ArrangementStep(None, "intro", 0.0, DURATION + 1, True)]
    with pytest.raises(ValueError, match="repair the Recording analysis or reset"):
        validate_arrangement(arrangement_json(out_of_range), None, None, DURATION)


def test_repair_or_reset_message_is_exact() -> None:
    assert ARRANGEMENT_UNAVAILABLE_OR_STALE_MESSAGE == (
        "Song arrangement is unavailable or stale; "
        "repair the Recording analysis or reset the arrangement"
    )
