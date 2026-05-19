-- Add tsvector generated column and GIN index for full-text search on songs table.
ALTER TABLE "songs" ADD COLUMN "search_vector" tsvector GENERATED ALWAYS AS (
  setweight(to_tsvector('simple', coalesce("title", '')), 'A') ||
  setweight(to_tsvector('simple', coalesce("title_pinyin", '')), 'A') ||
  setweight(to_tsvector('simple', coalesce("composer", '')), 'B') ||
  setweight(to_tsvector('simple', coalesce("lyricist", '')), 'B') ||
  setweight(to_tsvector('simple', coalesce("album_name", '')), 'B')
) STORED;--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "idx_songs_search_vector" ON "songs" USING GIN ("search_vector");
