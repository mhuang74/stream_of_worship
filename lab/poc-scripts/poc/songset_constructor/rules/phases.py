"""Theme fusion and phase inference."""

from __future__ import annotations

from .themes import THEMES

THEME_TO_PHASE = {
    "赞美": 1,
    "感恩": 2,
    "敬拜": 3,
    "祈祷": 3,
    "信心": 3,
    "圣灵": 3,
    "奉献": 4,
    "认罪": 4,
    "十字架": 4,
    "差遣": 5,
    "跟随": 5,
    "复兴": 5,
}


def fuse_themes(
    title: dict[str, float],
    lyrics: dict[str, float],
    song_emb: dict[str, float],
    line_emb: dict[str, float],
) -> dict[str, float]:
    weighted_sources = [
        (0.35, title),
        (0.25, lyrics),
        (0.25, song_emb),
        (0.15, line_emb),
    ]
    totals = {theme: 0.0 for theme in THEMES}
    weights = {theme: 0.0 for theme in THEMES}
    for weight, source in weighted_sources:
        if any(value > 0 for value in source.values()):
            for theme in THEMES:
                totals[theme] += weight * source.get(theme, 0.0)
                weights[theme] += weight
    return {
        theme: (totals[theme] / weights[theme] if weights[theme] else 0.0)
        for theme in THEMES
    }


def apply_seasonal_bias(fused: dict[str, float], season: str | None) -> dict[str, float]:
    if season not in {"advent", "christmas", "lent", "easter", "pentecost"}:
        return fused
    biased = dict(fused)
    if season in {"advent", "christmas"}:
        biased["赞美"] = max(biased.get("赞美", 0.0), 0.7)
        biased["感恩"] = max(biased.get("感恩", 0.0), 0.5)
    elif season == "lent":
        biased["认罪"] = max(biased.get("认罪", 0.0), 0.7)
        biased["十字架"] = max(biased.get("十字架", 0.0), 0.65)
    elif season == "easter":
        biased["复兴"] = max(biased.get("复兴", 0.0), 0.65)
        biased["赞美"] = max(biased.get("赞美", 0.0), 0.65)
    elif season == "pentecost":
        biased["圣灵"] = max(biased.get("圣灵", 0.0), 0.75)
    return biased


def infer_phase(fused: dict[str, float], tempo_bpm: float | None = None) -> int:
    if fused and max(fused.values(), default=0.0) > 0:
        theme = max(fused.items(), key=lambda item: (item[1], item[0]))[0]
        if theme == "圣灵" and tempo_bpm is not None and tempo_bpm < 82:
            return 4
        return THEME_TO_PHASE.get(theme, 3)
    if tempo_bpm is None:
        return 3
    if tempo_bpm >= 118:
        return 1
    if tempo_bpm >= 100:
        return 2
    if tempo_bpm >= 84:
        return 3
    return 4


def top_themes(themes: dict[str, float], limit: int = 2) -> list[str]:
    ranked = sorted(themes.items(), key=lambda item: (-item[1], item[0]))
    return [theme for theme, score in ranked[:limit] if score > 0]
