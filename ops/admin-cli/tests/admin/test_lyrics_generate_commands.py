"""Tests for `sow-admin lyrics generate` stdin/batch behavior."""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from stream_of_worship.admin.commands import lyrics as lyrics_commands

runner = CliRunner()
lyrics_app = lyrics_commands.app


def test_generate_requires_song_id_or_stdin():
    result = runner.invoke(lyrics_app, ["generate"])
    assert result.exit_code == 1
    assert "Either provide a song_id argument or use --stdin flag" in result.output


def test_generate_rejects_song_id_with_stdin():
    result = runner.invoke(lyrics_app, ["generate", "song_001", "--stdin"])
    assert result.exit_code == 1
    assert "Cannot use both song_id argument and --stdin flag" in result.output


def test_generate_rejects_wait_with_stdin():
    result = runner.invoke(lyrics_app, ["generate", "--stdin", "--wait"])
    assert result.exit_code == 1
    assert "--wait is not supported with --stdin" in result.output


def test_generate_empty_stdin_prints_message_and_exits_zero():
    with (
        patch.object(lyrics_commands, "AdminConfig", MagicMock()),
        patch.object(lyrics_commands, "get_db_client", MagicMock()),
        patch.object(lyrics_commands, "AnalysisClient", MagicMock()),
    ):
        result = runner.invoke(lyrics_app, ["generate", "--stdin"], input="")
    assert result.exit_code == 0, result.output
    assert "No song IDs provided via stdin" in result.output


def _invoke_stdin_generate(stdin_text: str):
    """Invoke `generate --stdin --force` with config/DB/service and both
    submit helpers mocked; returns (result, single_mock, batch_mock)."""
    with (
        patch.object(lyrics_commands, "AdminConfig", MagicMock()),
        patch.object(lyrics_commands, "get_db_client", MagicMock()),
        patch.object(lyrics_commands, "AnalysisClient", MagicMock()),
        patch.object(lyrics_commands, "submit_lrc_single", return_value=MagicMock()) as single_mock,
        patch.object(lyrics_commands, "submit_lrc_batch", return_value=MagicMock()) as batch_mock,
    ):
        result = runner.invoke(lyrics_app, ["generate", "--stdin", "--force"], input=stdin_text)
    return result, single_mock, batch_mock


def test_generate_stdin_single_id_routes_to_single():
    result, single_mock, batch_mock = _invoke_stdin_generate("song_001\n")
    assert result.exit_code == 0, result.output
    single_mock.assert_called_once()
    assert single_mock.call_args.kwargs["song_id"] == "song_001"
    assert single_mock.call_args.kwargs["force"] is True
    batch_mock.assert_not_called()


def test_generate_stdin_multiple_ids_routes_to_batch():
    result, single_mock, batch_mock = _invoke_stdin_generate("song_001\nsong_002\n")
    assert result.exit_code == 0, result.output
    batch_mock.assert_called_once()
    assert batch_mock.call_args.kwargs["song_ids"] == ["song_001", "song_002"]
    assert batch_mock.call_args.kwargs["force"] is True
    single_mock.assert_not_called()
