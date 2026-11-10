"""Tests for the Arrange-gate structure backfill.

The webapp's Arrange button is gated on
``recordings.sections`` + ``recordings.beats`` + ``duration_seconds`` passing
``buildSnappedSteps`` in ``delivery/webapp/src/lib/arrangements.ts``. That
function returns null when ANY section collapses to zero length after snapping,
so every section written by the backfill must survive the same snapping. These
tests pin that contract, plus the two payload shapes it straddles: madmom's
`[time, beat_position]` beat grid, and LRC files that pack several lyric lines
into one timestamped line.
"""

from stream_of_worship.admin.commands.audio import _finalize_arrangement_structure
from stream_of_worship.admin.services.arrangement_structure import normalize_beats
from stream_of_worship.admin.services.lyrics_sections import (
    DerivedSection,
    derive_sections_from_lyrics,
)


def _beats(max_seconds, step=0.5):
    """Beat grid covering [0, max_seconds] so intended bounds snap exactly."""
    return [i * step for i in range(int(max_seconds / step) + 1)]


def _structured(*sections):
    """Build a structured-lyrics payload from (label, [lines...]) pairs."""
    return {
        "sections": [
            {"label": label, "raw_label": label.title(), "lines": list(lines)}
            for label, lines in sections
        ]
    }


class TestNormalizeBeats:
    def test_accepts_flat_timestamps(self):
        assert normalize_beats([0.5, 1.0, 1.5], 10.0) == [0.5, 1.0, 1.5]

    def test_accepts_madmom_pairs(self):
        # sow-downbeats stores madmom's raw grid.tolist(): [time, beat_position].
        pairs = [[0.76, 1.0], [1.16, 2.0], [1.52, 3.0]]
        assert normalize_beats(pairs, 10.0) == [0.76, 1.16, 1.52]

    def test_drops_out_of_duration_and_sorts_and_dedupes(self):
        assert normalize_beats([5.0, -1.0, 2.0, 2.0, 99.0], 10.0) == [2.0, 5.0]

    def test_rejects_non_positive_duration(self):
        assert normalize_beats([1.0], 0) == []


class TestDeriveSectionsFromLyrics:
    def test_matches_sections_packed_into_shared_lrc_lines(self):
        # Regression: the pool's LRC files routinely put two lyric lines on one
        # timestamp. A per-line window matcher yields zero sections for these.
        structured = _structured(
            ("verse", ["line one", "line two"]),
            ("chorus", ["chorus a", "chorus b"]),
        )
        lrc = "[00:10.00] line one line two\n[00:20.00] chorus a chorus b\n[00:30.00] outro\n"
        sections = derive_sections_from_lyrics(structured, lrc, 40.0)
        assert [(s.label, s.start, s.end) for s in sections] == [
            ("verse", 10.0, 20.0),
            ("chorus", 20.0, 30.0),
        ]

    def test_keeps_repeated_section_occurrences(self):
        # Every occurrence is placeable in the editor, so a repeated chorus must
        # not collapse to a single entry. The final occurrence has no following
        # line to bound it, so it falls back to the estimated line duration.
        structured = _structured(("chorus", ["same words"]))
        lrc = "[00:10.00] same words\n[00:20.00] other\n[00:30.00] same words\n"
        sections = derive_sections_from_lyrics(structured, lrc, 40.0)
        assert [(s.start, s.end) for s in sections] == [(10.0, 20.0), (30.0, 34.0)]

    def test_labels_interleaved_repeats_from_one_section_block(self):
        # Regression: the pool's [verse] blocks list lines whose second half is
        # sung after a chorus, so the block never appears contiguously in the
        # LRC even though every line is present. Labelling each timed line on
        # its own evidence keeps both the verse occurrences and the chorus.
        structured = _structured(
            ("verse", ["v1 words", "v2 words", "v3 words", "v4 words"]),
            ("chorus", ["c1 words", "c2 words"]),
        )
        lrc = (
            "[00:10.00] v1 words v2 words\n"
            "[00:20.00] c1 words c2 words\n"
            "[00:30.00] v3 words v4 words\n"
        )
        sections = derive_sections_from_lyrics(structured, lrc, 40.0)
        assert [(s.label, s.start, s.end) for s in sections] == [
            ("verse", 10.0, 20.0),
            ("chorus", 20.0, 30.0),
            ("verse", 30.0, 34.0),
        ]

    def test_ignores_punctuation_and_full_width_spaces(self):
        structured = _structured(("verse", ["主我敬拜祢，"]))
        lrc = "[00:10.00] 主我敬拜祢\n[00:20.00] next\n"
        assert len(derive_sections_from_lyrics(structured, lrc, 30.0)) == 1

    def test_clamps_final_section_to_duration(self):
        structured = _structured(("outro", ["last words"]))
        lrc = "[00:10.00] last words\n[00:16.00] fade\n"
        sections = derive_sections_from_lyrics(structured, lrc, 12.0)
        assert [(s.start, s.end) for s in sections] == [(10.0, 12.0)]

    def test_returns_empty_when_lrc_stops_before_the_lyrics(self):
        # 6% of the pool has an LRC that ends long before the song does; nothing
        # is derivable and the write-back must report that instead of guessing.
        structured = _structured(("verse", ["words never present in the lrc"]))
        lrc = "[00:10.00] different words\n[00:20.00] more words\n"
        assert derive_sections_from_lyrics(structured, lrc, 300.0) == []

    def test_returns_empty_without_lrc_or_sections(self):
        assert derive_sections_from_lyrics({"sections": []}, "[00:01.00] x\n", 10.0) == []
        assert derive_sections_from_lyrics(_structured(("verse", ["x"])), None, 10.0) == []

    def test_matches_a_lyric_line_split_across_timestamps(self):
        # The LRC splits one structured lyric line across two timestamps, so
        # neither fragment clears the whole-line ratio on its own.
        structured = _structured(("verse", ["主我敬拜祢 我全心讚美祢"]))
        lrc = "[00:10.00] 主我敬拜祢\n[00:15.00] 我全心讚美祢\n[00:20.00] fade out\n"
        sections = derive_sections_from_lyrics(structured, lrc, 40.0)
        assert [(s.label, s.start, s.end) for s in sections] == [("verse", 10.0, 20.0)]

    def test_attributes_a_shared_lyric_line_by_its_neighbours(self):
        # Both sections contain "saved by grace"; order in structured_lyrics must
        # not decide which one owns each occurrence.
        structured = _structured(
            ("verse", ["saved by grace", "verse only line"]),
            ("chorus", ["saved by grace", "chorus only line"]),
        )
        lrc = (
            "[00:10.00] saved by grace\n"
            "[00:15.00] verse only line\n"
            "[00:20.00] saved by grace\n"
            "[00:25.00] chorus only line\n"
            "[00:30.00] fade out\n"
        )
        sections = derive_sections_from_lyrics(structured, lrc, 40.0)
        assert [(s.label, s.start, s.end) for s in sections] == [
            ("verse", 10.0, 20.0),
            ("chorus", 20.0, 30.0),
        ]

    def test_keeps_a_repeat_line_inside_one_occurrence(self):
        # The pool's chorus blocks list each sung line twice, so one rendition is
        # A A B B across four timestamps. All four belong to one occurrence: the
        # cursor steps one duplicate at a time instead of jumping past it and
        # forcing spurious boundaries.
        structured = _structured(("chorus", ["chorus a", "chorus a", "chorus b", "chorus b"]))
        lrc = (
            "[00:10.00] chorus a\n"
            "[00:15.00] chorus a\n"
            "[00:20.00] chorus b\n"
            "[00:25.00] chorus b\n"
            "[00:30.00] fade out\n"
        )
        sections = derive_sections_from_lyrics(structured, lrc, 40.0)
        assert [(s.label, s.start, s.end) for s in sections] == [("chorus", 10.0, 30.0)]

    def test_keeps_a_phrase_repeated_inside_one_occurrence(self):
        # LRC files repeat a phrase inside a rendition ("...宣告" sung twice) while
        # structured_lyrics lists it once. That is a repeat within the occurrence,
        # not a restart, so the chorus must not fragment around it.
        structured = _structured(("chorus", ["chorus a", "chorus b", "chorus c"]))
        lrc = (
            "[00:10.00] chorus a\n"
            "[00:15.00] chorus b\n"
            "[00:20.00] chorus c\n"
            "[00:25.00] chorus c\n"
            "[00:30.00] fade out\n"
        )
        sections = derive_sections_from_lyrics(structured, lrc, 40.0)
        assert [(s.label, s.start, s.end) for s in sections] == [("chorus", 10.0, 30.0)]

    def test_keeps_boundaries_between_consecutive_repeats(self):
        # A chorus repeated back-to-back has no differently-labelled line between
        # the repetitions; both occurrences must stay separately selectable.
        structured = _structured(("chorus", ["first chorus line", "second chorus line"]))
        lrc = (
            "[00:10.00] first chorus line\n"
            "[00:15.00] second chorus line\n"
            "[00:20.00] first chorus line\n"
            "[00:25.00] second chorus line\n"
            "[00:30.00] fade out\n"
        )
        sections = derive_sections_from_lyrics(structured, lrc, 40.0)
        assert [(s.label, s.start, s.end) for s in sections] == [
            ("chorus", 10.0, 20.0),
            ("chorus", 20.0, 30.0),
        ]


class TestFinalizeArrangementStructure:
    def test_snaps_to_nearest_beat_and_returns_flat_beats(self):
        sections = [DerivedSection(label="verse", start=1.2, end=2.7)]
        written, beats = _finalize_arrangement_structure(sections, _beats(10.0), 10.0)
        assert written == [{"label": "verse", "start": 1.0, "end": 2.5}]
        assert all(isinstance(beat, float) for beat in beats)

    def test_drops_sections_that_collapse_after_snapping(self):
        # buildSnappedSteps nulls the WHOLE recording if any section is
        # zero-length, so a collapsing section must be dropped, not written.
        sections = [
            DerivedSection(label="verse", start=1.02, end=1.03),
            DerivedSection(label="chorus", start=2.0, end=5.0),
        ]
        written, _ = _finalize_arrangement_structure(sections, _beats(10.0), 10.0)
        assert [section["label"] for section in written] == ["chorus"]

    def test_truncates_overlapping_bounds(self):
        sections = [
            DerivedSection(label="verse", start=1.0, end=6.0),
            DerivedSection(label="chorus", start=4.0, end=8.0),
        ]
        written, _ = _finalize_arrangement_structure(sections, _beats(10.0), 10.0)
        assert [(s["start"], s["end"]) for s in written] == [(1.0, 6.0), (6.0, 8.0)]

    def test_drops_sections_spanning_past_duration(self):
        sections = [DerivedSection(label="verse", start=1.0, end=99.0)]
        assert _finalize_arrangement_structure(sections, _beats(10.0), 10.0) == ([], [])

    def test_caps_sections_at_the_editor_step_limit(self):
        sections = [
            DerivedSection(label="verse", start=float(i), end=float(i) + 0.5) for i in range(80)
        ]
        written, _ = _finalize_arrangement_structure(sections, _beats(100.0), 100.0)
        assert len(written) == 64

    def test_returns_empty_without_beats(self):
        sections = [DerivedSection(label="verse", start=1.0, end=2.0)]
        assert _finalize_arrangement_structure(sections, [], 10.0) == ([], [])
