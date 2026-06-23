"""Ollama bge-m3 embedding"""
import os
import logging

import aiohttp

OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434")

logger = logging.getLogger(__name__)


async def embed(text: str, model: str = "bge-m3") -> list[float] | None:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OLLAMA_URL}/api/embed",
                json={"model": model, "input": text},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                result = await resp.json()
                embeddings = result.get("embeddings", [])
                if embeddings:
                    return embeddings[0]
    except Exception as e:
        logger.warning(f"Embedding 失败: {e}")
    return None


async def batch_embed(texts: list[str], model: str = "bge-m3") -> list[list[float]]:
    results = []
    for text in texts:
        vec = await embed(text, model)
        if vec:
            results.append(vec)
    return results
