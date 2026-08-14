# Known Failure Cases — Heuristic Parser Limitations

This document records the specific failure modes in
`_XgP0p-S4S8_description.txt` that the current regex heuristic parser
(`parse_structured_lyrics`) mishandles. The LLM path
(`extract_structured_lyrics_with_llm`) is expected to fix these.

## Failure cases

### 1. Mid-section promo lines (lines 24, 31-33)

The heuristic's `_is_trailing_non_lyric` only drops junk lines that appear
**after a blank-line gap** following the last section's lyric block
(`_TRAILING_NON_LYRIC_HINTS` check at `structured_lyrics.py:104`).
Promo/junk lines that appear **between** lyric lines within a section
(without a blank-line gap) are NOT detected and get included as lyric
content.

**Example:** The trailing block after `[Chorus]` (lines 31-33) contains:
```
▶ 敬拜播放清單：https://www.youtube.com/playlist
追蹤我們的Facebook：fb.me/streamofworship
版權所有 © 讚美之泉音樂事工
```
These appear after a blank-line gap, so the heuristic's trailing-junk
filter catches some of them (via `http`, `@`, `▶` hints). However, the
line `追蹤我們的Facebook：fb.me/streamofworship` does NOT match any
`_TRAILING_NON_LYRIC_HINTS` pattern and would be incorrectly included
as a lyric line.

### 2. Preamble junk lines (lines 1-9)

Lines before the first section tag go into `preamble_lines`. The heuristic
correctly separates them, but they include URLs, social handles, and promo
text mixed with song metadata. The heuristic does not distinguish between
song metadata (title, album) and pure promo junk in the preamble.

### 3. Chinese section labels (deferred to v2)

If a description uses Chinese section labels like `副歌` (Chorus) or
`主歌` (Verse) instead of `[Chorus]`/`[Verse]`, the regex
`_SECTION_TAG_RE` (`^\[<label>\]$`) does not match them. The LLM path
can recognize these, but Chinese-label normalization is explicitly out
of scope for v1.

### 4. Inline tags (deferred to v2)

Tags that appear inline with lyric text (e.g., `Line 1 [Chorus] Line 2`)
are not split into separate sections by the heuristic. The LLM path
can handle this, but it's not a common pattern in the fixture.
