"""AgentLoop — 驱动 Agent 核心执行循环：LLM → Tool/MCP → LLM ... 直到 final answer

与 AgentRuntime 的职责边界：
  Runtime 负责提供运行环境（config / tool schemas / llm params / trace / on_event），
  Loop 负责驱动 Agent 执行（构造 messages、调用 LLM、消费 stream、判断 tool_calls、
  执行 ToolRegistry / MCP、把 tool_result 放回 messages、记录 trace、生成最终 answer）。

防失控（简化版 harness，职责划分）：
  max_steps      — LLM→Tool 循环轮数上限（AgentRuntime._llm_params 配置）
  max_tool_calls — 整个 run 的 Tool 调用次数上限（AgentRuntime._llm_params 配置）
  max_wall_time  — 整个 run 的总时长上限（AgentRuntime._llm_params 配置）
  tool timeout   — 单个 Tool 的最大执行时间（ToolRegistry 提供，未声明用 DEFAULT_TOOL_TIMEOUT）
  repeat tool    — 连续重复调用相同 Tool + 标准化参数达到 REPEAT_TOOL_THRESHOLD 后停止
"""
import asyncio
import json
import logging
import time

import aiohttp

from backend.agents.runtime.events import EventSink
from backend.core.http import get_http_session
from backend.mcp_service.registry import get_registry

logger = logging.getLogger(__name__)

# 未注册 timeout 的 Tool 使用该默认值（秒）
DEFAULT_TOOL_TIMEOUT = 30
# 连续重复调用相同 Tool + 相同参数达到该次数后触发保护（前两次重复允许，第三次停止）
REPEAT_TOOL_THRESHOLD = 3


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
        """驱动完整 Agent 循环，返回最终 answer（含 guard 安全停止 / fallback 兜底）"""
        config = self.config
        api_key = (config.get("api_key", "") or "").strip()
        base_url = (config.get("base_url", "") or "").strip()
        model = (config.get("model", "") or "").strip()

        if not api_key or not base_url or not model:
            return config.get("fallback_reply", "服务未配置")

        max_steps = self.llm_params["max_steps"]
        max_tool_calls = self.llm_params["max_tool_calls"]
        max_wall_time = self.llm_params["max_wall_time"]

        # trace 增强：记录本次 run 的运行限制（不改动现有 steps 结构）
        self.trace["limits"] = {
            "max_steps": max_steps,
            "max_tool_calls": max_tool_calls,
            "max_wall_time": max_wall_time,
        }

        # run 级防失控状态
        run_start = time.perf_counter()        # max_wall_time 起点（整个 run，非单步）
        tool_call_count = 0                    # max_tool_calls 计数
        last_tool_signature = None             # repeat tool：上次调用的 tool+参数签名
        repeat_tool_count = 0                  # 连续重复次数
        tool_calls_exhausted = False           # guard 触发标记
        repeat_tool_stopped = False
        wall_time_exhausted = False
        last_tool_timeout_msg = ""             # 最后一次 tool 超时信息（供终止兜底）

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
                # max_wall_time：整个 run 的总时长，每个 step 开始前检查
                # （区别于 max_steps 轮数上限与 per-tool timeout）
                if time.perf_counter() - run_start >= max_wall_time:
                    wall_time_exhausted = True
                    break

                t_step = time.perf_counter()
                # LLM 请求超时不能突破 max_wall_time：按剩余时间裁剪
                remaining = max(max_wall_time - (time.perf_counter() - run_start), 1.0)

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
                        session, base_url, api_key, payload, step, t_step, remaining,
                    )
                else:
                    async with session.post(
                        f"{base_url}/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=min(60, remaining)),
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
                    args["tenant_id"] = self.tenant_id  # 系统覆盖，不信任 LLM 传入值

                    # max_tool_calls：run 级计数达到上限直接终止，不再执行新 Tool
                    if tool_call_count >= max_tool_calls:
                        tool_calls_exhausted = True
                        break

                    # repeat tool 检测：tool + 标准化 args 完全相同才计数；前两次重复允许，第三次停止
                    signature = (mcp_name, json.dumps(args, ensure_ascii=False, sort_keys=True))
                    if signature == last_tool_signature:
                        repeat_tool_count += 1
                    else:
                        last_tool_signature = signature
                        repeat_tool_count = 0
                    if repeat_tool_count >= REPEAT_TOOL_THRESHOLD:
                        repeat_tool_stopped = True
                        break

                    tool_call_count += 1

                    if self.on_event:
                        await self.on_event({"type": "tool_call", "tool": mcp_name, "args": args})

                    t_tool = time.perf_counter()
                    try:
                        registry = get_registry()
                        # per-tool timeout：优先 registry 声明，未声明用默认值；执行统一走带超时的调用
                        tool_timeout = registry.get_tool_timeout(mcp_name) or DEFAULT_TOOL_TIMEOUT
                        tool_result = await self._call_tool_with_progress(registry, mcp_name, args, tool_timeout)
                    except Exception as e:
                        tool_result = {"error": str(e)}
                    tool_latency = round((time.perf_counter() - t_tool) * 1000)

                    # tool 超时属于安全停止而非系统异常：已作为 error 结果给 LLM，同时记录供终止兜底
                    if isinstance(tool_result, dict) and "执行超时" in (tool_result.get("error") or ""):
                        last_tool_timeout_msg = tool_result["error"]

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

                if tool_calls_exhausted or repeat_tool_stopped:
                    break

            else:
                steps_exhausted = True

        except Exception as e:
            logger.warning(f"AgentLoop [{self.key}] LLM 调用失败: {e}")

        # 终止兜底：guard 安全停止都给出明确原因（不是「服务不可用」）
        if final_answer:
            return final_answer
        if tool_calls_exhausted:
            self.trace["stop_reason"] = "max_tool_calls"
            return f"工具调用次数过多（超过 {max_tool_calls} 次），已自动停止。请尝试把问题拆分成更小的步骤后重试。"
        if repeat_tool_stopped:
            self.trace["stop_reason"] = "repeat_tool"
            return "检测到 Agent 连续重复调用相同工具，已自动停止。请尝试重新描述问题或拆分任务。"
        if steps_exhausted:
            self.trace["stop_reason"] = "max_steps"
            # 轮数耗尽：明确告知而不是模糊的「服务不可用」
            return f"处理轮数过多（超过 {max_steps} 轮），已自动停止。请尝试把问题拆分成更小的步骤后重试。"
        if wall_time_exhausted:
            self.trace["stop_reason"] = "max_wall_time"
            return f"处理时间过长（超过 {max_wall_time:.0f} 秒），已自动停止。请尝试把问题拆分成更小的步骤后重试。"
        if last_tool_timeout_msg:
            self.trace["stop_reason"] = "tool_timeout"
            return f"{last_tool_timeout_msg}，请重试或拆分成更小的步骤。"
        return config.get("fallback_reply", "服务暂时不可用")

    async def _call_tool_with_progress(self, registry, tool_name: str, args: dict, timeout: float) -> dict:
        """执行工具：所有 Tool 统一带 timeout；有进度查询 + 事件订阅者时额外轮询 → tool_progress 事件

        兼容性：progress_fn 为 None（未注册进度查询 / 无 on_event）时不再「没有超时」，
        退化为 asyncio.wait_for 兜底，超时返回 error 结果而非让 AgentLoop 异常退出。
        """
        progress_fn = registry.get_progress_query(tool_name)

        async def _run():
            return await registry.call_async(tool_name, args)

        if progress_fn is None or self.on_event is None:
            try:
                return await asyncio.wait_for(_run(), timeout=timeout)
            except asyncio.TimeoutError:
                return {"error": f"工具 {tool_name} 执行超时（>{timeout:.0f}s），已取消"}

        start = time.perf_counter()
        task = asyncio.create_task(_run())
        last_stage = ""
        while not task.done():
            if time.perf_counter() - start > timeout:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                await self.on_event({
                    "type": "tool_progress",
                    "tool": tool_name,
                    "stage": f"工具执行超时（>{timeout:.0f}s），已取消",
                    "seconds": round(timeout),
                })
                return {"error": f"工具 {tool_name} 执行超时（>{timeout:.0f}s），已取消"}
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
        step: int, t_step: float, remaining: float,
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
            timeout=aiohttp.ClientTimeout(total=min(300, remaining)),  # 流式长任务放宽，但不能突破 max_wall_time
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
