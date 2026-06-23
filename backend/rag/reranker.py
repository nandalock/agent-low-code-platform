"""CrossEncoder 重排序，精排 pgvector 初筛结果"""
import logging

logger = logging.getLogger(__name__)

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
