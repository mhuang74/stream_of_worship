# Fix: Semantic Search `ANY()` Array Parameter Error (v2)

## 1. Problem Statement

The `findTopMatchingLines` function in `webapp/src/lib/db/songs.ts:434` fails at runtime with a PostgreSQL type error. Drizzle's `sql` template tag expands a JS array (`songIds: string[]`) into individual positional parameters, producing:

```sql
WHERE sle.song_id = ANY(($3, $4, $5, ...)::text[])
```

PostgreSQL interprets `($3, $4, ...)` as a **row constructor** (composite/record type), not an array literal. The `ANY()` operator then fails because it expects `text[]`, not a record.

### Error signature

```
Failed query: SELECT ... WHERE sle.song_id = ANY(($3, $4, $5, ...)::text[]) ...
```

### Impact

- Semantic search "Describe" tab returns 500 for any query that reaches `findTopMatchingLines`
- The function is called after `semanticSearchSongs` succeeds, so the error occurs mid-pipeline

## 2. Root Cause Analysis

Drizzle ORM's `sql` tagged template interpolates JS values as individual bind parameters. When `${songIds}` (a `string[]`) is interpolated, each element becomes a separate `$N` placeholder. The resulting SQL `($3, $4, $5)` is a PostgreSQL row constructor, not an array. Casting `($3, $4, $5)::text[]` fails because you cannot cast a record to `text[]`.

This is the **only** place in the webapp codebase where an array is passed into a raw `sql` template with `ANY()`.

**Additional concern — vector interpolation hardening:** `findTopMatchingLines` builds `vectorStr` via `[${queryEmbedding.join(",")}]` on line 420 without any validation. While the caller (`semanticSearchSongs`) validates the embedding before this function is called, `findTopMatchingLines` has no defense-in-depth of its own. If called from a new call site in the future, invalid input could produce malformed SQL. The sibling function `semanticSearchSongs` (lines 321-336) already validates its embedding — `findTopMatchingLines` should do the same.

## 3. Fix Strategy

### Fix A: `ANY()` array parameter

Use `sql.join()` to build a proper `ARRAY[...]::text[]` expression. Each song ID remains a separate bind parameter (safe from SQL injection), but they are wrapped in `ARRAY[...]` syntax that PostgreSQL recognizes as a `text[]` array.

**Before (broken):**
```ts
WHERE sle.song_id = ANY(${songIds}::text[])
```
Produces: `WHERE sle.song_id = ANY(($3, $4, $5)::text[])` — row constructor, fails.

**After (fixed):**
```ts
WHERE sle.song_id = ANY(ARRAY[${sql.join(songIds.map(id => sql`${id}`), sql`, `)}]::text[])
```
Produces: `WHERE sle.song_id = ANY(ARRAY[$3, $4, $5]::text[])` — proper array, works.

### Fix B: Vector interpolation hardening

Add the same embedding validation that `semanticSearchSongs` already performs (lines 321-336) to `findTopMatchingLines`. Extract the validation into a shared utility to avoid duplication.

Both `semanticSearchSongs` and `findTopMatchingLines` will call this utility before building `vectorStr`.

## 4. Implementation Steps

### Step 1: Extract shared embedding validation

**File:** `webapp/src/lib/db/songs.ts`

Extract the validation logic from `semanticSearchSongs` (lines 321-336) into a private helper:

```ts
function validateEmbedding(embedding: number[], expectedDims: number = 1536): void {
  if (embedding.length !== expectedDims) {
    throw new Error(`Invalid embedding: expected ${expectedDims} dimensions, got ${embedding.length}`);
  }
  for (const v of embedding) {
    if (typeof v !== "number" || !isFinite(v)) {
      throw new Error("Invalid embedding value: all values must be finite numbers");
    }
    if (Math.abs(v) > 100) {
      throw new Error("Invalid embedding value: values must be in range [-100, 100]");
    }
  }
  const vectorStr = `[${embedding.join(",")}]`;
  if (!/^\[-?\d+(\.\d+)?(,-?\d+(\.\d+)?)*\]$/.test(vectorStr)) {
    throw new Error("Invalid embedding: vector string contains unexpected characters");
  }
}
```

Then replace the inline validation in `semanticSearchSongs` (lines 321-336) with a call to `validateEmbedding(embedding)`.

### Step 2: Add validation to `findTopMatchingLines`

**File:** `webapp/src/lib/db/songs.ts`

At the top of `findTopMatchingLines` (after the empty-array early return on line 418), add:

```ts
validateEmbedding(queryEmbedding);
```

This ensures `vectorStr` is safe even if `findTopMatchingLines` is called from a new call site that doesn't pre-validate.

### Step 3: Fix `ANY()` array parameter

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

### Step 4: Add unit test for SQL generation and validation

**New file:** `webapp/src/test/lib/db/songs.test.ts`

Integration test is skipped due to FK constraints (`song_line_embedding` references `songs` with `ON DELETE CASCADE`) and no transaction-rollback support in the current db client. Instead, add a lightweight unit test that verifies the SQL structure is correct without hitting a real database.

**Test approach:**

- Mock `db.execute` to capture the SQL string
- Call `findTopMatchingLines` with a known embedding and song IDs
- Assert the captured SQL contains `ARRAY[` (not a row constructor `(`)
- Assert the captured SQL contains `::text[]`
- Assert the function returns empty Map for empty `songIds` (early return, no DB call)
- Assert the function throws on invalid embedding (wrong dimensions, NaN, etc.)

```ts
import { describe, it, expect, beforeEach, vi } from "vitest";
import { findTopMatchingLines } from "@/lib/db/songs";
import { db } from "@/db";

vi.mock("@/db", () => ({
  db: {
    execute: vi.fn(),
  },
}));

const NON_ZERO_EMBEDDING = Array.from({ length: 1536 }, () => 0.01);

describe("findTopMatchingLines", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns empty Map for empty songIds without calling DB", async () => {
    const result = await findTopMatchingLines(NON_ZERO_EMBEDDING, []);
    expect(result.size).toBe(0);
    expect(db.execute).not.toHaveBeenCalled();
  });

  it("builds ARRAY[] syntax for ANY() clause", async () => {
    vi.mocked(db.execute).mockResolvedValue({ rows: [] } as any);
    await findTopMatchingLines(NON_ZERO_EMBEDDING, ["song-1", "song-2"]);
    const callArgs = vi.mocked(db.execute).mock.calls[0][0];
    const sqlStr = typeof callArgs === "string" ? callArgs : String(callArgs);
    expect(sqlStr).toContain("ARRAY[");
    expect(sqlStr).toContain("::text[]");
  });

  it("throws on invalid embedding (wrong dimensions)", async () => {
    const badEmbedding = [1, 2, 3];
    await expect(
      findTopMatchingLines(badEmbedding, ["song-1"])
    ).rejects.toThrow("Invalid embedding");
  });

  it("throws on embedding with NaN", async () => {
    const nanEmbedding = Array.from({ length: 1536 }, (_, i) =>
      i === 0 ? NaN : 0.01
    );
    await expect(
      findTopMatchingLines(nanEmbedding, ["song-1"])
    ).rejects.toThrow("Invalid embedding");
  });
});
```

### Step 5: Verify existing tests still pass

```bash
cd webapp && pnpm test -- src/test/api/songs/search/semantic.test.ts
cd webapp && pnpm test -- src/test/lib/db/search.test.ts
```

## 5. Files Changed

| File | Change |
|------|--------|
| `webapp/src/lib/db/songs.ts` | Extract `validateEmbedding` helper; add validation call to `findTopMatchingLines`; fix `ANY()` array parameter on line 434 |
| `webapp/src/test/lib/db/songs.test.ts` | **New file** — unit tests for `findTopMatchingLines` (SQL structure + validation) |

## 6. Verification Checklist

- [ ] `findTopMatchingLines` no longer throws on `ANY()` array parameter
- [ ] Semantic search "Describe" tab returns 200 with snippet results
- [ ] `findTopMatchingLines` validates embedding before building `vectorStr`
- [ ] `semanticSearchSongs` still validates embedding (via shared helper, no regression)
- [ ] Existing mocked tests in `semantic.test.ts` still pass
- [ ] Existing mocked tests in `search.test.ts` still pass
- [ ] New unit tests in `songs.test.ts` pass
- [ ] `pnpm lint` passes
- [ ] `pnpm build` passes
