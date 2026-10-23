"""HTTP listing for LLM/ASR debug dump reports (SOW_DEBUG_DUMP_DIR).

The files themselves are served by the StaticFiles mount added in ``main.py``; this
router only serves the newest-first index at the mount root, because StaticFiles
has no directory listing.
"""

import html
import logging
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from ..config import settings
from ..workers.llm_trace import HTML_STYLE

logger = logging.getLogger(__name__)
router = APIRouter()


def list_dump_reports(directory: Path) -> list[Path]:
    """HTML reports in ``directory``, newest first (mtime, then name). Missing dir -> []."""
    try:
        candidates = [p for p in directory.glob("*.html") if p.is_file()]
    except OSError as e:
        logger.warning("Cannot list LLM trace dumps in %s: %s", directory, e)
        return []
    stamped: list[tuple[float, Path]] = []
    for path in candidates:
        try:
            stamped.append((path.stat().st_mtime, path))
        except OSError:
            continue
    stamped.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    return [path for _, path in stamped]


def render_index(paths: list[Path]) -> str:
    """Render the dump index page (static HTML, no JavaScript)."""
    if not paths:
        body = "<p>No LLM trace dumps yet.</p>"
    else:
        items = []
        for path in paths:
            try:
                stamp = datetime.fromtimestamp(
                    path.stat().st_mtime, tz=UTC
                ).strftime("%Y-%m-%d %H:%M:%S UTC")
            except OSError:
                stamp = "(missing mtime)"
            items.append(
                f'<li><a href="{html.escape(quote(path.name, safe=""), quote=True)}">'
                f"{html.escape(path.name)}</a>"
                f' <span style="color:#888">{stamp}</span></li>'
            )
        body = f"<p>{len(paths)} report(s), newest first</p><ul>" + "".join(items) + "</ul>"
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>LLM trace dumps</title>"
        + HTML_STYLE
        + "</head><body><h1>LLM trace dumps</h1>"
        + body
        + "</body></html>"
    )


@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dump_index() -> HTMLResponse:
    """List dump reports in the debug dump directory, newest first."""
    reports = list_dump_reports(settings.SOW_DEBUG_DUMP_DIR)
    return HTMLResponse(render_index(reports), headers={"Cache-Control": "no-store"})
