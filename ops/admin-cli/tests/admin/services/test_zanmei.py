"""Tests for the zanmei.ai structured-lyrics scraper service.

All network I/O is mocked by patching :func:`_fetch`, so these run offline.
"""

from __future__ import annotations

import urllib.parse

import pytest

from stream_of_worship.admin.services import zanmei
from stream_of_worship.admin.services.zanmei import (
    ZanmeiSearchResult,
    _parse_search_results,
    _select_best_match,
    fetch_structured_lyrics_from_zanmei,
    fetch_zanmei_lyrics,
    search_zanmei_songs,
)

SEARCH_HTML = """<html><body>
<table>
  <tr>
    <td class="name">
      <div>
        <a href="/song/45335.html"><em>祢就</em><em>是</em><em>唯一</em></a>
        &nbsp;- <a href="/album/hear-our-cry.html" class="gray_link">《听见这世代的呼唤》</a>
        -&nbsp;&nbsp;<a href="/artist/stream-of-praise.html" class="gray_link">赞美之泉</a></div>
    </td>
  </tr>
  <tr>
    <td class="name">
      <div>
        <a href="/song/42508.html"><em>赞美</em><em>之</em><em>泉</em></a>
        &nbsp;- <a href="/album/x.html" class="gray_link">《轻音乐赞美诗》</a>
        -&nbsp;&nbsp;<a href="/artist/510.html" class="gray_link">王璜青</a></div>
    </td>
  </tr>
</table>
</body></html>"""

SONG_HTML = """<html><body>
<div id="lyrics">
  <pre id="lyric_text" style="font-size: 12px;">词：恒恩 Brook
曲：恒恩 Brook

[Verse]
有时候会迷惘　找不到前路方向

[Chorus]
祢就是唯一　点亮我生命的奇迹
</pre>
</div>
</body></html>"""

SONG_HTML_NO_LYRICS = """<html><body>
<div id="lyrics"><p>no pre here</p></div>
</body></html>"""


class TestParseSearchResults:
    def test_parses_song_links_and_metadata(self):
        """Search rows yield song_id, title, album, artist (em tags stripped)."""
        results = _parse_search_results(SEARCH_HTML)
        assert len(results) == 2
        first = results[0]
        assert first.song_id == "45335"
        assert first.title == "祢就是唯一"  # <em> highlights flattened
        assert first.album == "听见这世代的呼唤"
        assert first.artist == "赞美之泉"
        assert first.href == "/song/45335.html"

    def test_empty_html_returns_empty(self):
        """No song links → empty list."""
        assert _parse_search_results("<html></html>") == []


class TestSelectBestMatch:
    def _results(self):
        return [
            ZanmeiSearchResult(song_id="1", title="赞歌", artist="别的乐队"),
            ZanmeiSearchResult(song_id="2", title="祢就是唯一", artist="赞美之泉"),
            ZanmeiSearchResult(song_id="3", title="祢就是唯一"),
        ]

    def test_exact_title_and_band_wins(self):
        """Best match requires exact title + matching band."""
        best = _select_best_match(self._results(), "祢就是唯一", "赞美之泉")
        assert best.song_id == "2"

    def test_exact_title_without_band(self):
        """When band doesn't narrow, first exact title match is chosen."""
        best = _select_best_match(self._results(), "祢就是唯一", "无关乐手")
        assert best.song_id == "2"  # first exact-title with band, else first exact

    def test_falls_back_to_first_result(self):
        """No exact title → first result."""
        best = _select_best_match(self._results(), "别的歌", None)
        assert best.song_id == "1"

    def test_empty_results_returns_none(self):
        assert _select_best_match([], "x", None) is None


class TestSearchZanmeiSongs:
    def test_builds_url_with_title_and_band(self, monkeypatch):
        """Search URL encodes title + band; returns parsed results."""
        captured = {}

        def fake_fetch(url):
            captured["url"] = url
            return SEARCH_HTML

        monkeypatch.setattr(zanmei, "_fetch", fake_fetch)
        results = search_zanmei_songs("祢就是唯一", "赞美之泉")
        assert len(results) == 2
        assert "search/song/" in captured["url"]
        assert urllib.parse.quote("祢就是唯一") in captured["url"]
        assert urllib.parse.quote("赞美之泉") in captured["url"]

    def test_fetch_failure_raises_runtime_error(self, monkeypatch):
        def fake_fetch(url):
            raise RuntimeError("boom")

        monkeypatch.setattr(zanmei, "_fetch", fake_fetch)
        with pytest.raises(RuntimeError):
            search_zanmei_songs("祢就是唯一")


class TestFetchZanmeiLyrics:
    def test_extracts_lyric_text(self, monkeypatch):
        """Extracts the <pre> block and converts Simplified -> Traditional."""
        monkeypatch.setattr(zanmei, "_fetch", lambda url: SONG_HTML)
        text = fetch_zanmei_lyrics("45335")
        assert text is not None
        assert "[Verse]" in text
        assert "禰就是唯一" in text
        # zanmei.ai lyrics are Simplified; we canonicalise to Traditional.
        assert text.startswith("詞：恆恩 Brook")
        assert "有時候會迷惘　找不到前路方向" in text
        assert "點亮我生命的奇蹟" in text

    def test_missing_lyric_pre_returns_none(self, monkeypatch):
        monkeypatch.setattr(zanmei, "_fetch", lambda url: SONG_HTML_NO_LYRICS)
        assert fetch_zanmei_lyrics("1") is None


class TestFetchStructuredLyricsFromZanmei:
    def test_end_to_end_search_and_fetch(self, monkeypatch):
        """Searches, picks best match, and fetches its lyrics."""
        urls = []

        def fake_fetch(url):
            urls.append(url)
            if "search/song/" in url:
                return SEARCH_HTML
            return SONG_HTML

        monkeypatch.setattr(zanmei, "_fetch", fake_fetch)
        text = fetch_structured_lyrics_from_zanmei("祢就是唯一", "赞美之泉")
        assert text is not None
        assert "[Chorus]" in text
        # Followed the best match's song page.
        assert any("/song/45335.html" in u for u in urls)

    def test_no_results_returns_none(self, monkeypatch):
        monkeypatch.setattr(zanmei, "_fetch", lambda url: "<html></html>")
        assert fetch_structured_lyrics_from_zanmei("不存在", "无人") is None


class TestToTraditional:
    """Tests for the Simplified -> Traditional lyric canonicalisation."""

    def test_converts_simplified_to_traditional(self):
        assert zanmei._to_traditional("词曲：恒恩 点亮奇迹") == "詞曲：恆恩 點亮奇蹟"

    def test_idempotent_on_traditional(self):
        assert zanmei._to_traditional("詞曲：恆恩 點亮奇蹟") == "詞曲：恆恩 點亮奇蹟"

    def test_returns_original_when_opencc_missing(self, monkeypatch):
        monkeypatch.setattr(zanmei, "OpenCC", None)
        monkeypatch.setattr(zanmei, "_converter_cache", {})
        text = "词：恒恩"
        assert zanmei._to_traditional(text) == text

    def test_returns_original_when_conversion_fails(self, monkeypatch):
        class Boom:
            def convert(self, text):
                raise RuntimeError("boom")

        monkeypatch.setattr(zanmei, "_converter_cache", {"s2t": Boom()})
        text = "词：恒恩"
        assert zanmei._to_traditional(text) == text
