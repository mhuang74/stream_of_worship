# Songset Constructor: Post-Run Remediation Plan

## Overview

This document catalogs issues discovered during a live execution of the Songset Constructor skill (Sin & Hope, 4 songs, 3 proposals) and prescribes concrete fixes. Issues are grouped by priority.

---

## P1: Bugs (caused actual failures)

### 1. Preflight script: PYTHONPATH not resolved

**Files:** `.agents/skills/songset-constructor/scripts/preflight.sh:58-88`

**Problem:** The `uv run` Python snippet at line 60 uses `'$ADMIN_CLI/src'` inside a double-quoted `-c` string. In bash, single quotes inside double quotes are literal characters — the `$ADMIN_CLI` variable is not expanded. The Python `sys.path.insert` receives the literal string `$ADMIN_CLI/src` instead of the resolved path, causing `ModuleNotFoundError: No module named 'stream_of_worship'`.

Additionally, `2>/dev/null` on line 88 suppresses all stderr, making failures impossible to diagnose.

**Fix:**
```bash
# Before (line 58-88):
DB_CHECK=$(uv run --project "$ADMIN_CLI" --extra admin python -c "
import sys
sys.path.insert(0, '$ADMIN_CLI/src')
...
" 2>/dev/null)

# After:
DB_CHECK=$(uv run --project "$ADMIN_CLI" --extra admin python -c "
import sys
sys.path.insert(0, '"$ADMIN_CLI/src"')
...
" 2>&1)
```

Or better, use a standalone Python script instead of inline `-c` to avoid shell quoting entirely.

**Verification:** Run `bash scripts/preflight.sh` — all checks should pass with `[OK]`.

---

### 2. All proposals show "Rank 0" in report

**Files:**
- `scripts/score_songset.py` (calls `proposal_from_draft` which defaults `rank=0`)
- `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/proposals.py:76-82`

**Problem:** When scoring individual proposals via `score_songset.py`, the `SongsetProposal.rank` field defaults to 0 because `rank_proposals()` is never called. The report writer at `artifacts/writer.py:364` renders `## Rank {proposal.rank}` faithfully, so all proposals display "Rank 0".

**Fix (option A — in score_songset.py):**
```python
# Accept optional --rank argument
parser.add_argument("--rank", type=int, default=0)
# Pass to proposal construction
proposal = proposal_from_draft(draft, pool, placeholder, llm_origin=True)
proposal.rank = args.rank
```

**Fix (option B — in writer.py):**
```python
# Auto-assign rank by presentation order when all are 0
if all(p.rank == 0 for p in proposals):
    for i, p in enumerate(proposals, start=1):
        p.rank = i
```

Option B is safer as it doesn't break existing callers.

---

## P2: Confusing Behaviors

### 3. H4 `gap_beats > 4` threshold is undocumented

**Files:**
- `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/hard_constraints.py:80`
- `.agents/skills/songset-constructor/SKILL.md` (H4 rule description)

**Problem:** The validation logic at `hard_constraints.py:80`:
```python
allowed = h4_limit if (right.crossfade_duration_seconds > 0 or right.gap_beats > 4) else min(25, h4_limit)
```

The default `gap_beats` is 2.0, which is NOT `> 4`. So the effective default BPM limit is `min(25, 35)` = **25 BPM**, not 35. The SKILL.md says "Adjacent BPM delta ≤ 35 (25 without crossfade/gap, 40 if relaxed)" — this is technically correct but misleading because a 2-beat gap is treated as "no gap."

**Fix (option A — lower threshold):**
```python
allowed = h4_limit if (right.crossfade_duration_seconds > 0 or right.gap_beats > 0) else min(25, h4_limit)
```
This makes the default 2-beat gap qualify for the 35 BPM limit.

**Fix (option B — document):**
Update SKILL.md H4 rule to read:
> Adjacent BPM delta must stay ≤ 35 (25 without crossfade or gap ≥ 4 beats; 40 if relaxed).

---

### 4. Crossfade settings hidden in transition display

**File:** `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/artifacts/writer.py:369`

**Problem:** The transition column renders only:
```python
transition = f"shift {item.key_shift_semitones}, gap {item.gap_beats:g} beats"
```

When `crossfade_enabled=True` and `crossfade_duration_seconds > 0`, this information is invisible. Users who set crossfade to satisfy H4 have no way to verify it in the report.

**Fix:**
```python
parts = [f"shift {item.key_shift_semitones}", f"gap {item.gap_beats:g} beats"]
if item.crossfade_duration_seconds > 0:
    parts.append(f"crossfade {item.crossfade_duration_seconds:g}s")
transition = ", ".join(parts)
```

---

### 5. "Phase gap" bottleneck noise for intentional template skips

**Files:**
- `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/artifacts/writer.py:262-265`
- `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/fitness.py:12-15` (template definitions)

**Problem:** The `_bottleneck_lines` function flags any phase absent from all proposals as a gap:
```python
for phase in range(1, 6):
    if phase not in metrics["unique_phases"]:
        label = PHASE_NAMES.get(phase, "unknown")
        lines.append(f"Phase gap: Phase {phase} ({label}) absent from all top-k songsets.")
```

For a 4-song template `(1, 3, 4, 5)`, phase 2 is intentionally absent. The report flags this as a bottleneck, which is noise.

**Fix:**
```python
from stream_of_worship.admin.songset_constructor.rules.fitness import _THEME_TEMPLATES

template = _THEME_TEMPLATES.get(config.count, (1, 2, 3, 4, 5))
for phase in range(1, 6):
    if phase not in metrics["unique_phases"]:
        if phase in template:
            label = PHASE_NAMES.get(phase, "unknown")
            lines.append(f"Phase gap: Phase {phase} ({label}) absent from all top-k songsets.")
        # else: intentionally absent from this template — skip
```

Requires passing `config` (or `config.count`) to `_bottleneck_lines`.

---

## P3: Missing Features

### 6. No agent-authored summary in report

**Files:**
- `scripts/write_report.py:59` (accepts `summary` field)
- `.agents/skills/songset-constructor/SKILL.md` (Step 10 — Write Report)

**Problem:** The `write_report.py` script accepts an optional `summary` field and renders an "Agent Summary" section, but the skill workflow never populates it. The report lacks an executive summary.

**Fix:** After ranking proposals (Step 9), have the LLM generate a 3-5 sentence summary covering:
- Number of proposals generated
- Top proposal score and sequence
- Key findings (diversity, constraint relaxations, concerns)

Pass this as the `summary` field in the JSON payload to `write_report.py`.

---

### 7. Empty `line_theme_scores_raw` weakens theme fusion

**Files:**
- `scripts/enrich_pool.py:83`
- `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/phases.py:33-63`

**Problem:** All songs have `line_theme_scores_raw: {}` (empty dict). The `fuse_themes` function allocates 15% weight to line embeddings (or 5% when title and lyrics agree), but with empty input this weight contributes nothing. The effective fusion sources are reduced from 4 to 3.

**Fix (option A — populate line embeddings):**
Run line-level theme embedding on the catalog. This requires:
1. Splitting lyrics into lines
2. Computing embedding for each line
3. Aggregating line embeddings into per-theme scores
4. Storing in `line_theme_scores_raw`

**Fix (option B — redistribute weight):**
In `fuse_themes`, detect when `line_emb` is empty and redistribute its weight across the remaining sources:
```python
if not any(v > 0 for v in line_emb.values()):
    # Redistribute line_emb weight across title, lyrics, song_emb
    weighted_sources = [
        (w + (line_weight * w / total_active_weight), source)
        for (w, source) in weighted_sources
        if source is not line_emb
    ]
```

---

## P4: Robustness Recommendations

### 8. `relax_h1` defaults to `True`

**File:** `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/config.py:36`

**Problem:** `relax_h1: bool = True` means the strict phase-1 opener requirement is always waived by default. The report shows "Flags: relax_h1=true" but this is invisible to users who don't read the flags line.

**Recommendation:** Either:
- (a) Change default to `False` and require explicit opt-in, or
- (b) Document prominently in SKILL.md that H1 is relaxed by default and explain why (phase inference may not be reliable enough for strict enforcement).

---

### 9. Preflight stderr diagnostics

**File:** `.agents/skills/songset-constructor/scripts/preflight.sh:88`

**Problem:** `2>/dev/null` hides all stderr from the `uv run` command. When the check fails, there's no way to see what went wrong (missing dependency, connection timeout, etc.).

**Recommendation:** Remove `2>/dev/null` or replace with:
```bash
" 2>&1)  # capture stderr too
# or
" 2>/tmp/sow_preflight_err.log)
```

---

### 10. Semantic search and lyrics inspection underused

**Files:**
- `.agents/skills/songset-constructor/scripts/semantic_search.py`
- `.agents/skills/songset-constructor/scripts/get_lyrics.py`
- `.agents/skills/songset-constructor/SKILL.md` (Step 5)

**Problem:** The skill provides `semantic_search.py` and `get_lyrics.py` as optional tools during planning, but the workflow doesn't define *when* to use them. In the Sin & Hope run, they were never invoked.

**Recommendation:** Add conditional workflow steps:
- If pool diversity for a given template slot is low (fewer than 3 candidates), run `semantic_search.py` with a theme query to find more candidates.
- If a transition has CFD > 2, run `get_lyrics.py` on both songs to inspect lyrical endings/startings for natural transition points.

---

## Implementation Order

| Order | Item | Effort | Risk | Impact |
|-------|------|--------|------|--------|
| 1 | P1.1 — Fix preflight quoting | 15 min | Low | Unblocks preflight checks |
| 2 | P1.2 — Fix Rank 0 display | 30 min | Low | Correct report output |
| 3 | P2.3 — Fix H4 gap threshold or docs | 15 min | Low | Correct constraint behavior |
| 4 | P2.4 — Show crossfade in report | 15 min | Low | Informative report |
| 5 | P2.5 — Filter phase gap noise | 30 min | Low | Cleaner report |
| 6 | P3.6 — Add agent summary | 30 min | Low | Better report |
| 7 | P3.7 — Handle empty line_emb | 1 hr | Medium | Better theme fusion |
| 8 | P4.8-P4.10 — Recommendations | 1 hr | Low | Robustness |
