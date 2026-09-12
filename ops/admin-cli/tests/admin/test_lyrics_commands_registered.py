"""Tests for the lyrics command group registration.

Pins the clean cutover: the five LRC-file lifecycle commands live under
``sow-admin lyrics`` with their new names, and the old ``audio`` aliases
are gone.
"""

from typer.testing import CliRunner

from stream_of_worship.admin.main import app

runner = CliRunner()


def test_lyrics_help_lists_lifecycle_commands():
    """lyrics --help shows generate/align/view/upload/edit plus feedback."""
    result = runner.invoke(app, ["lyrics", "--help"])
    assert result.exit_code == 0
    for command in ("generate", "align", "view", "upload", "edit", "feedback"):
        assert command in result.output


def test_audio_help_does_not_list_lrc_lifecycle_commands():
    """audio --help no longer offers the moved LRC-file commands."""
    result = runner.invoke(app, ["audio", "--help"])
    assert result.exit_code == 0
    for command in ("lrc", "align-lrc", "view-lrc", "upload-lrc", "edit-lrc"):
        assert command not in result.output