"""One-off key detection audit against the Stream of Worship catalog database."""

from __future__ import annotations

import argparse
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
import psycopg.rows


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "reports" / "key_detection_algorithm_review.md"
ENV_FILE = REPO_ROOT / ".env.local"

KEY_QUERY = """
SELECT
    s.id AS song_id,
    s.title,
    s.album_name,
    s.musical_key AS nominal_key,
    r.hash_prefix,
    r.original_filename,
    r.musical_key AS detected_key,
    r.musical_mode,
    r.key_confidence,
    r.analysis_status,
    r.duration_seconds,
    r.tempo_bpm
FROM recordings r
JOIN songs s ON s.id = r.song_id
WHERE s.deleted_at IS NULL
  AND r.deleted_at IS NULL
  AND r.analysis_status IN ('completed', 'partial')
  AND NULLIF(BTRIM(s.musical_key), '') IS NOT NULL
  AND NULLIF(BTRIM(r.musical_key), '') IS NOT NULL
ORDER BY s.id, r.hash_prefix;
"""

COUNT_QUERY = """
SELECT
    COUNT(*) FILTER (
        WHERE NULLIF(BTRIM(s.musical_key), '') IS NOT NULL
          AND NULLIF(BTRIM(r.musical_key), '') IS NOT NULL
    ) AS compared_candidate_rows,
    COUNT(*) FILTER (
        WHERE NULLIF(BTRIM(s.musical_key), '') IS NULL
    ) AS missing_nominal_key_rows,
    COUNT(*) FILTER (
        WHERE NULLIF(BTRIM(r.musical_key), '') IS NULL
    ) AS missing_detected_key_rows,
    COUNT(*) FILTER (
        WHERE NULLIF(BTRIM(s.musical_key), '') IS NULL
           OR NULLIF(BTRIM(r.musical_key), '') IS NULL
    ) AS missing_data_rows,
    COUNT(*) AS active_analyzed_rows
FROM recordings r
JOIN songs s ON s.id = r.song_id
WHERE s.deleted_at IS NULL
  AND r.deleted_at IS NULL
  AND r.analysis_status IN ('completed', 'partial');
"""

PITCH_CLASSES = {
    "C": 0,
    "B#": 0,
    "C#": 1,
    "DB": 1,
    "D": 2,
    "D#": 3,
    "EB": 3,
    "E": 4,
    "FB": 4,
    "E#": 5,
    "F": 5,
    "F#": 6,
    "GB": 6,
    "G": 7,
    "G#": 8,
    "AB": 8,
    "A": 9,
    "A#": 10,
    "BB": 10,
    "B": 11,
    "CB": 11,
}
CANONICAL_KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_DISPLAY = {1: "Db", 3: "Eb", 6: "Gb", 8: "Ab", 10: "Bb"}
FULL_WIDTH_TRANSLATION = str.maketrans(
    {
        "Ａ": "A",
        "Ｂ": "B",
        "Ｃ": "C",
        "Ｄ": "D",
        "Ｅ": "E",
        "Ｆ": "F",
        "Ｇ": "G",
        "ａ": "a",
        "ｂ": "b",
        "ｃ": "c",
        "ｄ": "d",
        "ｅ": "e",
        "ｆ": "f",
        "ｇ": "g",
    }
)
CONFIDENCE_BANDS = [
    ("< 0.20", None, 0.20),
    ("0.20-0.39", 0.20, 0.40),
    ("0.40-0.59", 0.40, 0.60),
    ("0.60-0.79", 0.60, 0.80),
    (">= 0.80", 0.80, None),
]


@dataclass(frozen=True)
class ParsedKey:
    raw: str
    token: str
    pitch_class: int

    @property
    def canonical(self) -> str:
        return CANONICAL_KEYS[self.pitch_class]

    @property
    def friendly(self) -> str:
        return FLAT_DISPLAY.get(self.pitch_class, self.canonical)


def load_env_file(path: Path = ENV_FILE) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def normalize_key_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().translate(FULL_WIDTH_TRANSLATION)
    text = text.replace("♯", "#").replace("＃", "#").replace("♭", "b").replace("♮", "")
    text = text.replace("升", "#").replace("降", "b")
    text = re.sub(r"\s+", "", text)
    return text


def parse_key(value: Any) -> ParsedKey | None:
    text = normalize_key_text(value)
    if not text:
        return None

    # Prefer obvious western key spellings. This covers C#, Db, Bb major, F#m, etc.
    match = re.search(r"([A-Ga-g])([#b]?)(?:maj|min|major|minor|m|大|小|調|调)?", text)
    if not match:
        # Also handle Chinese accidental prefixes such as bB or #F after normalization.
        match = re.search(r"([#b])([A-Ga-g])", text)
        if not match:
            return None
        accidental, letter = match.groups()
        token = f"{letter.upper()}{accidental}"
    else:
        letter, accidental = match.groups()
        token = f"{letter.upper()}{accidental}"

    pitch_class = PITCH_CLASSES.get(token.upper())
    if pitch_class is None:
        return None
    return ParsedKey(raw=str(value), token=token, pitch_class=pitch_class)


def semitone_distance(a: int, b: int) -> int:
    diff = abs(a - b) % 12
    return min(diff, 12 - diff)


def confidence_band(confidence: Any) -> str:
    if confidence is None:
        return "missing"
    value = float(confidence)
    for label, lower, upper in CONFIDENCE_BANDS:
        if (lower is None or value >= lower) and (upper is None or value < upper):
            return label
    return "missing"


def pct(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "n/a"
    return f"{(numerator / denominator) * 100:.1f}%"


def md_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def run_self_tests() -> None:
    cases = [
        ("C#", "Db", True),
        ("Bb", "A#", True),
        ("F# minor", "Gb", True),
        ("E大調", "E minor", True),
        ("Ｄ-F", "D", True),
        ("", "C", False),
        (None, "C", False),
        ("unknown", "C", False),
    ]
    for left, right, expected in cases:
        parsed_left = parse_key(left)
        parsed_right = parse_key(right)
        actual = (
            parsed_left is not None
            and parsed_right is not None
            and parsed_left.pitch_class == parsed_right.pitch_class
        )
        if actual != expected:
            raise AssertionError(f"normalization failed for {left!r} vs {right!r}: {actual}")


def fetch_rows(database_url: str) -> tuple[dict[str, int], list[dict[str, Any]]]:
    with psycopg.connect(database_url, row_factory=psycopg.rows.dict_row) as conn:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            counts = conn.execute(COUNT_QUERY).fetchone()
            rows = conn.execute(KEY_QUERY).fetchall()
    if counts is None:
        raise RuntimeError("count query returned no row")
    return dict(counts), [dict(row) for row in rows]


def analyze_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    matched: list[dict[str, Any]] = []
    mismatched: list[dict[str, Any]] = []
    unparseable: list[dict[str, Any]] = []
    distance_counts: Counter[int] = Counter()
    pair_counts: Counter[tuple[str, str]] = Counter()
    band_counts: dict[str, Counter[str]] = defaultdict(Counter)

    for row in rows:
        nominal = parse_key(row["nominal_key"])
        detected = parse_key(row["detected_key"])
        if nominal is None or detected is None:
            enriched = dict(row)
            enriched["nominal_parsed"] = nominal
            enriched["detected_parsed"] = detected
            unparseable.append(enriched)
            band_counts[confidence_band(row["key_confidence"])]["unparseable"] += 1
            continue

        distance = semitone_distance(nominal.pitch_class, detected.pitch_class)
        enriched = dict(row)
        enriched["nominal_parsed"] = nominal
        enriched["detected_parsed"] = detected
        enriched["distance"] = distance

        status = "match" if distance == 0 else "mismatch"
        band_counts[confidence_band(row["key_confidence"])][status] += 1
        distance_counts[distance] += 1

        if distance == 0:
            matched.append(enriched)
        else:
            mismatched.append(enriched)
            pair_counts[(nominal.friendly, detected.friendly)] += 1

    return {
        "matched": matched,
        "mismatched": mismatched,
        "unparseable": unparseable,
        "distance_counts": distance_counts,
        "pair_counts": pair_counts,
        "band_counts": band_counts,
    }


def render_report(counts: dict[str, int], analysis: dict[str, Any]) -> str:
    matched = analysis["matched"]
    mismatched = analysis["mismatched"]
    unparseable = analysis["unparseable"]
    distance_counts = analysis["distance_counts"]
    pair_counts = analysis["pair_counts"]
    band_counts = analysis["band_counts"]

    comparable = len(matched) + len(mismatched)
    candidate_rows = counts["compared_candidate_rows"]
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    lines: list[str] = [
        "# Key Detection Algorithm Review",
        "",
        f"Generated: {generated_at}",
        "",
        "## Scope",
        "",
        "This audit compares `recordings.musical_key` from the analysis service against "
        "`songs.musical_key` from the scraped catalog for active analyzed recordings. The "
        "headline metric is pitch-class/root only: enharmonic equivalents match, and "
        "`recordings.musical_mode` is ignored.",
        "",
        "The nominal scraped key is useful reference data, but it is not guaranteed ground truth.",
        "",
        "## Query",
        "",
        "- `songs.deleted_at IS NULL`",
        "- `recordings.deleted_at IS NULL`",
        "- `recordings.analysis_status IN ('completed', 'partial')`",
        "- main comparison requires non-empty nominal and detected keys",
        "",
        "## Counts",
        "",
        "| Metric | Rows |",
        "| --- | ---: |",
        f"| Active analyzed rows | {counts['active_analyzed_rows']} |",
        f"| Candidate rows with both keys present | {candidate_rows} |",
        f"| Included comparable rows | {comparable} |",
        f"| Exact pitch-class matches | {len(matched)} |",
        f"| Pitch-class mismatches | {len(mismatched)} |",
        f"| Unparseable key rows | {len(unparseable)} |",
        f"| Excluded missing-data rows | {counts['missing_data_rows']} |",
        f"| Rows missing nominal scraped key | {counts['missing_nominal_key_rows']} |",
        f"| Rows missing detected key | {counts['missing_detected_key_rows']} |",
        "",
        "## Headline Accuracy",
        "",
        f"- Match rate: {pct(len(matched), comparable)} ({len(matched)} / {comparable})",
        f"- Mismatch rate: {pct(len(mismatched), comparable)} ({len(mismatched)} / {comparable})",
        f"- Unparseable candidate rate: {pct(len(unparseable), candidate_rows)} "
        f"({len(unparseable)} / {candidate_rows})",
        "",
        "## Match Rate by Key Confidence",
        "",
        "| Confidence band | Comparable | Matches | Mismatches | Unparseable | Match rate |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]

    band_labels = [label for label, _, _ in CONFIDENCE_BANDS] + ["missing"]
    for label in band_labels:
        bucket = band_counts.get(label, Counter())
        matches = bucket["match"]
        mismatches = bucket["mismatch"]
        unparseable_count = bucket["unparseable"]
        bucket_comparable = matches + mismatches
        lines.append(
            f"| {label} | {bucket_comparable} | {matches} | {mismatches} | "
            f"{unparseable_count} | {pct(matches, bucket_comparable)} |"
        )

    lines.extend(
        [
            "",
            "## Mismatch Distance Distribution",
            "",
            "| Shortest distance | Rows | Share of comparable rows |",
            "| ---: | ---: | ---: |",
        ]
    )
    for distance in range(0, 7):
        count = distance_counts[distance]
        lines.append(f"| {distance} semitones | {count} | {pct(count, comparable)} |")

    lines.extend(
        [
            "",
            "## Most Common Nominal-to-Detected Mismatch Pairs",
            "",
            "| Nominal root | Detected root | Rows |",
            "| --- | --- | ---: |",
        ]
    )
    for (nominal, detected), count in pair_counts.most_common(20):
        lines.append(f"| {nominal} | {detected} | {count} |")

    high_conf = sorted(
        mismatched,
        key=lambda row: (row["key_confidence"] is None, -(row["key_confidence"] or -1.0)),
    )[:20]
    lines.extend(
        [
            "",
            "## High-Confidence Mismatch Examples",
            "",
            "| Confidence | Distance | Nominal | Detected | Mode | Song | Recording | File |",
            "| ---: | ---: | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in high_conf:
        nominal = row["nominal_parsed"].friendly
        detected = row["detected_parsed"].friendly
        lines.append(
            f"| {row['key_confidence']:.3f} | {row['distance']} | {nominal} "
            f"({md_escape(row['nominal_key'])}) | {detected} "
            f"({md_escape(row['detected_key'])}) | {md_escape(row['musical_mode'])} | "
            f"{md_escape(row['title'])} | {md_escape(row['hash_prefix'])} | "
            f"{md_escape(row['original_filename'])} |"
        )

    lines.extend(
        [
            "",
            "## Unparseable Nominal or Detected Keys",
            "",
            "| Nominal key | Detected key | Song | Recording |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in unparseable[:30]:
        lines.append(
            f"| {md_escape(row['nominal_key'])} | {md_escape(row['detected_key'])} | "
            f"{md_escape(row['title'])} | {md_escape(row['hash_prefix'])} |"
        )
    if len(unparseable) > 30:
        lines.append(f"| ... | ... | {len(unparseable) - 30} more rows | ... |")

    low_conf_mismatches = [
        row for row in mismatched if row["key_confidence"] is not None and row["key_confidence"] < 0.4
    ]
    relative_like = sum(row["distance"] in {3, 4} for row in mismatched)
    tritone_like = sum(row["distance"] == 6 for row in mismatched)
    off_by_one = sum(row["distance"] == 1 for row in mismatched)

    lines.extend(
        [
            "",
            "## Diagnostic Findings",
            "",
            f"- Low-confidence mismatches below 0.40: {len(low_conf_mismatches)} "
            f"({pct(len(low_conf_mismatches), len(mismatched))} of mismatches).",
            f"- Relative-major/minor-style distances (3 or 4 semitones): {relative_like} "
            f"({pct(relative_like, len(mismatched))} of mismatches).",
            f"- Neighbor-key distances (1 semitone): {off_by_one} "
            f"({pct(off_by_one, len(mismatched))} of mismatches).",
            f"- Tritone-distance mismatches (6 semitones): {tritone_like} "
            f"({pct(tritone_like, len(mismatched))} of mismatches).",
            "",
            "The current implementation in `ops/analysis-service/src/sow_analysis/workers/analyzer.py` "
            "loads mono audio, computes `librosa.feature.chroma_cqt`, averages chroma over the full "
            "track, then selects the best correlation among 24 rolled major/minor Krumhansl-Schmuckler "
            "profiles. That design is simple and deterministic, but full-track averaging makes it "
            "sensitive to non-tonic intros/outros, medleys, modulations, extended bridges, dense vocal "
            "arrangements, and live recordings where accompaniment energy does not strongly represent "
            "the sung tonic.",
            "",
            "## Ranked Fix Recommendations",
            "",
            "1. Segment-aware key voting: compute chroma/key per section or sliding window, weight by "
            "stable high-energy vocal/accompaniment sections, and choose a consensus tonic instead of "
            "one full-track average.",
            "2. Persist diagnostic scores: store top-N key candidates, score margin between first and "
            "second candidate, and an algorithm version. The current single correlation value does not "
            "show ambiguity well enough for thresholding or review.",
            "3. Add confidence policy: mark low-margin or low-correlation keys as unverified instead of "
            "publishing a hard key. Use the confidence-band results above to set the first threshold.",
            "4. Improve reference normalization and review UX: normalize scraped keys into pitch-class "
            "columns and surface high-confidence mismatches for manual correction because scraped keys "
            "are nominal, not guaranteed ground truth.",
            "5. Test alternate chroma extraction: compare CQT chroma with HPCP or beat-synchronous "
            "chroma, and test stem-informed analysis where vocals or accompaniment dominate failures.",
            "",
            "## Validation",
            "",
            "- Normalization self-tests passed for `C# == Db`, `Bb == A#`, `F# minor == Gb`, "
            "mode-insensitive root matching, and empty/null/unrecognized exclusions.",
            "- Database access used a read-only transaction.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the key detection audit report.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    run_self_tests()
    load_env_file()
    database_url = os.environ.get("SOW_DATABASE_URL")
    if not database_url:
        raise SystemExit("SOW_DATABASE_URL is required in the environment or .env.local")

    counts, rows = fetch_rows(database_url)
    report = render_report(counts, analyze_rows(rows))
    args.output.write_text(report, encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
