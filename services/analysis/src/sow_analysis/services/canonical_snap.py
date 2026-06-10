"""Snap ASR phrases to canonical lyric lines."""

from __future__ import annotations

import re
from dataclasses import dataclass

from difflib import SequenceMatcher

try:
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover - dependency is declared for the service image

    class _FallbackFuzz:
        @staticmethod
        def ratio(a: str, b: str) -> float:
            return SequenceMatcher(None, a, b).ratio() * 100.0

    fuzz = _FallbackFuzz()

try:
    from zhconv import convert
except ImportError:  # pragma: no cover - dependency is declared for the service image

    def convert(text: str, _target: str) -> str:
        return text


from .qwen3_asr_client import Qwen3AsrResult, Qwen3AsrSegment, Qwen3AsrWord


@dataclass
class SnappedPhrase:
    text: str
    start: float
    end: float
    confidence: float
    snapped: bool
    asr_text: str


def _normalize(text: str) -> str:
    text = convert(text, "zh-tw")
    text = re.sub(r"[\s，。！？、,.!?;；:：'\"“”‘’（）()\[\]【】\-]", "", text)
    return text.lower()


def _canonical_lines(lyrics_text: str) -> list[str]:
    return [line.strip() for line in lyrics_text.splitlines() if line.strip()]


def snap_qwen3_asr_to_canonical(
    result: Qwen3AsrResult,
    lyrics_text: str,
    threshold: float,
) -> list[SnappedPhrase]:
    """Snap Qwen ASR phrases to canonical lyrics without deduplicating repeats."""
    canonical = _canonical_lines(lyrics_text)
    phrases = _phrases_from_words(result.words, canonical) if result.words else []
    if not phrases:
        phrases = [
            SnappedPhrase(s.text, s.start, s.end, 0.0, False, s.text)
            for s in result.segments
            if s.text.strip()
        ]

    snapped: list[SnappedPhrase] = []
    search_start = 0
    for phrase in phrases:
        best_line = ""
        best_score = 0.0
        best_index = search_start
        phrase_norm = _normalize(phrase.text)
        if phrase_norm:
            for index in range(search_start, len(canonical)):
                score = fuzz.ratio(phrase_norm, _normalize(canonical[index])) / 100.0
                if score > best_score:
                    best_score = score
                    best_line = canonical[index]
                    best_index = index
            if best_score < threshold:
                for index, line in enumerate(canonical):
                    score = fuzz.ratio(phrase_norm, _normalize(line)) / 100.0
                    if score > best_score:
                        best_score = score
                        best_line = line
                        best_index = index

        if best_line and best_score >= threshold:
            snapped.append(
                SnappedPhrase(best_line, phrase.start, phrase.end, best_score, True, phrase.text)
            )
            search_start = min(best_index + 1, len(canonical) - 1)
        else:
            snapped.append(
                SnappedPhrase(phrase.text, phrase.start, phrase.end, best_score, False, phrase.text)
            )
    return snapped


def _phrases_from_words(words: list[Qwen3AsrWord], canonical: list[str]) -> list[SnappedPhrase]:
    if not words:
        return []
    target_lengths = [max(2, len(_normalize(line))) for line in canonical] or [12]
    phrases: list[SnappedPhrase] = []
    cursor = 0
    bucket: list[Qwen3AsrWord] = []
    norm_len = 0
    for word in words:
        bucket.append(word)
        norm_len += len(_normalize(word.text))
        target = target_lengths[min(cursor, len(target_lengths) - 1)]
        if norm_len >= target:
            phrases.append(_bucket_to_phrase(bucket))
            bucket = []
            norm_len = 0
            cursor += 1
    if bucket:
        phrases.append(_bucket_to_phrase(bucket))
    return phrases


def _bucket_to_phrase(bucket: list[Qwen3AsrWord]) -> SnappedPhrase:
    return SnappedPhrase(
        text="".join(w.text for w in bucket).strip(),
        start=bucket[0].start,
        end=bucket[-1].end,
        confidence=0.0,
        snapped=False,
        asr_text="".join(w.text for w in bucket).strip(),
    )
