"""Async client for the Jina embeddings API.

Used by the topic-flow clustering pipeline. Requests embeddings with
`task="clustering"` so Jina applies the clustering-task LoRA adapter
(tighter, more separated clusters than the retrieval variant).
"""

import asyncio
import logging
from typing import Any

import httpx

from backend.app.config import settings

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(timeout=120, connect=10)
_BATCH_SIZE = 64
_MAX_RETRIES = 3


async def embed_clustering(texts: list[str]) -> list[list[float]]:
    """Return one 1024-dim vector per input text.

    Batches internally and retries 429/5xx with exponential backoff.
    Raises if the API key is missing or repeated failures exhaust retries.
    """
    if not texts:
        return []
    if not settings.jina_api_key:
        raise RuntimeError("JINA_API_KEY is not configured")

    out: list[list[float]] = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for start in range(0, len(texts), _BATCH_SIZE):
            batch = texts[start : start + _BATCH_SIZE]
            vectors = await _embed_batch(client, batch)
            out.extend(vectors)
            logger.info(
                "jina embeddings: %d/%d done",
                len(out),
                len(texts),
            )
    return out


async def _embed_batch(client: httpx.AsyncClient, batch: list[str]) -> list[list[float]]:
    payload: dict[str, Any] = {
        "model": settings.jina_clustering_model,
        "task": settings.jina_clustering_task,
        "input": batch,
        "embedding_type": "float",
    }
    headers = {
        "Authorization": f"Bearer {settings.jina_api_key}",
        "Content-Type": "application/json",
    }
    delay = 1.0
    for attempt in range(_MAX_RETRIES):
        resp = await client.post(
            settings.jina_embeddings_url,
            json=payload,
            headers=headers,
        )
        if resp.status_code in (429, 500, 502, 503, 504):
            logger.warning(
                "jina embeddings transient %s (attempt %d/%d): %s",
                resp.status_code,
                attempt + 1,
                _MAX_RETRIES,
                resp.text[:500],
            )
            await asyncio.sleep(delay)
            delay *= 2
            continue
        if resp.status_code >= 400:
            # Don't retry 4xx — surface the body so we can see what Jina rejected.
            raise RuntimeError(
                f"jina embeddings {resp.status_code}: {resp.text[:1000]}"
            )
        data = resp.json()
        return [item["embedding"] for item in data["data"]]
    raise RuntimeError("jina embeddings: exhausted retries on transient errors")
