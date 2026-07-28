"""Tests for --relax parser."""

from __future__ import annotations

from stream_of_worship.admin.commands.songset import _parse_relax


def test_relax_h1():
    result = _parse_relax("h1")
    assert result.get("relax_h1") is True


def test_relax_h2_with_value():
    result = _parse_relax("h2:90")
    assert result.get("relax_h2_bpm") == 90


def test_relax_h3_without_value():
    result = _parse_relax("h3")
    assert result.get("relax_h3_bpm") is None


def test_relax_h3_with_value():
    result = _parse_relax("h3:85")
    assert result.get("relax_h3_bpm") == 85


def test_relax_h4():
    result = _parse_relax("h4")
    assert result.get("relax_h4") is True


def test_relax_h4_with_value():
    result = _parse_relax("h4:40")
    assert result.get("relax_h4") is True
    assert result.get("relax_h4_bpm") == 40


def test_relax_h5():
    result = _parse_relax("h5")
    assert result.get("relax_h5") is True


def test_relax_h5_with_value():
    result = _parse_relax("h5:3")
    assert result.get("relax_h5") is True
    assert result.get("relax_h5_cfd") == 3


def test_relax_combined():
    result = _parse_relax("h2:90,h3:80,h4,h5:3")
    assert result.get("relax_h2_bpm") == 90
    assert result.get("relax_h3_bpm") == 80
    assert result.get("relax_h4") is True
    assert result.get("relax_h5") is True
    assert result.get("relax_h5_cfd") == 3


def test_relax_unknown_token():
    result = _parse_relax("h1,unknown")
    assert result.get("relax_h1") is True
