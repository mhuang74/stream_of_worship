"""Shared utilities for song ID computation."""

import hashlib
import re
import unicodedata

from pypinyin import lazy_pinyin


def _normalize(s: str) -> str:
    """Normalize string for ID computation: NFKC + strip."""
    return unicodedata.normalize("NFKC", (s or "").strip())


def compute_new_song_id(title: str, composer: str, lyricist: str) -> str:
    """Compute the new stable song ID format.

    Format: <pinyin_slug>_<8-hex-hash>
    Hash is computed from: sha256(NFKC(title) + "|" + NFKC(composer) + "|" + NFKC(lyricist))[:8]

    Args:
        title: Song title (Chinese or English)
        composer: Composer name (may be None/empty)
        lyricist: Lyricist name (may be None/empty)

    Returns:
        New content-hash-based song ID
    """
    pinyin_parts = lazy_pinyin(_normalize(title))
    slug = re.sub(r"[^a-z0-9_]", "", "_".join(pinyin_parts).lower())
    payload = f"{_normalize(title)}|{_normalize(composer)}|{_normalize(lyricist)}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
    song_id = f"{slug}_{digest}"
    if len(song_id) > 100:
        song_id = f"{slug[:91]}_{digest}"
    return song_id
