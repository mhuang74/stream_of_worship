// @vitest-environment node

import { existsSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { and, ilike, isNull, sql } from "drizzle-orm";
import { songs } from "@/db/schema";

const SOW_ENV_PATH = "/opt/sow/.env";
const PREFERRED_SEARCH_QUERY = "恩典";
const RUN_NEON_INTEGRATION = process.env.SOW_RUN_NEON_INTEGRATION === "1";

interface SearchFixture {
  query: string;
  reason: string;
}

function readSowDatabaseUrl(): string | undefined {
  if (!existsSync(SOW_ENV_PATH)) {
    return undefined;
  }

  const envContent = readFileSync(SOW_ENV_PATH, "utf8");
  for (const line of envContent.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }

    const separatorIndex = trimmed.indexOf("=");
    if (separatorIndex === -1) {
      continue;
    }

    const key = trimmed.slice(0, separatorIndex).trim();
    if (key !== "SOW_DATABASE_URL") {
      continue;
    }

    const rawValue = trimmed.slice(separatorIndex + 1).trim();
    return rawValue.replace(/^(['"])(.*)\1$/, "$2");
  }

  return undefined;
}

const databaseUrl = readSowDatabaseUrl();
const describeIfDatabaseConfigured = databaseUrl && RUN_NEON_INTEGRATION ? describe : describe.skip;

function findTwoCharacterChineseSubstring(title: string): string | undefined {
  const chars = Array.from(title);
  for (let index = 0; index < chars.length - 1; index += 1) {
    const candidate = `${chars[index]}${chars[index + 1]}`;
    if (/^\p{Script=Han}{2}$/u.test(candidate) && candidate !== title) {
      return candidate;
    }
  }

  return undefined;
}

describeIfDatabaseConfigured("fullTextSearchSongs Neon integration", () => {
  it(
    "finds Chinese title substrings with the real Postgres search query",
    async () => {
      process.env.SOW_DATABASE_URL = databaseUrl;

      const [{ db }, { fullTextSearchSongs }] = await Promise.all([
        import("@/db"),
        import("@/lib/db/search"),
      ]);

      const preferredRows = await db
        .select({ id: songs.id, title: songs.title })
        .from(songs)
        .where(
          and(
            isNull(songs.deletedAt),
            ilike(songs.title, `%${PREFERRED_SEARCH_QUERY}%`),
            sql`exists (
              select 1
              from recordings
              where recordings.song_id = ${songs.id}
                and recordings.deleted_at IS NULL
            )`
          )
        )
        .limit(10);

      let fixture: SearchFixture | undefined;
      if (preferredRows.length > 0) {
        fixture = {
          query: PREFERRED_SEARCH_QUERY,
          reason: "preferred reported query",
        };
      } else {
        const playableChineseRows = await db
          .select({ id: songs.id, title: songs.title })
          .from(songs)
          .where(
            and(
              isNull(songs.deletedAt),
              sql`${songs.title} ~ '[一-龥].*[一-龥]'`,
              sql`exists (
                select 1
                from recordings
                where recordings.song_id = ${songs.id}
                  and recordings.deleted_at IS NULL
              )`
            )
          )
          .limit(100);

        const fallback = playableChineseRows
          .map((row) => ({
            query: findTwoCharacterChineseSubstring(row.title),
            title: row.title,
          }))
          .find((row): row is { query: string; title: string } => Boolean(row.query));

        if (fallback) {
          fixture = {
            query: fallback.query,
            reason: `fallback from live playable title "${fallback.title}"`,
          };
        }
      }

      expect(
        fixture,
        "Expected the Neon catalog to contain at least one playable Chinese song title with a two-character substring"
      ).toBeDefined();

      const searchQuery = fixture!.query;
      const matchingCatalogRows = await db
        .select({ id: songs.id, title: songs.title })
        .from(songs)
        .where(
          and(
            isNull(songs.deletedAt),
            ilike(songs.title, `%${searchQuery}%`),
            sql`exists (
              select 1
              from recordings
              where recordings.song_id = ${songs.id}
                and recordings.deleted_at IS NULL
            )`
          )
        )
        .limit(10);

      expect(
        matchingCatalogRows,
        `Expected the Neon catalog to contain at least one searchable song title with ${searchQuery} (${fixture!.reason})`
      ).not.toHaveLength(0);

      const result = await fullTextSearchSongs(searchQuery, 200, 0, "all");
      const returnedIds = new Set(result.songs.map((song) => song.id));

      expect(result.total).toBeGreaterThan(0);
      expect(
        matchingCatalogRows.some((song) => returnedIds.has(song.id)),
        `Expected fullTextSearchSongs("${searchQuery}") to return at least one known Neon catalog match`
      ).toBe(true);
    },
    20000
  );
});
