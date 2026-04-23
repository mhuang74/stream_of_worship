# Fix segment extraction in gen_lrc_qwen3_asr_local.py: phrase-level from text + per-char timestamps

## Context

Running the POC on `wo_yao_yi_xin_cheng_xie_mi_247`:

```
uv run --extra transcription python poc/gen_lrc_qwen3_asr_local.py \
  wo_yao_yi_xin_cheng_xie_mi_247 \
  --save-raw ./tmp_output -o ./tmp_output/out.txt \
  --no-lyrics-context --force-rerun
```

…emits a valid `asr_raw.json` but fails with:

```
Error: No valid text segments extracted (found 299 empty segments)
Error: No segments extracted from ASR result
```

Two problems need to be addressed together:

### Problem 1 — `extract_segments` reads dicts with `getattr`

`mlx_qwen3_asr.Session.transcribe()` returns a `TranscriptionResult` dataclass (see `.venv/…/mlx_qwen3_asr/transcribe.py:56-73`):

```python
@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str
    segments: Optional[list[dict]] = None   # list of DICTS
    chunks: Optional[list[dict]] = None
    speaker_segments: Optional[list[dict]] = None
```

In `poc/gen_lrc_qwen3_asr_local.py:113-126`, `extract_segments()` takes the `hasattr(result, "segments")` branch (the dataclass field is present), then reads each `seg` via `getattr(seg, "text", "")`. But `seg` is a dict, `getattr` on a dict does not access keys, so every segment is silently read as `""` → 299 "empty" segments.

### Problem 2 — segments are per-Chinese-character

Even once extracted correctly, `.segments` is emitted at **single-character** granularity (e.g. `{"text":"我","start":17.17,"end":17.49}`). Downstream `canonical_line_snap` and LRC writing both assume **phrase/line-level** segments — per-character segments would produce one LRC timestamp per character and near-zero snap scores.

`chunks` and `speaker_segments` are both `null` in this output, so they're not a source of coarser granularity.

**However**, the top-level `result.text` *does* contain phrase-level transcription with Chinese punctuation (`。`, `，`, etc.) as natural phrase delimiters, e.g. `"嗯。我要一心奉献你，在主神面前歌颂你。…"`. Verification in the actual output:

- `len(non_punct_chars_in_text) == len(segments) == 299`
- Every non-punctuation char in `text` matches the corresponding `segments[i]["text"]` in order.

So phrase-level segments can be reconstructed deterministically: walk `text`, split on punctuation, consume one per-char segment for each non-punct char, and take `start` from the first char's segment and `end` from the last char's segment.

## Recommended change

Rewrite `extract_segments()` in `poc/gen_lrc_qwen3_asr_local.py` to:

1. Read the raw output shape (dict or dataclass), pulling both `text` and `segments`.
2. Read each per-char segment's fields via a dict-first / getattr-fallback helper (fixes Problem 1 and future-proofs against an object-based segment type).
3. Group per-char segments into phrases by walking `text` and splitting on Chinese + ASCII sentence/phrase punctuation (see Punctuation set below), producing one phrase segment per delimited span:
   ```python
   {"text": phrase_text, "start": first_char_start, "end": last_char_end}
   ```
4. If the reconstruction hits a length mismatch (non-punct char count != segment count, or a seg text disagrees with the corresponding char), fall back to returning per-char segments and emit a clear warning — so the script still produces something and the bug is visible, rather than silently degrading.
5. Skip empty phrases (e.g. leading/trailing punctuation with no chars) and keep the existing empty-count reporting.

### Punctuation set

Full-width Chinese punctuation used as phrase/line delimiters, plus ASCII equivalents for safety:

```
。，、！？；：．
. , ! ? ; :
```

(Do **not** split on quotation marks, brackets, or the interjection `嗯` — those are not phrase boundaries.)

### Function signature

Keep `extract_segments(result) -> list[dict]` unchanged so the rest of the pipeline (caching, snap, LRC, diagnostic) is untouched. The returned dicts still have `start` / `end` / `text` keys — just phrase-scoped now.

### Minimal scope

- Only touch `extract_segments()` (lines 101–161).
- Do not refactor unrelated bugs in the file (unreachable echo at line 518–522, `valid_segments == 0` comparison on a list at line 215). Call these out separately if the user wants to address them.

## Critical files

- `poc/gen_lrc_qwen3_asr_local.py` — edit `extract_segments` (lines 101–161).
- `.venv/…/mlx_qwen3_asr/transcribe.py:56-73` — reference: confirms `segments: Optional[list[dict]]` and `text: str` on `TranscriptionResult`.
- `tmp_output/asr_raw.json` — reproduction artifact; phrase text lives at `.text`, per-char timing at `.segments`.

No tests exist for this POC script; no test changes required.

## Verification

1. Re-run the failing command verbatim:
   ```
   uv run --extra transcription python poc/gen_lrc_qwen3_asr_local.py \
     wo_yao_yi_xin_cheng_xie_mi_247 \
     --save-raw ./tmp_output -o ./tmp_output/out.txt \
     --no-lyrics-context --force-rerun
   ```
2. Expect stderr to report "Extracted N segments" where N is small (roughly the number of phrases in `text` — ~30–40 for this song, one per comma/period-delimited span), **not** 299.
3. `tmp_output/out.txt` is a non-empty LRC with phrase-level lines (e.g. `[00:17.17] 我要一心奉献你`), not one character per line.
4. `tmp_output/diagnostic.md` has a populated segment table with non-trivial snap scores (phrase-length ASR text scored against canonical lyric lines) and a non-zero "Replaced by snap" count.
5. Re-run without `--force-rerun` — should use the cached phrase-level segments (cache file was written on the previous run).

## Edge cases to watch in review

- Song starts/ends with `嗯` (filler) before the first phrase — ensure the leading/trailing filler becomes its own phrase or is filtered consistently (the text begins and ends with `嗯。`).
- A phrase containing zero non-punct chars (shouldn't happen, but guard with a skip).
- Char-count mismatch between `text` and `segments` — fall back to per-char with warning.
