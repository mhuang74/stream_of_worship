"""Unit tests for the LLMTrace debug dump recorder."""

import json
import re
from pathlib import Path

import pytest

from sow_analysis.workers.llm_trace import LLMTrace, render_html


class TestLLMTraceAppend:
    """append() must flush a valid JSON document per step."""

    def test_append_writes_json_immediately(self, tmp_path):
        trace = LLMTrace(
            tmp_path,
            "job_test123",
            {"job_id": "job_test123", "song_id": "s1", "content_hash": "ab" * 32},
        )
        trace.append("youtube_transcript", {"video_id": "vid"})

        json_path = tmp_path / f"{trace.base_name}.json"
        assert json_path.exists()
        doc = json.loads(json_path.read_text(encoding="utf-8"))
        assert doc["job_context"]["song_id"] == "s1"
        assert doc["job_context"]["job_id"] == "job_test123"
        assert len(doc["events"]) == 1
        event = doc["events"][0]
        assert event["step"] == "youtube_transcript"
        assert event["video_id"] == "vid"
        assert re.fullmatch(r"\d{8}T\d{6}Z", event["at"])

    def test_two_appends_keep_both_events(self, tmp_path):
        trace = LLMTrace(tmp_path, "job_test123", {"song_id": "s1"})
        trace.append("first", {})
        trace.append("second", {"k": "v"})

        doc = json.loads((tmp_path / f"{trace.base_name}.json").read_text(encoding="utf-8"))
        assert [e["step"] for e in doc["events"]] == ["first", "second"]

    def test_base_name_uses_hash8_without_song_id(self, tmp_path):
        trace = LLMTrace(tmp_path, "job_test123", {"content_hash": "abc12345" + "0" * 56})
        assert trace.base_name.endswith("job_test123_abc12345")


class TestLLMTraceFinish:
    """finish() must write the HTML render; error path appends an error event."""

    def test_finish_writes_html_with_embedded_json(self, tmp_path):
        trace = LLMTrace(tmp_path, "job_test123", {"song_id": "s1"})
        trace.append("youtube_transcript", {"transcript_text": "hello\nworld"})
        trace.set("final_lrc", "[00:01.00] test")
        trace.finish()

        html_path = tmp_path / f"{trace.base_name}.html"
        assert html_path.exists()
        html_text = html_path.read_text(encoding="utf-8")
        assert "<details" in html_text
        assert "Final LRC" in html_text

        match = re.search(
            r'<script type="application/json" id="llm-trace-json">(.*?)</script>',
            html_text,
            re.DOTALL,
        )
        assert match is not None
        embedded = match.group(1).replace("\\u003c", "<")
        doc = json.loads(embedded)
        assert doc["final_lrc"] == "[00:01.00] test"
        assert doc["events"][0]["step"] == "youtube_transcript"

    def test_finish_with_error_appends_error_event(self, tmp_path):
        trace = LLMTrace(tmp_path, "job_test123", {"song_id": "s1"})
        trace.append("job_started", {})
        trace.finish(error="boom")

        doc = json.loads((tmp_path / f"{trace.base_name}.json").read_text(encoding="utf-8"))
        assert doc["events"][-1]["step"] == "error"
        assert doc["events"][-1]["error"] == "boom"

    def test_second_finish_is_noop(self, tmp_path):
        trace = LLMTrace(tmp_path, "job_test123", {"song_id": "s1"})
        trace.finish()
        first_html = (tmp_path / f"{trace.base_name}.html").read_text(encoding="utf-8")
        trace.finish(error="late error")  # must not add anything
        second_html = (tmp_path / f"{trace.base_name}.html").read_text(encoding="utf-8")
        assert first_html == second_html

    def test_broken_base_dir_never_raises(self):
        # A path whose parent is a regular file cannot be created.
        import tempfile

        with tempfile.NamedTemporaryFile() as fh:
            bad_dir = Path(fh.name) / "subdir"
            trace = LLMTrace(bad_dir, "job_test123", {"song_id": "s1"})
            assert trace._broken is True
            trace.append("step", {})  # must be a silent no-op
            trace.set("final_lrc", "x")
            trace.finish(error="x")  # must be a silent no-op

    def test_appends_after_finish_are_noop(self, tmp_path):
        trace = LLMTrace(tmp_path, "job_test123", {"song_id": "s1"})
        trace.append("before", {})
        trace.finish()
        before = (tmp_path / f"{trace.base_name}.json").read_text(encoding="utf-8")
        trace.append("after", {})
        after = (tmp_path / f"{trace.base_name}.json").read_text(encoding="utf-8")
        assert before == after


class TestRenderHtml:
    """HTML renderer contract."""

    def test_error_banner_present(self):
        doc = {"job_context": {}, "events": [{"step": "error", "error": "badness"}]}
        html_text = render_html(doc)
        assert "Error: badness" in html_text

    def test_script_injection_escaped(self):
        doc = {"job_context": {}, "events": [{"step": "s", "prompt": "</script>"}]}
        html_text = render_html(doc)
        match = re.search(
            r'<script type="application/json" id="llm-trace-json">(.*?)</script>',
            html_text,
            re.DOTALL,
        )
        assert match is not None
        # The embedded payload must contain no raw '<' that could break out of
        # the script element (escaped as literal \u003c text).
        assert "</" not in match.group(1)
        payload = match.group(1).replace("\\u003c", "<")
        assert json.loads(payload)["events"][0]["prompt"] == "</script>"

    def test_event_details_rendered(self):
        doc = {
            "job_context": {},
            "events": [{"step": "youtube_transcript", "at": "ts", "video_id": "vid"}],
        }
        html_text = render_html(doc)
        assert "youtube_transcript @ ts" in html_text
        assert "vid" in html_text