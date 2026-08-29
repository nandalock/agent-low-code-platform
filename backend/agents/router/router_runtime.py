"""RouterRuntime — 可配置 L1/L2/L3 级联意图路由

路由策略:
  L1 关键词子串匹配 (<1ms)
     ↓ 未命中
  L2-B 历史向量匹配 (pgvector 余弦相似度)
     ↓ 未命中
  L2-C 描述向量匹配 (embedding 余弦相似度)
     ↓ 未命中
  L3 LLM Function Calling (自动生成路由工具, tool_choice=required)

返回值: AgentReply(answer=json, tier="router")
  answer = {"agent_key": "...", "route_level": "L1|L2|L3", "confidence": 0.xx}
"""
import asyncio
import json
import logging
import time

import aiohttp

from backend.agents.base import BaseAgent, AgentReply
from backend.agents.config_service import get_agent_config
from backend.core.rag import embed

logger = logging.getLogger(__name__)


# ── 描述向量缓存（按 agent_key，配置保存时失效） ──

_desc_cache: dict[str, dict[str, list[float]]] = {}  # router_key -> {agent_key: vector}


def invalidate_desc_cache(router_key: str):
    _desc_cache.pop(router_key, None)


# ── L3 系统提示词模板 ──

_L3_BASE_PROMPT = """你是一个意图路由系统。分析用户消息，从可用路由中选择最匹配的一个。

可用路由:
{agent_list}

{fewshot_examples}
规则:
1. **必须**调用一个 function，不要直接回复文本
2. 仔细阅读每个路由的描述，选择描述与用户问题最匹配的路由
3. 在 reason 参数中简要说明为什么选择该路由
4. 在 confidence 参数中标明把握程度：
   - very_high: 问题与路由描述完全匹配
   - high: 问题与路由描述很匹配
   - medium: 基本能对应
   - low: 勉强对应，不太确定
   - very_low: 乱码、无意义输入、与所有路由都无关
5. 如果没有任何路由的描述与用户问题匹配，选最接近的一个，confidence 用 very_low"""


# ── RouterRuntime ──

class RouterRuntime(BaseAgent):
    """可配置的三层意图路由器"""

    def __init__(self, key: str, definition: dict | None = None):
        from backend.agents.config_service import get_agent_definition

        if definition is None:
            definition = get_agent_definition(key) or {}
        self.key = key
        self.name = definition.get("name", key)
        self.desc = definition.get("description", "")
        self.status = definition.get("status", "active")
        self._definition = definition

    def _cfg(self) -> dict:
        base = self._definition.get("config", {}) if self._definition else {}
        overrides = get_agent_config(self.key) or {}
        return {**base, **overrides}

    # ── 入口 ──

    async def reply(self, tenant_id: int, question: str, context: dict | None = None) -> AgentReply:
        t0 = time.perf_counter()
        config = self._cfg()
        steps: list[dict] = []
        agent_key: str | None = None
        confidence: float = 0.0
        route_level: str = "L3"

        # L1: 关键词
        if config.get("l1_enabled", True):
            agent_key, confidence = await self._l1_match(question, config)
            if agent_key:
                route_level = "L1"
                steps.append({"level": "L1", "method": "keyword", "agent_key": agent_key, "confidence": confidence})

        # L2: 向量语义
        if not agent_key and config.get("l2_embedding", {}).get("enabled", True):
            # L2-B: 历史向量
            if config.get("l2_embedding", {}).get("history_enabled", True):
                result = await self._l2b_history_search(question, tenant_id, config)
                if result:
                    agent_key = result["agent_key"]
                    confidence = result["confidence"]
                    route_level = "L2"
                    steps.append({"level": "L2-B", "method": "history_vector", **result})
                else:
                    steps.append({"level": "L2-B", "method": "history_vector", "result": None})

            # L2-C: 描述向量（未命中级联或加权并行）
            if not agent_key and config.get("l2_embedding", {}).get("description_enabled", True):
                result = await self._l2c_description_match(question, config)
                if result["agent_key"]:
                    agent_key = result["agent_key"]
                    confidence = result["confidence"]
                    route_level = "L2"
                steps.append({"level": "L2-C", "method": "description_vector", **result})

        # L3: LLM Function Calling
        if not agent_key and config.get("l3_llm", {}).get("enabled", True):
            agent_key, confidence, fc_steps = await self._l3_fc_route(question, config, context)
            route_level = "L3"
            if agent_key is None:
                # L3 置信度过低，拦截不路由
                steps.append({
                    "level": "L3", "method": "llm_fc",
                    "agent_key": None, "confidence": confidence,
                    "action": "blocked", "fc_steps": fc_steps,
                })
                answer = json.dumps({
                    "agent_key": None,
                    "route_level": "L3",
                    "confidence": confidence,
                    "action": "clarify",
                    "message": "抱歉，我不太理解您的问题，请再详细描述一下您的需求。",
                }, ensure_ascii=False)
                total_ms = round((time.perf_counter() - t0) * 1000)
                return AgentReply(
                    answer=answer,
                    tier="router",
                    trace={"route_level": "L3", "confidence": confidence, "action": "blocked", "steps": steps, "total_ms": total_ms},
                )
            steps.append({
                "level": "L3", "method": "llm_fc",
                "agent_key": agent_key, "confidence": confidence, "fc_steps": fc_steps,
            })

        # 所有层都未命中（全部关闭或 L3 关闭后无兜底）
        if not agent_key:
            fallback = config.get("l3_llm", {}).get("fallback_agent", "human_handoff")
            if config.get("l3_llm", {}).get("enabled", True) is False:
                route_level = "fallback"
            steps.append({"level": "fallback", "method": "no_match", "agent_key": fallback, "confidence": 0})
            agent_key = fallback
            confidence = 0

        # 异步持久化（不阻塞响应）
        asyncio.ensure_future(
            self._save_history(question, agent_key, route_level, confidence, tenant_id, config)
        )

        answer = json.dumps({
            "agent_key": agent_key,
            "route_level": route_level,
            "confidence": confidence,
        }, ensure_ascii=False)

        total_ms = round((time.perf_counter() - t0) * 1000)
        return AgentReply(
            answer=answer,
            tier="router",
            trace={"route_level": route_level, "confidence": confidence, "steps": steps, "total_ms": total_ms},
        )

    # ── L1: 关键词匹配（走独立表 router_l1_keywords） ──

    async def _l1_match(self, question: str, config: dict) -> tuple[str | None, float]:
        try:
            from backend.core.connection import get_conn
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT keywords, target FROM router_l1_keywords WHERE router_key = %s ORDER BY id",
                        (self.key,),
                    )
                    rows = cur.fetchall()
            for row in rows:
                keywords = row.get("keywords") or []
                target = row.get("target", "")
                if not target:
                    continue
                for kw in keywords:
                    if kw and kw in question:
                        return target, 1.0
        except Exception as e:
            logger.warning(f"[{self.key}] L1 keyword query failed: {e}")
        return None, 0.0

    # ── L2-B: 历史向量匹配 ──

    async def _l2b_history_search(self, question: str, tenant_id: int, config: dict) -> dict | None:
        l2 = config.get("l2_embedding", {})
        threshold = l2.get("threshold", 0.85)
        top_k = l2.get("top_k", 3)
        model = l2.get("model", "bge-m3")

        query_vec = await embed(question, model)
        if not query_vec:
            return None

        try:
            from backend.core.connection import get_conn
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT id, target_agent, route_level, confidence,
                                 1 - (embedding <=> %s::vector) AS _score
                           FROM router_history
                           WHERE tenant_id = %s AND router_key = %s
                             AND embedding IS NOT NULL
                           ORDER BY embedding <=> %s::vector
                           LIMIT %s""",
                        (query_vec, tenant_id, self.key, query_vec, top_k),
                    )
                    rows = cur.fetchall()
        except Exception as e:
            logger.warning(f"[{self.key}] L2-B vector search failed: {e}")
            return None

        if not rows:
            return None

        best = max(rows, key=lambda r: r["_score"])
        if best["_score"] >= threshold:
            return {
                "agent_key": best["target_agent"],
                "confidence": round(best["_score"], 4),
                "matched_id": best["id"],
            }
        return None

    # ── L2-C: 描述向量匹配 ──

    async def _l2c_description_match(self, question: str, config: dict) -> dict:
        l2 = config.get("l2_embedding", {})
        threshold = l2.get("threshold", 0.85)
        model = l2.get("model", "bge-m3")
        routable = config.get("routable_agents", [])

        candidates = [a for a in routable if a.get("tool_description", "").strip()]
        if not candidates:
            return {"agent_key": None, "confidence": 0, "threshold": threshold, "model": model, "scores": []}

        query_vec = await embed(question, model)
        if not query_vec:
            return {"agent_key": None, "confidence": 0, "threshold": threshold, "model": model, "scores": []}

        # 获取或创建描述向量缓存
        cache = _desc_cache.setdefault(self.key, {})
        best_key = None
        best_score = 0.0
        all_scores: list[dict] = []

        for agent in candidates:
            akey = agent["key"]
            if akey in cache:
                desc_vec = cache[akey]
            else:
                desc_vec = await embed(agent["tool_description"], model)
                if desc_vec:
                    cache[akey] = desc_vec

            if not desc_vec:
                continue

            score = self._cosine_similarity(query_vec, desc_vec)
            all_scores.append({
                "agent_key": akey,
                "score": round(score, 4),
                "description": agent.get("tool_description", ""),
            })
            if score > best_score:
                best_score = score
                best_key = akey

        hit = best_key is not None and best_score >= threshold
        return {
            "agent_key": best_key if hit else None,
            "confidence": round(best_score, 4),
            "threshold": threshold,
            "model": model,
            "scores": sorted(all_scores, key=lambda s: s["score"], reverse=True),
        }

    # ── L3: LLM Function Calling ──

    async def _l3_fc_route(
        self, question: str, config: dict, context: dict | None = None
    ) -> tuple[str | None, float, list]:
        l3 = config.get("l3_llm", {})
        model = l3.get("model") or config.get("model", "").strip()
        api_key = l3.get("api_key") or config.get("api_key", "").strip()
        base_url = l3.get("base_url") or config.get("base_url", "").strip()
        fallback = l3.get("fallback_agent", "human_handoff")
        system_extra = l3.get("system_prompt_extra", "")
        min_confidence = l3.get("min_confidence", 0.6)

        if not all([model, api_key, base_url]):
            logger.warning(f"[{self.key}] L3 LLM 未配置，兜底 {fallback}")
            return fallback, 0.0, [{"error": "LLM 未配置"}]

        tools = self._build_routing_tools(config)
        if not tools:
            return fallback, 0.0, [{"error": "无可路由 Agent"}]

        # 检索 few-shot 示例
        fewshot = await self._get_fewshot_examples(question, config)

        system_prompt = self._build_system_prompt(config, system_extra, context, fewshot)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{base_url}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": question},
                        ],
                        "tools": tools,
                        "tool_choice": "required",
                        "temperature": 0,
                    },
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    result = await resp.json()

            msg = result.get("choices", [{}])[0].get("message", {})
            tool_calls = msg.get("tool_calls") or []

            if not tool_calls:
                return fallback, 0.0, [{"error": "LLM 未返回 tool_call"}]

            tc = tool_calls[0]
            fn_name = tc["function"]["name"]
            args_str = tc["function"].get("arguments", "{}")
            args = json.loads(args_str) if args_str else {}

            agent_key = self._fn_name_to_agent(fn_name, config)

            # LLM 自评置信度 → 数值
            confidence_map = {"very_high": 0.95, "high": 0.85, "medium": 0.7, "low": 0.4, "very_low": 0.15}
            confidence = confidence_map.get(args.get("confidence", "medium"), 0.7)

            # 置信度低于阈值 → 拦截，不路由
            if confidence < min_confidence:
                return None, confidence, [{"function": fn_name, "args": args, "blocked": True, "reason": "confidence below threshold"}]

            return agent_key, confidence, [{"function": fn_name, "args": args}]

        except Exception as e:
            logger.warning(f"[{self.key}] L3 FC 失败: {e}")
            return fallback, 0.0, [{"error": str(e)}]

    # ── Few-shot 示例检索 ──

    async def _get_fewshot_examples(self, question: str, config: dict) -> str:
        """从 router_history 检索相似历史问题作为 few-shot 示例"""
        l2 = config.get("l2_embedding", {})
        model = l2.get("model", "bge-m3")
        top_k = l2.get("top_k", 3)

        query_vec = await embed(question, model)
        if not query_vec:
            return ""

        try:
            from backend.core.connection import get_conn
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT question, target_agent,
                                 1 - (embedding <=> %s::vector) AS _score
                           FROM router_history
                           WHERE router_key = %s AND embedding IS NOT NULL
                           ORDER BY embedding <=> %s::vector
                           LIMIT %s""",
                        (query_vec, self.key, query_vec, top_k),
                    )
                    rows = cur.fetchall()
        except Exception as e:
            logger.warning(f"[{self.key}] few-shot 检索失败: {e}")
            return ""

        if not rows:
            return ""

        examples = []
        for r in rows:
            examples.append(f'- 用户问「{r["question"]}」→ 路由到 **{r["target_agent"]}**')
        return "参考示例（相似历史问题）:\n" + "\n".join(examples) + "\n"

    # ── 工具生成 ──

    def _build_routing_tools(self, config: dict) -> list[dict]:
        routable = config.get("routable_agents", [])
        tools = []
        for agent in routable:
            akey = agent.get("key", "")
            desc = agent.get("tool_description", "").strip() or f"Route to {akey}"
            safe_name = f"route_to_{akey}"
            tools.append({
                "type": "function",
                "function": {
                    "name": safe_name,
                    "description": desc,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "reason": {
                                "type": "string",
                                "description": f"选择路由到 {akey} 的原因",
                            },
                            "confidence": {
                                "type": "string",
                                "enum": ["very_high", "high", "medium", "low", "very_low"],
                                "description": "把握程度。very_high=完全匹配, high=很匹配, medium=基本匹配, low=勉强匹配, very_low=乱输/无关/无法理解",
                            },
                        },
                        "required": ["reason", "confidence"],
                    },
                },
            })
        return tools

    def _build_system_prompt(self, config: dict, system_extra: str = "", context: dict | None = None, fewshot: str = "") -> str:
        routable = config.get("routable_agents", [])
        lines = []
        for a in routable:
            akey = a.get("key", "")
            desc = a.get("tool_description", "") or a.get("key", "")
            lines.append(f"- **{akey}**: {desc}")
        agent_list = "\n".join(lines) if lines else "(无可用路由)"

        context_str = ""
        if context:
            context_str = f"\n\n## 对话上下文\n{json.dumps(context, ensure_ascii=False, indent=2)}"

        extra_block = f"\n\n## 额外指示\n{system_extra}" if system_extra else ""
        return _L3_BASE_PROMPT.format(
            agent_list=agent_list, fewshot_examples=fewshot,
        ) + extra_block + context_str

    def _fn_name_to_agent(self, fn_name: str, config: dict) -> str:
        prefix = "route_to_"
        if fn_name.startswith(prefix):
            candidate = fn_name[len(prefix):]
            routable = config.get("routable_agents", [])
            for a in routable:
                if a.get("key") == candidate:
                    return candidate
        return config.get("l3_llm", {}).get("fallback_agent", "human_handoff")

    # ── 历史持久化 ──

    async def _save_history(
        self, question: str, target: str, route_level: str,
        confidence: float, tenant_id: int, config: dict,
    ):
        # L1 是确定性规则，不需要向量历史；L3 失败结果也不写
        if route_level == "L1" or confidence <= 0:
            return
        try:
            l2 = config.get("l2_embedding", {})
            model = l2.get("model", "bge-m3")
            vec = None
            try:
                vec = await embed(question, model)
            except Exception:
                pass

            from backend.core.connection import get_conn
            with get_conn() as conn:
                with conn.cursor() as cur:
                    # 已存在相同问题+相同目标的记录则跳过
                    cur.execute(
                        """SELECT id FROM router_history
                           WHERE router_key = %s AND tenant_id = %s
                             AND question = %s AND target_agent = %s
                           LIMIT 1""",
                        (self.key, tenant_id, question, target),
                    )
                    if cur.fetchone():
                        return
                    cur.execute(
                        """INSERT INTO router_history
                           (router_key, tenant_id, question, target_agent, route_level, confidence, embedding)
                           VALUES (%s, %s, %s, %s, %s, %s, %s::vector)""",
                        (self.key, tenant_id, question, target, route_level, confidence, vec),
                    )
        except Exception as e:
            logger.warning(f"[{self.key}] 保存路由历史失败: {e}")

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = (sum(x * x for x in a) ** 0.5)
        nb = (sum(y * y for y in b) ** 0.5)
        if not na or not nb:
            return 0.0
        return dot / (na * nb)
