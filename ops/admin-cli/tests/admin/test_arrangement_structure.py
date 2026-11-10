"""Tests for the admin CLI mirror of the webapp Arrange gate.

``delivery/webapp/src/lib/arrangements.ts`` (``buildSnappedSteps``) returns
``null`` for the WHOLE Recording when any section collapses to zero length
after snapping, so "``sections`` + ``beats`` are present" does not imply the
Arrange button opens. ``services/arrangement_structure.py`` is the third
implementation of that rule (the render worker is the second); the shared
numeric fixture pins all three to the same bounds at 1e-9.

No database is touched here: the evaluator is a pure function of a
``Recording`` row, which is what makes ``audio list --structure unusable`` and
``audio structure-audit`` testable without testcontainers.
"""

import json
from pathlib import Path

import pytest

from stream_of_worship.admin.commands.audio import _unusable_rows
from stream_of_worship.admin.db.models import Recording
from stream_of_worship.admin.services.arrangement_structure import (
    RecordingSection,
    build_snapped_steps,
    evaluate_recording_structure,
    normalize_beats,
    parse_beats,
    parse_sections,
    round_seconds,
    snap_to_beat,
)

FIXTURE = (
    Path(__file__).resolve().parents[4]
    / "delivery/webapp/src/test/fixtures/arrangements.cross_language.json"
)

# Grid that covers [0, 120] so intended bounds snap exactly.
BEATS_OK = json.dumps([float(t) for t in range(0, 121, 2)])
SECTIONS_OK = json.dumps(
    [
        {"label": "verse", "start": 10.0, "end": 30.0},
        {"label": "chorus", "start": 30.0, "end": 60.0},
        {"label": "verse", "start": 60.0, "end": 90.0},
    ]
)
# 0.00-0.17 s leading micro-section: both bounds snap to the first beat at
# 6.23 s, i.e. the degenerate artefact measured on 28a0ed6e1408 / e5c16c2f35f2.
SECTIONS_COLLAPSING = json.dumps(
    [
        {"label": "intro", "start": 0.0, "end": 0.17},
        {"label": "verse", "start": 10.0, "end": 40.0},
    ]
)
BEATS_COLLAPSING = json.dumps([6.23] + [float(t) for t in range(10, 45, 2)])


def _recording(
    *,
    sections=None,
    beats=None,
    duration: "float | None" = 120.0,
    hash_prefix: str = "a" * 12,
    visibility_status=None,
):
    """Build a minimal Recording row (only the gate-relevant fields matter)."""
    return Recording(
        content_hash=hash_prefix.rjust(64, "0"),
        hash_prefix=hash_prefix,
        original_filename="fixture.mp3",
        file_size_bytes=1024,
        imported_at="2024-01-01T00:00:00",
        duration_seconds=duration,
        sections=sections,
        beats=beats,
        visibility_status=visibility_status,
    )


def _row(recording):
    """Shape of one ``list_recordings_with_songs`` row: (rec, title, album, series)."""
    return (recording, "Title", "Album", None)


class TestCrossLanguageFixture:
    def test_snapped_steps_match_webapp_and_render_worker(self):
        """The shared fixture is the parity contract; 1e-9 is its bound."""
        if not FIXTURE.exists():
            pytest.skip("cross-language fixture missing")
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        sections = [RecordingSection(**entry) for entry in fixture["sections"]]
        steps = build_snapped_steps(sections, fixture["beats"], fixture["durationSeconds"])

        assert steps is not None
        got = [(step.section_index, step.label, step.start, step.end) for step in steps]
        expected = [
            (entry["sectionIndex"], entry["label"], entry["startSeconds"], entry["endSeconds"])
            for entry in fixture["expectedSteps"]
        ]
        assert [item[:2] for item in got] == [item[:2] for item in expected]
        # Flattened because pytest.approx rejects nested sequences; 1e-9 is the
        # bound the webapp and render-worker suites pin.
        assert [value for item in got for value in item[2:]] == pytest.approx(
            [value for item in expected for value in item[2:]], abs=1e-9
        )


class TestSnapPrimitives:
    def test_leading_micro_section_disables_the_whole_recording(self):
        """Regression for the two published recordings the dev guide documents."""
        sections = [
            RecordingSection(label="intro", start=0.0, end=0.17),
            RecordingSection(label="verse", start=10.0, end=40.0),
        ]
        beats = [6.23] + [float(t) for t in range(10, 45, 2)]
        assert build_snapped_steps(sections, beats, 180.0) is None

    def test_snap_ties_resolve_to_the_earlier_beat(self):
        assert snap_to_beat(2.5, [2.0, 3.0]) == 2.0
        assert snap_to_beat(1.4, [1.0, 2.0]) == 1.0

    def test_round_seconds_is_half_up_not_half_even(self):
        assert round_seconds(2.0000005) == 2.000001
        assert round_seconds(22.0000004) == 22.0

    def test_out_of_range_section_is_skipped_not_fatal(self):
        sections = [
            RecordingSection(label="verse", start=10.0, end=20.0),
            RecordingSection(label="outro", start=95.0, end=120.5),
        ]
        beats = [float(t) for t in range(0, 121, 2)]
        steps = build_snapped_steps(sections, beats, 120.0)
        assert steps is not None
        assert [(step.section_index, step.label) for step in steps] == [(0, "verse")]

    def test_normalize_beats_accepts_madmom_pairs(self):
        """Write-path tolerance (raw ``beat_grid.json``), kept out of parsing."""
        assert normalize_beats([[0.76, 1.0], [1.16, 2.0]], 10.0) == [0.76, 1.16]
        assert parse_beats("[[0.76, 1.0], [1.16, 2.0]]") == []


class TestParsing:
    @pytest.mark.parametrize("raw", [None, "", "   ", "not json", "{}", "1"])
    def test_parse_sections_treats_absent_and_unreadable_as_none(self, raw):
        assert parse_sections(raw) is None

    def test_parse_sections_keeps_empty_array_distinct_from_null(self):
        assert parse_sections("[]") == []

    @pytest.mark.parametrize(
        "raw",
        [
            '[{"label": "intro", "start": true, "end": 4.0}]',
            '[{"label": "", "start": 0.0, "end": 4.0}]',
            '[{"start": 0.0, "end": 4.0}]',
            '[{"label": "intro", "start": 0.0}]',
            '["intro"]',
            "[[0.0, 4.0]]",
        ],
    )
    def test_parse_sections_rejects_malformed_entries(self, raw):
        """One bad entry invalidates the whole column, as in the webapp."""
        assert parse_sections(raw) is None

    def test_parse_sections_preserves_stored_order_and_extra_keys(self):
        raw = json.dumps(
            [
                {"label": "verse", "start": 10.0, "end": 30.0, "raw_label": "Verse"},
                {"label": "chorus", "start": 30.0, "end": 60.0},
            ]
        )
        assert parse_sections(raw) == [
            RecordingSection(label="verse", start=10.0, end=30.0),
            RecordingSection(label="chorus", start=30.0, end=60.0),
        ]

    @pytest.mark.parametrize("raw", [None, "", "nope", '{"a": 1}'])
    def test_parse_beats_absent_or_unreadable_is_empty(self, raw):
        assert parse_beats(raw) == []

    def test_parse_beats_drops_pairs_and_booleans_and_dedupes(self):
        assert parse_beats('[[0.76, 1.0], 5.0, 1.0, 5.0, true]') == [1.0, 5.0]


class TestEvaluateRecordingStructure:
    def test_no_duration(self):
        for duration in (None, 0.0, -1.0):
            evaluation = evaluate_recording_structure(
                _recording(sections=SECTIONS_OK, beats=BEATS_OK, duration=duration)
            )
            assert evaluation.verdict == "no_duration"
            assert evaluation.detail == "duration_seconds missing or <= 0"
            assert evaluation.steps == []
            assert evaluation.section is None

    @pytest.mark.parametrize("raw", [None, "", "   ", "[]"])
    def test_missing_sections(self, raw):
        evaluation = evaluate_recording_structure(
            _recording(sections=raw, beats=BEATS_OK)
        )
        assert evaluation.verdict == "missing_sections"
        assert evaluation.detail == "recordings.sections absent or empty"

    def test_invalid_sections(self):
        evaluation = evaluate_recording_structure(
            _recording(sections='[{"label": "intro", "start": true, "end": 4.0}]', beats=BEATS_OK)
        )
        assert evaluation.verdict == "invalid_sections"
        assert evaluation.detail == "recordings.sections is not a JSON array of {label,start,end}"

    @pytest.mark.parametrize("raw", [None, "", "[]", "[[1.0, 1.0]]"])
    def test_missing_beats(self, raw):
        evaluation = evaluate_recording_structure(
            _recording(sections=SECTIONS_OK, beats=raw)
        )
        assert evaluation.verdict == "missing_beats"
        assert evaluation.detail == "recordings.beats absent or has no finite beat times"

    def test_no_beats_in_range(self):
        evaluation = evaluate_recording_structure(
            _recording(sections=SECTIONS_OK, beats=json.dumps([200.0, 300.0]), duration=120.0)
        )
        assert evaluation.verdict == "no_beats_in_range"
        assert evaluation.detail == "no beat in 0..120.00s"

    def test_collapsed_reports_the_offending_section(self):
        evaluation = evaluate_recording_structure(
            _recording(sections=SECTIONS_COLLAPSING, beats=BEATS_COLLAPSING, duration=180.0)
        )
        assert evaluation.verdict == "collapsed"
        assert evaluation.section == RecordingSection(label="intro", start=0.0, end=0.17)
        assert evaluation.detail == "section 0 'intro' 0..0.17s collapses to 6.23s"
        # Sections before the collapse still snapped; the gate ignores them.
        assert evaluation.steps == []

    @pytest.mark.parametrize(
        "sections",
        [
            [{"label": "late", "start": 200.0, "end": 300.0}],
            [{"label": "zero", "start": 50.0, "end": 50.0}],
        ],
    )
    def test_no_usable_sections(self, sections):
        evaluation = evaluate_recording_structure(
            _recording(sections=json.dumps(sections), beats=BEATS_OK, duration=120.0)
        )
        assert evaluation.verdict == "no_usable_sections"
        assert evaluation.detail == "no section within 0..120.00s with start < end"

    def test_arrangeable_carries_the_snapped_steps(self):
        recording = _recording(sections=SECTIONS_OK, beats=BEATS_OK)
        evaluation = evaluate_recording_structure(recording)
        assert evaluation.verdict == "arrangeable"
        assert evaluation.detail == ""
        assert len(evaluation.steps) == 3
        assert evaluation.section is None
        # Same input, same answer as the function the webapp gate mirrors.
        assert evaluation.steps == build_snapped_steps(
            parse_sections(SECTIONS_OK), parse_beats(BEATS_OK), 120.0
        )

    @pytest.mark.parametrize(
        "recording",
        [
            _recording(sections=SECTIONS_OK, beats=BEATS_OK, duration=None),
            _recording(sections=None, beats=BEATS_OK),
            _recording(sections="junk", beats=BEATS_OK),
            _recording(sections=SECTIONS_OK, beats=None),
            _recording(sections=SECTIONS_OK, beats=json.dumps([500.0])),
            _recording(sections=SECTIONS_COLLAPSING, beats=BEATS_COLLAPSING, duration=180.0),
            _recording(
                sections=json.dumps([{"label": "late", "start": 200.0, "end": 300.0}]),
                beats=BEATS_OK,
            ),
            _recording(sections=SECTIONS_OK, beats=BEATS_OK),
        ],
    )
    def test_verdict_agrees_with_build_snapped_steps(self, recording):
        """Non-arrangeable exactly when the webapp's builder returns null."""
        evaluation = evaluate_recording_structure(recording)
        steps = build_snapped_steps(
            parse_sections(recording.sections),
            parse_beats(recording.beats),
            recording.duration_seconds or 0.0,
        )
        assert (evaluation.verdict == "arrangeable") is (steps is not None)


class TestUnusableRows:
    def test_keeps_only_failing_rows_in_order(self):
        arrangeable = _recording(sections=SECTIONS_OK, beats=BEATS_OK, hash_prefix="a" * 12)
        missing_beats = _recording(sections=SECTIONS_OK, beats=None, hash_prefix="b" * 12)
        collapsed = _recording(
            sections=SECTIONS_COLLAPSING,
            beats=BEATS_COLLAPSING,
            duration=180.0,
            hash_prefix="c" * 12,
        )
        rows = [_row(arrangeable), _row(missing_beats), _row(collapsed)]

        kept = _unusable_rows(rows, None)
        assert [row[0].hash_prefix for row in kept] == ["b" * 12, "c" * 12]
        # limit slices AFTER evaluation: a SQL LIMIT would have kept the
        # arrangeable first row and reported nothing.
        assert [row[0].hash_prefix for row in _unusable_rows(rows, 1)] == ["b" * 12]

    def test_all_arrangeable_yields_empty(self):
        rows = [_row(_recording(sections=SECTIONS_OK, beats=BEATS_OK))]
        assert _unusable_rows(rows, None) == []
