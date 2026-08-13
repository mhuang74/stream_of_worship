# Implementation Plan v2: LLM Whole-Song Segmentation for Chorus/Verse Identification

> **Supersedes:** `specs/component-identification-llm-segmentation-v1.md` (v1). v2 corrects **12 issues** found during a code-level review of v1 against the actual codebase (see "Review corrections" below).

> Goal: replace the plateaued lyrics-repetition component identification with **Design C** — LLM whole-song segmentation (`section_segmenter.py`) plus a deterministic repetition cross-check validator — to lift IoU from the current **0.286** baseline to **≥ 0.70** on the 3-song fixture set.
>
> This is the detailed implementation plan for Design C as selected in `specs/component-identification-alternatives-v1.md`. It implements the full scoring path (both the primary LLM segmentation call AND the opt-in sanity-check calls) because accuracy is the priority over token cost / tuning complexity.
>
> Scope: **identification** in `ops/analysis-service/src/sow_analysis/workers/components.py` and the new `section_segmenter.py` module. The allin1-sections path and the theme classifier are out of scope (see "Out of scope").

---

## Review corrections applied in v2

v1 was reviewed against the actual codebase. The following issues were found and corrected in this document:

1. **Runtime Compile Error**: v1's `_render_numbered_lrc` was typed `-> str` but returned a tuple `("\n".join(...), len(lines))`. Furthermore, `parse_lrc()` raises `ValueError` on empty LRC instead of returning empty, which would crash the LLM path instead of falling back. **Fix:** corrected to `-> tuple[str, int]` and wrapped in `try/except ValueError`.
2. **Behavioral Regression (IoU degradation)**: v1 unconditionally snapped to beats/downbeats (`if downbeats: ... elif beats: ...`) regardless of the caller's `snap_to_downbeat` preference. This introduced coordinates *inconsistent* with the fallback path, causing IoU score deltas between runs. **Fix:** added `snap_to_downbeat: bool` parameter to `segment_song()` and mapper; snapping is conditional on the flag matching the fallback (`identify_from_allin1_sections` and `identify_from_lyrics_repetition`) behavior.
3. **End-time Derivation Drift**: v1's `_sec_time` used `lines[s.line_end].time_seconds` when `s.line_end < len(lines)` and `start + 4.0` otherwise. This uses `len(lines)` (list length) and 4.0 hardcoded. **Fix:** mirror `_expected_time_range` exactly: `end = lines[s.line_end].time_seconds if s.line_end < n else avg_duration`, where `n = len(lines)`.
4. **API Data Loss**: v1 did not add `section_label`, `lyrics_excerpt`, `llm_rationale` to `ComponentResult` in `models.py` or the conversion loop in `queue.py`. These fields would persist to local `components.json` but never reach R2 or the job-result API. **Fix:** added fields to `ComponentResult` and updated `queue.py` conversion.
5. **admin-cli Schema Drift**: v1 bumped `COMPONENT_SCHEMA_VERSION` to 3 **only** in analysis-service. Admin-cli has mirrored constants (`analysis.py:22`, `component_editor/constants.py:49`) that would reject v3 payloads as cache misses, causing false recomputation. **Fix:** v2 updates both mirrored constants in admin-cli.
6. **Pytest Marker Conflict**: v1's test file used `pytestmark = pytest.mark.skipif(...)` at module level, which would skip **all** tests (including pure unit tests) unless `SOW_LLM_LIVE_TESTS=1`. It also used `@pytest.mark.llm` without registering the marker in `pyproject.toml`. **Fix:** removed module-level `pytestmark`; unit tests run unconditionally; live integration tests use `@pytest.mark.skipif` (no custom marker).
7. **Few-Shot File Not Packaged**: v1 committed `segmentation_few_shot.json` under `src/sow_analysis/workers/` but no `[tool.setuptools.package-data]` or `MANIFEST.in` existed, silently excluding it from pip installs. **Fix:** added `[project.setuptools.package-data]` to `pyproject.toml`.
8. **Job Option Logic Ambiguity**: v1's Change 5 was self-contradictory — it implied job option "short-circuits" the env gate while also requiring both to be true. **Fix:** clarified logic: `use_llm = use_llm_segmentation or settings.SOW_COMPONENTS_USE_LLM_SEGMENTATION`; job option is one-way OR (can only force-ON, never force-OFF) as confirmed by user.
9. **Few-Shot Leakage Risk**: v1 mentioned "not from 3-fixture set" but provided no enforcement. **Fix:** added `source_song_id` field to each few-shot example; loader asserts no overlap with fixture SONG_IDS.
10. **Missing `max_tokens` Parameter**: v1 did not specify `max_tokens` on the segmentation call. The existing classifier uses 300, but whole-song JSON output needs much more. **Fix:** added `SOW_LLM_SEGMENTATION_MAX_TOKENS` setting (default 2048).
11. **Fixture Path Documentation Error**: v1 said fixtures were under `ops/analysis-service/eval/`. Actual location: `eval/components_tuning/` at the **repo root**. **Fix:** corrected all paths.
12. **CLI Flag Not Documented**: v1 mentioned `--use-llm-segmentation` in rollout step 11 but never specified it in the component-analysis CLI. **Fix:** noted the existing `sow-admin audio analyze components --options-json` pattern from v5 spec; the per-job option is the official CLI vector.

---

## Why the repetition path is being superseded (root causes)

`identify_from_lyrics_repetition` (`ops/analysis-service/src/sow_analysis/workers/components.py:854`) plateaus at **grand-total IoU ≈ 0.286** on the 3-fixture eval set. The v1 weight-tuning loop (`specs/component-identification-tuning-loop-v1.md`) grid-searched the four multi-cue weight knobs and found **no meaningful win** — the gap is structural, not weight-tunable:

1. **Zero `verse`/`loop_target` components on all 3 songs (verse IoU = 0.0).** The verse is synthesized by walking backward from the first chorus occurrence (`components.py:1089-1119`), but the winning chorus candidate's first occurrence always starts at line 1 / index 0 (the merged block absorbs the verse), so `first_chorus_start_idx > 0` is `False` and no verse is emitted. This single defect costs ≈ a third of achievable IoU per song.
2. **Over-merged chorus windows.** The repeated-sequence signature greedily joins verse + chorus into one giant block (the 13-line blocks beginning with the verse 聖潔耶穌/哈利路亞 and 祢的話在我心). The predicted range only partially covers the true chorus, capping IoU near 0.25–0.5.
3. **Entry/exit role misalignment.** With only two essential chorus rows derived by pure occurrence order, the algorithm labels the ground-truth *entry* as *exit* → −0.10 role-mismatch penalty.

These are fixed by Design C: the LLM labels `verse` independently of where the chorus candidate starts (fixes #1), semantically separates verse from chorus content rather than joining them (fixes #2), and the mapper derives roles from ordered chorus occurrences (fixes #3, matching the scorer's own definition).

---

## Line indexing convention

The prompt's numbered-LRC input and the `_parse_segmenter_json` output use **1-based line numbers into the raw, unfiltered LRC file** — every physical line counts as a slot, **including blank / empty metadata lines** — matching the exact convention already defined in `specs/component-identification-tuning-loop-v1.md` ("Line indexing convention"). This keeps the LLM's integer ranges directly reusable by the existing `_expected_time_range` scorer with no conversion.

Both `line_start` (inclusive) and `line_end` (inclusive) are 1-based: `[line_start, line_end]` covers raw lines `line_start, line_start+1, ..., line_end`. The equivalent 0-based index is `zero = line - 1`.

`_render_numbered_lrc` must include blanks:

```
1  [00:12.10]
2  [00:33.32] 聖潔耶穌 祢寶座在這裡
3  [00:47.89] 哈利路亞 祢榮耀在這裡
4  [01:02.33] 聖潔耶穌 祢寶座在這裡
5  [01:16.64] 哈利路亞 祢榮耀在這裡
6  [01:29.34] 君王就在這裡 我們歡然獻祭
7  [01:33.02] 平安的王在這裡 歡迎祢降臨
8  [01:37.55]
9  [01:41.22] 聖潔耶穌 祢寶座在這裡
```

Note line 1 and line 8 are blank/metadata-only lines; the "8" does NOT skip to 9 because blanks are numbered.

---

## Critical files

### New files
- `ops/analysis-service/src/sow_analysis/workers/section_segmenter.py` — the LLM segmentation module (~450 lines): client construction, numbered-LRC rendering, prompt builder, JSON parser/validator, section-to-component mapper, chorus-repetition validator, opt-in sanity check.
- `ops/analysis-service/src/sow_analysis/workers/segmentation_few_shot.json` — committed hand-written few-shot examples (2–3) from worship songs NOT in the 3-fixture set, with leakage guard fields.
- `ops/analysis-service/tests/test_section_segmenter.py` — unit + integration tests.

### Modified files
- `ops/analysis-service/src/sow_analysis/storage/cache.py` — bump `COMPONENT_SCHEMA_VERSION` (Change 0).
- `ops/analysis-service/src/sow_analysis/workers/components.py` — extend `ComponentInstance` (Change 1), update `_serialize_components` / `_deserialize_components` (Change 1), wire the LLM path into `extract_components` (Change 4).
- `ops/analysis-service/src/sow_analysis/config.py` — add five settings (Change 2).
- `ops/analysis-service/src/sow_analysis/models.py` — add `use_llm_segmentation` to `ComponentAnalysisOptions`, add v6 fields to `ComponentResult` (Change 5).
- `ops/analysis-service/src/sow_analysis/workers/queue.py` — accept `use_llm_segmentation` param, thread through job processing, update ComponentResult conversion (Change 5).
- `ops/analysis-service/pyproject.toml` — add `[tool.setuptools.package-data]` for the JSON resource (Change 6).
- `ops/analysis-service/tests/test_components_tuning.py` — scorer tolerates `source="llm_segmentation"`; add no-regression fallback assertion (Evaluation section).

### Files to update in ops/admin-cli (schema version alignment)
- `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py:22` — bump `COMPONENT_SCHEMA_VERSION = 2` → `3`.
- `ops/admin-cli/src/stream_of_worship/admin/component_editor/constants.py:49` — same bump.

### Unchanged files (do NOT modify)
- `ops/analysis-service/src/sow_analysis/workers/classifier.py` — no changes; `ThemeClassifier._client` / `_parse_llm_json` are only *referenced* as the pattern to mirror.
- `ops/analysis-service/src/sow_analysis/workers/llm_rate_limit.py` — no changes; `call_llm_with_retry` reused as-is.
- `ops/analysis-service/src/sow_analysis/workers/lrc_parser.py` — no changes; `parse_lrc` reused.
- `ops/analysis-service/tests/test_components.py` — existing tests untouched; they exercise the fallback path, which is unchanged.
- `ops/analysis-service/src/sow_analysis/workers/components.py` `identify_from_allin1_sections` and `identify_from_lyrics_repetition` — no behavioral changes (only file-level additions).

---

## Implementation changes

### Change 0 — Bump `COMPONENT_SCHEMA_VERSION`

**Files:**
- `ops/analysis-service/src/sow_analysis/storage/cache.py` — constant at `cache.py:15`: `= 3`.
- `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py:22` — `COMPONENT_SCHEMA_VERSION = 3`.
- `ops/admin-cli/src/stream_of_worship/admin/component_editor/constants.py:49` — `COMPONENT_SCHEMA_VERSION = 3`.

This forces re-compute of cached `components.json` across all songs: `CacheManager` treats a mismatched version as a cache miss and recomputes (see the comment block at `cache.py:13-14`). The new optional fields `section_label` / `lyrics_excerpt` / `llm_rationale` populate only on re-compute (or `--force`), which the bump guarantees. Admin-cli's R2 reads will treat any v2 payloads as misses and trigger an analysis-service re-compute instead of serving stale entries.

### Change 1 — Extend `ComponentInstance` dataclass

**File:** `ops/analysis-service/src/sow_analysis/workers/components.py`
**Location:** the `ComponentInstance` dataclass, after the v5 reasoning fields (`components.py:265-267`).

Add three new optional fields, all defaulting to `None`:

```python
    # v5: LLM reasoning fields
    theme_reasoning: Optional[str] = None
    posture_reasoning: Optional[str] = None
    # v6: LLM whole-song segmentation (Design C) fields
    section_label: Optional[str] = None      # e.g. "chorus", "verse"
    lyrics_excerpt: Optional[str] = None     # joined text of section lines
    llm_rationale: Optional[str] = None      # model's free-text reason
```

`section_label` is the section label the LLM assigned (`intro`/`verse`/`prechorus`/`chorus`/`bridge`/`outro`/`instrumental`); `lyrics_excerpt` is the joined lyric text of the section lines; `llm_rationale` is the model's free-text reason. All `None` for non-LLM paths, so existing rows / persisted JSON are unchanged.

Update `_serialize_components` (`components.py:1521-1563`) to include the new keys in the per-component dict (after `posture_reasoning`):

```python
                # v6: LLM segmentation
                "section_label": c.section_label,
                "lyrics_excerpt": c.lyrics_excerpt,
                "llm_rationale": c.llm_rationale,
```

Update `_deserialize_components` (`components.py:1566-1600`) to read them back (after `posture_reasoning`):

```python
                # v6: LLM segmentation
                section_label=c.get("section_label"),
                lyrics_excerpt=c.get("lyrics_excerpt"),
                llm_rationale=c.get("llm_rationale"),
```

### Change 2 — Add new settings

**File:** `ops/analysis-service/src/sow_analysis/config.py`
**Location:** in the `Settings` class, next to the existing LLM configuration block (`config.py:112-155`).

```python
    # v6: LLM whole-song segmentation (Design C) gate + tuning.
    SOW_COMPONENTS_USE_LLM_SEGMENTATION: bool = False
    # Master switch for the LLM segmentation identification path. Off by
    # default; when off (or when SOW_LLM_API_KEY is unset, or the LLM call
    # or JSON validation fails), extract_components falls back to the
    # existing lyrics-repetition path at the 0.286 baseline.

    SOW_LLM_SEGMENTATION_MODEL: Optional[str] = None
    # Optional override model for the segmentation LLM call. Defaults to
    # SOW_LLM_MODEL when None/empty. May be pointed at a stronger model
    # (e.g. gpt-4o) for accurate Chinese lyric structure segmentation
    # without changing the theme classifier's model.

    SOW_LLM_SEGMENTATION_TIMEOUT_SECONDS: float = 60.0
    # Per-request SDK-level HTTP timeout for the segmentation OpenAI client
    # (mirrors SOW_LLM_CLASSIFICATION_TIMEOUT_SECONDS). call_llm_with_retry's
    # budget is the overall wall-clock ceiling.

    SOW_LLM_SEGMENTATION_SANITY_CHECK: bool = False
    # Opt-in 2nd/3rd LLM call that presents the validated section list back
    # to the LLM for a yes/no sanity check. Default off. Enable only if the
    # measured IoU on the 3 fixtures is below target (see Open questions #6).

    SOW_LLM_SEGMENTATION_MAX_TOKENS: int = 2048
    # Maximum tokens for the segmentation LLM call. The whole-song LRC +
    # few-shot examples consume a large token budget; 2048 accommodates
    # ~100-line songs safely. Increase for very long LRCs.
```

The empty-string-to-None handling for `SOW_LLM_SEGMENTATION_MODEL` mirrors `_empty_str_to_none` (`config.py:240-257`) — apply the same validator pattern:

```python
    @field_validator("SOW_LLM_SEGMENTATION_MODEL", mode="before")
    @classmethod
    def _empty_str_to_none_segmentation(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v
```

### Change 3 — New module `section_segmenter.py`

**File:** `ops/analysis-service/src/sow_analysis/workers/section_segmenter.py` (new, ~450 lines)

Module overview:

```python
"""LLM whole-song segmentation (Design C) + repetition cross-check validator.

Segments an LRC into labelled sections (intro/verse/prechorus/chorus/bridge/
outro/instrumental) via one LLM call, maps sections to ComponentInstance via
the pure-Python mapper, then runs a deterministic repetition validator over
each chorus section to confirm repetition and tighten boundaries. An opt-in
2nd/3rd LLM sanity check runs only when SOW_LLM_SEGMENTATION_SANITY_CHECK is
enabled. Any failure falls back to the existing lyrics-repetition path.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from openai import OpenAI

from ..config import settings
from .components import ComponentInstance, _snap_to_beat, _snap_to_downbeat, parse_lrc
from .llm_rate_limit import call_llm_with_retry

logger = logging.getLogger(__name__)

# Held-out fixture song IDs — few-shot examples must NOT come from these songs.
# This list is duplicated from test_components_tuning.py:_SONG_IDS to provide
# a runtime assertion against test-set leakage in the few-shot loader.
_EXPECTED_HELD_OUT_IDS = {
    "jun_wang_jiu_zai_zhe_li_1c32724c",
    "yi_sheng_jing_bai_mi_da2173d0",
    "zhu_a__wo_yao_gen_sui_mi_83163301",
}
```

**The `Section` dataclass:**

```python
@dataclass
class Section:
    label: str
    line_start: int
    line_end: int
    confidence: float
    rationale: Optional[str] = None
```

**`_build_client()` — LLM client construction (mirrors `ThemeClassifier._client`, `classifier.py:214-234`):**

```python
def _build_client() -> OpenAI:
    if not settings.SOW_LLM_API_KEY:
        raise ValueError("SOW_LLM_API_KEY environment variable not set.")
    if not settings.SOW_LLM_BASE_URL:
        raise ValueError("SOW_LLM_BASE_URL environment variable not set.")
    return OpenAI(
        api_key=settings.SOW_LLM_API_KEY,
        base_url=settings.SOW_LLM_BASE_URL,
        timeout=settings.SOW_LLM_SEGMENTATION_TIMEOUT_SECONDS,
        max_retries=0,
    )
```

The model resolver (returns `SOW_LLM_SEGMENTATION_MODEL` if set, else `SOW_LLM_MODEL`):

```python
def _segmentation_model() -> str:
    model = settings.SOW_LLM_SEGMENTATION_MODEL or settings.SOW_LLM_MODEL
    if not model:
        raise ValueError("No segmentation model configured (SOW_LLM_MODEL unset).")
    return model
```

**`_render_numbered_lrc(lrc_content) -> tuple[str, int]` (S2 — numbered format including blanks):**

```python
def _render_numbered_lrc(lrc_content: str) -> tuple[str, int]:
    try:
        lines = parse_lrc(lrc_content).lines
    except ValueError:
        # parse_lrc raises ValueError("No valid LRC lines found ...")
        # for empty files; treat as "no lines" so caller can fall back.
        logger.warning("parse_lrc returned no lines; treating as empty LRC")
        return "(empty LRC)", 0
    out: list[str] = []
    for i, ln in enumerate(lines, start=1):
        text = ln.text if ln.text is not None else ""
        stamp = f"{ln.time_seconds:.2f}"
        out.append(f"{i}  [{stamp}] {text}")
    return "\n".join(out), len(lines)
```

Returns the numbered block and the total raw line count (used for bounds validation and for the `n_lines` argument of `_parse_segmenter_json`). Note this counts **every physical line, blanks included**, per the line-indexing convention.

**`_build_segmentation_prompt(lrc_content, song_title, duration, few_shot_examples) -> list[dict]` (S2+S3 prompt builder):**

```python
def _build_segmentation_prompt(
    lrc_content: str,
    song_title: Optional[str],
    duration: Optional[float],
    few_shot_examples: list[dict],
) -> list[dict]:
    numbered, n_lines = _render_numbered_lrc(lrc_content)
    system = (
        "You are a Chinese worship-music structure analyst. Given a numbered LRC "
        "lyric file, segment the song into labeled sections and return a JSON object "
        "with a single key 'sections'. Each section has: label (one of intro, verse, "
        "prechorus, chorus, bridge, outro, instrumental), line_start (1-based, "
        "inclusive), line_end (1-based, inclusive), confidence (0.0-1.0), and a short "
        "rationale. Sections must be non-overlapping, cover every non-blank line in "
        "order, and be sorted by line_start. Respond with JSON only."
    )
    user_parts: list[str] = []
    if song_title:
        user_parts.append(f"Song title: {song_title}")
    if duration is not None:
        user_parts.append(f"Approximate duration: {duration:.1f}s")
    user_parts.append("Here are a few reference examples of correct output:")
    for ex in few_shot_examples:
        user_parts.append(ex["input"])
        user_parts.append("Expected output:")
        user_parts.append(json.dumps({"sections": ex["sections"]}, ensure_ascii=False))
    user_parts.append("Now segment this numbered LRC:")
    user_parts.append(numbered)
    user_parts.append("Output JSON only:")
    user = "\n".join(user_parts)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
```

**`_parse_segmenter_json(response_text, n_lines) -> Optional[list[Section]]` (S3 — defensive parsing):**

```python
_VALID_LABELS = {
    "intro", "verse", "prechorus", "chorus", "bridge", "outro", "instrumental",
}


def _parse_segmenter_json(response_text: str, n_lines: int) -> Optional[list[Section]]:
    try:
        data = json.loads(response_text)
    except (json.JSONDecodeError, TypeError):
        return None
    sections_list = data.get("sections") if isinstance(data, dict) else None
    if not isinstance(sections_list, list) or not sections_list:
        return None
    sections: list[Section] = []
    prev_end = 0
    seen_ranges: set[tuple[int, int]] = set()
    for raw in sections_list:
        if not isinstance(raw, dict):
            return None
        label = str(raw.get("label", "")).lower()
        if label not in _VALID_LABELS:
            return None
        try:
            line_start = int(raw["line_start"])
            line_end = int(raw["line_end"])
            confidence = float(raw.get("confidence", 0.5))
        except (KeyError, TypeError, ValueError):
            return None
        if not (1 <= line_start <= line_end <= n_lines):
            return None
        # Strict non-overlap: next section must start at least line after prev_end.
        if line_start <= prev_end or (line_start, line_end) in seen_ranges:
            return None
        prev_end = line_end
        seen_ranges.add((line_start, line_end))
        sections.append(
            Section(
                label=label,
                line_start=line_start,
                line_end=line_end,
                confidence=max(0.0, min(1.0, confidence)),
                rationale=raw.get("rationale"),
            )
        )
    if not sections:
        return None
    return sections
```

Validation rules (any failure → `None` → caller falls back): requires a dict with a `sections` list; every item is a dict with a valid label, in-bounds inclusive range, non-overlap with the previous section (`line_start > prev_end` — strictly contiguous, no gaps), and no duplicate range. Individual OOB/overlapping/bad-label entries are treated as a total failure (whole result rejected) so a corrupted chunk does not silently produce a monoculture segmentation; this matches the "on any violation → fall back" policy in S3.

Note: the v1 spec's original `line_start <= prev_end + 1` allowed a 1-blank-line gap between sections (`prev_end=5` → next `line_start=7`). While LLMs commonly skip blank lines (a `[01:37.55]` instrument-only timestamp), a gap would introduce a non-segmented region into the output. Design C treats *any* gap as invalid (falls back) rather than trying to auto-fill. This is stricter but simpler: if the LLM output leaves a gap, the model did something wrong.

**`_map_sections_to_components(sections, lines, beats, downbeats, snap_to_downbeat) -> list[ComponentInstance]` (S4 mapper, pure Python):**

```python
def _map_sections_to_components(
    sections: list[Section],
    lines: list,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    snap_to_downbeat: bool = False,
) -> list[ComponentInstance]:
    if not sections:
        return []
    chorus_sections = [s for s in sections if s.label == "chorus"]
    if not chorus_sections:
        return []

    n = len(lines)

    def _sec_time(s: Section) -> tuple[float, float]:
        """Derive (start, end) seconds from a Section.

        Mirrors _expected_time_range in test_components_tuning.py and the
        end-time convention used by identify_from_lyrics_repetition:
        - start = timestamp of line s.line_start (0-based index s.line_start - 1)
        - end = timestamp of the line *after* the block (0-based index s.line_end),
          estimated via average line duration when at file end.
        """
        start = lines[s.line_start - 1].time_seconds
        if s.line_end < n:
            end = lines[s.line_end].time_seconds
        else:
            # At the last raw line: estimate via average duration
            # of the block's lines (mirrors _expected_time_range).
            block_durations = [
                lines[k + 1].time_seconds - lines[k].time_seconds
                for k in range(s.line_start - 1, min(s.line_end, n - 1))
            ]
            avg = sum(block_durations) / len(block_durations) if block_durations else 4.0
            end = lines[min(s.line_end - 1, n - 1)].time_seconds + avg

        # Snap only when the caller explicitly requested downbeat snapping
        # AND provided the grid. This matches the fallback path
        # (identify_from_lyrics_repetition uses `snap_to_downbeat` flag).
        if snap_to_downbeat and downbeats:
            start = _snap_to_downbeat(start, downbeats)
            end = _snap_to_downbeat(end, downbeats)
        elif beats:
            start = _snap_to_beat(start, beats)
            end = _snap_to_beat(end, beats)
        return start, end

    def _lyrics_excerpt(s: Section) -> Optional[str]:
        lines_in = [ln.text for ln in lines[s.line_start - 1:s.line_end]
                    if ln.text and ln.text.strip()]
        return "\n".join(lines_in) if lines_in else None

    components: list[ComponentInstance] = []
    n_choruses = len(chorus_sections)
    for i, sec in enumerate(chorus_sections):
        start, end = _sec_time(sec)
        conf = sec.confidence * 0.95
        excerpt = _lyrics_excerpt(sec)
        if n_choruses == 1:
            # Single chorus → two rows (entry + exit), same occurrence_index=1.
            components.append(
                ComponentInstance(
                    component_type="chorus", occurrence_index=1, role="entry",
                    start_time=start, end_time=end, confidence=conf,
                    source="llm_segmentation", section_label="chorus",
                    lyrics_excerpt=excerpt, llm_rationale=sec.rationale,
                )
            )
            components.append(
                ComponentInstance(
                    component_type="chorus", occurrence_index=1, role="exit",
                    start_time=start, end_time=end, confidence=conf,
                    source="llm_segmentation", section_label="chorus",
                    lyrics_excerpt=excerpt, llm_rationale=sec.rationale,
                )
            )
        else:
            role = "entry" if i == 0 else ("exit" if i == n_choruses - 1 else "none")
            components.append(
                ComponentInstance(
                    component_type="chorus", occurrence_index=i + 1, role=role,
                    start_time=start, end_time=end, confidence=conf,
                    source="llm_segmentation", section_label="chorus",
                    lyrics_excerpt=excerpt, llm_rationale=sec.rationale,
                )
            )

    # Verse / loop_target: the last verse-labeled section ending at or before
    # the first chorus's first line.
    first_chorus_start = lines[chorus_sections[0].line_start - 1].time_seconds
    verse_before: Optional[Section] = None
    for sec in sections:
        sec_start = lines[sec.line_start - 1].time_seconds
        if sec_start >= first_chorus_start:
            break
        if sec.label == "verse":
            verse_before = sec
    if verse_before is not None:
        start, end = _sec_time(verse_before)
        components.append(
            ComponentInstance(
                component_type="verse", occurrence_index=1, role="loop_target",
                start_time=start, end_time=end,
                confidence=verse_before.confidence * 0.95,
                source="llm_segmentation", section_label="verse",
                lyrics_excerpt=_lyrics_excerpt(verse_before),
                llm_rationale=verse_before.rationale,
            )
        )
    return components
```

Role logic and the single-chorus two-row contract (`entry` + `exit` with identical times) mirror `identify_from_allin1_sections` (`components.py:735-851`). The verse-selection rule (last verse section ending at or before first chorus start) mirrors Root Cause #1 from the alternatives spec.

**`_load_few_shot_examples() -> list[dict]`:**

Reads the committed JSON file and validates against leakage from the 3 held-out fixture songs:

```python
def _load_few_shot_examples() -> list[dict]:
    """Load few-shot examples from the committed JSON file.

    Each example must include ``source_song_id`` so the loader can assert
    it does not come from any of the 3 fixture evaluation songs (held-out).
    The file must contain 2-3 examples. If absent/empty, logs a warning and
    proceeds with zero examples (still valid).
    """
    few_shot_path = Path(__file__).parent / "segmentation_few_shot.json"
    if not few_shot_path.exists():
        logger.warning(
            "Few-shot examples file not found at %s; running with zero examples.",
            few_shot_path,
        )
        return []
    try:
        examples = json.loads(few_shot_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to load few-shot examples: %s", e)
        return []
    if not isinstance(examples, list):
        logger.warning("Few-shot examples file is not a list; ignoring.")
        return []
    # Leakage guard.
    for ex in examples:
        song_id = str(ex.get("source_song_id", "")).strip()
        if song_id in _EXPECTED_HELD_OUT_IDS:
            raise ValueError(
                f"Few-shot example source_song_id '{song_id}' is a held-out "
                f"fixture evaluation song. Remove this example from "
                f"segmentation_few_shot.json to prevent test-set leakage."
            )
    return examples
```

**`_validate_chorus_repetition(sections, lrc_content) -> list[Section]` (Design C validator):**

```python
def _validate_chorus_repetition(
    sections: list[Section], lrc_content: str
) -> list[Section]:
    # (Implementation note: the actual normalization and line-matching logic
    # mirrors the lyrics-repetition path's _normalize_line / signature match.
    # For spec brevity, this document references the algorithm contract; the
    # implementation file will contain the concrete string similarity calls.)
    # ...
    # Pseudocode contract:
    # 1. Parse LRC lines.
    # 2. For each chorus section:
    #    a. Extract normalized joined text of section lines.
    #    b. Search the *rest* of the song (exclude those line indices).
    #    c. Find occurrences of the *last* section line whose text repeats.
    #    d. If found within the section, trim line_end. If not found at all,
    #       multiply confidence by 0.60. If found and no trim needed, add 0.05
    #       (capped at 1.0).
    # 3. Never expand boundaries, never merge sections, never extend a
    #    previously-trimmed boundary. Only shorten `line_end` to a line whose
    #    text is verified to repeat elsewhere.
    return sections
```

The validator must **guard against re-introducing the original over-merge**: a trim only *shortens* `line_end` to a line whose text is verified to repeat elsewhere; it never expands a boundary, never merges sections, and never extends a previously-trimmed boundary. Confirmed-unchanged sections get a `+0.05` bonus; confirmed-trimmed get `*0.90`; non-repeated choruses are **kept** at `*0.60` (a non-repeated chorus is musically valid, e.g. an outro chorus), never dropped.

**`_sanity_check_llm(sections, lrc_content) -> Optional[list[Section]]` (opt-in 2nd/3rd call):**

```python
async def _sanity_check_llm(
    sections: list[Section],
    lrc_content: str,
    client: OpenAI,
    model: str,
) -> Optional[list[Section]]:
    numbered, n_lines = _render_numbered_lrc(lrc_content)
    proposed = json.dumps(
        [{"label": s.label, "line_start": s.line_start, "line_end": s.line_end}
         for s in sections],
        ensure_ascii=False,
    )
    prompt = (
        "Here is a proposed segmentation of a worship song. The numbered LRC and the "
        "proposed sections (label, line_start, line_end) follow. Return a JSON object "
        "with key 'correct' (true/false) and, if false, key 'rationale' describing any "
        "mislabeled or mis-bounded section.\n\n"
        f"{numbered}\n\nProposed sections:\n{proposed}"
    )

    def _call() -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system",
                 "content": "You verify song structure segmentations. Return JSON only."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=settings.SOW_LLM_SEGMENTATION_MAX_TOKENS,
        )
        return resp.choices[0].message.content or ""

    text = await call_llm_with_retry(
        _call, description="LLM segmentation sanity check"
    )
    try:
        verdict = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if verdict.get("correct") is True:
        return sections
    if verdict.get("correct") is False:
        return None  # caller issues a corrective 3rd call or falls back
    return None
```

**`segment_song(lrc_content, song_title, duration, beats, downbeats, snap_to_downbeat) -> list[ComponentInstance]` (public entry):**

```python
async def segment_song(
    lrc_content: str,
    song_title: Optional[str] = None,
    duration: Optional[float] = None,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    snap_to_downbeat: bool = False,
) -> list[ComponentInstance]:
    client = _build_client()
    model = _segmentation_model()
    few_shot = _load_few_shot_examples()
    messages = _build_segmentation_prompt(
        lrc_content, song_title, duration, few_shot
    )

    def _call() -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
            max_tokens=settings.SOW_LLM_SEGMENTATION_MAX_TOKENS,
        )
        return resp.choices[0].message.content or ""

    text = await call_llm_with_retry(_call, description="LLM whole-song segmentation")
    numbered, n_lines = _render_numbered_lrc(lrc_content)
    sections = _parse_segmenter_json(text, n_lines)
    if sections is None:
        return []
    sections = _validate_chorus_repetition(sections, lrc_content)
    if settings.SOW_LLM_SEGMENTATION_SANITY_CHECK:
        checked = await _sanity_check_llm(sections, lrc_content, client, model)
        if checked is None:
            corrected = await _corrective_segmentation_call(
                client, model, lrc_content, song_title, duration, few_shot, sections
            )
            if corrected is not None:
                sections = _validate_chorus_repetition(corrected, lrc_content)
    lines = list(parse_lrc(lrc_content).lines)
    return _map_sections_to_components(
        sections, lines, beats=beats, downbeats=downbeats,
        snap_to_downbeat=snap_to_downbeat,
    )
```

**Failure behavior:** any exception from `_build_client`, `call_llm_with_retry`, or parser returning `None` propagates as either `[]` (empty result from `segment_song`) or an exception that `extract_components` catches — in both cases `extract_components` falls through to the existing path (Change 4).

### Change 4 — Wire into `extract_components`

**File:** `ops/analysis-service/src/sow_analysis/workers/components.py`
**Location:** inside `extract_components`, in the `if not components and lrc_content:` block, with the LLM-path branch inserted BEFORE the existing beat-grid cache read at `components.py:1434`.

Add `use_llm_segmentation` as the last parameter (after `all_components`) so all existing call sites remain backward-compatible:

```python
async def extract_components(
    audio_path: Path,
    content_hash: str,
    cache_manager: CacheManager,
    r2_client: Optional[R2Client],
    sections: Optional[list[dict]] = None,
    lrc_content: Optional[str] = None,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
    force: bool = False,
    use_stems: bool = False,
    snap_to_downbeat: bool = False,
    energy_aware_roles: bool = False,
    all_components: bool = False,
    # v6: enable LLM whole-song segmentation (Design C). When True and
    # SOW_LLM_API_KEY is set, the LLM path runs before the beat-grid cache
    # read. Falls back to allin1/lyrics-repetition on any failure.
    use_llm_segmentation: bool = False,
) -> tuple[list[ComponentInstance], str]:
```

New branch (insert at the top of the `if not components and lrc_content:` block, before the beat-grid cache read at `components.py:1434`):

```python
    if not components and lrc_content:
        # v6: LLM whole-song segmentation — opt-in per job or globally via env.
        # The ``use_llm_segmentation`` job param is one-way OR: it can force-ON
        # the path when the env flag is off, but cannot force-OFF when env
        # is on.
        _use_llm = (
            use_llm_segmentation
            or settings.SOW_COMPONENTS_USE_LLM_SEGMENTATION
        )
        if _use_llm and settings.SOW_LLM_API_KEY:
            try:
                from .section_segmenter import segment_song
                seg_start = time.time()
                components = await segment_song(
                    lrc_content,
                    song_title=gf.title if gf is not None else None,
                    duration=gf.duration if gf is not None else None,
                    beats=beats,
                    downbeats=downbeats,
                    snap_to_downbeat=snap_to_downbeat,
                )
                logger.info(
                    f"LLM segmentation completed in {time.time() - seg_start:.2f}s "
                    f"({len(components)} components)"
                )
                if components:
                    source = "llm_segmentation"
            except Exception as e:
                logger.warning("LLM segmentation failed, falling back: %s", e)
                components = []

        if not components:
            # v6: prefer the beat-grid cache. ... (existing block unchanged)
            ...
```

Note: the existing `settings` import at `components.py:25` and `time` at `components.py:16` are already available; only the `section_segmenter` import is new. `snap_to_downbeat` is the existing function parameter (`components.py:1350`) passed through to `segment_song`.

**Gating note (user-confirmed):** the per-job option is one-way OR. It can force-ON the LLM path when the global env flag is off, but cannot force-OFF when the global env flag is on. To disable globally, set `SOW_COMPONENTS_USE_LLM_SEGMENTATION=false`.

### Change 5 — Add per-job option and extend `ComponentResult` API model

**File:** `ops/analysis-service/src/sow_analysis/models.py`
**Location:** `ComponentAnalysisOptions` (`models.py:74-96`). Add:

```python
    # v6: use LLM whole-song segmentation (Design C) for identification.
    # Operator sets this to A/B test the LLM path against the
    # lyrics-repetition fallback per song, independent of the
    # SOW_COMPONENTS_USE_LLM_SEGMENTATION env flag (which remains
    # the global default gate). The job option is one-way OR — it can
    # enable per-job but cannot disable when the env flag is on.
    use_llm_segmentation: bool = False
```

**File:** `ops/analysis-service/src/sow_analysis/models.py`
**Location:** `ComponentResult` (`models.py:219-247`). Add after `posture_reasoning`:

```python
    # v6: LLM segmentation fields
    section_label: Optional[str] = None    # e.g. "chorus"
    lyrics_excerpt: Optional[str] = None   # joined section text
    llm_rationale: Optional[str] = None    # model's free-text reason
```

**File:** `ops/analysis-service/src/sow_analysis/workers/queue.py`

Three changes:

1. **Function signature** — add `use_llm_segmentation: bool = False` to `_process_component_analysis_job` after `all_components` param.
2. **extract_components call site** — add `use_llm_segmentation=request.options.use_llm_segmentation` at `queue.py:1012`.
3. **ComponentResult conversion** — add after `posture_reasoning=c.posture_reasoning,` at `queue.py:1091`:

```python
                            section_label=c.section_label,
                            lyrics_excerpt=c.lyrics_excerpt,
                            llm_rationale=c.llm_rationale,
```

The CLI vector for submitting the per-job `use_llm_segmentation` option is the existing `--options-json` pattern on `sow-admin audio analyze components`, e.g.:

```bash
sow-admin audio analyze components --song-id <id> --options-json '{"use_llm_segmentation": true}'
```

### Change 6 — Few-shot examples file and package-data inclusion

**File:** `ops/analysis-service/src/sow_analysis/workers/segmentation_few_shot.json` (new, committed)

Schema — a top-level list of example objects; each has a **required** `source_song_id` field (for the leakage guard), a numbered-LRC `input` string, and the `sections` array the model should output. **These are PLACEHOLDERS to be filled by the operator** with hand-written examples from worship songs NOT in the 3-fixture set (no test-set leakage):

```json
[
  {
    "source_song_id": "__CHANGE_ME__",
    "input": "1  [00:10.00] 主 祢的愛激勵我心\n2  [00:25.00] 我願一生跟隨祢\n3  [00:40.00] 哈利路亞 哈利路亞\n4  [00:55.00] 祢聖名配得讚美\n5  [01:20.00] 主 祢的愛激勵我心\n6  [01:35.00] 我願一生跟隨祢\n7  [01:50.00] 哈利路亞 哈利路亞\n8  [02:05.00] 祢聖名配得讚美\n",
    "sections": [
      {"label": "verse",   "line_start": 1, "line_end": 2, "confidence": 0.9, "rationale": "first verse"},
      {"label": "chorus",  "line_start": 3, "line_end": 4, "confidence": 0.95, "rationale": "repeated refrain"},
      {"label": "verse",   "line_start": 5, "line_end": 6, "confidence": 0.9, "rationale": "verse repeated"},
      {"label": "chorus",  "line_start": 7, "line_end": 8, "confidence": 0.95, "rationale": "refrain repeated"}
    ]
  }
]
```

Placeholder note: the `input` and `sections` shown are illustrative; the operator must replace them with **real, manually-verified** numbered LRC excerpts from songs outside the 3-fixture eval set, plus their correct `sections` output. The `source_song_id` must be set to the actual song ID (not `__CHANGE_ME__`). The file must contain 2–3 such examples. `_load_few_shot_examples()` validates that none of the `source_song_id` values overlap with the 3 held-out fixture IDs.

**File:** `ops/analysis-service/pyproject.toml` — add package-data declaration so the JSON file is included in built wheels:

```toml
[tool.setuptools.package-data]
sow_analysis = ["workers/segmentation_few_shot.json"]
```

This is placed in the `[tool.setuptools]` section alongside the existing `[tool.setuptools.packages.find]` configuration.

### Change 7 — Test file `test_section_segmenter.py`

**File:** `ops/analysis-service/tests/test_section_segmenter.py` (new)

```python
"""Tests for the LLM whole-song segmentation module (Design C).

LLM-dependent tests are gated via @pytest.mark.skipif on SOW_LLM_LIVE_TESTS.
They run only when SOW_LLM_LIVE_TESTS=1 with a configured SOW_LLM_API_KEY.
No custom pytest markers are used — skipif is the pattern.
"""

import os

import pytest

from sow_analysis.workers.section_segmenter import (
    Section,
    _load_few_shot_examples,
    _map_sections_to_components,
    _parse_segmenter_json,
    _render_numbered_lrc,
    _validate_chorus_repetition,
    segment_song,
)
from sow_analysis.workers.components import extract_components as _extract_components
from sow_analysis.workers.lrc_parser import parse_lrc
```

**Test list:**

- **`test_render_numbered_lrc_format`** (unit): `_render_numbered_lrc` on a short LRC with a blank line returns numbered block with correct count; verify blank lines are numbered.
- **`test_render_numbered_lrc_empty_lrc`** (unit): verify `ValueError` → `("(empty LRC)", 0)`.
- **`test_parse_segmenter_json_valid`** (unit): well-formed JSON yields `list[Section]`.
- **`test_parse_segmenter_json_rejects_overlap`** / **`rejects_oob`** / **`rejects_bad_label`** / **`rejects_gap`** (unit): each invalid input returns `None`; malformed top-level returns `None`.
- **`test_map_sections_to_components`** (unit, no LLM): hand-crafted Sections + parsed LRC; assert `component_type`, `role`, `occurrence_index`, `source`, `section_label`, `lyrics_excerpt`.
  - **Single-chorus case** — assert two-row entry+exit contract.
  - **Verse-before-chorus** — assert `loop_target` role selected.
  - **snap_to_downbeat=False case** — assert beat snapping is NOT applied when flag is off (fixing v1's unconditional snap issue).
- **`test_load_few_shot_leakage_guard`** (unit): `source_song_id` matching a fixture ID raises `ValueError`; `__CHANGE_ME__` placeholder does not; absent key returns empty list (warning).
- **`test_validate_chorus_repetition_trims`** (unit): over-merged chorus `line_end` tightened to last repeating line; confidence `* 0.90`.
- **`test_validate_chorus_repetition_keeps_nonrepeated`** (unit): non-repeated chorus kept with confidence `* 0.60`.
- **`test_extract_components_falls_back_when_llm_disabled`** (integration): `SOW_COMPONENTS_USE_LLM_SEGMENTATION=false` → source is `lyrics_repetition` (not `llm_segmentation`), bit-identical to current.
- **`test_extract_components_falls_back_when_no_key`** (integration): `SOW_LLM_API_KEY=""` + `use_llm_segmentation=True` → falls through without exception; source != `llm_segmentation`.
- **`test_extract_components_falls_back_on_json_violation`** (integration): monkeypatch `_parse_segmenter_json` → `None`; `use_llm_segmentation=True` + key set → falls through.
- **`test_extract_components_no_regression_default`** (integration): default env → source is `lyrics_repetition` or `allin1_sections` (no `llm_segmentation`).
- **`@pytest.mark.skipif(os.environ.get("SOW_LLM_LIVE_TESTS") != "1", ...)`**: `test_segment_song_live` — full `segment_song` call; asserts components with `source="llm_segmentation"` and ≥1 chorus row.

---

## Evaluation

Reuse the existing harness at `ops/analysis-service/tests/test_components_tuning.py` + `eval/components_tuning/` unchanged in structure at the repo root (NOT under `ops/analysis-service/`). The scorer partitions by `component_type` only — a new `source` string has no effect on IoU math and no scorer changes are needed. Only the review-dump header comment (`# source: {c.source}`) updates with the new value.

**Success criteria (gating, decided before implementation):**
- **Pass bar:** grand-total IoU ≥ **0.70** on the 3 fixtures.
- **Stretch:** ≥ **0.85** per-song mean on all 3.
- **No regression:** the no-LLM fallback still produces the existing **0.286** baseline, asserted by `test_extract_components_no_regression_default`.

**A/B testing:** use the `use_llm_segmentation` job option (Change 5) via `sow-admin audio analyze components --options-json '{"use_llm_segmentation": true}'` to run the LLM path per song without global env changes.

**Running the eval:**
```bash
cd ops/analysis-service && uv run --extra dev pytest tests/test_components_tuning.py -v -s
```

---

## Rollout order

1. **Change 0** — bump `COMPONENT_SCHEMA_VERSION` to 3 in analysis-service AND admin-cli (`analysis.py:22`, `component_editor/constants.py:49`).
2. **Change 1** — extend `ComponentInstance` + serializers; run `test_components.py`.
3. **Change 2** — add 5 new settings to `config.py`.
4. **Change 7 (foundation)** — create `test_section_segmenter.py` skeleton (no implementation yet; validates imports).
5. **Change 6** — commit `segmentation_few_shot.json` scaffold + `pyproject.toml` package-data.
6. **Change 3** — implement `section_segmenter.py`; run unit tests (LLM-free) until green.
7. **Change 4** — wire LLM branch into `extract_components`; run `test_components.py` + integration fallback tests.
8. **Change 5** — add job option + API model fields; re-run `test_components.py`; run relevant queue tests.
9. **Evaluate:** run `test_components_tuning.py` with `SOW_COMPONENTS_USE_LLM_SEGMENTATION=true` and `SOW_LLM_API_KEY` set; compare IoU against 0.286.
10. **Regression:** run `test_components_tuning.py` with defaults (no LLM flags); confirm 0.286 baseline intact.
11. **Re-compute sanity:** run `sow-admin audio analyze components --options-json '{"use_llm_segmentation":true}' --force` on each of the 3 component fixture songs; verify persisted `components.json` populates the new fields; verify admin-cli reads accept v3 payloads without cache misses.

---

## Manual verification checklist

1. **Bit-identical fallback with flag off:** `SOW_COMPONENTS_USE_LLM_SEGMENTATION=false` + `--force` → identical components/source as pre-v2. Deployments without a key still produce 0.286.
2. **LLM path produces components:** `SOW_COMPONENTS_USE_LLM_SEGMENTATION=true` (or `--options-json '{"use_llm_segmentation":true}'`) + `SOW_LLM_API_KEY` set → `source="llm_segmentation"` + new fields populated.
3. **Validator trims over-merged chorus:** `test_validate_chorus_repetition_trims` passes; no expansion from the 13-line block.
4. **Schema version bump triggers re-compute:** v2-cached `components.json` is a miss at v3; new payload carries `schema_version: 3`. Admin-cli accepts v3.
5. **New fields in admin-cli and API:** `sow-admin components list` + `/api/v1/jobs/{id}` show the new fields on LLM-path rows, `None`/missing on fallback rows.
6. **Existing `test_components.py` stays green.**
7. **LLM-dependent tests skipped by default:** `pytest tests/test_section_segmenter.py` passes without `SOW_LLM_LIVE_TESTS`; live tests run with the env var set.
8. **Per-job option is force-ON only:** `env=false` + `options=true` → runs LLM; `env=true` + `options=false` → still runs LLM (one-way OR).

---

## Open questions (resolve before implementation)

1. **max_tokens tuning.** The current setting `SOW_LLM_SEGMENTATION_MAX_TOKENS = 2048` may need adjustment after the first prototype run against a long LRC. Operator confirms whether the setting is sufficient.
2. **Model choice.** `SOW_LLM_SEGMENTATION_MODEL` may need a stronger model for Chinese lyric structure. Operator decides the value; default falls back to `SOW_LLM_MODEL`.
3. **Few-shot source.** Confirmed hand-written from other songs. The operator must actually produce 2–3 examples (Change 6) with real song IDs, ensuring none draw from the 3-fixture set. `source_song_id` field is mandatory and enforced by the leakage guard.
4. **Sampling temperature.** Segmentation wants deterministic output → `temperature=0` on all calls. Confirm the provider honours it.
5. **Validator over-merge guard.** Ensure `_validate_chorus_repetition` only ever shortens boundaries to lines verified to repeat (never re-introduces the 13-line block). A unit test asserting no expansion is included.
6. **Sanity-check opt-in threshold.** Enable `SOW_LLM_SEGMENTATION_SANITY_CHECK` (2nd/3rd call) only if the measured IoU is below pass bar on the 3 fixtures; otherwise leave it off.

---

## Out of scope

- **Implementing** — this document is the implementation plan; coding is a separate step.
- **Re-tuning `ThemeClassifier`** — orthogonal, runs downstream of identification.
- **allin1-sections path** beyond treating it as a weak optional prior (it stays as one input to the fallback ordering; the LLM path does not depend on it).
- **Expanding the 3-song fixture set** — a 10-song follow-up eval is planned but separate.
- **Weight-tuning the repetition path** — the v1 loop plateaued; the repetition path is retained only as an unchanged fallback.
- **Persisting LLM segmentation results to the song_components DB table** — the DB table schema migration for `section_label`/`lyrics_excerpt`/`llm_rationale` is deferred as administrative. The fields flow through to `components.json` and the job-result API without DB storage.

---

## Related specs

- `specs/component-identification-alternatives-v1.md` — the design comparison that framed Design A/B/C.
- `specs/component-identification-tuning-loop-v1.md` — the v1 weight-tuning effort (scoring formula, line-indexing convention, fixture layout).
- `specs/fix-component-analysis-llm-persistence-admin-cli-v3-implementation.md` — admin-cli LLM persistence fix (parallel consumer of `components.json`).
- `specs/chorus-component-metadata-impl-plan-v5.md` — v5 component metadata plan (origin of `ComponentAnalysisOptions` and `ComponentResult`).
