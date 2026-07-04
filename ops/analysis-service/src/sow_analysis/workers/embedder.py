"""Embedding worker for generating text embeddings via OpenAI-compatible API."""

import asyncio
import logging
from typing import List

from openai import OpenAI

from ..config import settings
from ..models import EmbeddingJobRequest, EmbeddingJobResult, LineEmbedding
from .exceptions import LLMConfigError

logger = logging.getLogger(__name__)

_CJK_RANGE_START = 0x4E00
_CJK_RANGE_END = 0x9FFF
_MAX_INPUT_CHARS_HEURISTIC = 6000


def _count_cjk_chars(text: str) -> int:
    return sum(1 for ch in text if _CJK_RANGE_START <= ord(ch) <= _CJK_RANGE_END)


class EmbeddingWorker:
    """Generates text embeddings using OpenAI text-embedding-3-small."""

    def __init__(self):
        if not settings.SOW_EMBEDDING_API_KEY:
            raise LLMConfigError(
                "SOW_EMBEDDING_API_KEY environment variable not set. "
                "Set this to your OpenAI-compatible API key for embeddings."
            )
        if not settings.SOW_EMBEDDING_BASE_URL:
            raise LLMConfigError(
                "SOW_EMBEDDING_BASE_URL environment variable not set. "
                "Set this to your OpenAI-compatible API base URL for embeddings "
                "(e.g., https://api.openai.com/v1)."
            )
        self._client = OpenAI(
            api_key=settings.SOW_EMBEDDING_API_KEY,
            base_url=settings.SOW_EMBEDDING_BASE_URL,
            timeout=60.0,
            max_retries=2,
        )

    async def embed_song(self, request: EmbeddingJobRequest) -> EmbeddingJobResult:
        song_text = f"{request.title} {request.composer} {request.lyrics_raw}".strip()

        if len(song_text) > _MAX_INPUT_CHARS_HEURISTIC:
            logger.warning(
                "Song %s lyrics exceed %d chars, OpenAI will truncate at 8191 tokens",
                request.song_id,
                _MAX_INPUT_CHARS_HEURISTIC,
            )

        song_embedding = await self._embed_texts([song_text])

        eligible_lines = [
            (i, line)
            for i, line in enumerate(request.lyrics_lines)
            if _count_cjk_chars(line) >= 4
        ]

        line_texts = [line for _, line in eligible_lines]
        line_embeddings_raw = (
            await self._embed_texts(line_texts) if line_texts else []
        )

        line_embeddings = [
            LineEmbedding(
                line_index=idx,
                line_text=line,
                embedding=emb,
            )
            for (idx, line), emb in zip(eligible_lines, line_embeddings_raw)
        ]

        return EmbeddingJobResult(
            song_id=request.song_id,
            embedding=song_embedding[0],
            line_embeddings=line_embeddings,
            model_version="text-embedding-3-small",
            content_hash=request.content_hash,
        )

    async def _embed_texts(self, texts: List[str]) -> List[List[float]]:
        response = await asyncio.to_thread(
            self._client.embeddings.create,
            model=settings.SOW_EMBEDDING_MODEL,
            input=texts,
            dimensions=1536,
        )
        return [d.embedding for d in sorted(response.data, key=lambda x: x.index)]
