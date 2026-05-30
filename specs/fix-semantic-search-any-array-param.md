# Fix: Semantic Search `ANY()` Array Parameter Error

## 1. Problem Statement

The `findTopMatchingLines` function in `webapp/src/lib/db/songs.ts:434` fails at runtime with a PostgreSQL type error. The root cause is that Drizzle's `sql` template tag expands a JS array (`songIds: string[]`) into individual positional parameters, producing SQL like:

```sql
WHERE sle.song_id = ANY(($3, $4, $5, ...)::text[])
```

PostgreSQL interprets `($3, $4, ...)` as a **row constructor** (composite/record type), not an array literal. The `ANY()` operator then fails because it expects `text[]`, not a record.

### Error signature

```
Failed query: SELECT song_id, line_text, line_similarity FROM ( ... WHERE sle.song_id = ANY(($3, $4, $5, ...)::text[]) ... )
```

### Impact

- Semantic search "Describe" tab returns 500 for any query that reaches `findTopMatchingLines`
- The function is called after `semanticSearchSongs` succeeds, so the error occurs mid-pipeline

## 2. Root Cause Analysis

Drizzle ORM's `sql` tagged template interpolates JS values as individual bind parameters. When `${songIds}` (a `string[]`) is interpolated, each element becomes a separate `$N` placeholder. The resulting SQL `($3, $4, $5)` is a PostgreSQL row constructor, not an array. Casting `($3, $4, $5)::text[]` fails because you cannot cast a record to `text[]`.

This is the **only** place in the webapp codebase where an array is passed into a raw `sql` template with `ANY()`.

## 3. Fix Strategy

Use `sql.join()` to build a proper `ARRAY[...]::text[]` expression. Each song ID remains a separate bind parameter (safe from SQL injection), but they are wrapped in `ARRAY[...]` syntax that PostgreSQL recognizes as a `text[]` array.

### Before (broken)

```ts
WHERE sle.song_id = ANY(${songIds}::text[])
```

Produces: `WHERE sle.song_id = ANY(($3, $4, $5)::text[])` — row constructor, fails.

### After (fixed)

```ts
WHERE sle.song_id = ANY(ARRAY[${sql.join(songIds.map(id => sql`${id}`), sql`, `)}]::text[])
```

Produces: `WHERE sle.song_id = ANY(ARRAY[$3, $4, $5]::text[])` — proper array, works.

## 4. Implementation Steps

### Step 1: Fix `findTopMatchingLines` in `webapp/src/lib/db/songs.ts`

**File:** `webapp/src/lib/db/songs.ts`  
**Line:** 434

Replace:
```ts
WHERE sle.song_id = ANY(${songIds}::text[])
```

With:
```ts
WHERE sle.song_id = ANY(ARRAY[${sql.join(songIds.map(id => sql`${id}`), sql`, `)}]::text[])
```

No import changes needed — `sql` is already imported from `drizzle-orm` on line 3.

### Step 2: Add DB integration test for `findTopMatchingLines`

**New file:** `webapp/src/test/lib/db/songs.test.ts`

This test exercises the actual SQL query against a real PostgreSQL instance (Neon dev DB), not a mock. It validates that:

1. The function returns correct results when song IDs match rows in `song_line_embedding`
2. The function returns an empty Map when no song IDs match
3. The function returns an empty Map when `songIds` is empty (early return)
4. The function correctly filters lines with < 4 CJK characters
5. The function returns at most 2 lines per song (ROW_NUMBER <= 2)

**Test approach:**

- Use the existing `db` import from `@/db` (no mock)
- Insert test data into `song_line_embedding` before each test, clean up after
- Use a known embedding vector (e.g., all zeros or a fixed 1536-dim vector) for both the query embedding and the stored line embeddings
- Test with 2-3 song IDs to exercise the `ARRAY[...]` path

**Test skeleton:**

```ts
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { db } from "@/db";
import { songLineEmbeddings } from "@/db/schema";
import { eq, sql } from "drizzle-orm";
import { findTopMatchingLines } from "@/lib/db/songs";

const TEST_SONG_IDS = ["test-song-alpha", "test-song-beta"];
const ZERO_VECTOR = Array.from({ length: 1536 }, () => 0);

describe("findTopMatchingLines (integration)", () => {
  beforeAll(async () => {
    // Insert test rows into song_line_embedding
    // (requires corresponding songs in songs table or use ON DELETE CASCADE bypass)
  });

  afterAll(async () => {
    // Clean up test rows
  });

  it("returns top 2 matching lines per song", async () => {
    const result = await findTopMatchingLines(ZERO_VECTOR, TEST_SONG_IDS);
    // assertions...
  });

  it("returns empty Map for non-existent song IDs", async () => {
    const result = await findTopMatchingLines(ZERO_VECTOR, ["nonexistent"]);
    expect(result.size).toBe(0);
  });

  it("returns empty Map for empty songIds array", async () => {
    const result = await findTopMatchingLines(ZERO_VECTOR, []);
    expect(result.size).toBe(0);
  });

  it("filters out lines with fewer than 4 CJK characters", async () => {
    // Insert a line with only 2 CJK chars, verify it's excluded
  });

  it("returns at most 2 lines per song", async () => {
    // Insert 5 lines for one song, verify only top 2 returned
  });
});
```

**Note:** If the Neon dev DB does not have pgvector enabled or the `song_line_embedding` table doesn't exist yet, the integration test should be skipped via `describe.skipIf` with an env check. Alternatively, the test can be run only in CI where the DB is guaranteed to be set up.

### Step 3: Verify existing tests still pass

Run the existing mocked test suite to confirm no regressions:

```bash
cd webapp && pnpm test -- src/test/api/songs/search/semantic.test.ts
```

## 5. Files Changed

| File | Change |
|------|--------|
| `webapp/src/lib/db/songs.ts` | Fix `ANY()` array parameter on line 434 |
| `webapp/src/test/lib/db/songs.test.ts` | **New file** — DB integration test for `findTopMatchingLines` |

## 6. Verification Checklist

- [ ] `findTopMatchingLines` no longer throws on `ANY()` array parameter
- [ ] Semantic search "Describe" tab returns 200 with snippet results
- [ ] Existing mocked tests in `semantic.test.ts` still pass
- [ ] New integration test passes against dev DB
- [ ] `pnpm lint` passes
- [ ] `pnpm build` passes
