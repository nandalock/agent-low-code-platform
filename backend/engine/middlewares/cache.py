import time
import logging

from backend.cache.config import CacheConfig
from backend.cache.semantic_cache import SemanticCache
from backend.engine.middlewares.base import Middleware

logger = logging.getLogger(__name__)


def _timeline_entry(node_id: str, label: str, node_type: str, output: str, ms: int,
                     cache: dict | None = None) -> list:
    entry = {"node_id": node_id, "label": label, "node_type": node_type, "output": output, "ms": ms}
    if cache is not None:
        entry["cache"] = cache
    return [entry]


class CacheMiddleware(Middleware):
    """缓存中间件。前置查缓存（命中直接返回），后置异步写缓存。"""

    def __init__(self, node_id: str, cache_config: dict, cache_policy: dict | None = None):
        self.node_id = node_id
        self.config = CacheConfig.from_node_config(cache_config)
        agent_config = {"cache_policy": cache_policy} if cache_policy else None
        self.cache = SemanticCache(self.config, node_id, agent_config)

    async def process(self, state: dict, node_id: str, next_handler) -> dict:
        question = state.get("input", "")

        # ── 前置：查缓存 ──
        t0 = time.time()
        hit, answer, tier, score = await self.cache.lookup(question)
        elapsed_ms = int((time.time() - t0) * 1000)

        if hit:
            logger.debug(f"[cache] middleware hit: {node_id} tier={tier} score={score}")
            return {
                "node_results": {node_id: answer},
                "node_timeline": _timeline_entry(
                    node_id, f"缓存命中 ({tier})", "agent", str(answer)[:200], elapsed_ms,
                    cache={"hit": True, "tier": tier, "score": score},
                ),
            }

        # ── 未命中：预评估 policy 打分 ──
        pre_eval = self.cache.policy.evaluate(
            question=question,
            answer="",
            intent="unknown",
            intent_confidence=0.5,
        )
        cache_info = {
            "hit": False,
            "tier": "miss",
            "score": pre_eval.score,
            "factors": [
                {"name": f.name, "score": f.score, "weight": f.weight, "reason": f.reason, "suggestion": f.suggestion}
                for f in pre_eval.factors
            ],
            "summary": pre_eval.summary,
        }

        # ── 执行下游 agent ──
        result = await next_handler(state)

        # ── 后置：写缓存并取实际决策 ──
        answer_text = ""
        nr = result.get("node_results", {})
        if isinstance(nr, dict):
            answer_text = str(nr.get(node_id, ""))

        actual_decision = None
        if answer_text:
            actual_decision = await self.cache.store(
                question=question,
                answer=answer_text,
            )

        # 用实际存储决策覆盖预评估（确保思考过程展示与真实行为一致）
        if actual_decision is not None:
            cache_info = {
                "hit": False,
                "tier": "miss",
                "score": actual_decision.score,
                "factors": [
                    {"name": f.name, "score": f.score, "weight": f.weight, "reason": f.reason, "suggestion": f.suggestion}
                    for f in actual_decision.factors
                ],
                "summary": actual_decision.summary,
            }

        miss_entry = _timeline_entry(
            node_id, "缓存未命中 → 调用 Agent", "cache", "", elapsed_ms,
            cache=cache_info,
        )
        result["node_timeline"] = miss_entry + result.get("node_timeline", [])
        return result
