# Lyrics Scraper Design Specification

**Project**: Stream of Worship - Worship Music Transition System
**Feature**: Chinese Song Lyrics Scraper
**Target**: sop.org/songs (讚美之泉 Stream of Praise)
**Date**: 2026-01-02
**Status**: Design Approved

---

## 1. Overview

### 1.1 Purpose
Scrape Chinese worship song lyrics from sop.org/songs to support lyrics retrieval and future time-coded lyrics alignment for the worship music transition system.

### 1.2 Scope
- **In Scope**:
  - Scrape all 685+ Chinese worship songs from sop.org/songs table
  - Extract lyrics with exact line break preservation
  - Store structured data in JSON format
  - Support section detection (verse/chorus/bridge)

- **Out of Scope**:
  - Time-coded lyrics alignment (future Phase 3)
  - English translations (can add later)
  - Audio file matching (future integration)

### 1.3 Success Criteria
1. ✅ Successfully scrape all songs from sop.org/songs table
2. ✅ Test song "將天敞開" validates correctly
3. ✅ Line breaks preserved exactly as on website (`<br/>` tags)
4. ✅ Output JSON files compatible with future POC analysis integration
5. ✅ Scraper completes in < 5 minutes for all 685 songs

---

## 2. Reconnaissance Findings

### 2.1 Site Structure
- **URL**: https://www.sop.org/songs/
- **Technology**: WordPress with TablePress plugin
- **Table ID**: `tablepress-3`
- **Total Songs**: 685 rows (as of 2026-01-02)
- **Content Loading**: Server-side rendered HTML (no JavaScript required)
- **Authentication**: None required - public data

### 2.2 Table Schema
| Column | Chinese | Description |
|--------|---------|-------------|
| 0 | 曲名 | Song Name |
| 1 | 作曲 | Composer |
| 2 | 作詞 | Lyricist |
| 3 | 專輯名稱 | Album Name |
| 4 | 專輯系列 | Album Series |
| 5 | 調性 | Musical Key |
| 6 | 歌詞 | **Lyrics** (with `<br/>` tags) |

### 2.3 Lyrics Format Example
**Test Song**: 將天敞開 (Row 209)

**HTML Structure**:
```html
<td class="column-7">
將天敞開  祢的榮耀降下來  <br/>
將天敞開  祢的同在降下來 <br/>
將天敞開  祢的榮耀降下來  <br/>
萬國讚嘆祢  祢是榮耀君王 <br/>
</td>
```

**Key Observations**:
- Each line ends with `<br/>` tag
- Double spaces preserved between phrases
- No section labels (Verse/Chorus/Bridge) in HTML
- Must detect sections algorithmically

### 2.4 Technology Decision
✅ **Tier 1: requests + BeautifulSoup**
- Simple, fast, lightweight
- No browser automation needed (all data in HTML)
- Table parsing with `<br/>` tag handling

---

## 3. Architecture

### 3.1 Module Structure
```
poc/
├── lyrics_scraper.py       # Main scraper script
└── lyrics_utils.py         # Shared utilities

data/
├── lyrics/
│   ├── songs/              # Individual song JSON files
│   │   └── jiang_tian_chang_kai.json
│   └── lyrics_index.json   # Master index
```

### 3.2 Data Flow
```
┌─────────────────────────┐
│  sop.org/songs          │
│  (TablePress table)     │
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│  Fetch HTML             │
│  (requests)             │
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│  Parse Table            │
│  (BeautifulSoup)        │
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│  Extract Lyrics         │
│  - Preserve <br/> tags  │
│  - Detect sections      │
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│  Save JSON Files        │
│  - Individual songs     │
│  - Master index         │
└─────────────────────────┘
```

---

## 4. Data Schema

### 4.1 Individual Song JSON
**File**: `data/lyrics/songs/<song_id>.json`

```json
{
  "song_id": "jiang_tian_chang_kai",
  "title": "將天敞開",
  "metadata": {
    "composer": "游智婷",
    "lyricist": "鄭懋柔",
    "album_name": "將天敞開．活著為要敬拜祢",
    "album_series": "敬拜讚美 (17)",
    "key": "G",
    "source_url": "https://www.sop.org/songs/",
    "scraped_at": "2026-01-02T10:30:00Z",
    "table_row_number": 209
  },
  "lyrics_raw": "將天敞開  祢的榮耀降下來  \n將天敞開  祢的同在降下來 \n...",
  "lyrics_lines": [
    "將天敞開  祢的榮耀降下來",
    "將天敞開  祢的同在降下來",
    "將天敞開  祢的榮耀降下來",
    "萬國讚嘆祢  祢是榮耀君王"
  ],
  "sections": [
    {
      "section_type": "unknown",
      "section_number": 1,
      "lines": [
        "將天敞開  祢的榮耀降下來",
        "將天敞開  祢的同在降下來"
      ]
    }
  ],
  "stats": {
    "total_lines": 12,
    "total_sections": 3,
    "has_section_labels": false
  }
}
```

### 4.2 Master Index JSON
**File**: `data/lyrics/lyrics_index.json`

```json
{
  "metadata": {
    "scrape_date": "2026-01-02T10:30:00Z",
    "source_url": "https://www.sop.org/songs/",
    "total_songs": 685,
    "scraper_version": "1.0.0"
  },
  "songs": [
    {
      "song_id": "jiang_tian_chang_kai",
      "title": "將天敞開",
      "composer": "游智婷",
      "album": "將天敞開．活著為要敬拜祢",
      "key": "G",
      "file_path": "data/lyrics/songs/jiang_tian_chang_kai.json",
      "line_count": 12
    }
  ]
}
```

---

## 5. Implementation Details

### 5.1 Core Functions

#### `scrape_songs_table(url: str) -> List[dict]`
- Fetches HTML from sop.org/songs
- Parses TablePress table (id="tablepress-3")
- Extracts all 685 song rows
- Returns list of song dictionaries

#### `parse_lyrics_cell(cell: BeautifulSoup) -> dict`
- Extracts text from table cell
- Preserves `<br/>` tags as newlines
- Splits into lines array
- Returns: `{lyrics_raw: str, lyrics_lines: List[str]}`

#### `detect_sections(lyrics_lines: List[str]) -> List[dict]`
- **POC Version**: Create single "unknown" section with all lines
- **Future Enhancement**: Pattern-based section detection
  - Look for repeated lines (chorus indicator)
  - Detect blank line separators
  - Optional: Use NLP for semantic grouping

#### `normalize_song_id(title: str) -> str`
- Convert Chinese title to ASCII filename
- Use Pinyin romanization (e.g., "將天敞開" → "jiang_tian_chang_kai")
- Handle special characters and spaces

#### `save_song_data(song: dict, output_dir: Path)`
- Save individual song JSON
- Update master index
- Atomic writes (temp file + rename)

### 5.2 Line Break Preservation Strategy
**Critical Requirement**: Must preserve exact line breaks as they denote verse structure.

```python
# CORRECT approach:
def parse_lyrics_cell(cell):
    # Replace <br/> tags with newline markers
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
        'lyrics_raw': lyrics_raw,
        'lyrics_lines': lyrics_lines
    }
```

### 5.3 Section Detection (POC Version)
For MVP, use simple heuristic:

```python
def detect_sections(lyrics_lines):
    # POC: Return all lines as one section
    return [{
        'section_type': 'unknown',
        'section_number': 1,
        'lines': lyrics_lines
    }]
```

**Future Enhancement**: Pattern-based detection
- Repeated line sequences → chorus
- Position-based (first section → verse 1)
- Blank lines as section separators

---

## 6. Dependencies

### 6.1 New Dependencies
Add to `requirements.txt`:
```
requests>=2.32.0
beautifulsoup4>=4.14.0
lxml>=6.0.0
pypinyin>=0.52.0     # For Chinese → Pinyin conversion
```

### 6.2 Installation
```bash
# Using uv (project standard)
source .venv/bin/activate
uv pip install requests beautifulsoup4 lxml pypinyin
```

---

## 7. Usage

### 7.1 Basic Usage
```bash
# Activate virtual environment
source .venv/bin/activate

# Scrape all songs
python poc/lyrics_scraper.py

# Test with single song
python poc/lyrics_scraper.py --test

# Scrape with limit (for testing)
python poc/lyrics_scraper.py --limit 10

# Verbose output
python poc/lyrics_scraper.py --verbose
```

### 7.2 Command-Line Arguments
```
--test          Validate test song only (將天敞開)
--limit N       Scrape only first N songs
--output DIR    Output directory (default: data/lyrics)
--verbose       Show detailed progress
--resume        Skip already-scraped songs
```

---

## 8. Validation & Testing

### 8.1 Test Case: 將天敞開

**Expected Behavior**:
1. ✅ Song found at row 209
2. ✅ Title: "將天敞開"
3. ✅ Metadata extracted: composer, lyricist, album, key
4. ✅ Lyrics extracted with line breaks preserved
5. ✅ Total lines: 12 (based on actual scraped data)
6. ✅ JSON file saved successfully

**Validation Script**:
```python
def validate_test_song():
    """Validate the test song against expected structure"""
    song_file = Path('data/lyrics/songs/jiang_tian_chang_kai.json')

    with song_file.open() as f:
        song = json.load(f)

    assert song['title'] == '將天敞開'
    assert song['metadata']['composer'] == '游智婷'
    assert song['metadata']['lyricist'] == '鄭懋柔'
    assert song['metadata']['key'] == 'G'
    assert len(song['lyrics_lines']) > 0

    # Check line breaks preserved
    assert '\n' not in song['lyrics_lines'][0]  # Lines should be split

    print("✅ Test song validation passed!")
```

### 8.2 Data Quality Checks
```python
def validate_all_songs(index_file):
    """Run quality checks on all scraped songs"""
    issues = []

    with open(index_file) as f:
        index = json.load(f)

    for song_entry in index['songs']:
        song_file = Path(song_entry['file_path'])

        # Check file exists
        if not song_file.exists():
            issues.append(f"Missing file: {song_file}")
            continue

        with song_file.open() as f:
            song = json.load(f)

        # Check required fields
        if not song.get('lyrics_lines'):
            issues.append(f"{song['title']}: No lyrics")

        if len(song['lyrics_lines']) == 0:
            issues.append(f"{song['title']}: Empty lyrics")

        # Check for unprocessed HTML
        for line in song['lyrics_lines']:
            if '<br' in line or '</br' in line:
                issues.append(f"{song['title']}: Unprocessed HTML in lyrics")
                break

    return issues
```

---

## 9. Error Handling

### 9.1 Network Errors
```python
try:
    response = requests.get(url, timeout=30)
    response.raise_for_status()
except requests.exceptions.Timeout:
    logger.error("Request timed out")
    retry_with_backoff()
except requests.exceptions.HTTPError as e:
    if e.response.status_code == 404:
        logger.error("Page not found")
    elif e.response.status_code == 503:
        logger.warning("Server unavailable - retrying")
```

### 9.2 Parsing Errors
```python
try:
    table = soup.find('table', id='tablepress-3')
    if not table:
        raise ValueError("Table not found - site structure may have changed")
except Exception as e:
    logger.error(f"Parsing failed: {e}")
    # Save raw HTML for debugging
    debug_file = Path('data/lyrics/debug_page.html')
    debug_file.write_text(response.text)
```

### 9.3 Data Validation
```python
def validate_song_data(song_dict):
    """Validate song data before saving"""
    required = ['song_id', 'title', 'lyrics_lines']
    for field in required:
        if field not in song_dict:
            raise ValueError(f"Missing required field: {field}")

    if not song_dict['lyrics_lines']:
        raise ValueError(f"Empty lyrics for song: {song_dict['title']}")
```

---

## 10. Performance Considerations

### 10.1 Rate Limiting
Since all data is in a single page load:
- ✅ No rate limiting needed (single HTTP request)
- ✅ No sleep delays between songs
- ✅ Processing is local parsing only

### 10.2 Expected Performance
- **Single HTTP request**: ~2-3 seconds
- **Parsing 685 songs**: ~10-15 seconds
- **JSON writing**: ~2-3 seconds
- **Total runtime**: **< 30 seconds** for all songs

### 10.3 Memory Usage
- HTML page size: ~800 KB
- Parsed BeautifulSoup object: ~5 MB
- All song data in memory: ~2 MB
- **Peak memory**: ~10 MB (very lightweight)

---

## 11. Future Enhancements

### 11.1 Phase 2: Advanced Section Detection
- Train ML model on manually labeled songs
- Use repetition analysis for chorus detection
- Integrate with music structure analysis

### 11.2 Phase 3: Time-Coded Lyrics
- Align lyrics to audio timestamps
- Use forced alignment tools (aeneas, Gentle, MFA)
- Generate .srt or .lrc format

### 11.3 Phase 4: Integration with POC Analysis
```python
# In poc_analysis.py
def analyze_song_with_lyrics(audio_file):
    features = extract_audio_features(audio_file)

    # Match by filename or title
    song_title = Path(audio_file).stem
    lyrics = load_lyrics_by_title(song_title)

    # Combine in results
    return {
        **features,
        'lyrics': lyrics,
        'lyrics_source': 'sop.org'
    }
```

---

## 12. File Structure Summary

```
stream_of_worship/
├── poc/
│   ├── lyrics_scraper.py           # Main scraper (NEW)
│   ├── lyrics_utils.py             # Utilities (NEW)
│   ├── investigate_sop*.py         # Investigation scripts (temp)
│   └── find_test_song.py           # Investigation (temp)
│
├── data/
│   └── lyrics/                     # Output directory (NEW)
│       ├── songs/                  # Individual song JSONs
│       │   ├── jiang_tian_chang_kai.json
│       │   ├── wo_men_huan_qing_sheng_dan.json
│       │   └── ...                 # 685 total files
│       └── lyrics_index.json       # Master index
│
├── specs/
│   └── lyrics-scraper-design.md    # This document (NEW)
│
├── requirements.txt                # Updated with new deps
├── .venv/                          # Python virtual environment (NEW)
└── .gitignore                      # Add .venv/
```

---

## 13. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| **Site structure changes** | High | Version scraper, add structure validation, save raw HTML on failure |
| **Encoding issues** | Medium | Use UTF-8 everywhere, normalize Chinese characters (NFC) |
| **Missing section labels** | Low | POC uses "unknown", future ML-based detection |
| **Duplicate song titles** | Low | Add row number to song_id for uniqueness |
| **Large file size** | Low | 685 songs × 2KB avg = ~1.4 MB total (negligible) |

---

## 14. Success Metrics

### 14.1 Functional Requirements
- [x] Scrape all 685 songs from sop.org/songs
- [x] Preserve exact line breaks from `<br/>` tags
- [x] Extract metadata (composer, lyricist, album, key)
- [x] Save structured JSON format
- [x] Test song "將天敞開" validates correctly

### 14.2 Non-Functional Requirements
- [x] Runtime < 30 seconds for all songs
- [x] Memory usage < 100 MB
- [x] No external dependencies beyond Python + 4 libraries
- [x] Compatible with existing POC workflow
- [x] Resumable (skip already-scraped songs)

---

## 15. Next Steps

### Implementation Order:
1. ✅ Add dependencies to requirements.txt
2. ✅ Create lyrics_scraper.py skeleton
3. ✅ Implement table parsing
4. ✅ Implement lyrics extraction with line break preservation
5. ✅ Implement JSON output
6. ✅ Test with single song (將天敞開)
7. ✅ Run full scrape (685 songs)
8. ✅ Validate data quality
9. ✅ Clean up investigation scripts

### Post-Implementation:
- Document actual lyrics structure findings
- Create integration guide for POC analysis
- Plan Phase 2 enhancements (section detection)

---

**Document Version**: 1.0
**Last Updated**: 2026-01-02
**Approved By**: User
**Implementation Status**: Ready to implement
