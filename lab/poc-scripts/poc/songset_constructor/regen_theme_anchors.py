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
    "讚美": (
        "我們要讚美耶和華，用歡呼歌唱讚美他的名。"
        "哈利路亞，萬民都要來讚美主。"
        "Praise the Lord with joyful songs, hallelujah."
    ),
    "感恩": (
        "主啊，我感恩感謝你的恩典，稱頌你的恩惠。"
        "我要報答你的慈愛，因你的恩典永遠長存。"
        "Thank you Lord for your grace and thanksgiving."
    ),
    "敬拜": (
        "我們敬拜你，俯伏在你面前，尊崇你的榮耀。"
        "主啊，你配得一切敬拜與尊崇。"
        "We worship and adore you, glory to your name."
    ),
    "奉獻": (
        "主啊，我奉獻獻上自己當作活祭，全人擺上為你。"
        "我們獻祭為感恩，將一切獻給你。"
        "I offer and dedicate myself as a living sacrifice to you."
    ),
    "認罪": (
        "主啊，我認罪悔改，求你赦免我的罪孽，用寶血洗淨我一切的軟弱。"
        "我在你面前承認我的虧欠和過犯，求你潔淨我的心。"
        "Lord, I confess and repent, forgive my sins and cleanse me."
    ),
    "差遣": (
        "主啊，差遣我出去宣教傳揚你的福音，為萬民作見證。"
        "這是我的使命，遵行大使命，傳道到地極。"
        "Send me out to proclaim the gospel, mission to all nations."
    ),
    "信心": (
        "主啊，我以信心相信你，倚靠你的應許，心中充滿盼望。"
        "求你加添我的信心，讓我全心信靠你。"
        "Faith and trust in you, hope in your promises."
    ),
    "祈禱": (
        "主啊，我禱告祈禱呼求你，求你垂聽我的懇求。"
        "我代求仰望你，祈求你的旨意成就。"
        "Prayer and intercession, cry out to the Lord who hears."
    ),
    "復興": (
        "求主復興你的教會，更新我們的心，澆灌你的靈。"
        "願聖靈甦醒我們，覺醒我們沉睡的靈，燃燒復活的火。"
        "Revive and renew us, pour out your Spirit, awaken our souls."
    ),
    "聖靈": (
        "聖靈充滿我們，靈火澆灌，賜下恩膏與能力。"
        "願聖靈的能力臨到我們，彰顯你的榮耀。"
        "Holy Spirit fill us, pour out your fire and anointing."
    ),
    "十字架": (
        "耶穌在十字架上捨命，寶血救贖我們，羔羊的受苦與釘痕。"
        "主啊，你為我們受苦，用寶血買贖我們。"
        "The cross, the blood of the Lamb, redemption through suffering."
    ),
    "跟隨": (
        "我要跟隨跟從主，走你的道路，作你的門徒。"
        "我願順服背十字架，效法你的樣式。"
        "Follow and obey, take up the cross, be a disciple."
    ),
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
