# Plan: Improve User-Facing Musical Key Accuracy (v2)

Supersedes v1 (`specs/improve-musical-key-accuracy-v1.md`). Incorporates
decisions on: range keys (entry + exit roots), legacy audio fallback (always
used when catalog is missing), cache invalidation (full namespace switch),
and deferred overrides (v2 does not implement an overrides table).

## Summary of changes from v1

| Concern | v1 | v2 |
|---|---|---|
| Range keys | start root only | store `start_root` (entry), `end_root` (exit); transition logic treats each song as an `(entry, exit)` pair |
| Legacy audio rows (null margin/window) | "low-trust in admin, accept only as fallback" undefined threshold | always used as fallback when catalog is missing; marked `audio_legacy` in admin |
| Cache invalidation | lazy / selective | full namespace switch on `key_algorithm_version` change; new cache key suffix |
| Manual override table | optional v1, deferred | explicitly deferred to v3 (post-v2 audit) |
| Scraper → normalized fields | not addressed | scraper imports shared parser and writes normalized fields in same INSERT |
| `accept-catalog` / `accept-detected` durability | undefined | explicitly noted as *non-durable* — these mutate `recordings` rows and will be clobbered on detector re-analysis; reviewers must re-decide after v3 detector rollouts |

## Problem

(See v1 §"Problem". Unchanged: current detector matches scraped pitch-class
roots on 75/98 = 76.5% of comparable recordings, with non-random
dominant/subdominant confusion. Full-track chroma averaging is fragile for
worship recordings because intros, outros, modulations, vamping, dense vocal
arrangements, and accompaniment parts overweight non-tonic harmonies.)

## Goals

Inherited from v1, plus:

6. Treat every song as having an ordered `(entry_root, exit_root)` key pair
   for transition planning; single-key songs collapse entry == exit.
7. Keep `accept-catalog` / `accept-detected` functional but visibly
   non-durable, so reviewers know detector rollouts will wipe their decisions.

## Non-Goals

- **Defer manual override table to v3.** v2 ships without persistence of human
  key decisions beyond the analyzed row. The legacy `accept-catalog` /
  `accept-detected` mechanisms mutate the recording row directly; reviewers
  will need to re-decide after detector re-runs.
- No automatic durability guarantees for review state (acknowledged cost).
- No key source badges in Android UI.
- No GPU acceleration in the analysis service.

## Proposed User-Facing Key Policy

```text
if scraped catalog key exists and is parseable:
    display scraped catalog key
    source = "catalog"
elif audio-detected key exists:
    if margin / window-agreement fields present AND pass confidence policy:
        source = "audio"
    else if margin / window-agreement fields absent (legacy detector):
        source = "audio_legacy"      # always used as fallback; no catalog alternative
    else (fails new-detector thresholds):
        source = "unknown"
else:
    source = "unknown"
```

`audio_legacy` is a distinct source from `audio`. Both display the detected
key, but `audio_legacy` is flagged in admin surfaces and in transition
planning as "single correlation score only, no margin data." The threshold
policy applies only to rows from the new detector (`ks_segment_vote_v1`).

## Data Model

### `songs` additions (entry/exit roots)

```sql
ALTER TABLE songs ADD COLUMN musical_key_root              text;  -- entry pitch class display, e.g. 'F'
ALTER TABLE songs ADD COLUMN musical_key_mode             text;  -- 'major' | 'minor' | 'unknown'
ALTER TABLE songs ADD COLUMN musical_key_start_root       text;  -- entry root display
ALTER TABLE songs ADD COLUMN musical_key_end_root         text;  -- exit root display (null if single key)
ALTER TABLE songs ADD COLUMN musical_key_start_pitch_class int;  -- 0-11, normalized
ALTER TABLE songs ADD COLUMN musical_key_end_pitch_class   int;  -- 0-11, normalized (null if single key)
ALTER TABLE songs ADD COLUMN musical_key_parse_status     text;  -- 'ok' | 'range' | 'unparseable' | 'missing'
```

Semantics:

- For a single key (`F`): `start_root = end_root = F`;
  `start_pitch_class = end_pitch_class = 5`; `parse_status = 'ok'`.
- For a range key (`F-G`): `start_root = F`, `end_root = G`;
  `parse_status = 'range'`.
- For `D-Eb-F` (multi-modulation): `start_root = D`, `end_root = F`;
  intermediate modulations collapsed. Display raw string preserved in
  `musical_key`.
- For minor keys (`Em`): `mode = 'minor'`, `start_root = E`,
  `start_pitch_class = 4`.

### `recordings` additions (audio diagnostics)

```sql
ALTER TABLE recordings ADD COLUMN key_algorithm_version   text;
ALTER TABLE recordings ADD COLUMN key_score_margin        real;
ALTER TABLE recordings ADD COLUMN key_window_agreement    real;
ALTER TABLE recordings ADD COLUMN key_candidates         text;   -- JSON
ALTER TABLE recordings ADD COLUMN key_detected_at        timestamptz;
```

Same semantics as v1. Candidate JSON:

```json
[
  {"key": "F", "mode": "major", "score": 0.81, "window_votes": 12, "source": "segment_vote"},
  {"key": "C", "mode": "major", "score": 0.79, "window_votes": 9,  "source": "segment_vote"}
]
```

For legacy rows (Phase 2 stores candidates but computes them from the
existing detector without reruns), `window_votes` is `null` and
`source = "fulltrack_correlation"`.

### No override table in v2

Override table explicitly **deferred to v3**. v2 implementation should not
stub it. Accept-catalog / accept-detected mutate `recordings.musical_key`
and (for now) `key_confidence` (set to `1.0`, marked "admin-set"); these are
non-durable.

## Shared Key Normalization Module

Same as v1 §"Shared Key Normalization Module", with two additions:

### Output type (TypeScript)

```typescript
type ParsedMusicalKey = {
  raw: string;
  status: "ok" | "range" | "missing" | "unparseable";
  display: string;
  root: string | null;          // entry root, same as startRoot for single keys
  mode: "major" | "minor" | "unknown";
  startRoot: string | null;
  endRoot: string | null;       // null only when status = 'ok' (single key) or unparseable/missing
  pitchClass: number | null;    // entry pitch class (== startPitchClass)
  startPitchClass: number | null;
  endPitchClass: number | null | undefined;
};
```

### Range parsing rules

- Tokenize on `-` or `→` or `~` separators.
- Each token parsed independently using the same root+mode grammar.
- First non-empty token → `start_root`. Last non-empty token → `end_root`.
- Mode is inferred from the FIRST token only: `Em-G` → mode `minor`,
  startRoot `E`, endRoot `G`. (Worship modulation typically goes
  minor→relative-major; mode on later tokens ignored.)
- If only one token survives tokenization, `parse_status = 'ok'` and
  `endRoot = startRoot`.
- Empty input → `status: 'missing'`.
- Unparseable tokens (e.g. `unknown`, `?`) → `status: 'unparseable'` and
  all root fields `null`.

### Parser tests (Python + TypeScript parity)

Each case asserts both fields and pitch-class equality:

| Input | status | startRoot | startPitchClass | endRoot | endPitchClass | mode |
|---|---|---|---|---|---|---|
| `C#` | ok | C# | 1 | C# | 1 | major |
| `Db` | ok | Db | 1 | Db | 1 | major |
| `F# minor` | ok | F# | 6 | F# | 6 | minor |
| `F#m` | ok | F# | 6 | F# | 6 | minor |
| `E大調` | ok | E | 4 | E | 4 | major |
| `E小調` | ok | E | 4 | E | 4 | minor |
| `Em` | ok | E | 4 | E | 4 | minor |
| `Ｄ-F` | range | D | 2 | F | 5 | major |
| `D-Eb-F` | range | D | 2 | F | 5 | major |
| `Em-G` | range | E | 4 | G | 7 | minor |
| `` | missing | null | null | null | null | unknown |
| `null` | missing | null | null | null | null | unknown |
| `unknown` | unparseable | null | null | null | null | unknown |

Pitch-class equality assertions (former audit self-tests):

- `C#` ≡ `Db` (both pitch class 1)
- `Bb` ≡ `A#` (both pitch class 10)
- `F# minor` ≡ `Gb` (both pitch class 6)

## Effective Key Helper

```typescript
type EffectiveKeyInput = {
  catalogKey?: string | null;
  catalogParsed?: ParsedMusicalKey | null;
  detectedKey?: string | null;
  detectedMode?: string | null;
  detectedConfidence?: number | null;
  detectedMargin?: number | null;
  detectedWindowAgreement?: number | null;
};

type EffectiveKey = {
  display: string | null;
  source: "catalog" | "audio" | "audio_legacy" | "unknown";
  startRoot: string | null;
  endRoot: string | null;       // null only when source = "unknown"
  mode: "major" | "minor" | "unknown";
  startPitchClass: number | null;
  endPitchClass: number | null;
  confidence: number | null;
  warning: "none" | "audio_low_confidence" | "catalog_audio_disagree" | "unparseable_catalog";
};
```

Note: `source = "audio_legacy"` is not a warning; it is a distinct source
value surfaced separately in admin UI.

### Audio fallback acceptance policy (new detector rows only)

Accept the audio-detected key when:

```text
key_confidence >= 0.70
  AND key_score_margin >= 0.05
  AND key_window_agreement >= 0.55
```

If any threshold fails: source = `unknown`, with
`warning = audio_low_confidence`. Detected key is still stored on the
recording row, just not surfaced to users.

### Legacy rows (`key_score_margin IS NULL`)

- Always accepted as `audio_legacy` when no catalog key exists. (User
  decision: better than `Unknown`.)
- When catalog key exists, catalog wins regardless.
- Never block browse or render.
- Marked distinct from `audio` in admin UI.

### Catalog / audio agreement check

When a parseable catalog key exists, compute
`pitch_class(catalog.startPitchClass) == audio.pitchClass`. Modes ignored.
If they disagree and audio threshold passes:
`warning = catalog_audio_disagree`.

## Phase 1: Presentation Policy and Normalization

### 1.1 Add shared key parser

Implementation files:

- `ops/admin-cli/src/stream_of_worship/music/key.py` (Python)
- `ops/admin-cli/tests/music/test_key.py`
- `delivery/webapp/src/lib/music/key.ts` (TypeScript)
- `delivery/webapp/src/lib/music/key.test.ts`

Constraints:

- Pure functions; no I/O.
- No external deps beyond stdlib.
- Identical behavior across both languages. Fixture-driven tests.

### 1.2 Backfill normalized catalog key fields

1. Add `songs` migration via Drizzle (`npx drizzle-kit generate`).
2. Add admin backfill command:

   ```bash
   uv run --project ops/admin-cli --extra admin sow-admin maintenance backfill-key-normalization --dry-run
   uv run --project ops/admin-cli --extra admin sow-admin maintenance backfill-key-normalization
   ```

3. Batched writes: 500 rows per `UPDATE` statement, sleep 100ms between
   batches. Avoid long table locks on Neon.
4. Dry-run mode prints counts only: `missing`, `ok`, `range`, `unparseable`.
5. Idempotent: re-running on already-normalized rows is a no-op.

### 1.3 Scraper integration

Modify the sop.org scraper to call the shared parser during the song insert
path. Files:

- `ops/admin-cli/src/stream_of_worship/admin/commands/scrape.py` (or
  equivalent scraper module)

The scraper writes both `songs.musical_key` (raw) and the normalized columns
in the same `INSERT`. If parsing fails, `parse_status='unparseable'` is
written; the row is still inserted successfully. Scraper never throws on bad
key strings.

### 1.4 Webapp effective-key helper

- `delivery/webapp/src/lib/music/effective-key.ts`
- `delivery/webapp/src/lib/music/effective-key.test.ts`

Tests cover priority order: catalog → audio (.threshold) → audio_legacy →
unknown.

### 1.5 Webapp API surface

Apply helper in DB-shaping layer:

- `delivery/webapp/src/lib/db/songs.ts`
- `delivery/webapp/src/lib/db/search.ts`
- `delivery/webapp/src/lib/db/songsets.ts` (or wherever songset API responses
  are shaped)
- Songset editor routes that serialize recording metadata.

API response additions (nullable, additive):

- `effectiveKey` (display string)
- `effectiveKeySource` (`"catalog" | "audio" | "audio_legacy" | "unknown"`)
- `effectiveKeyStartRoot`
- `effectiveKeyEndRoot`
- `effectiveKeyMode`
- `effectiveKeyStartPitchClass` (number, for client-side transition logic)
- `effectiveKeyEndPitchClass` (number)
- `keyWarning`

Old `musicalKey` field stays in responses for compatibility. Sunset in v3
after Android migrations.

### 1.6 Webapp UI updates

- `SongCard.tsx`: display `effectiveKey`; render as `F → G` when endRoot
  differs from startRoot.
- `TransitionPanel.tsx`: use start/end pitch classes for compatibility
  scoring.
- Songset editor: surface source badge in dev/admin mode only.

### 1.7 Android DTO changes

- Add nullable fields: `effectiveKey`, `effectiveKeySource`,
  `effectiveKeyStartRoot`, `effectiveKeyEndRoot`, `effectiveKeyMode`.
- Display `effectiveKey`, falling back to legacy `musicalKey` when absent
  (older webapp versions).
- No source badges in v1.

### 1.8 Admin surfaces

- Admin detail view shows: `Key: F → G (catalog)`,
  `Detected: C major, confidence 0.832`,
  `Warning: catalog/audio disagree`.
- Admin list shows effective key source as a small badge (catalog / audio /
  audio_legacy / unknown).

## Phase 2: Detector Diagnostics Without Algorithm Replacement

### 2.1 Refactor detector return type

Refactor `detect_key` in `analyzer.py` to return `KeyDetectionResult`
(dataclass / Pydantic-compatible dict). Backward-compatible API writes:

- `musical_key`
- `musical_mode`
- `key_confidence`

New optional writes:

- `key_candidates` (JSON, top 5)
- `key_score_margin` (top − second)
- `key_window_agreement` (null for full-track)
- `key_algorithm_version` (`"ks_fulltrack_v1"`)
- `key_detected_at` (timestamp)

### 2.2 Update analysis-service API and admin ingestion

- `models.py`: extend `AnalyzeResult`/`AnalyzeResponse` with optional
  fields.
- `routes/jobs.py`: serialize new fields when present; missing triggers
  null.
- `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`:
  tolerate unknown fields.
- `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`: write new
  fields to DB when present.

### 2.3 Backfill diagnostics for existing rows

Lazy: when `key_algorithm_version` in DB is null AND row was analyzed
previously, the row is considered `legacy`. v2 does not retroactively
reprocess all legacy rows. Diagnostic backfill happens as a side effect of
Phase 3 cache invalidation (see below).

## Phase 3: Segment / Window-Based Audio Key Detector

### 3.1 Algorithm: `ks_segment_vote_v1`

1. Load mono audio. Reuse already-loaded `y` from `analyze_audio` and
   `analyze_audio_fast`.
2. `y_harmonic, _ = librosa.effects.hpss(y)`.
3. Compute **once** at high resolution:
   `chroma = librosa.feature.chroma_cqt(y=y_harmonic, sr=sr, hop_length=512)`
   → shape `(12, n_frames)`.
4. Frame slicing:
   - Full-tier: slice by `allin1` section boundaries from
     `result.segments`. Each section becomes one window.
   - Fast-tier: sliding windows of 20s with 50% overlap. For a 4-min track
     at 22050 Hz, hop_length 512: frame count ≈ 10336; ~25 windows. Cheap.
5. Score each window against 24 rolled KS profiles using `np.corrcoef`.
6. Filter windows:
   - too quiet: RMS percentile < 10th-percentile cutoff
   - too short: < 8s
   - flat chroma: max(chroma_avg) - min(chroma_avg) < threshold (0.1)
   - low margin: top - second < 0.03 within the window
7. Aggregate by pitch-class × mode:
   - weight votes by window duration
   - weight by RMS (capped at 90th percentile to avoid loud endings
     dominating)
   - downweight first section by 0.5 if shorter than 20s
   - downweight last section by 0.5 if shorter than 20s
8. Output:
   - winning `(key, mode)`
   - top 5 candidates with window votes
   - score margin (top minus second after aggregation)
   - window agreement (accepted windows voting for winning pitch class /
     total accepted windows)
   - algorithm_version = `ks_segment_vote_v1` (full-tier) or
     `ks_window_vote_v1` (fast-tier if behavior differs materially)

### 3.2 Confidence policy

```text
confidence >= 0.70
  AND score_margin >= 0.05
  AND window_agreement >= 0.55
```

Failing any threshold → source = `unknown`,
`warning = audio_low_confidence`.

### 3.3 Cache namespace switch

**User decision: full switch.** On enabling the new detector:

1. Bump cache namespace: add `key_algorithm_version` as part of the cache
   key generation in `CacheManager._get_hash_prefix`. New files are stored
   as `{hash}.v{version}.json`.
2. Old cache files (`{hash}.json`, `{hash}_fast.json`) become invalid for
   the new detector version.
3. Re-analysis triggered for any new request hitting a missing-cache-row.
   allin1 re-runs are expected (30s+ per recording in Docker).
4. Old analysis rows remain in DB with
   `key_algorithm_version='ks_fulltrack_v1'` and recompute lazily on next
   requested analysis.

**Operational cost:** for ~hundreds of recordings, expect up to ~10h
aggregate compute. Plan a backfill job that processes a queue offline (not
blocking user requests):

- Recommended: a new admin command
  `sow-admin audio backfill-key-diagnostics --algorithm ks_segment_vote_v1 --batch 20`
  that polls recordings with
  `key_algorithm_version IS NULL OR != 'ks_segment_vote_v1'` and submits
  re-analysis with `force=True`.
- Throttle to N concurrent analysis-service jobs to avoid OOM in Docker.

### 3.4 Audio retrieval during backfill

Audio for re-analysis is fetched from R2 via existing analysis-service audio
fetch path (already used by `analyze_audio`). Don't re-introduce a
local-audio-reader path; rely on the analysis service's existing
download-to-temp pattern.

### 3.5 Algorithm version config switch

A new env flag / config in the analysis service:
`KEY_ALGORITHM_VERSION` (default `'ks_segment_vote_v1'`).

- `analyze_audio` and `analyze_audio_fast` consult this value to decide
  which detector path to invoke.
- Algorithm dispatch is a single switch
  (`if algorithm == 'ks_segment_vote_v1'`).
- Reverting to `'ks_fulltrack_v1'` is supported.

## Phase 4: Audit Harness

### 4.1 Algorithm comparison

Extend `reports/key_detection_audit.py` with
`--candidate-results <file.jsonl>` flag. JSONL shape:

```json
{"content_hash": "<prefix>", "key": "F", "mode": "major", "confidence": 0.81, "score_margin": 0.05, "window_agreement": 0.6, "candidates": [...]}
```

Audit joins by `recordings.content_hash`, so the JSONL must use the same
hash prefix (first 32 chars) as `recordings.hash_prefix`.

### 4.2 New report metrics

In addition to current audit output:

- match rate by source class (`catalog` / `audio` / `audio_legacy` /
  `unknown`)
- match rate after applying effective-key policy
- disagreement count in catalog-using rows (manual review queue size)
- effective-key policy outcome distribution

### 4.3 Acceptance criteria for default detector

Do not make `ks_segment_vote_v1` default unless:

- pitch-class match rate improves from 76.5% to ≥ 85% on rows with catalog
  keys, **OR**
- fifth-related mismatches decrease by ≥ 50% without increasing relative
  major/minor errors, **AND**
- high-confidence mismatches are materially reduced at
  `confidence >= 0.80`.

## Phase 5: Disagreement Review Workflow

### 5.1 Review query

```sql
SELECT
  s.id,
  s.title,
  s.musical_key AS catalog_key,
  s.musical_key_start_root AS catalog_start_root,
  s.musical_key_end_root   AS catalog_end_root,
  r.content_hash,
  r.hash_prefix,
  r.original_filename,
  r.musical_key AS detected_key,
  r.musical_mode,
  r.key_confidence,
  r.key_score_margin,
  r.key_window_agreement,
  r.key_algorithm_version,
  r.key_candidates
FROM recordings r
JOIN songs s ON s.id = r.song_id
WHERE s.deleted_at IS NULL
  AND r.deleted_at IS NULL
  AND NULLIF(BTRIM(s.musical_key), '') IS NOT NULL
  AND NULLIF(BTRIM(r.musical_key), '') IS NOT NULL
  AND r.key_algorithm_version = 'ks_segment_vote_v1';
```

Pitch-class comparison happens through the shared parser at the app layer,
not SQL string comparison. SQL filter narrows to new-detector rows only.

### 5.2 Admin commands

```bash
sow-admin audio key-review list --limit 50
sow-admin audio key-review show --hash-prefix <hash>
sow-admin audio key-review accept-catalog --hash-prefix <hash>
sow-admin audio key-review accept-detected --hash-prefix <hash>
```

Acceptance mutates `recordings.musical_key`, `recordings.musical_mode`, and
sets `recordings.key_confidence = 1.0` (marker for "admin-accepted"). Lists
are row-mutation-only, no audit history in v2.

### 5.3 Durability caveat banner

All `key-review` commands print a caveat:

> Detected-key rows accepted through accept-catalog / accept-detected are
> non-durable. A detector version change + forced analysis re-run will
> clobber this state. v3 will introduce an overrides table preserving
> manual decisions.

## Phase 6: Rollout and Backfill

### 6.1 Order

1. Add shared Python key parser + tests.
2. Add shared TypeScript key parser + tests.
3. Add `songs` migrations; backfill catalog normalization.
4. Scraper integration — new songs write normalized fields on insert.
5. Add effective-key helper to webapp; expose new API fields.
6. Webapp UI updates (SongCard, TransitionPanel, admin views).
7. Android DTO + display updates.
8. Phase 2: `KeyDetectionResult` refactor; backfill candidates for
   `ks_fulltrack_v1` rows lazily on re-analysis.
9. Phase 3 implementation, gated behind
   `KEY_ALGORITHM_VERSION='ks_fulltrack_v1'`.
10. Run offline audit experiment; compare candidate detector vs current.
11. If metrics hit acceptance criteria → flip
    `KEY_ALGORITHM_VERSION='ks_segment_vote_v1'` → cache namespace bump →
    trigger backfill queue.
12. Phase 5 admin review queue.

### 6.2 Backfill strategy

- Catalog normalization: all active songs, batched 500/batch.
- Detector diagnostics (Phase 3): queue-based, N=3 concurrent, no
  user-facing impact. Records skipped on transient R2 / Docker errors;
  retried on next queue pass.

### 6.3 Deployment compatibility

- All new columns nullable.
- Webapp tolerates missing effective-key fields (older API consumers).
- Analysis service tolerates older DB clients (writes accepted without new
  fields).
- Old cached analysis results become stale on detector version switch —
  handled by namespace bump.

## Testing Plan

### Python

```bash
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest ops/admin-cli/tests -v
```

Focused tests:

- key parser normalization (TS-vs-Python parity fixtures)
- catalog key backfill dry-run counts
- analysis response parsing with and without diagnostic fields

### Analysis service

```bash
cd ops/analysis-service && PYTHONPATH=src pytest tests/ -v
```

Focused tests:

- `KeyDetectionResult` candidate ordering
- score margin calculation
- legacy cache compatibility (versioned lookup resolves to
  `ks_fulltrack_v1` default for missing-field JSON)
- segment/window detector on synthetic audio (sine wave at known pitch)
- API result serialization with optional new fields

### Webapp

```bash
cd delivery/webapp && pnpm test && pnpm lint && pnpm build
```

Focused tests:

- TypeScript key parser parity with Python
- effective-key helper priority (catalog > audio > audio_legacy > unknown;
  threshold failures → unknown)
- DB/API mapping includes new effective-key fields

### Android

```bash
cd delivery/android && ./gradlew testDebugUnitTest && ./gradlew koverXmlReport && ./gradlew lintDebug
```

Tests:

- nullable effective-key fields fall back cleanly
- present effective-key fields are displayed

### Audit

```bash
uv run --project ops/analysis-service python reports/run_key_detector_experiment.py --limit 100 --output reports/key_detector_experiment.jsonl

uv run --project ops/admin-cli --python 3.11 --extra admin --extra test \
  python reports/key_detection_audit.py --candidate-results reports/key_detector_experiment.jsonl \
  --output reports/key_detection_algorithm_review_v2.md
```

## Implementation Checklist

### Phase 1

- [ ] Add shared Python key parser.
- [ ] Add shared TypeScript key parser.
- [ ] Add parser fixtures and parity tests in both languages.
- [ ] Add nullable normalized key columns to `songs`.
- [ ] Add admin backfill command (dry-run + batched writes).
- [ ] Backfill normalized catalog keys.
- [ ] Integrate parser into scraper INSERT path.
- [ ] Add webapp effective-key helper.
- [ ] Add effective-key fields to API responses.
- [ ] Update webapp user-facing displays.
- [ ] Update Android DTO + display.
- [ ] Update admin displays to show source/disagreement.

### Phase 2

- [ ] Add nullable diagnostic columns to `recordings`.
- [ ] Refactor analysis-service detector return type.
- [ ] Store top-N candidates for current full-track detector.
- [ ] Update cache manager for versioned lookup.
- [ ] Update analysis-service API models.
- [ ] Update admin CLI analysis-ingestion.
- [ ] Add tests for legacy vs. new result payloads.

### Phase 3

- [ ] Implement HPSS + harmonic chroma path.
- [ ] Implement sliding-window + section-aware scoring.
- [ ] Implement window filtering and aggregation.
- [ ] Algorithm version config switch.
- [ ] Cache namespace bump on version change.
- [ ] Run offline detector experiment.
- [ ] Compare with audit before enabling default.

### Phase 5

- [ ] Add disagreement query.
- [ ] Add admin list/show commands.
- [ ] Add accept-catalog / accept-detected commands (with durability
      caveat).
- [ ] Tests.

## Risks and Mitigations

### Risk: Range key display is misleading for transition logic

- Use `(start_root → end_root)` in songset builder; transition scoring
  treats each song as an ordered pair, not a single key.
- If only one of start/end is set, treat as single-key song.

### Risk: Detector version bump clobbers manual accept-catalog/accept-detected

- Note prominently in v2 docs and admin CLI output.
- Mitigation coming in v3 via overrides table.

### Risk: Cache namespace switch triggers full re-analysis cost

- Backfill queue with throttled concurrency.
- Plan offline window for the first rollout.

### Risk: HPSS adds CPU overhead

- Documented cost: ~3-5s extra per `<5min` track on commodity hardware.
  Acceptable; analysis service has no SLO for fast-tier.

### Risk: Catalog key parse failure onboard new songs

- Scraper uses shared parser; on parse failure,
  `parse_status='unparseable'` is stored, song still inserts.
- Admin review queue catches these later for manual fix.

## Open Decisions

1. (Resolved) Range keys resolve to `(entry, exit)` pair.
2. (Resolved) Legacy audio rows accepted as fallback unconditionally.
3. (Resolved) Cache invalidation via full namespace switch.
4. (Resolved) Defer overrides to v3.
5. (Resolved) Scraper writes normalized fields inline.
6. (Open) Should transition scoring accept `audio_legacy` rows as
   authoritative for compatibility logic, or treat them as `unknown` for the
   songset builder? Initial recommendation: accept as authoritative
   (matches user-facing display).

## Definition of Done

- User-facing surfaces prefer scraped catalog keys whenever available and
  parseable.
- Range keys display as `start → end` and contribute `(entry, exit)` to
  transition logic.
- Audio-detected keys: new detector passes confidence policy before being
  used as fallback; legacy rows accepted unconditionally as `audio_legacy`.
- Effective key source (`catalog` / `audio` / `audio_legacy` / `unknown`)
  available in webapp API responses.
- Parser parity tested in Python and TypeScript.
- Analysis-service backward compatibility preserved for legacy cache
  (treated as `ks_fulltrack_v1`).
- New detector shipped behind config flag; default only after audit
  acceptance metrics hit.
- Admin disagreement review queue exists; accept-catalog / accept-detected
  work; durability caveat documented.
- Manual override table explicitly deferred to v3.
