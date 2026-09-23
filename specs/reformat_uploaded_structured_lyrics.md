# Reformat `lyrics upload-structured` raw text to canonical section style

## Context

`sow-admin lyrics upload-structured` (ops/admin-cli/src/stream_of_worship/admin/commands/lyrics.py:997) currently stores `structured_lyrics_raw` as the **verbatim input file text**. The user wants the stored raw text reformatted to a canonical style: one `[label]` header per section, its lyric lines, and a **blank line between sections** — e.g.

```
[verse 1]
line 1
line 2

[chorus]
line 1
line 2
```

Decisions confirmed by user: drop preamble lines; store the reformatted text in `structured_lyrics_raw` (parsed JSON unchanged); use heuristic-only parsing (existing `parse_structured_lyrics`, no LLM); label style = lowercase canonical, matching what the Analysis Service's `identify_from_structured_lyrics` expects (ops/analysis-service/src/sow_analysis/workers/components.py:1221 `_LABEL_TO_COMPONENT_TYPE` maps lowercase labels like `verse 1` → `verse`, `prechorus`).

Key fact: `flatten_structured_lyrics()` (ops/admin-cli/src/stream_of_worship/admin/services/structured_lyrics.py:277) renders `[raw_label]` + lines but (a) uses the original-case `raw_label`, (b) omits blank lines between sections. The canonical format needs lowercase `label` + blank-line separators, so a new function is required (no equivalent exists). Do NOT modify `flatten_structured_lyrics` itself: it has other callers with their own contracts — `lrc_jobs.resolve_lyrics_text` (services/lrc_jobs.py:50, builds lyrics_text for LRC jobs), `audio.py:1854` (recording info display panel), `lyrics.py:1137` (`view-structured` JSON fallback), plus tests in tests/admin/services/test_structured_lyrics.py and lab/skills/eval-models-for-fixing-youtube-transcription/scripts/make_fixture.py:94.

## Approach

1. **Add `format_structured_lyrics_canonical(structured: dict) -> str`** to ops/admin-cli/src/stream_of_worship/admin/services/structured_lyrics.py (after `flatten_structured_lyrics`).
   - Signature: `def format_structured_lyrics_canonical(structured: dict) -> str:`
   - For each section in `structured.get("sections", [])`: emit `f"[{label}]"` where `label = section.get("label", "")` (the lowercase normalized field), then each line in `section.get("lines", [])`. Sections with zero lines still emit their header (info-preserving; `identify_from_structured_lyrics` skips empty sections anyway). Do NOT emit `preamble_lines`.
   - Join sections with a blank line: i.e. build each section block as `"\n".join([f"[{label}]", *lines])`, then `"\n\n".join(blocks)`. Empty sections list → `""`.
   - Docstring: states canonical output style and that it lowercases labels and drops preamble lines, unlike `flatten_structured_lyrics`.

2. **Use it in the command** — in `lyrics_upload_structured` (lyrics.py:997):
   - After `parsed = parse_structured_lyrics(content)` validation, compute `formatted = format_structured_lyrics_canonical(parsed)`.
   - Change the DB write (lyrics.py:1074-1078): `structured_lyrics_raw=formatted` instead of `content`. `structured_lyrics=json.dumps(parsed, ensure_ascii=False)` unchanged.
   - Preview panel: keep existing section-count preview lines; add one line when `formatted != content.rstrip("\n")`: `[yellow]Raw text will be reformatted to canonical style (lowercase labels, blank line between sections)[/yellow]`. This shows in the preview BEFORE the confirmation prompt, so the operator knows what will be stored.
   - Import: add `format_structured_lyrics_canonical` to the existing `from stream_of_worship.admin.services.structured_lyrics import (...)` block at lyrics.py:52-55.
   - Existing advisory hints (LRC status, components check) untouched.

3. **Update tests** — ops/admin-cli/tests/admin/test_lyrics_upload_structured.py:
   - `test_upload_structured_happy_path` currently asserts `kwargs["structured_lyrics_raw"] == SECTION_TAGGED_TEXT` — change to assert the canonical form: `"[verse]\nLine 1\nLine 2\n\n[chorus]\nChorus line 1\nChorus line 2\nChorus line 3"`.
   - Add `test_upload_structured_reformats_raw_text`: input file containing preamble lines, blank lines, mixed-case labels, and trailing promo junk (e.g. `"Song Title\n\n[Verse 1]\n  Line 1  \n\n[CHORUS]\nChorus line\n\nhttps://youtube.com/..."`) → stored raw equals exactly `"[verse 1]\nLine 1\n\n[chorus]\nChorus line"` (whitespace-stripped lines, lowercase labels, preamble and promo dropped, blank line between sections). Reuse `_invoke_upload` helper; the helper writes `SECTION_TAGGED_TEXT`, so for this test write the custom fixture to `tmp_path / "lyrics.txt"` before invoking (mirror the pattern in `test_upload_structured_no_section_tags`, lines 131-150, which inlines its own file + mocks instead of the helper).
   - `view-structured` tests unchanged: `test_view_structured_prefers_raw_text` stores its own `structured_lyrics_raw` value, unaffected.

No other callsites change: the analysis service consumes `structured_lyrics` JSON (not raw); `fetch_structured_lyrics`/audio.py backfill paths write their own raw values and are out of scope.

## Critical files & anchors

- ops/admin-cli/src/stream_of_worship/admin/commands/lyrics.py — `lyrics_upload_structured` (lines 997-1105): DB write at 1074, preview at 1048-1068, imports at 52-55.
- ops/admin-cli/src/stream_of_worship/admin/services/structured_lyrics.py — new `format_structured_lyrics_canonical` after `flatten_structured_lyrics` (line 277); `parse_structured_lyrics` output shape at lines 61-118.
- ops/admin-cli/tests/admin/test_lyrics_upload_structured.py — `_invoke_upload` helper (line 46), `test_upload_structured_happy_path` (line 74).

## Verification

```bash
cd ops/admin-cli
uv run --python 3.11 --extra admin --extra test pytest tests/admin/test_lyrics_upload_structured.py tests/test_structured_lyrics.py -v
uv run --python 3.11 --extra admin --extra test pytest -v   # full non-integration suite
```

- New-behavior check: `test_upload_structured_reformats_raw_text` proves preamble/junk dropped, labels lowercased, blank line between sections in the stored raw text.
- Existing proof: happy-path assertion now pins canonical reformatting of `SECTION_TAGGED_TEXT`.
- Coverage gates: run `uv run --python 3.11 --extra admin --extra test pytest --cov=stream_of_worship.admin.commands.lyrics --cov=stream_of_worship.admin.services.structured_lyrics --cov-fail-under=100 -q` if the repo enforces 100% coverage on admin-cli (check pyproject; match existing gate).
