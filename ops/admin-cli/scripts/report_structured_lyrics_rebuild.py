#!/usr/bin/env python3
"""Generate the HTML report for the 340-song structured-lyrics rebuild.

The forced rebuild of ``recordings.structured_lyrics`` for the review corpus
finished on 2026-10-29. Pre-run lyric text is unrecoverable (no audit table
stores text, PITR retention is shorter than the elapsed time, no dump on this
host), so the report is split in two:

1. A provable headline partition computed from ``/tmp/sow-review-pre.csv``
   (``song_id|md5(coalesce(structured_lyrics,''))`` captured before the run)
   compared against the current column value.
2. A clearly-labelled proxy section that compares each new parse against the
   stale R2 ``lyrics.lrc`` object (YouTube ASR text), which yields the
   material / non-material / verbatim split requested by the user.

Read-only: every DB statement is a SELECT; the only write is the HTML file.

Usage (no arguments):
    set -a; . /opt/sow/.env; set +a
    uv run --project ops/admin-cli --extra admin \\
        python ops/admin-cli/scripts/report_structured_lyrics_rebuild.py
"""

from __future__ import annotations

import difflib
import hashlib
import html
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from botocore.exceptions import ClientError

from stream_of_worship.admin.services.r2 import R2Client

PRE_SNAPSHOT = Path("/tmp/sow-review-pre.csv")
EMPTY_MD5 = "d41d8cd98f00b204e9800998ecf8427e"
REPO_ROOT = Path(__file__).resolve().parents[3]  # ops/admin-cli/scripts/<file> -> repo root
OUTPUT = REPO_ROOT / "reports" / "review-structured-lyrics-rebuild-2026-10-30.html"
TS_RE = re.compile(r"^\[(\d+):(\d+(?:\.\d+)?)\](.*)$")
META_RE = re.compile(r"^\[[A-Za-z]+:.*\]$")
PUNCT = "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~。，、；：？！…—～·「」『』（）【】《》〈〉“”‘’‧・　"

CORPUS_DESC = "visibility_status = 'review' AND deleted_at IS NULL"

QUERY = """
SELECT song_id, hash_prefix, structured_lyrics, lrc_source
FROM recordings
WHERE visibility_status = 'review' AND deleted_at IS NULL
ORDER BY song_id
"""

PROVABLE_ORDER = ("created", "rebuilt", "byte_identical", "unprovable")
PROVABLE_MEANING = {
    "created": "no structured lyrics before the rebuild",
    "rebuilt": "had structured lyrics; the stored JSON provably differs now",
    "byte_identical": "stored JSON unchanged",
    "unprovable": "absent from the pre-run snapshot (corpus drift)",
}
LRC_ORDER = ("material", "non-material", "verbatim", "unknown")
LEVEL_ORDER = ("S0", "S1", "S2", "S3", "none", "no_lrc")
LEVEL_MEANING = {
    "S0": "identical lines and line breaks",
    "S1": "identical after collapsing intra-line whitespace runs",
    "S2": "identical after removing all whitespace (line breaks ignored)",
    "S3": "identical after also removing punctuation",
    "none": "differs beyond punctuation — treated as material",
    "no_lrc": "no usable LRC text to compare against",
}

MAX_LRC_ERRORS = 10


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def parse_json_doc(text: str | None) -> dict | None:
    """Decode a stored ``structured_lyrics`` document, or ``None``."""
    if not text:
        return None
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return None
    return doc if isinstance(doc, dict) else None


def parse_json_lines(text: str | None) -> list[str]:
    """Flatten every ``sections[*].lines`` entry in document order.

    ``preamble_lines`` is excluded: the LRC side carries lyric lines only.
    """
    doc = parse_json_doc(text)
    if not doc:
        return []
    out: list[str] = []
    for section in doc.get("sections") or []:
        if not isinstance(section, dict):
            continue
        for line in section.get("lines") or []:
            if not isinstance(line, str):
                continue
            stripped = line.strip()
            if stripped:
                out.append(stripped)
    return out


def lrc_lines(text: str | None) -> list[str]:
    """Strip LRC metadata tags and timestamps, returning lyric lines."""
    if not text:
        return []
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if META_RE.match(line):
            continue
        match = TS_RE.match(line)
        if match:
            line = match.group(3)
        line = line.strip()
        if line:
            out.append(line)
    return out


def _n1(lines: list[str]) -> str:
    return "\n".join(re.sub(r"\s+", " ", ln).strip() for ln in lines)


def _n2(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _n3(text: str) -> str:
    return "".join(ch for ch in text if ch not in PUNCT)


def ladder(pre: list[str], post: list[str]) -> tuple[str, float]:
    """Return ``(level, similarity)`` for a baseline/parse line pair."""
    s0p, s0q = "\n".join(pre), "\n".join(post)
    s1p, s1q = _n1(pre), _n1(post)
    s2p, s2q = _n2(s1p), _n2(s1q)
    s3p, s3q = _n3(s2p), _n3(s2q)
    if s0p == s0q:
        level = "S0"
    elif s1p == s1q:
        level = "S1"
    elif s2p == s2q:
        level = "S2"
    elif s3p == s3q:
        level = "S3"
    else:
        level = "none"
    return level, round(difflib.SequenceMatcher(None, s3p, s3q).ratio(), 3)


def level_to_bucket(level: str) -> str:
    """Map a ladder level to the report bucket (whitespace+formatting merged)."""
    if level == "S0":
        return "verbatim"
    if level in ("S1", "S2", "S3"):
        return "non-material"
    if level == "no_lrc":
        return "unknown"
    return "material"


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile of an already-sorted list."""
    if not values:
        return 0.0
    pos = (len(values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def load_snapshot(path: Path) -> dict[str, str]:
    """Read ``song_id|md5`` lines captured before the rebuild."""
    snapshot: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        song_id, md5 = line.split("|", 1)
        snapshot[song_id.strip()] = md5.strip()
    return snapshot


# --------------------------------------------------------------------------- #
# Row collection
# --------------------------------------------------------------------------- #
def collect_rows() -> list[dict]:
    """Fetch the corpus and compute every per-song measurement."""
    with psycopg.connect(os.environ["SOW_DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute(QUERY)
            raw_rows = cur.fetchall()
    print(f"rows={len(raw_rows)}")

    snapshot = load_snapshot(PRE_SNAPSHOT) if PRE_SNAPSHOT.exists() else None
    if snapshot is None:
        print("pre-snapshot unavailable — provable split omitted")

    client = R2Client(
        bucket=os.environ["SOW_R2_BUCKET"],
        endpoint_url=os.environ["SOW_R2_ENDPOINT_URL"],
    )
    cache: dict[str, str | None] = {}
    state = {"errors": 0, "stopped": False}

    def get_lrc(hash_prefix: str | None) -> str | None:
        if not hash_prefix:
            return None
        if hash_prefix in cache:
            return cache[hash_prefix]
        if state["stopped"]:
            return None
        try:
            content = client.download_lrc_content(hash_prefix)
        except ClientError:
            state["errors"] += 1
            if state["errors"] >= MAX_LRC_ERRORS:
                state["stopped"] = True
            cache[hash_prefix] = None
            return None
        cache[hash_prefix] = content
        return content

    rows: list[dict] = []
    for song_id, hash_prefix, structured, lrc_source in raw_rows:
        post_lines = parse_json_lines(structured)
        post_md5 = hashlib.md5(
            (structured or "").encode(), usedforsecurity=False
        ).hexdigest()
        if snapshot is None:
            provable = "unprovable"
            pre_state = "?"
        elif song_id not in snapshot:
            provable = "unprovable"
            pre_state = "?"
        else:
            pre_md5 = snapshot[song_id]
            pre_state = "empty" if pre_md5 == EMPTY_MD5 else "JSON"
            if pre_md5 == post_md5:
                provable = "byte_identical"
            elif pre_md5 == EMPTY_MD5:
                provable = "created"
            else:
                provable = "rebuilt"

        baseline = lrc_lines(get_lrc(hash_prefix))
        if baseline:
            level, similarity = ladder(baseline, post_lines)
            lrc_bucket = level_to_bucket(level)
        else:
            level, similarity = "no_lrc", None
            lrc_bucket = "unknown"

        doc = parse_json_doc(structured)
        rows.append(
            {
                "song_id": song_id,
                "hash_prefix": hash_prefix or "",
                "pre_state": pre_state,
                "provable": provable,
                "lrc_bucket": lrc_bucket,
                "level": level,
                "similarity": similarity,
                "sections": len((doc or {}).get("sections") or []),
                "new_lines": len(post_lines),
                "lrc_lines": len(baseline),
                "lrc_source": lrc_source,
            }
        )

    if state["errors"]:
        print(
            f"warning: {state['errors']} non-404 R2 errors"
            + (" — remaining rows marked no_lrc" if state["stopped"] else "")
        )
    return rows


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
STYLE = """
:root { color-scheme: light dark; }
body { font-family: -apple-system, "Segoe UI", "Noto Sans TC", sans-serif;
       max-width: 1100px; margin: 2rem auto; padding: 0 1rem; line-height: 1.55;
       font-size: 15px; }
h1 { font-size: 1.6rem; margin-bottom: .25rem; }
h2 { font-size: 1.12rem; margin-top: 2.25rem; border-bottom: 1px solid currentColor;
     padding-bottom: .2rem; opacity: .95; }
p, li { max-width: 72ch; }
table { border-collapse: collapse; width: 100%; margin: .8rem 0; font-size: .9rem; }
th, td { border: 1px solid rgba(127,127,127,.45); padding: .32rem .55rem;
         text-align: left; vertical-align: top; }
th { background: rgba(127,127,127,.14); font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums;
                 white-space: nowrap; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
            font-size: .86rem; }
pre { background: rgba(127,127,127,.12); padding: .7rem; overflow-x: auto;
      white-space: pre-wrap; }
.note { opacity: .72; font-size: .88rem; }
details > summary { cursor: pointer; font-weight: 600; margin: .6rem 0; }
.scroll { overflow-x: auto; }
.scroll table { width: auto; min-width: 100%; }
"""


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def render(rows: list[dict], generated_at: str) -> str:
    total = len(rows)
    provable_counts = Counter(r["provable"] for r in rows)
    lrc_counts = Counter(r["lrc_bucket"] for r in rows)
    level_counts = Counter(r["level"] for r in rows)
    source_counts = Counter(r["lrc_source"] for r in rows)

    # Material split detail: whitespace (S1/S2) vs formatting (S3).
    whitespace = level_counts["S1"] + level_counts["S2"]
    formatting = level_counts["S3"]
    verbatim = lrc_counts["verbatim"]
    material = lrc_counts["material"]
    unknown = lrc_counts["unknown"]
    non_material = lrc_counts["non-material"]

    sims = sorted(r["similarity"] for r in rows if r["similarity"] is not None)
    short_lrc = sum(1 for r in rows if r["similarity"] is not None and r["lrc_lines"] <= 2)

    provable_rows = [b for b in PROVABLE_ORDER if provable_counts[b]]
    col_sum = total if not provable_rows else sum(provable_counts[b] for b in provable_rows)
    reported_sum = verbatim + non_material + material + unknown
    if col_sum != total or reported_sum != total:
        print(
            f"SUM MISMATCH provable={col_sum} lrc={reported_sum} rows={total}",
        )
        raise SystemExit(1)

    parts: list[str] = []
    add = parts.append
    add("<!DOCTYPE html>")
    add('<html lang="en">')
    add("<head>")
    add('<meta charset="utf-8">')
    add('<meta name="viewport" content="width=device-width, initial-scale=1">')
    add("<title>Structured Lyric Rebuild — Review Corpus</title>")
    add(f"<style>{STYLE}</style>")
    add("</head>")
    add("<body>")

    # 1. Header
    add("<h1>Structured Lyric Rebuild — Review Corpus</h1>")
    add(
        f"<p>Corpus: <code>{esc(CORPUS_DESC)}</code> — <strong>{total}</strong> recordings, "
        f"queried {esc(generated_at)}. The headline partition below is "
        "<strong>provable</strong>: it compares a per-song md5 snapshot taken before the "
        "rebuild against the stored value now. The material / non-material / verbatim split "
        "is a <strong>proxy</strong> that compares each new parse against the stale "
        "<code>lyrics.lrc</code> object, which holds YouTube ASR text rather than the "
        "pre-rebuild structured lyrics.</p>"
    )

    # 2. Headline (provable)
    if PRE_SNAPSHOT.exists() and len(provable_rows) and "unprovable" not in provable_counts:
        add("<h2>Headline (provable from the pre-run md5 snapshot)</h2>")
        add("<table>")
        add('<tr><th>Bucket</th><th class="num">Count</th><th>Meaning</th></tr>')
        for bucket in provable_rows:
            add(
                f"<tr><td>{esc(bucket)}</td>"
                f'<td class="num">{provable_counts[bucket]}</td>'
                f"<td>{esc(PROVABLE_MEANING[bucket])}</td></tr>"
            )
        add(f'<tr><th>Total</th><th class="num">{total}</th><th></th></tr>')
        add("</table>")
        add(
            '<p class="note">The reconstructed column is stored as '
            "<code>json.dumps(structured, ensure_ascii=False)</code>, so md5 equality is "
            "exactly parse equality.</p>"
        )

    # 3. LRC/ASR proxy
    add("<h2>LRC/ASR proxy — what the lyric text looks like now vs the LRC baseline</h2>")
    add("<table>")
    add('<tr><th>Bucket</th><th class="num">Count</th><th>Meaning</th></tr>')
    proxy_rows = [
        ("material", material, "differs beyond punctuation and whitespace"),
        (
            "non-material (whitespace/punctuation/line-break only)",
            non_material,
            f"identical once normalised — whitespace {whitespace} / formatting {formatting}",
        ),
        ("verbatim (exact line text and line breaks match)", verbatim, "level S0"),
        ("unknown (no usable LRC text)", unknown, "LRC missing, empty, or unfetchable"),
    ]
    for label, count, meaning in proxy_rows:
        add(
            f"<tr><td>{esc(label)}</td>"
            f'<td class="num">{count}</td><td>{esc(meaning)}</td></tr>'
        )
    add(f'<tr><th>Total</th><th class="num">{reported_sum}</th><th></th></tr>')
    add("</table>")
    usable = reported_sum - unknown
    if unknown:
        add(
            '<p class="note">Percentages in this section exclude the '
            f"{unknown} <code>unknown</code> rows: of the "
            f"{usable} rows with usable LRC text, "
            f"{material} are material, {non_material} non-material and {verbatim} verbatim.</p>"
        )
    else:
        add(
            f'<p class="note">Every row had usable LRC text, so the split covers all '
            f"{usable} rows: {material} material, {non_material} non-material and "
            f"{verbatim} verbatim.</p>"
        )

    # 4. Ladder detail
    add("<h2>Ladder detail</h2>")
    add("<table>")
    add('<tr><th>Level</th><th class="num">Count</th><th>Normalisation that made it match</th></tr>')
    for level in LEVEL_ORDER:
        add(
            f"<tr><td>{esc(level)}</td>"
            f'<td class="num">{level_counts[level]}</td>'
            f"<td>{esc(LEVEL_MEANING[level])}</td></tr>"
        )
    add("</table>")

    # 5. Cross-tab
    if PRE_SNAPSHOT.exists() and "unprovable" not in provable_counts:
        add("<h2>Cross-tab — provable bucket × proxy bucket</h2>")
        add("<table>")
        header = "".join(f'<th class="num">{esc(b)}</th>' for b in LRC_ORDER)
        add(f'<tr><th>provable \\ proxy</th>{header}<th class="num">Total</th></tr>')
        for bucket in provable_rows:
            cells = "".join(
                f'<td class="num">'
                f'{sum(1 for r in rows if r["provable"] == bucket and r["lrc_bucket"] == b)}</td>'
                for b in LRC_ORDER
            )
            add(
                f"<tr><td>{esc(bucket)}</td>{cells}"
                f'<td class="num">{provable_counts[bucket]}</td></tr>'
            )
        totals = "".join(f'<td class="num">{lrc_counts[b]}</td>' for b in LRC_ORDER)
        add(f'<tr><th>Total</th>{totals}<th class="num">{total}</th></tr>')
        add("</table>")

    # 6. Similarity diagnostics
    add("<h2>Similarity diagnostics</h2>")
    add(
        f'<p class="note">Over the {len(sims)} rows with usable LRC text, on the '
        "punctuation-stripped, whitespace-free strings "
        "(<code>difflib.SequenceMatcher.ratio()</code>; 0.0 = no shared characters, "
        "1.0 = identical).</p>"
    )
    add("<table>")
    add('<tr><th>min</th><th class="num">p25</th><th class="num">median</th>'
        '<th class="num">p75</th><th class="num">max</th></tr>')
    if sims:
        add(
            f'<tr><td class="num">{sims[0]:.3f}</td>'
            f'<td class="num">{percentile(sims, 0.25):.3f}</td>'
            f'<td class="num">{percentile(sims, 0.50):.3f}</td>'
            f'<td class="num">{percentile(sims, 0.75):.3f}</td>'
            f'<td class="num">{sims[-1]:.3f}</td></tr>'
        )
    else:
        add('<tr><td class="num">—</td><td class="num">—</td><td class="num">—</td>'
            '<td class="num">—</td><td class="num">—</td></tr>')
    add("</table>")
    add("<table>")
    add('<tr><th>Diagnostic</th><th class="num">Count</th></tr>')
    add(f'<tr><td>ratio &gt;= 0.90</td>'
        f'<td class="num">{sum(1 for s in sims if s >= 0.90)}</td></tr>')
    add(f'<tr><td>ratio &lt; 0.50</td>'
        f'<td class="num">{sum(1 for s in sims if s < 0.50)}</td></tr>')
    add(f'<tr><td>LRC baseline has 2 lines or fewer</td>'
        f'<td class="num">{short_lrc}</td></tr>')
    add("</table>")

    # 7. How to read this
    source_bits = ", ".join(
        f"{esc(src if src is not None else 'null')} {count}"
        for src, count in source_counts.most_common()
    )
    add("<h2>How to read this</h2>")
    add("<ul>")
    add(
        "<li><strong>Pre-run structured lyrics are unrecoverable.</strong> No audit table "
        "stores lyric text (<code>log_change</code> records counts only); Neon PITR "
        "retention is 6 h while the rebuild started 2026-10-29T06:56:33Z; the live project "
        "has only its <code>production</code> branch; no DB dump exists on this host; and "
        "re-running the heuristic parser over the current raw text reproduces 0 of the "
        "212 pre-run md5s.</li>"
    )
    add(
        "<li><strong>&ldquo;Provable&rdquo; means byte equality of the stored column.</strong> "
        "Because storage serialisation is deterministic "
        "(<code>json.dumps(structured, ensure_ascii=False)</code>), a changed md5 means the "
        "parse itself changed — not just formatting of the JSON.</li>"
    )
    add(
        "<li><strong>The proxy compares the new parse against ASR text, not against the "
        "rebuild&rsquo;s delta.</strong> <code>lyrics.lrc</code> is a transcript whose line "
        "breaks are driven by ASR timestamps, so line-level and even text-level differences "
        "are expected regardless of what the rebuild changed. <code>lrc_source</code> over "
        f"this corpus: {source_bits}. A <code>material</code> verdict therefore means "
        "&ldquo;the new parse is textually far from the ASR transcript&rdquo;, which is "
        "often the <em>better</em> outcome, not a regression.</li>"
    )
    add(
        "<li><strong>Verbatim here can be coincidental.</strong> An S0 match means the new "
        "parse happens to reproduce the ASR line text and line breaks exactly; it does not "
        "prove the rebuild preserved something.</li>"
    )
    if unknown:
        add(
            f"<li><strong>{unknown} rows have no proxy verdict.</strong> Their LRC object is "
            "missing, empty, or was unfetchable, so they are unknown, not unchanged; they are "
            "excluded from the proxy percentages but listed per song below.</li>"
        )
    else:
        add(
            "<li><strong>Every row has a proxy verdict.</strong> All "
            f"{total} recordings had a readable <code>lyrics.lrc</code> object, so no row is "
            f"missing from the proxy split.</li>"
        )
    add(
        f"<li><strong>Row count is live.</strong> The corpus query returned {total} rows at "
        "generation time; nothing is padded to a target count.</li>"
    )
    add("</ul>")

    # 8. Per-song detail
    add("<h2>Per-song detail</h2>")
    add(f"<details><summary>Show all {total} rows</summary>")
    add('<div class="scroll">')
    add("<table>")
    ordered = sorted(
        rows,
        key=lambda r: (LRC_ORDER.index(r["lrc_bucket"]), r["song_id"]),
    )
    include_pre = PRE_SNAPSHOT.exists() and "unprovable" not in provable_counts
    head = "<tr><th>song_id</th><th>hash_prefix</th>"
    if include_pre:
        head += "<th>pre-state</th><th>provable bucket</th>"
    head += (
        '<th>lrc bucket</th><th>level</th><th class="num">similarity</th>'
        '<th class="num">new sections</th><th class="num">new lines</th>'
        '<th class="num">lrc lines</th></tr>'
    )
    add(head)
    for row in ordered:
        sim = "—" if row["similarity"] is None else f"{row['similarity']:.3f}"
        cells = (
            f'<td><code>{esc(row["song_id"])}</code></td>'
            f'<td><code>{esc(row["hash_prefix"])}</code></td>'
        )
        if include_pre:
            cells += (
                f'<td>{esc(row["pre_state"])}</td><td>{esc(row["provable"])}</td>'
            )
        cells += (
            f'<td>{esc(row["lrc_bucket"])}</td><td>{esc(row["level"])}</td>'
            f'<td class="num">{sim}</td>'
            f'<td class="num">{row["sections"]}</td>'
            f'<td class="num">{row["new_lines"]}</td>'
            f'<td class="num">{row["lrc_lines"]}</td>'
        )
        add(f'<tr data-song="{esc(row["song_id"])}">{cells}</tr>')
    add("</table>")
    add("</div>")
    add("</details>")

    # 9. Footer
    add("<h2>Regenerate</h2>")
    add(
        "<pre>set -a; . /opt/sow/.env; set +a\n"
        "uv run --project ops/admin-cli --extra admin \\\n"
        "    python ops/admin-cli/scripts/report_structured_lyrics_rebuild.py</pre>"
    )
    add(
        f'<p class="note">Pre-run md5 snapshot: <code>{esc(PRE_SNAPSHOT)}</code> '
        "(rows <code>song_id|md5(coalesce(structured_lyrics,''))</code>). "
        f"Generated {esc(generated_at)}.</p>"
    )
    add("</body>")
    add("</html>")

    document = "\n".join(parts) + "\n"
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(document, encoding="utf-8")
    print(f"wrote {OUTPUT} ({len(document)} chars)")

    # Print the bucket tables for verification.
    for bucket in provable_rows:
        print(f"provable|{bucket}|{provable_counts[bucket]}")
    for bucket in LRC_ORDER:
        print(f"lrc|{bucket}|{lrc_counts[bucket]}")
    print(f"provable_total={col_sum} lrc_total={reported_sum} rows={total}")
    return total


def main() -> int:
    rows = collect_rows()
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    total = render(rows, generated_at)
    if total:
        print("TOTAL_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
