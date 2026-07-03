from stream_of_worship.music.key import parse_musical_key, pitch_class


CASES = [
    ("C#", "ok", "C#", 1, "C#", 1, "major"),
    ("Db", "ok", "Db", 1, "Db", 1, "major"),
    ("F# minor", "ok", "F#", 6, "F#", 6, "minor"),
    ("F#m", "ok", "F#", 6, "F#", 6, "minor"),
    ("E大調", "ok", "E", 4, "E", 4, "major"),
    ("E小調", "ok", "E", 4, "E", 4, "minor"),
    ("Em", "ok", "E", 4, "E", 4, "minor"),
    ("Ｄ-F", "range", "D", 2, "F", 5, "major"),
    ("D-Eb-F", "range", "D", 2, "F", 5, "major"),
    ("Em-G", "range", "E", 4, "G", 7, "minor"),
    ("", "missing", None, None, None, None, "unknown"),
    (None, "missing", None, None, None, None, "unknown"),
    ("unknown", "unparseable", None, None, None, None, "unknown"),
]


def test_parse_musical_key_fixtures():
    for raw, status, start_root, start_pc, end_root, end_pc, mode in CASES:
        parsed = parse_musical_key(raw)
        assert parsed.status == status
        assert parsed.start_root == start_root
        assert parsed.start_pitch_class == start_pc
        assert parsed.end_root == end_root
        assert parsed.end_pitch_class == end_pc
        assert parsed.mode == mode
        assert parsed.root == start_root
        assert parsed.pitch_class == start_pc


def test_enharmonic_pitch_class_equivalence():
    assert pitch_class("C#") == pitch_class("Db") == 1
    assert pitch_class("Bb") == pitch_class("A#") == 10
    assert pitch_class("F# minor") == pitch_class("Gb") == 6
