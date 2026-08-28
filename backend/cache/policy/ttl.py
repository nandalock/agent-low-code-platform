class TTLStrategy:
    """综合评分 → TTL 映射。

    高信任 → 长 TTL，含敏感实体 → 缩短。
    """

    def compute(self, composite_score: float, factors: list) -> int:
        entity_score = 1.0
        for f in factors:
            if f.name == "entity":
                entity_score = f.score
                break

        if composite_score >= 0.85:
            base = 7 * 86400  # 7 天
        elif composite_score >= 0.60:
            base = 3 * 86400  # 3 天
        elif composite_score >= 0.40:
            base = 86400  # 1 天
        else:
            base = 3600  # 1 小时

        if entity_score < 0.5:
            base = min(base, 3600)

        return base
