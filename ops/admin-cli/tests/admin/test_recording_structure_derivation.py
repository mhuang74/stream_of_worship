"""Tests for derive_recording_structure (Arrange-gate structure backfill).

The webapp's Arrange button is gated on
``recordings.sections`` + ``recordings.beats`` + ``duration_seconds`` passing
``buildSnappedSteps`` in ``delivery/webapp/src/lib/arrangements.ts``. That
function returns null when ANY section collapses to zero length after snapping,
so every section written by the backfill must survive the same snapping. These
tests pin that contract, plus the madmom beat-grid payload shape.
"""

from stream_of_worship.admin.commands.audio import (
    _normalize_beats_for_snap,
    derive_recording_structure,
)


def _beats(max_seconds, step=0.5):
    """Beat grid covering [0, max_seconds] so intended bounds snap exactly."""
    return [i * step for i in range(int(max_seconds / step) + 1)]


def _component(component_type, section_label, start_time, end_time):
    return {
        "component_type": component_type,
        "section_label": section_label,
        "start_time": start_time,
        "end_time": end_time,
    }


class TestNormalizeBeatsForSnap:
    def test_accepts_flat_timestamps(self):
        assert _normalize_beats_for_snap([0.5, 1.0, 1.5], 10.0) == [0.5, 1.0, 1.5]

    def test_accepts_madmom_pairs(self):
        # sow-downbeats stores madmom's raw grid.tolist(): [time, beat_position].
        pairs = [[0.76, 1.0], [1.16, 2.0], [1.52, 3.0]]
        assert _normalize_beats_for_snap(pairs, 10.0) == [0.76, 1.16, 1.52]

    def test_drops_out_of_duration_and_sorts_and_dedupes(self):
        assert _normalize_beats_for_snap([5.0, 0.5, 5.0, 99.0, -1.0], 10.0) == [0.5, 5.0]

    def test_rejects_non_positive_duration(self):
        assert _normalize_beats_for_snap([1.0, 2.0], 0.0) == []


class TestDeriveRecordingStructure:
    def test_maps_section_label_to_section_label_field(self):
        sections, beats = derive_recording_structure(
            [_component("verse", "verse 1", 10.0, 30.0)],
            _beats(40.0),
            40.0,
        )
        assert sections == [{"label": "verse 1", "start": 10.0, "end": 30.0}]
        assert beats[0] == 0.0

    def test_falls_back_to_component_type_when_label_missing(self):
        sections, _ = derive_recording_structure(
            [_component("chorus", None, 10.0, 30.0)],
            _beats(40.0),
            40.0,
        )
        assert sections == [{"label": "chorus", "start": 10.0, "end": 30.0}]

    def test_drops_section_that_collapses_on_snap(self):
        # 0.21s "start" rows exist in real data; both bounds snap to one beat,
        # which would make buildSnappedSteps return null for the WHOLE recording.
        sections, _ = derive_recording_structure(
            [
                _component("start", "start", 0.0, 0.21),
                _component("verse", "verse", 10.0, 30.0),
            ],
            _beats(40.0),
            40.0,
        )
        assert sections == [{"label": "verse", "start": 10.0, "end": 30.0}]

    def test_returns_empty_without_beats(self):
        sections, beats = derive_recording_structure(
            [_component("verse", "verse", 10.0, 30.0)], [], 40.0
        )
        assert sections == []
        assert beats == []

    def test_returns_empty_when_no_component_is_usable(self):
        sections, beats = derive_recording_structure(
            [_component("verse", "verse", 10.0, 10.1)],
            _beats(40.0),
            40.0,
        )
        assert sections == []
        assert beats == []

    def test_trims_overlap_so_bounds_never_cross(self):
        sections, _ = derive_recording_structure(
            [
                _component("a", "verse", 10.0, 30.0),
                _component("b", "chorus", 20.0, 40.0),
            ],
            _beats(40.0),
            40.0,
        )
        assert sections == [
            {"label": "verse", "start": 10.0, "end": 30.0},
            {"label": "chorus", "start": 30.0, "end": 40.0},
        ]

    def test_drops_sections_past_duration(self):
        sections, _ = derive_recording_structure(
            [
                _component("verse", "verse", 10.0, 30.0),
                _component("outro", "outro", 30.0, 999.0),
            ],
            _beats(40.0),
            40.0,
        )
        assert sections == [{"label": "verse", "start": 10.0, "end": 30.0}]

    def test_caps_sections_at_max_arrangement_steps(self):
        components = [_component("verse", f"verse {i}", float(i), float(i + 1)) for i in range(100)]
        sections, _ = derive_recording_structure(components, _beats(200.0, 0.25), 200.0)
        assert len(sections) == 64
