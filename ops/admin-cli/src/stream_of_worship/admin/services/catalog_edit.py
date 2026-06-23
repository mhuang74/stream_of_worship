"""Helpers for curated catalog insert and edit flows."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import tomli_w
import tomllib
from pypinyin import lazy_pinyin

from stream_of_worship.admin.db.models import Song

REQUIRED_REVIEW_FIELDS = ("title", "source_url")
EDITABLE_REVIEW_FIELDS = (
    "title",
    "composer",
    "lyricist",
    "album_name",
    "album_series",
    "musical_key",
    "source_url",
    "lyrics_raw",
)


@dataclass
class ReviewedSongData:
    """Normalized song review payload."""

    title: str
    source_url: str
    composer: str | None = None
    lyricist: str | None = None
    album_name: str | None = None
    album_series: str | None = None
    musical_key: str | None = None
    lyrics_raw: str | None = None

    def to_editor_dict(self) -> dict[str, str]:
        """Render editable fields for the review document."""
        return {
            "title": self.title,
            "composer": self.composer or "",
            "lyricist": self.lyricist or "",
            "album_name": self.album_name or "",
            "album_series": self.album_series or "",
            "musical_key": self.musical_key or "",
            "source_url": self.source_url,
            "lyrics_raw": self.lyrics_raw or "",
        }


def compute_song_id(title: str, composer: str | None, lyricist: str | None) -> str:
    """Compute the stable content-derived song ID used by the catalog scraper."""

    def _norm(value: str | None) -> str:
        return unicodedata.normalize("NFKC", (value or "").strip())

    slug = re.sub(r"[^a-z0-9_]", "", "_".join(lazy_pinyin(_norm(title))).lower())
    payload = f"{_norm(title)}|{_norm(composer)}|{_norm(lyricist)}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
    song_id = f"{slug}_{digest}"
    if len(song_id) > 100:
        song_id = f"{slug[:91]}_{digest}"
    return song_id


def normalize_reviewed_data(raw_data: dict[str, Any]) -> ReviewedSongData:
    """Normalize and validate reviewed editor data."""

    normalized: dict[str, str | None] = {}
    for field_name in EDITABLE_REVIEW_FIELDS:
        value = raw_data.get(field_name, "")
        if value is None:
            cleaned = None
        else:
            cleaned_text = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
            cleaned = cleaned_text or None
        normalized[field_name] = cleaned

    missing = [field for field in REQUIRED_REVIEW_FIELDS if not normalized.get(field)]
    if missing:
        raise ValueError(f"Missing required field(s): {', '.join(missing)}")

    return ReviewedSongData(
        title=normalized["title"] or "",
        source_url=normalized["source_url"] or "",
        composer=normalized["composer"],
        lyricist=normalized["lyricist"],
        album_name=normalized["album_name"],
        album_series=normalized["album_series"],
        musical_key=normalized["musical_key"],
        lyrics_raw=_normalize_lyrics_raw(normalized["lyrics_raw"]),
    )


def _normalize_lyrics_raw(lyrics_raw: str | None) -> str | None:
    """Normalize lyrics text to the existing catalog convention."""
    if lyrics_raw is None:
        return None

    lines = []
    for raw_line in lyrics_raw.split("\n"):
        line = re.sub(r"\s+", " ", raw_line.strip())
        if line:
            lines.append(line)

    if not lines:
        return None
    return "\n".join(lines)


def build_lyrics_payload(lyrics_raw: str | None) -> tuple[str | None, str | None, str | None]:
    """Convert raw lyrics into `lyrics_raw`, `lyrics_lines`, and section JSON."""
    normalized = _normalize_lyrics_raw(lyrics_raw)
    if not normalized:
        return None, None, None

    lyrics_lines = normalized.split("\n")
    sections = [
        {
            "section_type": "unknown",
            "section_number": 1,
            "lines": lyrics_lines,
        }
    ]
    return (
        normalized,
        json.dumps(lyrics_lines, ensure_ascii=False),
        json.dumps(sections, ensure_ascii=False),
    )


def build_song_from_review(
    reviewed: ReviewedSongData,
    *,
    song_id: str | None = None,
    existing_song_id: str | None = None,
    created_at: str | None = None,
    scraped_at: str | None = None,
) -> Song:
    """Build a `Song` model from reviewed metadata."""
    final_song_id = existing_song_id or song_id or compute_song_id(
        reviewed.title, reviewed.composer, reviewed.lyricist
    )
    now_iso = datetime.now().isoformat()
    lyrics_raw, lyrics_lines, sections = build_lyrics_payload(reviewed.lyrics_raw)

    return Song(
        id=final_song_id,
        title=reviewed.title,
        title_pinyin="_".join(lazy_pinyin(reviewed.title)),
        composer=reviewed.composer,
        lyricist=reviewed.lyricist,
        album_name=reviewed.album_name,
        album_series=reviewed.album_series,
        musical_key=reviewed.musical_key,
        lyrics_raw=lyrics_raw,
        lyrics_lines=lyrics_lines,
        sections=sections,
        source_url=reviewed.source_url,
        table_row_number=None,
        scraped_at=scraped_at or now_iso,
        created_at=created_at,
        updated_at=now_iso,
    )


def render_review_document(
    initial_data: dict[str, Any],
    *,
    comments: list[str] | None = None,
) -> str:
    """Render editable TOML for manual review."""
    payload = {field: initial_data.get(field, "") or "" for field in EDITABLE_REVIEW_FIELDS}
    lines = []
    for comment in comments or []:
        lines.append(f"# {comment}")
    if lines:
        lines.append("")
    lines.append(tomli_w.dumps(payload).strip())
    lines.append("")
    return "\n".join(lines)


def parse_review_document(document_text: str) -> dict[str, Any]:
    """Parse reviewed TOML back into a dictionary."""
    return tomllib.loads(document_text)


def review_document_in_editor(initial_text: str) -> str:
    """Open the review document in `$EDITOR` and return the edited content."""
    editor = os.environ.get("EDITOR") or shutil.which("nano") or shutil.which("vi")
    if not editor:
        raise RuntimeError("No editor configured. Set $EDITOR to review catalog metadata.")

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".toml",
        prefix="sow-catalog-review-",
        delete=False,
        encoding="utf-8",
    ) as handle:
        temp_path = Path(handle.name)
        handle.write(initial_text)

    try:
        result = subprocess.run([editor, str(temp_path)], check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Editor exited with status {result.returncode}")
        return temp_path.read_text(encoding="utf-8")
    finally:
        temp_path.unlink(missing_ok=True)


def build_song_diff(before: Song, after: Song) -> str:
    """Build a compact diff for reviewed song changes."""
    before_text = json.dumps(before.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    after_text = json.dumps(after.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    diff_lines = difflib.unified_diff(
        before_text.splitlines(),
        after_text.splitlines(),
        fromfile="before",
        tofile="after",
        lineterm="",
    )
    return "\n".join(diff_lines)
