---
# Implementation Plan: Decouple --compute-all-fields from --all-components + LLM Lyric Transparency (v1)

> **Date:** 2026-08-13
> **Branch:** TBD
> **Spec ID:** `decouple-compute-all-fields-and-llm-lyric-transparency-v1`
> **Supersedes (partially):** `reduce-component-analysis-llm-calls-v1.md` §1.7 (the `--compute-all-fields` implies `--all-components` decision)

---

## Problem

The predecessor spec `reduce-component-analysis-llm-calls-v1.md` is **fully implemented** across
all three layers (analysis service, queue orchestration, admin CLI). Verification:

- `ComponentAnalysisOptions.all_components: bool = False` — `models.py:96`
- `classify_components(..., all_components=False)` with selective filtering + lyric-hash dedup — `classifier.py:220-333`
- `extract_components(..., all_components=False)` with essential-only feature gating — `components.py:1421`
- `--all-components` Typer flag — `audio.py:2236-2243`
- `--compute-all-fields` shortcut sets `all_components=True` — `audio.py:2279-2284`

However, production logs show that LLM is **still invoked for ALL components** instead of only
the essential (entry/exit/loop_target) ones:

```
LLM classification: 5 unique lyric groups (deduped from 5 candidates)
LLM classification: starting component 1/5 (occurrence=1, type=chorus)
LLM classification: starting component 2/5 (occurrence=2, type=chorus)
LLM classification: starting component 3/5 (occurrence=3, type=chorus)
LLM classification: starting component 4/5 (occurrence=4, type=chorus)
LLM classification: starting component 5/5 (occurrence=1, type=verse)
```

### Root Cause

The job was submitted via `sow-admin audio components <id> --compute-all-fields`. Per v1 §1.7,
the `--compute-all-fields` shortcut **sets `all_components=True`** (`audio.py:2279-2284`):

```python
if compute_all_fields:
    snap_to_downbeat = True
    energy_roles = True
    classify_theme = True
    classify_posture = True
    all_components = True   # ← this bypasses essential-only filtering
```

With `all_components=True`, the `classify_components()` candidate-selection loop
(`classifier.py:255-259`) treats every component as a candidate (`all_components or _is_essential(comp)`),
so 0 components are skipped and all 5 get LLM calls.

### Secondary Observation — Dedup Not Collapsing Choruses

The log shows `5 unique lyric groups (deduped from 5 candidates)`. If the 4 chorus occurrences
shared identical lyrics, lyric-hash dedup should collapse them into **1 representative** LLM call
(1 verse + 1 chorus = 2 unique groups). The fact that all 5 are unique means one of:

1. The choruses genuinely have different lyrics (reprise variations / ad-libs) — dedup is
   working correctly but there is nothing to collapse.
2. The LRC time-range lyric extraction (`_extract_lyrics_for_component`,
   `classifier.py:105-133`) is misaligned with the allin1 section boundaries — each chorus
   time-window captures a different subset of lyric lines (verse/bridge bleed-in), producing
   different normalized hashes even when the actual chorus lyrics are identical.

**Currently there is no log transparency on what lyrics each candidate was extracted and what
hash it received**, making it impossible to distinguish (1) from (2) without code changes.

---

## Goal

1. **Decouple `--compute-all-fields` from `--all-components`.** The essential-only default
   (`all_components=False`) should always apply unless `--all-components` is passed
   explicitly. `--compute-all-fields` becomes a shortcut for the four feature/LLM flags
   only (snap-to-downbeat, energy-roles, classify-theme, classify-posture).
2. **Add LLM lyric transparency to logs.** When classifying each representative, log the
   `lyric_hash` and a truncated preview of the extracted lyric text, so operators can
   diagnose whether dedup should be collapsing repetitions and whether boundary misalignment
   is causing lyric-extraction drift.

---

## Design Decisions (from clarifying questions)

| Decision | Choice |
|---|---|
| `--compute-all-fields` scope | Enables snap-to-downbeat + energy-roles + classify-theme + classify-posture. **Does NOT** set `all_components=True`. |
| `--all-components` opt-in | Still available as an independent explicit flag for backfill/debugging. |
| Lyric log level | INFO (same level as existing per-component start/completed lines). Visible by default — no need for DEBUG. |
| Lyric preview length | Truncate to ~60 chars with ellipsis. Full lyrics remain in the LLM prompt (DEBUG diagnostics already log token counts). |
| `lyric_hash` in logs | Include the 16-char hex digest (or `EMPTY` sentinel) in the "starting" log line. Lets operators spot identical hashes that should have deduped. |

---

## Phase 1: Decouple `--compute-all-fields` from `all_components`

**Complexity:** S

### 1.1 Admin CLI — remove `all_components = True` from the shortcut block

**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py:2278-2284`

Remove the `all_components = True` line from the `if compute_all_fields:` block:

```python
    # Flag override: --compute-all-fields enables advanced feature flags.
    # NOTE: does NOT set all_components — essential-only filtering still
    # applies by default. Pass --all-components explicitly to backfill all.
    if compute_all_fields:
        snap_to_downbeat = True
        energy_roles = True
        classify_theme = True
        classify_posture = True
```

### 1.2 Admin CLI — update docstrings

**File:** `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`

Update the `--compute-all-fields` Typer option help text (`audio.py:2222-2227`):

```python
    compute_all_fields: bool = typer.Option(
        False,
        "--compute-all-fields",
        help="Shortcut: enable snap-to-downbeat, energy-roles, classify-theme, "
        "and classify-posture. Does NOT imply --all-components.",
    ),
```

Update the command docstring (`audio.py:2257-2271`) — remove the sentence
"`--compute-all-fields` also implies `--all-components`." and the line listing
`all-components` in the shortcut description. Replace with:

```
Use --compute-all-fields as a shortcut to enable snap-to-downbeat,
energy-roles, classify-theme, and classify-posture at once.
--use-stems is not included in the shortcut but can be combined with it.
--compute-all-fields does NOT imply --all-components; essential-only
filtering (entry/exit/loop_target/entry_exit) still applies. Pass
--all-components explicitly to populate all components (backfill/debug).
```

### 1.3 No changes needed to the analysis service or models

The `ComponentAnalysisOptions.all_components` default (`False`) and the
classifier/components worker logic remain unchanged. The decoupling is purely
a client-side (admin CLI) behavior change.

---

## Phase 2: LLM Lyric Transparency in Logs

**Complexity:** S

### 2.1 Classifier — add `_truncate_lyrics` helper

**File:** `ops/analysis-service/src/sow_analysis/workers/classifier.py`

Add a module-level helper near `_lyric_hash` (after `classifier.py:150`):

```python
def _truncate_lyrics(lyrics_lines: Optional[list[str]], max_len: int = 60) -> str:
    """Return a truncated single-line preview of lyric lines for logging.

    Joins lines with a space, truncates to ``max_len`` chars with an
    ellipsis. Returns ``"<empty>"`` for None/empty input.
    """
    if not lyrics_lines:
        return "<empty>"
    text = " ".join(line.strip() for line in lyrics_lines if line.strip())
    if not text:
        return "<empty>"
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text
```

### 2.2 Classifier — thread `lyric_hash` into `_classify_component_with_logging`

**File:** `ops/analysis-service/src/sow_analysis/workers/classifier.py:298-300, 335-360`

In `classify_components`, pass the group hash `h` to `_classify_component_with_logging`:

```python
        rep_tasks.append(
            self._classify_component_with_logging(
                rep_i, total, rep_comp, rep_lyrics, h
            )
        )
```

Modify `_classify_component_with_logging` signature and the "starting" log line to include
`lyric_hash` and `lyrics` preview:

```python
    async def _classify_component_with_logging(
        self,
        idx: int,
        total: int,
        component: ComponentInstance,
        lyrics_lines: Optional[list[str]],
        lyric_hash: str = "",
    ) -> None:
        """Classify one component with start/completed/failed progress logging."""
        lyrics_preview = _truncate_lyrics(lyrics_lines)
        comp_label = (
            f"component {idx}/{total} (occurrence={component.occurrence_index}, "
            f"type={component.component_type}, lyric_hash={lyric_hash}, "
            f"lyrics={lyrics_preview})"
        )
        logger.info(f"LLM classification: starting {comp_label}")
        start = time.time()
        try:
            await self.classify_component(component, lyrics_lines)
            elapsed = time.time() - start
            logger.info(
                f"LLM classification: completed {comp_label} ({elapsed:.2f}s, "
                f"theme={component.theme}, posture={component.vocal_posture})"
            )
        except Exception as e:
            elapsed = time.time() - start
            logger.warning(
                f"LLM classification: failed {comp_label} ({elapsed:.2f}s): {e}"
            )
```

The "completed" line reuses the same `comp_label` (which now carries the hash + preview),
so the full lifecycle of each representative includes the lyric context. Duplicates inherit
their result via the existing copy block (`classifier.py:314-331`); the existing "dedup hit"
log already includes the `lyric_hash`, so no change is needed there.

### 2.3 No changes to dedup logic

The lyric-hash grouping and representative selection logic
(`classifier.py:275-305`) is unchanged. The transparency logs merely surface
information that was already computed internally. If operators discover that
choruses with identical lyrics are producing different hashes (boundary
misalignment), a follow-up spec can address LRC/component-boundary alignment —
that is out of scope here.

---

## Phase 3: Tests

**Complexity:** M

### 3.1 Analysis Service — `tests/test_classifier.py`

Update the existing per-component logging test (`test_classify_components_logs_per_component`,
`test_classifier.py:63-92`) to assert the new `lyric_hash` and `lyrics` fields appear in
the "starting" log line:

```python
    assert any("lyric_hash=" in m for m in messages)
    assert any("lyrics=" in m for m in messages)
```

Add a new test for the `_truncate_lyrics` helper:

```python
def test_truncate_lyrics():
    from sow_analysis.workers.classifier import _truncate_lyrics

    assert _truncate_lyrics(None) == "<empty>"
    assert _truncate_lyrics([]) == "<empty>"
    assert _truncate_lyrics([""]) == "<empty>"
    # Short text: no truncation.
    assert _truncate_lyrics(["讚美主"]) == "讚美主"
    # Long text: truncated with ellipsis.
    long = "X" * 100
    result = _truncate_lyrics([long])
    assert result.endswith("...")
    assert len(result) == 60 + 3  # 60 chars + "..."
    # Multiple lines joined with spaces.
    assert _truncate_lyrics(["讚美", "主"]) == "讚美 主"
```

Add a test that the lyric_hash appears in the "completed" line too:

```python
def test_classify_components_logs_include_lyric_hash(classifier, caplog):
    """The completed log line includes lyric_hash for traceability."""
    caplog.set_level(logging.INFO, logger="sow_analysis.workers.classifier")
    components = [_make_component(1, "chorus", start=0.0, end=5.0, role="entry")]
    lrc_content = "[00:00.00]讚美主\n"

    async def fake_call(sync_fn, *, description, loop=None):
        return _parsed_result()

    with patch("sow_analysis.workers.classifier.call_llm_with_retry", new=fake_call):
        asyncio.run(
            classifier.classify_components(
                components, lrc_content=lrc_content, all_components=False
            )
        )

    messages = [r.message for r in caplog.records]
    # Both starting and completed lines must carry lyric_hash.
    assert any("lyric_hash=" in m and "completed" in m for m in messages)
```

### 3.2 Admin CLI — `--compute-all-fields` no longer implies `--all-components`

There is currently no admin CLI command-level test for `components_recording` (grep
confirms no test references `compute_all_fields` or `components_recording` in
`ops/admin-cli/tests/`). Add a lightweight CliRunner test in a new or existing test file
under `ops/admin-cli/tests/`:

```python
def test_compute_all_fields_does_not_set_all_components():
    """--compute-all-fields must NOT set all_components in the payload options."""
    captured = {}

    class FakeClient:
        def submit_component_analysis(self, **kwargs):
            captured.update(kwargs)
            return MagicMock(job_id="test-job")

        def get_cached_component_result(self, *a, **kw):
            return None

    with patch(
        "stream_of_worship.admin.commands.audio.AnalysisClient",
        return_value=FakeClient(),
    ), patch("stream_of_worship.admin.commands.audio._submit_component_analysis_job") as mock_submit:
        # The flag-override block runs before _submit_component_analysis_job,
        # so we assert via the forwarded all_components kwarg.
        from stream_of_worship.admin.commands.audio import components_recording
        # ... invoke via CliRunner with --compute-all-fields, mock DB + R2 ...
        # Assert: mock_submit was called with all_components=False
        _, kwargs = mock_submit.call_args
        assert kwargs["all_components"] is False
```

> **Note:** The exact mocking surface depends on the existing admin CLI test harness.
> If a `components_recording` CliRunner harness already exists, adapt to it. If not,
> this test can be simplified to patch `_submit_component_analysis_job` and assert the
> `all_components` kwarg is `False` when `--compute-all-fields` is passed (without other
> `--all-components`).

### 3.3 Existing tests that must still pass

The following existing tests assert `all_components` behavior and are **unaffected** by this
change (they pass `all_components` explicitly, not via `--compute-all-fields`):

- `test_classify_components_skips_non_essential` — `test_classifier.py:95`
- `test_classify_components_all_components_flag` — `test_classifier.py:147`
- `test_classify_components_lyric_dedup` — `test_classifier.py:188`
- `test_cached_components_have_llm_fields_all_components_mode` — `test_component_cache_validation.py:43`
- `test_submit_component_analysis_with_all_components` — `integration/test_api.py:296`
- `test_submit_component_analysis_all_components_defaults_false` — `integration/test_api.py:337`

---

## Phase 4: Documentation

**Complexity:** S

- Update the `components_recording` command docstring (covered in 1.2).
- Add a short addendum note to this spec referencing the v1 spec §1.7 reversal.
- No `AGENTS.md` change needed (test commands unchanged).

---

## Risk & Rollback

### Risks

- **Existing backfill workflows broken:** Any script/alias that relied on
  `--compute-all-fields` to populate ALL components will now get essential-only. This is
  the intended behavior change. Users who need all components must add `--all-components`
  explicitly (combine: `--compute-all-fields --all-components`).
- **Log verbosity increase:** The "starting" and "completed" log lines now include a
  ~60-char lyric preview + 16-char hash. This is a modest increase (one line per
  representative LLM call, which is already a small number with essential-only filtering).

### Rollback

To restore the v1 coupling, re-add `all_components = True` to the `if compute_all_fields:`
block in `audio.py`. To disable lyric transparency, revert the `_classify_component_with_logging`
signature change. No DB migration or cache invalidation required.

---

## Acceptance Criteria

- [ ] `sow-admin audio components <id> --compute-all-fields` (without `--all-components`)
      classifies only essential components (entry, exit, loop_target, entry_exit). Log shows
      "N skipped (essential-only)" for non-essential rows.
- [ ] `sow-admin audio components <id> --compute-all-fields --all-components` restores the
      full-populate behavior (all components classified).
- [ ] The "LLM classification: starting component X/Y" log line includes
      `lyric_hash=<16hex>` and `lyrics=<preview>`.
- [ ] The "LLM classification: completed component X/Y" log line includes the same
      `lyric_hash` and `lyrics` preview for traceability.
- [ ] When two chorus occurrences have identical extracted lyrics, they share the same
      `lyric_hash` in logs (operator can confirm dedup correctness).
- [ ] `test_truncate_lyrics` passes.
- [ ] Updated `test_classify_components_logs_per_component` passes with lyric assertions.
- [ ] Admin CLI test asserts `--compute-all-fields` forwards `all_components=False`.
- [ ] All existing `all_components` tests still pass (they pass the flag explicitly).

---

## Out of Scope

- **LRC / component-boundary alignment fix:** If the lyric transparency logs reveal that
  identical-lyric choruses produce different hashes due to boundary misalignment, a
  follow-up spec should address snapping `_extract_lyrics_for_component` to structural
  boundaries or expanding the time window. This spec only adds observability.
- **Persisting the `lyric_hash` in `components.json`:** Currently the hash is computed
  transiently during classification. Storing it in the persisted component rows would
  enable cross-run dedup diagnostics but is not needed for this change.
- **Renaming or removing `--all-components`:** The flag remains as an explicit opt-in.

---

## Addendum (2026-08-13, implementation)

This spec **reverses** `reduce-component-analysis-llm-calls-v1.md` §1.7, which had decided
that `--compute-all-fields` implies `--all-components`. That coupling caused production
jobs submitted with `--compute-all-fields` to invoke the LLM for ALL components instead of
only the essential (entry/exit/loop_target/entry_exit) ones.

Implemented:

- `--compute-all-fields` now enables only snap-to-downbeat, energy-roles, classify-theme,
  and classify-posture. It does **NOT** set `all_components`. Essential-only filtering
  applies by default; pass `--all-components` explicitly to backfill all.
- The classifier's "starting"/"completed" log lines now include `lyric_hash` (16-hex or
  `EMPTY`) and a ~60-char `lyrics` preview via the new `_truncate_lyrics()` helper, giving
  operators visibility into dedup correctness and boundary-misalignment drift.

Rollback: re-add `all_components = True` to the `if compute_all_fields:` block in
`audio.py` to restore the v1 coupling; revert the `_classify_component_with_logging`
signature change to disable lyric transparency. No DB migration or cache invalidation
required.

---

## File-by-file change summary

| File | Change |
|---|---|
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | Remove `all_components = True` from `--compute-all-fields` block (2284); update option help (2222-2227) + command docstring (2257-2271) |
| `ops/analysis-service/src/sow_analysis/workers/classifier.py` | Add `_truncate_lyrics()` helper (after 150); add `lyric_hash` param to `_classify_component_with_logging` (335); include `lyric_hash` + `lyrics` preview in start/completed log lines (347, 353); pass `h` from `classify_components` rep_tasks (298-300) |
| `ops/analysis-service/tests/test_classifier.py` | Update `test_classify_components_logs_per_component` to assert `lyric_hash` + `lyrics` in logs; add `test_truncate_lyrics`; add `test_classify_components_logs_include_lyric_hash` |
| `ops/admin-cli/tests/` (new or existing test file) | Add test asserting `--compute-all-fields` forwards `all_components=False` to `_submit_component_analysis_job` |
