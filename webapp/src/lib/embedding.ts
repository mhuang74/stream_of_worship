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

export const QUERY_MODEL = "openai-text-embedding-3-small";
