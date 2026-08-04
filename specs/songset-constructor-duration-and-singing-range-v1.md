# Songset Constructor — Duration Hard-Limit & Singing-Range Filter v1

**Date:** 2026-08-03
**Skill location:** `lab/skills/songset-constructor/` (canonical; sole target of this spec)
**Spec type:** Code enhancement + documentation
**Audience:** Fresh implementing agent — this spec is self-contained.

## Goal

Enhance the songset-constructor skill with two new constraints that must be enforced **early** in the planning pipeline, so the agent does not draft proposals that fail later at the `sow-admin songset create` step:

1. **Duration hard-limit (H9):** Each songset must respect `SONGSET_MAX_SONGS = 5` and `SONGSET_MAX_DURATION_SECONDS = 1500` (25 min). Today these are enforced only at `songset create` time (`ops/admin-cli/src/stream_of_worship/admin/commands/songset.py:753,773`); the agent learns of the violation only after drafting, scoring, and writing a report.
2. **Singing-range filter:** Ask the user (plain-English interview) about the worship leader's vocal range, then constrain candidate songs' musical keys to that range. Out-of-range songs are **not** dropped — they receive a soft score penalty and a recommended key shift, leaving the planner free to use them when no in-range alternative fits.

## Scope Clarifications

- **Canonical path:** `lab/skills/songset-constructor/` is the only skill location modified by this spec. The `.agents/skills/songset-constructor/` mirror is out of scope (externally managed).
- **Enforcement layers (per user decision):** Planner + validator only. No pool-level pre-filter — the LLM planner tracks running duration while drafting, and `score_songset.py` adds a formal Hard Constraint H9. Pool-level filtering risks planning into a corner when long songs are the only phase-appropriate candidates.
- **Out-of-range songs (per user decision):** Soft penalty, allow original. Songs whose tonic PC falls outside the leader's comfortable range are kept in the pool; the scorer subtracts a penalty proportional to the range distance, and the planner is encouraged (but not forced) to apply a `recommended_key_shift_for_range` if one fits.
- **Duration source (per user decision):** Extend `SongCandidate` with a `duration_seconds` field, populated by `fetch_catalog_pool` from the DB. No new lookup script.
- **Singing-range capture (per user decision):** Plain-English interview via the Question tool. The skill maps voice-type labels (e.g., "normal male", "low male", "high male", "normal female", "low female", "high female") to a comfortable tonic-PC set. Free-form range strings (e.g., "A2-G4") override the table.
- **Step 12 (persist songset):** Remains optional. The agent only invokes `sow-admin songset create` when the user explicitly requests persistence.
- **Pipeline robustness / pipefail:** Out of scope.
- **Webapp / Android / Render-worker:** Out of scope. The 25-min cap already exists in `constants.py` and is enforced by `songset create`; this spec only adds early enforcement in the constructor skill.

## Background — Root Cause Analysis

### Issue A: Duration cap discovered too late

`SongCandidate` (`ops/admin-cli/src/stream_of_worship/admin/songset_constructor/models.py:8-30`) has **no `duration_seconds` field**. The catalog pool returned by `fetch_pool.py` carries `tempo_bpm`, `musical_key`, `musical_mode`, `key_confidence`, `loudness_db`, `lyrics_raw`, theme scores, phase, etc. — but not duration.

The 25-min cap is defined in `ops/admin-cli/src/stream_of_worship/admin/constants.py:7-8`:

```python
SONGSET_MAX_SONGS = 5
SONGSET_MAX_DURATION_SECONDS = 1500  # 25 minutes
```

These constants are enforced only inside `sow-admin songset create` (`ops/admin-cli/src/stream_of_worship/admin/commands/songset.py:753` for song count, `:773` for total duration). The constructor skill's planner (Step 5 in `SKILL.md`) has no visibility into per-song duration, so it routinely drafts 5-song sets whose total duration exceeds 25 min. The agent only discovers the violation when `songset create --dry-run` fails, by which point the report has already been written.

**Relevant files:**
- `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/models.py:8-30` — `SongCandidate` model (no duration field)
- `ops/admin-cli/src/stream_of_worship/admin/constants.py:7-8` — `SONGSET_MAX_SONGS`, `SONGSET_MAX_DURATION_SECONDS`
- `ops/admin-cli/src/stream_of_worship/admin/commands/songset.py:753,773` — enforcement at `songset create`
- `lab/skills/songset-constructor/SKILL.md:140-162` — Step 5 planning guidelines (no duration tracking)
- `lab/skills/songset-constructor/SKILL.md:140-153` — H0-H8 constraint table (no H9)

### Issue B: No singing-range awareness

The constructor skill's harmony rules (`ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/harmony.py`) already model key compatibility via circle-of-fifths distance (`cfd()`, line 54), pitch-class conversion (`pitch_class()`, line 32), and transposition (`transpose_note()`, line 37; `suggest_key_shift()`, line 75). The data model carries `musical_key` (e.g., `'C'`, `'Eb'`), `musical_mode` (`'maj'`/`'min'`), and `key_confidence` (0-1).

However, **nothing in the pipeline knows whether a song's key is singable by the worship leader**. A 5-song set drafted purely on thematic/tempo/harmonic grounds may land in keys that are too high for a bass-baritone leader or too low for a soprano. The agent has no mechanism to ask the user about their range, nor to penalize or transpose songs that fall outside it.

**Relevant files:**
- `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/harmony.py:7-14` — `NOTE_TO_PC`, `PC_TO_NOTE`, `FIFTH_ORDER`, `FIFTH_INDEX`
- `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/harmony.py:32-34` — `pitch_class()`
- `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/harmony.py:37-38` — `transpose_note()`
- `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/harmony.py:75-90` — `suggest_key_shift()`
- `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/models.py:18-20` — `musical_key`, `musical_mode`, `key_confidence`
- `lab/skills/songset-constructor/SKILL.md:131-170` — Step 5 planning guidelines (no range awareness)

## Design

### Part 1: Duration Hard-Limit (H9)

#### 1.1 Extend `SongCandidate` with `duration_seconds`

**File:** `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/models.py`

Add a new optional field to `SongCandidate` after `loudness_db` (line 21):

```python
class SongCandidate(BaseModel):
    song_id: str
    title: str
    title_pinyin: str | None = None
    composer: str | None = None
    lyricist: str | None = None
    album_name: str | None = None
    album_series: str | None = None
    recording_hash_prefix: str
    tempo_bpm: float | None = None
    musical_key: str | None = None
    musical_mode: str | None = None
    key_confidence: float | None = None
    loudness_db: float | None = None
    duration_seconds: float | None = None  # NEW — from latest-active recording
    lyrics_raw: str | None = None
    # ... rest unchanged
```

**Rationale:** Optional with default `None` so existing cached pool JSON files (which lack the field) still validate via `SongCandidate.model_validate(...)`. The stale-cache fallback in `fetch_pool.py:152-166` continues to work; songs loaded from old caches simply have `duration_seconds = None`, and the planner treats `None` as "unknown duration — do not block, but warn".

#### 1.2 Populate `duration_seconds` in `fetch_catalog_pool`

**File:** `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/db.py`

The `fetch_catalog_pool()` function builds a SQL query joining `songs` → `recordings` (latest-active-wins). Add `duration_seconds` to the SELECT list, sourced from the recording row.

**Implementation notes:**
- The recording table already stores duration (the `songset create` command reads it at `songset.py:773`). Confirm the exact column name by grepping the recording model / schema before implementing. Likely candidates: `duration_seconds`, `duration`, `recording_duration_seconds`.
- If the column is nullable in the DB, the Python field stays `None` for songs with missing duration — acceptable.
- The cache layer (`cache.py:save_pool` / `try_load_pool`) serializes via `model_dump(mode="json")`, so the new field flows through automatically. Old cache files lacking the field validate to `None`.

**Verification step before implementing:** Read `ops/admin-cli/src/stream_of_worship/admin/commands/songset.py` around line 773 to find the exact DB column name and the join used to fetch the latest-active recording. Mirror that join in `fetch_catalog_pool` so the duration source matches the enforcement source.

#### 1.3 Add Hard Constraint H9 to the validator

**File (logic):** `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/validation.py` (or wherever H0-H8 are implemented — confirm by grepping for `H0` / `violated` before editing)

Add a new check after H8:

```python
# H9 — Total duration must not exceed SONGSET_MAX_DURATION_SECONDS
SONGSET_MAX_DURATION_SECONDS = 1500  # import from constants.py instead of redefining

total_duration = sum(item.duration_seconds or 0.0 for item in proposal.items)
if total_duration > SONGSET_MAX_DURATION_SECONDS:
    violated.append("H9")
    errors.append(
        f"Total duration {total_duration:.0f}s exceeds {SONGSET_MAX_DURATION_SECONDS}s (25 min) limit"
    )
    repair_hints.append(
        "Replace one song with a shorter alternative, or reduce song count (≤4)"
    )
```

**Edge case — `None` durations:** If all items have `duration_seconds = None` (e.g., loaded from a stale cache predating this spec), `total_duration` is 0 and H9 passes vacuously. The planner should still warn the user in the summary that durations were unknown. Do **not** fail H9 on missing data — that would block all runs until the cache is refreshed.

**Edge case — song count:** H0 already enforces the requested count. The 5-song cap is enforced by H0 (when `count ≤ 5` is requested) and by the planner's hard cap at Step 5. No new H-constraint is needed for song count; H9 covers only duration.

#### 1.4 Update `score_songset.py` to surface H9 in output

**File:** `lab/skills/songset-constructor/scripts/score_songset.py`

The script already returns a `ValidationFeedback` with `violated`, `errors`, `repair_hints`. No structural change needed — H9 flows through automatically once the validator adds it. Confirm by reading the script's validation call site and ensuring it invokes the shared validator (not an inline copy of H0-H8).

**If H0-H8 are inlined in `score_songset.py`** (not in a shared `rules/validation.py`): add H9 directly to `score_songset.py` in the same validation block. Prefer extracting to `rules/validation.py` if the implementer has time, but inlining is acceptable for v1.

#### 1.5 Update `ProposalItem` to carry `duration_seconds`

**File:** `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/models.py`

`ProposalItem` (line 62-72) currently has `bpm`, `key`, `mode`, `key_confidence` but not duration. Add:

```python
class ProposalItem(DraftItem):
    song_id: str
    title: str
    album_name: str | None = None
    phase: int
    secondary_phases: list[int] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    bpm: float | None = None
    key: str | None = None
    mode: str | None = None
    key_confidence: float | None = None
    duration_seconds: float | None = None  # NEW
    in_leader_range: bool = False  # NEW — Part 2
    leader_range_distance: int = 0  # NEW — Part 2
    recommended_key_shift_for_range: int = 0  # NEW — Part 2
```

`score_songset.py` must populate `duration_seconds` (and the range fields) when building `ProposalItem` from the pool lookup.

#### 1.6 Update `write_report.py` to render total duration

**File:** `lab/skills/songset-constructor/scripts/write_report.py`

In the per-proposal section, add a "Total duration" line after the song sequence:

```
**Total duration:** 23m 12s (1392s / 1500s limit) ✓
```

If H9 is violated, render in red/warning:

```
**Total duration:** 27m 05s (1625s / 1500s limit) ✗ H9 VIOLATED
```

Also add per-item duration in the song sequence table (new column or inline parenthetical).

---

### Part 2: Singing-Range Filter

#### 2.1 New "Step 0 — Gather User Inputs"

**File:** `lab/skills/songset-constructor/SKILL.md`

Insert a new Step 0 between the Workflow header and Step 1 (Pre-flight Checks). The agent uses the Question tool to interview the user.

**Plain-English voice-type interview:**

The agent asks:

> **Question:** "Tell me about the worship leader's singing range. You can answer in plain English — for example: 'normal male', 'low male', 'high male', 'normal female', 'low female', 'high female', 'alto', 'tenor', 'bass', 'soprano'. If you know your exact comfortable range (e.g., 'A2 to G4'), feel free to provide that instead."

**Options presented to the user (via the Question tool):**
- Normal male (baritone / tenor-baritone)
- Low male (bass)
- High male (tenor)
- Normal female (mezzo)
- Low female (alto)
- High female (soprano)
- (Type your own answer — e.g., "G2 to E4")

**Voice-type → comfortable tonic PC mapping:**

The skill maintains an internal mapping table. Tonic PCs are pitch-class numbers (0-11) per `NOTE_TO_PC` in `harmony.py:7-11`. The "comfortable" set is the set of tonic PCs where the song's tonic (or relative-major tonic for minor-key songs) falls within the leader's singable range.

| Voice label | Approx. vocal range | Comfortable tonic PCs (relative-major) |
|---|---|---|
| normal male | A2–E4 | {0, 1, 2, 3, 4, 5} (C, C#, D, Eb, E, F) |
| low male | E2–B3 | {8, 9, 10, 11, 0, 1} (Ab, A, Bb, B, C, C#) |
| high male | C3–G4 | {2, 3, 4, 5, 6, 7} (D, Eb, E, F, F#, G) |
| normal female | G3–E5 | {0, 2, 3, 4, 5, 7} (C, D, Eb, E, F, G) |
| low female | E3–B4 | {9, 10, 11, 0, 2} (A, Bb, B, C, D) |
| high female | C4–G5 | {2, 4, 5, 7, 9} (D, E, F, G, A) |

> **Note for the implementer:** These PC sets are a first-pass guess based on typical worship-leading ranges. They are intentionally configurable (not hardcoded constants) so they can be tuned later by someone with music ministry experience. Store them as a dict in a new `lab/skills/songset-constructor/scripts/voice_ranges.py` module (or inline in `enrich_pool.py` if simpler).

**Free-form range override:**

If the user types a range like "A2 to G4", the agent parses it:
1. Extract low and high notes (e.g., `A2`, `G4`).
2. Convert to pitch classes: `pitch_class("A") = 9`, `pitch_class("G") = 7`.
3. Compute comfortable tonic PCs: all PCs `p` such that `p` is within a perfect fifth (7 semitones) below the leader's high note AND within a perfect fifth above the leader's low note (circular PC arithmetic). This gives the leader roughly an octave of comfortable tonic range centered on their tessitura.
4. If parsing fails, fall back to the "normal male" default and warn the user.

**Persistence:**

The resolved range is passed to `enrich_pool.py` via a new CLI flag:

```bash
uv run --project ops/admin-cli --extra admin --extra constructor python \
    lab/skills/songset-constructor/scripts/enrich_pool.py \
    --leader-range '{"comfortable_pcs": [0, 1, 2, 3, 4, 5], "label": "normal male"}'
```

Or via two flags for simpler invocation:

```bash
--leader-low-pc 9 --leader-high-pc 4
```

The implementer should pick one form (JSON blob is more flexible; two-int form is simpler). Recommend JSON blob since it can carry the label for reporting.

#### 2.2 Enrichment computes range metadata

**File:** `lab/skills/songset-constructor/scripts/enrich_pool.py`

After the existing enrichment logic (theme fusion, phase inference, seasonal bias), add a range-computation pass. For each `SongCandidate`:

1. Compute the song's relative-major tonic PC:
   ```python
   from stream_of_worship.admin.songset_constructor.rules.harmony import (
       pitch_class, relative_major_pc, transpose_note
   )
   tonic_pc = relative_major_pc(song.musical_key, song.musical_mode)
   ```

2. If `tonic_pc in comfortable_pcs`:
   - `in_leader_range = True`
   - `leader_range_distance = 0`
   - `recommended_key_shift_for_range = 0`

3. Else if `song.key_confidence is not None and song.key_confidence >= 0.6`:
   - Try shifts `[-2, -1, 1, 2]` (skip 0 since it already failed). For each shift, compute `shifted_pc = (tonic_pc + shift) % 12`. Find the smallest `abs(shift)` whose `shifted_pc in comfortable_pcs`.
   - If found: `recommended_key_shift_for_range = shift`, `in_leader_range = True` (after shift), `leader_range_distance = 0`.
   - If no shift in ±2 fits: `in_leader_range = False`, `recommended_key_shift_for_range = 0` (no valid shift), `leader_range_distance = min(circular_pc_distance(tonic_pc, c) for c in comfortable_pcs)`.

4. Else (key confidence < 0.6, cannot transpose per H8):
   - `in_leader_range = False`
   - `recommended_key_shift_for_range = 0`
   - `leader_range_distance = min(circular_pc_distance(tonic_pc, c) for c in comfortable_pcs)`

**Circular PC distance helper:**

```python
def circular_pc_distance(a: int, b: int) -> int:
    d = abs(a - b) % 12
    return min(d, 12 - d)
```

(Or reuse `fifth_distance_on_circle` from `harmony.py:47-51` if the implementer prefers circle-of-fifths distance over chromatic distance. Chromatic is more intuitive for vocal range; recommend chromatic.)

**CLI flag:** `--leader-range` accepts a JSON string `{"comfortable_pcs": [...], "label": "..."}`. If omitted, the range pass is skipped and all songs get `in_leader_range = True` (backwards-compatible default — no penalty applied).

#### 2.3 Soft penalty in `score_songset.py`

**File:** `lab/skills/songset-constructor/scripts/score_songset.py`

After computing the base `total` from `f_theme` (0.40) + `f_tempo` (0.30) + `f_harmony` (0.20) + `f_diversity` (0.10), apply a soft range penalty:

```python
range_penalty = 0.0
for item in proposal.items:
    # Apply the draft's key_shift_semitones to check if the shifted key is in range
    shifted_pc = (item.tonic_pc + item.key_shift_semitones) % 12
    if shifted_pc not in comfortable_pcs:
        # Distance from the closest comfortable PC
        dist = min(circular_pc_distance(shifted_pc, c) for c in comfortable_pcs)
        range_penalty += 0.05 * dist

# Cap the penalty at 0.20 (4 semitones × 0.05)
range_penalty = min(range_penalty, 0.20)

total = max(0.0, base_total - range_penalty)
```

**Design decisions:**
- **Penalty weight:** `0.05` per semitone of distance. A song 2 semitones out of range costs 0.10; 4+ semitones costs the cap of 0.20. This is enough to deprioritize out-of-range songs without making them unscoreable.
- **Cap:** 0.20 per song (4 semitones). Beyond that, the penalty saturates — a song 6 semitones out is not meaningfully worse than 4.
- **Total cap:** The sum is not capped across songs (a 5-song set all out of range could lose up to 1.0). This is intentional — a set where every song is out of range should score near 0. If the implementer prefers a global cap, use 0.30.
- **No new `f_range` component:** The penalty subtracts from `total` directly, preserving the existing 0.40/0.30/0.20/0.10 weights. This avoids reweighting `f_theme`/`f_tempo`/`f_harmony`/`f_diversity` and keeps the ScoreBreakdown schema stable. The report can surface `range_penalty` as a separate field in the proposal metadata.

**Score interpretation table update** (in SKILL.md Step 6):

| Component | Good | Acceptable | Concern |
|---|---|---|---|
| f_theme | ≥ 0.90 | ≥ 0.80 | < 0.80 |
| f_tempo | ≥ 0.70 | ≥ 0.65 | < 0.60 |
| f_harmony | ≥ 0.70 | ≥ 0.50 | < 0.40 |
| f_diversity | 1.00 | 1.00 | < 1.00 |
| range_penalty | 0.00 | ≤ 0.05 | > 0.10 |
| **total** | **≥ 0.80** | **≥ 0.70** | **< 0.65** |

#### 2.4 Planner guidance for range

**File:** `lab/skills/songset-constructor/SKILL.md` (Step 5 — Plan Songsets)

Add to the "Planning guidelines" bullet list:

> - **Singing range:** Prefer songs with `in_leader_range = True`. If a chosen song has `in_leader_range = False` and `recommended_key_shift_for_range != 0`, set the draft item's `key_shift_semitones` to `recommended_key_shift_for_range` (this also satisfies H5/H8 if the shifted CFD ≤ 3). If no in-range song fits a template slot, accept the out-of-range song — the soft penalty will reduce the score but not block the proposal.

#### 2.5 Report rendering for range

**File:** `lab/skills/songset-constructor/scripts/write_report.py`

In the per-proposal section, add a "Singing range" subsection:

```
**Singing range:** normal male (comfortable tonics: C, C#, D, Eb, E, F)

| # | Song | Key | Mode | In range | Applied shift | Range distance |
|---|------|-----|------|----------|---------------|----------------|
| 1 | 我的耶稣 | G | maj | ✓ | 0 | 0 |
| 2 | 感谢神 | Bb | maj | ✓ (shifted) | -2 | 0 |
| 3 | ... | ... | ... | ✗ | 0 | 3 |
```

Also surface `range_penalty` in the score breakdown:

```
**Score:** 0.78 (f_theme 0.85, f_tempo 0.72, f_harmony 0.65, f_diversity 1.00, range_penalty -0.12)
```

---

## SKILL.md Documentation Changes

**File:** `lab/skills/songset-constructor/SKILL.md`

The following edits use `[INSERT]`, `[REPLACE]`, `[APPEND]` markers per the convention established in `specs/songset-constructor-skill-usability-improvements-v3.md`.

### Edit 1: Insert Step 0 before Step 1

`[INSERT]` before the `### Step 1 — Pre-flight Checks` header:

```markdown
### Step 0 — Gather User Inputs

Before pre-flight checks, gather two user inputs that constrain the entire run:

**0a. Singing range (plain-English interview):**

Use the Question tool to ask the user about the worship leader's singing range. Present these options:
- Normal male (baritone / tenor-baritone)
- Low male (bass)
- High male (tenor)
- Normal female (mezzo)
- Low female (alto)
- High female (soprano)
- (Type your own answer — e.g., "G2 to E4")

Map the answer to a comfortable tonic-PC set using the table in `scripts/voice_ranges.py` (or the inline mapping in `enrich_pool.py`). If the user provides a free-form range (e.g., "A2 to G4"), parse the low and high notes, convert to pitch classes, and compute the comfortable set as all PCs within a perfect fifth of the leader's tessitura.

If the user declines or is unsure, default to "normal male" and note the default in the run summary.

**0b. Songset size preference:**

Ask the user how many songs they want (2-5). This is the `count` config field. If the user says "5", remind them that the 25-minute duration cap (H9) may force a smaller set if the chosen songs are long.

**Passing the range to enrichment:**

```bash
uv run --project ops/admin-cli --extra admin --extra constructor python \
    lab/skills/songset-constructor/scripts/enrich_pool.py \
    --leader-range '{"comfortable_pcs": [0, 1, 2, 3, 4, 5], "label": "normal male"}'
```
```

### Edit 2: Update Step 2 to note `duration_seconds`

`[REPLACE]` the last sentence of Step 2's paragraph beginning "This returns a JSON array of raw SongCandidate objects...":

```markdown
This returns a JSON array of raw SongCandidate objects (pre-enrichment). Each song has: `song_id`, `title`, `title_pinyin`, `tempo_bpm`, `musical_key`, `musical_mode`, `key_confidence`, `duration_seconds` (from the latest-active recording), `lyrics_raw`, `song_theme_scores_raw`, `line_theme_scores_raw`, `recording_hash_prefix`, etc.
```

### Edit 3: Update Step 3 to document range fields and `--leader-range` flag

`[APPEND]` to the end of Step 3 (after the "If the pool has 0 valid openers..." paragraph):

```markdown
**Singing-range enrichment (if `--leader-range` provided):**

For each song, the enrichment computes:
- `in_leader_range` (bool): whether the song's relative-major tonic PC falls in the leader's comfortable set (after applying `recommended_key_shift_for_range` if non-zero).
- `leader_range_distance` (int): chromatic distance from the closest comfortable PC (0 if in range).
- `recommended_key_shift_for_range` (int): the smallest ±2 semitone shift that brings the song into range (0 if already in range or if no shift fits).

Songs with `key_confidence < 0.6` cannot be transposed (per H8) and are marked `in_leader_range = False` if their original key is out of range.

**Duration field:**

Each song also carries `duration_seconds` (from the DB via `fetch_pool.py`). Songs loaded from a stale cache predating this field will have `duration_seconds = None` — the planner treats `None` as "unknown duration" and warns the user.
```

### Edit 4: Update Step 5 planning guidelines

`[REPLACE]` the "Planning guidelines" bullet list in Step 5:

```markdown
**Planning guidelines:**
- Select an opener: phase 1 (or 2), tempo ≥ 90 BPM, not a dead-end song
- Select middle songs: phase matches template position, BPM delta ≤ 45 from previous (40 without crossfade), CFD ≤ 3 (or apply key shift if CFD > 3 and key confidence ≥ 0.6)
- Select a closer: phase 4 or 5, tempo ≤ 90 BPM (80 if intimate)
- Ensure phase doesn't drop by more than 1 between adjacent songs (H7)
- **Hard cap: `count ≤ 5`** (enforced by `SONGSET_MAX_SONGS`; exceeding this fails at `songset create` time, not earlier). Never draft a proposal with more than 5 songs.
- **Duration tracking (H9):** Before adding a song to the draft, accumulate `running_duration_seconds` (sum of selected songs' `duration_seconds`). Reject any candidate that would push the running total past `SONGSET_MAX_DURATION_SECONDS` (1500s = 25 min). If `duration_seconds` is `None` for a song, do not block on it, but warn the user in the summary that durations were unknown.
- **Singing range:** Prefer songs with `in_leader_range = True`. If a chosen song has `in_leader_range = False` and `recommended_key_shift_for_range != 0`, set the draft item's `key_shift_semitones` to `recommended_key_shift_for_range` (this also satisfies H5/H8 if the shifted CFD ≤ 3). If no in-range song fits a template slot, accept the out-of-range song — the soft penalty will reduce the score but not block the proposal.
- Maximize theme diversity across the set
- Consider tempo arc: opener should be faster than closer
```

### Edit 5: Add H9 to the constraints table

`[REPLACE]` the H0-H8 constraint table in Step 5:

```markdown
**Hard Constraints (H0-H9) — all must pass:**

| Code | Rule | Default |
|------|------|---------|
| H0 | Correct song count (must match requested count) | — |
| H1 | One phase-1 *primary* opener (primary only, not secondary_phases), middle worship/response, phase 4/5 closer (primary or secondary) | relaxable (opt-in via --relax-h1) |
| H2 | Opener tempo ≥ 90 BPM | 90 (relaxable) |
| H3 | Closer tempo ≤ 90 BPM (80 if intimate) | 90/80 (relaxable) |
| H4 | Adjacent BPM delta ≤ 45 (40 without crossfade; 55 if relaxed) — gap_beats > 0 (any gap) triggers crossfade-tier cap | 45/40 |
| H5 | Circle-of-fifths distance ≤ 3 (4 if relaxed) unless key shift applied | 3 |
| H6 | No duplicate song IDs | — |
| H7 | Phase drops by at most 1 between adjacent songs | — |
| H8 | Songs with key confidence < 0.6 cannot be transposed (key_shift must be 0) | 0.6 |
| H9 | Total song duration ≤ 1500s (25 min) | 1500 (from `SONGSET_MAX_DURATION_SECONDS`) |
```

### Edit 6: Update Step 6 score interpretation table

`[REPLACE]` the score interpretation table in Step 6:

```markdown
**Score interpretation:**

| Component | Good | Acceptable | Concern |
|-----------|------|------------|---------|
| f_theme | ≥ 0.90 | ≥ 0.80 | < 0.80 (phase mismatch) |
| f_tempo | ≥ 0.70 | ≥ 0.65 | < 0.60 (large BPM jumps) |
| f_harmony | ≥ 0.70 | ≥ 0.50 | < 0.40 (key incompatibility) |
| f_diversity | 1.00 | 1.00 | < 1.00 (duplicate songs) |
| range_penalty | 0.00 | ≤ 0.05 | > 0.10 (out-of-range songs) |
| **total** | **≥ 0.80** | **≥ 0.70** | **< 0.65** |

**Range penalty:** Subtracted from `total` after the base components are summed. Weight: 0.05 per semitone of distance from the closest comfortable tonic PC, capped at 0.20 per song. Only applies if `--leader-range` was provided to `enrich_pool.py`.
```

### Edit 7: Update Step 7 refine section

`[APPEND]` to the "If constraints are too strict..." paragraph in Step 7:

```markdown
- If H9 (duration) is the only violated constraint, prefer swapping one long song for a shorter alternative over reducing the song count. Use `semantic_search.py` with a theme query to find shorter candidates in the same phase.
- If range_penalty is high (> 0.10), check whether any out-of-range songs have a non-zero `recommended_key_shift_for_range` that was not applied in the draft. Applying the shift may bring the song into range without violating H5.
```

### Edit 8: Update Step 10 report description

`[REPLACE]` the bullet list describing the report contents in Step 10:

```markdown
This writes a single `proposal_report.md` containing:
- Run configuration (including leader range label and comfortable PCs)
- Pool overview (phase distribution, theme coverage, tempo/key coverage, duration distribution)
- Per-proposal details: song sequence, phase arc, BPM/key journey, duration per song, total duration vs. 25-min cap, score breakdown (including range_penalty), transition settings, singing-range status per song, warnings
- Diversity matrix: unique songs/themes/composers, song overlap matrix, song frequency table, theme coverage, bottlenecks
```

### Edit 9: Update Step 11 summary

`[APPEND]` to the Step 11 bullet list:

```markdown
- Total duration of the top proposal vs. 25-min cap (warn if close to the limit)
- Singing range used (label + comfortable PCs), count of in-range vs. out-of-range songs in the top proposal
- If any songs had `duration_seconds = None` (stale cache), warn that duration validation was skipped
```

---

## File Change Summary

| # | File | Change | Part |
|---|------|--------|------|
| 1 | `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/models.py` | Add `duration_seconds`, `in_leader_range`, `leader_range_distance`, `recommended_key_shift_for_range` to `SongCandidate` and `ProposalItem` | 1 + 2 |
| 2 | `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/db.py` | SELECT `duration_seconds` from recording in `fetch_catalog_pool` (mirror the latest-active-wins join from `songset.py:773`) | 1 |
| 3 | `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/validation.py` (or inline in `score_songset.py`) | Add H9 Hard Constraint (total duration ≤ 1500s) | 1 |
| 4 | `lab/skills/songset-constructor/scripts/enrich_pool.py` | Add `--leader-range` CLI flag; compute `in_leader_range`, `leader_range_distance`, `recommended_key_shift_for_range` per song | 2 |
| 5 | `lab/skills/songset-constructor/scripts/score_songset.py` | Apply soft range penalty to `total`; populate `duration_seconds` and range fields on `ProposalItem` | 1 + 2 |
| 6 | `lab/skills/songset-constructor/scripts/write_report.py` | Render total duration, per-item duration, range status, `range_penalty` in score breakdown | 1 + 2 |
| 7 | `lab/skills/songset-constructor/scripts/voice_ranges.py` (new) | Voice-type → comfortable tonic PC mapping table | 2 |
| 8 | `lab/skills/songset-constructor/SKILL.md` | Step 0 (gather inputs), Step 2/3/5/6/7/10/11 updates (Edits 1-9 above) | 1 + 2 |

---

## Open Questions (to resolve before or during implementation)

1. **DB column name for duration:** The implementer must read `ops/admin-cli/src/stream_of_worship/admin/commands/songset.py` around line 773 to find the exact column name and join used to fetch recording duration. Likely `duration_seconds` on the `recordings` table, but confirm before editing `db.py`.

2. **Validator location:** Are H0-H8 implemented in `ops/admin-cli/src/stream_of_worship/admin/songset_constructor/rules/validation.py` or inlined in `lab/skills/songset-constructor/scripts/score_songset.py`? Grep for `H0` / `violated` to confirm. If inlined, add H9 inline; if shared, add to the shared module.

3. **Voice-type → tonic PC table tuning:** The PC sets in section 2.1 are a first-pass guess. They should be reviewed by someone with music ministry experience before shipping. The table is stored in `scripts/voice_ranges.py` (or inline) for easy tuning.

4. **Range penalty weight:** `0.05` per semitone, capped at `0.20` per song. Should this be larger? The implementer should run a few test proposals with out-of-range songs and check whether the penalty is strong enough to deprioritize them without making them unscoreable. Adjust if needed.

5. **Circular PC distance vs. circle-of-fifths distance:** Section 2.2 uses chromatic distance (`circular_pc_distance`) for `leader_range_distance`. Should it use circle-of-fifths distance (`fifth_distance_on_circle` from `harmony.py:47-51`) instead? Chromatic is more intuitive for vocal range (a semitone is a semitone); circle-of-fifths is more musically meaningful for key compatibility. Recommend chromatic for range, circle-of-fifths for harmony (H5).

6. **`--leader-range` flag format:** JSON blob (`'{"comfortable_pcs": [...], "label": "..."}'`) vs. two-int form (`--leader-low-pc 9 --leader-high-pc 4`). Recommend JSON blob for flexibility (carries the label for reporting).

---

## Verification Plan

After implementation, verify with these test scenarios:

### Test 1: Duration cap enforced

1. Run the skill with `count=5` on a pool that includes long songs (> 5 min each).
2. Confirm the planner rejects candidates that would push the running total past 1500s.
3. Confirm `score_songset.py` reports H9 in `violated` if a draft exceeds 1500s.
4. Confirm `write_report.py` renders the total duration and H9 status.

### Test 2: Singing range applied

1. Run the skill with `--leader-range '{"comfortable_pcs": [0, 1, 2, 3, 4, 5], "label": "normal male"}'`.
2. Confirm `enrich_pool.py` populates `in_leader_range`, `leader_range_distance`, `recommended_key_shift_for_range` on each song.
3. Confirm `score_songset.py` applies the range penalty to `total`.
4. Confirm the report renders the per-song range status and `range_penalty`.

### Test 3: Backwards compatibility (no range provided)

1. Run the skill without `--leader-range`.
2. Confirm all songs get `in_leader_range = True` (or the range pass is skipped entirely).
3. Confirm `range_penalty = 0.0` and the score matches the pre-change behavior.

### Test 4: Stale cache (no duration)

1. Run the skill with a cached pool file that predates the `duration_seconds` field.
2. Confirm songs load with `duration_seconds = None`.
3. Confirm H9 passes vacuously (total = 0).
4. Confirm the summary warns that durations were unknown.

### Test 5: Step 0 interview

1. Invoke the skill and confirm the Question tool prompts for singing range.
2. Answer "normal male" and confirm the comfortable PCs `[0, 1, 2, 3, 4, 5]` are passed to `enrich_pool.py`.
3. Answer "G2 to E4" and confirm the parsed PCs are passed.
4. Decline / say "I don't know" and confirm the default ("normal male") is used with a warning.

---

## Out of Scope

- Pool-level pre-filtering by duration or range (per user decision: planner + validator only).
- Hard-dropping out-of-range songs (per user decision: soft penalty, allow original).
- New lookup script for durations (per user decision: extend SongCandidate).
- `.agents/skills/songset-constructor/` mirror sync.
- Webapp / Android / Render-worker changes.
- Pipeline robustness (pipefail, per-step smoke tests).
- Machine-readable voice-range input (the Question tool's plain-English interview is the sole input mechanism).
