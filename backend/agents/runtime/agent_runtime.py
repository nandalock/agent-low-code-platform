"""AgentRuntime — 统一 Agent 运行时：配置 + ToolRegistry + LLM 参数 + 执行调度

职责：加载 agent definition / config，获取 ToolRegistry 与 tool schemas，准备 LLM 参数，
创建并驱动 AgentLoop，返回最终结果。Agent 执行循环（LLM → Tool → LLM）在 AgentLoop 中。
"""
import logging
import time

import aiohttp

from backend.agents.base import BaseAgent, AgentReply
from backend.agents.config_service import get_agent_definition, get_agent_config
from backend.agents.runtime.agent_loop import AgentLoop
from backend.agents.runtime.events import EventSink
from backend.core.http import get_http_session
from backend.mcp_service.registry import get_registry

logger = logging.getLogger(__name__)


class AgentRuntime(BaseAgent):
    """通用 Agent 运行时 — 配置来自 agent_definitions + agent_configs 表"""

    def __init__(self, key: str, definition: dict | None = None):
        if definition is None:
            definition = get_agent_definition(key) or {}
        self.key = key
        self.name = definition.get("name", key)
        self.desc = definition.get("description", "")
        self.status = definition.get("status", "active")
        self._definition = definition

    def _cfg(self) -> dict:
        """合并 agent_definitions.config + agent_configs 覆盖"""
        base = self._definition.get("config", {}) if self._definition else {}
        overrides = get_agent_config(self.key) or {}
        return {**base, **overrides}

    async def reply(
        self, tenant_id: int, question: str,
        context: dict | None = None,
        on_event: EventSink | None = None,
    ) -> AgentReply:
        t0 = time.perf_counter()
        config = self._cfg()
        trace = {"steps": [], "total_ms": 0}

        try:
            registry = get_registry()
            tool_schemas = await registry.get_schemas_for(self.key)
        except RuntimeError:
            tool_schemas = []

        # 准备运行环境，创建并驱动 AgentLoop（执行循环在 Loop 内部）
        loop = AgentLoop(
            key=self.key,
            config=config,
            llm_params=self._llm_params(config),
            tool_schemas=tool_schemas,
            tenant_id=tenant_id,
            trace=trace,
            on_event=on_event,
        )
        answer = await loop.run(question, context)

        trace["total_ms"] = round((time.perf_counter() - t0) * 1000)
        tier = "llm" if any(s.get("type") == "llm" for s in trace.get("steps", [])) else "fallback"
        if on_event:
            await on_event({"type": "done", "answer": answer, "tier": tier, "trace": trace})
        return AgentReply(answer=answer, tier=tier, trace=trace)

    def _llm_params(self, config: dict) -> dict:
        """LLM 调用参数，全部从 agent 配置读取，默认值=现行为（零破坏）"""
        return {
            "max_steps": int(config.get("max_steps", 5)),
            "max_tokens": int(config.get("max_tokens", 1024)),
            "max_tokens_plain": int(config.get("max_tokens_plain", 512)),
            "temperature": float(config.get("temperature", 0.3)),
        }

    async def _call_llm_plain(self, messages: list[dict], config: dict) -> tuple[str, bool]:
        """简单 LLM 调用（无 tools），FaqAgent RAG 用（非 Agent 循环，留在 Runtime）"""
        api_key = (config.get("api_key", "") or "").strip()
        base_url = (config.get("base_url", "") or "").strip()
        model = (config.get("model", "") or "").strip()
        if not api_key or not base_url or not model:
            return "", False
        params = self._llm_params(config)
        try:
            session = await get_http_session()
            async with session.post(
                f"{base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": messages, "temperature": params["temperature"], "max_tokens": params["max_tokens_plain"]},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                result = await resp.json()
                answer = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                return answer, bool(answer)
        except Exception as e:
            logger.warning(f"AgentRuntime [{self.key}] LLM plain 调用失败: {e}")
            return "", False
