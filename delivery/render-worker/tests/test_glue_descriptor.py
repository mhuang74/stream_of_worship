"""Cross-language canonical Glue descriptor tests.

Loads the same fixture as
``delivery/webapp/src/test/lib/glue/descriptor.test.ts`` and asserts byte
identical canonical JSON plus the sha256 key, so any drift between the
TypeScript writer and this Python mirror fails loudly.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from sow_render_worker.glue_descriptor import (
    build_descriptor,
    canonical_json,
    canonical_number_text,
    canonical_six_dp,
)

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "webapp"
    / "src"
    / "test"
    / "fixtures"
    / "glue-canonical.cross_language.json"
)

FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
DESCRIPTOR_CASES: list[dict[str, Any]] = FIXTURE["descriptors"]
DESCRIPTOR_IDS = [case["name"] for case in DESCRIPTOR_CASES]

HASH_A = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0"
HASH_B = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"

PINNED_INPUT: dict[str, Any] = {
    "beats": 4,
    "toTempoBpm": 120,
    "from": {"contentHash": HASH_A, "steps": [[0, 12.0]]},
    "to": {"contentHash": HASH_B, "steps": [[0, 120]]},
}

PINNED_CANONICAL_JSON = (
    '{"v":2,"model":"music_v2_5","promptVersion":2,"excerptSeconds":12,"beats":4,'
    f'"toTempoBpm":120,"from":{{"contentHash":"{HASH_A}","steps":[[0,12]]}},'
    f'"to":{{"contentHash":"{HASH_B}","steps":[[0,120]]}}}}'
)

PINNED_KEY = "5e0d13ac268778bf5ec5d619834562b5706027ab82da141cc3aad241b8987651"

PAYLOAD_KEYS = [
    "v",
    "model",
    "promptVersion",
    "excerptSeconds",
    "beats",
    "toTempoBpm",
    "from",
    "to",
]


def build_from_input(case_input: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    return build_descriptor(
        beats=case_input["beats"],
        to_tempo_bpm=case_input["toTempoBpm"],
        from_side=case_input["from"],
        to_side=case_input["to"],
    )


def test_fixture_shape_is_shared_with_the_webapp() -> None:
    assert DESCRIPTOR_CASES, "cross-language fixture has no descriptor cases"
    # #314 appends excerpt cases to the same file.
    assert isinstance(FIXTURE["excerpts"], list)


@pytest.mark.parametrize("case", DESCRIPTOR_CASES, ids=DESCRIPTOR_IDS)
def test_descriptor_matches_shared_fixture(case: dict[str, Any]) -> None:
    payload, canonical, key = build_from_input(case["input"])

    assert canonical == case["canonicalJson"]
    assert key == case["key"]
    assert key == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert len(key) == 64
    assert key == key.lower()
    assert json.loads(canonical) == payload
    assert list(payload) == PAYLOAD_KEYS
    assert list(payload["from"]) == ["contentHash", "steps"]
    assert list(payload["to"]) == ["contentHash", "steps"]


def test_descriptor_pins_the_exact_canonical_string_and_key() -> None:
    payload, canonical, key = build_from_input(PINNED_INPUT)

    assert canonical == PINNED_CANONICAL_JSON
    assert key == PINNED_KEY
    assert payload["from"]["steps"] == [[0, 12]]
    assert payload["to"]["steps"] == [[0, 120]]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0"),
        (-0.0, "0"),
        (12, "12"),
        (12.0, "12"),
        (120, "120"),
        (0.1, "0.1"),
        (4.125, "4.125"),
        (127.35, "127.35"),
        (123456789.5, "123456789.5"),
        (1e-7, "0"),
        (1.0000005, "1.000001"),
        (0.0000005, "0.000001"),
        (2.3456785, "2.345679"),
        (1.0000004, "1"),
        (-1.5, "-1.5"),
        (-0.0000004, "0"),
    ],
)
def test_canonical_number_text_matches_the_typescript_table(
    value: float, expected: str
) -> None:
    assert canonical_number_text(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (4.125, 4.125),
        (127.35, 127.35),
        (12, 12),
        (1.0000005, 1.000001),
        (0.0000005, 0.000001),
        (2.3456785, 2.345679),
        (1.0000004, 1),
        (0.9999995, 1),
    ],
)
def test_canonical_six_dp_rounds_half_up(value: float, expected: float) -> None:
    assert canonical_six_dp(value) == expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_are_rejected(value: float) -> None:
    with pytest.raises(ValueError):
        canonical_six_dp(value)
    with pytest.raises(ValueError):
        canonical_number_text(value)


def test_micro_units_beyond_exact_range_are_rejected() -> None:
    with pytest.raises(ValueError):
        canonical_number_text(1e12)
    with pytest.raises(ValueError):
        canonical_six_dp(1e12)


def test_canonical_json_keeps_non_ascii_text_raw() -> None:
    assert canonical_json("漸進") == '"漸進"'
    assert canonical_json({"name": "漸進"}) == '{"name":"漸進"}'


def test_canonical_json_keeps_insertion_order_and_compact_separators() -> None:
    value = {"b": [1, 2], "a": None, "c": True, "d": "x"}
    assert canonical_json(value) == '{"b":[1,2],"a":null,"c":true,"d":"x"}'


def test_canonical_json_rejects_unsupported_values() -> None:
    with pytest.raises(TypeError):
        canonical_json(object())
    with pytest.raises(TypeError):
        canonical_json({1: "x"})
    with pytest.raises(TypeError):
        canonical_number_text("12")  # type: ignore[arg-type]


def test_steps_null_stays_null_and_differs_from_a_full_range() -> None:
    null_steps = {
        **PINNED_INPUT,
        "to": {"contentHash": HASH_B, "steps": None},
    }
    payload, canonical, key = build_from_input(null_steps)

    assert payload["to"]["steps"] is None
    assert canonical != PINNED_CANONICAL_JSON
    assert key != PINNED_KEY


def test_step_reorder_changes_the_key() -> None:
    forward = {
        **PINNED_INPUT,
        "from": {"contentHash": HASH_A, "steps": [[0, 12], [12, 3]]},
        "to": {"contentHash": HASH_A, "steps": [[0, 20], [20, 4]]},
    }
    reversed_steps = {
        **PINNED_INPUT,
        "from": {"contentHash": HASH_A, "steps": [[12, 3], [0, 12]]},
        "to": {"contentHash": HASH_A, "steps": [[20, 4], [0, 20]]},
    }
    forward_canonical = build_from_input(forward)[1]
    reversed_canonical = build_from_input(reversed_steps)[1]

    assert reversed_canonical != forward_canonical
    assert build_from_input(reversed_steps)[2] != build_from_input(forward)[2]


@pytest.mark.parametrize(
    "patch",
    [
        {"beats": 8},
        {"toTempoBpm": 121},
        {"from": {"contentHash": HASH_B, "steps": [[0, 12]]}},
        {"from": {"contentHash": HASH_A, "steps": [[0, 6]]}},
        {"to": {"contentHash": HASH_A, "steps": [[0, 120]]}},
        {"to": {"contentHash": HASH_B, "steps": None}},
    ],
    ids=[
        "beats",
        "toTempoBpm",
        "from.contentHash",
        "from.steps",
        "to.contentHash",
        "to.steps",
    ],
)
def test_each_field_changes_the_key(patch: dict[str, Any]) -> None:
    changed = {**PINNED_INPUT, **patch}
    assert build_from_input(changed)[2] != PINNED_KEY


def test_canonicalizes_numbers_before_hashing() -> None:
    rounded_down = {
        **PINNED_INPUT,
        "toTempoBpm": 120.0000004,
        "from": {"contentHash": HASH_A, "steps": [[0, 12.0000004]]},
    }
    payload, canonical, key = build_from_input(rounded_down)

    assert canonical == PINNED_CANONICAL_JSON
    assert key == PINNED_KEY
    assert payload["toTempoBpm"] == 120

    rounded_up = {**PINNED_INPUT, "toTempoBpm": 120.0000005}
    assert '"toTempoBpm":120.000001' in build_from_input(rounded_up)[1]


def test_titles_lyrics_labels_ids_and_locale_are_absent() -> None:
    decorated = {
        **PINNED_INPUT,
        "title": "Amazing Grace",
        "lyrics": "how sweet the sound",
        "label": "Verse",
        "itemId": "item-a",
        "songsetId": "songset-1",
        "locale": "zh-Hant",
        "from": {
            **PINNED_INPUT["from"],
            "title": "Amazing Grace",
            "locale": "zh-Hant",
        },
        "to": {**PINNED_INPUT["to"], "label": "Chorus", "lyrics": "how sweet"},
    }
    payload, canonical, key = build_from_input(decorated)

    assert canonical == PINNED_CANONICAL_JSON
    assert key == PINNED_KEY
    assert list(payload) == PAYLOAD_KEYS
    lowered = canonical.lower()
    for forbidden in ("title", "lyric", "label", "itemid", "songsetid", "locale"):
        assert forbidden not in lowered


def test_rejects_unsupported_beats_and_empty_content_hash() -> None:
    with pytest.raises(ValueError):
        build_descriptor(
            beats=3,
            to_tempo_bpm=120,
            from_side=PINNED_INPUT["from"],
            to_side=PINNED_INPUT["to"],
        )
    with pytest.raises(ValueError):
        build_descriptor(
            beats=4,
            to_tempo_bpm=120,
            from_side={"contentHash": "", "steps": None},
            to_side=PINNED_INPUT["to"],
        )
