from dataclasses import dataclass, field


@dataclass
class CacheConfig:
    """工作流节点级别的缓存配置，来源于 workflow node data.cache"""

    enabled: bool = False
    strategy: str = "exact"  # "none" | "exact" | "semantic"
    threshold: float = 0.90
    ttl: int = 604800  # 秒，默认 7 天
    min_score: float = 0.40  # CachePolicyEngine 综合分最低门槛（精确缓存）
    semantic_min_score: float = 0.70  # 综合分达到此分数才写入 L2 语义索引
    min_answer_length: int = 50
    max_question_length: int = 10_000

    @classmethod
    def from_node_config(cls, raw: dict | None) -> "CacheConfig":
        if not raw:
            return cls()
        return cls(
            enabled=raw.get("enabled", cls.enabled),
            strategy=raw.get("strategy", cls.strategy),
            threshold=raw.get("threshold", cls.threshold),
            ttl=raw.get("ttl", cls.ttl),
            min_score=raw.get("min_score", cls.min_score),
            semantic_min_score=raw.get("semantic_min_score", cls.semantic_min_score),
            min_answer_length=raw.get("min_answer_length", cls.min_answer_length),
            max_question_length=raw.get("max_question_length", cls.max_question_length),
        )
