# Plan: BPM-CPS Intelligent-Prior POC (Run #5 + LRC CPS)

## Overview

Extend `lab/poc-scripts/compare_bpm_libraries.py` with two new, tightly-coupled
analyses on top of the existing 4-library comparison (librosa_raw, madmom,
beatnet, prod_v4):

1. **Run #5 — `prod_v5`**: A fifth BPM detection run that improves on the
   production v4 path by replacing librosa's flat `start_bpm=80` scalar prior
   with an **intelligent prior distribution** (`scipy.stats.rv_continuous`)
   whose center is derived from the song's own **Characters-Per-Second (CPS)**
   computed from its timestamped lyrics (LRC).

2. **CPS analysis**: Download each song's `lyrics.lrc` from R2, count
   characters (CJK per-char, ASCII per-word, exclude whitespace/punctuation)
   across the **first→last timed LRC line** vocal span, and compute a single
   per-song CPS.

3. **Distribution comparison + empirical cutoffs**: Cluster all per-song CPS
   values into 3 groups via 1-D k-means, label them Slow / Moderate / Fast, and
   compare the resulting empirical cut points against the **nominal cutoffs**
   (< 1.5, 1.5–2.8, > 2.8). Cross-tabulate CPS bucket vs. detected-BPM bucket
   so the correlation between lyrical density and tempo is visible per library.

This is a **research POC** — adoption into the production analyzer is out of
scope (same boundary as the existing v1 spec).

## Background & Motivation

`prod_v4` (the baseline) uses `librosa.feature.rhythm.tempo(...,
start_bpm=80)` followed by an octave guard (double-time when `>120`,
half-time when `<60`). The fixed `start_bpm=80` prior is a coarse anchor — it
biases every song toward the same tempo band regardless of how fast the song
actually is, which is the root cause of the v4 octave-guard's fundamental
ambiguity (see `specs/fix-tempo-detection-quantization-v4.md`).

librosa's `tempo()` accepts an optional `prior: scipy.stats.rv_continuous`.
When provided, `start_bpm` and `std_bpm` are ignored. This lets us shape the
prior with a real distribution (mean + spread) instead of a single point.

CPS (lyrical density) is a tempo-orthogonal signal available from the LRC that
already exists for completed catalog songs. A song with high CPS is almost
certainly fast; a low-CPS song is almost certainly slow. Using CPS to seed the
librosa prior gives run #5 an information advantage that prod_v4 lacks — and
**ties two independent measures of "speed"** (one from the beat, one from the
words) into a single, comparable distribution.

## Design Decisions (confirmed with user)

| Decision | Choice |
|---|---|
| Prior source for run #5 | **CPS-derived prior** (fallback to `start_bpm=80` when LRC missing) |
| Character counting | **CJK per-char, ASCII per-word**, exclude whitespace + punctuation |
| Vocal span | **First → last timed LRC line** (`last_ts − first_ts`) |
| Empirical cutoffs | **1-D k-means, 3 clusters** on per-song CPS, cross-checked vs. nominal |

## Run #5 — `prod_v5` Algorithm

### CPS → expected BPM band mapping

Lyrical density correlates with, but is not identical to, musical tempo. The
mapping below converts CPS into a prior **mean BPM**, with a prior **std**
wide enough to remain a Bayesian prior (not a hard pin) and narrow enough to
disambiguate the v4 octave cases:

| CPS bucket | Nominal label | Prior mean (BPM) | Prior std (BPM) |
|---|---|---|---|
| `cps < 1.5` | Slow | `70` | `12` |
| `1.5 ≤ cps ≤ 2.8` | Moderate | `105` | `15` |
| `cps > 2.8` | Fast | `135` | `15` |

The prior is built as a **lognormal** distribution (tempo is positive and
right-skewed) via `scipy.stats.lognorm`:

```python
from scipy import stats

def cps_to_prior(cps: float | None) -> stats.rv_continuous:
    if cps is None:
        return None  # caller falls back to start_bpm=80
    if cps < 1.5:
        mean, std = 70.0, 12.0
    elif cps <= 2.8:
        mean, std = 105.0, 15.0
    else:
        mean, std = 135.0, 15.0
    # Convert (mean, std) of the underlying BPM scale to lognormal parameters.
    # (Use the standard mean/std -> sigma/mu conversion.)
    var = std ** 2
    mu = math.log(mean ** 2 / math.sqrt(var + mean ** 2))
    sigma = math.sqrt(math.log(1 + var / mean ** 2))
    return stats.lognorm(scale=math.exp(mu), s=sigma)
```

### `timed_prod_v5(y, sr, cps)` — wrapper

Mirrors `timed_prod_v4` signature but takes an extra `cps` argument (already
computed before this call). Reuses the same octave-guard heuristic as v4 so the
**only** difference between v4 and v5 is the prior:

```python
def timed_prod_v5(y, sr, cps):
    t0 = time.perf_counter()
    try:
        onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=HOP)
        prior = cps_to_prior(cps)  # None when LRC missing → start_bpm fallback
        if prior is not None:
            tempo_primary = librosa_tempo(
                onset_envelope=onset_env, sr=sr, hop_length=HOP, prior=prior)
        else:
            tempo_primary = librosa_tempo(
                onset_envelope=onset_env, sr=sr, hop_length=HOP, start_bpm=80)
        # Same octave-guard as v4 (start_bpm=60 / 120 re-estimation is
        # only applied when prior is None, since the prior already encodes
        # the half/double-time belief).
        ...  # identical octave guard, returning LibraryResult(bpm, elapsed)
    except Exception as exc:
        ...  # identical error handling
```

**Octave-guard interaction note**: when a CPS-derived prior is active, the
v4 octave guard is **skipped** — the prior is the v5 signal that resolves the
octave ambiguity, and re-running with scalar `start_bpm` would discard the
prior. When `cps is None` (LRC missing), v5 falls back to the exact v4
behavior (prior=None → start_bpm=80 + octave guard).

## CPS Computation

### LRC retrieval

Per song, after audio download succeeds and before running the 5 libraries:

```python
lrc_text = r2_client.download_lrc_content(song.hash_prefix)  # str | None
cps, cps_meta = compute_cps(lrc_text) if lrc_text else (None, None)
```

`download_lrc_content` is `R2Client.download_lrc_content` at
`ops/admin-cli/src/stream_of_worship/admin/services/r2.py:486`. It returns the
raw LRC string or `None` (404/NoSuchKey). Songs without an LRC are processed
normally but CPS is `None` for them; v5 then falls back to start_bpm=80 — the
script does not skip songs lacking LRC.

### LRC parsing

Reuse the existing parser at
`ops/admin-cli/src/stream_of_worship/admin/services/lrc_parser.py:103`
(`parse_lrc`) returning an `LRCFile` with `.lines: List[LRCLine]` where each
line has `.time_seconds` and `.text`. Already an admin-cli dependency of
`bpm_poc` via `stream-of-worship[postgres]`.

### Character counting — CJK per-char, ASCII per-word

```python
import unicodedata

_WS_OR_PUNCT = lambda ch: ch.isspace() or unicodedata.category(ch).startswith(("P", "S"))

def count_lyric_chars(text: str) -> int:
    """CJK characters count individually; ASCII alphanumeric runs count as 1 token each."""
    count = 0
    ascii_run = 0
    for ch in text:
        if _WS_OR_PUNCT(ch):
            if ascii_run:
                count += 1
                ascii_run = 0
            continue
        if ord(ch) > 0x2E7F:  # CJK and surrounding ranges
            if ascii_run:
                count += 1
                ascii_run = 0
            count += 1
        else:  # ASCII letter/digit
            ascii_run += 1
    if ascii_run:
        count += 1
    return count
```

Threshold `0x2E7F` covers CJK Unified Ideographs, CJK Compatibility,
Bopomofo, Hiragana/Katakana, CJK punctuation, etc. ASCII letters/digits
accumulate into word tokens (e.g. "Hallelujah" → 1).

### CPS formula — first→last vocal span

```python
def compute_cps(lrc_content: str) -> tuple[float | None, dict]:
    try:
        parsed = parse_lrc(lrc_content)
    except ValueError:
        return None, {"reason": "no valid LRC lines"}
    if len(parsed.lines) < 2:
        return None, {"reason": "fewer than 2 timed lines"}
    total_chars = sum(count_lyric_chars(line.text) for line in parsed.lines)
    span = parsed.lines[-1].time_seconds - parsed.lines[0].time_seconds
    if span <= 0:
        return None, {"reason": "non-positive span"}
    cps = total_chars / span
    return cps, {
        "lines": len(parsed.lines),
        "chars": total_chars,
        "span_s": span,
        "first_ts": parsed.lines[0].time_seconds,
        "last_ts": parsed.lines[-1].time_seconds,
    }
```

`cps_meta` is carried alongside the CPS scalar so the per-song table and CSV
can show diagnostics (line count, char count, span).

## Empirical Cutoffs — k-means, 3 clusters

After all songs are processed:

1. Collect `valid_cps = [(song, cps), ...]` for songs whose CPS is not None.
2. If `len(valid_cps) >= 3`, run 1-D k-means (`sklearn.cluster.KMeans(n_clusters=3,
   n_init=10, random_state=42)` on `cps.reshape(-1,1)`).
3. Sort the 3 cluster centers ascending → label them **Slow / Moderate / Fast**.
4. Empirical cut points = midpoints between adjacent sorted centers.
5. Report side-by-side against nominal cutoffs (1.5 / 2.8).

### Cross-tabulation: CPS bucket × BPM bucket

For each library (and for the stored DB BPM), bucket each song into one of
3 BPM bands — Slow (`< 90`), Moderate (`90–120`), Fast (`> 120`) — and report a
3×3 contingency table against the CPS k-means labels. This is the empirical
validation that "high CPS ⟺ high BPM": a diagonal-dominant table supports
using CPS as a prior; a diffuse table weakens that justification.

## Output Format Additions

### Per-song table (extended)

Two new columns are added for the existing 5 libraries, plus a CPS header:

```
Song 1/N: ... (song_id)
  Hash: ... | Stored BPM: ...
  CPS: 2.34  (chars=412, lines=24, span=176.0s, first=0.42s, last=176.42s)
  LRC: lyrics.lrc  |  CPS bucket: Moderate (kmeans cluster #2)

  Library            BPM   Octave*    Time   Prior
  ────────────────  ─────  ────────  ─────  ──────────────
  librosa (raw)     ...
  madmom             ...
  BeatNet           ...
  prod-v4           ...                 start_bpm=80
  prod-v5           ...                 cps(Moderate,105,15)
```

`Prior` column documents what each run used; v1–v4 leave it blank/`—`.

### Aggregate runtime (1 new row)

`prod_v5` added to `LIBRARIES` and to the runtime table, projected-99 line,
and BPM distribution. A `Prior source: CPS (N songs) / start_bpm=80 (M songs)`
summary line shows how many songs had LRC available.

### New section: CPS distribution + empirical cutoffs

```
=== CPS Distribution ===
  N=42 songs with LRC  (57 missing)
  CPS range = [0.78, 4.12]  median = 2.05  mean = 2.14

  k-means 3-cluster cutoffs (sorted ascending):
    Slow      CPS < 1.42   (n=11)   nominal < 1.5
    Moderate  1.42 ≤ CPS < 2.74  (n=22)   nominal 1.5–2.8
    Fast      CPS ≥ 2.74   (n=9)    nominal > 2.8

  Nominal-vs-empirical deltas: lower +0.08, upper +0.06

=== CPS bucket × BPM bucket (stored DB) ===
                 BPM<90   90–120   >120
  Slow             9        2       0
  Moderate         1       18       3
  Fast             0        1       8
  Diagonal mass: 35/42 = 83%  (CPS-BPM agreement)
```

(Repeats the cross-tab for each library column the user is most interested in.)

### CSV columns (extended)

To the existing fieldnames, append:

```
lrc_available, cps, cps_chars, cps_lines, cps_span_s, cps_first_ts, cps_last_ts,
cps_bucket, prod_v5_bpm, prod_v5_sec, prod_v5_prior
```

`prod_v5_prior` is one of `cps_fast`, `cps_moderate`, `cps_slow`, or `fallback_start_bpm_80`.

## Architecture Changes

### `compare_bpm_libraries.py`

```
LIBRARIES = ["librosa_raw", "madmom", "beatnet", "prod_v4", "prod_v5"]
LIBRARY_LABELS = { ..., "prod_v5": "prod-v5" }

# New helpers (added above timed_* wrappers):
compute_cps(lrc_content) -> (cps | None, meta dict)
count_lyric_chars(text) -> int
cps_to_prior(cps | None) -> scipy.stats.rv_continuous | None
cps_bucket_label(cps) -> "slow" | "moderate" | "fast" | None

# New timed wrapper
timed_prod_v5(y, sr, cps) -> LibraryResult

# Existing resolve_songs / download_audio unchanged

# main() loop: after audio download succeeds,
#   lrc_text = r2_client.download_lrc_content(hash_prefix)
#   cps, cps_meta = compute_cps(lrc_text) if lrc_text else (None, None)
#   append to per-song cps_results list
#   run all 5 libraries (v5 gets cps)

# New reporting:
print_cps_distribution(cps_results, all_results, stored_bpms)
print_cps_vs_bpm_crosstab(...)
# Existing print_per_song_table extended with CPS header + Prior column
# Existing write_csv extended with CPS columns + prod_v5 columns
```

### Dependencies — `bpm_poc` extra

Add two dependencies to `lab/poc-scripts/pyproject.toml`:

```toml
bpm_poc = [
    # ...existing entries...
    "scipy>=1.10.0",                  # already present
    "scikit-learn>=1.3.0",            # NEW — for KMeans clustering
]
```

`scipy` is already in the extra (used for `stats.lognorm`). The parser
`stream_of_worship.admin.services.lrc_parser.parse_lrc` is available without a
new dependency because `stream-of-worship[postgres]` is a base dependency of
`bpm_poc`.

### Imports added at the top of the script

```python
import math
import unicodedata
from scipy import stats
from stream_of_worship.admin.services.lrc_parser import parse_lrc
```

`sklearn.cluster.KMeans` is imported lazily inside `print_cps_distribution`
(only needed for the aggregate step, keeps per-song path light).

## Run Command

Unchanged from the v1 spec:

```bash
uv run --project lab/poc-scripts --extra bpm_poc python lab/poc-scripts/compare_bpm_libraries.py
# ... --all-catalog --limit 99   (Phase 2)
```

## Implementation Phases

### Phase 1 — pyproject
- Add `scikit-learn>=1.3.0` to the `bpm_poc` extra in `lab/poc-scripts/pyproject.toml`.
- Verify install:
  `uv run --project lab/poc-scripts --extra bpm_poc python -c "import sklearn; from scipy import stats; from stream_of_worship.admin.services.lrc_parser import parse_lrc; print('ok')"`.

### Phase 2 — script changes
1. Add `prod_v5` to `LIBRARIES` / `LIBRARY_LABELS`.
2. Add `count_lyric_chars`, `compute_cps`, `cps_to_prior`, `cps_bucket_label`.
3. Add `timed_prod_v5(y, sr, cps)`.
4. In `main()`: after a successful audio download, fetch LRC content via
   `r2_client.download_lrc_content(hash_prefix)`; compute CPS; collect into
   a `cps_results: list[dict]` aligned with `all_results`.
5. Pass `cps` to `timed_prod_v5`; all other library calls unchanged.
6. Extend `print_per_song_table` with the CPS header line + a new `Prior`
   column; extend `print_aggregate_runtime` and `print_bpm_distribution` to
   include `prod_v5`.
7. Add `print_cps_distribution(cps_results, all_results, stored_bpms)` (k-means
   cutoffs + nominal comparison + cross-tab).
8. Extend `write_csv` with the new CPS + v5 columns; update `col_map`.

### Phase 3 — run & verify
- Run on the 3 default POC songs (all 3 have LRC in R2); confirm CPS values
  are sensible (1.5–3.0 range typical for worship music), v5 prior column
  prints, k-means on n=3 yields trivially 3 singletons (warning expected and
  acceptable for Phase 1 — the cut-point machinery is what matters).
- Run `--all-catalog --limit 20` to validate the k-means path with enough
  points to produce non-degenerate cut points.
- Confirm CSV columns present and the v5 fallback path fires when an LRC is
  intentionally pointed at a song lacking lyrics (e.g. retry on a known
  no-LRC song from the catalog).

## Files Changed

| File | Change |
|---|---|
| `lab/poc-scripts/pyproject.toml` | Add `scikit-learn>=1.3.0` to `bpm_poc` extra |
| `lab/poc-scripts/compare_bpm_libraries.py` | Add `prod_v5` run, CPS pipeline, k-means cutoffs, CPS×BPM cross-tab, extended CSV/per-song/runtime/distribution output |

No changes to: analysis-service, admin-cli, the LRC parser, R2Client, or any
production code path. The LRC parser is imported read-only.

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| LRC missing for many catalog songs | v5 falls back to `start_bpm=80` (identical to v4); CPS section reports N-with-LRC vs N-missing so the reader is not misled. |
| n=3 Phase 1 run makes k-means trivial | Expected; documented in output. Phase 2 catalog run is the real measurement. |
| CPS→BPM prior means (70/105/135) are guesses | They are prior *centers*, not exact pins; std=12–15 keeps the prior soft. The cross-tab validates the choice post-hoc. If diagonal agreement < 60%, the plan's tuning parameter is the prior mean table, not the algorithm. |
| lognormal param conversion off-by-one | Use the textbook mean/std→(mu,sigma) conversion (shown in code); covered by a `scipy.stats.lognorm.mean()`/`.std()` round-trip assertion in Phase 2. |
| CJK detection threshold `0x2E7F` misclassifies rare punctuation | The `_WS_OR_PUNCT` category check runs first, so Unicode P*/S* categories are already excluded before the CJK threshold is consulted — robust to width-specific entries. |
| `scipy.stats` freeze of lognorm with `scale=exp(mu), s=sigma` returns a frozen rv_continuous that librosa accepts | Verified by librosa docstring: "prior : scipy.stats.rv_continuous [optional]". Phase 3 includes a one-line smoke test passing the prior to `librosa_tempo`. |
| sklearn import bloats startup | Lazy-import inside `print_cps_distribution` only (aggregate step). |

## Out of Scope

- **Adoption into the production analyzer** — separate spec, informed by this
  POC's CPS×BPM agreement table.
- **Word-level LRC parsing** — the existing LRC is line-timestamped only; CPS
  is computed at line granularity (chars per span), not per-word duration.
- **Beat/downbeat arrays** — BPM-only, consistent with v1 spec.
- **Multilingual CPS calibration** — the CJK/ASCII heuristic covers
  Chinese/English; full-CJK-only calibration is a future refinement.
- **Per-song prior mean tuning** — the 70/105/135 table is shared across all
  songs; per-song regression from CPS→BPM is a future improvement.
