"""LLM construction and structured-output helpers."""

from __future__ import annotations

import os
from typing import TypeVar

from poc.songset_constructor.config import RunConfig

SchemaT = TypeVar("SchemaT")


def build_chat_model(config: RunConfig):
    if config.no_llm:
        return None
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=config.llm_model,
        api_key=os.environ["SOW_LLM_API_KEY"],
        base_url=os.environ.get("SOW_LLM_BASE_URL"),
        temperature=0.2,
        max_retries=2,
    )


def structured(chat, schema: type[SchemaT]):
    try:
        return chat.with_structured_output(schema, method="json_schema")
    except TypeError:
        return chat.with_structured_output(schema, method="function_calling")
