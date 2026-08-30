"""AgentRuntime — 统一 Agent 运行时：配置 + LLM + Tool Calling + Memory"""
import logging, time, json
import aiohttp
from backend.agents.base import BaseAgent, AgentReply
from backend.agents.config_service import get_agent_definition, get_agent_config
from backend.mcp_service.registry import get_registry

logger = logging.getLogger(__name__)


def _safe_name(mcp_name: str) -> str:
    return mcp_name.replace("/", "_").replace(".", "_")


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

    async def reply(self, tenant_id: int, question: str, context: dict | None = None) -> AgentReply:
        t0 = time.perf_counter()
        config = self._cfg()
        trace = {"steps": [], "total_ms": 0}

        try:
            registry = get_registry()
            tool_schemas = await registry.get_schemas_for(self.key)
        except RuntimeError:
            tool_schemas = []

        answer, trace = await self._call_llm(question, config, tool_schemas, tenant_id, trace, context)

        trace["total_ms"] = round((time.perf_counter() - t0) * 1000)
        tier = "llm" if any(s.get("type") == "llm" for s in trace.get("steps", [])) else "fallback"
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
        """简单 LLM 调用（无 tools），FaqAgent RAG 用"""
        api_key = (config.get("api_key", "") or "").strip()
        base_url = (config.get("base_url", "") or "").strip()
        model = (config.get("model", "") or "").strip()
        if not api_key or not base_url or not model:
            return "", False
        params = self._llm_params(config)
        try:
            async with aiohttp.ClientSession() as session:
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

    async def _call_llm(
        self, question: str, config: dict, tool_schemas: list[dict],
        tenant_id: int, trace: dict, context: dict | None = None,
    ) -> tuple[str, dict]:
        api_key = (config.get("api_key", "") or "").strip()
        base_url = (config.get("base_url", "") or "").strip()
        model = (config.get("model", "") or "").strip()

        if not api_key or not base_url or not model:
            return config.get("fallback_reply", "服务未配置"), trace

        params = self._llm_params(config)
        max_steps = params["max_steps"]

        name_map = {}
        tools = []
        for ts in tool_schemas:
            mcp_name = ts["function"]["name"]
            safe = _safe_name(mcp_name)
            name_map[safe] = mcp_name
            t = json.loads(json.dumps(ts))
            t["function"]["name"] = safe
            tools.append(t)

        for t in tools:
            params = t["function"]["parameters"]
            props = params.setdefault("properties", {})
            props["tenant_id"] = {"type": "integer", "description": "租户ID，必须为1"}
            required = params.setdefault("required", [])
            if "tenant_id" not in required:
                required.append("tenant_id")

        messages = [
            {"role": "system", "content": config.get("system_prompt", "")},
        ]
        if context:
            context_str = json.dumps(context, ensure_ascii=False, indent=2)
            messages.append({
                "role": "system",
                "content": f"【上游节点输出，供你参考】\n{context_str}",
            })
        messages.append({"role": "user", "content": question})

        final_answer = ""

        try:
            async with aiohttp.ClientSession() as session:
                for step in range(max_steps):
                    t_step = time.perf_counter()

                    payload = {
                        "model": model,
                        "messages": messages,
                        "temperature": params["temperature"],
                        "max_tokens": params["max_tokens"],
                    }
                    if tools:
                        payload["tools"] = tools
                        payload["tool_choice"] = "auto"

                    logger.info(
                        f"AgentRuntime [{self.key}] step {step + 1}/{max_steps}, "
                        f"messages={len(messages)}, tools={[t['function']['name'] for t in tools]}"
                    )

                    async with session.post(
                        f"{base_url}/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=60),
                    ) as resp:
                        result = await resp.json()

                    msg = result.get("choices", [{}])[0].get("message", {})
                    messages.append(msg)

                    step_latency = round((time.perf_counter() - t_step) * 1000)
                    trace.setdefault("steps", []).append({
                        "step": step,
                        "type": "llm",
                        "content": msg.get("content"),
                        "latency_ms": step_latency,
                    })

                    tool_calls = msg.get("tool_calls") or []

                    if not tool_calls:
                        final_answer = msg.get("content", "")
                        break

                    for tc in tool_calls:
                        safe = tc["function"]["name"]
                        mcp_name = name_map.get(safe, safe)
                        args = json.loads(tc["function"]["arguments"])
                        args["tenant_id"] = tenant_id

                        t_tool = time.perf_counter()
                        try:
                            registry = get_registry()
                            tool_result = await registry.call_async(mcp_name, args)
                        except Exception as e:
                            tool_result = {"error": str(e)}
                        tool_latency = round((time.perf_counter() - t_tool) * 1000)

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": json.dumps(tool_result, ensure_ascii=False),
                        })

                        trace["steps"].append({
                            "step": step,
                            "type": "tool",
                            "tool": mcp_name,
                            "args": args,
                            "output": tool_result,
                            "latency_ms": tool_latency,
                        })

        except Exception as e:
            logger.warning(f"AgentRuntime [{self.key}] LLM 调用失败: {e}")

        return final_answer or config.get("fallback_reply", "服务暂时不可用"), trace
