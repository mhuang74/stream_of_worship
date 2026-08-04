#!/usr/bin/env python3
"""Enrich raw pool with fused themes, inferred phase, and secondary phases.

Drops songs missing both tempo and key metadata.

Usage:
    cat raw_pool.json | uv run --project ops/admin-cli --extra admin --extra constructor python enrich_pool.py [--season christmas]
    uv run --project ops/admin-cli --extra admin --extra constructor python enrich_pool.py --input raw_pool.json [--season christmas]

Input: JSON array of raw SongCandidate objects (from fetch_pool.py).
Output: JSON array of enriched SongCandidate objects to stdout.
Enrichment summary printed to stderr.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
ADMIN_CLI_SRC = PROJECT_ROOT / "ops" / "admin-cli" / "src"
if str(ADMIN_CLI_SRC) not in sys.path:
    sys.path.insert(0, str(ADMIN_CLI_SRC))


def _shannon_entropy(counts: list[int]) -> float:
    values = [c for c in counts if c > 0]
    total = sum(values)
    if total <= 0:
        return 0.0
    return -sum((c / total) * math.log2(c / total) for c in values)


def main() -> None:
    parser = argparse.ArgumentParser(description="Enrich pool with themes, phases, and secondary phases")
    parser.add_argument("--input", type=Path, default=None, help="Input JSON file (default: stdin)")
    parser.add_argument(
        "--season",
        type=str,
        default=None,
        choices=["advent", "christmas", "lent", "easter", "pentecost"],
        help="Seasonal bias",
    )
    args = parser.parse_args()

    if args.input:
        raw_data = json.loads(args.input.read_text(encoding="utf-8"))
    else:
        raw_data = json.load(sys.stdin)

    from stream_of_worship.admin.songset_constructor.models import SongCandidate
    from stream_of_worship.admin.songset_constructor.rules.phases import (
        apply_seasonal_bias,
        fuse_themes,
        infer_phase,
        infer_secondary_phases,
    )
    from stream_of_worship.admin.songset_constructor.rules.themes import (
        THEMES,
        classify_lyrics_themes,
        classify_title_themes,
        normalise_cosine_scores,
    )

    raw_pool = [SongCandidate.model_validate(item) for item in raw_data]
    loaded_size = len(raw_pool)

    enriched: list[SongCandidate] = []
    dropped = 0

    for candidate in raw_pool:
        if candidate.tempo_bpm is None and candidate.musical_key is None:
            dropped += 1
            continue

        title = classify_title_themes(candidate.title, candidate.title_pinyin)
        lyrics = classify_lyrics_themes(candidate.lyrics_raw)
        song_emb = normalise_cosine_scores(candidate.song_theme_scores_raw)
        line_emb = normalise_cosine_scores(candidate.line_theme_scores_raw)
        fused = apply_seasonal_bias(fuse_themes(title, lyrics, song_emb, line_emb), args.season)
        primary_phase = infer_phase(fused, candidate.tempo_bpm)
        secondary = infer_secondary_phases(fused, primary_phase, candidate.tempo_bpm)

        enriched.append(
            candidate.model_copy(
                update={
                    "themes": fused,
                    "phase": primary_phase,
                    "secondary_phases": secondary,
                    "is_hymn": candidate.album_series == "HYMN",
                }
            )
        )

    enriched_size = len(enriched)

    # Enrichment summary to stderr
    phase_counts: Counter[int] = Counter(c.phase for c in enriched)
    phase_dist = ", ".join(f"P{p}={phase_counts.get(p, 0)}" for p in range(1, 6))

    theme_inferred = sum(1 for c in enriched if c.themes and max(c.themes.values(), default=0.0) > 0)
    tempo_fallback = enriched_size - theme_inferred

    title_hits = sum(
        1 for c in enriched if any(v > 0 for v in classify_title_themes(c.title, c.title_pinyin).values())
    )
    lyrics_hits = sum(
        1 for c in enriched if any(v > 0 for v in classify_lyrics_themes(c.lyrics_raw).values())
    )

    # Theme entropy
    dominant_themes: Counter[str] = Counter()
    for c in enriched:
        if c.themes:
            positive = {t: s for t, s in c.themes.items() if s > 0}
            if positive:
                dominant = max(positive.items(), key=lambda item: (item[1], item[0]))[0]
                dominant_themes[dominant] += 1
    theme_entropy = _shannon_entropy(list(dominant_themes.values()))
    max_theme_entropy = math.log2(len(THEMES)) if THEMES else 0.0

    print(f"Pool: {loaded_size} loaded → {enriched_size} enriched ({dropped} dropped)", file=sys.stderr)
    print(f"Phase distribution: {phase_dist}", file=sys.stderr)
    print(
        f"Theme inference: {theme_inferred} from themes, {tempo_fallback} from tempo fallback",
        file=sys.stderr,
    )
    print(f"Title hits: {title_hits}/{enriched_size}, Lyrics hits: {lyrics_hits}/{enriched_size}", file=sys.stderr)
    print(f"Theme entropy: {theme_entropy:.2f} bits (max {max_theme_entropy:.3f})", file=sys.stderr)

    json.dump([c.model_dump(mode="json") for c in enriched], sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
