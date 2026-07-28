-- Migration: 0018_theme_anchors
-- Description: Add theme_anchors table for in-DB theme classification
-- See specs/songset_construct_command_v3.md

CREATE TABLE IF NOT EXISTS theme_anchors (
    theme         TEXT PRIMARY KEY,
    embedding     vector(1536) NOT NULL,
    model_version TEXT NOT NULL DEFAULT 'text-embedding-3-small',
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- HNSW index for fast cosine distance computation
CREATE INDEX IF NOT EXISTS idx_theme_anchors_embedding_cosine
    ON theme_anchors USING hnsw (embedding vector_cosine_ops);
