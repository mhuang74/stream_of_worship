"""THROWAWAY smoke: drive the real CLI wait paths against a stub service.

Not a permanent test — deleted after the run. Covers what the mocked suites
cannot: real `AnalysisClient` HTTP, real `wait_for_completion` polling, real
Postgres writes, and the `lyrics_align` manifest, all against a scratch
testcontainers DB and a local stub HTTP server (never the live catalog).
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.main import app

runner = CliRunner()

EMBED_DIMS = 1536


class _StubHandler(BaseHTTPRequestHandler):
    """Implements just enough of the analysis service job API."""

    jobs: dict = {}
    polls: dict = {}

    def log_message(self, *args):  # silence
        pass

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/api/v1/jobs/embedding":
            job_id = f"job_embed_{len(self.jobs) + 1}"
            self.jobs[job_id] = {
                "kind": "embedding",
                "song_id": payload["song_id"],
                "content_hash": payload["content_hash"],
                "lines": payload.get("lyrics_lines") or [],
            }
            self.polls[job_id] = 0
            self._json(
                {"job_id": job_id, "status": "queued", "job_type": "embedding", "progress": 0.0}
            )
            return
        if self.path == "/api/v1/jobs/forced-alignment":
            job_id = f"job_align_{len(self.jobs) + 1}"
            self.jobs[job_id] = {
                "kind": "alignment",
                "content_hash": payload["content_hash"],
            }
            self.polls[job_id] = 0
            self._json(
                {
                    "job_id": job_id,
                    "status": "queued",
                    "job_type": "forced_alignment",
                    "progress": 0.0,
                }
            )
            return
        self._json({"detail": "not found"}, status=404)

    def do_GET(self):  # noqa: N802
        job_id = self.path.rsplit("/", 1)[-1]
        job = self.jobs.get(job_id)
        if not job:
            self._json({"detail": "not found"}, status=404)
            return
        self.polls[job_id] += 1
        if self.polls[job_id] < 2:
            # One "processing" poll so the CLI really loops.
            self._json(
                {"job_id": job_id, "status": "processing", "job_type": job["kind"], "progress": 0.5}
            )
            return
        if job["kind"] == "embedding":
            result = {
                "song_id": job["song_id"],
                "embedding": [0.01] * EMBED_DIMS,
                "model_version": "stub-embed-v1",
                "content_hash": job["content_hash"],
                "line_embeddings": [
                    {"line_index": i, "line_text": text, "embedding": [0.02] * EMBED_DIMS}
                    for i, text in enumerate(job["lines"])
                ],
            }
        else:
            result = {
                "lrc_url": f"s3://stub-bucket/{job['content_hash'][:12]}/lyrics.lrc",
                "lrc_source": None,
                "line_count": 3,
            }
        self._json(
            {
                "job_id": job_id,
                "status": "completed",
                "job_type": job["kind"],
                "progress": 1.0,
                "result": result,
            }
        )


@pytest.fixture(autouse=True)
def _disable_ssl(monkeypatch):
    """CLI-built connections must skip SSL against the scratch Postgres."""
    from stream_of_worship.admin.commands import audio as audio_commands
    from stream_of_worship.admin.commands import catalog as catalog_commands
    from stream_of_worship.admin.commands import lyrics as lyrics_commands
    from stream_of_worship.db.connection import ConnectionProvider

    def _factory(url, sslmode="require"):
        return ConnectionProvider(url, sslmode="disable")

    for module in (catalog_commands, audio_commands, lyrics_commands):
        monkeypatch.setattr(module, "ConnectionProvider", _factory)


@pytest.fixture
def stub_service(monkeypatch):
    _StubHandler.jobs = {}
    _StubHandler.polls = {}
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "stub-key")
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def seeded(setup_db):
    db_client = setup_db["db_client"]
    db_client.insert_song(
        Song(
            id="smoke_song",
            title="Smoke Song",
            source_url="https://example.com/smoke",
            scraped_at="2024-01-01T00:00:00",
            composer="Tester",
            lyrics_raw="第一行\n第二行\n第三行",
        )
    )
    db_client.insert_recording(
        Recording(
            content_hash="s" * 64,
            hash_prefix="ssssssssssss",
            song_id="smoke_song",
            original_filename="smoke.mp3",
            file_size_bytes=1000,
            imported_at="2024-01-01T00:00:00",
            r2_audio_url="s3://stub-bucket/ssssssssssss/audio.mp3",
            lrc_status="missing",
            analysis_status="pending",
        )
    )
    return setup_db["db_client"]


def _config(tmp_path: Path, postgres_url: str, analysis_url: str) -> Path:
    path = tmp_path / "smoke_config.toml"
    path.write_text(
        f'[database]\nurl = "{postgres_url}"\n\n[service]\nanalysis_url = "{analysis_url}"\n'
    )
    return path


@pytest.mark.integration
def test_embed_default_wait_end_to_end(stub_service, seeded, postgres_url, tmp_path):
    """Real HTTP + real poll + real DB write; no mocks of AnalysisClient."""
    config_path = _config(tmp_path, postgres_url, stub_service)

    result = runner.invoke(
        app, ["audio", "embed", "smoke_song", "--force", "--config", str(config_path)]
    )

    assert result.exit_code == 0, result.output
    assert "Done: 1 succeeded, 0 failed" in result.output
    # Polled at least twice (queued/processing -> completed), i.e. the loop ran.
    assert max(_StubHandler.polls.values()) >= 2

    with seeded.connection_provider.get_connection().cursor() as cur:
        cur.execute(
            "select model_version, content_hash from song_embedding where song_id = %s",
            ("smoke_song",),
        )
        row = cur.fetchone()
        assert row is not None and row[0] == "stub-embed-v1"
        cur.execute(
            "select count(*) from song_line_embedding where song_id = %s", ("smoke_song",)
        )
        assert cur.fetchone()[0] == 3


@pytest.mark.integration
def test_align_default_wait_end_to_end(stub_service, seeded, postgres_url, tmp_path):
    """Single-song align: real forced-alignment submit + poll + update_recording_lrc."""
    config_path = _config(tmp_path, postgres_url, stub_service)

    result = runner.invoke(
        app, ["lyrics", "align", "smoke_song", "--config", str(config_path)]
    )

    assert result.exit_code == 0, result.output
    assert "Forced alignment completed" in result.output

    with seeded.connection_provider.get_connection().cursor() as cur:
        cur.execute(
            "select lrc_status, lrc_source, r2_lrc_url from recordings where song_id = %s",
            ("smoke_song",),
        )
        status, source, url = cur.fetchone()
        assert status == "completed"
        assert source == "forced_alignment"
        assert url.endswith("/lyrics.lrc")


@pytest.mark.integration
def test_align_stdin_batch_manifest_end_to_end(stub_service, seeded, postgres_url, tmp_path, monkeypatch):
    """--stdin batch: manifest kind/outcomes + per-job poll against the stub."""
    manifest_dir = tmp_path / "manifests"
    monkeypatch.setenv("SOW_BATCH_MANIFEST_DIR", str(manifest_dir))
    config_path = _config(tmp_path, postgres_url, stub_service)

    result = runner.invoke(
        app,
        ["lyrics", "align", "--stdin", "--force", "--config", str(config_path)],
        input="smoke_song\n",
    )

    assert result.exit_code == 0, result.output
    assert "Completed: 1" in result.output

    manifests = list(manifest_dir.glob("*_align_manifest.json"))
    assert len(manifests) == 1
    manifest = json.loads(manifests[0].read_text())
    assert manifest["kind"] == "lyrics_align"
    assert manifest["jobs"][0]["outcome"] == "completed"
    assert manifest["jobs"][0]["lrc_source"] == "forced_alignment"
    assert "finished_at" in manifest
