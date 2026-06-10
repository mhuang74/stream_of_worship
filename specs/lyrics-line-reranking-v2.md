# Lyrics Line-Level Re-Ranking Implementation Plan v2

## 1. Problem Statement

Semantic search currently ranks songs purely by song-level cosine similarity (full lyrics embedding vs query). This can miss songs where a specific lyric line is highly relevant to the query, even though the song's overall embedding is less similar. Line-level similarity scores are already computed by `findTopMatchingLines()` but are only used for snippet display — they have zero influence on ranking.

**Goal:** After `semanticSearchSongs()`, use the line-level similarity scores to re-rank results via Reciprocal Rank Fusion (RRF). RRF combines the song-level ranking and the line-level ranking by converting ranks into scores, producing a blended ordering that surfaces songs with strong line matches. This is cheaper than full hybrid search because we only compute line matches for the top-N results we already have.

### Changes from v1

| # | Issue | Fix |
|---|---|---|
| 1 | Example RRF scores were miscalculated, producing wrong final ordering | Corrected arithmetic and final order |
| 2 | `Math.max(...lines.map())` risks call-stack overflow with large arrays | Use `lines[0].lineSimilarity` directly (DB already sorts DESC) |
| 3 | Redundant sort for song-level ranking (input is already sorted) | Assign ranks directly from input index |
| 4 | `similarity` range claimed as `[0,1]` but cosine similarity is `[-1,1]` | Clarified practical vs theoretical range |
| 5 | All-zero line similarities produce arbitrary `rank_line`, degrading results | Skip RRF when line coverage is below threshold; fall back to song-level order |
| 6 | `snippets` entries are already sorted by similarity DESC — `Math.max` is unnecessary | Use `lines[0].lineSimilarity` directly |
| 7 | `QUERY_MODEL` hardcoded while `EMBEDDING_MODEL` reads from env — mismatch causes 0 results | Fix `QUERY_MODEL` to derive from same env var; add as prerequisite |

## 2. Prerequisite: Fix `QUERY_MODEL` / `EMBEDDING_MODEL` Mismatch

**File:** `webapp/src/lib/embedding.ts`

`QUERY_MODEL` is hardcoded as `"text-embedding-3-small"` while `EMBEDDING_MODEL` reads from `SOW_LLM_EMBEDDING_MODEL`. If the env var is overridden, `semanticSearchSongs()` filters by the wrong `model_version` and returns 0 results — making re-ranking moot.

**Fix:** Derive `QUERY_MODEL` from the same source as `EMBEDDING_MODEL`:

```typescript
const EMBEDDING_MODEL = process.env.SOW_LLM_EMBEDDING_MODEL || "text-embedding-3-small";

export const QUERY_MODEL = EMBEDDING_MODEL;
```

This must be done before or alongside the RRF implementation.

## 3. Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Fusion formula | **RRF**: `score = 1/(k + rank_song) + 1/(k + rank_line)` | Rank-based — robust to scale differences; well-studied in IR literature (Cormack et al.); no threshold tuning needed |
| RRF constant k | **60** (hardcoded) | Industry standard; good balance between rank 1 and lower ranks |
| Score exposure | **Keep `similarity` as song-level cosine** | RRF scores are not in [0,1] and would break the `% match` badge. RRF is for ordering only. |
| Internal ranking field | **Add `rrfScore` to `SemanticSearchResult`** | Used for sorting internally; stripped before API response. |
| Over-fetching | **Fetch `limit * 2`** from `semanticSearchSongs()` | Songs just outside top-N may have strong line matches. Over-fetch by 2x, trim after re-ranking. |
| Missing line embeddings | **Assign `rank_line = len(songs) + 1`** (last place) | Songs without line embeddings get poor line rank; song-level rank still contributes. |
| Frontend changes | **None required** | `similarity` field unchanged; `% match` badge continues to work. |
| Low line-coverage fallback | **Skip RRF when < 50% of songs have line matches** | When most songs lack line embeddings, `rank_line` is arbitrary and RRF degrades results. Fall back to song-level order. |
| Max line similarity extraction | **Use `lines[0].lineSimilarity`** | `findTopMatchingLines()` returns lines sorted by similarity DESC. First element is already the max. Avoids `Math.max(...spread)` stack-overflow risk. |

### Similarity Range Clarification

Cosine similarity is theoretically `[-1, 1]`. The DB computes `(1 - cosine_distance)::float` without clamping. For OpenAI `text-embedding-3-small` (normalized to unit length), the practical range is `[0, 1]`. The `% match` badge displays `similarity * 100` and will show a negative value if a pathological embedding produces one. This is pre-existing behavior, not introduced by this spec.

## 4. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser                                                        │
│  SemanticSearch.tsx                                             │
│  POST /api/songs/search/semantic { query, limit }               │
│  (no changes — similarity badge still reads result.similarity)  │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│  Next.js Route Handler                                          │
│  1. Auth check                                                  │
│  2. embedQuery(query) → OpenAI text-embedding-3-small (1536)    │
│  3. semanticSearchSongs(embedding, limit * 2)  ← over-fetch    │
│  4. findTopMatchingLines(queryEmbedding, songIds)               │
│  5. ★ NEW: rrfRerank(songs, snippets, k=60)                    │
│     → if line coverage < 50%, skip (return songs as-is)        │
│     → assign song-level ranks from input order (already sorted) │
│     → build line-level ranking (by max_line_sim DESC)          │
│     → RRF score = 1/(k+rank_song) + 1/(k+rank_line)           │
│     → sort by rrfScore DESC                                     │
│  6. Trim to requested limit, strip rrfScore                    │
│  7. Return { songs, query, total }                              │
└─────────────────────────────────────────────────────────────────┘
```

### Re-Ranking Algorithm

```
Input: songs[] (sorted by song-level similarity DESC from semanticSearchSongs)
Input: snippets Map<songId, {lineText, lineSimilarity}[]>
Input: k = 60

Step 0: Check line coverage
  songsWithLines = count of songs where snippets.has(song.id) && snippets.get(song.id).length > 0
  if songsWithLines / songs.length < 0.5:
    return songs unchanged (skip RRF — line ranking is not meaningful)

Step 1: Assign song-level ranks
  songs are already sorted by similarity DESC from semanticSearchSongs
  rank_song[i] = i + 1  (1-indexed, assigned directly from input order)

Step 2: Compute max_line_sim per song and assign line-level ranks
  For each song:
    lines = snippets.get(song.id)
    if lines && lines.length > 0:
      max_line_sim = lines[0].lineSimilarity  (already sorted DESC by DB)
    else:
      max_line_sim = 0
  Sort songs by max_line_sim DESC → assign rank_line (1-indexed)
  Songs with max_line_sim = 0 get the lowest ranks

Step 3: Compute RRF score per song
  rrfScore = 1/(k + rank_song) + 1/(k + rank_line)

Step 4: Sort by rrfScore DESC, trim to limit
```

### Example (k=60)

| Song | song_sim | rank_song | max_line_sim | rank_line | RRF score | Final order |
|---|---|---|---|---|---|---|
| A | 0.80 | 1 | 0.30 | 4 | 1/61 + 1/64 = 0.03202 | 2nd |
| B | 0.45 | 3 | 0.65 | 1 | 1/63 + 1/61 = 0.03227 | 1st |
| C | 0.50 | 2 | 0.40 | 3 | 1/62 + 1/63 = 0.03200 | 3rd |
| D | 0.35 | 4 | 0.60 | 2 | 1/64 + 1/62 = 0.03175 | 4th |

Song B overtakes A because its line rank is #1, even though its song-level rank is #3. The `similarity` field in the response still shows the original song-level cosine (A=0.80, B=0.45) — only the *ordering* changes.

## 5. Implementation Phases

### Phase 0: Fix `QUERY_MODEL` / `EMBEDDING_MODEL` mismatch

**File:** `webapp/src/lib/embedding.ts`

```typescript
const EMBEDDING_MODEL = process.env.SOW_LLM_EMBEDDING_MODEL || "text-embedding-3-small";

export const QUERY_MODEL = EMBEDDING_MODEL;
```

### Phase 1: Add RRF re-ranking function

**File:** `webapp/src/lib/db/songs.ts`

```typescript
const RRF_K = 60;
const MIN_LINE_COVERAGE = 0.5;

export function rrfRerank(
  songs: SemanticSearchResult[],
  snippets: Map<string, { lineText: string; lineSimilarity: number }[]>,
  k: number = RRF_K,
): SemanticSearchResult[] {
  if (songs.length === 0) return songs;

  const maxLineSimBySong = new Map<string, number>();
  let songsWithLines = 0;
  for (const song of songs) {
    const lines = snippets.get(song.id);
    if (lines && lines.length > 0) {
      maxLineSimBySong.set(song.id, lines[0].lineSimilarity);
      songsWithLines++;
    } else {
      maxLineSimBySong.set(song.id, 0);
    }
  }

  if (songsWithLines / songs.length < MIN_LINE_COVERAGE) {
    return songs;
  }

  const rankSong = new Map<string, number>();
  songs.forEach((song, i) => rankSong.set(song.id, i + 1));

  const rankLine = new Map<string, number>();
  const songsByLineSim = [...songs].sort((a, b) =>
    (maxLineSimBySong.get(b.id) ?? 0) - (maxLineSimBySong.get(a.id) ?? 0),
  );
  songsByLineSim.forEach((song, i) => rankLine.set(song.id, i + 1));

  const lastRank = songs.length + 1;
  const reranked = songs.map((song) => ({
    ...song,
    rrfScore: 1 / (k + (rankSong.get(song.id) ?? lastRank))
            + 1 / (k + (rankLine.get(song.id) ?? lastRank)),
  }));

  reranked.sort((a, b) => b.rrfScore - a.rrfScore);
  return reranked;
}
```

Key differences from v1:
- **No `Math.max(...spread)`**: Uses `lines[0].lineSimilarity` directly (DB returns lines sorted DESC). No call-stack risk.
- **No redundant song-level sort**: `rankSong` assigned from input array index, since `semanticSearchSongs()` already returns sorted by similarity DESC.
- **Line coverage guard**: If < 50% of songs have line matches, returns songs unchanged. Prevents arbitrary `rank_line` from degrading results.
- **Early return on empty input**: Handles `songs.length === 0` explicitly.

### Phase 2: Update `SemanticSearchResult` type

**File:** `webapp/src/lib/db/songs.ts`

```typescript
export interface SemanticSearchResult extends SongWithRecordings {
  similarity: number;
  modelVersion: string;
  matchingSnippet: string | null;
  whyThisMatch: string[];
  rrfScore?: number;
}
```

### Phase 3: Update API route

**File:** `webapp/src/app/api/songs/search/semantic/route.ts`

```typescript
const overfetchLimit = limit * 2;
const songs = await semanticSearchSongs(queryEmbedding, QUERY_MODEL, overfetchLimit);

const snippets = await findTopMatchingLines(
  queryEmbedding,
  songs.map((s) => s.id),
);

const rerankedSongs = rrfRerank(songs, snippets);

const trimmed = rerankedSongs.slice(0, limit);

const songsWithSnippets = trimmed.map(({ rrfScore: _, ...s }) => ({
  ...s,
  matchingSnippet: snippets.get(s.id)?.[0]?.lineText ?? null,
  whyThisMatch: snippets.get(s.id)?.map((l) => l.lineText) ?? [],
}));
```

### Phase 4: Add unit tests

**File:** `webapp/src/test/lib/db/songs.test.ts` (new file)

Test cases for `rrfRerank()`:
- Song with strong line match overtakes song with weak line match
- Song with no line embeddings gets last-place line rank, still ranked by song-level
- Results are sorted by `rrfScore` DESC
- `similarity` field is preserved (not overwritten)
- `rrfScore` is present on returned results
- Trimming to limit works correctly
- Single song: rank_song=1, rank_line=1, rrfScore = 2/(k+1)
- All songs have equal line similarity: ordering matches song-level rank
- All songs have equal song similarity: ordering matches line-level rank
- **Low line coverage (< 50%): returns songs unchanged, no `rrfScore` added**
- **Empty songs array: returns empty array**
- **All songs have no line embeddings: returns songs unchanged (0% coverage)**

Test cases for `QUERY_MODEL` fix:
- `QUERY_MODEL` equals `EMBEDDING_MODEL` when env var is set
- `QUERY_MODEL` defaults to `"text-embedding-3-small"` when env var is unset

## 6. File Change Summary

| File | Change |
|---|---|
| `webapp/src/lib/embedding.ts` | Fix `QUERY_MODEL` to derive from `EMBEDDING_MODEL` instead of hardcoding |
| `webapp/src/lib/db/songs.ts` | Add `rrfRerank()` function; add `rrfScore` to `SemanticSearchResult` |
| `webapp/src/app/api/songs/search/semantic/route.ts` | Over-fetch `limit*2`, call RRF re-ranking, trim to `limit`, strip `rrfScore` |
| `webapp/src/test/lib/db/songs.test.ts` | New file: unit tests for `rrfRerank()` and `QUERY_MODEL` |

## 7. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Over-fetching doubles DB query cost | Catalog is small (~hundreds of songs); pgvector HNSW index makes this negligible. |
| Re-ranking changes existing result order | Intended behavior — better relevance. Similarity % badge still shows song-level cosine. |
| RRF loses score magnitude information | Acceptable: rank-based fusion is more robust when similarity scales are poorly calibrated. |
| Songs without line embeddings get poor line rank | By design — they rely on song-level rank only. Coverage will improve as batch pipeline runs. |
| `rrfScore` accidentally leaked in API response | Destructure to strip in route handler; TypeScript `rrfScore?` is optional. |
| Low line-embedding coverage degrades results | 50% coverage threshold: RRF skipped when line data is too sparse. |
| `QUERY_MODEL` / `EMBEDDING_MODEL` mismatch | Fixed in Phase 0: `QUERY_MODEL` now derives from the same env var. |

## 8. Future Considerations

| Idea | Notes |
|---|---|
| Make k configurable via env var | Add `SOW_RERANK_RRF_K` env var with default 60 |
| Make line coverage threshold configurable | Add `SOW_RERANK_MIN_LINE_COVERAGE` env var |
| Weighted RRF variants | `w1/(k+r1) + w2/(k+r2)` for different song vs line weights |
| Hybrid BM25 + vector search | Full hybrid search combining full-text and semantic results |
| Expose RRF score in API | Add `rerankedScore` field if consumers need blended score |
| A/B test RRF vs weighted average | Run both strategies in parallel to compare relevance |
