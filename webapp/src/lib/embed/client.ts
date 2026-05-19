// Embedding client — fastembed removed from deployment; semantic search
// will be replaced with pre-computed embeddings (Task 15).
// This module is kept as a stub so existing route imports compile.

export const EMBEDDING_MODEL_VERSION = "bge-m3";
export const EMBEDDING_DIMENSIONS = 1024;

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export async function generateEmbedding(_text: string): Promise<number[]> {
  throw new Error(
    "Runtime embedding is no longer available. " +
    "Semantic search will be replaced with pre-computed embeddings."
  );
}

export function _resetModelForTesting() {}
