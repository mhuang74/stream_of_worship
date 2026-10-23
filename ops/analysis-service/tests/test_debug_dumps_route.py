"""HTTP index for LLM trace dump reports (``/debug-dumps``)."""

import os

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from sow_analysis.config import settings
from sow_analysis.routes import debug_dumps


@pytest.fixture
def dump_client(tmp_path, monkeypatch):
    """FastAPI app with the index router mounted before StaticFiles, as main.py does."""
    monkeypatch.setattr(settings, "SOW_DEBUG_DUMP_DIR", tmp_path)
    app = FastAPI()
    app.include_router(debug_dumps.router, prefix="/debug-dumps")
    app.mount(
        "/debug-dumps",
        StaticFiles(directory=str(tmp_path), html=False, check_dir=False),
        name="debug-dumps",
    )
    return TestClient(app)


def _write(path, body: str, mtime: float) -> None:
    path.write_text(body, encoding="utf-8")
    os.utime(path, (mtime, mtime))


class TestDumpIndex:
    """Index renders newest-first and links resolve through StaticFiles."""

    def test_index_lists_html_reports_newest_first(self, dump_client, tmp_path):
        _write(tmp_path / "older.html", "<html>old</html>", 1_700_000_000)
        _write(tmp_path / "newer.html", "<html>new</html>", 1_700_000_500)
        (tmp_path / "newer.json").write_text("{}", encoding="utf-8")

        for url in ("/debug-dumps/", "/debug-dumps"):
            response = dump_client.get(url)
            assert response.status_code == 200
            body = response.text
            assert body.index('href="newer.html"') < body.index('href="older.html"')
            assert "newer.json" not in body
            assert "2023-11-14 22:21:40 UTC" in body
            assert "2023-11-14 22:13:20 UTC" in body

    def test_report_file_is_served(self, dump_client, tmp_path):
        _write(tmp_path / "newer.html", "<html>report-body</html>", 1_700_000_000)
        response = dump_client.get("/debug-dumps/newer.html")
        assert response.status_code == 200
        assert "report-body" in response.text

    def test_missing_and_traversal_are_404(self, dump_client):
        assert dump_client.get("/debug-dumps/missing.html").status_code == 404
        traversal = dump_client.get("/debug-dumps/%2e%2e/%2e%2e/etc/passwd")
        assert traversal.status_code == 404

    def test_empty_directory_index(self, dump_client):
        response = dump_client.get("/debug-dumps/")
        assert response.status_code == 200
        assert "No LLM trace dumps yet." in response.text

    def test_filename_is_escaped_and_link_quoted(self, dump_client, tmp_path):
        _write(tmp_path / "<script>.html", "<html>x</html>", 1_700_000_000)
        body = dump_client.get("/debug-dumps/").text
        assert "&lt;script&gt;" in body
        assert "<script>" not in body
        assert 'href="%3Cscript%3E.html"' in body
        # The quoted link actually resolves.
        assert dump_client.get("/debug-dumps/%3Cscript%3E.html").status_code == 200


class TestReportUrlResolvesEndToEnd:
    """A trace's own report_url must resolve through the mount, whatever the song_id."""

    def test_song_id_with_special_characters(self, dump_client, tmp_path, monkeypatch):
        from sow_analysis.config import settings
        from sow_analysis.workers.llm_trace import LLMTrace

        monkeypatch.setattr(settings, "SOW_DEBUG_DUMP_PUBLIC_BASE_URL", "http://testserver")
        trace = LLMTrace(tmp_path, "job_url", {"song_id": "a b%#&敬拜"})
        trace.append("probe", {"k": "v"})
        trace.finish()

        assert trace.report_url is not None
        path = trace.report_url.removeprefix("http://testserver")
        assert " " not in path and "%25" in path and "%23" in path and "%26" in path

        response = dump_client.get(path)
        assert response.status_code == 200
        assert "probe" in response.text
