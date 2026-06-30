"""Theme classifiers and embedding fusion."""

from __future__ import annotations

import re
from collections import Counter

import numpy as np

from .embeddings import cosine

THEMES = ("赞美", "感恩", "敬拜", "奉献", "认罪", "差遣", "信心", "祈祷", "复兴", "圣灵", "十字架", "跟随")

THEME_VOCAB: dict[str, tuple[str, ...]] = {
    "赞美": ("赞美", "讚美", "歌唱", "欢呼", "hallelujah", "praise", "zan mei"),
    "感恩": ("感恩", "感谢", "謝謝", "恩典", "grace", "thanks", "gan en"),
    "敬拜": ("敬拜", "俯伏", "尊崇", "荣耀", "worship", "adore", "jing bai"),
    "奉献": ("奉献", "献上", "擺上", "祭", "offering", "dedicate", "feng xian"),
    "认罪": ("认罪", "悔改", "赦免", "洁净", "forgive", "repent", "ren zui"),
    "差遣": ("差遣", "宣教", "传扬", "万民", "send", "mission", "chai qian"),
    "信心": ("信心", "相信", "倚靠", "盼望", "faith", "trust", "xin xin"),
    "祈祷": ("祷告", "祈祷", "呼求", "垂听", "prayer", "pray", "qi dao"),
    "复兴": ("复兴", "復興", "更新", "燃烧", "revival", "renew", "fu xing"),
    "圣灵": ("圣灵", "聖靈", "灵火", "充满", "holy spirit", "sheng ling"),
    "十字架": ("十字架", "宝血", "羔羊", "救赎", "cross", "blood", "shi zi jia"),
    "跟随": ("跟随", "跟從", "道路", "门徒", "follow", "disciple", "gen sui"),
}


def _matches(text: str, terms: tuple[str, ...]) -> int:
    lowered = text.lower()
    return sum(1 for term in terms if re.search(re.escape(term.lower()), lowered))


def classify_title_themes(title: str | None, title_pinyin: str | None = None) -> dict[str, float]:
    text = " ".join(part for part in [title or "", title_pinyin or ""] if part)
    hits = {theme: _matches(text, terms) for theme, terms in THEME_VOCAB.items()}
    max_hits = max(hits.values(), default=0)
    if max_hits == 0:
        return {theme: 0.0 for theme in THEMES}
    return {theme: value / max_hits for theme, value in hits.items()}


def classify_lyrics_themes(lyrics_raw: str | None) -> dict[str, float]:
    if not lyrics_raw:
        return {theme: 0.0 for theme in THEMES}
    lines = [line.strip() for line in lyrics_raw.splitlines() if line.strip()]
    windows = [" ".join(lines[i : i + 2]) for i in range(max(1, len(lines) - 1))]
    counter: Counter[str] = Counter()
    for window in windows or [lyrics_raw]:
        for theme, terms in THEME_VOCAB.items():
            counter[theme] += _matches(window, terms)
    total = sum(counter.values())
    if total == 0:
        return {theme: 0.0 for theme in THEMES}
    return {theme: counter[theme] / total for theme in THEMES}


def _normalise_cosine_scores(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {theme: 0.0 for theme in THEMES}
    min_score = min(scores.values())
    shifted = {theme: max(0.0, score - min_score) for theme, score in scores.items()}
    max_score = max(shifted.values(), default=0.0)
    if max_score <= 1e-9:
        return {theme: 0.0 for theme in THEMES}
    return {theme: value / max_score for theme, value in shifted.items()}


def classify_embedding_themes(
    song_vec: list[float] | np.ndarray | None,
    line_vecs: list[list[float]] | list[np.ndarray] | None,
    theme_anchors: dict[str, np.ndarray],
) -> tuple[dict[str, float], dict[str, float]]:
    song_scores = {theme: cosine(song_vec, anchor) for theme, anchor in theme_anchors.items()}
    line_scores: dict[str, float] = {}
    for theme, anchor in theme_anchors.items():
        line_scores[theme] = max((cosine(vec, anchor) for vec in (line_vecs or [])), default=0.0)
    return (_normalise_cosine_scores(song_scores), _normalise_cosine_scores(line_scores))
