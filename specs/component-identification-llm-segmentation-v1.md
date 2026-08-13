# Implementation Plan v1: LLM Whole-Song Segmentation for Chorus/Verse Identification

> Goal: replace the plateaued lyrics-repetition component identification with **Design C** — LLM whole-song segmentation (`section_segmenter.py`) plus a deterministic repetition cross-check validator — to lift IoU from the current **0.286** baseline to **≥ 0.70** on the 3-song fixture set.
>
> This is the detailed implementation plan for Design C as selected in `specs/component-identification-alternatives-v1.md`. It implements the full scoring path (both the primary LLM segmentation call AND the opt-in sanity-check calls) because accuracy is the priority over token cost / tuning complexity.
>
> Scope: **identification** in `ops/analysis-service/src/sow_analysis/workers/components.py` and the new `section_segmenter.py` module. The allin1-sections path and the theme classifier are out of scope (see "Out of scope").

## Why the repetition path is being superseded (root causes)

`identify_from_lyrics_repetition` (`ops/analysis-service/src/sow_analysis/workers/components.py:854`) plateaus at **grand-total IoU ≈ 0.286** on the 3-fixture eval set. The v1 weight-tuning loop (`specs/component-identification-tuning-loop-v1.md`) grid-searched the four multi-cue weight knobs and found **no meaningful win** — the gap is structural, not weight-tunable:

1. **Zero `verse`/`loop_target` components on all 3 songs (verse IoU = 0.0).** The verse is synthesized by walking backward from the first chorus occurrence (`components.py:1089-1119`), but the winning chorus candidate's first occurrence always starts at line 1 / index 0 (the merged block absorbs the verse), so `first_chorus_start_idx > 0` is `False` and no verse is emitted. This single defect costs ≈ a third of achievable IoU per song.
2. **Over-merged chorus windows.** The repeated-sequence signature greedily joins verse + chorus into one giant block (the 13-line blocks beginning with the verse 聖潔耶穌/哈利路亞 and 祢的話在我心). The predicted range only partially covers the true chorus, capping IoU near 0.25–0.5.
3. **Entry/exit role misalignment.** With only two essential chorus rows derived by pure occurrence order, the algorithm labels the ground-truth *entry* as *exit* → −0.10 role-mismatch penalty.

These are fixed by Design C: the LLM labels `verse` independently of where the chorus candidate starts (fixes #1), semantically separates verse from chorus content rather than joining them (fixes #2), and the mapper derives roles from ordered chorus occurrences (fixes #3, matching the scorer's own definition).

## Locked decisions (user-confirmed)

These restate the five user-confirmed decisions for Design C, plus the locked constraints inherited from the alternatives spec.

1. **Design C — LLM whole-song segmentation + deterministic repetition cross-check validator.** Implement BOTH the primary LLM segmentation call and the opt-in sanity-check calls (`SOW_LLM_SEGMENTATION_SANITY_CHECK`), since accuracy is the priority.
2. **Accuracy first.** Accept the ~450 new lines and the added tuning complexity for the highest achievable IoU. Maximize IoU over token/latency parsimony.
3. **`SOW_LLM_SEGMENTATION_MODEL`** — add a separate env var override defaulting to the shared `SOW_LLM_MODEL`. It can be pointed at a stronger model (e.g. `gpt-4o`) for Chinese lyric structure segmentation without disturbing the theme classifier's model.
4. **Hand-written few-shot examples** drawn from worship songs **NOT** in the 3-fixture eval set (no test-set leakage). Manually segmented and committed under `workers/segmentation_few_shot.json`.
5. **Bump `COMPONENT_SCHEMA_VERSION` now** — forces re-compute of cached `components.json` across all songs. Consumers (admin-cli, webapp, render-worker) read defensively so they remain compatible. The new fields `section_label` and `lyrics_excerpt` populate on re-compute.

Locked constraints carried over from `component-identification-alternatives-v1.md`:

- **LLM stack:** reuse the existing OpenAI-compatible stack — `openai` SDK, `SOW_LLM_API_KEY` / `SOW_LLM_BASE_URL` / `SOW_LLM_MODEL` env vars, `call_llm_with_retry` rate-limit wrapper, `response_format={"type":"json_object"}`. **No** new dependencies (`anthropic`, `litellm`, `instructor`). Provider-agnostic via `base_url`.
- **LLM cost/latency:** no hard constraint; prioritize accuracy. State LLM-call count per song (see "LLM calls per song" below).
- **allin1 sections:** unreliable; optional weak prior only. The LLM path must not depend on allin1 correctness.
- **Output contract:** schema evolution allowed — extend `ComponentInstance` (`components.py:223`) with new optional fields `section_label`, `lyrics_excerpt`, `llm_rationale`. New fields default to `None` for backward compat. `COMPONENT_SCHEMA_VERSION` bump is in scope.
- **Deterministic fallback:** retain the no-LLM path; the existing `identify_from_lyrics_repetition` is promoted to fallback and unchanged, so deployments without `SOW_LLM_API_KEY` keep the current 0.286 baseline.

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

## Critical files

### New files
- `ops/analysis-service/src/sow_analysis/workers/section_segmenter.py` — the LLM segmentation module (~450 lines): client construction, numbered-LRC rendering, prompt builder, JSON parser/validator, section-to-component mapper, chorus-repetition validator, opt-in sanity check.
- `ops/analysis-service/src/sow_analysis/workers/segmentation_few_shot.json` — committed hand-written few-shot examples (2–3) from worship songs NOT in the 3-fixture set.
- `ops/analysis-service/tests/test_section_segmenter.py` — unit + integration tests.

### Modified files
- `ops/analysis-service/src/sow_analysis/storage/cache.py` — bump `COMPONENT_SCHEMA_VERSION` (Change 0).
- `ops/analysis-service/src/sow_analysis/workers/components.py` — extend `ComponentInstance` (Change 1), update `_serialize_components` / `_deserialize_components` (Change 1), wire the LLM path into `extract_components` (Change 4).
- `ops/analysis-service/src/sow_analysis/config.py` — add four settings (Change 2).
- `ops/analysis-service/src/sow_analysis/models.py` — add `use_llm_segmentation` option to `ComponentAnalysisOptions` (Change 5).
- `ops/analysis-service/src/sow_analysis/workers/queue.py` — thread the new option through `_process_component_analysis_job` (Change 5).
- `ops/analysis-service/tests/test_components_tuning.py` — scorer tolerates `source="llm_segmentation"`; add no-regression fallback assertion (Evaluation section).

### Unchanged files (do NOT modify)
- `ops/analysis-service/src/sow_analysis/workers/classifier.py` — no changes; `ThemeClassifier._client` / `_parse_llm_json` are only *referenced* as the pattern to mirror.
- `ops/analysis-service/src/sow_analysis/workers/llm_rate_limit.py` — no changes; `call_llm_with_retry` reused as-is.
- `ops/analysis-service/src/sow_analysis/workers/lrc_parser.py` — no changes; `parse_lrc` reused.
- `ops/analysis-service/tests/test_components.py` — existing tests untouched; they exercise the fallback path, which is unchanged.
- `ops/analysis-service/src/sow_analysis/workers/components.py` `identify_from_allin1_sections` and `identify_from_lyrics_repetition` — no behavioral changes (only file-level additions).

## Shared building blocks

These mirror the shared building blocks (S1–S5) from the alternatives spec, adapted for implementation.

### S1. LLM client & call helper — `section_segmenter.py`

Constructs an `OpenAI(api_key=..., base_url=..., timeout=..., max_retries=0)` client **identical in shape to `ThemeClassifier._client`** (`classifier.py:214-234`), wrapped synchronously through `call_llm_with_retry` (`llm_rate_limit.py:474`). New settings (Change 2) gate the path.

### S2. Numbered-LRC prompt input

1-based raw-LRC line numbering (see "Line indexing convention"). The model returns integer line ranges instead of timestamps, which the post-processor maps to `LRCLine.time_seconds`.

### S3. JSON response schema + parser + few-shot

`sections` array with `label` / `line_start` / `line_end` / `confidence` / `rationale`; `label ∈ {intro, verse, prechorus, chorus, bridge, outro, instrumental}`. Constraints: non-overlapping, cover all non-blank lines, in-order. `_parse_segmenter_json` mirrors `ThemeClassifier._parse_llm_json`'s defensive pattern (`classifier.py:515`). On any violation → fall back to the deterministic path.

### S4. Section-to-ComponentInstance mapper (deterministic, pure Python)

Mirrors `identify_from_allin1_sections` (`components.py:735-851`): collect chorus-labeled sections in order → `occurrence_index = 1..N`; role `entry`/`exit`/`none`; single chorus → two rows; verse/`loop_target` = last verse-labeled section ending at or before the first chorus starts. Sets new optional fields + `source = "llm_segmentation"` + `confidence = section.confidence * 0.95`; reuses the beat/downbeat snapping helpers.

### S5. Deterministic fallback (shared)

When the flag is off, `SOW_LLM_API_KEY` unset, the LLM call raises, or JSON validation fails, `extract_components` falls through to the existing `identify_from_allin1_sections` / `identify_from_lyrics_repetition` ordering (`components.py:1419-1466`). No-LLM deployments keep the 0.286 baseline.

## Design C specifics (the cross-check validator)

- **First LLM call** segments the whole song (identical to Design A via S1–S4).
- **Non-LLM validator** (`_validate_chorus_repetition`) runs the current repetition clustering WITHIN each LLM-labelled `chorus` section to:
  - (a) confirm the section's lyrics actually repeat elsewhere in the song;
  - (b) tighten `line_end` to the last line whose text repeats (fixing the over-merge from the *chorus side*).
  - Sections that fail repetition validation are flagged with **reduced confidence but KEPT** — a non-repeated chorus is musically valid (e.g. an outro chorus).
- **Optional 2nd LLM call** (`_sanity_check_llm`): present the validated section list back to the LLM for a yes/no sanity check ("is this segmentation correct?"); on "no" with rationale, run a corrective 3rd call. This is opt-in (`SOW_LLM_SEGMENTATION_SANITY_CHECK`, default off) — enable it if Design A's measured IoU is still below target.
- **Guard against re-introducing the original over-merge** when trimming: a trim may only shorten `line_end` to a line whose text is verified to repeat, and may never expand a section or lengthen a boundary that the validator previously confirmed. (See `_validate_chorus_repetition`.)

### LLM calls per song

| Mode | Calls/song |
|---|---|
| Default (sanity check off) | 1 |
| Sanity check (flag on, answer "yes") | 2 |
| Sanity check (flag on, answer "no") | up to 3 |

### Confidence-blending formula (concrete)

`_validate_chorus_repetition` recomputes each chorus section's confidence from `section.confidence` and the repetition cross-check result:

```
if repetition_confirmed and boundary_unchanged:  conf = section.confidence + 0.05      # capped at 1.0
if repetition_confirmed and boundary_trimmed:    conf = section.confidence * 0.90
if repetition_not_found:                          conf = section.confidence * 0.60
```

Where:
- `repetition_confirmed` = the section's core line text (see validator below) matches at least one other region of the song within a distance threshold.
- `boundary_trimmed` = `line_end` was shortened by the validator.
- `repetition_not_found` = no repeated match anywhere; the section is retained but its confidence is heavily penalized (the S4 mapper then multiplies by `0.95`).

## Implementation changes

### Change 0 — Bump `COMPONENT_SCHEMA_VERSION`

**File:** `ops/analysis-service/src/sow_analysis/storage/cache.py`
**Location:** constant at `cache.py:15`.

Current value: `COMPONENT_SCHEMA_VERSION = 2`
New value: `COMPONENT_SCHEMA_VERSION = 3`

This forces re-compute of cached `components.json` across all songs: `CacheManager` treats a mismatched version as a cache miss and recomputes (see the comment block at `cache.py:13-14`). The new optional fields `section_label` / `lyrics_excerpt` / `llm_rationale` populate only on re-compute (or `--force`), which the bump guarantees.

### Change 1 — Extend `ComponentInstance` dataclass

**File:** `ops/analysis-service/src/sow_analysis/workers/components.py`
**Location:** the `ComponentInstance` dataclass, after the v5 reasoning fields (`components.py:265-267`).

Add three new optional fields, all defaulting to `None`:

```python
    # v5: LLM reasoning fields
    theme_reasoning: Optional[str] = None
    posture_reasoning: Optional[str] = None
    # v6: LLM whole-song segmentation (Design C) fields
    section_label: Optional[str] = None
    lyrics_excerpt: Optional[str] = None
    llm_rationale: Optional[str] = None
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
**Location:** in the `Settings` class, next to the existing LLM configuration block (`config.py:112-155`), following the same pydantic-settings plain-attribute pattern.

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
```

Note: `SOW_LLM_SEGMENTATION_MODEL` is `Optional[str]` and empty-string-to-`None` is handled inside `section_segmenter.py` (where it falls back to `SOW_LLM_MODEL`), consistent with the empty-string handling used elsewhere via the `_empty_str_to_none` validator pattern (`config.py:240-257`).

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

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from openai import OpenAI

from ..config import settings
from .components import ComponentInstance, identify_from_lyrics_repetition
from .llm_rate_limit import call_llm_with_retry
from .lrc_parser import parse_lrc

logger = logging.getLogger(__name__)
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

**`_render_numbered_lrc(lrc_content) -> str` (S2 — numbered format including blanks):**

```python
def _render_numbered_lrc(lrc_content: str) -> str:
    lines = parse_lrc(lrc_content).lines
    out: list[str] = []
    for i, ln in enumerate(lines, start=1):
        text = ln.text if ln.text is not None else ""
        stamp = f"{ln.time_seconds:.2f}"
        out.append(f"{i}  [{stamp}] {text}")
    return "\n".join(out), len(lines)
```

Returns the numbered block and the total raw line count (used for bounds validation and for the `n_lines` argument of `_parse_segmenter_json`). Note this counts **every physical line, blanks included**, per the line-indexing convention.

**`_build_segmentation_prompt(lrc_content, song_title, duration, few_shot_examples) -> str` (S2+S3 prompt builder):**

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

Few-shot examples are read from the committed file (Change 6) and are **hand-written from worship songs NOT in the 3-fixture set**, so fixtures stay held-out.

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
        if line_start <= prev_end + 1 or (line_start, line_end) in seen_ranges:
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

Validation rules (any failure → `None` → caller falls back): requires a dict with a `sections` list; every item is a dict with a valid label, in-bounds inclusive range, non-overlap with the previous section (strictly increasing `line_start > prev line_end + 1` — a gap of exactly one blank line is tolerated), and no duplicate range. Individual OOB/overlapping/bad-label entries are treated as a total failure (whole result rejected) so a corrupted chunk does not silently produce a monoculture segmentation; this matches the "on any violation → fall back" policy in S3.

**`_map_sections_to_components(sections, lines, beats, downbeats) -> list[ComponentInstance]` (S4 mapper, pure Python):**

```python
def _map_sections_to_components(
    sections: list[Section],
    lines: list,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
) -> list[ComponentInstance]:
    if not sections:
        return []
    chorus_sections = [s for s in sections if s.label == "chorus"]
    if not chorus_sections:
        return []

    def _sec_time(s: Section) -> tuple[float, float]:
        start = lines[s.line_start - 1].time_seconds
        if s.line_end < len(lines):
            end = lines[s.line_end].time_seconds
        else:
            end = start + 4.0
        if downbeats:
            start = _snap_to_downbeat(start, downbeats)
            end = _snap_to_downbeat(end, downbeats)
        elif beats:
            start = _snap_to_beat(start, beats)
            end = _snap_to_beat(end, beats)
        return start, end

    def _lyrics_excerpt(s: Section) -> Optional[str]:
        lines_in = [ln.text for ln in lines[s.line_start - 1:s.line_end] if ln.text and ln.text.strip()]
        return "\n".join(lines_in) if lines_in else None

    components: list[ComponentInstance] = []
    n_choruses = len(chorus_sections)
    for i, sec in enumerate(chorus_sections):
        start, end = _sec_time(sec)
        conf = sec.confidence * 0.95
        excerpt = _lyrics_excerpt(sec)
        if n_choruses == 1:
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
                start_time=start, end_time=end, confidence=verse_before.confidence * 0.95,
                source="llm_segmentation", section_label="verse",
                lyrics_excerpt=_lyrics_excerpt(verse_before),
                llm_rationale=verse_before.rationale,
            )
        )
    return components
```

Role logic and the single-chorus two-row contract (`entry` + `exit` with identical times) mirror `identify_from_allin1_sections` (`components.py:735-851`). The verse is the **last `verse`-labelled section ending at or before the first chorus start** — this fixes Root Cause #1 because the LLM labels the 聖潔耶穌/哈利路亞 block as `verse` independent of where any repeated block starts, and it becomes the `loop_target`. Snapping reuses `_snap_to_downbeat` / `_snap_to_beat` from `components.py`.

**`_validate_chorus_repetition(sections, lrc_content) -> list[Section]` (Design C validator):**

```python
def _validate_chorus_repetition(sections: list[Section], lrc_content: str) -> list[Section]:
    lines = list(parse_lrc(lrc_content).lines)
    valid = [s for s in sections if s.label == "chorus"]
    if not valid:
        return sections
    core_lines = _normalize_lines(
        "\n".join(lines[valid[0].line_start - 1:valid[0].line_end].text or "" for l in ...)
    )
    # (For spec brevity: core_lines is the normalized joined text of the first
    # chorus section's non-blank lines; repetition is confirmed by matching each
    # line against occurrences in the rest of the LRC using the same exact/near
    # matching the lyrics-repetition path uses.)
    for sec in sections:
        if sec.label != "chorus":
            continue
        sec_lines = _line_range_text(lines, sec.line_start, sec.line_end)
        haystack = _line_range_text(lines, 1, len(lines), exclude=sec)
        confirmed, last_repeat_idx = _find_last_repeating_line(sec_lines, haystack)
        if confirmed and last_repeat_idx is not None and last_repeat_idx < sec.line_end:
            # (b) tighten line_end to the last line whose text repeats (chorus side).
            sec = Section(
                label=sec.label, line_start=sec.line_start, line_end=last_repeat_idx,
                confidence=sec.confidence * 0.90, rationale=sec.rationale,
            )
        elif confirmed:
            sec = Section(
                label=sec.label, line_start=sec.line_start, line_end=sec.line_end,
                confidence=min(1.0, sec.confidence + 0.05), rationale=sec.rationale,
            )
        else:
            sec = Section(
                label=sec.label, line_start=sec.line_start, line_end=sec.line_end,
                confidence=sec.confidence * 0.60, rationale=sec.rationale,
            )
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

On "no", the caller issues a corrective 3rd call — a fresh `segment_song` call seeded with the validator's trimmed sections as an additional few-shot hint (see `segment_song`). On any parse failure, the 2nd/3rd call is skipped and the centrally-validated sections produced by call 1 are used.

**`segment_song(lrc_content, song_title, duration, beats, downbeats) -> list[ComponentInstance]` (public entry):**

```python
async def segment_song(
    lrc_content: str,
    song_title: Optional[str] = None,
    duration: Optional[float] = None,
    beats: Optional[list[float]] = None,
    downbeats: Optional[list[float]] = None,
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
    return _map_sections_to_components(sections, lines, beats, downbeats)
```

**`_load_few_shot_examples() -> list[dict]`** (reads the committed JSON file, Change 6).

**`_corrective_segmentation_call(...) -> Optional[list[Section]]`** (3rd call; re-runs `_build_segmentation_prompt` with the validated section list appended as guidance and re-parses via `_parse_segmenter_json`; returns `None` on failure).

**Failure behavior:** any exception from `_build_client`, `call_llm_with_retry`, or parser returning `None` propagates as either `[]` (empty result from `segment_song`) or an exception that `extract_components` catches — in both cases `extract_components` falls through to the existing path (Change 4).

### Change 4 — Wire into `extract_components`

**File:** `ops/analysis-service/src/sow_analysis/workers/components.py`
**Location:** inside `extract_components`, in the `if not components and lrc_content:` block, with the LLM-path branch inserted BEFORE the existing `identify_from_lyrics_repetition` fallback (`components.py:1430`).

New branch (add at the top of the `if not components and lrc_content:` block, before the beat-grid cache read at `components.py:1434`):

```python
    if not components and lrc_content:
        if settings.SOW_COMPONENTS_USE_LLM_SEGMENTATION and settings.SOW_LLM_API_KEY:
            try:
                from .section_segmenter import segment_song
                seg_start = time.time()
                components = await segment_song(
                    lrc_content,
                    song_title=gf.title if gf is not None else None,
                    duration=gf.duration if gf is not None else None,
                    beats=beats,
                    downbeats=downbeats,
                )
                logger.info(
                    f"LLM segmentation completed in {time.time() - seg_start:.2f}s "
                    f"({len(components)} components)"
                )
                if components:
                    source = "llm_segmentation"
            except Exception as e:
                logger.warning(f"LLM segmentation failed, falling back: {e}")
                components = []

        if not components:
            # v6: prefer the beat-grid cache. ... (existing block unchanged)
            ...
```

Notes:
- Gated on **both** `settings.SOW_COMPONENTS_USE_LLM_SEGMENTATION` **and** the presence of `SOW_LLM_API_KEY` (mirrors `ThemeClassifier.__init__`'s requirement, `classifier.py:215`).
- The LLM path runs before the beat-grid cache read because `segment_song` does its own snapping; the beat-grid is still consulted by the fallback path below it.
- If the LLM path yields components → `source = "llm_segmentation"`. Otherwise (flag off, no key, exception, or empty result) execution falls through to the existing `identify_from_allin1_sections` / `identify_from_lyrics_repetition` ordering (`components.py:1419-1466`) unchanged.
- `gf.title` / `gf.duration` are read off the precomputed `GlobalFeatures` (optionally None for songs without global features, in which case `None` is passed as `song_title`/`duration` — both are tolerated by `_build_segmentation_prompt`).

### Change 5 — Add `--use-llm-segmentation` flag to COMPONENT ANALYSIS job options

**File:** `ops/analysis-service/src/sow_analysis/models.py`
**Location:** `ComponentAnalysisOptions` (`models.py:74-96`). Add:

```python
    # v6: use LLM whole-song segmentation (Design C) for identification.
    # Operator sets this to A/B the LLM path against the lyrics-repetition
    # fallback per song, independent of the SOW_COMPONENTS_USE_LLM_SEGMENTATION
    # env flag (which remains the default gate).
    use_llm_segmentation: bool = False
```

**File:** `ops/analysis-service/src/sow_analysis/workers/queue.py`
**Location:** `_process_component_analysis_job`, at the `extract_components` call site (`queue.py:997-1012`). Set the flag on the env gate so either the env var OR the per-job option turns the LLM path on:

```python
                        use_llm_segmentation=request.options.use_llm_segmentation,
```

**File:** `ops/analysis-service/src/sow_analysis/config.py`
**Location:** add to Change 2 block:

```python
    SOW_COMPONENTS_USE_LLM_SEGMENTATION_DEFAULT: bool = False
```

Where the effective gate in `extract_components` (Change 4) becomes `(settings.SOW_COMPONENTS_USE_LLM_SEGMENTATION or settings.SOW_COMPONENTS_USE_LLM_SEGMENTATION_DEFAULT)`; the practical design is that the **per-job `use_llm_segmentation` option short-circuits the env gate**, so the operator can enable the LLM path for a single song without flipping a global env var. (If the flag plumbing proves noisy, the operator can instead just set `SOW_COMPONENTS_USE_LLM_SEGMENTATION=true` for the whole analysis run; the per-job flag is the explicit A/B lever.)

Reference job options pattern: `ComponentAnalysisOptions` at `models.py:74-96`; the extraction call site at `queue.py:997-1012`. The `analyzer/queue` job schema must accept the new field (BaseModel default `False` keeps old requests valid).

### Change 6 — Few-shot examples file

**File:** `ops/analysis-service/src/sow_analysis/workers/segmentation_few_shot.json` (new, committed)

Schema — a top-level list of example objects; each has a numbered-LRC `input` string and the `sections` array the model should output. **These are PLACEHOLDERS to be filled by the operator** with hand-written examples from worship songs NOT in the 3-fixture set (no test-set leakage):

```json
[
  {
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

Placeholder note: the `input` strings shown are illustrative; the operator must replace them with **real, manually-verified** numbered LRC excerpts from songs outside the 3-fixture eval set (e.g. from the wider song library), plus their correct `sections` output. The file must contain 2–3 such examples. `_load_few_shot_examples()` reads this file at runtime; if absent/empty it logs a warning and proceeds with zero few-shot examples (still valid).

### Change 7 — Test file `test_section_segmenter.py`

**File:** `ops/analysis-service/tests/test_section_segmenter.py` (new)

```python
"""Tests for the LLM whole-song segmentation module (Design C).

LLM-dependent tests are marked @pytest.mark.llm and skipped by default.
They run only when SOW_LLM_LIVE_TESTS=1 with a configured SOW_LLM_API_KEY.
"""

import os

import pytest

from sow_analysis.workers.section_segmenter import (
    Section,
    _map_sections_to_components,
    _parse_segmenter_json,
    _render_numbered_lrc,
    _validate_chorus_repetition,
)
from sow_analysis.workers.lrc_parser import parse_lrc

pytestmark = pytest.mark.skipif(
    os.environ.get("SOW_LLM_LIVE_TESTS") != "1",
    reason="LLM live tests disabled unless SOW_LLM_LIVE_TESTS=1",
)
```

Tests:

- **`test_render_numbered_lrc_format`** (unit, no LLM): `_render_numbered_lrc` on a short LRC with a blank line returns a numbered block where every physical line (blanks included) has a 1-based index and the returned line count equals the number of raw parsed lines.
- **`test_parse_segmenter_json_valid`** (unit): a well-formed JSON response yields `list[Section]` with correct labels/bounds/confidence.
- **`test_parse_segmenter_json_rejects_overlap`** / **`test_parse_segmenter_json_rejects_oob`** / **`test_parse_segmenter_json_rejects_bad_label`** (unit): overlapping ranges, out-of-bounds ranges, and unknown labels each return `None`. Also a malformed top-level (not a dict, or missing `sections`) returns `None`.
- **`test_map_sections_to_components`** (unit, no LLM): feed hand-crafted `Section`s and a parsed LRC; assert `component_type`, `role` (entry/exit/none), `occurrence_index`, `source == "llm_segmentation"`, and `section_label`/`lyrics_excerpt` populate. Include a single-chorus case asserting the two-row `entry` + `exit` contract, and a verse-before-chorus case asserting the `loop_target` role.
- **`test_validate_chorus_repetition_trims`** (unit, no LLM): feed an over-merged chorus `Section` (its `line_end` extends past the last line whose text actually repeats) plus an LRC with repetition; assert `line_end` is tightened to the last repeating line and confidence is `* 0.90`.
- **`test_validate_chorus_repetition_keeps_nonrepeated`** (unit): a chorus whose text does not repeat elsewhere is kept with confidence `* 0.60` (not dropped).
- **`pytest.mark.llm`-marked live tests** (skipped by default, run with `SOW_LLM_LIVE_TESTS=1`): `test_segment_song_live` runs `segment_song` end-to-end on one fixture LRC and asserts components are returned with `source == "llm_segmentation"` and at least one chorus row.
- **`test_fallback_when_llm_unavailable`** (integration): with `SOW_COMPONENTS_USE_LLM_SEGMENTATION=true` but `SOW_LLM_API_KEY` empty, call `extract_components` and assert it falls through to `lyrics_repetition` (source) and does not crash.
- **`test_fallback_on_json_violation`** (integration): monkeypatch `_parse_segmenter_json` to return `None`; assert `extract_components` with the LLM flag on falls through to `lyrics_repetition`.
- **`test_no_regression_off_path`** (integration): with `SOW_COMPONENTS_USE_LLM_SEGMENTATION=false`, `extract_components` behaves identically to today (same components/source as the current fallback).

## Evaluation

Reuse the existing harness at `ops/analysis-service/tests/test_components_tuning.py` + `eval/components_tuning/` unchanged in structure.

**One extension** to the scorer (`test_components_tuning.py`): it currently partitions by `component_type` only, so IoU is unaffected by the `source` string; only the `n_predicted_choruses` count and role-bonus logic need to tolerate `source == "llm_segmentation"`. Add a helper that accepts both `"llm_segmentation"` and the existing sources wherever `source` feeds verdict/reporting logic, and don't filter out components on `source` when partitioning choruses/verses for IoU.

**Success criteria (gating, decided before implementation):**
- **Pass bar:** grand-total IoU ≥ **0.70** on the 3 fixtures (vs current 0.286).
- **Stretch:** ≥ **0.85** per-song mean on all 3.
- **No regression:** the no-LLM fallback still produces the existing 0.286 baseline, asserted by `test_no_regression_off_path` (runs with `SOW_COMPONENTS_USE_LLM_SEGMENTATION=false`).

**Generalization caveat:** 3 fixtures are too few to claim generalization. A follow-up eval on 10 additional songs (ground-truth labelled the same way as the 3-fixture set) is planned but **out of scope** for this spec.

**A/B testing:** use the `--use-llm-segmentation` job option (Change 5) to run the LLM path against the fallback per song and compare IoU without a global env flip.

**Running the eval:**
```
cd ops/analysis-service && uv run --extra dev pytest tests/test_components_tuning.py -v -s
```

## Rollout order

1. **Change 0** — bump `COMPONENT_SCHEMA_VERSION` to 3 (`cache.py:15`). This forces re-compute of cached components across all songs; existing serializers still emit/read the new (None) fields compatibly.
2. **Change 1** — extend `ComponentInstance` + `_serialize_components` / `_deserialize_components`. `test_components.py` must stay green (fields default `None`).
3. **Change 2** — add the four settings to `config.py`. No behavior change yet (flag off by default).
4. **Change 6** — commit the `segmentation_few_shot.json` scaffold; the operator fills 2–3 real hand-written examples from non-fixture worship songs.
5. **Change 3** — implement `section_segmenter.py` (client, renderer, prompt, parser, mapper, validator, sanity check, entry point).
6. **Change 7** — add `test_section_segmenter.py`; run the unit tests (LLM-free) until green.
7. **Change 4** — wire the LLM branch into `extract_components` (gated + fallback).
8. **Change 5** — add `use_llm_segmentation` to `ComponentAnalysisOptions` and thread it through `queue.py`.
9. **Evaluate:** run `test_components_tuning.py` with the flag on and off; compare IoU against the 0.286 baseline. If ≥ 0.70 on the 3 fixtures, gate in; else iterate on the prompt / few-shot / validator knobs (see Open questions).
10. **Regression:** re-run `test_components.py` (fallback path) and confirm `test_no_regression_off_path` stays green.
11. **Re-compute sanity:** re-run a COMPONENT ANALYSIS job on each of the 3 songs with the flag enabled and `--force`, and verify persisted `components.json` populates the new `section_label` / `lyrics_excerpt` fields.

## Manual verification checklist

1. **Bit-identical fallback with flag off:** with `SOW_COMPONENTS_USE_LLM_SEGMENTATION=false`, `extract_components` returns identical components/source as before Change 4 (verified by `test_no_regression_off_path`). Deployments without a key still produce 0.286.
2. **LLM path produces components:** with `SOW_COMPONENTS_USE_LLM_SEGMENTATION=true` (or `--use-llm-segmentation`) and `SOW_LLM_API_KEY` set, `extract_components` returns components with `source == "llm_segmentation"` and the new fields populated.
3. **Validator trims over-merged chorus boundaries:** `test_validate_chorus_repetition_trims` passes — an over-merged chorus's `line_end` is tightened to the last repeating line, and it does NOT re-expand (no re-introduction of the 13-line block).
4. **Schema version bump triggers re-compute:** after Change 0, a previously-cached `components.json` (v2) is treated as a cache miss and recomputed; the new payload carries `schema_version: 3`.
5. **New fields populate on new compute:** `test_map_sections_to_components` and a live `segment_song` run show `section_label`, `lyrics_excerpt`, `llm_rationale` set on LLM rows.
6. **Existing `test_components.py` stays green:** the fallback path and all existing component tests pass unchanged.
7. **LLM-dependent tests are skipped by default:** `pytest tests/test_section_segmenter.py` passes without `SOW_LLM_LIVE_TESTS=1`; live tests run with the env var set and a key configured.

## Open questions (resolve before/during implementation)

1. **Schema bump consumer compatibility.** Confirm admin-cli, webapp, and render-worker all read `components.json` defensively (they do — new optional fields drift to `None`/ignore, and `schema_version` mismatch triggers re-compute). Resolution happens by bumping now (Change 0) so the new fields reliably populate; verify each consumer tolerates extra keys and the new `component_source` value `llm_segmentation`.
2. **Model choice.** `SOW_LLM_SEGMENTATION_MODEL` may need a stronger model (e.g. `gpt-4o`) for Chinese lyric structure. Operator decides the value; default falls back to `SOW_LLM_MODEL`. If the chosen model's structure accuracy is insufficient, raise the pass bar conversation.
3. **Few-shot source.** Confirmed hand-written from other songs. The operator must actually produce 2–3 examples (Change 6) with real, manually-verified numbered-LRC + sections output, ensuring none are drawn from the 3-fixture set.
4. **Sampling temperature.** Segmentation wants deterministic output → `temperature=0` on both the primary and sanity-check calls. Confirm the provider honours it (OpenRouter routes may vary); if not, keep the parser + validator as the determinism backstop.
5. **Validator over-merge guard.** Ensure `_validate_chorus_repetition` only ever shortens boundaries to lines verified to repeat, and never re-introduces the original 13-line block (add the unit test asserting no expansion).
6. **Sanity-check opt-in threshold.** Enable `SOW_LLM_SEGMENTATION_SANITY_CHECK` (2nd/3rd call) only if the measured IoU < 0.75 on the 3 fixtures; otherwise leave it off to save tokens. Decide the exact threshold before enabling in production.

## Out of scope

- **Implementing** — this document is the implementation plan; coding is a separate step.
- **Re-tuning `ThemeClassifier`** (`classifier.py`) — orthogonal, runs downstream of identification.
- **allin1-sections path** beyond treating it as a weak optional prior (it stays as one input to the fallback ordering; the LLM path does not depend on it).
- **Expanding the 3-song fixture set** — a 10-song follow-up eval is planned but separate.
- **Persisting LLM rationale to R2/DB** beyond the fields already in `components.json` (`section_label`, `lyrics_excerpt`, `llm_rationale`).
- **Weight-tuning the repetition path** — the v1 loop already plateaued; the repetition path is retained only as an unchanged fallback.

## Related specs

- `specs/component-identification-alternatives-v1.md` — the design comparison that framed Design A/B/C; this spec is the detailed implementation of Design C.
- `specs/component-identification-tuning-loop-v1.md` — the v1 weight-tuning effort (scoring formula, line-indexing convention, fixture layout) that this spec reuses for evaluation.
- `specs/fix-component-analysis-llm-persistence-admin-cli-v3-implementation.md` — admin-cli LLM persistence fix (orthogonal consumers of the persisted `components.json`).
- `specs/chorus-component-metadata-impl-plan-v5.md` — v5 component metadata plan (origin of the `ComponentAnalysisOptions` and the fields extended in Change 1).
