"""Catalog scraper service for sop.org/songs.

Refactored from poc/lyrics_scraper.py to integrate with the admin CLI database.
"""

import json
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup
from pypinyin import lazy_pinyin

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Song

logger = logging.getLogger(__name__)


class CatalogScraper:
    """Scraper for sop.org/songs catalog.

    Refactored from the original LyricsScraper to integrate with the
    sow-admin database instead of writing to JSON files.
    """

    def __init__(self, db_client: Optional[DatabaseClient] = None):
        """Initialize the scraper.

        Args:
            db_client: Database client for saving scraped songs.
                      If None, songs will be returned but not saved.
        """
        self.url = "https://www.sop.org/songs/"
        self.db_client = db_client

        # Headers for HTTP requests
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }

    def scrape_all_songs(
        self,
        limit: Optional[int] = None,
        force: bool = False,
        incremental: bool = True,
    ) -> List[Song]:
        """Scrape all songs from the sop.org/songs table.

        Args:
            limit: Maximum number of songs to scrape (None for all)
            force: Re-scrape all songs even if already in database
            incremental: Skip songs already in database (ignored if force=True)

        Returns:
            List of Song objects
        """
        logger.info(f"Fetching lyrics table from {self.url}")

        try:
            response = requests.get(self.url, headers=self.headers, timeout=30)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch page: {e}")
            raise

        logger.info(f"Parsing HTML ({len(response.text)} bytes)")
        soup = BeautifulSoup(response.text, "html.parser")

        # Find the TablePress table
        table = soup.find("table", id="tablepress-3")
        if not table:
            raise ValueError("Table 'tablepress-3' not found - site structure may have changed")

        rows = table.find_all("tr")
        logger.info(f"Found {len(rows)} rows in table")

        # Get headers
        if not rows:
            raise ValueError("No rows found in table")

        header_row = rows[0]
        headers = [th.get_text(strip=True) for th in header_row.find_all(["th", "td"])]
        logger.info(f"Headers: {headers}")

        # Find column indices
        col_indices = {
            "title": self._find_header_index(headers, ["曲名", "title"]),
            "composer": self._find_header_index(headers, ["作曲", "composer"]),
            "lyricist": self._find_header_index(headers, ["作詞", "lyricist"]),
            "album": self._find_header_index(headers, ["專輯名稱", "album"]),
            "series": self._find_header_index(headers, ["專輯系列", "series"]),
            "key": self._find_header_index(headers, ["調性", "key"]),
            "lyrics": self._find_header_index(headers, ["歌詞", "lyrics"]),
        }

        if col_indices["lyrics"] is None:
            raise ValueError("Lyrics column not found in table")

        # Parse data rows
        songs = []
        data_rows = rows[1:]  # Skip header

        if limit:
            data_rows = data_rows[:limit]

        # Get existing song IDs for incremental mode
        existing_ids = set()
        if incremental and not force and self.db_client:
            existing_ids = self._get_existing_song_ids()
            logger.info(f"Found {len(existing_ids)} existing songs in database")

        for row_num, row in enumerate(data_rows, 1):
            cells = row.find_all(["td", "th"])
            if not cells:
                continue

            try:
                song = self._parse_row(cells, col_indices, row_num)
                if song:
                    # Skip if already exists and incremental mode
                    if incremental and not force and song.id in existing_ids:
                        logger.debug(f"Skipping existing song: {song.id}")
                        continue

                    songs.append(song)

                    if row_num % 100 == 0:
                        logger.info(f"Processed {row_num}/{len(data_rows)} songs...")

            except Exception as e:
                logger.warning(f"Failed to parse row {row_num}: {e}")
                continue

        logger.info(f"Successfully parsed {len(songs)} songs")
        return songs

    def _get_existing_song_ids(self) -> set:
        """Get set of existing song IDs from database."""
        if not self.db_client:
            return set()

        try:
            # List all songs and extract IDs
            songs = self.db_client.list_songs(limit=100000)  # Large limit to get all
            return {song.id for song in songs}
        except Exception as e:
            logger.warning(f"Failed to get existing song IDs: {e}")
            return set()

    def save_songs(self, songs: List[Song]) -> int:
        """Save songs to the database.

        Args:
            songs: List of Song objects to save

        Returns:
            Number of songs saved
        """
        if not self.db_client:
            logger.warning("No database client configured, songs not saved")
            return 0

        if not songs:
            logger.info("No songs to save")
            return 0

        logger.info(f"Saving {len(songs)} songs to database")
        saved_count = 0

        with self.db_client.transaction():
            for song in songs:
                try:
                    self.db_client.insert_song(song)
                    saved_count += 1
                except Exception as e:
                    logger.warning(f"Failed to save song {song.id}: {e}")

        logger.info(f"Successfully saved {saved_count}/{len(songs)} songs")
        return saved_count

    def _find_header_index(self, headers: List[str], keywords: List[str]) -> Optional[int]:
        """Find column index by matching keywords."""
        for i, header in enumerate(headers):
            if any(kw in header for kw in keywords):
                return i
        return None

    def _parse_row(self, cells: List, col_indices: Dict, row_num: int) -> Optional[Song]:
        """Parse a single table row into a Song object."""
        # Extract basic metadata
        title = (
            cells[col_indices["title"]].get_text(strip=True)
            if col_indices["title"] is not None
            else ""
        )
        if not title:
            return None

        composer = (
            cells[col_indices["composer"]].get_text(strip=True)
            if col_indices["composer"] is not None
            else ""
        )
        lyricist = (
            cells[col_indices["lyricist"]].get_text(strip=True)
            if col_indices["lyricist"] is not None
            else ""
        )
        album = (
            cells[col_indices["album"]].get_text(strip=True)
            if col_indices["album"] is not None
            else ""
        )
        series = (
            cells[col_indices["series"]].get_text(strip=True)
            if col_indices["series"] is not None
            else ""
        )
        key = (
            cells[col_indices["key"]].get_text(strip=True)
            if col_indices["key"] is not None
            else ""
        )

        # Extract lyrics
        lyrics_cell = cells[col_indices["lyrics"]]
        lyrics_data = self._parse_lyrics_cell(lyrics_cell)

        # Generate song ID
        song_id = self._normalize_song_id(title, row_num)

        # Generate pinyin for title
        title_pinyin = "_".join(lazy_pinyin(title))

        # Create Song object
        song = Song(
            id=song_id,
            title=title,
            title_pinyin=title_pinyin,
            composer=composer,
            lyricist=lyricist,
            album_name=album,
            album_series=series,
            musical_key=key,
            lyrics_raw=lyrics_data["lyrics_raw"],
            lyrics_lines=json.dumps(lyrics_data["lyrics_lines"], ensure_ascii=False),
            sections=json.dumps(self._detect_sections(lyrics_data["lyrics_lines"]), ensure_ascii=False),
            source_url=self.url,
            table_row_number=row_num,
            scraped_at=datetime.now().isoformat(),
        )

        return song

    def _parse_lyrics_cell(self, cell) -> Dict:
        """Extract lyrics from table cell, preserving line breaks."""
        # Replace <br/> tags with newlines
        for br in cell.find_all("br"):
            br.replace_with("\n")

        # Get text with newlines preserved
        lyrics_raw = cell.get_text()

        # Split into lines, strip whitespace, filter empty
        lyrics_lines = [
            line.strip() for line in lyrics_raw.split("\n") if line.strip()
        ]

        return {"lyrics_raw": lyrics_raw.strip(), "lyrics_lines": lyrics_lines}

    def _detect_sections(self, lyrics_lines: List[str]) -> List[Dict]:
        """Detect song sections (verse/chorus/bridge).

        POC Version: Returns all lines as single 'unknown' section.
        Future: Implement pattern-based detection.
        """
        return [
            {
                "section_type": "unknown",
                "section_number": 1,
                "lines": lyrics_lines,
            }
        ]

    def _normalize_song_id(self, title: str, row_num: int) -> str:
        """Convert song title to filesystem-safe ID.

        Uses Pinyin romanization for Chinese characters.

        Args:
            title: Song title (may contain Chinese)
            row_num: Row number for uniqueness

        Returns:
            Normalized song ID (e.g., "song_0209" or "jiang_tian_chang_kai_209")
        """
        # Convert to Pinyin
        pinyin_parts = lazy_pinyin(title)
        pinyin_str = "_".join(pinyin_parts)

        # Remove special characters, keep only alphanumeric and underscore
        clean_str = re.sub(r"[^a-z0-9_]", "", pinyin_str.lower())

        # Add row number for uniqueness (in case of duplicate titles)
        song_id = f"{clean_str}_{row_num}"

        # Limit length to avoid filesystem issues
        if len(song_id) > 100:
            song_id = song_id[:95] + f"_{row_num}"

        return song_id

    def validate_test_song(self) -> Song:
        """Validate the test song '將天敞開' against expected structure.

        This is used for testing and validation purposes.

        Returns:
            The validated Song object

        Raises:
            AssertionError: If validation fails
        """
        logger.info("Validating test song: 將天敞開")

        songs = self.scrape_all_songs()

        # Find test song
        test_song = None
        for song in songs:
            if "將天敞開" in song.title:
                test_song = song
                break

        if not test_song:
            raise AssertionError("Test song '將天敞開' not found in scraped data")

        # Validate structure
        assert test_song.title == "將天敞開", f"Title mismatch: {test_song.title}"
        assert test_song.composer == "游智婷", f"Composer mismatch: {test_song.composer}"
        assert test_song.lyricist == "鄭懋柔", f"Lyricist mismatch: {test_song.lyricist}"
        assert test_song.musical_key == "G", f"Key mismatch: {test_song.musical_key}"

        # Validate lyrics
        lyrics_list = test_song.lyrics_list
        assert len(lyrics_list) > 0, "No lyrics found"
        assert test_song.lyrics_raw, "Raw lyrics empty"

        # Check line breaks preserved (no unprocessed HTML)
        for line in lyrics_list:
            assert "<br" not in line, f"Unprocessed HTML in line: {line}"
            assert "</br" not in line, f"Unprocessed HTML in line: {line}"

        logger.info("Test song validation passed!")
        logger.info(f"   Title: {test_song.title}")
        logger.info(f"   Composer: {test_song.composer}")
        logger.info(f"   Lyricist: {test_song.lyricist}")
        logger.info(f"   Album: {test_song.album_name}")
        logger.info(f"   Key: {test_song.musical_key}")
        logger.info(f"   Total lines: {len(lyrics_list)}")
        logger.info(f"   First line: {lyrics_list[0]}")

        return test_song
