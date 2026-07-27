"""Theme classifiers and embedding fusion."""

from __future__ import annotations

import re
from collections import Counter

import numpy as np

from .embeddings import cosine

THEMES = ("讚美", "感恩", "敬拜", "奉獻", "認罪", "差遣", "信心", "祈禱", "復興", "聖靈", "十字架", "跟隨")

THEME_VOCAB: dict[str, tuple[str, ...]] = {
    "讚美": ("讚美", "歌唱", "歡呼", "hallelujah", "praise", "zan mei"),
    "感恩": ("感恩", "感謝", "謝謝", "恩典", "稱頌", "恩惠", "報答", "grace", "thanks", "gan en"),
    "敬拜": ("敬拜", "俯伏", "尊崇", "榮耀", "worship", "adore", "jing bai"),
    "奉獻": ("奉獻", "獻上", "擺上", "祭", "獻祭", "當作活祭", "全人", "offering", "dedicate", "feng xian"),
    "認罪": ("認罪", "悔改", "赦免", "潔淨", "罪孽", "洗淨", "軟弱", "虧欠", "過犯", "寶血", "forgive", "repent", "ren zui"),
    "差遣": ("差遣", "宣教", "傳揚", "萬民", "使命", "福音", "見證", "出去", "大使命", "傳道", "send", "mission", "chai qian"),
    "信心": ("信心", "相信", "倚靠", "盼望", "faith", "trust", "xin xin"),
    "祈禱": ("禱告", "祈禱", "呼求", "垂聽", "懇求", "代求", "仰望", "祈求", "prayer", "pray", "qi dao"),
    "復興": ("復興", "更新", "燃燒", "覺醒", "澆灌", "甦醒", "靈火", "復活", "更新變化", "revival", "renew", "fu xing"),
    "聖靈": ("聖靈", "靈火", "充滿", "澆灌", "恩膏", "能力", "holy spirit", "sheng ling"),
    "十字架": ("十字架", "寶血", "羔羊", "救贖", "受苦", "釘痕", "捨命", "cross", "blood", "shi zi jia"),
    "跟隨": ("跟隨", "跟從", "道路", "門徒", "順服", "背十字架", "效法", "follow", "disciple", "gen sui"),
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
