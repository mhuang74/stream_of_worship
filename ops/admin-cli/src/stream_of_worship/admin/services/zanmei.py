"""Scraper for structured lyrics from zanmei.ai (爱赞美).

zanmei.ai is a Chinese worship song library that publishes lyrics with
section tags (``[Verse]``, ``[Pre-Chorus]``, ``[Chorus]`` …). This module
searches the site by song title + band name, resolves the top result, and
returns the raw lyrics text from the song page's ``<pre id="lyric_text">``
block. The text is already section-tagged, so it can be fed directly into
:func:`stream_of_worship.admin.services.structured_lyrics.parse_structured_lyrics_smart`.

All network access goes through :func:`_fetch`; tests patch it.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

try:
    from opencc import OpenCC
except ImportError:  # pragma: no cover - optional runtime dependency
    OpenCC = None

logger = logging.getLogger(__name__)

BASE_URL = "https://www.zanmei.ai"
_SEARCH_PATH = "/search/song/"
_SONG_PATH = "/song/{song_id}.html"
_LYRIC_PRE_ID = "lyric_text"
_SONG_HREF_RE = re.compile(r"/song/(\d+)\.html")
_REQUEST_TIMEOUT = 20
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

# zanmei.ai publishes lyrics in Simplified Chinese; the app canonicalises to
# Traditional. Converter is built lazily once and reused (OpenCC init is slow).
_converter_cache: dict[str, object] = {}


# OpenCC's S2T dictionary maps the worship honorific 祢 ("You", for God) to
# 禰, but the app keeps 祢 for God across both Simplified and Traditional.
# Sentinel-protect it through conversion so it is preserved verbatim instead
# of becoming 禰. The private-use sentinel is not in any OpenCC dictionary, so
# it passes through untouched.
_GOD_YOU_PROTECT = "\ue000"


def _to_traditional(text: str) -> str:
    """Convert Simplified Chinese ``text`` to Traditional Chinese.

    The OpenCC S2T conversion is idempotent on already-Traditional input, so
    this is safe to run unconditionally. ``祢`` (the honorific "You" for God)
    is preserved as-is rather than mapped to ``禰``. If the converter is
    unavailable or fails, logs a warning and falls back to the original
    ``text`` so that a conversion problem never blocks lyrics persistence.
    """
    converter = _converter_cache.get("s2t")
    if converter is None:
        if OpenCC is None:
            logger.warning("opencc-python-reimplemented not installed; skipping S2T conversion")
            return text
        converter = OpenCC("s2t")
        _converter_cache["s2t"] = converter
    try:
        protected = text.replace("祢", _GOD_YOU_PROTECT)
        converted = converter.convert(protected)
        return converted.replace(_GOD_YOU_PROTECT, "祢")
    except Exception:
        # Never block persistence on a conversion failure; keep the original.
        logger.warning("S2T lyrics conversion failed; storing original text", exc_info=True)
        return text


@dataclass
class ZanmeiSearchResult:
    """One search hit from zanmei.ai."""

    song_id: str
    title: str
    artist: str | None = None
    album: str | None = None
    href: str = ""


def _fetch(url: str) -> str:
    """GET a zanmei.ai URL and return its HTML text.

    Raises RuntimeError on HTTP failure so callers can treat network errors
    uniformly.
    """
    try:
        resp = requests.get(
            url,
            timeout=_REQUEST_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Failed to fetch zanmei.ai page {url}: {e}") from e
    return resp.text


def _parse_search_results(html: str) -> list[ZanmeiSearchResult]:
    """Parse zanmei search-result table rows into ZanmeiSearchResult."""
    soup = BeautifulSoup(html, "lxml")
    results: list[ZanmeiSearchResult] = []
    for row in soup.select("table tr"):
        name_td = row.select_one("td.name")
        if not name_td:
            continue
        link = name_td.select_one("a[href^='/song/']")
        if not link:
            continue
        href = link.get("href", "")
        m = _SONG_HREF_RE.search(href)
        if not m:
            continue
        song_id = m.group(1)
        title = link.get_text(strip=True)
        artist: str | None = None
        album: str | None = None
        for a in name_td.select("a.gray_link"):
            text = a.get_text(strip=True)
            if text.startswith("《") and text.endswith("》"):
                album = text[1:-1]
            else:
                artist = text
        results.append(
            ZanmeiSearchResult(
                song_id=song_id,
                title=title,
                artist=artist,
                album=album,
                href=href,
            )
        )
    return results


def _select_best_match(
    results: list[ZanmeiSearchResult], title: str, band: str | None
) -> ZanmeiSearchResult | None:
    """Pick the best search hit by title (exact) then band (substring).

    Falls back to the first result when none match exactly.
    """
    if not results:
        return None
    title_norm = title.strip().lower()
    band_norm = band.strip().lower() if band else ""

    for r in results:
        if r.title.strip().lower() == title_norm and (
            not band_norm or (r.artist and band_norm in r.artist.lower())
        ):
            return r

    for r in results:
        if r.title.strip().lower() == title_norm:
            return r

    return results[0]


def search_zanmei_songs(title: str, band: str | None = None) -> list[ZanmeiSearchResult]:
    """Search zanmei.ai for songs matching ``title`` (and optionally ``band``).

    Returns ordered list of ZanmeiSearchResult. Raises RuntimeError on fetch
    failure.
    """
    query = " ".join(filter(None, [title, band])) if band else title
    url = BASE_URL + _SEARCH_PATH + urllib.parse.quote(query)
    html = _fetch(url)
    return _parse_search_results(html)


def fetch_zanmei_lyrics(song_id: str) -> str | None:
    """Fetch raw section-tagged lyrics text for a zanmei.ai song ID.

    Returns None if the song page has no ``<pre id="lyric_text">`` block.
    Raises RuntimeError on fetch failure.
    """
    url = BASE_URL + _SONG_PATH.format(song_id=song_id)
    html = _fetch(url)
    soup = BeautifulSoup(html, "lxml")
    pre = soup.select_one(f"pre#{_LYRIC_PRE_ID}")
    if not pre:
        return None
    text = pre.get_text().strip()
    if not text:
        return None
    # zanmei.ai lyrics are Simplified Chinese; canonicalise to Traditional.
    return _to_traditional(text)


def fetch_structured_lyrics_from_zanmei(title: str, band: str | None = None) -> str | None:
    """End-to-end: search zanmei.ai, pick best match, return lyrics text.

    Returns the raw section-tagged lyrics string, or None if no song found
    or the song page has no lyrics. Raises RuntimeError on network failure.
    """
    results = search_zanmei_songs(title, band)
    if not results:
        logger.info("zanmei.ai: no search results for title=%r band=%r", title, band)
        return None
    best = _select_best_match(results, title, band)
    if not best:
        return None
    logger.info(
        "zanmei.ai: best match song_id=%s title=%r artist=%r",
        best.song_id,
        best.title,
        best.artist,
    )
    return fetch_zanmei_lyrics(best.song_id)
