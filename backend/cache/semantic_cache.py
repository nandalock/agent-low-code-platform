import json
import logging
import hashlib
import uuid
import time

from backend.cache.config import CacheConfig
from backend.cache.redis_client import get_redis
from backend.cache.policy.engine import CachePolicyEngine, CacheDecision
from backend.rag.ollama_embed import embed

logger = logging.getLogger(__name__)

INDEX_NAME = "idx:cache:sem"


class SemanticCache:
    """语义缓存引擎。

    由工作流节点调用，组合 CachePolicyEngine + Redis 存储。

    用法：
        cache = SemanticCache(config=CacheConfig(...), node_id="node_1", agent_config={...})
        hit, answer, tier = await cache.lookup("如何退货")
        if not hit:
            # 调 agent ...
            await cache.store("如何退货", answer, trace)
    """

    def __init__(self, config: CacheConfig, node_id: str, agent_config: dict | None = None):
        self.config = config
        self.node_id = node_id
        self.policy = CachePolicyEngine(config, agent_config)
        self._index_ready = False

    # ── public API ──

    async def lookup(self, question: str) -> tuple[bool, str | None, str, float | None]:
        """返回 (命中, 答案, 命中层级, 相似度分数)。

        miss 时返回 (False, None, "miss", None)。
        exact 命中返回 (True, answer, "exact", 1.0)。
        semantic 命中返回 (True, answer, "semantic", similarity)。
        """
        if not self.config.enabled:
            return False, None, "miss", None

        try:
            r = await get_redis()

            # L1: exact match
            ek = _exact_key(self.node_id, question)
            cached = await r.get(ek)
            if cached:
                logger.debug(f"[cache] L1 hit: {self.node_id}")
                return True, cached, "exact", 1.0

            # L2: semantic match
            if self.config.strategy != "semantic":
                return False, None, "miss", None

            vec = await embed(question)
            if vec is None:
                logger.warning(f"[cache] L2 lookup 跳过: node={self.node_id} embedding 返回 None")
                return False, None, "miss", None

            await self._ensure_index(r)
            result = await r.execute_command(
                "FT.SEARCH", INDEX_NAME,
                f"@node_id:{{{self.node_id}}} =>[KNN 3 @embedding $vec AS similarity]",
                "PARAMS", "2", "vec", _vec_to_bytes(vec),
                "SORTBY", "similarity",
                "DIALECT", "2",
                "RETURN", "3", "answer", "similarity", "question",
            )

            # RediSearch returns: [N, key1, [field, val, ...], key2, ...]
            for i in range(1, len(result), 2):
                fields = _parse_fields(result[i + 1])
                similarity = float(fields.get("similarity", 0))
                if similarity >= self.config.threshold:
                    logger.debug(f"[cache] L2 hit: {self.node_id} similarity={similarity:.3f}")
                    return True, fields.get("answer", ""), "semantic", similarity

            return False, None, "miss", None

        except Exception as e:
            logger.warning(f"[cache] lookup 异常，降级为 miss: node={self.node_id} error={e}", exc_info=True)
            return False, None, "miss", None

    async def store(
        self,
        question: str,
        answer: str,
        intent: str = "unknown",
        intent_confidence: float = 0.5,
        fallback_reply: str = "",
        tier: str = "llm",
        has_error: bool = False,
        trace: dict | None = None,
    ) -> CacheDecision | None:
        """通过准入规则评估后，写入缓存。

        准入不通过返回 CacheDecision（含拒绝原因），通过返回 CacheDecision 并写入 Redis。
        """
        if not self.config.enabled:
            return None

        decision = self.policy.evaluate(
            question=question,
            answer=answer,
            intent=intent,
            intent_confidence=intent_confidence,
            fallback_reply=fallback_reply,
            tier=tier,
            has_error=has_error,
            trace=trace,
        )

        if not decision.should_cache:
            logger.info(f"[cache] store 拒绝: node={self.node_id} {decision.summary}")
            return decision

        try:
            r = await get_redis()

            # L1 exact
            ek = _exact_key(self.node_id, question)
            await r.set(ek, answer, ex=decision.ttl)
            logger.debug(f"[cache] L1 已写入: {ek}")

            # L2 semantic
            if decision.strategy == "semantic":
                logger.info(f"[cache] 开始写 L2: node={self.node_id} score={decision.score:.3f} question={question[:80]}")
                vec = await embed(question)
                if vec is None:
                    logger.warning(f"[cache] L2 跳过: node={self.node_id} embedding 返回 None（检查 Ollama bge-m3 是否可用）")
                else:
                    await self._ensure_index(r)
                    key = _semantic_key()
                    await r.hset(
                        key,
                        mapping={
                            "embedding": _vec_to_bytes(vec),
                            "question": question,
                            "answer": answer,
                            "node_id": self.node_id,
                            "created_at": int(time.time()),
                        },
                    )
                    await r.expire(key, decision.ttl)
                    logger.info(f"[cache] L2 已写入: {key} node={self.node_id} score={decision.score:.3f} ttl={decision.ttl}s")
            else:
                logger.info(f"[cache] L2 跳过: node={self.node_id} strategy={decision.strategy} (需 semantic, score={decision.score:.3f})")

            logger.info(f"[cache] store 成功: {self.node_id} strategy={decision.strategy} ttl={decision.ttl}s")

        except Exception as e:
            logger.warning(f"[cache] store 写入失败: node={self.node_id} error={e}", exc_info=True)

        return decision

    # ── internal ──

    async def _ensure_index(self, r):
        if self._index_ready:
            return
        try:
            await r.execute_command("FT.INFO", INDEX_NAME)
            logger.debug(f"[cache] RediSearch 索引已存在: {INDEX_NAME}")
        except Exception:
            logger.info(f"[cache] 创建 RediSearch 索引: {INDEX_NAME}")
            await r.execute_command(
                "FT.CREATE", INDEX_NAME,
                "ON", "HASH", "PREFIX", "1", "cache:sem:",
                "SCHEMA",
                "embedding", "VECTOR", "HNSW", "6",
                "DIM", "1024", "TYPE", "FLOAT32", "DISTANCE_METRIC", "COSINE",
                "question", "TEXT",
                "answer", "TEXT",
                "node_id", "TAG",
                "created_at", "NUMERIC", "SORTABLE",
            )
            logger.info(f"[cache] RediSearch 索引创建成功: {INDEX_NAME}")
        self._index_ready = True


# ── helpers ──

def _exact_key(node_id: str, question: str) -> str:
    q = question.strip().lower()
    h = hashlib.md5(q.encode()).hexdigest()
    return f"cache:exact:{node_id}:{h}"


def _semantic_key() -> str:
    return f"cache:sem:{uuid.uuid4().hex[:12]}"


def _vec_to_bytes(vec: list[float]) -> bytes:
    import numpy as np
    return np.array(vec, dtype=np.float32).tobytes()


def _parse_fields(fields: list) -> dict:
    d = {}
    for j in range(0, len(fields), 2):
        k = fields[j] if isinstance(fields[j], str) else fields[j].decode()
        v = fields[j + 1]
        d[k] = v if isinstance(v, str) else v.decode()
    return d
