# Fix: Canonical-Line Snap Traditional/Simplified Chinese Mismatch

## Problem

The canonical-line snap algorithm in `poc/gen_lrc_qwen3_asr_local.py` uses `fuzz.token_set_ratio` to match ASR output against canonical lyric lines. The SOP.org canonical lyrics are in **traditional Chinese**, but Qwen3-ASR (both local MLX and cloud DashScope variants) returns **simplified Chinese**.

`rapidfuzz.fuzz.token_set_ratio` does byte-exact Unicode character comparison. Traditional and simplified forms of the same character are different Unicode code points:

| Simplified | Traditional | Meaning |
|---|---|---|
| `颂` | `頌` | praise |
| `爱` | `愛` | love |
| `远` | `遠` | far/forever |
| `华` | `華` | glory/splendid |
| `为` | `為` | act/do |

Sample comparison from manual test (`wo_yao` song):
- ASR: `我要歌颂耶和华作为` (simplified)
- Canonical: `我要歌頌耶和華作為` (traditional)
- Score with raw comparison: ~0.58 — **below the 0.60 threshold, snap fails**

For heavily garbled lines like `咳嗽也和花作媒` vs `歌頌耶和華作為`, the score is ~0.25 regardless — these correctly don't snap. The mismatch problem specifically hits phonetically-correct simplified output.

## Root Cause

`fuzz.token_set_ratio` treats each Chinese character as a token (no spaces). Traditional and simplified forms are different code points, so they don't intersect in the token bag calculation. Lines that were correctly recognized phonetically but rendered in simplified characters score 0.50–0.70 — straddling the 0.60 threshold unpredictably.

## Fix

Normalize both the ASR text and canonical lines to simplified Chinese **before scoring only**. The output (`best_line`) remains the original traditional canonical line.

```python
from zhconv import convert

canonical_lines_simp = [convert(l, "zh-hans") for l in canonical_lines]

for seg in segments:
    asr_simp = convert(seg["text"], "zh-hans")
    scored = [
        (canonical_lines[i], fuzz.token_set_ratio(asr_simp, canonical_lines_simp[i]) / 100.0)
        for i in range(len(canonical_lines))
    ]
```

After fix: `我要歌颂耶和华作为` vs `我要歌颂耶和华作为` (both simplified) scores ~0.95 → snap succeeds.

## Files Changed

- `poc/gen_lrc_qwen3_asr_local.py` — `canonical_line_snap()` and `write_diagnostic()`
- `pyproject.toml` — add `zhconv>=1.4.0` to `poc_qwen3_local` extra

## Notes

- Same bug exists in `poc/gen_lrc_qwen3_asr.py` (cloud variant) — apply same fix there if needed.
- `zhconv` is pure Python, no native dependencies, no impact on other extras.
- The `祢` (you/thee, worship register) vs `你` (you, casual) distinction is **not** a traditional/simplified pair — both are traditional characters with different meanings. Lines using `祢` in canonical lyrics where ASR says `你` will still score ~0.85 (one character mismatch out of ~8) and snap correctly.
