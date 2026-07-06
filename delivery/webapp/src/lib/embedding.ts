import OpenAI from "openai";

if (!process.env.SOW_EMBEDDING_API_KEY) {
  throw new Error(
    "SOW_EMBEDDING_API_KEY environment variable not set. " +
    "Set this to your OpenAI-compatible API key for embeddings."
  );
}
if (!process.env.SOW_EMBEDDING_BASE_URL) {
  throw new Error(
    "SOW_EMBEDDING_BASE_URL environment variable not set. " +
    "Set this to your OpenAI-compatible API base URL for embeddings " +
    "(e.g., https://api.openai.com/v1)."
  );
}

const EMBEDDING_MODEL = process.env.SOW_EMBEDDING_MODEL || "text-embedding-3-small";

const openai = new OpenAI({
  apiKey: process.env.SOW_EMBEDDING_API_KEY,
  baseURL: process.env.SOW_EMBEDDING_BASE_URL,
  timeout: 10_000,
  maxRetries: 2,
});

const MODEL = EMBEDDING_MODEL;
const DIMENSIONS = 1536;

export async function embedQuery(text: string): Promise<number[]> {
  const response = await openai.embeddings.create({
    model: MODEL,
    input: text,
    dimensions: DIMENSIONS,
  });
  return response.data[0].embedding;
}

export const QUERY_MODEL = EMBEDDING_MODEL;
