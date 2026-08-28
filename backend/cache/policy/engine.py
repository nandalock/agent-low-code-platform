from dataclasses import dataclass, field

from backend.cache.config import CacheConfig
from backend.cache.policy.scorers import (
    AgentConfigScorer,
    IntentScorer,
    EntityScorer,
    QualityScorer,
    ScoreResult,
)

import logging

logger = logging.getLogger(__name__)


@dataclass
class CacheDecision:
    """最终缓存决策"""

    should_cache: bool
    score: float  # 0.0 ~ 1.0 加权综合分
    strategy: str  # "none" | "exact" | "semantic"
    ttl: int  # 秒
    factors: list[ScoreResult] = field(default_factory=list)
    summary: str = ""


# ── 全部可用评分器（新增 scorer 只需在此注册）──

_ALL_SCORERS = {
    "agent_config": AgentConfigScorer(),
    "intent": IntentScorer(),
    "entity": EntityScorer(),
    "quality": QualityScorer(),
}

# scorer key → evaluate 参数名映射
_SCORER_PARAMS: dict[str, list[str]] = {
    "agent_config": ["agent_config", "cache_policy"],
    "intent": ["intent", "intent_confidence", "cache_policy"],
    "entity": ["question", "cache_policy"],
    "quality": ["answer", "fallback_reply", "tier", "has_error", "trace"],
}


class CachePolicyEngine:
    """多维评分缓存策略引擎。

    通过 cache_policy 显式配置哪些评分器启用及权重。
    未配置 scorer_weights → 全部启用，均权。
    参考：GPTCache（zilliztech）的可插拔评估接口设计。

    用法：
        engine = CachePolicyEngine(config, agent_config)
        decision = engine.evaluate(question="...", answer="...", intent="faq", ...)
        if decision.should_cache:
            await cache.store(question, answer, decision)
    """

    def __init__(self, config: CacheConfig, agent_config: dict | None = None):
        self.config = config
        self._agent_config = agent_config or {}
        self._cache_policy = self._agent_config.get("cache_policy") if self._agent_config else None

    def evaluate(
        self,
        question: str = "",
        answer: str = "",
        intent: str = "unknown",
        intent_confidence: float = 0.5,
        fallback_reply: str = "",
        tier: str = "llm",
        has_error: bool = False,
        trace: dict | None = None,
    ) -> CacheDecision:
        """评估是否应该缓存这个回答。"""
        ctx = {
            "agent_config": self._agent_config,
            "cache_policy": self._cache_policy,
            "intent": intent,
            "intent_confidence": intent_confidence,
            "question": question,
            "answer": answer,
            "fallback_reply": fallback_reply,
            "tier": tier,
            "has_error": has_error,
            "trace": trace,
        }

        weights = self._resolve_weights()
        factors: list[ScoreResult] = []

        for key, scorer in _ALL_SCORERS.items():
            weight = weights.get(key, 0.0)
            if weight <= 0:
                continue

            param_names = _SCORER_PARAMS.get(key, [])
            kwargs = {k: ctx[k] for k in param_names if k in ctx}
            kwargs["weight"] = weight
            result = scorer.score(**kwargs)
            factors.append(result)

        # quality 硬否决：异常/兜底/转人工 → 强制不缓存
        quality_hard_reject = any(
            f.name == "quality" and f.score == 0.0 for f in factors
        )

        total_weight = sum(f.weight for f in factors)
        composite = (
            0.0 if quality_hard_reject
            else (
                sum(f.score * f.weight for f in factors) / total_weight
                if total_weight > 0
                else 0.50  # 无评分器启用时中性分，是否缓存看 min_score 阈值
            )
        )

        # content_hint 直接加成（非评分器，不占权重）
        _HINT_BONUS = {"knowledge": 0.10, "operation": -0.15, "realtime": -0.25}
        if self._cache_policy and "content_hint" in self._cache_policy:
            hint = self._cache_policy["content_hint"]
            bonus = _HINT_BONUS.get(hint, 0)
            composite = max(0.0, min(1.0, composite + bonus))

        ttl = self.config.ttl

        if composite >= self.config.semantic_min_score:
            strategy = "semantic"
        elif composite >= self.config.min_score:
            strategy = "exact"
        else:
            strategy = "none"

        summary = self._build_summary(factors, strategy, composite, ttl)

        return CacheDecision(
            should_cache=strategy != "none",
            score=round(composite, 3),
            strategy=strategy,
            ttl=ttl,
            factors=factors,
            summary=summary,
        )

    def _resolve_weights(self) -> dict[str, float]:
        """解析评分器权重。

        cache_policy.scorer_weights 显式指定 → 只启用列出的评分器。
        未指定 → 全部启用，均权。
        """
        if self._cache_policy and "scorer_weights" in self._cache_policy:
            raw = self._cache_policy["scorer_weights"]
            if isinstance(raw, dict):
                return {k: float(v) for k, v in raw.items() if float(v) > 0}

        # 未配置：全部启用，均权
        n = len(_ALL_SCORERS)
        return {k: 1.0 / n for k in _ALL_SCORERS}

    def _build_summary(self, factors: list[ScoreResult], strategy: str, score: float, ttl: int) -> str:
        blocked = [f for f in factors if f.suggestion == "no_cache"]
        weak = [f for f in factors if f.suggestion == "weak_cache"]

        if strategy == "semantic":
            base = f"语义缓存 (score={score:.2f}), TTL={ttl}s"
        elif strategy == "exact":
            base = f"精确缓存 (score={score:.2f}), TTL={ttl}s"
        else:
            if blocked:
                return f"不缓存 (score={score:.2f})，原因: {', '.join(f.reason for f in blocked)}"
            if weak:
                return f"不缓存 (score={score:.2f})，弱项: {', '.join(f.reason for f in weak)}"
            return f"不缓存 (score={score:.2f})"

        if blocked:
            base += f"，低分项: {', '.join(f.reason for f in blocked)}"
        return base
