"""Tests for `sow-admin lyrics align` wait-by-default and manifest handling."""

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from stream_of_worship.admin.commands import lyrics as lyrics_commands
from stream_of_worship.admin.services.analysis import AnalysisResult, JobInfo

runner = CliRunner()
lyrics_app = lyrics_commands.app

HASH = "h" * 64


def _quiet_console() -> Console:
    return Console(file=io.StringIO(), width=200)


def _invoke(args: list[str], stdin_text: str = ""):
    """Invoke `lyrics align` with config/DB/service/R2 all mocked."""
    with (
        patch.object(lyrics_commands, "AdminConfig", MagicMock()) as config_mock,
        patch.object(lyrics_commands, "get_db_client", MagicMock()),
        patch.object(lyrics_commands, "AnalysisClient", MagicMock()),
        patch.object(lyrics_commands, "R2Client", MagicMock()),
        patch.object(lyrics_commands, "_submit_forced_alignment_single") as single_mock,
        patch.object(lyrics_commands, "_run_alignment_batch") as batch_mock,
        patch.object(lyrics_commands, "_resume_alignment_batch") as resume_mock,
    ):
        config_mock.load.return_value = MagicMock(analysis_url="http://analysis")
        result = runner.invoke(lyrics_app, ["align", *args], input=stdin_text)
    return result, single_mock, batch_mock, resume_mock


# ---------------------------------------------------------------------------
# Routing and flags
# ---------------------------------------------------------------------------


def test_align_requires_song_id_stdin_or_resume():
    result, _, _, _ = _invoke([])
    assert result.exit_code == 1
    assert "Either provide a song_id argument" in result.output


def test_align_rejects_song_id_with_stdin():
    result, _, _, _ = _invoke(["song_001", "--stdin"], stdin_text="song_002\n")
    assert result.exit_code == 1
    assert "Cannot use both song_id argument and --stdin flag" in result.output


@pytest.mark.parametrize("args", [(), ("--no-wait",)])
def test_align_single_forwards_wait(args):
    """Single-song mode waits by default; --no-wait opts out."""
    result, single_mock, batch_mock, _ = _invoke(["song_001", *args])
    assert result.exit_code == 0, result.output
    single_mock.assert_called_once()
    assert single_mock.call_args.kwargs["wait"] is (not args)
    batch_mock.assert_not_called()


@pytest.mark.parametrize(
    "args, expected_no_wait", [((), False), (("--no-wait",), True)]
)
def test_align_stdin_batch_forwards_no_wait(args, expected_no_wait):
    result, single_mock, batch_mock, _ = _invoke(
        ["--stdin", *args], stdin_text="song_001\nsong_002\n"
    )
    assert result.exit_code == 0, result.output
    batch_mock.assert_called_once()
    assert batch_mock.call_args.kwargs["no_wait"] is expected_no_wait
    assert batch_mock.call_args.kwargs["song_ids"] == ["song_001", "song_002"]
    single_mock.assert_not_called()


def test_align_stdin_single_id_takes_batch_path():
    """A one-id pipe uses the manifest-able batch path, not the 600 s single wait."""
    result, single_mock, batch_mock, _ = _invoke(["--stdin"], stdin_text="song_001\n")
    assert result.exit_code == 0, result.output
    assert "not supported with --stdin" not in result.output
    single_mock.assert_not_called()
    batch_mock.assert_called_once()
    assert batch_mock.call_args.kwargs["song_ids"] == ["song_001"]


def test_align_stdin_empty_exits_zero():
    result, single_mock, batch_mock, _ = _invoke(["--stdin"], stdin_text="")
    assert result.exit_code == 0, result.output
    assert "No song IDs provided via stdin" in result.output
    single_mock.assert_not_called()
    batch_mock.assert_not_called()


def test_align_resume_routes_to_resume_helper(tmp_path):
    manifest_file = tmp_path / "run1_manifest.json"
    result, single_mock, batch_mock, resume_mock = _invoke(["--resume", str(manifest_file)])
    assert result.exit_code == 0, result.output
    resume_mock.assert_called_once()
    assert resume_mock.call_args.kwargs["manifest_file"] == manifest_file
    single_mock.assert_not_called()
    batch_mock.assert_not_called()


def test_align_rejects_wait_flag():
    result = runner.invoke(lyrics_app, ["align", "song_001", "--wait"])
    assert result.exit_code == 2
    assert "No such option" in result.output


def test_align_rejects_resume_with_stdin():
    result = runner.invoke(lyrics_app, ["align", "--resume", "m.json", "--stdin"], input="")
    assert result.exit_code == 1
    assert "Cannot use both --stdin and --resume" in result.output


def test_align_rejects_resume_with_song_id():
    result = runner.invoke(lyrics_app, ["align", "song_001", "--resume", "m.json"], input="")
    assert result.exit_code == 1
    assert "Cannot use both song_id argument and --resume" in result.output


def test_align_rejects_no_wait_with_resume():
    result = runner.invoke(lyrics_app, ["align", "--resume", "m.json", "--no-wait"], input="")
    assert result.exit_code == 1
    assert "--no-wait is not valid with --resume" in result.output


# ---------------------------------------------------------------------------
# Manifest + poll machinery
# ---------------------------------------------------------------------------


def _write_manifest(path: Path, kind: str, entries: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "batch_id": path.name.replace("_manifest.json", ""),
                "kind": kind,
                "started_at": "2024-01-01T00:00:00+00:00",
                "jobs": entries,
            }
        )
    )


def _pending_entry(song_id: str = "song_001", job_id: str = "job-1") -> dict:
    return {
        "song_id": song_id,
        "content_hash": HASH,
        "job_id": job_id,
        "outcome": "pending",
    }


def test_write_manifest_records_kind(tmp_path):
    manifest_path = lyrics_commands._write_lyrics_manifest(
        "b", tmp_path, "start", [], kind="lyrics_align"
    )
    assert manifest_path is not None
    assert json.loads(manifest_path.read_text())["kind"] == "lyrics_align"


def test_poll_preserves_kind_and_uses_default_lrc_source(tmp_path):
    manifest_path = tmp_path / "run1_manifest.json"
    _write_manifest(manifest_path, "lyrics_align", [_pending_entry()])

    analysis_client = MagicMock()
    analysis_client.get_job.return_value = JobInfo(
        job_id="job-1",
        status="completed",
        job_type="forced_alignment",
        progress=1.0,
        result=AnalysisResult(lrc_url="s3://b/lrc", lrc_source=None),
    )
    db_client = MagicMock()

    entries = lyrics_commands._poll_lrc_batch(
        [("song_001", HASH, "job-1")],
        db_client,
        analysis_client,
        MagicMock(),
        _quiet_console(),
        manifest_path,
        kind="lyrics_align",
        default_lrc_source="forced_alignment",
    )

    assert entries[0]["outcome"] == "completed"
    assert db_client.update_recording_lrc.call_args.kwargs["lrc_source"] == "forced_alignment"
    reloaded = json.loads(manifest_path.read_text())
    assert reloaded["kind"] == "lyrics_align"
    assert reloaded["jobs"][0]["outcome"] == "completed"


def test_poll_prefers_service_lrc_source(tmp_path):
    """A source reported by the service wins over the default."""
    manifest_path = tmp_path / "run1_manifest.json"
    _write_manifest(manifest_path, "lyrics_align", [_pending_entry()])

    analysis_client = MagicMock()
    analysis_client.get_job.return_value = JobInfo(
        job_id="job-1",
        status="completed",
        job_type="forced_alignment",
        progress=1.0,
        result=AnalysisResult(lrc_url="s3://b/lrc", lrc_source="qwen3_forced_alignment"),
    )
    db_client = MagicMock()

    lyrics_commands._poll_lrc_batch(
        [("song_001", HASH, "job-1")],
        db_client,
        analysis_client,
        MagicMock(),
        _quiet_console(),
        manifest_path,
        kind="lyrics_align",
        default_lrc_source="forced_alignment",
    )

    assert db_client.update_recording_lrc.call_args.kwargs["lrc_source"] == "qwen3_forced_alignment"


def test_run_alignment_batch_no_wait_writes_no_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("SOW_BATCH_MANIFEST_DIR", str(tmp_path))

    with (
        patch.object(
            lyrics_commands,
            "_submit_forced_alignment_batch",
            return_value=([("song_001", HASH, "job-1")], 0, 0),
        ),
        patch.object(lyrics_commands, "_poll_lrc_batch") as poll_mock,
    ):
        lyrics_commands._run_alignment_batch(
            song_ids=["song_001"],
            db_client=MagicMock(),
            analysis_client=MagicMock(),
            r2_client=MagicMock(),
            language="auto",
            force=False,
            use_vocals_stem=True,
            console=_quiet_console(),
            no_wait=True,
        )

    poll_mock.assert_not_called()
    assert list(tmp_path.glob("*_manifest.json")) == []


def test_run_alignment_batch_writes_align_manifest(tmp_path, monkeypatch):
    """Wait mode writes a lyrics_align manifest and polls it."""
    monkeypatch.setenv("SOW_BATCH_MANIFEST_DIR", str(tmp_path))

    def _fake_poll(
        submissions, db_client, analysis_client, r2_client, console, manifest_path, **kwargs
    ):
        assert kwargs["kind"] == "lyrics_align"
        assert kwargs["default_lrc_source"] == "forced_alignment"
        assert manifest_path is not None
        assert json.loads(manifest_path.read_text())["kind"] == "lyrics_align"
        return [
            {
                "song_id": "song_001",
                "content_hash": HASH,
                "job_id": "job-1",
                "outcome": "completed",
            }
        ]

    with (
        patch.object(
            lyrics_commands,
            "_submit_forced_alignment_batch",
            return_value=([("song_001", HASH, "job-1")], 0, 0),
        ),
        patch.object(lyrics_commands, "_poll_lrc_batch", side_effect=_fake_poll) as poll_mock,
    ):
        output = io.StringIO()
        lyrics_commands._run_alignment_batch(
            song_ids=["song_001"],
            db_client=MagicMock(),
            analysis_client=MagicMock(),
            r2_client=MagicMock(),
            language="auto",
            force=False,
            use_vocals_stem=True,
            console=Console(file=output, width=200),
            no_wait=False,
        )

    poll_mock.assert_called_once()
    assert "Completed: 1" in output.getvalue()
    assert list(tmp_path.glob("*_align_manifest.json"))


def test_resume_rejects_wrong_kind(tmp_path):
    manifest_file = tmp_path / "run1_manifest.json"
    _write_manifest(manifest_file, "lyrics_generate", [_pending_entry()])

    with pytest.raises(typer.Exit) as excinfo:
        lyrics_commands._resume_alignment_batch(
            manifest_file=manifest_file,
            db_client=MagicMock(),
            analysis_client=MagicMock(),
            r2_client=MagicMock(),
            console=_quiet_console(),
        )

    assert excinfo.value.exit_code == 1


def test_resume_reports_nothing_pending(tmp_path):
    manifest_file = tmp_path / "run1_manifest.json"
    done = _pending_entry()
    done["outcome"] = "completed"
    _write_manifest(manifest_file, "lyrics_align", [done])

    with patch.object(lyrics_commands, "_poll_lrc_batch") as poll_mock:
        lyrics_commands._resume_alignment_batch(
            manifest_file=manifest_file,
            db_client=MagicMock(),
            analysis_client=MagicMock(),
            r2_client=MagicMock(),
            console=_quiet_console(),
        )

    poll_mock.assert_not_called()


def test_resume_polls_pending_jobs(tmp_path):
    manifest_file = tmp_path / "run1_manifest.json"
    _write_manifest(manifest_file, "lyrics_align", [_pending_entry()])

    analysis_client = MagicMock()
    analysis_client.get_job.return_value = JobInfo(
        job_id="job-1",
        status="completed",
        job_type="forced_alignment",
        progress=1.0,
        result=AnalysisResult(lrc_url="s3://b/lrc", lrc_source=None),
    )
    db_client = MagicMock()

    lyrics_commands._resume_alignment_batch(
        manifest_file=manifest_file,
        db_client=db_client,
        analysis_client=analysis_client,
        r2_client=MagicMock(),
        console=_quiet_console(),
    )

    db_client.update_recording_lrc.assert_called_once()
    assert db_client.update_recording_lrc.call_args.kwargs["lrc_source"] == "forced_alignment"
    assert json.loads(manifest_file.read_text())["jobs"][0]["outcome"] == "completed"
