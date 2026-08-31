"""RAG 基础设施：Ollama bge-m3 embedding + CrossEncoder 重排序"""
import os
import logging

import aiohttp

from backend.core.http import get_http_session

OLLAMA_URL = os.getenv("OLLAMA_HOST", "http://localhost:11434")

logger = logging.getLogger(__name__)


# ═══ Embedding（Ollama bge-m3）═══

async def embed(text: str, model: str = "bge-m3") -> list[float] | None:
    try:
        session = await get_http_session()
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
        logger.warning(f"Embedding 失败: url={OLLAMA_URL}/api/embed model={model} error={e}")
    return None


async def batch_embed(texts: list[str], model: str = "bge-m3") -> list[list[float]]:
    results = []
    for text in texts:
        vec = await embed(text, model)
        if vec:
            results.append(vec)
    return results


# ═══ Rerank（CrossEncoder 精排 pgvector 初筛结果）═══

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import CrossEncoder
        logger.info("加载 reranker 模型 BAAI/bge-reranker-v2-m3 ...")
        _model = CrossEncoder("BAAI/bge-reranker-v2-m3")
    return _model


def rerank(question: str, candidates: list[dict], top_k: int = 3) -> list[dict]:
    """对候选列表重排序，返回 top_k"""
    if not candidates:
        return []

    model = _get_model()
    pairs = [(question, c["question"]) for c in candidates]

    try:
        scores = model.predict(pairs, show_progress_bar=False)
    except Exception as e:
        logger.warning(f"Rerank 失败: {e}")
        return candidates[:top_k]

    scored = list(zip(candidates, scores))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [item[0] for item in scored[:top_k]]
