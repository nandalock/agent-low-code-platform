"""AgentLoop — 驱动 Agent 核心执行循环：LLM → Tool/MCP → LLM ... 直到 final answer

与 AgentRuntime 的职责边界：
  Runtime 负责提供运行环境（config / tool schemas / llm params / trace / on_event），
  Loop 负责驱动 Agent 执行（构造 messages、调用 LLM、消费 stream、判断 tool_calls、
  执行 ToolRegistry / MCP、把 tool_result 放回 messages、记录 trace、生成最终 answer）。
"""
import asyncio
import json
import logging
import time

import aiohttp

from backend.agents.runtime.events import EventSink, TOOL_POLL_TIMEOUT
from backend.core.http import get_http_session
from backend.mcp_service.registry import get_registry

logger = logging.getLogger(__name__)


def _safe_name(mcp_name: str) -> str:
    return mcp_name.replace("/", "_").replace(".", "_")


class AgentLoop:
    """Agent 执行循环：for step in range(max_steps) 的 LLM → Tool 交替直到收敛"""

    def __init__(
        self,
        *,
        key: str,
        config: dict,
        llm_params: dict,
        tool_schemas: list[dict],
        tenant_id: int,
        trace: dict,
        on_event: EventSink | None = None,
    ):
        self.key = key
        self.config = config
        self.llm_params = llm_params
        self.tool_schemas = tool_schemas
        self.tenant_id = tenant_id
        self.trace = trace
        self.on_event = on_event

    async def run(self, question: str, context: dict | None = None) -> str:
        """驱动完整 Agent 循环，返回最终 answer（含 fallback / 轮数耗尽兜底）"""
        config = self.config
        api_key = (config.get("api_key", "") or "").strip()
        base_url = (config.get("base_url", "") or "").strip()
        model = (config.get("model", "") or "").strip()

        if not api_key or not base_url or not model:
            return config.get("fallback_reply", "服务未配置")

        max_steps = self.llm_params["max_steps"]

        name_map = {}
        tools = []
        for ts in self.tool_schemas:
            mcp_name = ts["function"]["name"]
            safe = _safe_name(mcp_name)
            name_map[safe] = mcp_name
            t = json.loads(json.dumps(ts))
            t["function"]["name"] = safe
            tools.append(t)

        for t in tools:
            # 注意：不要用 params 命名（会覆盖上面的 LLM 参数），这是给 LLM 的 tenant_id 强注入
            tool_params = t["function"]["parameters"]
            props = tool_params.setdefault("properties", {})
            props["tenant_id"] = {"type": "integer", "description": "租户ID，必须为1"}
            required = tool_params.setdefault("required", [])
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
        steps_exhausted = False  # 工具循环跑满 max_steps 未收敛

        try:
            session = await get_http_session()
            for step in range(max_steps):
                t_step = time.perf_counter()

                payload = {
                    "model": model,
                    "messages": messages,
                    "temperature": self.llm_params["temperature"],
                    "max_tokens": self.llm_params["max_tokens"],
                }
                if tools:
                    payload["tools"] = tools
                    payload["tool_choice"] = "auto"
                if self.on_event:
                    payload["stream"] = True  # 有订阅者才流式，其余保持原行为

                logger.info(
                    f"AgentLoop [{self.key}] step {step + 1}/{max_steps}, "
                    f"messages={len(messages)}, tools={[t['function']['name'] for t in tools]}"
                )

                if self.on_event:
                    await self.on_event({"type": "step", "step": step + 1, "total": max_steps})

                if payload.get("stream"):
                    msg = await self._call_llm_stream(
                        session, base_url, api_key, payload, step, t_step,
                    )
                else:
                    async with session.post(
                        f"{base_url}/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=60),
                    ) as resp:
                        result = await resp.json()
                    msg = result.get("choices", [{}])[0].get("message", {})

                messages.append(msg)

                if not payload.get("stream"):
                    step_latency = round((time.perf_counter() - t_step) * 1000)
                    self.trace.setdefault("steps", []).append({
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
                    args["tenant_id"] = self.tenant_id

                    if self.on_event:
                        await self.on_event({"type": "tool_call", "tool": mcp_name, "args": args})

                    t_tool = time.perf_counter()
                    try:
                        registry = get_registry()
                        if self.on_event:
                            tool_result = await self._call_tool_with_progress(registry, mcp_name, args)
                        else:
                            tool_result = await registry.call_async(mcp_name, args)
                    except Exception as e:
                        tool_result = {"error": str(e)}
                    tool_latency = round((time.perf_counter() - t_tool) * 1000)

                    if self.on_event:
                        await self.on_event({
                            "type": "tool_result", "tool": mcp_name,
                            "summary": f"{len(tool_result.get('rows', []))} 条结果",
                            "error": tool_result.get("error"),
                        })

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(tool_result, ensure_ascii=False),
                    })

                    self.trace["steps"].append({
                        "step": step,
                        "type": "tool",
                        "tool": mcp_name,
                        "args": args,
                        "output": tool_result,
                        "latency_ms": tool_latency,
                    })

            else:
                steps_exhausted = True

        except Exception as e:
            logger.warning(f"AgentLoop [{self.key}] LLM 调用失败: {e}")

        if final_answer:
            return final_answer
        if steps_exhausted:
            # 轮数耗尽：明确告知而不是模糊的「服务不可用」
            return f"处理轮数过多（超过 {max_steps} 轮），已自动停止。请尝试把问题拆分成更小的步骤后重试。"
        return config.get("fallback_reply", "服务暂时不可用")

    async def _call_tool_with_progress(self, registry, tool_name: str, args: dict) -> dict:
        """执行工具并轮询其内部进度（领域注册的 progress query）→ tool_progress 事件"""
        progress_fn = registry.get_progress_query(tool_name)

        async def _run():
            return await registry.call_async(tool_name, args)

        if progress_fn is None:
            return await _run()

        start = time.perf_counter()
        task = asyncio.create_task(_run())
        last_stage = ""
        while not task.done():
            if time.perf_counter() - start > TOOL_POLL_TIMEOUT:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                await self.on_event({
                    "type": "tool_progress",
                    "tool": tool_name,
                    "stage": f"工具执行超时（>{TOOL_POLL_TIMEOUT}s），已取消",
                    "seconds": TOOL_POLL_TIMEOUT,
                })
                return {"error": f"工具执行超时（>{TOOL_POLL_TIMEOUT}s），已取消"}
            await asyncio.sleep(2)
            try:
                stage = progress_fn(args) or ""
            except Exception:
                stage = ""
            if stage and stage != last_stage:
                last_stage = stage
                await self.on_event({
                    "type": "tool_progress",
                    "tool": tool_name,
                    "stage": stage,
                    "seconds": round(time.perf_counter() - start),
                })
        return task.result()

    async def _call_llm_stream(
        self, session, base_url: str, api_key: str, payload: dict,
        step: int, t_step: float,
    ) -> dict:
        """流式 LLM 请求（OpenAI 兼容 SSE）。逐 delta 广播事件，同时累积出完整 assistant message。

        delta 字段（deepseek 系网关）:
          reasoning_content → thinking 事件
          content           → text 事件
          tool_calls        → 按 index 累积拼装
        """
        async with session.post(
            f"{base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=aiohttp.ClientTimeout(total=300),  # 流式长任务放宽
        ) as resp:
            msg: dict = {}
            async for raw_line in resp.content:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = chunk.get("choices", [{}])[0].get("delta", {}) or {}

                # 推理过程（deepseek reasoning_content）— 只广播，不混入 content
                r = delta.get("reasoning_content")
                if r:
                    await self.on_event({"type": "thinking", "delta": r})

                # 正文增量
                c = delta.get("content")
                if c:
                    msg["content"] = msg.get("content", "") + c
                    await self.on_event({"type": "text", "delta": c})

                # 工具调用按 index 累积（流式下是分段片段）
                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    calls = msg.setdefault("tool_calls", [])
                    while len(calls) <= idx:
                        calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    slot = calls[idx]
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["function"]["name"] = fn["name"]
                    if fn.get("arguments"):
                        slot["function"]["arguments"] = slot["function"]["arguments"] + fn["arguments"]

            msg["role"] = "assistant"
            step_latency = round((time.perf_counter() - t_step) * 1000)
            self.trace.setdefault("steps", []).append({
                "step": step,
                "type": "llm",
                "content": msg.get("content", ""),
                "latency_ms": step_latency,
            })
            return msg
