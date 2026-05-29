DROP TABLE IF EXISTS song_line_embedding;--> statement-breakpoint
DROP TABLE IF EXISTS song_embedding;--> statement-breakpoint

CREATE TABLE song_embedding (
  song_id       TEXT PRIMARY KEY REFERENCES songs(id) ON DELETE CASCADE,
  embedding     vector(1536) NOT NULL,
  model_version TEXT NOT NULL DEFAULT 'openai-text-embedding-3-small',
  content_hash  TEXT NOT NULL,
  created_at    TIMESTAMPTZ DEFAULT NOW()
);--> statement-breakpoint

CREATE INDEX idx_song_embedding_cosine ON song_embedding
  USING hnsw (embedding vector_cosine_ops);--> statement-breakpoint

CREATE TABLE song_line_embedding (
  id           SERIAL PRIMARY KEY,
  song_id      TEXT NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
  line_index   INTEGER NOT NULL,
  line_text    TEXT NOT NULL,
  embedding    vector(1536) NOT NULL,
  model_version TEXT NOT NULL DEFAULT 'openai-text-embedding-3-small'
);--> statement-breakpoint

CREATE INDEX idx_song_line_embedding_song ON song_line_embedding (song_id);--> statement-breakpoint
CREATE INDEX idx_song_line_embedding_cosine ON song_line_embedding
  USING hnsw (embedding vector_cosine_ops);
