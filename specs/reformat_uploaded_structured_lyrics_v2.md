# Reformat `lyrics upload-structured` raw text to canonical section style (v2)

Supersedes `specs/reformat_uploaded_structured_lyrics.md` (v1, kept as history). Changes from v1: canonical format fully pinned (LF, single trailing newline, lowercase-only labels), `view-structured` exports canonical text, plus review fixes (test helper contract, verification paths, docstring semantics, preview comparison).

## Context

`sow-admin lyrics upload-structured` (ops/admin-cli/src/stream_of_worship/admin/commands/lyrics.py:997 `lyrics_upload_structured`) currently stores `structured_lyrics_raw` as the **verbatim input file text**. Store a canonical reformatting instead: one `[label]` header per section, its lyric lines, a blank line between sections, LF endings, and a single trailing newline — e.g.

```
[verse 1]
line 1
line 2

[chorus]
line 1
line 2
```

Confirmed decisions (user, 2026-09-23):
- Drop preamble lines; store the reformatted text in `structured_lyrics_raw`; parsed JSON (`structured_lyrics`) unchanged; heuristic-only parsing (existing `parse_structured_lyrics`, no LLM).
- Labels: lowercase only — never alias-map (do NOT convert `[Pre-Chorus]` → `[prechorus]`); unrecognised labels keep their text, lowercased.
- Accept loss of verbatim text (trailing promo lines, original case/whitespace/blank-line layout are unrecoverable). No new DB column. Preamble already survives in `structured_lyrics` JSON `preamble_lines`.
- Legacy rows: leave verbatim; no migration command. Mixed formats in the DB are expected — `view-structured` and the audio.py raw panels will show both styles across songs. Not a bug.
- Scope: `upload-structured` only. The `fetch_structured_lyrics` backfill paths (ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:554, 806, 5725) keep storing verbatim YouTube descriptions — those inputs are uncleaned and rely on LLM cleanup (`parse_structured_lyrics_smart`); do NOT touch audio.py.
- Preview UX: one yellow notice line before the confirmation prompt (no diff display).
- Round-trip: `lyrics view-structured` renders canonical text (step 3), so re-uploading an export reproduces the same canonical form.
- No version marker in JSON or raw text; no parse-back guard.
- Rationale note: the analysis service's `identify_from_structured_lyrics` (ops/analysis-service/src/sow_analysis/workers/components.py:1286) consumes the `structured_lyrics` JSON `label` field (already lowercase), never the raw text — canonical raw style is a storage/consistency choice, not a functional dependency.

## Approach

1. **Add `format_structured_lyrics_canonical(structured: dict) -> str`** to ops/admin-cli/src/stream_of_worship/admin/services/structured_lyrics.py, directly after `flatten_structured_lyrics` (~line 277). No equivalent function exists. Do NOT modify `flatten_structured_lyrics` — its callers have their own contracts: `lrc_jobs.resolve_lyrics_text` (services/lrc_jobs.py:50), `audio.py:1854` info panel, `lyrics.py:1137` view fallback, tests in tests/admin/services/test_structured_lyrics.py, and lab/skills/eval-models-for-fixing-youtube-transcription/scripts/make_fixture.py:94.
   - Signature: `def format_structured_lyrics_canonical(structured: dict) -> str:`
   - For each section in `structured.get("sections", [])`: emit `f"[{label}]"` where `label = section.get("label", "")` (the lowercase normalized field — NOT `raw_label`), then each line in `section.get("lines", [])`. Sections with zero lines still emit their header (info-preserving; `identify_from_structured_lyrics` skips empty sections anyway). Never emit `preamble_lines`.
   - Build each block as `"\n".join([f"[{label}]", *lines])`; join blocks with `"\n\n"`; append a single trailing `"\n"`. Empty sections list → return `""` (no trailing newline).
   - Output is always LF (input CRLF is irrelevant: `parse_structured_lyrics` already strips `\r` and strips each line).
   - Docstring: states the canonical style (lowercase labels, blank line between sections, single trailing newline, preamble dropped) and that it differs from `flatten_structured_lyrics` (which preserves `raw_label` case and omits blank lines).

2. **Use it in `lyrics_upload_structured`** (lyrics.py:997-1105):
   - Import: add `format_structured_lyrics_canonical` to the existing `from stream_of_worship.admin.services.structured_lyrics import (...)` block at lyrics.py:52-55.
   - After `parsed = parse_structured_lyrics(content)` validation, compute `formatted = format_structured_lyrics_canonical(parsed)`.
   - Preview panel (lines 1048-1068): keep the existing section-count lines; add, when `formatted != content`:
     `[yellow]Raw text will be reformatted to canonical style (lowercase labels, blank line between sections)[/yellow]`
     shown BEFORE the confirmation prompt. Comparison is against `content` verbatim (not `content.rstrip("\n")`): `formatted` already carries the trailing newline, so an already-canonical input file (with trailing newline) produces no notice, while any real reformat does.
   - DB write (lyrics.py:1074-1078): `structured_lyrics_raw=formatted` instead of `content`; `structured_lyrics=json.dumps(parsed, ensure_ascii=False)` unchanged.
   - Existing advisory hints (LRC status, components check) untouched.

3. **Make `lyrics view-structured` render canonical** (lyrics.py:1109-1166 `lyrics_view_structured`):
   - Rendering rule: if `recording.structured_lyrics` exists and `json.loads` succeeds → `text = format_structured_lyrics_canonical(json.loads(recording.structured_lyrics))` (canonical export for round-trip re-upload; also canonicalizes legacy verbatim rows on display/export). Else if `recording.structured_lyrics_raw` → `text = recording.structured_lyrics_raw` unchanged (JSON-absent/malformed edge). Else → existing "No structured lyrics" exit path.
   - Keep the `[dim]Note: raw section-tagged text unavailable — re-rendered…[/dim]` message only on the JSON-only branch (no raw column), as today.
   - `--output` write and console print both use `text` (unchanged mechanics). Canonical text ends with `\n`, so exported files end with a newline; the raw-fallback branch writes raw as-is.
   - `format_structured_lyrics_canonical` is already imported in this module per step 2.

4. **Docstring semantics**: update `update_recording_structured_lyrics`'s `structured_lyrics_raw` docstring (ops/admin-cli/src/stream_of_worship/admin/db/client.py:994): the column holds canonical reformatted text for `lyrics upload-structured`, verbatim input for other callers (`fetch_structured_lyrics` backfill).

5. **Update tests** — ops/admin-cli/tests/admin/test_lyrics_upload_structured.py:
   - `test_upload_structured_happy_path` (line 74): change `kwargs["structured_lyrics_raw"] == SECTION_TAGGED_TEXT` to `== "[verse]\nLine 1\nLine 2\n\n[chorus]\nChorus line 1\nChorus line 2\nChorus line 3\n"`.
   - Helper fix: add keyword param `text: str = SECTION_TAGGED_TEXT` to `_invoke_upload` (lines 46-73) and write THAT to `lyrics.txt` instead of the hardcoded constant (v1 bug: the helper unconditionally overwrote a custom fixture). Existing callers unchanged (default).
   - Add `test_upload_structured_reformats_raw_text`: fixture `"Song Title\n\n[Verse 1]\n  Line 1  \n\n[CHORUS]\nChorus line\n\nhttps://youtube.com/watch?v=x"` passed via `text=` → assert `kwargs["structured_lyrics_raw"] == "[verse 1]\nLine 1\n\n[chorus]\nChorus line\n"` (preamble and promo dropped, lines whitespace-stripped, labels lowercased, blank line between sections, trailing newline). Also assert the reformat notice appears in `result.output`.
   - Add `test_upload_structured_no_reformat_notice_when_canonical`: input already canonical (including trailing newline) → the notice is absent from `result.output`.
   - `view-structured` tests:
     - `test_view_structured_prefers_raw_text` → repin: with BOTH JSON and raw present, rendered text is canonical from JSON. Assert `[verse]` in output, `[Verse]` not in output, `"json line"` not in output.
     - `test_view_structured_falls_back_to_json` → expects canonical render: `[verse]`/`[chorus]` lowercase, blank line between sections; "re-rendered" note still present (no raw column).
     - `test_view_structured_output_flag_writes_file` → exported file text equals `format_structured_lyrics_canonical(structured)` (canonical, trailing newline).
   - Add `test_view_structured_round_trips_canonical`: upload via `_invoke_upload` (capture `kwargs["structured_lyrics_raw"]` from the db mock), then invoke `view-structured song_0001 --output <tmp>/out.txt` against a recording built with that raw + the same JSON; assert the file content equals the stored raw exactly.

6. **Unit tests for the formatter** — ops/admin-cli/tests/admin/services/test_structured_lyrics.py, mirroring the existing `flatten_structured_lyrics` tests (~line 165): mixed-case labels, CRLF input, empty sections list → `""`, section with zero lines still emits header, single trailing newline.

## Critical files & anchors

- ops/admin-cli/src/stream_of_worship/admin/services/structured_lyrics.py — new `format_structured_lyrics_canonical` after `flatten_structured_lyrics` (~line 277); `parse_structured_lyrics` shape at lines 59-118.
- ops/admin-cli/src/stream_of_worship/admin/commands/lyrics.py — `lyrics_upload_structured` (997-1105: DB write 1074, preview 1048-1068, imports 52-55); `lyrics_view_structured` (1109-1166).
- ops/admin-cli/src/stream_of_worship/admin/db/client.py — docstring at 994.
- ops/admin-cli/tests/admin/test_lyrics_upload_structured.py — `_invoke_upload` (46), happy path (74), view-structured tests (178-263).
- ops/admin-cli/tests/admin/services/test_structured_lyrics.py — formatter unit tests near existing flatten tests (~line 165).

## Verification

```bash
cd ops/admin-cli
uv run --python 3.11 --extra admin --extra test pytest tests/admin/test_lyrics_upload_structured.py tests/admin/services/test_structured_lyrics.py -v
uv run --python 3.11 --extra admin --extra test pytest -v   # full non-integration suite
```

- New-behavior check: `test_upload_structured_reformats_raw_text` proves preamble/promo dropped, labels lowercased, blank-line separation, trailing newline in the stored raw text.
- Round-trip proof: `test_view_structured_round_trips_canonical` — export equals stored raw exactly.
- Existing proof: happy-path assertion pins canonical reformatting of `SECTION_TAGGED_TEXT`.
- No coverage gate exists (`pyproject.toml` addopts = `-m 'not integration'`); do not add one.
- Smoke test if a dev DB is available: `sow-admin lyrics upload-structured <song_id> <file>` — confirm the notice line appears for dirty input, then `sow-admin lyrics view-structured <song_id>` prints canonical text.

## Assumptions & contingencies

- If `json.loads` on `structured_lyrics` fails in `view-structured`, fall back to raw text (treat malformed JSON like the JSON-absent case) rather than crashing.
- If `_invoke_upload`'s default `text` param conflicts with an unforeseen caller, keep the keyword optional so existing callers are untouched.
