# Plan: Improve User-Facing Musical Key Accuracy (v1)

## Problem

The application currently has two key sources with different trust levels:

- `songs.musical_key`: scraped/catalog key from sop.org metadata.
- `recordings.musical_key`, `recordings.musical_mode`, `recordings.key_confidence`:
  audio-analysis output from the analysis service.

The audit in `reports/key_detection_algorithm_review.md` found that the current
audio detector matches scraped catalog pitch-class roots for only 75 of 98 comparable
active analyzed recordings, or 76.5%. The mismatch pattern is not random. A large
share of misses are fifth-related roots such as:

- nominal `F`, detected `C`
- nominal `D`, detected `A`
- nominal `G`, detected `D`
- nominal `C`, detected `G`

Those are musically plausible dominant/subdominant confusions and are consistent
with the implementation in `ops/analysis-service/src/sow_analysis/workers/analyzer.py`:
it computes one full-track `librosa.feature.chroma_cqt`, averages chroma over the
entire track, correlates that vector against 24 rolled Krumhansl-Schmuckler profiles,
and publishes the top correlation as the key.

That implementation is deterministic and cheap, but full-track averaging is fragile
for worship recordings. Intros, outros, extended bridges, modulations, live vamping,
dense vocal arrangements, and accompaniment parts can all overweight non-tonic
harmonies. The detector also publishes one winning key and one raw correlation score,
which is not enough to express ambiguity or compare against the catalog key.

## Goals

1. Present a more accurate key to users by preferring scraped catalog key data whenever
   it is available and parseable.
2. Preserve audio-detected key data as diagnostic and fallback data instead of treating
   it as authoritative user-facing metadata.
3. Improve the audio key detector so recordings without catalog keys get better fallback
   keys.
4. Store enough key provenance and diagnostics to support review, thresholding, and
   future algorithm changes.
5. Add an audit-driven validation loop so algorithm changes are measured against the
   existing catalog comparison before production rollout.

## Non-Goals

- Do not replace the song catalog scraper.
- Do not require manual key review before users can browse or render songsets.
- Do not remove `recordings.musical_key`; existing callers and historical analysis
  results should remain readable.
- Do not treat scraped catalog key as absolute truth. It is the preferred user-facing
  source, but disagreement cases should be surfaced for review.
- Do not add heavy ML dependencies to the Admin CLI. Any audio analysis improvements
  stay in the analysis service or in isolated research scripts.

## Current State

### Data Model

Relevant existing columns:

- `songs.musical_key`: scraped nominal key string.
- `recordings.musical_key`: detected recording key root.
- `recordings.musical_mode`: detected mode.
- `recordings.key_confidence`: raw winning correlation from the current detector.
- `recordings.analysis_status`: currently used to distinguish pending, partial, and
  completed analysis states.

There is no explicit key source, no normalized pitch-class field, no candidate list,
no score margin, no algorithm version, and no manual override field.

### Audio Detector

`ops/analysis-service/src/sow_analysis/workers/analyzer.py`:

- `detect_key(y, sr)` computes CQT chroma with `hop_length=512`.
- It averages chroma over all frames.
- It scores 12 major and 12 minor rolled Krumhansl-Schmuckler profiles using
  `np.corrcoef`.
- It returns `(mode, key, confidence)` for only the best candidate.
- Both `analyze_audio()` and `analyze_audio_fast()` call this same detector.

### Audit Tooling

`reports/key_detection_audit.py` already contains useful parsing logic:

- full-width roman letter normalization
- sharp/flat symbol normalization
- Chinese accidental normalization
- enharmonic pitch-class matching
- mode-insensitive root comparison

That logic is report-local today. Production code should not duplicate it ad hoc.

## Design Principles

1. **Separate display from detection.** The key shown to users should be the best
   available key, not necessarily the most recent detector output.
2. **Keep raw sources intact.** Catalog, audio detection, and manual override should
   remain distinguishable.
3. **Prefer structured derived fields for logic.** Display strings such as `F-G` are
   useful to users, but transition logic needs normalized pitch classes.
4. **Use algorithm versioning.** Key detector results must be attributable to the
   algorithm that produced them.
5. **Measure before replacing.** Any detector change must run through the audit harness
   before being used to overwrite production recording rows.

## Proposed User-Facing Key Policy

Introduce an effective/display key policy:

```text
if manual key override exists:
    display manual override
    source = "manual"
elif scraped catalog key exists and is parseable:
    display scraped catalog key
    source = "catalog"
elif audio-detected key exists and passes confidence policy:
    display audio-detected key
    source = "audio"
else:
    display "Unknown"
    source = "unknown"
```

Important behavior:

- Do not overwrite `recordings.musical_key` with `songs.musical_key`.
- Do not hide detected key from admin/review surfaces.
- User-facing song cards, song details, songset editor metadata, Android browse/detail
  views, and transition planning should use the same effective-key helper.
- Admin detail views should show both:
  - effective/display key and source
  - raw catalog key and raw detected key

## Proposed Data Model

Implement this in migrations after final review. Exact column names can be adjusted to
match existing Drizzle/admin naming conventions, but the semantics should stay stable.

### `songs` Additions

Keep `songs.musical_key` as the raw scraped display string. Add derived normalized
fields:

```sql
ALTER TABLE songs ADD COLUMN musical_key_root text;
ALTER TABLE songs ADD COLUMN musical_key_mode text;
ALTER TABLE songs ADD COLUMN musical_key_start_root text;
ALTER TABLE songs ADD COLUMN musical_key_end_root text;
ALTER TABLE songs ADD COLUMN musical_key_parse_status text;
```

Semantics:

- `musical_key_root`: primary pitch-class root used for compatibility logic. For a
  simple key like `F`, this is `F`. For a range like `F-G`, this should be the start
  key unless a later product decision requires end-key semantics.
- `musical_key_mode`: `major`, `minor`, or `unknown`.
- `musical_key_start_root`: first key in a range/modulation notation.
- `musical_key_end_root`: final key in a range/modulation notation, nullable.
- `musical_key_parse_status`: `ok`, `range`, `unparseable`, or `missing`.

Rationale:

- `songs.musical_key` values such as `G-A`, `D-F`, and `Em` are useful display strings.
- Transition scoring needs stable pitch-class roots and should not parse display strings
  repeatedly in multiple layers.

### `recordings` Additions

Add audio-detection diagnostics:

```sql
ALTER TABLE recordings ADD COLUMN key_algorithm_version text;
ALTER TABLE recordings ADD COLUMN key_score_margin real;
ALTER TABLE recordings ADD COLUMN key_window_agreement real;
ALTER TABLE recordings ADD COLUMN key_candidates text;
ALTER TABLE recordings ADD COLUMN key_detected_at timestamp with time zone;
```

Semantics:

- `key_algorithm_version`: for example `ks_fulltrack_v1`, `ks_segment_vote_v1`.
- `key_score_margin`: score gap between the top two candidates after aggregation.
- `key_window_agreement`: share of accepted windows voting for the winning pitch class.
- `key_candidates`: JSON string for top-N candidates, matching existing text-JSON
  patterns such as `beats`, `downbeats`, and `sections`.
- `key_detected_at`: timestamp for the detector result.

Candidate JSON shape:

```json
[
  {
    "key": "F",
    "mode": "major",
    "score": 0.81,
    "window_votes": 12,
    "source": "segment_vote"
  },
  {
    "key": "C",
    "mode": "major",
    "score": 0.79,
    "window_votes": 9,
    "source": "segment_vote"
  }
]
```

### Optional Manual Override Table

Prefer a separate table rather than adding one-off columns to `songs` or `recordings`,
because overrides may need attribution and history.

```sql
CREATE TABLE key_overrides (
    id text PRIMARY KEY,
    song_id text NOT NULL REFERENCES songs(id),
    recording_content_hash text REFERENCES recordings(content_hash),
    musical_key text NOT NULL,
    musical_mode text,
    normalized_root text NOT NULL,
    normalized_mode text,
    reason text,
    created_by_user_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deleted_at timestamp with time zone
);
```

Interpretation:

- `recording_content_hash` nullable means the override applies to the song/catalog
  level.
- Non-null `recording_content_hash` applies to one specific recording.
- Active recording-level override outranks active song-level override.
- Overrides are soft-deletable to preserve audit history.

If this is too much for the first rollout, defer the table and implement only catalog
first, audio fallback second.

## Shared Key Normalization Module

Create a shared production key parser based on `reports/key_detection_audit.py`.

Recommended Python module:

- `ops/admin-cli/src/stream_of_worship/music/key.py`

Recommended TypeScript module:

- `delivery/webapp/src/lib/music/key.ts`

The two modules should expose equivalent behavior and be covered by matching fixtures.

### Parser Requirements

Inputs to support:

- `C`, `C#`, `Db`, `Bb`
- `F# minor`, `F#m`, `Em`
- `E大調`, `E小調`
- full-width roman letters such as `Ｄ`
- accidental symbols `♯`, `＃`, `♭`, `升`, `降`
- range/modulation strings such as `F-G`, `G-A`, `D-Eb-F`
- blank, null, and unknown strings

Normalized output:

```typescript
type ParsedMusicalKey = {
  raw: string;
  status: "ok" | "range" | "missing" | "unparseable";
  display: string;
  root: string | null;
  mode: "major" | "minor" | "unknown";
  startRoot: string | null;
  endRoot: string | null;
  pitchClass: number | null;
  startPitchClass: number | null;
  endPitchClass: number | null;
};
```

Canonical display:

- Preserve `songs.musical_key` for user display when source is catalog.
- Use friendly flat names for normalized display where the raw string is missing,
  matching audit behavior: `Db`, `Eb`, `Gb`, `Ab`, `Bb`.
- Internal equality should use pitch classes, not string equality.

### Parser Tests

Add equivalent tests in Python and TypeScript:

- `C#` equals `Db`
- `Bb` equals `A#`
- `F# minor` equals `Gb` by pitch class
- `E大調` parses root `E`, mode `major`
- `Em` parses root `E`, mode `minor`
- `Ｄ-F` parses start `D`, end `F`, status `range`
- blank and null return `missing`
- `unknown` returns `unparseable`

## Effective Key Helper

Add one central helper per application boundary instead of scattering fallback logic.

Recommended TypeScript helper:

- `delivery/webapp/src/lib/music/effective-key.ts`

Recommended shape:

```typescript
type EffectiveKeyInput = {
  catalogKey?: string | null;
  catalogParsed?: ParsedMusicalKey | null;
  detectedKey?: string | null;
  detectedMode?: string | null;
  detectedConfidence?: number | null;
  detectedMargin?: number | null;
  manualOverride?: ParsedMusicalKey | null;
};

type EffectiveKey = {
  display: string | null;
  source: "manual" | "catalog" | "audio" | "unknown";
  root: string | null;
  mode: "major" | "minor" | "unknown";
  pitchClass: number | null;
  confidence: number | null;
  warning: "none" | "audio_low_confidence" | "catalog_audio_disagree" | "unparseable_catalog";
};
```

Audio fallback acceptance policy, initial version:

- Accept audio key if `recordings.musical_key` is non-empty and either:
  - `key_confidence >= 0.80` and `key_score_margin >= 0.05`, or
  - `key_window_agreement >= 0.55`.
- If margin/window fields are absent because the row came from the old detector, accept
  only as fallback and mark confidence as legacy/low-trust in admin views.

Do not use `key_confidence` alone to override catalog data. The audit shows
high-confidence mismatches.

## Phase 1: Presentation Policy and Normalization

This phase gives the fastest user-visible accuracy improvement without changing audio
analysis behavior.

### 1.1 Add Shared Key Parser

Implement Python and TypeScript key normalization modules using the audit script as
the behavioral reference.

Files likely touched:

- `ops/admin-cli/src/stream_of_worship/music/key.py`
- `ops/admin-cli/tests/...`
- `delivery/webapp/src/lib/music/key.ts`
- `delivery/webapp/src/lib/music/key.test.ts`

Implementation notes:

- Keep parser pure and dependency-free.
- Use explicit pitch-class maps.
- Avoid parsing mode before root; range notation can include multiple roots.
- Return structured parse status instead of throwing for bad input.

### 1.2 Backfill Normalized Catalog Key Fields

Add migration and backfill tooling for `songs` normalized key columns.

Webapp migration:

- Add Drizzle schema fields.
- Generate migration with `npx drizzle-kit generate`.

Admin backfill command:

- Add a small admin maintenance command or script to parse existing `songs.musical_key`
  rows and update normalized fields.
- Support dry-run output with counts:
  - missing
  - ok
  - range
  - unparseable

Suggested command:

```bash
uv run --project ops/admin-cli --extra admin sow-admin maintenance backfill-key-normalization --dry-run
uv run --project ops/admin-cli --extra admin sow-admin maintenance backfill-key-normalization
```

### 1.3 Add Effective Key Helper to Webapp

Implement a webapp helper that accepts song + recording metadata and returns effective
key fields.

Use this helper in browse/search/songset-facing DB mapping or API response shaping,
preferably as close to the DB mapping layer as possible so UI components do not
duplicate policy.

Likely files to audit:

- `delivery/webapp/src/lib/db/songs.ts`
- `delivery/webapp/src/lib/db/search.ts`
- `delivery/webapp/src/components/songset/SongCard.tsx`
- `delivery/webapp/src/components/songset/TransitionPanel.tsx`
- any songset editor API routes that serialize recording metadata

Output contract:

- Keep existing `musicalKey` fields for compatibility during rollout.
- Add new explicit fields:
  - `effectiveKey`
  - `effectiveKeySource`
  - `effectiveKeyRoot`
  - `effectiveKeyMode`
  - `keyWarning`

Only after clients have migrated should old ambiguous fields be considered for cleanup.

### 1.4 Android API Compatibility

The Android app consumes webapp JSON APIs only. Do not make Android parse raw catalog
strings differently from webapp.

Webapp APIs should return effective key fields directly so Android can display them
without reimplementing parser policy.

Android follow-up:

- Add nullable fields to DTOs.
- Prefer `effectiveKey` for display.
- Fall back to the old field while older deployments are still possible.

### 1.5 Admin Display

Update admin detail/list surfaces to show source and disagreements:

```text
Key: F-G (catalog)
Detected: C major, confidence 0.832
Warning: catalog/audio disagree
```

Do this only after helper and normalized fields exist.

## Phase 2: Detector Diagnostics Without Algorithm Replacement

This phase keeps the current detector but stores better diagnostics, making later
algorithm changes safer.

### 2.1 Refactor Current Detector Return Type

Replace the raw tuple return internally with a dataclass/Pydantic-compatible dict:

```python
@dataclass(frozen=True)
class KeyDetectionResult:
    key: str
    mode: str
    confidence: float
    candidates: list[KeyCandidate]
    score_margin: float | None
    window_agreement: float | None
    algorithm_version: str
```

Maintain API compatibility by still returning/writing:

- `musical_key`
- `musical_mode`
- `key_confidence`

Add optional result fields:

- `key_candidates`
- `key_score_margin`
- `key_window_agreement`
- `key_algorithm_version`

### 2.2 Store Top-N Candidates for Current Full-Track Detector

For the existing detector:

- Sort all 24 correlations.
- Store top 5 candidates.
- Store `score_margin = top_score - second_score`.
- Set `window_agreement = null`.
- Set `key_algorithm_version = "ks_fulltrack_v1"`.

This immediately makes ambiguity inspectable without changing results.

### 2.3 Update Models, Cache, and Job Serialization

Likely files:

- `ops/analysis-service/src/sow_analysis/models.py`
- `ops/analysis-service/src/sow_analysis/workers/analyzer.py`
- `ops/analysis-service/src/sow_analysis/workers/queue.py`
- `ops/analysis-service/src/sow_analysis/routes/jobs.py`
- `ops/analysis-service/src/sow_analysis/storage/cache.py`
- `ops/admin-cli/src/stream_of_worship/admin/services/analysis.py`
- `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py`
- DB update methods that write analysis results

Compatibility rule:

- Old cached analysis results without diagnostic fields must still load.
- New fields are optional at API boundaries until all services are deployed.

## Phase 3: Segment/Window-Based Audio Key Detector

This phase changes the detector algorithm. It should be implemented behind an algorithm
version flag or config switch until audit results are acceptable.

### 3.1 Detector Algorithm

Recommended algorithm for `ks_segment_vote_v1`:

1. Load mono audio.
2. Use harmonic-percussive source separation:
   - `y_harmonic, _ = librosa.effects.hpss(y)`
3. Compute chroma from harmonic audio:
   - start with `librosa.feature.chroma_cqt`
   - optionally compare `chroma_stft` and `chroma_cens` during audit
4. Build analysis windows:
   - full-tier: prefer `allin1` sections when available
   - fast-tier: use fixed sliding windows, for example 20-30 seconds with 50% overlap
5. Exclude low-information windows:
   - too quiet by RMS percentile
   - too short
   - flat/near-uniform chroma
   - top-two score margin below threshold
6. Score each accepted window against 24 key profiles.
7. Aggregate by pitch class and mode:
   - weight by window duration
   - weight by RMS/energy with cap to avoid loud endings dominating
   - downweight likely intro/outro windows
8. Return:
   - winning key/mode
   - top candidates
   - score margin
   - window agreement
   - accepted/rejected window counts

### 3.2 Section Weighting Policy

For full-tier analysis, sections from allin1 are available after `result.segments`.

Initial weighting:

- Exclude sections shorter than 8 seconds unless there are too few windows.
- Downweight first section if it is shorter than 20 seconds.
- Downweight last section if it is shorter than 20 seconds.
- Weight repeated middle sections normally.
- Do not assume allin1 labels are semantically correct; use timing/duration more than
  label text.

For fast-tier analysis:

- Use sliding windows only.
- Use the same scoring/aggregation functions.
- Set algorithm version to `ks_window_vote_v1` if behavior differs materially from
  full-tier section voting.

### 3.3 Confidence Policy

Publish audio key as acceptable fallback only when:

- top aggregate score is above threshold
- `key_score_margin` is above threshold
- `key_window_agreement` is above threshold

Initial thresholds should be conservative:

```text
confidence >= 0.70
score_margin >= 0.05
window_agreement >= 0.55
```

Tune thresholds with the audit output. Do not use these values blindly if the audit
shows obvious false confidence.

### 3.4 Cache Versioning

Existing analysis caches are keyed by content hash and do not inherently distinguish
key algorithm behavior.

Options:

1. Add algorithm version to the cached result and force recomputation when missing or
   stale.
2. Version the cache namespace for analysis results.

Minimum requirement:

- If `force=False` and cached result has `key_algorithm_version` equal to the requested
  version, use it.
- If cached result is missing the field or has an older version, recompute key fields
  or require an explicit force depending on runtime cost.

Full recomputation may be expensive because allin1 runs are heavy. Prefer a path that
can recompute key-only diagnostics from audio without rerunning allin1 when possible.

## Phase 4: Audit Harness for Algorithm Comparison

Extend `reports/key_detection_audit.py` so algorithm changes can be measured before
production writes.

### 4.1 Add Algorithm Comparison Mode

Add a mode that can read detector outputs from either:

- existing database fields
- a JSONL file produced by offline detector runs
- both

Suggested commands:

```bash
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test \
  python reports/key_detection_audit.py --output reports/key_detection_algorithm_review.md

uv run --project ops/analysis-service --extra test \
  python reports/run_key_detector_experiment.py --limit 100 --output reports/key_detector_experiment.jsonl

uv run --project ops/admin-cli --python 3.11 --extra admin --extra test \
  python reports/key_detection_audit.py \
    --candidate-results reports/key_detector_experiment.jsonl \
    --output reports/key_detection_algorithm_review_v2.md
```

The exact `uv` project command may need adjustment based on dependency availability.
Do not make the Admin CLI import librosa or ML dependencies.

### 4.2 Metrics

Report for each algorithm/policy:

- comparable row count
- exact pitch-class match rate
- mismatch rate
- unparseable candidate rate
- match rate by confidence/margin/window-agreement bands
- mismatch distance distribution
- fifth-related mismatch count
- relative major/minor-style mismatch count
- top mismatch pairs
- high-confidence mismatch examples
- catalog-prior policy outcome:
  - catalog used
  - audio fallback used
  - unknown
  - disagreement queued for review

### 4.3 Acceptance Criteria for Detector Replacement

Do not make `ks_segment_vote_v1` the default detector unless it improves over
`ks_fulltrack_v1` on the current dataset:

- pitch-class match rate improves from 76.5% to at least 85% on rows with catalog keys,
  or
- fifth-related mismatches decrease by at least 50% without increasing relative
  major/minor errors, and
- high-confidence mismatches are materially reduced, especially at `confidence >= 0.80`.

Catalog-prior display policy can ship independently because it improves user-facing
accuracy without relying on the detector.

## Phase 5: Disagreement Review Workflow

After effective key and diagnostics exist, add a focused admin review surface.

### 5.1 Review Query

Flag active recordings where:

- catalog key parses successfully
- detected key parses successfully
- pitch classes differ
- and either:
  - detected confidence is high
  - score margin is high
  - catalog key is range/unusual
  - manual override does not exist

Initial SQL shape:

```sql
SELECT
  s.id,
  s.title,
  s.musical_key AS catalog_key,
  s.musical_key_root AS catalog_root,
  r.content_hash,
  r.hash_prefix,
  r.original_filename,
  r.musical_key AS detected_key,
  r.musical_mode AS detected_mode,
  r.key_confidence,
  r.key_score_margin,
  r.key_window_agreement,
  r.key_candidates
FROM recordings r
JOIN songs s ON s.id = r.song_id
WHERE s.deleted_at IS NULL
  AND r.deleted_at IS NULL
  AND NULLIF(BTRIM(s.musical_key), '') IS NOT NULL
  AND NULLIF(BTRIM(r.musical_key), '') IS NOT NULL;
```

Pitch-class comparison should happen through normalized columns or shared parser logic,
not SQL string comparison.

### 5.2 Admin Commands

Suggested CLI commands:

```bash
sow-admin audio key-review list --limit 50
sow-admin audio key-review show --hash-prefix <hash>
sow-admin audio key-review override --hash-prefix <hash> --key F --mode major --reason "verified by lead sheet"
sow-admin audio key-review accept-catalog --hash-prefix <hash>
sow-admin audio key-review accept-detected --hash-prefix <hash>
```

The first implementation can be list-only plus override creation. Rich interactive UX
can come later.

### 5.3 Web/Admin UX Later

If the admin CLI workflow proves useful, expose the same review queue in the admin
web UI later:

- side-by-side catalog/detected key
- top candidate list
- audio preview link
- one-click accept catalog/detected/manual key

## Phase 6: Rollout and Backfill

### 6.1 Rollout Order

1. Add parser tests.
2. Add normalized catalog key fields and backfill.
3. Add effective-key helper and API fields.
4. Update webapp user-facing displays to use effective key.
5. Update Android DTO/display after webapp API ships.
6. Add detector diagnostics while preserving old detector behavior.
7. Run audit and offline experiments.
8. Enable new segment/window detector behind a config flag.
9. Backfill analysis diagnostics for selected recordings.
10. Add key disagreement review queue.

### 6.2 Backfill Strategy

Catalog normalization:

- Safe to run across all active songs.
- Should also handle deleted songs if admin review needs historical data; otherwise
  active rows only are enough.

Detector diagnostics:

- Do not force full analysis for all recordings at once.
- Start with key-only recomputation for active recordings with:
  - missing catalog key
  - catalog/audio disagreement
  - low confidence
  - high user traffic or recently used in songsets

### 6.3 Deployment Compatibility

Because webapp, analysis service, admin CLI, Android, and render worker are separate:

- New DB columns must be nullable at first.
- API consumers must tolerate absent effective-key fields during rollout.
- Analysis service must tolerate older DB/admin clients not sending diagnostic fields.
- Admin CLI must tolerate older analysis service responses without diagnostics.

## Testing Plan

### Python

Run targeted parser/backfill/admin tests:

```bash
PYTHONPATH=ops/admin-cli/src uv run --project ops/admin-cli --python 3.11 --extra admin --extra test pytest ops/admin-cli/tests -v
```

Add focused tests for:

- key parser normalization
- catalog key backfill dry-run counts
- manual override priority if implemented
- analysis response parsing with and without diagnostic fields

Analysis service:

```bash
cd ops/analysis-service && PYTHONPATH=src pytest tests/ -v
```

Add focused tests for:

- current detector candidate ordering
- score margin calculation
- legacy cache compatibility
- segment/window detector on synthetic audio where feasible
- API result serialization with optional new fields

### Webapp

```bash
cd delivery/webapp
pnpm test
pnpm lint
pnpm build
```

Add focused tests for:

- TypeScript key parser
- effective-key helper priority:
  - manual over catalog
  - catalog over audio
  - audio fallback over unknown
  - low-confidence audio returns unknown
- DB/API mapping includes effective-key fields
- SongCard and transition metadata display effective key

### Android

```bash
cd delivery/android
./gradlew testDebugUnitTest
./gradlew koverXmlReport
./gradlew lintDebug
```

Add tests after Android DTO/display changes:

- missing effective-key fields fall back cleanly
- present effective-key fields are displayed

### Audit

After detector changes:

```bash
uv run --project ops/admin-cli --python 3.11 --extra admin --extra test \
  python reports/key_detection_audit.py --output reports/key_detection_algorithm_review.md
```

If the audit touches live database data, keep the transaction read-only.

## Implementation Checklist

### Phase 1: Display Accuracy

- [ ] Add shared Python key parser.
- [ ] Add shared TypeScript key parser.
- [ ] Add parser fixture tests in both languages.
- [ ] Add nullable normalized key columns to `songs`.
- [ ] Add admin backfill command with dry-run.
- [ ] Backfill normalized catalog keys.
- [ ] Add webapp effective-key helper.
- [ ] Add effective-key fields to relevant webapp API responses.
- [ ] Update webapp user-facing key displays.
- [ ] Update admin displays to show source/disagreement.
- [ ] Update Android DTO/display if API changes are user-visible there.

### Phase 2: Diagnostics

- [ ] Add nullable diagnostic columns to `recordings`.
- [ ] Refactor analysis-service key detector return type internally.
- [ ] Store top-N candidates for current full-track detector.
- [ ] Add score margin and algorithm version.
- [ ] Update cache compatibility.
- [ ] Update analysis-service API models.
- [ ] Update admin CLI analysis result ingestion.
- [ ] Add tests for old/new result payloads.

### Phase 3: Better Detector

- [ ] Implement shared key scoring function.
- [ ] Implement harmonic audio extraction.
- [ ] Implement sliding-window scoring.
- [ ] Implement section-aware scoring for full-tier analysis.
- [ ] Implement window filtering.
- [ ] Implement aggregate voting.
- [ ] Add algorithm version config.
- [ ] Run offline detector experiment.
- [ ] Compare with audit before default rollout.

### Phase 4: Review Workflow

- [ ] Add key disagreement query.
- [ ] Add admin list/show commands.
- [ ] Add manual override storage if approved.
- [ ] Add accept catalog/detected/manual actions.
- [ ] Add review workflow tests.

## Risks and Mitigations

### Risk: Catalog key is not always ground truth

Mitigation:

- Treat catalog key as preferred display source, not immutable truth.
- Preserve audio detection and disagreement diagnostics.
- Add review queue and manual override.

### Risk: Range keys are ambiguous

Examples: `F-G`, `G-A`, `D-Eb-F`.

Mitigation:

- Preserve raw display string.
- Store start and end roots.
- Use start root for initial compatibility logic unless product requirements specify
  final-key semantics.
- Flag range keys in review/admin UX.

### Risk: Existing clients rely on `recordings.musical_key`

Mitigation:

- Add effective-key fields instead of changing the meaning of existing fields.
- Keep old fields available through a compatibility window.
- Update clients gradually.

### Risk: New detector increases runtime

Mitigation:

- Keep fast-tier sliding-window detector cheap.
- Reuse already-loaded audio.
- Do not rerun allin1 for key-only reanalysis.
- Version caches so expensive recomputation is controlled.

### Risk: Confidence thresholds are misleading

Mitigation:

- Base user-facing fallback on margin/window agreement, not raw correlation alone.
- Tune thresholds against audit output.
- Keep low-confidence audio keys out of automatic transition/transposition decisions.

## Open Decisions

1. Should range keys use start root, end root, or both for transition compatibility?
   Initial recommendation: display raw range, use start root for song entry and end root
   for transition exit only after transition logic supports both.
2. Should manual key overrides be song-level only in v1, or support recording-level
   overrides immediately?
   Initial recommendation: support both in schema if implementing overrides; otherwise
   defer the override table entirely.
3. Should normalized key columns live only in `songs`, or should `recordings` also get
   normalized detected pitch-class columns?
   Initial recommendation: add detected normalization only if query performance needs it;
   otherwise parse/calculate in helpers from `recordings.musical_key`.
4. Should Android display key source badges?
   Initial recommendation: no for first pass; just display effective key. Admin/web can
   show source and disagreement details.

## Definition of Done

The work is complete when:

- User-facing surfaces prefer scraped catalog keys whenever available and parseable.
- Audio-detected keys are used only as fallback or diagnostic data.
- Effective key source is available in webapp API responses.
- Parser behavior is tested in Python and TypeScript.
- Existing analysis-service output remains backward compatible.
- Detector diagnostics are stored for new analysis results.
- An audit report can compare old detector, new detector, and catalog-prior display
  policy.
- The new detector is not made default until audit results justify it.

