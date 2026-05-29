# Semantic Search Implementation Plan

## 1. Problem Statement

The "Describe" tab in the Browse Sheet is broken — semantic search always returns a 400 error. Three root causes:

1. **Frontend-backend mismatch**: `SemanticSearch.tsx` sends `{ query: string, limit: 20 }` but the API route expects `{ recordingId: string, limit: 20 }` (Zod validation fails on `query` field).
2. **No embedding data**: The `song_embedding` table is empty — no code in the codebase populates it.
3. **Schema mismatch with spec**: Table is keyed by `recording_content_hash` with `vector(1024)`, but spec v4 §2.6 says keyed by `song_id` with embedding content = `title + composer + lyrics_raw`.

## 2. Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Embedding model | **OpenAI `text-embedding-3-small`** (1536 dims) | Simplest to implement; no ONNX bundle size issues; $0.02/1M tokens; same model for batch and query ensures vector space compatibility |
| Embedding key | **`song_id`** (per spec v4) | Embedding content is text-based (title+composer+lyrics), not audio-level; one embedding per song |
| Vector dimensions | **1536** (OpenAI default) | Best recall; storage is trivial for our catalog size (~hundreds of songs) |
| Query-time embedding | **External API** (OpenAI) | No Vercel bundle size concerns; no cold start; no ONNX runtime needed |
| Batch embedding | **Admin CLI → Analysis Service → OpenAI** | Follows existing job dispatch pattern; Analysis Service already runs in Docker; no GPU needed for text embedding |
| Schema migration | **Clean rebuild** (drop + recreate) | Table is currently empty; no data loss |
| Snippet features | **Full spec** (matchingSnippet + whyThisMatch) | Spec v4 §5.2a requires "top matching lyric line" and "Why this match?" expand |
| Snippet source | **`songs.lyrics_lines`** (JSON array in DB) | Already in the songs table; no R2 fetch needed at query time |

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser                                                        │
│  SemanticSearch.tsx                                             │
│  POST /api/songs/search/semantic { query, limit }              │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│  Next.js Route Handler                                          │
│  1. Auth check                                                  │
│  2. embedQuery(query) → OpenAI text-embedding-3-small (1536)   │
│  3. semanticSearchSongs(embedding, limit) → pgvector <=>        │
│  4. For each result song:                                       │
│     a. Parse lyrics_lines from songs table                      │
│     b. embedLines(lines) → OpenAI batch embedding               │
│     c. cosineSimilarity(query_emb, line_emb) → top 2 lines     │
│  5. Return { songs, query, total }                               │
└──────────────────────────┬──────────────────────────────────────┘
                           │
              ┌────────────┼────────────┐
              ▼                         ▼
     ┌────────────────┐      ┌──────────────────┐
     │  Neon Postgres  │      │  OpenAI API       │
     │  + pgvector     │      │  text-embedding-  │
     │  song_embedding │      │  3-small          │
     │  (1536-dim)     │      │  $0.02/1M tokens  │
     └────────────────┘      └──────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  Offline: Batch Embedding Pipeline                              │
│                                                                 │
│  sow-admin audio embed [--all] [--force]                        │
│    → reads songs from Neon (title, composer, lyrics_raw)        │
│    → submits EMBEDDING job to Analysis Service                  │
│    → Analysis Service calls OpenAI API                           │
│    → Admin CLI writes embedding to song_embedding table         │
│                                                                 │
│  sow-admin audio batch (extended)                               │
│    → after LRC generation, auto-submits embedding job           │
│      if song has no embedding yet                               │
└─────────────────────────────────────────────────────────────────┘
```

## 4. Implementation Phases

### Phase 1: Schema Migration — `song_embedding` table rebuild

**Why:** Current table is keyed by `recording_content_hash` with `vector(1024)`. Spec v4 says `song_id` with 1536 dims. Table is empty, so clean rebuild is safe.

**Files to change:**

| File | Change |
|---|---|
| `webapp/src/db/schema.ts` | Rewrite `songEmbeddings` table: PK = `songId`, `embedding vector(1536)`, `modelVersion` default `"openai-text-embedding-3-small"`, drop `recordingContentHash` FK |
| `webapp/src/db/schema.ts` | Update `songEmbeddingsRelations` to reference `songs.id` instead of `recordings.contentHash` |
| `webapp/drizzle/` | Generate migration via `npx drizzle-kit generate` |

**New Drizzle schema:**

```typescript
export const songEmbeddings = pgTable(
  "song_embedding",
  {
    songId: text("song_id")
      .primaryKey()
      .references(() => songs.id, { onDelete: "cascade" }),
    embedding: vector("embedding", { dimensions: 1536 }).notNull(),
    modelVersion: text("model_version")
      .notNull()
      .default("openai-text-embedding-3-small"),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
  },
  (t) => [
    index("idx_song_embedding_cosine").on(
      sql`${t.embedding} vector_cosine_ops`
    ),
  ]
);

export const songEmbeddingsRelations = relations(songEmbeddings, ({ one }) => ({
  song: one(songs, {
    fields: [songEmbeddings.songId],
    references: [songs.id],
  }),
}));
```

**New SQL migration:**

```sql
DROP TABLE IF EXISTS song_embedding;

CREATE TABLE song_embedding (
  song_id       TEXT PRIMARY KEY REFERENCES songs(id) ON DELETE CASCADE,
  embedding     vector(1536) NOT NULL,
  model_version TEXT NOT NULL DEFAULT 'openai-text-embedding-3-small',
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_song_embedding_cosine ON song_embedding
  USING hnsw (embedding vector_cosine_ops);
```

**HNSW index** instead of current B-tree — critical for pgvector cosine similarity performance. Gives sub-millisecond search on thousands of vectors.

---

### Phase 2: Batch Embedding Generation — Analysis Service

**Why:** No code populates `song_embedding`. Need an offline pipeline that generates embeddings for all songs and writes them to Neon.

#### 2a. Analysis Service — new `EMBEDDING` job type

**Files to change:**

| File | Change |
|---|---|
| `services/analysis/src/sow_analysis/models.py` | Add `JobType.EMBEDDING = "embedding"`, `EmbeddingJobRequest`, `EmbeddingJobResult` |
| `services/analysis/src/sow_analysis/workers/queue.py` | Add `elif job.type == JobType.EMBEDDING` dispatch |
| `services/analysis/src/sow_analysis/workers/embedder.py` (new) | `EmbeddingWorker` class: calls OpenAI `text-embedding-3-small` |
| `services/analysis/src/sow_analysis/routes/jobs.py` | Add `POST /api/v1/jobs/embedding` endpoint |
| `services/analysis/pyproject.toml` | Add `openai` dependency |

**New models:**

```python
class JobType(str, Enum):
    ANALYZE = "analyze"
    LRC = "lrc"
    STEM_SEPARATION = "stem_separation"
    EMBEDDING = "embedding"  # NEW

class EmbeddingJobRequest(BaseModel):
    song_id: str
    title: str
    composer: str = ""
    lyrics_raw: str = ""

class EmbeddingJobResult(BaseModel):
    song_id: str
    embedding: List[float]       # 1536-dim
    model_version: str = "openai-text-embedding-3-small"
```

**New worker (`embedder.py`):**

```python
import os
from openai import OpenAI

class EmbeddingWorker:
    def __init__(self):
        self._client = OpenAI(api_key=os.environ.get("SOW_OPENAI_API_KEY"))

    async def embed_song(self, title: str, composer: str, lyrics_raw: str) -> list[float]:
        text = f"{title} {composer} {lyrics_raw}".strip()
        response = await self._client.embeddings.create(
            model="text-embedding-3-small",
            input=text,
            dimensions=1536,
        )
        return response.data[0].embedding
```

**Concurrency:** The embedding job calls an external API (no GPU), so it doesn't need the `_local_model_semaphore`. Add a separate `_embedding_semaphore` with `max_concurrent=5` to respect OpenAI rate limits.

**Queue dispatch addition:**

```python
# In _process_job_with_semaphore():
elif job.type == JobType.EMBEDDING:
    await self._process_embedding_job(job)
```

**New route:**

```python
@router.post("/jobs/embedding")
async def create_embedding_job(request: EmbeddingJobRequest, ...):
    job = await job_queue.submit(JobType.EMBEDDING, request)
    return {"job_id": job.id, "status": job.status}
```

#### 2b. Admin CLI — new `audio embed` command

**Files to change:**

| File | Change |
|---|---|
| `src/stream_of_worship/admin/services/analysis.py` | Add `submit_embedding()` method to `AnalysisClient` |
| `src/stream_of_worship/admin/commands/audio.py` | Add `embed` command; extend `batch` command |
| `src/stream_of_worship/admin/db/client.py` | Add `upsert_song_embedding()`, `get_songs_without_embeddings()` |
| `src/stream_of_worship/admin/db/schema.py` | Add `song_embedding` table DDL (idempotent) |
| `src/stream_of_worship/admin/db/models.py` | Add `SongEmbedding` dataclass |

**`AnalysisClient.submit_embedding()`:**

```python
def submit_embedding(self, song_id: str, title: str, composer: str, lyrics_raw: str) -> JobInfo:
    payload = {
        "song_id": song_id,
        "title": title,
        "composer": composer,
        "lyrics_raw": lyrics_raw,
    }
    resp = self._post("/api/v1/jobs/embedding", payload)
    return JobInfo(job_id=resp["job_id"], status=resp["status"])
```

**`audio embed` command:**

```
sow-admin audio embed <song_id>          # embed a single song
sow-admin audio embed --all              # embed all songs without embeddings
sow-admin audio embed --all --force      # re-embed everything (model upgrade)
sow-admin audio embed --all --wait       # wait for all jobs to complete
```

**Flow:**
1. Read song from Neon DB (title, composer, lyrics_raw)
2. Submit EMBEDDING job to Analysis Service
3. If `--wait`, poll until complete
4. Write embedding to `song_embedding` table via `upsert_song_embedding()`

**`batch` extension:** After LRC generation completes for a recording, if the parent song has no embedding, submit an embedding job. This ensures embeddings are generated as part of the normal catalog pipeline.

**`DatabaseClient` additions:**

```python
def upsert_song_embedding(self, song_id: str, embedding: list[float], model_version: str):
    emb_str = "[" + ",".join(str(v) for v in embedding) + "]"
    self._execute("""
        INSERT INTO song_embedding (song_id, embedding, model_version)
        VALUES (%s, %s::vector, %s)
        ON CONFLICT (song_id) DO UPDATE
        SET embedding = EXCLUDED.embedding,
            model_version = EXCLUDED.model_version,
            created_at = NOW()
    """, (song_id, emb_str, model_version))

def get_songs_without_embeddings(self) -> list[Song]:
    rows = self._execute("""
        SELECT s.id, s.title, s.composer, s.lyrics_raw
        FROM songs s
        LEFT JOIN song_embedding se ON s.id = se.song_id
        WHERE se.song_id IS NULL
          AND s.deleted_at IS NULL
          AND s.lyrics_raw IS NOT NULL
    """)
    return [Song(id=r[0], title=r[1], composer=r[2], lyrics_raw=r[3]) for r in rows]
```

---

### Phase 3: Query-Time Embedding — OpenAI API in Webapp

**Why:** The frontend sends `{ query: "God's faithfulness" }` — we need to embed that text into the same 1536-dim space as the song embeddings.

**Files to change:**

| File | Change |
|---|---|
| `webapp/.env.example` | Add `SOW_OPENAI_API_KEY=` |
| `webapp/.env.production.example` | Add `SOW_OPENAI_API_KEY=` with documentation |
| `webapp/package.json` | Add `openai` dependency |
| `webapp/next.config.ts` | Add `"openai"` to `serverExternalPackages` |
| `webapp/src/lib/embedding.ts` (new) | `embedQuery()` and `embedLines()` functions |

**`webapp/src/lib/embedding.ts`:**

```typescript
import OpenAI from "openai";

const openai = new OpenAI({ apiKey: process.env.SOW_OPENAI_API_KEY });

const MODEL = "text-embedding-3-small";
const DIMENSIONS = 1536;

export async function embedQuery(text: string): Promise<number[]> {
  const response = await openai.embeddings.create({
    model: MODEL,
    input: text,
    dimensions: DIMENSIONS,
  });
  return response.data[0].embedding;
}

export async function embedLines(
  lines: string[]
): Promise<number[][]> {
  if (lines.length === 0) return [];
  const response = await openai.embeddings.create({
    model: MODEL,
    input: lines,
    dimensions: DIMENSIONS,
  });
  return response.data
    .sort((a, b) => a.index - b.index)
    .map((d) => d.embedding);
}

export function cosineSimilarity(a: number[], b: number[]): number {
  let dot = 0;
  let normA = 0;
  let normB = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    normA += a[i] ** 2;
    normB += b[i] ** 2;
  }
  return dot / (Math.sqrt(normA) * Math.sqrt(normB));
}
```

**Error handling:** If `SOW_OPENAI_API_KEY` is not set or the API call fails, the route handler returns `{ error: "Semantic search unavailable. Try Search mode." }` with status 503 — matching spec v4 §5.2a.

**Cost:** text-embedding-3-small is $0.02/1M tokens. A typical query is ~10 tokens → ~$0.0000002/query. A batch of 600 lyric lines (~3000 tokens) → ~$0.00006. Negligible.

---

### Phase 4: Fix API Route — Accept `query` Instead of `recordingId`

**Why:** Frontend sends `{ query, limit }`, API expects `{ recordingId, limit }`. This is the immediate cause of the 400 error.

**Files to change:**

| File | Change |
|---|---|
| `webapp/src/app/api/songs/search/semantic/route.ts` | Rewrite: accept `{ query, limit }`, call `embedQuery()`, then `semanticSearchSongs()` |
| `webapp/src/lib/db/search.ts` | Remove `getEmbeddingForRecording` (no longer needed), update re-exports |

**New route handler:**

```typescript
import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { embedQuery, embedLines, cosineSimilarity } from "@/lib/embedding";
import { semanticSearchSongs } from "@/lib/db/songs";
import { z } from "zod";

const RequestSchema = z.object({
  query: z.string().min(1, "query must not be empty"),
  limit: z.number().int().min(1).max(50).default(20),
});

export async function POST(request: NextRequest) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    let body: unknown;
    try {
      body = await request.json();
    } catch {
      return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
    }

    const parsed = RequestSchema.safeParse(body);
    if (!parsed.success) {
      return NextResponse.json(
        { error: parsed.error.issues[0]?.message ?? "Invalid request" },
        { status: 400 }
      );
    }

    const { query, limit } = parsed.data;

    let queryEmbedding: number[];
    try {
      queryEmbedding = await embedQuery(query);
    } catch {
      return NextResponse.json(
        { error: "Semantic search unavailable. Try Search mode." },
        { status: 503 }
      );
    }

    const songs = await semanticSearchSongs(queryEmbedding, limit);

    // Compute matchingSnippet and whyThisMatch for each result
    const songsWithSnippets = await computeSnippets(songs, queryEmbedding);

    return NextResponse.json({
      songs: songsWithSnippets,
      query,
      total: songsWithSnippets.length,
    });
  } catch (error) {
    console.error("Error in semantic search:", error);
    const message =
      error instanceof Error ? error.message : "Semantic search failed";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
```

**Snippet computation helper (in route or extracted to `lib/embedding.ts`):**

```typescript
async function computeSnippets(
  songs: SemanticSearchResult[],
  queryEmbedding: number[]
): Promise<SemanticSearchResult[]> {
  // Collect all lyric lines across all result songs
  const allLines: { songIndex: number; lineIndex: number; text: string }[] = [];
  for (let i = 0; i < songs.length; i++) {
    const lines = parseLyricsLines(songs[i].lyricsLines);
    for (let j = 0; j < lines.length; j++) {
      allLines.push({ songIndex: i, lineIndex: j, text: lines[j] });
    }
  }

  if (allLines.length === 0) {
    return songs.map((s) => ({
      ...s,
      matchingSnippet: null,
      whyThisMatch: [],
    }));
  }

  // Batch embed all lines in one OpenAI call
  const lineTexts = allLines.map((l) => l.text);
  const lineEmbeddings = await embedLines(lineTexts);

  // For each song, find top 2 lines by cosine similarity to query
  const results = songs.map((s) => ({
    ...s,
    matchingSnippet: null as string | null,
    whyThisMatch: [] as string[],
  }));

  const songLineScores: Map<number, { line: string; score: number }[]> =
    new Map();

  for (let i = 0; i < allLines.length; i++) {
    const { songIndex, text } = allLines[i];
    const score = cosineSimilarity(queryEmbedding, lineEmbeddings[i]);
    if (!songLineScores.has(songIndex)) {
      songLineScores.set(songIndex, []);
    }
    songLineScores.get(songIndex)!.push({ line: text, score });
  }

  for (const [songIndex, scores] of songLineScores) {
    scores.sort((a, b) => b.score - a.score);
    const top2 = scores.slice(0, 2);
    results[songIndex].matchingSnippet = top2[0]?.line ?? null;
    results[songIndex].whyThisMatch = top2.map((t) => t.line);
  }

  return results;
}

function parseLyricsLines(lyricsLines: string | null): string[] {
  if (!lyricsLines) return [];
  try {
    const parsed = JSON.parse(lyricsLines);
    return Array.isArray(parsed) ? parsed.filter((l) => typeof l === "string" && l.trim()) : [];
  } catch {
    return [];
  }
}
```

---

### Phase 5: Update `semanticSearchSongs` SQL Query

**Why:** Current query joins through `song_embedding → recordings → songs` (keyed by recording_content_hash). New schema keys by `song_id` directly, so the query simplifies. Also need to return `lyrics_lines` for snippet matching.

**Files to change:**

| File | Change |
|---|---|
| `webapp/src/lib/db/songs.ts` | Rewrite `semanticSearchSongs`: join on `song_id`, validate 1536 dims, include `lyrics_lines` in result |

**Updated `SemanticSearchResult` interface:**

```typescript
export interface SemanticSearchResult extends SongWithRecordings {
  similarity: number;
  lyricsLines: string | null;  // JSON string, for snippet matching
  matchingSnippet: string | null;
  whyThisMatch: string[];
}
```

**New SQL:**

```sql
SELECT * FROM (
  SELECT DISTINCT ON (s.id)
    s.id,
    s.title,
    s.title_pinyin,
    s.composer,
    s.lyricist,
    s.album_name,
    s.album_series,
    s.musical_key,
    s.lyrics_lines,
    s.created_at,
    s.updated_at,
    r.content_hash,
    r.hash_prefix,
    r.original_filename,
    r.duration_seconds,
    r.tempo_bpm,
    r.musical_key  AS recording_musical_key,
    r.musical_mode,
    r.loudness_db,
    r.r2_audio_url,
    r.r2_lrc_url,
    r.visibility_status,
    r.analysis_status,
    (1 - (se.embedding <=> $1::vector))::float AS similarity
  FROM song_embedding se
  JOIN songs s ON se.song_id = s.id
  JOIN recordings r ON r.song_id = s.id
    AND r.visibility_status = 'published'
    AND r.deleted_at IS NULL
  WHERE s.deleted_at IS NULL
  ORDER BY s.id, se.embedding <=> $1::vector ASC
) ranked
ORDER BY similarity DESC
LIMIT $2
```

**Validation update:** Change dimension check from 1024 to 1536:

```typescript
if (embedding.length !== 1536) {
  throw new Error(
    `Invalid embedding: expected 1536 dimensions, got ${embedding.length}`
  );
}
```

**Result mapping update:** Add `lyricsLines` to the mapped result:

```typescript
return resultRows.map((row) => ({
  // ... existing fields ...
  lyricsLines: (row.lyrics_lines as string | null) ?? null,
  similarity: Number(row.similarity),
  matchingSnippet: null,   // populated by computeSnippets() in route handler
  whyThisMatch: [],        // populated by computeSnippets() in route handler
  recordings: [ /* ... */ ],
}));
```

---

### Phase 6: Update Frontend Component

**Files to change:**

| File | Change |
|---|---|
| `webapp/src/components/search/SemanticSearch.tsx` | Update result interface, render snippet + "Why this match?" expand |

**Updated `SemanticSearchResult` interface in component:**

```typescript
interface SemanticSearchResult extends SongCardData {
  similarity: number;
  matchingSnippet: string | null;
  whyThisMatch: string[];
}
```

**UI per spec v4 §5.2a:**

```
How Great Is Our God       [+]
▸ "…for great is our God…"     ← matchingSnippet
G major · 72 BPM · 4:32  ✦91%

▾ Great Are You Lord       [+]
▸ Lyric 1: "…all my hope…"     ← whyThisMatch[0]
  Lyric 2: "…praise to the…"   ← whyThisMatch[1]
C major · 80 BPM · 3:45  ✦85%
```

**Component changes:**
- Add `matchingSnippet` display below song title (with `▸` prefix, italicized)
- Add expandable "Why this match?" section: tap row to expand, shows `whyThisMatch[0]` and `whyThisMatch[1]`
- Similarity badge already exists (✦ icon + percentage)
- Error state: show "Semantic search unavailable. Try Search mode." when API returns 503

---

### Phase 7: Remove Dead Code

**Files to change:**

| File | Change |
|---|---|
| `webapp/src/lib/db/search.ts` | Remove `getEmbeddingForRecording()` function and its import of `songEmbeddings` |
| `webapp/src/lib/db/search.ts` | Remove re-export of `semanticSearchSongs` from `./songs` (callers import directly) |

---

### Phase 8: Update Tests

**Files to change:**

| File | Change |
|---|---|
| `webapp/src/test/api/songs/search/semantic.test.ts` | Rewrite: mock `embedQuery` instead of `getEmbeddingForRecording`; test `{ query, limit }` request shape; test OpenAI failure → 503; test snippet matching |
| `webapp/src/test/components/search/SemanticSearch.test.tsx` | Update mock API response to include `matchingSnippet`, `whyThisMatch`; test snippet rendering; test "Why this match?" expand |
| `webapp/src/test/lib/db/search.test.ts` | Remove `getEmbeddingForRecording` tests; add `semanticSearchSongs` 1536-dim validation tests |
| `webapp/src/test/db/schema.test.ts` | Update `songEmbeddings` schema tests: PK is `songId`, dims is 1536 |

**Key test cases for route:**

| Test | Input | Expected |
|---|---|---|
| Not authenticated | No session | 401 |
| Invalid JSON | Malformed body | 400 |
| Missing `query` | `{ limit: 20 }` | 400 |
| Empty `query` | `{ query: "", limit: 20 }` | 400 |
| OpenAI API failure | `embedQuery` throws | 503 + "Semantic search unavailable" |
| Success | `{ query: "God's faithfulness", limit: 20 }` | 200 + songs with similarity, snippets |
| Custom limit | `{ query: "test", limit: 5 }` | 200, max 5 results |
| Limit > 50 | `{ query: "test", limit: 100 }` | 400 (Zod rejects) |

---

### Phase 9: Environment & Deployment

**Files to change:**

| File | Change |
|---|---|
| `webapp/.env.example` | Add `SOW_OPENAI_API_KEY=` |
| `webapp/.env.production.example` | Add `SOW_OPENAI_API_KEY=` with documentation |
| `webapp/next.config.ts` | Add `"openai"` to `serverExternalPackages` |

**Analysis Service env:**

| Variable | Purpose |
|---|---|
| `SOW_OPENAI_API_KEY` | OpenAI API key for batch embedding generation |

**Deployment sequence:**

1. Run `npx drizzle-kit generate` → new migration file for `song_embedding` rebuild
2. Run `npx drizzle-kit push` against Neon (dev) or `migrate` (prod)
3. Add `SOW_OPENAI_API_KEY` to Vercel environment variables
4. Add `SOW_OPENAI_API_KEY` to Analysis Service environment
5. Deploy Analysis Service with new `EMBEDDING` job type
6. Run `sow-admin audio embed --all --wait` to populate `song_embedding` table
7. Deploy webapp with new API route + OpenAI integration
8. Verify semantic search works end-to-end in "Describe" tab

---

## 5. Complete File Change Summary

| # | File | Phase | Change Type |
|---|---|---|---|
| 1 | `webapp/src/db/schema.ts` | 1 | Modify: rewrite `songEmbeddings` table + relations |
| 2 | `webapp/drizzle/` (new migration) | 1 | Generate: drop/recreate `song_embedding` with HNSW index |
| 3 | `services/analysis/src/sow_analysis/models.py` | 2a | Modify: add `EMBEDDING` job type, request/result models |
| 4 | `services/analysis/src/sow_analysis/workers/embedder.py` | 2a | **New**: `EmbeddingWorker` class |
| 5 | `services/analysis/src/sow_analysis/workers/queue.py` | 2a | Modify: add `EMBEDDING` dispatch + semaphore |
| 6 | `services/analysis/src/sow_analysis/routes/jobs.py` | 2a | Modify: add `POST /api/v1/jobs/embedding` |
| 7 | `services/analysis/pyproject.toml` | 2a | Modify: add `openai` dependency |
| 8 | `src/stream_of_worship/admin/services/analysis.py` | 2b | Modify: add `submit_embedding()` |
| 9 | `src/stream_of_worship/admin/commands/audio.py` | 2b | Modify: add `embed` command, extend `batch` |
| 10 | `src/stream_of_worship/admin/db/client.py` | 2b | Modify: add embedding CRUD methods |
| 11 | `src/stream_of_worship/admin/db/schema.py` | 2b | Modify: add `song_embedding` DDL |
| 12 | `src/stream_of_worship/admin/db/models.py` | 2b | Modify: add `SongEmbedding` dataclass |
| 13 | `webapp/.env.example` | 3 | Modify: add `SOW_OPENAI_API_KEY` |
| 14 | `webapp/.env.production.example` | 3 | Modify: add `SOW_OPENAI_API_KEY` |
| 15 | `webapp/package.json` | 3 | Modify: add `openai` dependency |
| 16 | `webapp/next.config.ts` | 3 | Modify: add `"openai"` to `serverExternalPackages` |
| 17 | `webapp/src/lib/embedding.ts` | 3 | **New**: `embedQuery()`, `embedLines()`, `cosineSimilarity()` |
| 18 | `webapp/src/app/api/songs/search/semantic/route.ts` | 4 | Rewrite: accept `{ query, limit }`, call `embedQuery()`, compute snippets |
| 19 | `webapp/src/lib/db/search.ts` | 7 | Modify: remove `getEmbeddingForRecording` |
| 20 | `webapp/src/lib/db/songs.ts` | 5 | Modify: rewrite `semanticSearchSongs` SQL, update result interface |
| 21 | `webapp/src/components/search/SemanticSearch.tsx` | 6 | Modify: add snippet + "Why this match?" rendering |
| 22 | `webapp/src/test/api/songs/search/semantic.test.ts` | 8 | Rewrite: test new request shape + snippet flow |
| 23 | `webapp/src/test/components/search/SemanticSearch.test.tsx` | 8 | Modify: test snippet rendering |
| 24 | `webapp/src/test/lib/db/search.test.ts` | 8 | Modify: remove `getEmbeddingForRecording` tests |
| 25 | `webapp/src/test/db/schema.test.ts` | 8 | Modify: update `songEmbeddings` schema tests |

---

## 6. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| OpenAI API downtime | Route returns 503 + "Semantic search unavailable" message; frontend shows fallback message per spec |
| OpenAI rate limits | Analysis Service uses semaphore (max 5 concurrent); Admin CLI has `--wait` polling with backoff |
| Vector dimension mismatch (batch vs query) | Both use same model (`text-embedding-3-small`) and same `dimensions: 1536`; `model_version` column in `song_embedding` enables future model upgrades |
| `lyrics_lines` is null for some songs | `parseLyricsLines()` returns `[]`; snippet fields are `null`/`[]`; song still appears in results by similarity |
| HNSW index build time on large catalog | Catalog is ~hundreds of songs; HNSW build is instantaneous at this scale |
| Cost overrun | OpenAI embedding is $0.02/1M tokens; even 1000 songs × 500 tokens = $0.01 for batch; queries are negligible |

---

## 7. Future Considerations

| Item | When |
|---|---|
| Switch to `fastembed-js` + ONNX for query embedding (spec v5) | If OpenAI costs become a concern or air-gapped deployment is needed |
| TurboVec in-memory index | If catalog grows to 100K+ songs or sub-10ms search latency is required |
| Model upgrade path | `sow-admin audio embed --all --force` regenerates all embeddings; `model_version` column tracks which model produced each embedding |
| Hybrid BM25 + vector search | Combine full-text search results with semantic search results for better recall on short queries |
