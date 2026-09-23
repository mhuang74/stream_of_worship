"""Tests for `sow-admin lyrics upload-structured` and `lyrics view-structured`."""

import json
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from stream_of_worship.admin.commands import lyrics as lyrics_commands
from stream_of_worship.admin.db.models import Recording, Song

runner = CliRunner()
lyrics_app = lyrics_commands.app

SECTION_TAGGED_TEXT = "[Verse]\nLine 1\nLine 2\n[Chorus]\nChorus line 1\nChorus line 2\nChorus line 3"


def _make_recording(
    structured_lyrics=None,
    structured_lyrics_raw=None,
    lrc_status="pending",
) -> Recording:
    return Recording(
        content_hash="abc123",
        hash_prefix="abc123",
        original_filename="test.mp3",
        file_size_bytes=1000,
        imported_at="2024-01-15T10:30:00",
        structured_lyrics=structured_lyrics,
        structured_lyrics_raw=structured_lyrics_raw,
        lrc_status=lrc_status,
    )


def _make_song(title="Test Song") -> Song:
    return Song(
        id="song_0001",
        title=title,
        source_url="https://sop.org/song/123",
        scraped_at="2024-01-15T10:30:00",
        lyrics_raw="irrelevant",
    )


def _invoke_upload(tmp_path, recording, args=(), confirm="y"):
    """Invoke upload-structured with config/DB/prompt mocked.

    Returns (result, db_mock).
    """
    lyrics_file = tmp_path / "lyrics.txt"
    lyrics_file.write_text(SECTION_TAGGED_TEXT, encoding="utf-8")

    db_mock = MagicMock()
    db_mock.get_recording_by_song_id.return_value = recording
    db_mock.get_song.return_value = _make_song()

    with (
        patch.object(lyrics_commands, "AdminConfig", MagicMock()),
        patch.object(lyrics_commands, "get_db_client", return_value=db_mock),
        patch.object(lyrics_commands, "prompt_confirmation", return_value=confirm == "y"),
    ):
        result = runner.invoke(
            lyrics_app, ["upload-structured", "song_0001", str(lyrics_file), *args]
        )
    return result, db_mock


def test_upload_structured_happy_path(tmp_path):
    recording = _make_recording()
    result, db_mock = _invoke_upload(tmp_path, recording)

    assert result.exit_code == 0, result.output
    assert "Upload Complete" in result.output
    db_mock.update_recording_structured_lyrics.assert_called_once()
    kwargs = db_mock.update_recording_structured_lyrics.call_args.kwargs
    assert kwargs["hash_prefix"] == "abc123"
    assert kwargs["structured_lyrics_raw"] == SECTION_TAGGED_TEXT
    parsed = json.loads(kwargs["structured_lyrics"])
    labels = [s["label"] for s in parsed["sections"]]
    assert labels == ["verse", "chorus"]
    assert parsed["sections"][0]["lines"] == ["Line 1", "Line 2"]
    assert parsed["sections"][1]["lines"] == ["Chorus line 1", "Chorus line 2", "Chorus line 3"]


def test_upload_structured_no_section_tags(tmp_path):
    lyrics_file = tmp_path / "lyrics.txt"
    lyrics_file.write_text("no tags here\njust plain lines\n", encoding="utf-8")

    recording = _make_recording()
    db_mock = MagicMock()
    db_mock.get_recording_by_song_id.return_value = recording
    db_mock.get_song.return_value = _make_song()

    with (
        patch.object(lyrics_commands, "AdminConfig", MagicMock()),
        patch.object(lyrics_commands, "get_db_client", return_value=db_mock),
        patch.object(lyrics_commands, "prompt_confirmation", return_value=True),
    ):
        result = runner.invoke(lyrics_app, ["upload-structured", "song_0001", str(lyrics_file)])

    assert result.exit_code == 1
    assert "No section tags" in result.output
    db_mock.update_recording_structured_lyrics.assert_not_called()


def test_upload_structured_refuses_overwrite_without_force(tmp_path):
    recording = _make_recording(structured_lyrics='{"sections": [], "preamble_lines": []}')
    result, db_mock = _invoke_upload(tmp_path, recording)

    assert result.exit_code == 1
    assert "already has structured lyrics" in result.output
    assert "--force" in result.output
    db_mock.update_recording_structured_lyrics.assert_not_called()


def test_upload_structured_force_overwrites(tmp_path):
    recording = _make_recording(structured_lyrics='{"sections": [], "preamble_lines": []}')
    result, db_mock = _invoke_upload(tmp_path, recording, args=("--force",))

    assert result.exit_code == 0, result.output
    db_mock.update_recording_structured_lyrics.assert_called_once()


def test_upload_structured_declined_confirmation(tmp_path):
    recording = _make_recording()
    result, db_mock = _invoke_upload(tmp_path, recording, confirm="n")

    assert result.exit_code == 0, result.output
    assert "cancelled" in result.output
    db_mock.update_recording_structured_lyrics.assert_not_called()


def test_upload_structured_lrc_hint_fires_when_not_completed(tmp_path):
    recording = _make_recording(lrc_status="pending")
    result, db_mock = _invoke_upload(tmp_path, recording)

    assert result.exit_code == 0, result.output
    assert "lyrics generate song_0001" in result.output


def test_upload_structured_no_lrc_hint_when_completed(tmp_path):
    recording = _make_recording(lrc_status="completed")
    result, db_mock = _invoke_upload(tmp_path, recording)

    assert result.exit_code == 0, result.output
    assert "lyrics generate" not in result.output


def test_upload_structured_components_hint_fires_when_empty(tmp_path):
    recording = _make_recording()
    db_mock = MagicMock()
    db_mock.get_recording_by_song_id.return_value = recording
    db_mock.get_song.return_value = _make_song()
    db_mock.get_song_components.return_value = []
    with (
        patch.object(lyrics_commands, "AdminConfig", MagicMock()),
        patch.object(lyrics_commands, "get_db_client", return_value=db_mock),
        patch.object(lyrics_commands, "prompt_confirmation", return_value=True),
    ):
        lyrics_file = tmp_path / "lyrics.txt"
        lyrics_file.write_text(SECTION_TAGGED_TEXT, encoding="utf-8")
        result = runner.invoke(lyrics_app, ["upload-structured", "song_0001", str(lyrics_file)])

    assert result.exit_code == 0, result.output
    assert "audio components song_0001" in result.output


def test_upload_structured_no_components_hint_when_present(tmp_path):
    recording = _make_recording()
    result, db_mock = _invoke_upload(tmp_path, recording)
    component = MagicMock()
    db_mock.get_song_components.return_value = [component]

    assert result.exit_code == 0, result.output
    assert "audio components" not in result.output


def test_view_structured_prefers_raw_text(tmp_path):
    recording = _make_recording(
        structured_lyrics='{"sections": [{"label": "verse", "raw_label": "Verse", "lines": ["json line"]}], "preamble_lines": []}',
        structured_lyrics_raw=SECTION_TAGGED_TEXT,
    )
    db_mock = MagicMock()
    db_mock.get_recording_by_song_id.return_value = recording
    db_mock.get_song.return_value = _make_song()

    with (
        patch.object(lyrics_commands, "AdminConfig", MagicMock()),
        patch.object(lyrics_commands, "get_db_client", return_value=db_mock),
    ):
        result = runner.invoke(lyrics_app, ["view-structured", "song_0001"])

    assert result.exit_code == 0, result.output
    assert "[Verse]" in result.output
    assert "[Chorus]" in result.output
    assert "json line" not in result.output


def test_view_structured_falls_back_to_json(tmp_path):
    structured = {
        "sections": [
            {"label": "verse", "raw_label": "Verse", "lines": ["Line 1", "Line 2"]},
            {"label": "chorus", "raw_label": "Chorus", "lines": ["Chorus line 1"]},
        ],
        "preamble_lines": [],
    }
    recording = _make_recording(structured_lyrics=json.dumps(structured))
    db_mock = MagicMock()
    db_mock.get_recording_by_song_id.return_value = recording
    db_mock.get_song.return_value = _make_song()

    with (
        patch.object(lyrics_commands, "AdminConfig", MagicMock()),
        patch.object(lyrics_commands, "get_db_client", return_value=db_mock),
    ):
        result = runner.invoke(lyrics_app, ["view-structured", "song_0001"])

    assert result.exit_code == 0, result.output
    assert "[Verse]" in result.output
    assert "[Chorus]" in result.output
    assert "re-rendered" in result.output


def test_view_structured_output_flag_writes_file(tmp_path):
    structured = {
        "sections": [
            {"label": "verse", "raw_label": "Verse", "lines": ["Line 1"]},
        ],
        "preamble_lines": [],
    }
    recording = _make_recording(structured_lyrics=json.dumps(structured))
    db_mock = MagicMock()
    db_mock.get_recording_by_song_id.return_value = recording
    db_mock.get_song.return_value = _make_song()
    out_file = tmp_path / "export.txt"

    with (
        patch.object(lyrics_commands, "AdminConfig", MagicMock()),
        patch.object(lyrics_commands, "get_db_client", return_value=db_mock),
    ):
        result = runner.invoke(
            lyrics_app, ["view-structured", "song_0001", "--output", str(out_file)]
        )

    assert result.exit_code == 0, result.output
    assert "Wrote structured lyrics" in result.output
    written = out_file.read_text(encoding="utf-8")
    assert "[Verse]" in written
    assert "Line 1" in written


def test_view_structured_no_structured_lyrics_exits_zero(tmp_path):
    recording = _make_recording()
    db_mock = MagicMock()
    db_mock.get_recording_by_song_id.return_value = recording
    db_mock.get_song.return_value = _make_song()

    with (
        patch.object(lyrics_commands, "AdminConfig", MagicMock()),
        patch.object(lyrics_commands, "get_db_client", return_value=db_mock),
    ):
        result = runner.invoke(lyrics_app, ["view-structured", "song_0001"])

    assert result.exit_code == 0, result.output
    assert "No structured lyrics" in result.output
