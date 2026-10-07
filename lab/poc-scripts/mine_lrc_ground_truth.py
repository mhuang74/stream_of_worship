#!/usr/bin/env python3
"""Phase 0b: mine real before/after LRC ground truth from R2 backup ladders.

Implements Phase 0b of ``specs/lrc-review-triage-cascade-design-v2.md`` (issue
#243). Pairs each R2 ``lyrics.backup.{ts}.lrc`` ladder entry with its successor
(the next ladder entry, or the current ``lyrics.lrc``) ONLY when the successor
is a human-corrected LRC — ``recordings.lrc_source ∈ {manual_upload, llm_edit}``
for the current successor. Never diffs two machine outputs.

Pairs are ladder-ordered (each backup matched to its immediate successor) and
cross-checked against the editor-session ladder
(``{hash_prefix}/backups/lyrics.{ts}.lrc``, written by ``upload_r2_backup``):
when the newest official backup's content hash equals the newest editor-session
backup's hash, the official backup was made by the editor session that uploaded
the current content — proving immediacy (no lost intermediate version between
the backup and the human fix).

Also mines the manual_upload seed negative (``bu_ting_zan_mei_mi_e937a9d3``,
the Appendix A anomaly): its feedback timeline vs its ladder writes, and what
the human fix actually changed.

Read-only: zero writes to canonical Lyrics, catalog status, provenance, or
visibility. The only artifacts are files under ``eval/lrc_truth/``.

Usage:
    uv run --project ops/admin-cli --extra admin \
        python lab/poc-scripts/mine_lrc_ground_truth.py \
        [--output-dir eval/lrc_truth] [--config <path>]
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.services.r2 import R2Client
from stream_of_worship.db.connection import ConnectionProvider

DEFAULT_OUTPUT_DIR = Path("eval/lrc_truth")

# Successor provenance that makes a backup→successor pair mineable ground truth:
# a human-corrected fix. Never pair machine→machine.
HUMAN_SOURCES = {"manual_upload", "llm_edit"}

# Anomaly song from spec Appendix A: manual_upload seed negative, "inspect".
ANOMALY_SONG_ID = "bu_ting_zan_mei_mi_e937a9d3"

LRC_TS_RE = re.compile(r"^\[(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?\]")


def sha12(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def fetch_text(r2: R2Client, s3_key: str) -> str:
    """Read-only fetch of one R2 object body as UTF-8 text."""
    return r2.get_object_stream(s3_key)["body"].read().decode("utf-8")


def backup_ts(key: str) -> str:
    """Timestamp component of a ``<hp>/lyrics.backup.<ts>.lrc`` key."""
    return key.split("lyrics.backup.")[1].removesuffix(".lrc")


def is_ms_epoch(ts: str) -> bool:
    """True for wall-clock ms timestamps; False for the analysis service's
    historical ``asyncio.get_event_loop().time() * 1000`` bug values (which are
    monotonic loop uptime, not wall clock — ordering still valid within one
    service process, wall-clock dating impossible)."""
    return int(ts) > 10**11


def parse_lrc_lines(text: str) -> list[dict]:
    """Parse LRC into [{time_s, text}] preserving order; blank-text placeholder
    lines (ADR-0008) keep their timestamp with text=''. Non-timestamped lines
    (metadata tags) are dropped. Internal whitespace runs (multi-space, CJK
    spacing churn) collapse to a single space so spacing-only variants do not
    become phantom dropped/added texts."""
    rows = []
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        m = LRC_TS_RE.match(line.strip())
        if not m:
            continue
        minutes, seconds, frac = m.groups()
        time_s = int(minutes) * 60 + int(seconds) + int(frac or 0) / 10 ** len(frac or "0")
        text_part = re.sub(r"\s+", " ", line.strip()[m.end() :]).strip()
        rows.append({"time_s": round(time_s, 3), "text": text_part})
    return rows


def timing_shift_stats(before: list[dict], after: list[dict]) -> dict:
    """Align lines by (text, occurrence index): walk both ordered lists and
    pair each sung line's k-th occurrence of a text with the other side's k-th
    occurrence of the same text. Full coverage — repeated chorus lines align
    too, so shift min/max/mean are trustworthy even when texts are reworded
    elsewhere. Lines whose text vanished/appeared simply don't participate."""
    from collections import defaultdict

    def _by_occurrence(rows: list[dict]) -> dict:
        seen: dict = defaultdict(int)
        out: dict = {}
        for l in rows:
            if l["text"]:
                out[(l["text"], seen[l["text"]])] = l["time_s"]
                seen[l["text"]] += 1
        return out

    b_by_occ = _by_occurrence(before)
    a_by_occ = _by_occurrence(after)
    shifts = sorted(a_by_occ[k] - b_by_occ[k] for k in b_by_occ.keys() & a_by_occ.keys())
    if not shifts:
        return {"matched_lines": 0}
    return {
        "matched_lines": len(shifts),
        "total_sung_before": sum(1 for l in before if l["text"]),
        "shift_seconds_min": round(min(shifts), 3),
        "shift_seconds_max": round(max(shifts), 3),
        "shift_seconds_mean": round(sum(shifts) / len(shifts), 3),
        "all_zero": all(s == 0 for s in shifts),
    }


def _normalized(text: str) -> str:
    """Space-stripped text for reconstruction/coverage comparisons."""
    return text.replace(" ", "")


def _reconstructs(target: str, pieces: list[str]) -> bool:
    """True when ≥2 pieces (spaces stripped) exactly partition ``target``
    (spaces stripped) as contiguous substrings, in any order — a real
    split/merge shares ALL its text, with line boundaries moved. A single
    equal piece is a pure re-spacing of the same line, not a split (n≥2
    enforced via the piece count in the DP state).

    DP over target positions (O(len(target)·|pieces|)): order-insensitive
    yet exact — each piece must match a contiguous span and the spans must
    tile the target, so reordered merges are caught while near-anagrams
    (reordered chars, not reordered lines) are not.

    Deliberately exact: partial resegmentation (a moved split point where a
    refrain tail lands elsewhere) is indistinguishable from a rewrite when
    refrains repeat, so it is NOT flagged — zero false positives preferred
    for a calibration dataset."""
    t = _normalized(target)
    if not t:
        return False
    usable = sorted({_normalized(p) for p in pieces if p}, key=len, reverse=True)
    usable = [p for p in usable if p]
    # State (pos, min(pieces_used, 2)): reaching (len(t), 2) means the target
    # is fully tiled by ≥2 pieces. Capping the count at 2 bounds the state
    # space; any count ≥2 is equivalent for the flag.
    reachable = {(0, 0)}
    for pos in range(len(t)):
        for count in (0, 1):
            if (pos, count) not in reachable:
                continue
            for p in usable:
                if t.startswith(p, pos):
                    reachable.add((pos + len(p), min(count + 1, 2)))
    return (len(t), 2) in reachable


def classify_edits(before: list[dict], after: list[dict]) -> dict:
    """Structural edit classification: line counts, blank placeholder inserts,
    content-only retimes, text changes. Repeat-count changes are captured per
    text (Counter deltas), so a repeated line trimmed 5→3 shows up as
    ``count_changes``, not as a phantom add."""
    from collections import Counter

    b_sung = [l for l in before if l["text"]]
    a_sung = [l for l in after if l["text"]]
    b_blank = [l for l in before if not l["text"]]
    a_blank = [l for l in after if not l["text"]]
    b_counts = Counter(l["text"] for l in b_sung)
    a_counts = Counter(l["text"] for l in a_sung)
    # Net per-text deltas: negative = dropped/reduced repeats, positive =
    # added/expanded repeats. Ordered by first appearance for readability.
    all_texts = list(dict.fromkeys([l["text"] for l in b_sung] + [l["text"] for l in a_sung]))
    dropped = [t for t in all_texts if a_counts[t] < b_counts[t]]
    added = [t for t in all_texts if a_counts[t] > b_counts[t]]
    timing = timing_shift_stats(before, after)
    return {
        "sung_lines_before": len(b_sung),
        "sung_lines_after": len(a_sung),
        "blank_lines_before": len(b_blank),
        "blank_lines_after": len(a_blank),
        "dropped_texts": dropped,
        "added_texts": added,
        "count_changes": {
            t: {"before": b_counts[t], "after": a_counts[t]} for t in dropped + added
        },
        # Split/merge: added texts reconstruct a dropped text (spaces
        # stripped, ≥2 pieces, in order) or vice versa — a real split/merge
        # shares ALL its text. Mere phrase overlap (a chorus phrase inside an
        # unrelated rewrite) does not reconstruct.
        "line_splits_or_merges": any(_reconstructs(t1, added) for t1 in dropped)
        or any(_reconstructs(t2, dropped) for t2 in added),
        "timing": timing,
    }


def scan_r2_ladders(r2: R2Client) -> tuple[dict, dict]:
    """Read-only scan of every object in the bucket. Returns:
    - official ladders: {hp: {backups: [obj...], current_key}}
    - editor-session backups: {hp: [obj...]} (``{hp}/backups/lyrics.{ts}.lrc``)
    """
    official: dict[str, dict] = {}
    editor: dict[str, list] = {}
    for obj in r2.iter_objects():
        parts = obj["key"].split("/")
        if len(parts) != 2:
            if len(parts) == 3 and parts[1] == "backups" and parts[2].endswith(".lrc"):
                editor.setdefault(parts[0], []).append(obj)
            continue
        hp, name = parts
        entry = official.setdefault(hp, {"backups": [], "current_key": None})
        if name == "lyrics.lrc":
            entry["current_key"] = obj["key"]
        elif name.startswith("lyrics.backup.") and name.endswith(".lrc"):
            entry["backups"].append(obj)
    for entry in official.values():
        entry["backups"].sort(key=lambda o: o["last_modified"])
    for objs in editor.values():
        objs.sort(key=lambda o: o["last_modified"])
    return official, editor


def query_recording_provenance(conn) -> dict:
    """Read-only: per recording, song_id / lrc_source / lrc_status / visibility /
    updated_at / youtube_url presence."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT hash_prefix, song_id, lrc_source, lrc_status, visibility_status,
                   updated_at, imported_at, (youtube_url IS NOT NULL), duration_seconds, tempo_bpm
            FROM recordings
            WHERE deleted_at IS NULL
            """)
        rows = {}
        for (
            hp,
            song_id,
            lrc_source,
            lrc_status,
            visibility,
            updated_at,
            imported_at,
            has_youtube,
            duration,
            bpm,
        ) in cur.fetchall():
            rows[hp] = {
                "song_id": song_id,
                "lrc_source": lrc_source,
                "lrc_status": lrc_status,
                "visibility_status": visibility,
                "updated_at": updated_at.isoformat() if updated_at else None,
                "imported_at": imported_at,
                "has_youtube_url": has_youtube,
                "duration_seconds": duration,
                "tempo_bpm": bpm,
            }
        return rows


def query_feedback_timeline(conn, song_id: str) -> list[dict]:
    """Read-only: feedback rows for one song (for the anomaly inspection)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT f.rating, f.reason, f.created_at, f.resolved_at, f.updated_at
            FROM lyrics_feedback f
            JOIN recordings r ON r.content_hash = f.recording_content_hash
            WHERE r.song_id = %s
            ORDER BY f.created_at
            """,
            (song_id,),
        )
        return [
            {
                "rating": rating,
                "reason": reason,
                "created_at": created_at.isoformat() if created_at else None,
                "resolved_at": resolved_at.isoformat() if resolved_at else None,
                "updated_at": updated_at.isoformat() if updated_at else None,
            }
            for rating, reason, created_at, resolved_at, updated_at in cur.fetchall()
        ]


def query_user_lrc_overrides(conn, r2: R2Client) -> dict:
    """Read-only: mine ``user_lrc_override.lrc_content`` rows.

    Each override is a per-user human-corrected LRC text for a recording — a
    potential before/after successor whose provenance is human by definition.
    Returns {hash_prefix: [{user_id, length, created_at, updated_at, content}]}.
    Empty table today; mined on the off-chance rows appear before a later
    snapshot.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT r.hash_prefix, o.user_id, LENGTH(o.lrc_content),
                   o.created_at, o.updated_at, o.lrc_content
            FROM user_lrc_override o
            JOIN recordings r ON r.content_hash = o.recording_content_hash
            WHERE r.deleted_at IS NULL
            ORDER BY r.hash_prefix, o.created_at
            """)
        out: dict[str, list] = {}
        for hp, user_id, length, created_at, updated_at, content in cur.fetchall():
            current = fetch_text(r2, f"{hp}/lyrics.lrc") if hp not in out else None
            out.setdefault(hp, []).append(
                {
                    "user_id": user_id,
                    "length": length,
                    "created_at": created_at.isoformat() if created_at else None,
                    "updated_at": updated_at.isoformat() if updated_at else None,
                    "content": content,
                    # Baseline for the before/after pair: the recording's
                    # current official LRC at mining time.
                    "current_official_sha12": sha12(current) if current else None,
                }
            )
        return out


def build_pairs(official, editor, prov, r2: R2Client) -> tuple[list[dict], list[dict]]:
    """Pair each eligible backup with its immediate successor.

    Pairing gate (two independent paths, both provably human-successor):

    1. ``recordings.lrc_source ∈ {manual_upload, llm_edit}`` — the column
       written by the editor upload path (captured since 2026-09-23,
       c49cf014).
    2. Editor-draft mechanism (covers NULL lrc_source: pre-2026-09-23
       recordings): the newest official backup's content hash equals the
       newest editor-session draft's hash (``{hp}/backups/lyrics.{ts}.lrc``,
       written by ``upload_r2_backup`` immediately before each editor save).
       The editor upload path provably writes draft → revised, so the
       successor (current content) is human-attributed by mechanism even
       though the DB column is NULL. Also requires before != current: a later
       machine regen after the session would have written a newer official
       backup, breaking the hash match — the gate self-protects.

    Intermediate backup→backup successors have no per-version provenance and
    are never paired; each gets a ``skipped`` accounting entry so a reviewer
    can see every backup was considered.

    Returns (pairs, skipped_records).
    """
    pairs: list[dict] = []
    skipped: list[dict] = []

    def _skip(hp: str, rec: dict, reason: str, backup_key: str | None = None) -> None:
        entry = {
            "hash_prefix": hp,
            "song_id": rec.get("song_id"),
            "lrc_source": rec.get("lrc_source"),
            "reason": reason,
        }
        if backup_key is not None:
            entry["backup_key"] = backup_key
        skipped.append(entry)

    for hp, ladder in sorted(official.items()):
        if not ladder["backups"] or ladder["current_key"] is None:
            continue
        rec = prov.get(hp) or {}
        newest = ladder["backups"][-1]

        # Account for every intermediate backup: backup→backup successors have
        # unknown provenance (never diff two machine outputs).
        for older in ladder["backups"][:-1]:
            _skip(
                hp,
                rec,
                "intermediate backup→backup successor: no per-version "
                "provenance (lrc_source is a recording-level column since "
                "2026-09-23); never diff two machine outputs",
                backup_key=older["key"],
            )

        column_human = rec.get("lrc_source") in HUMAN_SOURCES
        ed_objs = editor.get(hp, [])
        before_content = fetch_text(r2, newest["key"])
        after_content = fetch_text(r2, ladder["current_key"])
        identical = before_content == after_content

        if identical:
            _skip(
                hp,
                rec,
                "newest backup content identical to current (no diff)",
                backup_key=newest["key"],
            )
            continue

        if column_human:
            mechanism = "lrc_source_column"
            immediacy = (
                sha12(fetch_text(r2, ed_objs[-1]["key"])) == sha12(before_content)
                if ed_objs
                else None
            )
        elif ed_objs and sha12(fetch_text(r2, ed_objs[-1]["key"])) == sha12(before_content):
            # NULL column, but the draft→current hash chain proves an editor
            # session wrote both the backup and the current content.
            mechanism = "editor_draft_mechanism"
            immediacy = True
        else:
            _skip(
                hp,
                rec,
                "successor not attributable to a human edit: lrc_source not in "
                + "|".join(sorted(HUMAN_SOURCES))
                + (
                    " and no matching editor-session draft"
                    if ed_objs
                    else " and no editor-session ladder"
                ),
                backup_key=newest["key"],
            )
            continue

        before_rows = parse_lrc_lines(before_content)
        after_rows = parse_lrc_lines(after_content)
        pairs.append(
            {
                "hash_prefix": hp,
                "song_id": rec.get("song_id"),
                "lrc_source": rec.get("lrc_source"),
                "provenance": {
                    "mechanism": mechanism,
                    "immediacy_evidence": {
                        "newest_editor_backup_matches_before": immediacy,
                        "note": "the newest editor-session draft "
                        f"({hp}/backups/lyrics.{{ts}}.lrc) content hash equals the "
                        "newest official backup's hash, proving the backup was "
                        "written by the editor session that uploaded the "
                        "current content (no lost intermediate version)",
                    },
                },
                "before": {
                    "r2_key": newest["key"],
                    "uploaded_at": newest["last_modified"],
                    "timestamp_kind": (
                        "wall_clock_ms"
                        if is_ms_epoch(backup_ts(newest["key"]))
                        else "loop_time_ms_not_wall_clock"
                    ),
                    "sha12": sha12(before_content),
                    "content": before_content,
                },
                "after": {
                    "r2_key": ladder["current_key"],
                    "sha12": sha12(after_content),
                    "content": after_content,
                    "recording_updated_at": rec.get("updated_at"),
                },
                "diff": list(
                    difflib.unified_diff(
                        before_content.splitlines(),
                        after_content.splitlines(),
                        fromfile="before",
                        tofile="after",
                        lineterm="",
                        n=1,
                    )
                ),
                "edits": classify_edits(before_rows, after_rows),
            }
        )
    return pairs, skipped


def inspect_anomaly(r2: R2Client, prov, conn, official) -> dict:
    """Manual_upload seed negative inspection (Appendix A anomaly)."""
    hp = next((h for h, r in prov.items() if r.get("song_id") == ANOMALY_SONG_ID), None)
    findings: dict = {
        "song_id": ANOMALY_SONG_ID,
        "hash_prefix": hp,
        "feedback_timeline": query_feedback_timeline(conn, ANOMALY_SONG_ID),
    }
    if hp is None or hp not in official or not official[hp]["backups"]:
        findings["error"] = "no ladder found for anomaly song"
        return findings
    ladder = official[hp]
    rec = prov.get(hp) or {}

    # Pre-fix state: the oldest backup (batch backup 2026-09-24 00:21 preserved
    # the feedback-time content). Post-fix: the current content.
    oldest = ladder["backups"][0]
    before_content = fetch_text(r2, oldest["key"])
    after_content = fetch_text(r2, ladder["current_key"])

    # Canonical lyrics from the songs table + YouTube structured lyrics, for
    # the wrong_text attribution.
    with conn.cursor() as cur:
        cur.execute("SELECT lyrics_lines FROM songs WHERE id = %s", (ANOMALY_SONG_ID,))
        row = cur.fetchone()
        canon_lines = json.loads(row[0]) if row and row[0] else []
        cur.execute("SELECT structured_lyrics FROM recordings WHERE hash_prefix = %s", (hp,))
        sl_row = cur.fetchone()
        structured = json.loads(sl_row[0]) if sl_row and sl_row[0] else None

    structured_texts = [
        line
        for sec in (structured or {}).get("sections", [])
        for line in sec.get("lines", [])
        if line.strip()
    ]

    findings.update(
        {
            "recording": {
                "lrc_source": rec.get("lrc_source"),
                "visibility_status": rec.get("visibility_status"),
                "duration_seconds": rec.get("duration_seconds"),
                "tempo_bpm": rec.get("tempo_bpm"),
            },
            "ladder": [
                {"key": o["key"], "last_modified": o["last_modified"]} for o in ladder["backups"]
            ],
            "before": {
                "r2_key": oldest["key"],
                "uploaded_at": oldest["last_modified"],
                "sha12": sha12(before_content),
                "sung_lines": len([l for l in parse_lrc_lines(before_content) if l["text"]]),
            },
            "after": {
                "r2_key": ladder["current_key"],
                "sha12": sha12(after_content),
                "sung_lines": len([l for l in parse_lrc_lines(after_content) if l["text"]]),
            },
            "canonical_lyrics_lines": canon_lines,
            "canonical_lyrics_note": (
                (
                    "songs.lyrics_lines is a single merged line (scraper artifact) — "
                    "useless for per-line comparison"
                )
                if len(canon_lines) == 1
                else None
            ),
            "structured_lyrics_texts": structured_texts,
            "structured_lyrics_sections": [
                {"label": s.get("raw_label"), "n_lines": len(s.get("lines", []))}
                for s in (structured or {}).get("sections", [])
            ],
            "edits": classify_edits(
                parse_lrc_lines(before_content), parse_lrc_lines(after_content)
            ),
        }
    )
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for the mining JSON (default: eval/lrc_truth)",
    )
    parser.add_argument("--config", type=Path, default=None, help="Admin config path")
    args = parser.parse_args(argv)

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = output_dir / "_raw"
    raw_dir.mkdir(exist_ok=True)

    started_at = datetime.now(UTC).isoformat(timespec="seconds")

    config = AdminConfig.load(args.config)
    r2 = R2Client(bucket=config.r2_bucket, endpoint_url=config.r2_endpoint_url)
    provider = ConnectionProvider(config.get_connection_url())
    conn = provider.get_connection()

    official, editor = scan_r2_ladders(r2)
    prov = query_recording_provenance(conn)

    pairs, skipped = build_pairs(official, editor, prov, r2)
    anomaly = inspect_anomaly(r2, prov, conn, official)
    overrides = query_user_lrc_overrides(conn, r2)
    provider.close()

    raw_official = {
        hp: {
            "backups": [
                {"key": o["key"], "size": o["size"], "last_modified": o["last_modified"]}
                for o in ladder["backups"]
            ],
        }
        for hp, ladder in official.items()
        if ladder["backups"]
    }
    (raw_dir / "r2_ladder_index.json").write_text(
        json.dumps(raw_official, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # Full raw dumps: fetched ladder + editor-session contents with provenance.
    # Large (~300 KiB) but make the dataset reproducible without re-running the
    # network scan.
    raw_ladders = {}
    for hp, ladder in official.items():
        if not ladder["backups"]:
            continue
        rec = prov.get(hp) or {}
        raw_ladders[hp] = {
            "song_id": rec.get("song_id"),
            "lrc_source": rec.get("lrc_source"),
            "lrc_status": rec.get("lrc_status"),
            "visibility_status": rec.get("visibility_status"),
            "recording_updated_at": rec.get("updated_at"),
            "backups": raw_official[hp]["backups"],
            "current_lrc": fetch_text(r2, ladder["current_key"]),
            "backup_lrcs": {
                backup_ts(o["key"]): fetch_text(r2, o["key"]) for o in ladder["backups"]
            },
        }
    (raw_dir / "backup_ladders.json").write_text(
        json.dumps(raw_ladders, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    raw_editor = {
        hp: [
            {
                "key": o["key"],
                "size": o["size"],
                "last_modified": o["last_modified"],
                "content": fetch_text(r2, o["key"]),
            }
            for o in objs
        ]
        for hp, objs in editor.items()
        if objs
    }
    (raw_dir / "editor_backups.json").write_text(
        json.dumps(raw_editor, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    eligible_prefixes = [
        hp
        for hp, rec in prov.items()
        if rec.get("lrc_source") in HUMAN_SOURCES and hp in official and official[hp]["backups"]
    ]
    dataset = {
        "schema_version": 1,
        "created_at": started_at,
        "spec": "specs/lrc-review-triage-cascade-design-v2.md",
        "phase": "0b",
        "pairing_rule": (
            "backup → successor paired ONLY when the successor is the recording's "
            "current content AND is attributable to a human edit, by one of two "
            "paths: (1) recordings.lrc_source ∈ {manual_upload, llm_edit}, or "
            "(2) editor-draft mechanism — the newest official backup's content "
            "hash equals the newest editor-session draft's hash, proving that "
            "session uploaded current (used for pre-2026-09-23 recordings whose "
            "lrc_source is NULL). backup→backup successors have no per-version "
            "provenance and are skipped with accounting entries, never paired "
            "(never diff two machine outputs)"
        ),
        "ladder_order_check": (
            "newest ladder entry → current, gated on the hash match with the "
            "newest editor-session draft ({hp}/backups/lyrics.{ts}.lrc), which "
            "proves the backup was written by the same editor session that "
            "uploaded current — no intermediate version sits inside the pair; "
            "every intermediate backup appears in `skipped` with a reason"
        ),
        "counts": {
            "recordings": len(prov),
            "prefixes_with_backups": len(raw_official),
            "backup_objects": sum(len(ladder["backups"]) for ladder in official.values()),
            "human_source_prefixes_with_backups": len(eligible_prefixes),
            "pairs": len(pairs),
            "pairs_by_mechanism": {
                m: sum(1 for p in pairs if p["provenance"]["mechanism"] == m)
                for m in sorted({p["provenance"]["mechanism"] for p in pairs})
            },
            "skipped": len(skipped),
            "skipped_intermediate_backups": sum(
                1 for s in skipped if s["reason"].startswith("intermediate")
            ),
            "user_lrc_override_prefixes": len(overrides),
            "user_lrc_override_rows": sum(len(v) for v in overrides.values()),
        },
        "pairs": pairs,
        "skipped": skipped,
        "user_lrc_overrides": overrides,
        "anomaly": anomaly,
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = output_dir / f"ground-truth-pairs-{stamp}.json"
    out_path.write_text(json.dumps(dataset, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output_dir / "ground-truth-pairs-latest.json").write_text(
        json.dumps(dataset, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(f"pairs: {len(pairs)} (skipped {len(skipped)} entries)")
    for p in pairs:
        e = p["edits"]
        t = e.get("timing", {})
        print(
            f"  {p['song_id']} [{p['provenance']['mechanism']}]: "
            f"{e['sung_lines_before']}→{e['sung_lines_after']} sung lines, "
            f"timing matched {t.get('matched_lines', 0)}/{t.get('total_sung_before', '?')} lines "
            f"(shift {t.get('shift_seconds_min')}–{t.get('shift_seconds_max')}s), "
            f"split/merge={e['line_splits_or_merges']}"
        )
    print(f"anomaly: {anomaly.get('hash_prefix')} — see {out_path.name}")
    print(f"-> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
