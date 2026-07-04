"""Regenerate committed theme anchor embeddings.

Requires SOW_EMBEDDING_API_KEY and SOW_EMBEDDING_BASE_URL. The output must be
real text-embedding-3-small vectors; this script intentionally fails rather
than writing placeholders when the embedding endpoint is unavailable.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from langchain_openai import OpenAIEmbeddings

ANCHOR_TEXTS = {
    "赞美": "赞美 歌唱 哈利路亚 praise worship joyful song",
    "感恩": "感恩 感谢 恩典 grace thanksgiving thank you Lord",
    "敬拜": "敬拜 尊崇 荣耀 俯伏 worship adore glory",
    "奉献": "奉献 献上 摆上 offering dedicate surrender",
    "认罪": "认罪 悔改 赦免 洁净 repentance confession forgiveness",
    "差遣": "差遣 宣教 传扬 万民 mission send proclaim",
    "信心": "信心 相信 倚靠 盼望 faith trust hope",
    "祈祷": "祷告 祈祷 呼求 垂听 prayer intercession cry out",
    "复兴": "复兴 更新 燃烧 revival renewal awaken",
    "圣灵": "圣灵 充满 灵火 Holy Spirit fill fire",
    "十字架": "十字架 宝血 羔羊 救赎 cross blood lamb redemption",
    "跟随": "跟随 道路 门徒 顺服 follow disciple obedience",
}


def main() -> None:
    api_key = os.environ.get("SOW_EMBEDDING_API_KEY")
    if not api_key:
        raise RuntimeError("SOW_EMBEDDING_API_KEY is required to regenerate theme anchors")
    base_url = os.environ.get("SOW_EMBEDDING_BASE_URL")
    if not base_url:
        raise RuntimeError("SOW_EMBEDDING_BASE_URL is required to regenerate theme anchors")
    model = os.environ.get("SOW_EMBEDDING_MODEL", "text-embedding-3-small")
    embeddings = OpenAIEmbeddings(
        model=model,
        api_key=api_key,
        base_url=base_url,
    )
    vectors = embeddings.embed_documents([ANCHOR_TEXTS[theme] for theme in ANCHOR_TEXTS])
    payload = {
        "model_version": "text-embedding-3-small",
        "dim": len(vectors[0]) if vectors else 0,
        "anchors": {theme: vector for theme, vector in zip(ANCHOR_TEXTS, vectors, strict=True)},
    }
    if payload["dim"] != 1536:
        raise RuntimeError(f"Expected 1536-dim anchors, got {payload['dim']}")
    out = Path(__file__).resolve().parent / "data" / "theme_anchors.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
