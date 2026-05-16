// fastembed-based embedding client using bge-m3 model (1024 dims, multilingual)
// Requires: pnpm add fastembed

type EmbeddingModelType = {
  queryEmbed(text: string): Promise<Float32Array | number[]>;
};

let modelInstance: EmbeddingModelType | null = null;
let modelInitPromise: Promise<EmbeddingModelType> | null = null;

export const EMBEDDING_MODEL_VERSION = "bge-m3";
export const EMBEDDING_DIMENSIONS = 1024;

async function initModel(): Promise<EmbeddingModelType> {
  // Dynamic import so the module fails gracefully if fastembed isn't installed
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fastembed = await import("fastembed" as any);
  const { FlagEmbedding, EmbeddingModel } = fastembed;

  const model = await FlagEmbedding.init({
    model: EmbeddingModel.BGEM3,
    maxLength: 8192,
  });
  return model as EmbeddingModelType;
}

export async function getEmbeddingModel(): Promise<EmbeddingModelType> {
  if (modelInstance) return modelInstance;

  if (!modelInitPromise) {
    modelInitPromise = initModel().then((m) => {
      modelInstance = m;
      return m;
    });
  }

  return modelInitPromise;
}

export async function generateEmbedding(text: string): Promise<number[]> {
  const model = await getEmbeddingModel();
  const raw = await model.queryEmbed(text);
  return Array.from(raw as number[]);
}

// Reset singleton (for testing only)
export function _resetModelForTesting() {
  modelInstance = null;
  modelInitPromise = null;
}
