"""Seam 3 tests: `lyrics generate --stdin --wait` batch end-state.

Real Postgres (testcontainers) + mocked AnalysisClient driving jobs through
pending → completed/failed. Asserts the recordings rows end at
lrc_status='completed' + r2_lrc_url + lrc_source + visibility_status='review'
for successes, lrc_status='failed' for failures, manifest contents record job
ids and outcomes, and --resume re-polls without resubmitting. Also asserts
lyrics_feedback.resolved_at is untouched throughout.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from stream_of_worship.admin.commands import lyrics as lyrics_commands
from stream_of_worship.admin.services.analysis import AnalysisResult, JobInfo
from stream_of_worship.db.connection import ConnectionProvider
from stream_of_worship.db.postgres_schema import ALL_SCHEMA_STATEMENTS

runner = CliRunner()
lyrics_app = lyrics_commands.app


@pytest.fixture(autouse=True)
def _disable_ssl(monkeypatch):
    def _factory(url, sslmode="require"):
        return ConnectionProvider(url, sslmode="disable")

    monkeypatch.setattr(lyrics_commands, "ConnectionProvider", _factory)


def _drop_all_tables(make_test_provider):
    try:
        cleanup_provider = make_test_provider()
        with cleanup_provider.get_connection().cursor() as cur:
            cur.execute(
                """
                DROP TABLE IF EXISTS lyrics_feedback CASCADE;
                DROP TABLE IF EXISTS songset_share CASCADE;
                DROP TABLE IF EXISTS lyric_mark CASCADE;
                DROP TABLE IF EXISTS user_lrc_override CASCADE;
                DROP TABLE IF EXISTS user_settings CASCADE;
                DROP TABLE IF EXISTS songset_items CASCADE;
                DROP TABLE IF EXISTS songsets CASCADE;
                DROP TABLE IF EXISTS theme_anchors CASCADE;
                DROP TABLE IF EXISTS song_line_embedding CASCADE;
                DROP TABLE IF EXISTS song_embedding CASCADE;
                DROP TABLE IF EXISTS recordings CASCADE;
                DROP TABLE IF EXISTS songs CASCADE;
                DROP TABLE IF EXISTS "session" CASCADE;
                DROP TABLE IF EXISTS "account" CASCADE;
                DROP TABLE IF EXISTS "verification" CASCADE;
                DROP TABLE IF EXISTS "user" CASCADE;
                DROP FUNCTION IF EXISTS update_updated_at_column CASCADE;
                DROP FUNCTION IF EXISTS update_updatedat_column CASCADE;
                DROP EXTENSION IF EXISTS vector CASCADE;
                """
            )
        cleanup_provider.close()
    except Exception:
        pass


def _write_config(tmp_path, postgres_url):
    config_path = Path(tmp_path) / "config.toml"
    config_path.write_text(f'[database]\nurl = "{postgres_url}"\n')
    return config_path


def _init_schema(make_test_provider):
    _drop_all_tables(make_test_provider)
    provider = make_test_provider()
    with provider.get_connection().cursor() as cur:
        for stmt in ALL_SCHEMA_STATEMENTS:
            cur.execute(stmt)
    provider.get_connection().commit()
    return provider


def _seed(provider):
    """Seed two songs, one recording each, and open sad feedback on both.

    Each recording's target matches its feedback hash, so both pass the
    pre-flight guard and get submitted.
    """
    conn = provider.get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO "user" (id, name, email, "emailVerified")
            OVERRIDING SYSTEM VALUE
            VALUES (1, 'User One', 'u1@example.com', true)
            """
        )
        cur.execute(
            """
            INSERT INTO songs (id, title, source_url, scraped_at, composer, lyrics_raw)
            VALUES
                ('song_001', 'Song One', 'https://example.com/1', '2024-01-01T00:00:00', '作曲', '歌词一'),
                ('song_002', 'Song Two', 'https://example.com/2', '2024-01-01T00:00:00', '作曲', '歌词二')
            """
        )
        for hash_, prefix, filename, song_id in [
            ("hashaaaaaaaaaaaa", "hashaaaaaaaa", "one.mp3", "song_001"),
            ("hashbbbbbbbbbbbb", "hashbbbbbbbb", "two.mp3", "song_002"),
        ]:
            cur.execute(
                """
                INSERT INTO recordings (
                    content_hash, hash_prefix, song_id, original_filename,
                    file_size_bytes, imported_at, lrc_status, r2_audio_url
                )
                VALUES (%s, %s, %s, %s, 100, '2024-01-01T00:00:00', 'missing', 's3://bucket/audio')
                """,
                (hash_, prefix, song_id, filename),
            )
        cur.execute(
            """
            INSERT INTO lyrics_feedback (id, user_id, recording_content_hash, rating, reason)
            VALUES
                ('fb-1', 1, 'hashaaaaaaaaaaaa', 'sad', 'missing'),
                ('fb-2', 1, 'hashbbbbbbbbbbbb', 'sad', 'timing')
            """
        )
    conn.commit()


def _recording_row(provider, hash_prefix, cols="lrc_status, r2_lrc_url, lrc_source, visibility_status"):
    with provider.get_connection().cursor() as cur:
        cur.execute(
            f"SELECT {cols} FROM recordings WHERE hash_prefix = %s",
            (hash_prefix,),
        )
        return cur.fetchone()


def _feedback_open_count(provider):
    with provider.get_connection().cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM lyrics_feedback WHERE resolved_at IS NULL")
        return cur.fetchone()[0]


class _FakeAnalysisClient:
    """Drives LRC jobs through pending → completed/failed on demand.

    `states[job_id]` holds either "pending" (first N get_job calls) or a
    terminal JobInfo. `submit_calls` counts submit_lrc invocations so
    resume tests can assert no resubmission happened.
    """

    def __init__(self, states: dict[str, object], pending_calls: int = 0):
        self.states = states
        self.pending_calls = pending_calls
        self.seen_calls: dict[str, int] = {jid: 0 for jid in states}
        self.submit_calls = 0

    def get_job(self, job_id: str) -> JobInfo:
        self.seen_calls[job_id] = self.seen_calls.get(job_id, 0) + 1
        state = self.states[job_id]
        if state == "pending":
            return JobInfo(job_id=job_id, status="processing", job_type="lrc", progress=0.5)
        if self.seen_calls[job_id] <= self.pending_calls:
            return JobInfo(job_id=job_id, status="processing", job_type="lrc", progress=0.5)
        return state  # terminal JobInfo

    def submit_lrc(self, **kwargs):
        self.submit_calls += 1
        raise AssertionError("submit_lrc must not be called in a resumed run")


def _job(job_id: str, status: str, lrc_url=None, lrc_source=None, error=None) -> JobInfo:
    result = None
    if status == "completed":
        result = AnalysisResult(lrc_url=lrc_url, lrc_source=lrc_source)
    return JobInfo(
        job_id=job_id,
        status=status,
        job_type="lrc",
        error_message=error,
        result=result,
    )


def _manifest_dir(tmp_path):
    d = tmp_path / "manifests"
    return d


def _real_db_factory(provider):
    """get_db_client stand-in that honors sslmode=disable."""

    from stream_of_worship.admin.db.client import DatabaseClient

    def _factory(config):
        return DatabaseClient(provider)

    return _factory


def _invoke_wait_generate(tmp_path, postgres_url, config_path, stdin_text="song_001\nsong_002\n"):
    """Invoke `generate --stdin --force --wait` with:
    - real DB (config points at testcontainers Postgres)
    - AnalysisClient patched at class level in the lyrics module
    - R2Client patched to a MagicMock whose lrc_exists returns None
      (service-first path provides lrc_url)
    - AnalysisClient instance replaced with the fake
    """
    manifests = _manifest_dir(tmp_path)
    fake = None  # set inside the patch context

    with (
        patch.object(lyrics_commands, "AnalysisClient") as analysis_cls,
        patch.object(lyrics_commands, "R2Client") as r2_cls,
        patch.dict(
            "os.environ", {"SOW_BATCH_MANIFEST_DIR": str(manifests)}
        ),
    ):
        fake = _FakeAnalysisClient(states={})
        analysis_cls.return_value = fake
        r2_cls.return_value.lrc_exists.return_value = None
        # submit_lrc_batch needs to produce job ids for the poll loop;
        # patch it to submit through the fake so job ids and DB state line up.
        def _fake_submit_batch(**kwargs):
            analysis_client = kwargs["analysis_client"]
            submissions = []
            for i, song_id in enumerate(kwargs["song_ids"], 1):
                job_id = f"job-{i:03d}"
                db_client = kwargs["db_client"]
                db_client.update_recording_status(
                    hash_prefix=f"hash{'aaaaaa' if song_id == 'song_001' else 'bbbbbb'}",
                    lrc_status="processing",
                    lrc_job_id=job_id,
                )
                submissions.append((song_id, f"hash{'aaaaaaaaaaaa' if song_id == 'song_001' else 'bbbbbbbbbbbb'}", job_id))
            return submissions

        with patch.object(lyrics_commands, "submit_lrc_batch", side_effect=_fake_submit_batch):
            result = runner.invoke(
                lyrics_app,
                ["generate", "--stdin", "--force", "--wait", "--config", str(config_path)],
                input=stdin_text,
            )
    return result, manifests, fake


@pytest.mark.integration
class TestBatchWaitEndState:
    def test_batch_wait_completes_and_fails(
        self, make_test_provider, postgres_url, tmp_path
    ):
        provider = _init_schema(make_test_provider)
        _seed(provider)
        config_path = _write_config(tmp_path, postgres_url)

        # Drive: job-001 completes (song_001), job-002 fails (song_002).
        with (
            patch.object(lyrics_commands, "AnalysisClient") as analysis_cls,
            patch.object(lyrics_commands, "R2Client") as r2_cls,
            patch.object(
                lyrics_commands,
                "get_db_client",
                side_effect=_real_db_factory(provider),
            ),
            patch.dict(
                "os.environ", {"SOW_BATCH_MANIFEST_DIR": str(tmp_path / "manifests")}
            ),
        ):
            fake = _FakeAnalysisClient(
                states={
                    "job-001": _job("job-001", "completed", lrc_url="s3://bucket/lrc1.lrc", lrc_source="youtube_transcript"),
                    "job-002": _job("job-002", "failed", error="transcription boom"),
                }
            )
            analysis_cls.return_value = fake
            r2_cls.return_value.lrc_exists.return_value = None

            def _fake_submit_batch(**kwargs):
                submissions = []
                mapping = {
                    "song_001": ("hashaaaaaaaa", "hashaaaaaaaaaaaa"),
                    "song_002": ("hashbbbbbbbb", "hashbbbbbbbbbbbb"),
                }
                for i, song_id in enumerate(kwargs["song_ids"], 1):
                    prefix, content_hash = mapping[song_id]
                    kwargs["db_client"].update_recording_status(
                        hash_prefix=prefix, lrc_status="processing", lrc_job_id=f"job-{i:03d}"
                    )
                    submissions.append((song_id, content_hash, f"job-{i:03d}"))
                return submissions

            with patch.object(lyrics_commands, "submit_lrc_batch", side_effect=_fake_submit_batch):
                result = runner.invoke(
                    lyrics_app,
                    ["generate", "--stdin", "--force", "--wait", "--config", str(config_path)],
                    input="song_001\nsong_002\n",
                )

                assert result.exit_code == 0, result.output

        # song_001: completed with review demotion + provenance
        row = _recording_row(provider, "hashaaaaaaaa")
        assert row[0] == "completed"
        assert row[1] == "s3://bucket/lrc1.lrc"
        assert row[2] == "youtube_transcript"
        assert row[3] == "review"

        # song_002: failed
        row = _recording_row(provider, "hashbbbbbbbb")
        assert row[0] == "failed"

        # Feedback untouched (ADR-0007: nothing auto-resolved)
        assert _feedback_open_count(provider) == 2

        # Manifest records job ids and outcomes
        manifests = list((tmp_path / "manifests").glob("*_manifest.json"))
        assert len(manifests) == 1
        manifest = json.loads(manifests[0].read_text())
        assert manifest["kind"] == "lyrics_generate"
        outcomes = {e["job_id"]: e["outcome"] for e in manifest["jobs"]}
        assert outcomes == {"job-001": "completed", "job-002": "failed"}

        # Report: failures carry job ids; resolve commands listed for completed
        assert "job-002" in result.output
        assert "lyrics feedback resolve song_001" in result.output
        assert "lyrics feedback resolve song_002" not in result.output
        _drop_all_tables(make_test_provider)

    def test_resume_repolls_without_resubmitting(
        self, make_test_provider, postgres_url, tmp_path
    ):
        provider = _init_schema(make_test_provider)
        _seed(provider)
        config_path = _write_config(tmp_path, postgres_url)
        manifests = _manifest_dir(tmp_path)

        # Simulate an interrupted run: manifest on disk with both jobs pending.
        manifests.mkdir(parents=True)
        manifest_path = manifests / "2026-09-24T000000_lyrics_manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "batch_id": "2026-09-24T000000_lyrics",
                    "kind": "lyrics_generate",
                    "started_at": "2026-09-24T00:00:00+00:00",
                    "jobs": [
                        {"song_id": "song_001", "content_hash": "hashaaaaaaaaaaaa", "job_id": "job-001", "outcome": "pending"},
                        {"song_id": "song_002", "content_hash": "hashbbbbbbbbbbbb", "job_id": "job-002", "outcome": "pending"},
                    ],
                }
            )
        )

        with (
            patch.object(lyrics_commands, "AnalysisClient") as analysis_cls,
            patch.object(lyrics_commands, "R2Client") as r2_cls,
            patch.object(
                lyrics_commands,
                "get_db_client",
                side_effect=_real_db_factory(provider),
            ),
        ):
            fake = _FakeAnalysisClient(
                states={
                    "job-001": _job("job-001", "completed", lrc_url="s3://bucket/lrc1.lrc", lrc_source="qwen3_asr"),
                    "job-002": _job("job-002", "failed", error="boom"),
                }
            )
            analysis_cls.return_value = fake
            r2_cls.return_value.lrc_exists.return_value = None
            result = runner.invoke(
                lyrics_app,
                ["generate", "--resume", str(manifest_path), "--config", str(config_path)],
            )

        assert result.exit_code == 0, result.output
        # No resubmission: the fake's submit_lrc raises AssertionError, so
        # reaching here proves the resume path only polled.
        assert fake.submit_calls == 0

        row = _recording_row(provider, "hashaaaaaaaa")
        assert row[0] == "completed"
        assert row[1] == "s3://bucket/lrc1.lrc"
        assert row[3] == "review"
        row = _recording_row(provider, "hashbbbbbbbb")
        assert row[0] == "failed"
        assert _feedback_open_count(provider) == 2

        # Manifest outcomes updated in place
        manifest = json.loads(manifest_path.read_text())
        outcomes = {e["job_id"]: e["outcome"] for e in manifest["jobs"]}
        assert outcomes == {"job-001": "completed", "job-002": "failed"}
        _drop_all_tables(make_test_provider)

    def test_submit_lrc_batch_returns_submitted_triples(
        self, make_test_provider, postgres_url, tmp_path
    ):
        """Contract the poll loop depends on: submit_lrc_batch returns
        (song_id, content_hash, job_id) for every submitted song."""
        from stream_of_worship.admin.services.lrc_jobs import submit_lrc_batch

        provider = _init_schema(make_test_provider)
        _seed(provider)
        fake = _FakeAnalysisClient(states={})

        def _submit(**kwargs):
            job = _job("pending", "processing")
            job.job_id = f"job-{_submit.counter}"
            _submit.counter += 1
            return job

        _submit.counter = 1

        from stream_of_worship.admin.db.client import DatabaseClient

        db_client = DatabaseClient(provider)
        submissions = submit_lrc_batch(
            song_ids=["song_001", "song_002"],
            db_client=db_client,
            analysis_client=MagicMock(submit_lrc=_submit),
            force=True,
            whisper_model="large-v3",
            language="auto",
            no_vocals=False,
            no_youtube=False,
            no_whisper_cache=False,
            no_qwen3_asr=False,
            force_qwen3_asr=False,
            console=runner_console(),
        )
        assert len(submissions) == 2
        by_song = {s[0]: s for s in submissions}
        assert by_song["song_001"][1] == "hashaaaaaaaaaaaa"
        assert by_song["song_002"][1] == "hashbbbbbbbbbbbb"
        assert by_song["song_001"][2].startswith("job-")
        assert by_song["song_002"][2].startswith("job-")
        # DB marked processing with the submitted job ids
        row = _recording_row(provider, "hashaaaaaaaa", cols="lrc_status, lrc_job_id")
        assert row[0] == "processing"
        assert row[1] == by_song["song_001"][2]
        _drop_all_tables(make_test_provider)


def _config(tmp_path, postgres_url):
    from stream_of_worship.admin.config import AdminConfig

    return AdminConfig.load(_write_config(tmp_path, postgres_url))


def runner_console():
    from rich.console import Console

    return Console()