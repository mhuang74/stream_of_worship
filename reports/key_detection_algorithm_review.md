# Key Detection Algorithm Review

Generated: 2026-07-03 03:13:06 UTC

## Scope

This audit compares `recordings.musical_key` from the analysis service against `songs.musical_key` from the scraped catalog for active analyzed recordings. The headline metric is pitch-class/root only: enharmonic equivalents match, and `recordings.musical_mode` is ignored.

The nominal scraped key is useful reference data, but it is not guaranteed ground truth.

## Query

- `songs.deleted_at IS NULL`
- `recordings.deleted_at IS NULL`
- `recordings.analysis_status IN ('completed', 'partial')`
- main comparison requires non-empty nominal and detected keys

## Counts

| Metric | Rows |
| --- | ---: |
| Active analyzed rows | 100 |
| Candidate rows with both keys present | 98 |
| Included comparable rows | 98 |
| Exact pitch-class matches | 75 |
| Pitch-class mismatches | 23 |
| Unparseable key rows | 0 |
| Excluded missing-data rows | 2 |
| Rows missing nominal scraped key | 2 |
| Rows missing detected key | 0 |

## Headline Accuracy

- Match rate: 76.5% (75 / 98)
- Mismatch rate: 23.5% (23 / 98)
- Unparseable candidate rate: 0.0% (0 / 98)

## Match Rate by Key Confidence

| Confidence band | Comparable | Matches | Mismatches | Unparseable | Match rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| < 0.20 | 0 | 0 | 0 | 0 | n/a |
| 0.20-0.39 | 1 | 1 | 0 | 0 | 100.0% |
| 0.40-0.59 | 7 | 3 | 4 | 0 | 42.9% |
| 0.60-0.79 | 18 | 13 | 5 | 0 | 72.2% |
| >= 0.80 | 72 | 58 | 14 | 0 | 80.6% |
| missing | 0 | 0 | 0 | 0 | n/a |

## Mismatch Distance Distribution

| Shortest distance | Rows | Share of comparable rows |
| ---: | ---: | ---: |
| 0 semitones | 75 | 76.5% |
| 1 semitones | 0 | 0.0% |
| 2 semitones | 4 | 4.1% |
| 3 semitones | 2 | 2.0% |
| 4 semitones | 2 | 2.0% |
| 5 semitones | 15 | 15.3% |
| 6 semitones | 0 | 0.0% |

## Most Common Nominal-to-Detected Mismatch Pairs

| Nominal root | Detected root | Rows |
| --- | --- | ---: |
| F | C | 5 |
| D | A | 3 |
| F | G | 3 |
| G | D | 3 |
| C | G | 2 |
| E | Ab | 1 |
| D | G | 1 |
| E | G | 1 |
| A | E | 1 |
| C | D | 1 |
| F | A | 1 |
| G | E | 1 |

## High-Confidence Mismatch Examples

| Confidence | Distance | Nominal | Detected | Mode | Song | Recording | File |
| ---: | ---: | --- | --- | --- | --- | --- | --- |
| 0.925 | 3 | E (Em) | G (G) | major | 讓我尋見祢 | 2b051828f3a9 | 【讓我尋見祢 Seek And Find】官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美 (26).mp3 |
| 0.893 | 2 | F (F) | G (G) | major | 唯有主耶穌的寶血 | fe4f11da783d | 唯有主耶穌的寶血 Nothing But The Blood.mp3 |
| 0.882 | 5 | G (G-A) | D (D) | major | 深不見底的愛 | 872698a1f72c | 【深不見底的愛 Endless Love】官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美 (26).mp3 |
| 0.878 | 5 | D (D) | A (A) | major | Holy, Holy [聖潔榮耀主] | 4d2cc303b5d5 | 【Holy, Holy 聖潔榮耀主】官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美 (30).mp3 |
| 0.865 | 5 | D (D-F) | A (A) | major | 數不盡 | 777480bb9d96 | 【數不盡 Grace Beyond All Measure】官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美 (28).mp3 |
| 0.853 | 5 | C (C) | G (G) | major | 我的耶穌 | 675d313d8ba3 | 【我的耶穌 My Jesus】官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美 (30).mp3 |
| 0.853 | 4 | E (E) | Ab (G#) | minor | 藏身之處 | 36a6e4e0912b | 【藏身之處 My Hiding Place】官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美 (29).mp3 |
| 0.837 | 5 | C (C) | G (G) | major | 我敬拜祢，耶穌 | 28f09e4d4382 | 【我敬拜祢，耶穌 I Worship You, Jesus】官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美 (29).mp3 |
| 0.832 | 5 | F (F-G) | C (C) | major | 曠野中唯一的力量 | c629d964cacf | 【曠野中唯一的力量 My Strength In The Wilderness】現場敬拜MV (Worship MV) - 讚美之泉敬拜讚美 (27).mp3 |
| 0.827 | 5 | G (G-A) | D (D) | major | 頌讚歸於祢 | 441e02a0dbc7 | 【頌讚歸於祢 Taste And See】官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美 (26).mp3 |
| 0.824 | 5 | F (F) | C (C) | major | 我能給你什麼 | 41f1236fc61a | 【我能給你什麼 What Could I Bring To You】官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美 (29).mp3 |
| 0.822 | 5 | F (F-G) | C (C) | major | 聖潔和榮耀 | 685609a3f62e | 【聖潔和榮耀 Holy And Glorious】官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美 (27).mp3 |
| 0.818 | 5 | F (F-G) | C (C) | major | 愛使我們勇敢 | 4814d8d1131e | 【愛使我們勇敢 Love Can Make Us Brave ⧸ 我們愛 We Will Love】現場敬拜MV (Worship MV) - 讚美之泉敬拜讚美 (28).mp3 |
| 0.812 | 5 | F (F) | C (C) | major | 美好的創造 | 196669aa37bd | 【美好的創造 Beautifully Made】官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美 (22).mp3 |
| 0.799 | 5 | D (D) | A (A) | major | 不管世界如何看我 | 1991e5d50a4d | 【不管世界如何看我 No Matter How The World Sees Me】官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美 (29).mp3 |
| 0.792 | 2 | F (F-G) | G (G) | major | 賜福在這地 | 780d0c81d058 | 【賜福在這地 Send Thy Blessing On This Land】官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美 (28).mp3 |
| 0.669 | 5 | A (A-B-C) | E (E) | major | 十架的大能 | 031f3f721fb3 | 【十架的大能 The Power Of The Cross】官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美 (28).mp3 |
| 0.654 | 5 | D (D-Eb-F) | G (G) | major | 得勝的宣告 | 13c379af0bdd | 【得勝的宣告 You Are My Victory】官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美 (25).mp3 |
| 0.644 | 2 | F (F) | G (G) | minor | 偉大的神 | a3925e3d94bc | 【偉大的神 Great Is Our God】官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美 (25).mp3 |
| 0.579 | 3 | G (G-A) | E (E) | minor | 爭戰得勝在於祢 | f5272b32c0cb | 【爭戰得勝在於祢 The Battle Belongs To You】官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美 (27).mp3 |

## Unparseable Nominal or Detected Keys

| Nominal key | Detected key | Song | Recording |
| --- | --- | --- | --- |

## Diagnostic Findings

- Low-confidence mismatches below 0.40: 0 (0.0% of mismatches).
- Relative-major/minor-style distances (3 or 4 semitones): 4 (17.4% of mismatches).
- Neighbor-key distances (1 semitone): 0 (0.0% of mismatches).
- Tritone-distance mismatches (6 semitones): 0 (0.0% of mismatches).

The current implementation in `ops/analysis-service/src/sow_analysis/workers/analyzer.py` loads mono audio, computes `librosa.feature.chroma_cqt`, averages chroma over the full track, then selects the best correlation among 24 rolled major/minor Krumhansl-Schmuckler profiles. That design is simple and deterministic, but full-track averaging makes it sensitive to non-tonic intros/outros, medleys, modulations, extended bridges, dense vocal arrangements, and live recordings where accompaniment energy does not strongly represent the sung tonic.

## Ranked Fix Recommendations

1. Segment-aware key voting: compute chroma/key per section or sliding window, weight by stable high-energy vocal/accompaniment sections, and choose a consensus tonic instead of one full-track average.
2. Persist diagnostic scores: store top-N key candidates, score margin between first and second candidate, and an algorithm version. The current single correlation value does not show ambiguity well enough for thresholding or review.
3. Add confidence policy: mark low-margin or low-correlation keys as unverified instead of publishing a hard key. Use the confidence-band results above to set the first threshold.
4. Improve reference normalization and review UX: normalize scraped keys into pitch-class columns and surface high-confidence mismatches for manual correction because scraped keys are nominal, not guaranteed ground truth.
5. Test alternate chroma extraction: compare CQT chroma with HPCP or beat-synchronous chroma, and test stem-informed analysis where vocals or accompaniment dominate failures.

## Validation

- Normalization self-tests passed for `C# == Db`, `Bb == A#`, `F# minor == Gb`, mode-insensitive root matching, and empty/null/unrecognized exclusions.
- Database access used a read-only transaction.
