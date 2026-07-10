# Update Webapp BPM Filter Bands (align to fixed-detector catalog distribution)

## Overview

The half-time guard fix (`fix-tempo-detection-quantization-v4.md`) corrected false-doublings that had been inflating slow songs to ~130 BPM. After re-analysis, the worship catalog's true tempo distribution is 64.6–107.7 BPM (mean 77.9), with 88% of songs below 90 BPM. The webapp's "Search Songs" BPM filter still uses the legacy bands (`Slow <90`, `Moderate 90–120`, `Fast ≥120`) which lump nearly the entire catalog into "Slow" and leave "Fast" empty.

This plan restructures the BPM filter into 4 bands whose boundaries align with natural gaps in the observed distribution and with the worship-tempo taxonomy (hymn / slow-worship / mid-tempo / upbeat).

| | |
|---|---|
| **Date** | 2026-07-06 |
| **Status** | Plan — pending implementation |
| **Components** | `delivery/webapp/` |
| **Breaking** | None to the API surface. The `bpmRange` query-string param accepts a new value (`upbeat`); existing values (`slow`, `moderate`, `fast`) remain valid but their SQL semantics change (narrower ranges). Clients passing the old literals keep working but match different song sets. |
| **Depends on** | `fix-tempo-detection-quantization-v4.md` deployed and catalog re-analyzed so `recordings.tempo_bpm` reflects true tempos. |

---

## 1. Catalog Distribution (measured)

Source: live query of `recordings.tempo_bpm` for all rows where `tempo_bpm IS NOT NULL`.

```
count: 100   min: 64.6   max: 107.7   mean: 77.9

Histogram (rounded BPM: count):
  65:   1   66:  12   68:  12   70:  13   72:   6
  74:   5   76:   4   78:   5   81:   5   83:   4
  86:  12   89:   9   92:   8   96:   2   99:   1   108:  1

Cumulative:
  ≤70: 38%   ≤75: 49%   ≤80: 58%   ≤85: 67%   ≤90: 88%
  ≤95: 98%   ≤100: 99%   ≤108: 100%
```

### 1.1 Why the legacy bands are wrong now

| Legacy band | Range | Catalog share |
|---|---|---|
| Slow | `< 90` | 88% |
| Moderate | `90 – <120` | 12% |
| Fast | `≥ 120` | 0% |

"Slow" swallows almost the entire catalog, "Moderate" is a thin sliver, and "Fast" is empty (the old "fast" songs were artifacts of the doubled-BPM bug). The filter no longer discriminates usefully.

### 1.2 Natural boundaries

The histogram has clean gaps at 70→72, 80→81, and 90→92. These align with the worship-tempo taxonomy:
- ≤70: contemplative hymns / ballads
- 71–80: slow worship
- 81–90: mid-tempo
- >90: upbeat praise

---

## 2. Design Decisions

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D1 | Band count | 4 (was 3) | 3 bands either over-lump "Slow" (≤75 = 49%) or under-populate "Moderate" (76–90 = 30% with no internal split). 4 bands give balanced buckets (38/20/30/12) and align to worship-tempo taxonomy. |
| D2 | Boundaries | `<70` / `70–<80` / `80–<90` / `≥90` | Aligns with histogram gaps (70→72, 80→81, 90→92). Each band has ≥12 songs; no empty or singleton band. |
| D3 | Band keys | `slow`, `moderate`, `upbeat`, `fast` | Preserves the existing 3 keys (no breaking change to clients passing `slow`/`moderate`/`fast` literals) and adds `upbeat` for the new mid-tempo band. |
| D4 | Labels | "Slow", "Moderate", "Upbeat", "Fast" | Matches the keys; "Upbeat" is the conventional label for the 80–90 mid-tempo band in worship-music taxonomy. |
| D5 | Dedupe `BPM_BANDS` definition | Yes — `search/types.ts` re-exports from `@/lib/constants` | `types.ts` currently duplicates `BPM_BANDS`/`BPM_BAND_KEYS`/`BpmBandKey` from `constants.ts`. Consolidating prevents the drift that already caused this issue. |
| D6 | `StructuredSearchCriteria.bpmRange` type | `BpmBandKey` (was hardcoded `"slow" \| "moderate" \| "fast"` union) | Using the derived type means future band additions update the criteria type automatically. |
| D7 | Predicate hardcoding | Keep `switch` in `buildBpmPredicate` | Matches existing pattern; the `BPM_BANDS` `min`/`max` fields are for UI display, not SQL generation. Keeping the switch explicit makes the SQL auditable. |
| D8 | Migration / backfill | None | `bpmRange` is a query-time filter, not persisted state. No DB migration. Stale URL bookmarks with `?bpmRange=fast` still work but match a different (smaller, slower) set — acceptable. |

---

## 3. Implementation Plan

### Phase A: Update the single source of truth in `constants.ts`

**File**: `delivery/webapp/src/lib/constants.ts` (lines 74–81)

Replace the 3-band definition with the 4-band scheme:

```ts
export const BPM_BANDS = {
  slow: { label: "Slow", max: 70 },
  moderate: { label: "Moderate", min: 70, max: 80 },
  upbeat: { label: "Upbeat", min: 80, max: 90 },
  fast: { label: "Fast", min: 90 },
} as const;

export const BPM_BAND_KEYS = ["slow", "moderate", "upbeat", "fast"] as const;
export type BpmBandKey = (typeof BPM_BAND_KEYS)[number];
```

Key changes:
- `slow.max`: `90` → `70`
- `moderate`: `min 90, max 120` → `min 70, max 80`
- New `upbeat` band: `{ label: "Upbeat", min: 80, max: 90 }`
- `fast.min`: `120` → `90`
- `BPM_BAND_KEYS` gains `"upbeat"` between `"moderate"` and `"fast"`.

### Phase B: Dedupe `search/types.ts`

**File**: `delivery/webapp/src/components/songset/search/types.ts` (lines 1–17)

Replace the local `BPM_BANDS` / `BPM_BAND_KEYS` / `BpmBandKey` definitions with a re-export from the single source of truth, and tighten the `StructuredSearchCriteria.bpmRange` type:

```ts
import type { AlbumFilter } from "@/lib/search/album-filter";
import type { BpmBandKey } from "@/lib/constants";

export type { BpmBandKey } from "@/lib/constants";

export interface StructuredSearchCriteria {
  query?: string;
  keys?: string[];
  bpmRange?: BpmBandKey;
  albums?: AlbumFilter[];
}
```

Key changes:
- Removes the duplicate `BPM_BANDS` / `BPM_BAND_KEYS` / `BpmBandKey` block (lines 10–17).
- `StructuredSearchCriteria.bpmRange` changes from the inline union `"slow" | "moderate" | "fast"` to `BpmBandKey`, so it picks up `"upbeat"` automatically.
- Re-exports `BpmBandKey` so existing imports from `@/components/songset/search/types` (if any) keep resolving. (Audit needed at implementation time; if no external importer uses `types.ts`'s `BpmBandKey`, the re-export can be dropped.)

### Phase C: Update the SQL predicate

**File**: `delivery/webapp/src/lib/db/search-helpers.ts` (lines 124–134, `buildBpmPredicate`)

Update the `switch` to the new boundaries and add the `upbeat` case:

```ts
export function buildBpmPredicate(bpmRange: BpmBandKey, alias: string = "r"): SQL {
  const col = sql.raw(`${alias}.tempo_bpm`);
  switch (bpmRange) {
    case "slow":
      return sql`${col} < 70`;
    case "moderate":
      return sql`${col} >= 70 AND ${col} < 80`;
    case "upbeat":
      return sql`${col} >= 80 AND ${col} < 90`;
    case "fast":
      return sql`${col} >= 90`;
  }
}
```

Key changes:
- `slow`: `< 90` → `< 70`
- `moderate`: `>= 90 AND < 120` → `>= 70 AND < 80`
- New `upbeat` case: `>= 80 AND < 90`
- `fast`: `>= 120` → `>= 90`

### Phase D: No source changes needed (auto-derives)

These files read band definitions generically and require no edits:

- **`src/components/songset/SharedFilters.tsx:142–167`** — chip list rendered via `BPM_BAND_KEYS.map(...)`. The label formatter already handles all three shapes (`max`-only, `min+max`, `min`-only). The new "Upbeat (80–90)" chip appears automatically; the existing chips' range text updates to the new boundaries.
- **`src/components/songset/SongSearch.tsx`**, **`src/components/songset/BrowseSheet.tsx`**, **`src/components/search/SemanticSearch.tsx`** — use `BpmBandKey` / `bpmRange` generically; no literal band keys.
- **`src/lib/db/songs.ts`**, **`src/lib/db/search.ts`** — pass `bpmRange` through to `buildBpmPredicate`; no literal band keys.

---

## 4. Tests

### 4.1 Update `src/test/lib/db/search-helpers.test.ts`

**`buildBpmPredicate` block (lines 99–128)** — update the 3 existing SQL assertions to the new boundaries and add a 4th case for `"upbeat"`:

```ts
describe("buildBpmPredicate", () => {
  it("slow: tempo_bpm < 70 (default alias r)", () => {
    const sqlFragment = buildBpmPredicate("slow");
    const query = dialect.sqlToQuery(sqlFragment);
    expect(query.sql).toContain("r.tempo_bpm");
    expect(query.sql).toContain("< 70");
  });

  it("moderate: 70 <= tempo_bpm < 80 (default alias r)", () => {
    const sqlFragment = buildBpmPredicate("moderate");
    const query = dialect.sqlToQuery(sqlFragment);
    expect(query.sql).toContain("r.tempo_bpm");
    expect(query.sql).toContain(">= 70");
    expect(query.sql).toContain("< 80");
  });

  it("upbeat: 80 <= tempo_bpm < 90 (default alias r)", () => {
    const sqlFragment = buildBpmPredicate("upbeat");
    const query = dialect.sqlToQuery(sqlFragment);
    expect(query.sql).toContain("r.tempo_bpm");
    expect(query.sql).toContain(">= 80");
    expect(query.sql).toContain("< 90");
  });

  it("fast: tempo_bpm >= 90 (default alias r)", () => {
    const sqlFragment = buildBpmPredicate("fast");
    const query = dialect.sqlToQuery(sqlFragment);
    expect(query.sql).toContain("r.tempo_bpm");
    expect(query.sql).toContain(">= 90");
  });

  it("uses custom alias when provided", () => {
    const sqlFragment = buildBpmPredicate("slow", "r3");
    const query = dialect.sqlToQuery(sqlFragment);
    expect(query.sql).toContain("r3.tempo_bpm");
    expect(query.sql).not.toContain("r.tempo_bpm");
  });
});
```

**`isValidBpmBand` block (lines 173–184)** — add `upbeat` to the valid-band assertion:

```ts
it("returns true for valid bands", () => {
  expect(isValidBpmBand("slow")).toBe(true);
  expect(isValidBpmBand("moderate")).toBe(true);
  expect(isValidBpmBand("upbeat")).toBe(true);
  expect(isValidBpmBand("fast")).toBe(true);
});
```

**`parseBpmRangeParam` block (lines 216–221)** — add `upbeat` to the parsed-valid assertion:

```ts
it("parses valid band", () => {
  expect(parseBpmRangeParam("slow")).toBe("slow");
  expect(parseBpmRangeParam("moderate")).toBe("moderate");
  expect(parseBpmRangeParam("upbeat")).toBe("upbeat");
  expect(parseBpmRangeParam("fast")).toBe("fast");
});
```

### 4.2 Tests that remain green (no changes)

- **`src/test/lib/db/search.test.ts`** (lines 235, 274) — uses `"slow"` as a filter value and only asserts that `mockFindMany` was called. `"slow"` is still a valid key. Green.
- **`src/test/api/songs/route.test.ts`**, **`src/test/api/songs/search.test.ts`**, **`src/test/api/songs/search/semantic.test.ts`** — pass `"slow"` / `"fast"` literals as filter values; no SQL assertions. Green.
- **`src/test/components/search/SemanticSearch.test.tsx`** — passes `bpmRange: "slow"`. Green.
- **`src/test/components/songset/SharedFilters.test.tsx`** — clicks `bpm-chip-slow` and asserts `onSelectedBpmChange("slow")`. `"slow"` is still a valid key and the chip still renders. Green.
- **`src/test/components/songset/BrowseSheet.test.tsx`** — clicks `bpm-chip-slow`, expects `bpmRange: "slow"` in the URL params. Green.

### 4.3 Tests not added (deliberately out of scope)

- No new component test for the `upbeat` chip. The chip rendering is driven by `BPM_BAND_KEYS.map(...)`, so a chip-count assertion would be brittle and the existing `bpm-chip-slow` test already covers the render path. If a regression drops `upbeat` from `BPM_BAND_KEYS`, the `isValidBpmBand("upbeat")` and `parseBpmRangeParam("upbeat")` unit tests will catch it at the predicate layer.
- No E2E test for the URL `?bpmRange=upbeat` round-trip. Covered by the `parseBpmRangeParam("upbeat")` unit test.

---

## 5. Files Changed

| File | Change |
|---|---|
| `delivery/webapp/src/lib/constants.ts` | `BPM_BANDS` 3→4 bands with new boundaries; `BPM_BAND_KEYS` gains `"upbeat"`. |
| `delivery/webapp/src/components/songset/search/types.ts` | Remove duplicate `BPM_BANDS`/`BPM_BAND_KEYS`/`BpmBandKey`; re-export `BpmBandKey` from `@/lib/constants`. `StructuredSearchCriteria.bpmRange` type → `BpmBandKey`. |
| `delivery/webapp/src/lib/db/search-helpers.ts` | `buildBpmPredicate` switch: new boundaries + new `"upbeat"` case. |
| `delivery/webapp/src/test/lib/db/search-helpers.test.ts` | Update `buildBpmPredicate` SQL assertions (3 cases) + add `upbeat` case; add `upbeat` to `isValidBpmBand` and `parseBpmRangeParam` valid-band tests. |

No changes to: `SharedFilters.tsx` (renders from `BPM_BAND_KEYS`), `SongSearch.tsx`, `BrowseSheet.tsx`, `SemanticSearch.tsx`, `songs.ts`, `search.ts`, or any component/API test.

---

## 6. Verification

```bash
cd delivery/webapp

# Unit tests for the predicate and helpers
pnpm test src/test/lib/db/search-helpers.test.ts src/test/lib/db/search.test.ts

# Component tests that render the BPM chips
pnpm test src/test/components/songset/SharedFilters.test.tsx \
          src/test/components/songset/BrowseSheet.test.tsx

# Semantic search and API route tests
pnpm test src/test/components/search/SemanticSearch.test.tsx \
          src/test/api/songs

# Lint
pnpm lint
```

Expected: all green. No new failures.

---

## 7. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Bookmarked URL `?bpmRange=fast` now matches 90+ BPM songs instead of 120+ | User sees different (smaller, slower) result set | Acceptable — `fast` is still a valid key. The old 120+ band was empty post-fix anyway, so the user was getting zero results before. |
| Bookmarked URL `?bpmRange=moderate` now matches 70–80 instead of 90–120 | User sees different (slower) result set | Acceptable — same reasoning. The old 90–120 band had only 12% of the catalog; the new 70–80 band has 20%. |
| External client passing `bpmRange=upbeat` to older webapp deployment | 400 / ignored param | `parseBpmRangeParam` returns `undefined` for unknown values on old deployments; the filter is silently dropped. No crash. |
| `types.ts` re-export breaks an importer that imported `BpmBandKey` from `types.ts` by value | Type error | Audit at implementation time: `grep -r "from.*songset/search/types" delivery/webapp/src` — if no importer uses `BpmBandKey`, drop the re-export. If some do, keep it. |
| Future catalog imports bring in true 130+ BPM songs | "Fast" band (≥90) becomes too broad | Re-evaluate boundaries when catalog grows. The 4-band scheme is easy to extend (add a 5th key) or retune (adjust `min`/`max`) without touching the predicate switch if we later derive SQL from `BPM_BANDS` instead of hardcoding. |

---

## 8. Out of Scope

- **Deriving SQL from `BPM_BANDS` instead of hardcoding the switch** (D7). Considered and rejected: the hardcoded switch is auditable and the band count changes rarely. Promote to data-driven only if band tuning becomes frequent.
- **Admin CLI / analysis service BPM display**. The admin CLI's `audio list` table shows the raw `tempo_bpm` value; no banding. Out of scope.
- **Android app**. The Android app does not implement BPM filtering; it consumes the webapp JSON APIs which return raw `tempoBpm`. Out of scope.
- **Re-analysis of the catalog**. Tracked separately in `fix-tempo-detection-quantization-v4.md` Phase B. This plan assumes that re-analysis is complete (or will complete independently).
- **Tempo-band-based transition logic**. The transition engine uses raw `tempoBpm` and `tempoRatio`, not bands. Out of scope.

---

## 9. Changelog from legacy → new

1. `BPM_BANDS` expanded from 3 to 4 entries; boundaries retuned to catalog distribution (D2).
2. New band key `upbeat` added between `moderate` and `fast` (D3).
3. `slow.max` lowered 90 → 70; `moderate` retuned to 70–80; `fast.min` lowered 120 → 90.
4. `search/types.ts` duplicate `BPM_BANDS` definition removed; re-exports from `@/lib/constants` (D5).
5. `StructuredSearchCriteria.bpmRange` type changed from inline union to `BpmBandKey` (D6).
6. `buildBpmPredicate` switch updated with new boundaries + `upbeat` case (Phase C).
7. `search-helpers.test.ts` updated: 3 SQL assertions retuned, 1 new `upbeat` SQL case, `upbeat` added to `isValidBpmBand` and `parseBpmRangeParam` valid-band tests (§4.1).
