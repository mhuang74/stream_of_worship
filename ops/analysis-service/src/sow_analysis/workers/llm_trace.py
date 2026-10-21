"""Per-request LLM/ASR debug trace recorder for ``--dump-llm-trace``.

Writes a JSON trace (agent-readable) and a static HTML render (human-readable)
of every LLM/ASR step performed by an LRC job into ``SOW_DEBUG_DUMP_DIR``.
Every append/set rewrites the whole document atomically (tmp + os.replace) so
the file stays valid after any intermediate failure or crash. All write
failures degrade to a log warning - a dump problem must never affect the job.
"""

from __future__ import annotations

import html
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_HTML_STYLE = (
    "<style>"
    "body{font-family:system-ui,sans-serif;margin:1em}"
    "pre{font-family:ui-monospace,monospace;white-space:pre-wrap}"
    "details{margin:0.5em 0}"
    "summary{cursor:pointer;font-weight:bold}"
    "table{border-collapse:collapse}"
    "td,th{border:1px solid #ccc;padding:2px 8px;text-align:left;vertical-align:top}"
    "</style>"
)


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _json_default(value: Any) -> str:
    return str(value)


def _atomic_write(path: Path | None, payload: str) -> None:
    if path is None:
        raise OSError("trace path unavailable")
    tmp_path = path.with_name(path.name + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(payload)
    os.replace(tmp_path, path)


def _trace_base_name(job_id: str, context: dict) -> str:
    timestamp = _utc_now()
    song_id = str(context.get("song_id") or "")
    if not song_id:
        content_hash = str(context.get("content_hash") or "")
        song_id = content_hash[:8]
    return f"{timestamp}_{job_id}_{song_id}"


def _scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _is_primitive(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def _format_value(value: Any) -> str:
    if _is_primitive(value):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=_json_default)
    except (TypeError, ValueError):
        return str(value)


class LLMTrace:
    """Step-by-step debug trace of an LRC job, flushed per step to disk."""

    def __init__(self, base_dir: Path, job_id: str, context: dict) -> None:
        self.enabled = True  # kept so call sites can stay uniform with trace=None
        self._broken = False
        self._finished = False
        self._json_path: Path | None = None
        self._html_path: Path | None = None

        try:
            doc_context: dict = dict(context)
        except (TypeError, ValueError) as e:
            self._broken = True
            LLMTrace._warn("invalid trace context: %s", e)
            doc_context = {}

        try:
            self._base_name = _trace_base_name(job_id, doc_context)
        except (TypeError, ValueError) as e:
            self._broken = True
            LLMTrace._warn("failed to build trace file name: %s", e)
            self._base_name = "invalid"

        self._doc: dict = {"job_context": doc_context, "events": []}

        if self._broken:
            return
        try:
            base_dir = Path(base_dir)
            base_dir.mkdir(parents=True, exist_ok=True)
            self._json_path = base_dir / f"{self._base_name}.json"
            self._html_path = base_dir / f"{self._base_name}.html"
            self._flush()
        except OSError as e:
            self._broken = True
            LLMTrace._warn("cannot create trace files in %s: %s", base_dir, e)

    @staticmethod
    def _warn(message: str, *args: Any) -> None:
        logger.warning("LLMTrace: " + message, *args)

    @property
    def base_name(self) -> str:
        """File name base (without extension) chosen in the constructor."""
        return self._base_name

    def append(self, step: str, data: dict) -> None:
        """Add an event ``{"step", "at", **data}`` and flush immediately."""
        if self._broken or self._finished:
            return
        try:
            event: dict = {"step": step, "at": _utc_now()}
            event.update(data)
            self._doc["events"].append(event)
            self._flush()
        except (OSError, TypeError, ValueError) as e:
            self._broken = True
            LLMTrace._warn("trace append failed (step %r): %s", step, e)

    def set(self, key: str, value: Any) -> None:
        """Set a top-level document key (e.g. ``final_lrc``) and flush."""
        if self._broken or self._finished:
            return
        try:
            self._doc[key] = value
            self._flush()
        except (OSError, TypeError, ValueError) as e:
            self._broken = True
            LLMTrace._warn("trace set failed (key %r): %s", key, e)

    def finish(self, error: str | None = None) -> None:
        """Record the terminal error (if any), rewrite both files, idempotent."""
        if self._broken or self._finished:
            return
        self._finished = True
        if error:
            self._doc["events"].append({"step": "error", "at": _utc_now(), "error": error})
        self._flush()

    def _flush(self) -> None:
        """Rewrite the JSON doc and the HTML render so both stay current."""
        if self._broken:
            return
        payload = json.dumps(self._doc, ensure_ascii=False, indent=2, default=_json_default)
        _atomic_write(self._json_path, payload)
        try:
            _atomic_write(self._html_path, render_html(self._doc))
        except OSError as e:
            LLMTrace._warn("trace HTML write failed: %s", e)


def _table_rows(items: list) -> str | None:
    """Render a list as an HTML table, or None when items are unsuitable."""
    if not items or not all(_is_primitive(i) or isinstance(i, dict) for i in items):
        return None
    normalized: list = []
    for item in items:
        if isinstance(item, dict):
            normalized.append({str(k): _scalar(v) for k, v in item.items()})
        else:
            normalized.append({"value": item})
    if all(len(r) == 1 and "value" in r for r in normalized):
        entries = "".join(f"<li>{html.escape(str(r['value']))}</li>" for r in normalized)
        return f"<ul>{entries}</ul>"
    headers: list = []
    for row in normalized:
        for key in row:
            if key not in headers:
                headers.append(key)
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body_rows = []
    for row in normalized:
        cells = "".join(f"<td>{html.escape(_format_value(row.get(h)))}</td>" for h in headers)
        body_rows.append(f"<tr>{cells}</tr>")
    body = "".join(body_rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _context_table(context: dict) -> str:
    rows = []
    for key, value in context.items():
        if key == "options":
            rendered = _format_value(value)
            rows.append(
                f"<tr><th>{html.escape(str(key))}</th>"
                f"<td><pre>{html.escape(rendered)}</pre></td></tr>"
            )
            continue
        rows.append(
            f"<tr><th>{html.escape(str(key))}</th>"
            f"<td>{html.escape(_format_value(value))}</td></tr>"
        )
    return "<table>" + "".join(rows) + "</table>"


def _event_body(event: dict) -> str:
    parts: list = []
    for key, value in event.items():
        label = html.escape(str(key))
        if isinstance(value, str) and "\n" in value:
            parts.append(f"<h4>{label}</h4><pre>{html.escape(value)}</pre>")
        elif isinstance(value, list):
            table = _table_rows(value)
            if table is not None:
                parts.append(f"<h4>{label}</h4>{table}")
            else:
                rendered = "<pre>" + html.escape(_format_value(value)) + "</pre>"
                parts.append(f"<details><summary>{label}</summary>{rendered}</details>")
        elif isinstance(value, dict):
            rendered = "<pre>" + html.escape(_format_value(value)) + "</pre>"
            parts.append(f"<details><summary>{label}</summary>{rendered}</details>")
        else:
            parts.append(f"<p><b>{label}</b>: {html.escape(str(value))}</p>")
    return "".join(parts)


def render_html(doc: dict) -> str:
    """Render a trace document as self-contained static HTML (no JavaScript)."""
    context = doc.get("job_context") or {}
    events = doc.get("events") or []
    error_step = None
    for event in reversed(events):
        if event.get("step") == "error":
            error_step = event
            break

    error_section = ""
    if error_step:
        message = error_step.get("error") or error_step.get("message") or "unknown error"
        error_section = f'<div style="color:red">Error: {html.escape(str(message))}</div><hr/>'

    event_details = []
    for event in events:
        title = f"{event.get('step')} @ {event.get('at')}"
        event_details.append(
            f"<details><summary>{html.escape(title)}</summary>{_event_body(event)}</details>"
        )
    events_section = "".join(event_details)

    final_lrc = doc.get("final_lrc")
    final_section = ""
    if final_lrc:
        final_section = f"<h3>Final LRC</h3><pre>{html.escape(str(final_lrc))}</pre>"

    context_section = (
        "<details open><summary>Job Context</summary>"
        + _context_table(context)
        + "</details>"
    )

    embedded = json.dumps(doc, ensure_ascii=False, indent=2, default=_json_default)
    embedded = embedded.replace("<", "\\u003c")

    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        + "<title>LLM trace</title>"
        + _HTML_STYLE
        + "</head><body>"
        + error_section
        + context_section
        + events_section
        + final_section
        + '<script type="application/json" id="llm-trace-json">'
        + embedded
        + "</script></body></html>"
    )