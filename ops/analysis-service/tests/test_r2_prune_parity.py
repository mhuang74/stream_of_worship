"""Parity tests for the two vendored ``select_backups_to_delete`` copies.

The analysis-service image installs admin-cli only as a dev dependency, so
``sow_analysis.storage.r2`` cannot import the admin copy at runtime and vendors
it instead (same pattern as ``workers/lrc_parser.py``). These tests prove the
two copies stay byte-identical and agree on every retention case.
"""

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from sow_analysis.storage import r2 as analysis_r2
from stream_of_worship.admin.services import r2 as admin_r2

PREFIX = "abc123"
T0 = datetime(2026, 10, 22, 23, 9, 5, tzinfo=timezone.utc)


def _key(name: str) -> str:
    return f"{PREFIX}/lyrics.backup.{name}.lrc"


def _at(minutes: float, *, naive: bool = False) -> datetime:
    value = T0 + timedelta(minutes=minutes)
    return value.replace(tzinfo=None) if naive else value


def _keys(count: int, *, start: int = 0) -> list[tuple[str, datetime]]:
    return [(_key(str(1000 + start + i)), _at(start + i)) for i in range(count)]


# case name -> (objects, protected_key, max_backups, expected deletions)
CASES = {
    "mixed_clock_over_cap": (
        [
            (_key("1792710546483"), _at(0)),
            (_key("1792711476891"), _at(15.5)),
            (_key("357735493"), _at(18.65)),
            (_key("358288869"), _at(27.9)),
            (_key("358431311"), _at(30.25)),
            (_key("1792712640000"), _at(34.9)),
        ],
        _key("1792712640000"),
        5,
        [_key("1792710546483")],
    ),
    "tie_breaks_on_key": (
        [(_key("2000"), _at(0)), (_key("1000"), _at(0))],
        None,
        1,
        [_key("1000")],
    ),
    "naive_last_modified": (
        [(_key("2000"), _at(1, naive=True)), (_key("1000"), _at(0))],
        None,
        1,
        [_key("1000")],
    ),
    "unparsable_recent": (
        _keys(5) + [(_key("manual"), _at(30))],
        None,
        5,
        [_key("1000")],
    ),
    "unparsable_oldest": (
        [(_key("manual"), _at(0))] + _keys(5, start=1),
        None,
        5,
        [_key("manual")],
    ),
    "protected_oldest": (
        [(_key("1000"), _at(0))] + _keys(5, start=1),
        _key("1000"),
        5,
        [_key("1001")],
    ),
    "at_cap": (_keys(5), None, 5, []),
    "over_cap": (
        _keys(8),
        None,
        5,
        [_key("1000"), _key("1001"), _key("1002")],
    ),
    "max_backups_zero": (
        [(_key("1000"), _at(0)), (_key("2000"), _at(1))],
        _key("2000"),
        0,
        [_key("1000")],
    ),
    "empty": ([], None, 5, []),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_copies_agree_on_case(case):
    objects, protected_key, max_backups, expected = CASES[case]

    assert admin_r2.select_backups_to_delete(objects, protected_key, max_backups) == expected
    assert analysis_r2.select_backups_to_delete(objects, protected_key, max_backups) == expected


def test_selector_source_is_byte_identical():
    assert inspect.getsource(analysis_r2.select_backups_to_delete) == inspect.getsource(
        admin_r2.select_backups_to_delete
    )


def test_vendored_module_docstring_names_admin_source_of_truth():
    assert "ops/admin-cli/src/stream_of_worship/admin/services/r2.py" in (analysis_r2.__doc__ or "")
