"""Catalog lyrics analyzer for THEME_VOCAB gap detection.

Loads the real SOP.org catalog from the database, tokenizes lyrics using CJK
character bigrams + unigrams, and reports high-frequency words that do not
currently match any theme in ``THEME_VOCAB``. The output is written to
``reports/vocab_gap_analysis.json`` for manual categorization.

Usage::

    uv run --project lab/poc-scripts --extra admin python -m \
        poc.songset_constructor.analyze_vocab_gaps
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Iterable

from poc.songset_constructor.config import RunConfig
from poc.songset_constructor.db import fetch_catalog_pool
from poc.songset_constructor.models import SongCandidate
from poc.songset_constructor.rules.themes import THEME_VOCAB, THEMES, _matches

MIN_FREQUENCY = 5
REPORT_PATH = Path(__file__).resolve().parents[3] / "reports" / "vocab_gap_analysis.json"


def _extract_unigrams(text: str) -> list[str]:
    """Extract CJK character unigrams and ASCII words from text."""
    tokens: list[str] = []
    for char in text:
        if "\u4e00" <= char <= "\u9fff":
            tokens.append(char)
    for word in re.findall(r"[a-zA-Z]+", text):
        if len(word) >= 2:
            tokens.append(word.lower())
    return tokens


def _extract_bigrams(text: str) -> list[str]:
    """Extract CJK character bigrams from text."""
    cjk_chars = [char for char in text if "\u4e00" <= char <= "\u9fff"]
    return [cjk_chars[i] + cjk_chars[i + 1] for i in range(len(cjk_chars) - 1)]


def _tokenize(candidate: SongCandidate) -> list[str]:
    """Tokenize a song's lyrics + title into unigrams and bigrams."""
    tokens: list[str] = []
    title_text = " ".join(
        part for part in [candidate.title or "", candidate.title_pinyin or ""] if part
    )
    tokens.extend(_extract_unigrams(title_text))
    tokens.extend(_extract_bigrams(title_text))
    if candidate.lyrics_raw:
        tokens.extend(_extract_unigrams(candidate.lyrics_raw))
        tokens.extend(_extract_bigrams(candidate.lyrics_raw))
    return tokens


def _word_matches_any_theme(word: str) -> bool:
    """Return True if ``word`` already matches at least one theme term."""
    return any(_matches(word, terms) > 0 for terms in THEME_VOCAB.values())


def _word_matches_theme(word: str, theme: str) -> bool:
    """Return True if ``word`` matches a term in the given theme."""
    return _matches(word, THEME_VOCAB[theme]) > 0


def analyze_pool(pool: Iterable[SongCandidate]) -> dict:
    """Analyze the catalog pool for unmatched high-frequency words.

    Returns a dict with:
    - ``unmatched_words``: list of {word, frequency, songs} sorted by frequency
    - ``matched_words``: count of words that matched existing vocab
    - ``theme_distribution``: current theme distribution for reference
    - ``total_songs``: number of songs analyzed
    """
    pool_list = list(pool)
    word_freq: Counter[str] = Counter()
    word_songs: dict[str, set[str]] = {}
    matched_count = 0

    for candidate in pool_list:
        tokens = _tokenize(candidate)
        seen_in_song: set[str] = set()
        for token in tokens:
            if token in seen_in_song:
                continue
            seen_in_song.add(token)
            word_freq[token] += 1
            word_songs.setdefault(token, set()).add(candidate.song_id)
            if _word_matches_any_theme(token):
                matched_count += 1

    unmatched: list[dict] = []
    for word, freq in word_freq.most_common():
        if freq < MIN_FREQUENCY:
            continue
        if _word_matches_any_theme(word):
            continue
        unmatched.append(
            {
                "word": word,
                "frequency": freq,
                "songs": sorted(word_songs[word])[:10],
            }
        )

    theme_distribution: dict[str, int] = {}
    for theme in THEMES:
        count = sum(
            1
            for candidate in pool_list
            if candidate.lyrics_raw
            and _matches(candidate.lyrics_raw, THEME_VOCAB[theme]) > 0
        )
        theme_distribution[theme] = count

    return {
        "total_songs": len(pool_list),
        "min_frequency": MIN_FREQUENCY,
        "matched_word_occurrences": matched_count,
        "unmatched_words": unmatched,
        "theme_distribution": theme_distribution,
        "theme_vocab_sizes": {theme: len(terms) for theme, terms in THEME_VOCAB.items()},
    }


def main() -> None:
    config = RunConfig(pool_limit=500)
    pool = fetch_catalog_pool(config)
    result = analyze_pool(pool)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Analyzed {result['total_songs']} songs.")
    print(f"Found {len(result['unmatched_words'])} unmatched high-frequency words.")
    print(f"Report written to: {REPORT_PATH}")


if __name__ == "__main__":
    main()
