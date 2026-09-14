#!/usr/bin/env python3
"""Shared helpers for the eval-models-for-fixing-youtube-transcription skill.

Constraints (verified 2026-09-14):
- The admin venv CANNOT import ``sow_analysis`` (workers/__init__ pulls
  aiosqlite transitively). This module therefore performs ZERO top-level
  ``sow_analysis`` imports; production imports happen lazily inside the
  analysis-env scripts that need them, after ``bootstrap_analysis_src()``.
- ``LRCLine`` has ``format()``, NOT ``__str__`` — all LRC serialization uses
  ``lrc_text()`` / ``line.format()``.
"""

from __future__ import annotations

import json
import re
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
ANALYSIS_SRC = PROJECT_ROOT / "ops" / "analysis-service" / "src"
ADMIN_SRC = PROJECT_ROOT / "ops" / "admin-cli" / "src"

SKILL_DIR = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = PROJECT_ROOT / "output" / "eval-models-for-fixing-youtube-transcription"

TAG_LINE_RE = re.compile(r"^\[.+\]$")


class EvalError(Exception):
    """Fatal skill error with a user-facing message."""


class FixtureError(EvalError):
    """Fixture validation failure."""


class JudgeParseError(EvalError):
    """Judge LLM output could not be parsed as JSON."""


def bootstrap_analysis_src() -> None:
    """Make ``sow_analysis`` importable (analysis env). Idempotent."""
    p = str(ANALYSIS_SRC)
    if p not in sys.path:
        sys.path.insert(0, p)


def bootstrap_admin_src() -> None:
    """Make ``stream_of_worship.admin`` importable (admin env). Idempotent."""
    p = str(ADMIN_SRC)
    if p not in sys.path:
        sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# Fixtures


def load_fixture(path: Path | str) -> list[dict]:
    """Load and validate a fixture JSON file.

    Each entry must have: non-empty ``song_id``, ``youtube_url``,
    ``duration_seconds`` (> 0), ``language`` (``zh``|``en``), ``lyrics``
    (non-empty list of strings), ``title``. ``lyrics_source`` optional
    (``structured``|``raw``|absent). Unknown keys warn (not error).
    """
    path = Path(path)
    if not path.is_file():
        raise FixtureError(f"Fixture file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise FixtureError(f"Fixture is not valid JSON: {path}: {e}") from e
    if not isinstance(data, list) or not data:
        raise FixtureError(f"Fixture must be a non-empty JSON array: {path}")

    known = {
        "song_id",
        "title",
        "language",
        "youtube_url",
        "duration_seconds",
        "lyrics",
        "lyrics_source",
    }
    entries: list[dict] = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise FixtureError(f"Entry {i}: must be a JSON object")
        for field in ("song_id", "title", "youtube_url"):
            if not entry.get(field) or not str(entry[field]).strip():
                raise FixtureError(f"Entry {i}: missing/empty required field '{field}'")
        duration = entry.get("duration_seconds")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or duration <= 0:
            raise FixtureError(f"Entry {i} ({entry['song_id']}): 'duration_seconds' must be > 0")
        if entry.get("language") not in ("zh", "en"):
            raise FixtureError(f"Entry {i} ({entry['song_id']}): 'language' must be 'zh' or 'en'")
        lyrics = entry.get("lyrics")
        if (
            not isinstance(lyrics, list)
            or not lyrics
            or not all(isinstance(x, str) and x.strip() for x in lyrics)
        ):
            raise FixtureError(
                f"Entry {i} ({entry['song_id']}): 'lyrics' must be a non-empty list of strings"
            )
        source = entry.get("lyrics_source")
        if source is not None and source not in ("structured", "raw"):
            raise FixtureError(
                f"Entry {i} ({entry['song_id']}): 'lyrics_source' must be 'structured' or 'raw'"
            )
        unknown = sorted(set(entry) - known)
        if unknown:
            print(
                f"WARNING: entry {i} ({entry['song_id']}): unknown keys ignored: {', '.join(unknown)}",
                file=sys.stderr,
            )
        entries.append(entry)
    return entries


def resolve_lyrics_lines(fixture_entry: dict) -> list[str]:
    """Official lyrics for the correction prompt — full list INCLUDING tag lines.

    Fixtures never re-parse; the stored list is production-prompt-faithful.
    """
    return list(fixture_entry["lyrics"])


def judge_official_lines(fixture_entry: dict) -> list[str]:
    """Official lyrics for judging/mechanical checks — tag lines excluded.

    A line matching ``^\\[.+\\]$`` (after strip) is a section tag, not a sung
    phrase. Used ONLY by judge/mechanical checks.
    """
    return [ln for ln in fixture_entry["lyrics"] if not TAG_LINE_RE.match(ln.strip())]


# ---------------------------------------------------------------------------
# Model IDs


def slugify(model_id: str) -> str:
    """Artifact-safe slug: ``/`` and ``:`` become ``-``."""
    return model_id.replace("/", "-").replace(":", "-")


def load_model_ids(spec: str) -> list[str]:
    """Resolve a model spec: comma-separated IDs or a file path (leading ``@``
    stripped) with one ID per line, ``#`` comments and blank lines skipped.

    Deduplicates preserving order; empty → ``EvalError``; two distinct IDs
    mapping to the same slug → ``EvalError`` (prevents silent artifact
    overwrites).
    """
    spec = spec.strip()
    if spec.startswith("@"):
        spec = spec[1:].strip()
    ids: list[str] = []
    if Path(spec).is_file():
        for raw in Path(spec).read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            ids.append(line)
    else:
        ids = [part.strip() for part in spec.split(",") if part.strip()]

    deduped: list[str] = []
    seen: set[str] = set()
    for mid in ids:
        if mid not in seen:
            seen.add(mid)
            deduped.append(mid)
    if not deduped:
        raise EvalError(
            f"No model IDs resolved from spec: {spec!r} (comma-separated list or file path with one ID per line)"
        )

    slug_map: dict[str, str] = {}
    for mid in deduped:
        slug = slugify(mid)
        owner = slug_map.get(slug)
        if owner is not None and owner != mid:
            raise EvalError(
                f"Slug collision: {owner!r} and {mid!r} both map to slug {slug!r}; "
                "artifact files would silently overwrite each other. Rename one model ID."
            )
        slug_map[slug] = mid
    return deduped


# ---------------------------------------------------------------------------
# Transcript cache


def transcript_cache_path(transcripts_dir: Path | str, video_id: str, language: str) -> Path:
    """Language-safe cache path: ``<video_id>__<language>.json``.

    Keyed by the REQUESTED language so a later run with a different
    ``--language`` can never hit a wrong-language cache.
    """
    return Path(transcripts_dir) / f"{video_id}__{language}.json"


def save_transcript(
    path: Path | str,
    video_id: str,
    language: str,
    fetched_language_code: str,
    snippets: list,
) -> None:
    """Write the transcript cache record."""
    record = {
        "video_id": video_id,
        "language": language,
        "fetched_language_code": fetched_language_code,
        "snippets": [
            {"start": float(s.start), "duration": float(s.duration), "text": str(s.text)}
            for s in snippets
        ],
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


def load_transcript(path: Path | str) -> dict:
    """Load a transcript cache record (full dict)."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def snippets_to_namespace(snippets: list[dict]) -> list:
    """Wrap cached snippet dicts in SimpleNamespace so production
    ``_format_transcript_text`` consumes them unchanged (reads .start/.text;
    .duration cached for completeness)."""
    return [
        types.SimpleNamespace(start=s["start"], duration=s["duration"], text=s["text"])
        for s in snippets
    ]


# ---------------------------------------------------------------------------
# LRC serialization


def lrc_text(lines) -> str:
    """Serialize LRCLine objects via production ``format()`` (NO ``str()`` —
    that yields the dataclass repr)."""
    return "\n".join(line.format() for line in lines)


# ---------------------------------------------------------------------------
# JSONL


def write_jsonl(path: Path | str, rows: list[dict], *, append: bool = False) -> None:
    """Write rows as JSONL. Caller controls append vs truncate."""
    path = Path(path)
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path | str) -> list[dict]:
    path = Path(path)
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------------------
# Judge JSON parsing


def parse_judge_json(text: str) -> dict:
    """Parse the judge's strict-JSON output. Tries the whole text first, then
    the first fenced ```json block. Raises ``JudgeParseError``."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    raise JudgeParseError(
        "Judge output is not valid JSON (tried whole text and fenced ```json block)"
    )


def load_skill_env() -> None:
    """Load SOW_LLM_* / SOW_YOUTUBE_PROXY variables into os.environ.

    Precedence: already-exported os.environ wins; then
    ``<skill>/fixtures/.env`` (per-run override, never commit values) wins
    per-key over the host default ``/opt/sow/.env``. Values are never echoed.
    """
    import os

    keys = ("SOW_LLM_API_KEY", "SOW_LLM_BASE_URL", "SOW_LLM_MODEL", "SOW_YOUTUBE_PROXY")
    # Earlier files win per-key; already-exported os.environ wins over all files.
    for env_file in (SKILL_DIR / "fixtures" / ".env", Path("/opt/sow/.env")):
        if not env_file.is_file():
            continue
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name = name.strip()
            value = value.strip().strip('"').strip("'")
            if name in keys and name not in os.environ and value:
                os.environ[name] = value


def resolve_model_ids(spec: str | None) -> list[str]:
    """Resolve candidate model IDs: ``--models`` spec wins; else
    ``SOW_TRANSCRIPT_LLM_MODELS`` env var (comma-separated list or ``@file``
    path); else EvalError telling the caller to ask the user.
    """
    spec = (spec or "").strip()
    if not spec:
        import os

        spec = os.environ.get("SOW_TRANSCRIPT_LLM_MODELS", "").strip()
        if not spec:
            raise EvalError(
                "No candidate models: pass --models, or export "
                "SOW_TRANSCRIPT_LLM_MODELS (comma-separated IDs, or a path to a "
                "file with one ID per line — leading @ accepted, # comments allowed)."
            )
    return load_model_ids(spec)


# ---------------------------------------------------------------------------
# Misc


def require_env(names: list[str]) -> None:
    """Exit 1 printing the MISSING var NAMES only (never values)."""
    import os

    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        print(
            "ERROR: missing required environment variables (values are never echoed):",
            file=sys.stderr,
        )
        for n in missing:
            print(f"  export {n}=...", file=sys.stderr)
        sys.exit(1)


def now_run_id() -> str:
    """``YYYYMMDD-HHMMSS`` UTC."""
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
