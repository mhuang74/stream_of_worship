# Semantic Search Implementation Plan v2

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
| **Line embedding storage** | **Separate `song_line_embedding` table** | Pre-computed during batch pipeline; eliminates query-time OpenAI call for snippets; reduces search latency from ~1.5s to ~300ms |
| **Long lyrics handling** | **Truncate with warning** | OpenAI silently truncates at 8191 tokens; log a warning when lyrics exceed a threshold (~6000 chars as heuristic) |
| **Staleness detection** | **`content_hash` column** in `song_embedding` | Stores sha256(title+composer+lyrics_raw+lyrics_lines)[:16]; detects stale embeddings when lyrics are corrected after embedding |
| **Model version validation** | **Runtime check at query time** | After fetching results, verify `model_version` matches query model; return 503 if mismatch |
| **OpenAI retry** | **Retry with exponential backoff** | 2 retries for 429/500 errors; covers both query-time and batch paths |
| **Short line filter** | **Skip lines with < 4 CJK characters** | Chinese worship lyrics often have short interjections ("阿们", "哈利路亚"); filter by counting CJK Unified Ideograph code points (U+4E00–U+9FFF) |
| **Missing line embeddings** | **Return null snippets** | If a song has no pre-computed line embeddings, return it with `matchingSnippet: null` and `whyThisMatch: []`; no on-the-fly computation |
| **OpenAI timeouts** | **10s / 30s / 60s** | embedQuery: 10s, embedLines (batch): 30s, batch embedding per song: 60s |
| **Rate limiting** | **None** | OpenAI costs are negligible; auth already gates access |
| **Empty table guard** | **None** | Search returns empty results if table is empty; admin must verify manually |

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
│     (with retry + 10s timeout)                                  │
│  3. semanticSearchSongs(embedding, limit) → pgvector <=>        │
│  4. Validate model_version matches query model                  │
│  5. For each result song:                                       │
│     a. Fetch pre-computed line embeddings from                   │
│        song_line_embedding table                                │
│     b. cosineSimilarity(query_emb, line_emb) → top 2 lines     │
│     c. Filter lines with < 4 CJK chars                          │
│  6. Return { songs, query, total }                              │
└──────────────────────────┬──────────────────────────────────────┘
                           │
              ┌────────────┼────────────┐
              ▼                         ▼
     ┌────────────────┐      ┌──────────────────┐
     │  Neon Postgres  │      │  OpenAI API       │
     │  + pgvector     │      │  text-embedding-  │
     │  song_embedding │      │  3-small          │
     │  (1536-dim)     │      │  $0.02/1M tokens  │
     │  song_line_     │      └──────────────────┘
     │  embedding      │
     │  (1536-dim)     │
     └────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  Offline: Batch Embedding Pipeline                              │
│                                                                 │
│  sow-admin audio embed [--all] [--force]                        │
│    → reads songs from Neon (title, composer, lyrics_raw,        │
│      lyrics_lines)                                              │
│    → computes content_hash = sha256(title+composer+            │
│      lyrics_raw+lyrics_lines)[:16]                              │
│    → skips songs where content_hash matches existing            │
│      (unless --force)                                           │
│    → submits EMBEDDING job to Analysis Service                  │
│    → Analysis Service calls OpenAI API (with retry + 60s timeout)│
│      - embeds song-level: title + composer + lyrics_raw         │
│      - embeds line-level: each line from lyrics_lines          │
│        (skipping lines with < 4 CJK chars)                      │
│    → Admin CLI writes song_embedding + song_line_embedding     │
│                                                                 │
│  sow-admin audio batch (extended)                               │
│    → after LRC generation, auto-submits embedding job           │
│      if song has no embedding yet                               │
└─────────────────────────────────────────────────────────────────┘
```

## 4. Implementation Phases

### Phase 1: Schema Migration — `song_embedding` + `song_line_embedding` table rebuild

**Why:** Current table is keyed by `recording_content_hash` with `vector(1024)`. Spec v4 says `song_id` with 1536 dims. Table is empty, so clean rebuild is safe. New `song_line_embedding` table stores pre-computed line embeddings for snippet matching.

**Files to change:**

| File | Change |
|---|---|
| `webapp/src/db/schema.ts` | Rewrite `songEmbeddings` table: PK = `songId`, `embedding vector(1536)`, `modelVersion` default `"openai-text-embedding-3-small"`, `contentHash` column, drop `recordingContentHash` FK. Add `songLineEmbeddings` table. |
| `webapp/src/db/schema.ts` | Update `songEmbeddingsRelations` to reference `songs.id`. Add `songLineEmbeddingsRelations`. |
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
    contentHash: text("content_hash").notNull(),
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

export const songLineEmbeddings = pgTable(
  "song_line_embedding",
  {
    id: serial("id").primaryKey(),
    songId: text("song_id")
      .notNull()
      .references(() => songs.id, { onDelete: "cascade" }),
    lineIndex: integer("line_index").notNull(),
    lineText: text("line_text").notNull(),
    embedding: vector("embedding", { dimensions: 1536 }).notNull(),
  },
  (t) => [
    index("idx_song_line_embedding_song").on(t.songId),
    index("idx_song_line_embedding_cosine").on(
      sql`${t.embedding} vector_cosine_ops`
    ),
  ]
);

export const songLineEmbeddingsRelations = relations(
  songLineEmbeddings,
  ({ one }) => ({
    song: one(songs, {
      fields: [songLineEmbeddings.songId],
      references: [songs.id],
    }),
  })
);
```

**New SQL migration:**

```sql
DROP TABLE IF EXISTS song_line_embedding;
DROP TABLE IF EXISTS song_embedding;

CREATE TABLE song_embedding (
  song_id       TEXT PRIMARY KEY REFERENCES songs(id) ON DELETE CASCADE,
  embedding     vector(1536) NOT NULL,
  model_version TEXT NOT NULL DEFAULT 'openai-text-embedding-3-small',
  content_hash  TEXT NOT NULL,
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_song_embedding_cosine ON song_embedding
  USING hnsw (embedding vector_cosine_ops);

CREATE TABLE song_line_embedding (
  id        SERIAL PRIMARY KEY,
  song_id   TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
  line_index INTEGER NOT NULL,
  line_text  TEXT NOT NULL,
  embedding  vector(1536) NOT NULL
);

CREATE INDEX idx_song_line_embedding_song ON song_line_embedding (song_id);
CREATE INDEX idx_song_line_embedding_cosine ON song_line_embedding
  USING hnsw (embedding vector_cosine_ops);
```

**HNSW index** instead of current B-tree — critical for pgvector cosine similarity performance. Gives sub-millisecond search on thousands of vectors.

**`content_hash`** stores `sha256(title+composer+lyrics_raw+lyrics_lines)[:16]`. During `embed --all` (non-`--force`), the Admin CLI compares this hash to the current content to detect stale embeddings and only re-embeds changed songs.

---

### Phase 2: Batch Embedding Generation — Analysis Service

**Why:** No code populates `song_embedding` or `song_line_embedding`. Need an offline pipeline that generates embeddings for all songs and writes them to Neon.

#### 2a. Analysis Service — new `EMBEDDING` job type

**Files to change:**

| File | Change |
|---|---|
| `services/analysis/src/sow_analysis/models.py` | Add `JobType.EMBEDDING = "embedding"`, `EmbeddingJobRequest`, `EmbeddingJobResult` |
| `services/analysis/src/sow_analysis/workers/queue.py` | Add `elif job.type == JobType.EMBEDDING` dispatch |
| `services/analysis/src/sow_analysis/workers/embedder.py` (new) | `EmbeddingWorker` class: calls OpenAI `text-embedding-3-small` with retry |
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
    lyrics_lines: list[str] = []

class EmbeddingJobResult(BaseModel):
    song_id: str
    embedding: list[float]
    line_embeddings: list[LineEmbedding]
    model_version: str = "openai-text-embedding-3-small"
    content_hash: str

class LineEmbedding(BaseModel):
    line_index: int
    line_text: str
    embedding: list[float]
```

**New worker (`embedder.py`):**

```python
import os
import asyncio
import hashlib
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)

_CJK_RANGE_START = 0x4E00
_CJK_RANGE_END = 0x9FFF
_MAX_INPUT_TOKENS_HEURISTIC = 6000

def _count_cjk_chars(text: str) -> int:
    return sum(1 for ch in text if _CJK_RANGE_START <= ord(ch) <= _CJK_RANGE_END)

def _compute_content_hash(title: str, composer: str, lyrics_raw: str, lyrics_lines: list[str]) -> str:
    content = f"{title}\0{composer}\0{lyrics_raw}\0{'|'.join(lyrics_lines)}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

class EmbeddingWorker:
    def __init__(self):
        self._client = OpenAI(
            api_key=os.environ.get("SOW_OPENAI_API_KEY"),
            timeout=60.0,
            max_retries=2,
        )

    async def embed_song(self, request: EmbeddingJobRequest) -> EmbeddingJobResult:
        song_text = f"{request.title} {request.composer} {request.lyrics_raw}".strip()

        if len(song_text) > _MAX_INPUT_TOKENS_HEURISTIC:
            logger.warning(
                "Song %s lyrics exceed %d chars, OpenAI will truncate at 8191 tokens",
                request.song_id, _MAX_INPUT_TOKENS_HEURISTIC,
            )

        song_embedding = await self._embed_texts([song_text])

        eligible_lines = [
            (i, line) for i, line in enumerate(request.lyrics_lines)
            if _count_cjk_chars(line) >= 4
        ]

        line_texts = [line for _, line in eligible_lines]
        line_embeddings_raw = await self._embed_texts(line_texts) if line_texts else []

        line_embeddings = [
            LineEmbedding(
                line_index=idx,
                line_text=line,
                embedding=emb,
            )
            for (idx, line), emb in zip(eligible_lines, line_embeddings_raw)
        ]

        content_hash = _compute_content_hash(
            request.title, request.composer, request.lyrics_raw, request.lyrics_lines
        )

        return EmbeddingJobResult(
            song_id=request.song_id,
            embedding=song_embedding[0],
            line_embeddings=line_embeddings,
            model_version="openai-text-embedding-3-small",
            content_hash=content_hash,
        )

    async def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        response = await asyncio.to_thread(
            self._client.embeddings.create,
            model="text-embedding-3-small",
            input=texts,
            dimensions=1536,
        )
        return [d.embedding for d in sorted(response.data, key=lambda x: x.index)]
```

**Concurrency:** The embedding job calls an external API (no GPU), so it doesn't need the `_local_model_semaphore`. Add a separate `_embedding_semaphore` with `max_concurrent=5` to respect OpenAI rate limits.

**Queue dispatch addition:**

```python
# In _process_job_with_semaphore():
elif job.type == JobType.EMBEDDING:
    async with self._embedding_semaphore:
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
| `src/stream_of_worship/admin/db/client.py` | Add `upsert_song_embedding()`, `upsert_song_line_embeddings()`, `get_songs_without_embeddings()`, `get_stale_embeddings()` |
| `src/stream_of_worship/admin/db/schema.py` | Add `song_embedding` + `song_line_embedding` table DDL (idempotent) |
| `src/stream_of_worship/admin/db/models.py` | Add `SongEmbedding`, `SongLineEmbedding` dataclasses |

**`AnalysisClient.submit_embedding()`:**

```python
def submit_embedding(self, song_id: str, title: str, composer: str, lyrics_raw: str, lyrics_lines: list[str]) -> JobInfo:
    payload = {
        "song_id": song_id,
        "title": title,
        "composer": composer,
        "lyrics_raw": lyrics_raw,
        "lyrics_lines": lyrics_lines,
    }
    resp = self._post("/api/v1/jobs/embedding", payload)
    return JobInfo(job_id=resp["job_id"], status=resp["status"])
```

**`audio embed` command:**

```
sow-admin audio embed <song_id>          # embed a single song
sow-admin audio embed --all              # embed all songs without embeddings
                                        #   (or with stale content_hash)
sow-admin audio embed --all --force      # re-embed everything (model upgrade)
sow-admin audio embed --all --wait      # wait for all jobs to complete
```

**Flow:**
1. Read song from Neon DB (title, composer, lyrics_raw, lyrics_lines)
2. Compute content_hash = sha256(title+composer+lyrics_raw+lyrics_lines)[:16]
3. If `--all` (non-`--force`): compare content_hash to existing `song_embedding.content_hash`; skip if match
4. Submit EMBEDDING job to Analysis Service
5. If `--wait`, poll until complete
6. Write song_embedding + song_line_embeddings to DB via `upsert_song_embedding()` + `upsert_song_line_embeddings()`

**`batch` extension:** After LRC generation completes for a recording, if the parent song has no embedding, submit an embedding job. This ensures embeddings are generated as part of the normal catalog pipeline.

**`DatabaseClient` additions:**

```python
import json

def upsert_song_embedding(self, song_id: str, embedding: list[float], model_version: str, content_hash: str):
    emb_str = json.dumps(embedding)
    self._execute("""
        INSERT INTO song_embedding (song_id, embedding, model_version, content_hash)
        VALUES (%s, %s::vector, %s, %s)
        ON CONFLICT (song_id) DO UPDATE
        SET embedding = EXCLUDED.embedding,
            model_version = EXCLUDED.model_version,
            content_hash = EXCLUDED.content_hash,
            created_at = NOW()
    """, (song_id, emb_str, model_version, content_hash))

def upsert_song_line_embeddings(self, song_id: str, line_embeddings: list[dict]):
    self._execute("DELETE FROM song_line_embedding WHERE song_id = %s", (song_id,))
    if not line_embeddings:
        return
    values = []
    for le in line_embeddings:
        emb_str = json.dumps(le["embedding"])
        values.append((song_id, le["line_index"], le["line_text"], emb_str))
    self._execute_many("""
        INSERT INTO song_line_embedding (song_id, line_index, line_text, embedding)
        VALUES (%s, %s, %s, %s::vector)
    """, values)

def get_songs_without_embeddings(self) -> list[Song]:
    rows = self._execute("""
        SELECT s.id, s.title, s.composer, s.lyrics_raw, s.lyrics_lines
        FROM songs s
        LEFT JOIN song_embedding se ON s.id = se.song_id
        WHERE se.song_id IS NULL
          AND s.deleted_at IS NULL
          AND s.lyrics_raw IS NOT NULL
          AND s.lyrics_raw != ''
    """)
    return [Song(id=r[0], title=r[1], composer=r[2], lyrics_raw=r[3], lyrics_lines=r[4]) for r in rows]

def get_stale_embeddings(self) -> list[Song]:
    rows = self._execute("""
        SELECT s.id, s.title, s.composer, s.lyrics_raw, s.lyrics_lines
        FROM songs s
        JOIN song_embedding se ON s.id = se.song_id
        WHERE se.content_hash != (
            SELECT encode(sha256(
                s.title || E'\\0' || s.composer || E'\\0' || s.lyrics_raw || E'\\0' || s.lyrics_lines
            ), 'hex')
        )
        AND s.deleted_at IS NULL
    """)
    return [Song(id=r[0], title=r[1], composer=r[2], lyrics_raw=r[3], lyrics_lines=r[4]) for r in rows]
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
| `webapp/src/lib/embedding.ts` (new) | `embedQuery()` function with retry + timeout |

**`webapp/src/lib/embedding.ts`:**

```typescript
import OpenAI from "openai";

const openai = new OpenAI({
  apiKey: process.env.SOW_OPENAI_API_KEY,
  timeout: 10_000,
  maxRetries: 2,
});

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

const _CJK_RANGE_START = 0x4e00;
const _CJK_RANGE_END = 0x9fff;

export function countCjkChars(text: string): number {
  let count = 0;
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    if (code >= _CJK_RANGE_START && code <= _CJK_RANGE_END) {
      count++;
    }
  }
  return count;
}
```

**Error handling:** If `SOW_OPENAI_API_KEY` is not set or the API call fails (after retries), the route handler returns `{ error: "Semantic search unavailable. Try Search mode." }` with status 503 — matching spec v4 §5.2a.

**Cost:** text-embedding-3-small is $0.02/1M tokens. A typical query is ~10 tokens → ~$0.0000002/query. Negligible.

---

### Phase 4: Fix API Route — Accept `query` Instead of `recordingId`

**Why:** Frontend sends `{ query, limit }`, API expects `{ recordingId, limit }`. This is the immediate cause of the 400 error.

**Files to change:**

| File | Change |
|---|---|
| `webapp/src/app/api/songs/search/semantic/route.ts` | Rewrite: accept `{ query, limit }`, call `embedQuery()`, then `semanticSearchSongs()`, then compute snippets from pre-computed line embeddings |
| `webapp/src/lib/db/search.ts` | Remove `getEmbeddingForRecording` (no longer needed), update re-exports |

**New route handler:**

```typescript
import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { embedQuery, cosineSimilarity, countCjkChars } from "@/lib/embedding";
import { semanticSearchSongs, getLineEmbeddingsForSong } from "@/lib/db/songs";
import { z } from "zod";

const QUERY_MODEL = "openai-text-embedding-3-small";
const MIN_CJK_CHARS_FOR_SNIPPET = 4;

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

    // Validate model version matches
    if (songs.length > 0 && songs[0].modelVersion !== QUERY_MODEL) {
      return NextResponse.json(
        { error: "Semantic search unavailable — embeddings need regeneration. Contact admin." },
        { status: 503 }
      );
    }

    // Compute matchingSnippet and whyThisMatch from pre-computed line embeddings
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

**Snippet computation helper (using pre-computed line embeddings):**

```typescript
async function computeSnippets(
  songs: SemanticSearchResult[],
  queryEmbedding: number[]
): Promise<SemanticSearchResult[]> {
  const results = songs.map((s) => ({
    ...s,
    matchingSnippet: null as string | null,
    whyThisMatch: [] as string[],
  }));

  // Fetch line embeddings for all result songs in one query
  const songIds = songs.map((s) => s.id);
  const lineEmbeddings = await getLineEmbeddingsForSongs(songIds);

  for (let i = 0; i < results.length; i++) {
    const songLines = lineEmbeddings.get(results[i].id) ?? [];

    // Filter out short lines (< 4 CJK chars)
    const eligibleLines = songLines.filter(
      (l) => countCjkChars(l.lineText) >= MIN_CJK_CHARS_FOR_SNIPPET
    );

    if (eligibleLines.length === 0) continue;

    // Score each line against query embedding
    const scored = eligibleLines.map((l) => ({
      line: l.lineText,
      score: cosineSimilarity(queryEmbedding, l.embedding),
    }));
    scored.sort((a, b) => b.score - a.score);

    const top2 = scored.slice(0, 2);
    results[i].matchingSnippet = top2[0]?.line ?? null;
    results[i].whyThisMatch = top2.map((t) => t.line);
  }

  return results;
}
```

**Key difference from v1:** No `embedLines()` call at query time. Snippets are computed from pre-computed `song_line_embedding` rows, eliminating the second OpenAI API call and reducing latency from ~1.5s to ~300ms.

---

### Phase 5: Update `semanticSearchSongs` SQL Query + Add `getLineEmbeddingsForSongs`

**Why:** Current query joins through `song_embedding → recordings → songs` (keyed by recording_content_hash). New schema keys by `song_id` directly, so the query simplifies. Also need to return `model_version` for validation and fetch line embeddings.

**Files to change:**

| File | Change |
|---|---|
| `webapp/src/lib/db/songs.ts` | Rewrite `semanticSearchSongs`: join on `song_id`, validate 1536 dims, include `model_version` in result. Add `getLineEmbeddingsForSongs()`. |

**Updated `SemanticSearchResult` interface:**

```typescript
export interface SemanticSearchResult extends SongWithRecordings {
  similarity: number;
  modelVersion: string;
  matchingSnippet: string | null;
  whyThisMatch: string[];
}
```

**New SQL for `semanticSearchSongs`:**

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
    se.model_version,
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

**New `getLineEmbeddingsForSongs()` function:**

```typescript
export async function getLineEmbeddingsForSongs(
  songIds: string[]
): Promise<Map<string, { lineText: string; embedding: number[] }[]>> {
  if (songIds.length === 0) return new Map();

  const rows = await db
    .select({
      songId: songLineEmbeddings.songId,
      lineText: songLineEmbeddings.lineText,
      embedding: sql<string>`${songLineEmbeddings.embedding}::text`,
    })
    .from(songLineEmbeddings)
    .where(inArray(songLineEmbeddings.songId, songIds))
    .orderBy(songLineEmbeddings.songId, songLineEmbeddings.lineIndex);

  const result = new Map<string, { lineText: string; embedding: number[] }[]>();
  for (const row of rows) {
    const lines = result.get(row.songId) ?? [];
    lines.push({
      lineText: row.lineText,
      embedding: JSON.parse(row.embedding),
    });
    result.set(row.songId, lines);
  }
  return result;
}
```

**Validation update:** Change dimension check from 1024 to 1536:

```typescript
if (embedding.length !== 1536) {
  throw new Error(
    `Invalid embedding: expected 1536 dimensions, got ${embedding.length}`
  );
}
```

**Result mapping update:** Add `modelVersion` to the mapped result:

```typescript
return resultRows.map((row) => ({
  // ... existing fields ...
  modelVersion: row.model_version as string,
  similarity: Number(row.similarity),
  matchingSnippet: null,
  whyThisMatch: [],
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
| `webapp/src/test/api/songs/search/semantic.test.ts` | Rewrite: mock `embedQuery` instead of `getEmbeddingForRecording`; test `{ query, limit }` request shape; test OpenAI failure → 503; test model version mismatch → 503; test snippet matching from pre-computed line embeddings |
| `webapp/src/test/components/search/SemanticSearch.test.tsx` | Update mock API response to include `matchingSnippet`, `whyThisMatch`; test snippet rendering; test "Why this match?" expand |
| `webapp/src/test/lib/db/search.test.ts` | Remove `getEmbeddingForRecording` tests; add `semanticSearchSongs` 1536-dim validation tests |
| `webapp/src/test/db/schema.test.ts` | Update `songEmbeddings` schema tests: PK is `songId`, dims is 1536, has `contentHash`. Add `songLineEmbeddings` schema tests. |

**Key test cases for route:**

| Test | Input | Expected |
|---|---|---|
| Not authenticated | No session | 401 |
| Invalid JSON | Malformed body | 400 |
| Missing `query` | `{ limit: 20 }` | 400 |
| Empty `query` | `{ query: "", limit: 20 }` | 400 |
| OpenAI API failure | `embedQuery` throws | 503 + "Semantic search unavailable" |
| Model version mismatch | `modelVersion != query model` | 503 + "embeddings need regeneration" |
| Success | `{ query: "God's faithfulness", limit: 20 }` | 200 + songs with similarity, snippets |
| Custom limit | `{ query: "test", limit: 5 }` | 200, max 5 results |
| Limit > 50 | `{ query: "test", limit: 100 }` | 400 (Zod rejects) |
| Song with no line embeddings | Song has `song_embedding` but no `song_line_embedding` rows | 200, `matchingSnippet: null`, `whyThisMatch: []` |
| Short CJK lines filtered | Line "阿们" in `song_line_embedding` | Not returned as snippet |

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

1. Run `npx drizzle-kit generate` → new migration file for `song_embedding` rebuild + `song_line_embedding` creation
2. Run `npx drizzle-kit push` against Neon (dev) or `migrate` (prod)
3. Add `SOW_OPENAI_API_KEY` to Vercel environment variables
4. Add `SOW_OPENAI_API_KEY` to Analysis Service environment
5. Deploy Analysis Service with new `EMBEDDING` job type
6. Run `sow-admin audio embed --all --wait` to populate `song_embedding` + `song_line_embedding` tables
7. Deploy webapp with new API route + OpenAI integration
8. Verify semantic search works end-to-end in "Describe" tab

---

## 5. Complete File Change Summary

| # | File | Phase | Change Type |
|---|---|---|---|
| 1 | `webapp/src/db/schema.ts` | 1 | Modify: rewrite `songEmbeddings` table + relations; add `songLineEmbeddings` table + relations |
| 2 | `webapp/drizzle/` (new migration) | 1 | Generate: drop/recreate `song_embedding` with HNSW index; create `song_line_embedding` with HNSW index |
| 3 | `services/analysis/src/sow_analysis/models.py` | 2a | Modify: add `EMBEDDING` job type, `EmbeddingJobRequest`, `EmbeddingJobResult`, `LineEmbedding` |
| 4 | `services/analysis/src/sow_analysis/workers/embedder.py` | 2a | **New**: `EmbeddingWorker` class with retry, CJK filter, content hash, truncation warning |
| 5 | `services/analysis/src/sow_analysis/workers/queue.py` | 2a | Modify: add `EMBEDDING` dispatch + `_embedding_semaphore` |
| 6 | `services/analysis/src/sow_analysis/routes/jobs.py` | 2a | Modify: add `POST /api/v1/jobs/embedding` |
| 7 | `services/analysis/pyproject.toml` | 2a | Modify: add `openai` dependency |
| 8 | `src/stream_of_worship/admin/services/analysis.py` | 2b | Modify: add `submit_embedding()` |
| 9 | `src/stream_of_worship/admin/commands/audio.py` | 2b | Modify: add `embed` command with `--all`/`--force`/`--wait`; extend `batch` |
| 10 | `src/stream_of_worship/admin/db/client.py` | 2b | Modify: add `upsert_song_embedding()`, `upsert_song_line_embeddings()`, `get_songs_without_embeddings()`, `get_stale_embeddings()` |
| 11 | `src/stream_of_worship/admin/db/schema.py` | 2b | Modify: add `song_embedding` + `song_line_embedding` DDL |
| 12 | `src/stream_of_worship/admin/db/models.py` | 2b | Modify: add `SongEmbedding`, `SongLineEmbedding` dataclasses |
| 13 | `webapp/.env.example` | 3 | Modify: add `SOW_OPENAI_API_KEY=` |
| 14 | `webapp/.env.production.example` | 3 | Modify: add `SOW_OPENAI_API_KEY=` |
| 15 | `webapp/package.json` | 3 | Modify: add `openai` dependency |
| 16 | `webapp/next.config.ts` | 3 | Modify: add `"openai"` to `serverExternalPackages` |
| 17 | `webapp/src/lib/embedding.ts` | 3 | **New**: `embedQuery()`, `cosineSimilarity()`, `countCjkChars()` |
| 18 | `webapp/src/app/api/songs/search/semantic/route.ts` | 4 | Rewrite: accept `{ query, limit }`, call `embedQuery()`, model version validation, compute snippets from pre-computed line embeddings |
| 19 | `webapp/src/lib/db/search.ts` | 7 | Modify: remove `getEmbeddingForRecording` |
| 20 | `webapp/src/lib/db/songs.ts` | 5 | Modify: rewrite `semanticSearchSongs` SQL, add `getLineEmbeddingsForSongs()`, update result interface |
| 21 | `webapp/src/components/search/SemanticSearch.tsx` | 6 | Modify: add snippet + "Why this match?" rendering |
| 22 | `webapp/src/test/api/songs/search/semantic.test.ts` | 8 | Rewrite: test new request shape + snippet flow + model version validation |
| 23 | `webapp/src/test/components/search/SemanticSearch.test.tsx` | 8 | Modify: test snippet rendering |
| 24 | `webapp/src/test/lib/db/search.test.ts` | 8 | Modify: remove `getEmbeddingForRecording` tests |
| 25 | `webapp/src/test/db/schema.test.ts` | 8 | Modify: update `songEmbeddings` schema tests; add `songLineEmbeddings` tests |

---

## 6. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| OpenAI API downtime | Route returns 503 + "Semantic search unavailable" message; frontend shows fallback message per spec |
| OpenAI rate limits | Analysis Service uses semaphore (max 5 concurrent); OpenAI client configured with `max_retries=2` for 429/500; Admin CLI has `--wait` polling with backoff |
| Vector dimension mismatch (batch vs query) | Both use same model (`text-embedding-3-small`) and same `dimensions: 1536`; `model_version` column in `song_embedding` enables future model upgrades; runtime validation at query time |
| `lyrics_lines` is null for some songs | Pre-computed line embeddings won't exist for these songs; snippet fields are `null`/`[]`; song still appears in results by similarity |
| HNSW index build time on large catalog | Catalog is ~hundreds of songs; HNSW build is instantaneous at this scale |
| Cost overrun | OpenAI embedding is $0.02/1M tokens; even 1000 songs × 500 tokens = $0.01 for batch; queries are negligible |
| Long lyrics truncated at 8191 tokens | Log warning when lyrics exceed ~6000 chars heuristic; accept truncation for now (tail content lost) |
| Stale embeddings after lyrics correction | `content_hash` column detects content changes; `embed --all` (non-`--force`) re-embeds only changed songs |
| Model version mismatch | Runtime validation in route handler: if `model_version` doesn't match query model, return 503 |
| Missing line embeddings for new songs | Return `matchingSnippet: null` and `whyThisMatch: []`; no on-the-fly computation |
| Short CJK lines as snippet noise | Filter lines with < 4 CJK characters in both batch (skip embedding) and query (skip from results) |
| OpenAI batch input limit (2048) | Pre-computing line embeddings in batch pipeline eliminates this concern at query time; batch pipeline embeds per-song (typically < 100 lines) |

---

## 7. Future Considerations

| Item | When |
|---|---|
| Switch to `fastembed-js` + ONNX for query embedding (spec v5) | If OpenAI costs become a concern or air-gapped deployment is needed |
| TurboVec in-memory index | If catalog grows to 100K+ songs or sub-10ms search latency is required |
| Model upgrade path | `sow-admin audio embed --all --force` regenerates all embeddings; `model_version` column tracks which model produced each embedding |
| Hybrid BM25 + vector search | Combine full-text search results with semantic search results for better recall on short queries |
| Chunk + average for long lyrics | If truncation proves problematic for specific songs, implement chunked embedding with averaging |
