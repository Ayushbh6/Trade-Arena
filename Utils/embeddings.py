"""OpenRouter embeddings helper.

Uses the official OpenRouter Python SDK to generate embeddings.
Strategy-neutral and framework-free.
"""

from __future__ import annotations

import os
from typing import List, Optional

from Utils.openrouter import openrouter_client


def _default_embedding_model() -> str:
    return os.getenv("EMBEDDING_MODEL", "openai/text-embedding-3-small")


def embed_texts(
    texts: List[str],
    *,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    dimensions: Optional[int] = None,
) -> List[List[float]]:
    """Return embeddings for a list of texts."""
    if not texts:
        return []
    emb_model = model or _default_embedding_model()
    with openrouter_client(api_key=api_key) as client:
        res = client.embeddings.generate(
            model=emb_model,
            input=texts,
            encoding_format="float",
            dimensions=dimensions,
        )
    return [row.embedding for row in res.data]


def embed_text(
    text: str,
    *,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    dimensions: Optional[int] = None,
) -> List[float]:
    """Return embedding for a single text."""
    return embed_texts([text], model=model, api_key=api_key, dimensions=dimensions)[0]


__all__ = ["embed_texts", "embed_text"]

