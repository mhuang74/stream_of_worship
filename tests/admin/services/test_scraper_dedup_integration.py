"""Integration tests for scraper dedup behavior with HTML fixtures."""

import pytest
from unittest.mock import patch

from stream_of_worship.admin.services.scraper import CatalogScraper


@pytest.fixture
def html_with_duplicates():
    """Minimal HTML table where a song appears twice with different series."""
    return """
    <table id="tablepress-3">
        <tr>
            <th>曲名</th><th>作曲</th><th>作詞</th>
            <th>專輯系列</th><th>專輯名稱</th><th>調性</th><th>歌詞</th>
        </tr>
        <tr>
            <td>將天敞開</td><td>游智婷</td><td>鄭懋柔</td>
            <td>其他專輯</td><td>讚美之泉精選</td><td>G</td><td>歌詞A</td>
        </tr>
        <tr>
            <td>將天敞開</td><td>游智婷</td><td>鄭懋柔</td>
            <td>敬拜讚美15</td><td>讓讚美飛揚</td><td>G</td><td>歌詞B</td>
        </tr>
    </table>
    """


@pytest.fixture
def html_without_duplicates():
    """HTML table with unique songs."""
    return """
    <table id="tablepress-3">
        <tr>
            <th>曲名</th><th>作曲</th><th>作詞</th>
            <th>專輯系列</th><th>專輯名稱</th><th>調性</th><th>歌詞</th>
        </tr>
        <tr>
            <td>將天敞開</td><td>游智婷</td><td>鄭懋柔</td>
            <td>敬拜讚美15</td><td>讓讚美飛揚</td><td>G</td><td>歌詞</td>
        </tr>
        <tr>
            <td>另一首歌</td><td>某人</td><td>某人</td>
            <td>其他專輯</td><td>精選集</td><td>C</td><td>其他歌詞</td>
        </tr>
    </table>
    """


class FakeResponse:
    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        pass


class TestDedupIntegration:
    def test_scrape_prefers_jingbai_in_full_mode(self, html_with_duplicates):
        """Full scrape should return the 敬拜讚美 version."""
        scraper = CatalogScraper()

        with patch("requests.get", return_value=FakeResponse(html_with_duplicates)):
            songs = scraper.scrape_all_songs(force=True, incremental=False)

        jiang_tian = [s for s in songs if "將天敞開" in s.title]
        assert len(jiang_tian) == 1
        assert jiang_tian[0].album_series == "敬拜讚美15"

    def test_scrape_no_duplicates_unchanged(self, html_without_duplicates):
        """Non-duplicate fixture behaves identically to old code."""
        scraper = CatalogScraper()

        with patch("requests.get", return_value=FakeResponse(html_without_duplicates)):
            songs = scraper.scrape_all_songs(force=True, incremental=False)

        assert len(songs) == 2
        titles = [s.title for s in songs]
        assert "將天敞開" in titles
        assert "另一首歌" in titles

    def test_duplicate_count_reported_correctly(self, html_with_duplicates):
        """Duplicate count should reflect the number of extra rows."""
        scraper = CatalogScraper()

        with patch("requests.get", return_value=FakeResponse(html_with_duplicates)):
            scraper.scrape_all_songs(force=True, incremental=False)

        assert scraper.last_run_duplicate_count == 1

    def test_preserve_all_unique_songs(self, html_with_duplicates):
        """When there are multiple songs, all unique ones are preserved."""
        html = """
        <table id="tablepress-3">
            <tr>
                <th>曲名</th><th>作曲</th><th>作詞</th>
                <th>專輯系列</th><th>專輯名稱</th><th>調性</th><th>歌詞</th>
            </tr>
            <tr>
                <td>歌曲A</td><td>甲</td><td>甲</td>
                <td>其他專輯</td><td>精選</td><td>C</td><td>詞A</td>
            </tr>
            <tr>
                <td>歌曲A</td><td>甲</td><td>甲</td>
                <td>敬拜讚美15</td><td>專輯</td><td>C</td><td>詞A-2</td>
            </tr>
            <tr>
                <td>歌曲B</td><td>乙</td><td>乙</td>
                <td>其他專輯</td><td>精選</td><td>D</td><td>詞B</td>
            </tr>
        </table>
        """
        scraper = CatalogScraper()

        with patch("requests.get", return_value=FakeResponse(html)):
            songs = scraper.scrape_all_songs(force=True, incremental=False)

        assert len(songs) == 2
        titles = [s.title for s in songs]
        assert "歌曲A" in titles
        assert "歌曲B" in titles

        song_a = next(s for s in songs if s.title == "歌曲A")
        assert song_a.album_series == "敬拜讚美15"
