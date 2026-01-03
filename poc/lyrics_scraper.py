#!/usr/bin/env python3
"""
Lyrics Scraper for sop.org/songs

Scrapes Chinese worship song lyrics from Stream of Praise (讚美之泉) website.

Usage:
    python poc/lyrics_scraper.py                 # Scrape all songs
    python poc/lyrics_scraper.py --test          # Test with single song
    python poc/lyrics_scraper.py --limit 10      # Scrape first 10 songs
    python poc/lyrics_scraper.py --verbose       # Show detailed progress
"""

import argparse
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup
from pypinyin import lazy_pinyin

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)


class LyricsScraper:
    """Scraper for sop.org/songs lyrics table"""

    def __init__(self, output_dir: str = 'data/lyrics'):
        self.url = 'https://www.sop.org/songs/'
        self.output_dir = Path(output_dir)
        self.songs_dir = self.output_dir / 'songs'
        self.index_file = self.output_dir / 'lyrics_index.json'

        # Create output directories
        self.songs_dir.mkdir(parents=True, exist_ok=True)

        # Headers for HTTP requests
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }

    def scrape_all_songs(self, limit: Optional[int] = None) -> List[Dict]:
        """
        Scrape all songs from the sop.org/songs table.

        Args:
            limit: Maximum number of songs to scrape (None for all)

        Returns:
            List of song dictionaries
        """
        logger.info(f"Fetching lyrics table from {self.url}")

        try:
            response = requests.get(self.url, headers=self.headers, timeout=30)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch page: {e}")
            raise

        logger.info(f"Parsing HTML ({len(response.text)} bytes)")
        soup = BeautifulSoup(response.text, 'html.parser')

        # Find the TablePress table
        table = soup.find('table', id='tablepress-3')
        if not table:
            raise ValueError("Table 'tablepress-3' not found - site structure may have changed")

        rows = table.find_all('tr')
        logger.info(f"Found {len(rows)} rows in table")

        # Get headers
        if not rows:
            raise ValueError("No rows found in table")

        header_row = rows[0]
        headers = [th.get_text(strip=True) for th in header_row.find_all(['th', 'td'])]
        logger.info(f"Headers: {headers}")

        # Find column indices
        col_indices = {
            'title': self._find_header_index(headers, ['曲名', 'title']),
            'composer': self._find_header_index(headers, ['作曲', 'composer']),
            'lyricist': self._find_header_index(headers, ['作詞', 'lyricist']),
            'album': self._find_header_index(headers, ['專輯名稱', 'album']),
            'series': self._find_header_index(headers, ['專輯系列', 'series']),
            'key': self._find_header_index(headers, ['調性', 'key']),
            'lyrics': self._find_header_index(headers, ['歌詞', 'lyrics'])
        }

        if col_indices['lyrics'] is None:
            raise ValueError("Lyrics column not found in table")

        # Parse data rows
        songs = []
        data_rows = rows[1:]  # Skip header

        if limit:
            data_rows = data_rows[:limit]

        for row_num, row in enumerate(data_rows, 1):
            cells = row.find_all(['td', 'th'])
            if not cells:
                continue

            try:
                song = self._parse_row(cells, col_indices, row_num)
                if song:
                    songs.append(song)

                    if row_num % 100 == 0:
                        logger.info(f"Processed {row_num}/{len(data_rows)} songs...")

            except Exception as e:
                logger.warning(f"Failed to parse row {row_num}: {e}")
                continue

        logger.info(f"Successfully parsed {len(songs)} songs")
        return songs

    def _find_header_index(self, headers: List[str], keywords: List[str]) -> Optional[int]:
        """Find column index by matching keywords"""
        for i, header in enumerate(headers):
            if any(kw in header for kw in keywords):
                return i
        return None

    def _parse_row(self, cells: List, col_indices: Dict, row_num: int) -> Optional[Dict]:
        """Parse a single table row into song dictionary"""

        # Extract basic metadata
        title = cells[col_indices['title']].get_text(strip=True) if col_indices['title'] is not None else ''
        if not title:
            return None

        composer = cells[col_indices['composer']].get_text(strip=True) if col_indices['composer'] is not None else ''
        lyricist = cells[col_indices['lyricist']].get_text(strip=True) if col_indices['lyricist'] is not None else ''
        album = cells[col_indices['album']].get_text(strip=True) if col_indices['album'] is not None else ''
        series = cells[col_indices['series']].get_text(strip=True) if col_indices['series'] is not None else ''
        key = cells[col_indices['key']].get_text(strip=True) if col_indices['key'] is not None else ''

        # Extract lyrics
        lyrics_cell = cells[col_indices['lyrics']]
        lyrics_data = self._parse_lyrics_cell(lyrics_cell)

        # Generate song ID
        song_id = self._normalize_song_id(title, row_num)

        song = {
            'song_id': song_id,
            'title': title,
            'metadata': {
                'composer': composer,
                'lyricist': lyricist,
                'album_name': album,
                'album_series': series,
                'key': key,
                'source_url': self.url,
                'scraped_at': datetime.utcnow().isoformat() + 'Z',
                'table_row_number': row_num
            },
            'lyrics_raw': lyrics_data['lyrics_raw'],
            'lyrics_lines': lyrics_data['lyrics_lines'],
            'sections': self._detect_sections(lyrics_data['lyrics_lines']),
            'stats': {
                'total_lines': len(lyrics_data['lyrics_lines']),
                'total_sections': 1,  # POC version
                'has_section_labels': False
            }
        }

        return song

    def _parse_lyrics_cell(self, cell) -> Dict:
        """
        Extract lyrics from table cell, preserving line breaks.

        Args:
            cell: BeautifulSoup cell element

        Returns:
            Dict with 'lyrics_raw' and 'lyrics_lines'
        """
        # Replace <br/> tags with newlines
        for br in cell.find_all('br'):
            br.replace_with('\n')

        # Get text with newlines preserved
        lyrics_raw = cell.get_text()

        # Split into lines, strip whitespace, filter empty
        lyrics_lines = [
            line.strip()
            for line in lyrics_raw.split('\n')
            if line.strip()
        ]

        return {
            'lyrics_raw': lyrics_raw.strip(),
            'lyrics_lines': lyrics_lines
        }

    def _detect_sections(self, lyrics_lines: List[str]) -> List[Dict]:
        """
        Detect song sections (verse/chorus/bridge).

        POC Version: Returns all lines as single 'unknown' section.
        Future: Implement pattern-based detection.
        """
        return [{
            'section_type': 'unknown',
            'section_number': 1,
            'lines': lyrics_lines
        }]

    def _normalize_song_id(self, title: str, row_num: int) -> str:
        """
        Convert song title to filesystem-safe ID.

        Uses Pinyin romanization for Chinese characters.

        Args:
            title: Song title (可能包含中文)
            row_num: Row number for uniqueness

        Returns:
            Normalized song ID (e.g., "jiang_tian_chang_kai_209")
        """
        # Convert to Pinyin
        pinyin_parts = lazy_pinyin(title)
        pinyin_str = '_'.join(pinyin_parts)

        # Remove special characters, keep only alphanumeric and underscore
        clean_str = re.sub(r'[^a-z0-9_]', '', pinyin_str.lower())

        # Add row number for uniqueness (in case of duplicate titles)
        song_id = f"{clean_str}_{row_num}"

        # Limit length to avoid filesystem issues
        if len(song_id) > 100:
            song_id = song_id[:95] + f"_{row_num}"

        return song_id

    def save_songs(self, songs: List[Dict]):
        """
        Save songs to JSON files and update master index.

        Args:
            songs: List of song dictionaries
        """
        logger.info(f"Saving {len(songs)} songs to {self.songs_dir}")

        index_data = {
            'metadata': {
                'scrape_date': datetime.utcnow().isoformat() + 'Z',
                'source_url': self.url,
                'total_songs': len(songs),
                'scraper_version': '1.0.0'
            },
            'songs': []
        }

        for song in songs:
            # Save individual song file
            song_file = self.songs_dir / f"{song['song_id']}.json"

            with song_file.open('w', encoding='utf-8') as f:
                json.dump(song, f, ensure_ascii=False, indent=2)

            # Add to index
            index_data['songs'].append({
                'song_id': song['song_id'],
                'title': song['title'],
                'composer': song['metadata']['composer'],
                'album': song['metadata']['album_name'],
                'key': song['metadata']['key'],
                'file_path': f"data/lyrics/songs/{song['song_id']}.json",
                'line_count': song['stats']['total_lines']
            })

        # Save master index
        logger.info(f"Saving master index to {self.index_file}")
        with self.index_file.open('w', encoding='utf-8') as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)

        logger.info("✅ All songs saved successfully!")

    def validate_test_song(self):
        """
        Validate the test song '將天敞開' against expected structure.

        Raises:
            AssertionError: If validation fails
        """
        logger.info("Validating test song: 將天敞開")

        songs = self.scrape_all_songs()

        # Find test song
        test_song = None
        for song in songs:
            if '將天敞開' in song['title']:
                test_song = song
                break

        if not test_song:
            raise AssertionError("Test song '將天敞開' not found in scraped data")

        # Validate structure
        assert test_song['title'] == '將天敞開', f"Title mismatch: {test_song['title']}"
        assert test_song['metadata']['composer'] == '游智婷', f"Composer mismatch: {test_song['metadata']['composer']}"
        assert test_song['metadata']['lyricist'] == '鄭懋柔', f"Lyricist mismatch: {test_song['metadata']['lyricist']}"
        assert test_song['metadata']['key'] == 'G', f"Key mismatch: {test_song['metadata']['key']}"

        # Validate lyrics
        assert len(test_song['lyrics_lines']) > 0, "No lyrics found"
        assert test_song['lyrics_raw'], "Raw lyrics empty"

        # Check line breaks preserved (no unprocessed HTML)
        for line in test_song['lyrics_lines']:
            assert '<br' not in line, f"Unprocessed HTML in line: {line}"
            assert '</br' not in line, f"Unprocessed HTML in line: {line}"

        logger.info("✅ Test song validation passed!")
        logger.info(f"   Title: {test_song['title']}")
        logger.info(f"   Composer: {test_song['metadata']['composer']}")
        logger.info(f"   Lyricist: {test_song['metadata']['lyricist']}")
        logger.info(f"   Album: {test_song['metadata']['album_name']}")
        logger.info(f"   Key: {test_song['metadata']['key']}")
        logger.info(f"   Total lines: {len(test_song['lyrics_lines'])}")
        logger.info(f"   First line: {test_song['lyrics_lines'][0]}")

        return test_song


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Scrape lyrics from sop.org/songs')
    parser.add_argument('--test', action='store_true', help='Validate test song only')
    parser.add_argument('--limit', type=int, help='Limit number of songs to scrape')
    parser.add_argument('--output', default='data/lyrics', help='Output directory')
    parser.add_argument('--verbose', action='store_true', help='Show detailed progress')

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    scraper = LyricsScraper(output_dir=args.output)

    if args.test:
        # Test mode: validate single song
        test_song = scraper.validate_test_song()

        # Save test song for inspection
        test_file = Path(args.output) / 'test_song.json'
        with test_file.open('w', encoding='utf-8') as f:
            json.dump(test_song, f, ensure_ascii=False, indent=2)

        logger.info(f"Test song saved to: {test_file}")

    else:
        # Full scrape mode
        songs = scraper.scrape_all_songs(limit=args.limit)
        scraper.save_songs(songs)

        logger.info(f"\n{'='*60}")
        logger.info(f"Scraping completed successfully!")
        logger.info(f"Total songs scraped: {len(songs)}")
        logger.info(f"Output directory: {scraper.output_dir}")
        logger.info(f"Master index: {scraper.index_file}")
        logger.info(f"{'='*60}")


if __name__ == '__main__':
    main()
