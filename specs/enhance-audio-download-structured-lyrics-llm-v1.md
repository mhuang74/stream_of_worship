# Implementation Plan v1: Enhance Admin CLI `audio download` Structured Lyrics Parsing with LLM

## Goal

Improve accuracy of structured (section-tagged) lyrics extraction in `sow-admin audio download` and `sow-admin audio download --backfill-lyrics`. The current regex heuristic parser (`services/structured_lyrics.py`) handles clean, well-formed YouTube descriptions (`[Verse]`/`[Chorus]` tags on their own lines, no junk) but degrades on real-world descriptions containing: channel promos interspersed with lyrics, timestamps, inline tags, Chinese section labels (副歌/主歌), and missing tags.

Approach: add an LLM-based extraction/cleanup path (LangChain + OpenAI-compatible chat model) that ALWAYS runs after the heuristic, even when the heuristic succeeds. LLM is ON by default; `--no-llm` opts out and falls back to the heuristic-only path. When LLM is enabled but unavailable (no API key / network / malformed response), the command HARD FAILS (unless `--no-llm`).

## Accuracy baseline

The provided example (`https://www.youtube.com/watch?v=_XgP0p-S4S8`) parses **correctly** through the current heuristic — it is the expected CLEAN output, and the regex handles well-formed `[Verse]`/`[Chorus]` headers, blank-line-separated chorus blocks merged into one section, and trailing-whitespace stripping. The existing `WORKED_EXAMPLE` fixture (`tests/admin/services/test_structured_lyrics.py:8`) is equally clean and passes.

Real accuracy gaps live in the actual YouTube description text. To make tests honest, implementation step 1 captures the real description for `_XgP0p-S4S8` via `extract_video_metadata()` and commits it as a fixture alongside the expected structured output. The spec enumerates the common real-world failure modes the LLM is expected to fix (the captured fixture will surface the specific ones).

## Design decisions (fixed)

1. **LLM runs always (enhance/cleanup).** After `parse_structured_lyrics()` returns its dict (or `None`), the LLM path runs to clean up: drop non-lyric junk lines from inside sections, ensure section boundaries, normalize labels to the canonical set. The heuristic result is passed to the LLM as a starting hint (so the LLM can correct rather than re-derive from scratch).

2. **LLM is ON by default.** New `--no-llm` flag (paired with `--llm` as the positive form) opts out. Default is LLM-enabled. Applies to BOTH:
   - `audio download` (fresh import, `commands/audio.py:1304`)
   - `--backfill-lyrics` (`_backfill_lyrics_for_song` at `commands/audio.py:742`, and the stdin batch path `_backfill_lyrics_batch` at `commands/audio.py:1089`)
   - The `--all`/multi-step orchestration path (`commands/audio.py:5717`) that includes `backfill_lyrics`.

3. **Hard fail when LLM unavailable (unless `--no-llm`).** If LLM is enabled and `SOW_LLM_API_KEY` / `SOW_LLM_MODEL` unset, network errors, or malformed JSON response → the command exits non-zero with a red error message identifying the missing config / failure. This deliberately fails loud rather than silently degrading to the heuristic (per user preference).

4. **Dependency: add langchain-openai + pydantic to the `admin` extra.** Move `langchain-openai`, `langchain-core`, and `pydantic` into the `admin` extra so any `--extra admin` invocation can use LLM lyrics without requiring `--extra constructor`. `python-dotenv` is also added to `admin` for env loading parity with the songset constructor (which loads `/opt/sow/.env`).

5. **New Pydantic output schema.** Define `StructuredLyricsSection` and `StructuredLyricsResult` in `services/structured_lyrics.py` (mirroring the existing dict shape: `sections:[{label, raw_label, lines}], preamble_lines`). Use `.with_structured_output()` for type-validated LLM responses. Provide `.to_dict()` for backward-compat dict output consumed by `Recording.structured_lyrics` DB persistence (so the DB column shape is unchanged).

6. **Reuse existing LLM helpers / env vars.** Generalize the `build_chat_model()` / `structured()` pattern from `songset_constructor/graph/llm.py:13` into a lightweight `build_chat_model_for_lyrics()` in `services/structured_lyrics.py` reading `SOW_LLM_API_KEY` / `SOW_LLM_BASE_URL` / `SOW_LLM_MODEL` from env (same vars as songset constructor). Lazy-import `langchain_openai.ChatOpenAI` inside the function (the pattern at `graph/llm.py:16`) so admin-cli imports stay light on non-LLM code paths.

7. **Model: reuse `SOW_LLM_MODEL`.** No new env var. One model env var for the whole admin CLI's LLM use.

8. **LLM cleanup scope (this version): extract sections + drop non-lyric junk.** LLM is asked to: (a) identify section boundaries and assign lines, (b) drop non-lyric junk (channel promos, URLs, social handles, timestamps) from inside sections (not just trailing). **NOT in scope this version:** Chinese-label normalization (副歌→chorus), duplicate-section merging, multi-song detection (deferred to v2 — capture as open questions).

9. **Trailing-promo heuristic logic stays.** The existing `_TRAILING_NON_LYRIC_HINTS` / `_is_trailing_non_lyric` logic stays as a safety net for the `--no-llm` path. The LLM path does its own (broader) junk detection — it does NOT consult `_is_trailing_non_lyric`.

10. **Non-fatal contract for heuristic stays; LLM path is fatal-on-failure.** Current heuristic parsing is already non-fatal (`audio.py:972-975` wraps it in try/except, logs yellow, continues). The new LLM path is fatal when enabled (decision 3): if the LLM fails and `--no-llm` was NOT passed, the command exits non-zero. The heuristic-only (`--no-llm`) path keeps today's non-fatal behavior.

## Critical files

**Admin CLI**
- `ops/admin-cli/src/stream_of_worship/admin/services/structured_lyrics.py` — add `StructuredLyricsSection`/`StructuredLyricsResult` Pydantic models; add `extract_structured_lyrics_with_llm(description, heuristic_result) -> StructuredLyricsResult`; add `build_chat_model_for_lyrics()`; lazy langchain import.
- `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` — add `--no-llm`/`--llm` typer flags; thread `use_llm` through `import_youtube_audio_for_song` (~line 823 / call at 962-975) and `_backfill_lyrics_for_song` (line 742) and `_backfill_lyrics_batch` (line 1089); hard-fail validation when LLM enabled but unavailable.
- `ops/admin-cli/pyproject.toml` — add `langchain-openai`, `langchain-core`, `pydantic>=2.7.0`, `python-dotenv` to the `admin` extra.

**Tests**
- `ops/admin-cli/tests/admin/services/test_structured_lyrics.py` — extend: LLM extraction tests (mock the langchain model via dependency injection), Pydantic schema round-trip, `.to_dict()` regression.
- `ops/admin-cli/tests/admin/services/fixtures/` (new directory) — committed real-world description fixture + expected structured output, captured via `extract_video_metadata()`.
- `ops/admin-cli/tests/admin/test_audio_commands.py` — `--no-llm`/`--llm` wiring tests, hard-fail-on-missing-API-key test, LLM failure → exit non-zero.

## Implementation changes

### Change 1 — Capture test fixture from real YouTube URL

Create `ops/admin-cli/tests/admin/services/fixtures/` and add two files using a one-off capture:

```bash
uv run --project ops/admin-cli --extra admin python -c "
from stream_of_worship.admin.services.youtube import extract_video_metadata
m = extract_video_metadata('https://www.youtube.com/watch?v=_XgP0p-S4S8')
print(m.description)
" > ops/admin-cli/tests/admin/services/fixtures/_XgP0p-S4S8_description.txt
```

Then commit `fixtures/_XgP0p-S4S8_description.txt` (raw input) and `fixtures/_XgP0p-S4S8_expected.json` (the expected `StructuredLyricsResult` JSON — the clean lyrics from the user's example). The test in Change 7 asserts `extract_structured_lyrics_with_llm(description) == expected` (with the LLM mocked to a deterministic transformation OR marked `@pytest.mark.integration` requiring a real LLM key). Commit one concrete `KNOWN_FAILURE_CASES.md` in the fixtures dir documenting which directives present in `_XgP0p-S4S8_description.txt` the heuristic mishandles today.

### Change 2 — Pydantic models in structured_lyrics.py

In `services/structured_lyrics.py` add (after imports):

```python
from pydantic import BaseModel, Field


class StructuredLyricsSection(BaseModel):
    label: str
    raw_label: str
    lines: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict:
        return {"label": self.label, "raw_label": self.raw_label, "lines": list(self.lines)}


class StructuredLyricsResult(BaseModel):
    sections: list[StructuredLyricsSection] = Field(default_factory=list)
    preamble_lines: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "sections": [s.to_dict() for s in self.sections],
            "preamble_lines": list(self.preamble_lines),
        }
```

This keeps the persisted DB column shape byte-identical to today's dict via `.to_dict()`.

### Change 3 — LLM extraction function + chat model builder

In `services/structured_lyrics.py` add:

```python
import os


def build_chat_model_for_lyrics():
    """Build an OpenAI-compatible chat model for lyrics cleanup.

    Reads SOW_LLM_API_KEY / SOW_LLM_BASE_URL / SOW_LLM_MODEL from env.
    Raises RuntimeError if LLM env is not configured.
    """
    api_key = os.environ.get("SOW_LLM_API_KEY")
    model = os.environ.get("SOW_LLM_MODEL")
    if not api_key or not model:
        raise RuntimeError(
            "LLM lyrics extraction is enabled but SOW_LLM_API_KEY / "
            "SOW_LLM_MODEL are not set. Either set them or pass --no-llm."
        )
    from langchain_openai import ChatOpenAI  # lazy import

    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=os.environ.get("SOW_LLM_BASE_URL"),
        temperature=0.0,  # deterministic for cleanup
        max_retries=2,
    )


def _build_lyrics_prompt(description: str, heuristic: dict | None) -> str:
    """Build the LLM prompt for lyrics cleanup."""
    hint = ""
    if heuristic:
        import json

        hint = (
            "\n\nA regex heuristic already produced this candidate parse "
            "(use as a starting hint, correct any errors):\n"
            f"{json.dumps(heuristic, ensure_ascii=False, indent=2)}"
        )
    return (
        "You are a lyrics parser. Given a YouTube video description, extract "
        "the structured song lyrics.\n\n"
        "Identify section boundaries (Verse, Pre-Chorus, Chorus, Bridge, Intro, "
        "Outro, Instrumental, Hook, Refrain, Tag) and assign each non-blank "
        "lyric line to the section it belongs to.\n\n"
        "STRICT RULES:\n"
        "- Keep ALL actual lyric lines verbatim (including Chinese text and "
        "full-width punctuation).\n"
        "- DROP non-lyric junk lines: channel promos, subscribe requests, URLs, "
        "social handles (@...), timestamps, and any other non-lyric noise — "
        "wherever they appear (not just at the end).\n"
        "- Preserve the original section label spelling in raw_label; put the "
        "lowercased normalized form in label.\n"
        "- Lines before the first section tag go into preamble_lines.\n"
        "- If the description contains no section tags and no recognizable "
        "lyrics structure, return an empty sections list.\n"
        "- Do NOT translate, paraphrase, or reorder lyric lines.\n\n"
        "Description to parse:\n"
        "---\n"
        f"{description}\n"
        "---"
        f"{hint}\n"
    )


def extract_structured_lyrics_with_llm(description: str | None) -> StructuredLyricsResult | None:
    """Parse a description into structured lyrics using an LLM for cleanup.

    Returns a StructuredLyricsResult or None if the description is empty.
    Raises RuntimeError if LLM env is not configured.
    Raises on LLM call failure (network, malformed JSON) — caller must handle.
    """
    if not description:
        return None
    chat = build_chat_model_for_lyrics()
    try:
        structured_chat = chat.with_structured_output(
            StructuredLyricsResult, method="json_schema"
        )
    except TypeError:
        structured_chat = chat.with_structured_output(
            StructuredLyricsResult, method="function_calling"
        )
    heuristic = parse_structured_lyrics(description)
    prompt = _build_lyrics_prompt(description, heuristic)
    result = structured_chat.invoke(prompt)
    return result
```

Note: the `.with_structured_output` fallback mirrors `songset_constructor/graph/llm.py:27-31` exactly.

### Change 4 — New orchestration entrypoint: parse_structured_lyrics_smart

In `services/structured_lyrics.py` add a top-level orchestration entrypoint that the command-layer calls instead of calling `parse_structured_lyrics` / `extract_structured_lyrics_with_llm` directly:

```python
def parse_structured_lyrics_smart(
    description: str | None, *, use_llm: bool = True
) -> dict | None:
    """Parse structured lyrics, preferring LLM cleanup when enabled.

    - use_llm=True (default): runs heuristic, then LLM cleanup. LLM env misconfig /
      call failure is FATAL (raises). Returns the cleaned dict
      (StructuredLyricsResult.to_dict()) or None if the description is empty.
    - use_llm=False: runs the heuristic only. Non-fatal on parse failure
      (returns None).
    """
    if not description:
        return None
    if use_llm:
        result = extract_structured_lyrics_with_llm(description)
        if result is None:
            return None
        return result.to_dict() or None
    return parse_structured_lyrics(description)
```

The command layer calls `parse_structured_lyrics_smart(metadata.description, use_llm=use_llm)`. The existing `parse_structured_lyrics` stays as the `--no-llm` path and is unchanged.

### Change 5 — Thread `--no-llm`/`--llm` through audio.py commands

File: `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`.

Add the typer Option to the `download_audio` command signature near the existing `--backfill-lyrics` option (~line 1323):

```python
    use_llm: bool = typer.Option(
        True,
        "--llm/--no-llm",
        help=(
            "Use LLM to extract/cleanup structured lyrics (default: on). "
            "--no-llm falls back to the regex heuristic only. When LLM is "
            "enabled but SOW_LLM_API_KEY/SOW_LLM_MODEL are unset, the command "
            "exits non-zero."
        ),
    ),
```

Pass `use_llm=use_llm` through every callsite that computes structured lyrics:
- `import_youtube_audio_for_song(...)` — replace `parse_structured_lyrics(metadata.description)` at line 969 with `parse_structured_lyrics_smart(metadata.description, use_llm=use_llm)`.
- `_backfill_lyrics_for_song(...)` at line 742 — add `use_llm: bool = True` param; replace `parse_structured_lyrics` at line 795 with `parse_structured_lyrics_smart(metadata.description, use_llm=use_llm)`.
- `_backfill_lyrics_batch(...)` at line 1089 — add `use_llm: bool = True` param; forward into `_backfill_lyrics_for_song(...)`.
- The `--all`/multi-step orchestration path at line 5717 — add `use_llm` to the typer signature and forward into the backfill step (~line 5976).

The structured-lyrics try/except at `audio.py:966-975` currently catches `RuntimeError` non-fatally. Change it to: when `use_llm` is True, do NOT swallow the `RuntimeError` from `extract_structured_lyrics_with_llm` — re-raise so the command exits non-zero (decision 3). When `use_llm` is False, keep today's non-fatal `try/except`:

```python
    structured_raw: Optional[str] = None
    structured_json_str: Optional[str] = None
    try:
        metadata = extract_video_metadata(search_or_url)
        structured_raw = metadata.description
        structured_json = parse_structured_lyrics_smart(
            metadata.description, use_llm=use_llm
        )
        if structured_json:
            structured_json_str = json.dumps(structured_json, ensure_ascii=False)
    except RuntimeError as e:
        if use_llm:
            console.print(f"[red]LLM lyrics extraction failed: {e}[/red]")
            console.print(
                "[dim]Use --no-llm to fall back to the regex heuristic only.[/dim]"
            )
            raise typer.Exit(1)
        console.print(
            f"[yellow]Could not fetch video metadata for structured lyrics: {e}[/yellow]"
        )
```

Note `parse_structured_lyrics_smart` raises `RuntimeError` only when `use_llm=True`; the heuristic path returns None on failure — so the `if use_llm` branch is the only fatal one. Other exceptions (validation errors, pydantic) are NOT caught here — let them propagate as bugs.

### Change 6 — pyproject.toml: add langchain + pydantic to admin extra

File: `ops/admin-cli/pyproject.toml`. In the `admin` extra, add:

```toml
admin = [
    ...existing...
    "langchain-openai>=0.2.0",
    "langchain-core>=0.3.0",
    "pydantic>=2.7.0",
    "python-dotenv>=1.2.2",
]
```

(Search for any other place that documents the admin extra's dependencies — e.g. `README.md` — and update the prose if it claims langchain is constructor-only.)

### Change 7 — Tests

**`tests/admin/services/test_structured_lyrics.py`:**

- `TestStructuredLyricsModels`: `StructuredLyricsSection.to_dict()` / `StructuredLyricsResult.to_dict()` round-trip matches the existing dict shape (regression against current `parse_structured_lyrics` output).
- `TestExtractStructuredLyricsWithLLM`: inject a fake chat model (monkeypatch `build_chat_model_for_lyrics` to return a stub whose `.with_structured_output(...).invoke()` returns a known `StructuredLyricsResult`). Assert the cleaned result drops a promo line that appears mid-section and keeps all real lyric lines.
- `TestExtractStructuredLyricsWithLLM`: stub `.invoke()` raises `ValueError` → `extract_structured_lyrics_with_llm` propagates (caller hard-fails).
- `TestParseStructuredLyricsSmart`: `use_llm=False` returns the heuristic dict unchanged (regression guard that today's behavior is preserved). `use_llm=True` with LLM env unset raises `RuntimeError`.

**`tests/admin/services/fixtures/` (new):**

- `_XgP0p-S4S8_description.txt` — the raw YouTube description captured via Change 1.
- `_XgP0p-S4S8_expected.json` — the expected `StructuredLyricsResult.to_dict()` (the clean lyrics from the user's example).
- `KNOWN_FAILURE_CASES.md` — documents which directives in `_XgP0p-S4S8_description.txt` the current heuristic mishandles today (e.g. mid-section promos, Chinese labels, inline tags), anchored to specific line numbers in the fixture. This is the evidence base for why the LLM path exists.

**`tests/admin/services/test_structured_lyrics_real_url.py` (new, integration):**

- `@pytest.mark.integration` test that calls `extract_structured_lyrics_with_llm(open(fixture).read())` against the REAL LLM (requires `SOW_LLM_API_KEY`) and asserts the result equals `_XgP0p-S4S8_expected.json`. Excluded by default (`addopts = "-m 'not integration'"` at `pyproject.toml:96`).

**`tests/admin/test_audio_commands.py`:**

- `--no-llm` on `audio download` → structured-lyrics path calls `parse_structured_lyrics` (heuristic only), no langchain import attempted (assert via mock).
- `--llm` (default) on `audio download` with `SOW_LLM_API_KEY` unset → exit code 1, red "LLM lyrics extraction failed" message, "Use --no-llm" hint printed.
- `--llm` with API key set but LLM `.invoke()` raises → exit code 1.
- `--backfill-lyrics --no-llm` → heuristic-only path (existing behavior regression guard).
- `--backfill-lyrics --llm` (default) with env configured → calls `extract_structured_lyrics_with_llm`.

## Backward compatibility

- **DB persistence shape unchanged.** `StructuredLyricsResult.to_dict()` produces the same dict accepted by `Recording.structured_lyrics` (DB client `update_recording_structured_lyrics` at `db/client.py:936`) and `flatten_structured_lyrics`.
- **`--no-llm` recovers today's behavior bit-for-bit.** `parse_structured_lyrics` and all its heuristics are unchanged; only the new smart entrypoint gates between the two paths.
- **Existing tests stay green.** All 9 `TestParseStructuredLyrics` tests + 4 `TestFlattenStructuredLyrics` tests in `tests/admin/services/test_structured_lyrics.py` are unchanged. New test classes are additive.
- **Env vars reused.** `SOW_LLM_API_KEY` / `SOW_LLM_BASE_URL` / `SOW_LLM_MODEL` are the same ones the songset constructor already uses (`config.py:77`, `graph/llm.py:18-21`). No new env var needed.
- **No CLI contract change for consumers.** The `audio download` and `--backfill-lyrics` outputs are identical in shape; only the accuracy of the structured-lyrics content improves (when LLM is on).

## CLI usage examples

Default (LLM on) — new behavior:

```bash
uv run --project ops/admin-cli --extra admin sow-admin audio download <song-id>
```

Explicit LLM on (same as default):

```bash
uv run --project ops/admin-cli --extra admin sow-admin audio download <song-id> --llm
```

Opt out, heuristic only (today's behavior):

```bash
uv run --project ops/admin-cli --extra admin sow-admin audio download <song-id> --no-llm
```

Backfill lyrics with LLM (default):

```bash
uv run --project ops/admin-cli --extra admin sow-admin audio download <song-id> --backfill-lyrics
```

Backfill lyrics, heuristic only:

```bash
uv run --project ops/admin-cli --extra admin sow-admin audio download <song-id> --backfill-lyrics --no-llm
```

Failure mode (LLM on, no env):

```bash
$ sow-admin audio download <song-id>
[red]LLM lyrics extraction failed: LLM lyrics extraction is enabled but
SOW_LLM_API_KEY / SOW_LLM_MODEL are not set. Either set them or pass --no-llm.[/red]
# exit 1
```

## Rollout order

1. **Capture fixture** (Change 1) — gives the implementation-and-tests phase a concrete accuracy target.
2. **Pydantic models + LLM function** (Changes 2, 3, 4) in `structured_lyrics.py` with mocked-LLM unit tests.
3. **pyproject.toml** (Change 6) — add deps so imports resolve.
4. **Command-layer wiring** (Change 5) — `--no-llm`/`--llm` thread + hard-fail.
5. **Command-layer tests** (Change 7, `test_audio_commands.py`).
6. **Integration test** (Change 7, `test_structured_lyrics_real_url.py`) — manual run with real `SOW_LLM_API_KEY`.
7. **Manual verification** per checklist below.

## Manual verification checklist

- `--no-llm` on `audio download` produces structured lyrics identical to today's heuristic output for `_XgP0p-S4S8` (regression: same section count, same lines).
- Default (LLM on) on `_XgP0p-S4S8` produces lyrics matching `_XgP0p-S4S8_expected.json` (the clean lyrics from the user's example), with any mid-section promo lines dropped that the heuristic left in.
- `--llm` with `SOW_LLM_API_KEY` unset → red error, exit 1, "Use --no-llm" hint printed.
- `--llm` with env set but model returns malformed JSON → exit 1 (pydantic validation error propagates, NOT silently swallowed).
- `--no-llm` on `--backfill-lyrics` → exits 0 with heuristic parse (today's behavior).
- `--llm` (default) on `--backfill-lyrics` with env set → persists cleaned structured lyrics via `update_recording_structured_lyrics`.
- `audio show <song-id>` still renders structured lyrics correctly (consumer unaffected).
- `audio lrc <song-id>` still works with the cleaned structured lyrics (reads `structured_lyrics` JSON column).
- `uv run --project ops/admin-cli --extra admin --extra test pytest tests/admin/services/test_structured_lyrics.py -v` — all existing + new tests green.

## Open questions

- Should Chinese-label normalization (副歌→chorus, 主歌→verse, 橋段→bridge) be added this version, or deferred to v2? Recommendation: defer — keeps this version's LLM scope to the two agreed tasks (section extraction + junk drop) and avoids quietly rewriting `raw_label` on first deploy.
- Should the LLM result be cached by description-content-hash so `--backfill-lyrics --stdin` batch runs don't re-incur cost per song? Recommendation: defer to v2 — a single backfill over the catalog is a bounded cost; caching adds DB/fixture complexity now.
- Should `--llm`/`--no-llm` ALSO be added to the `--all` multi-step orchestration (line 5717) so it threads through embedding/analyze steps too? Recommendation: this version adds it only to the structured-lyrics-producing paths (decision 2); LLM usage in component analysis uses its own existing env gate.

## Out of scope

- Chinese section-label normalization.
- Duplicate-section merging (two `[Chorus]` blocks → Chorus 1 / Chorus 2).
- Multi-song detection in a single description.
- LLM caching by description hash.
- Webapp / Android UI exposure of the LLM lyrics toggle.
- The `services/scraper.py` `_detect_sections` stub (currently returns `unknown` for sop.org lyrics) — separate path from YouTube download.

## End of file
