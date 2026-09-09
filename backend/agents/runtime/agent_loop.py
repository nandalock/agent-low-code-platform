"""AgentLoop — 驱动 Agent 核心执行循环：LLM → Tool/MCP → LLM ... 直到 final answer

与 AgentRuntime 的职责边界：
  Runtime 负责提供运行环境（config / tool schemas / llm params / Session / on_event）
  并装配派生消费者（TraceProjection / session_event_sink）；
  Loop 负责驱动 Agent 执行（derive messages、调用 LLM、消费 stream、判断 tool_calls、
  经 ToolRuntime 执行 Tool、把 tool_result 写回 Session、生成最终 answer）。
  Loop 不认识 MCP / HTTP / STDIO / server_id —— 这些都在 ToolRuntime + Executor 里；
  Loop 也不感知 Tool 生命周期事件（started / progress / completed / failed）——
  只负责每次调用构造 ToolContext（session_id / agent_id / event_sink），事件由 Runtime 层发出。

事实记录原则：Session Event Log 是 Agent 执行事实的唯一 Source of Truth ——
  Loop 只 session.append(...) 产生事实，不再维护任何第二套执行 trace
  （Trace / Telemetry 由 TraceProjection 从 Event Log 投影派生，见 trace_projection.py）。

防失控（简化版 harness，职责划分）：
  max_steps      — LLM→Tool 循环轮数上限（AgentRuntime._llm_params 配置）
  max_tool_calls — 整个 run 的 Tool 调用次数上限（AgentRuntime._llm_params 配置）
  max_wall_time  — 整个 run 的总时长上限（AgentRuntime._llm_params 配置）
  tool timeout   — 单个 Tool 的最大执行时间（Executor 生效：声明式 timeout 或 DEFAULT_TOOL_TIMEOUT）
  repeat tool    — 连续重复调用相同 Tool + 标准化参数达到 REPEAT_TOOL_THRESHOLD 后停止
  Session        — 执行事实源（AgentRuntime 创建并注入，必填）：Loop append 事件，
                   LLM 消息由 session.derive_messages() 派生（seed 由 Runtime 注入）
"""
import json
import logging
import time

import aiohttp

from backend.agents.runtime.events import EventSink
from backend.agents.runtime.session import (
    ASSISTANT_CHUNK,
    ASSISTANT_MESSAGE,
    LLM_USAGE,
    STEP_END,
    STEP_START,
    TOOL_CALL,
    TOOL_RESULT,
    TURN_END,
    TURN_START,
    USER_MESSAGE,
    Session,
)
from backend.agents.runtime.tool_event_sink import SessionToolEventSink
from backend.core.http import get_http_session
from backend.tool_system.context import ToolContext
from backend.tool_system.runtime.runtime import get_tool_runtime

logger = logging.getLogger(__name__)

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
        on_event: EventSink | None = None,
        session: Session,
    ):
        self.key = key
        self.config = config
        self.llm_params = llm_params
        self.tool_schemas = tool_schemas
        self.tenant_id = tenant_id
        self.on_event = on_event
        # Session（必填）：Event Log 是执行事实来源，LLM 消息由 session.derive_messages()
        # 派生；Loop 只 append 事件，不维护第二套 trace（TraceProjection 负责投影）。
        self.session = session

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

        # Session 是唯一执行事实来源：init_messages（system prompt / 上游 context）
        # 由 AgentRuntime 在 Session 创建时注入为 seed 事件（hot restore 的 seed 已在
        # Event Log 里），LLM 消息全程由 session.derive_messages() 派生 —— 此处不再
        # 构造本地 messages（context 参数仅在新会话 seed 注入时被 Runtime 消费）。
        self.session.append(TURN_START, {
            "agent": self.key,
            # run 级运行限制入 turn/start 事件（log-only）：TraceProjection 从中投影
            "limits": {
                "max_steps": max_steps,
                "max_tool_calls": max_tool_calls,
                "max_wall_time": max_wall_time,
            },
        })
        self.session.append(USER_MESSAGE, {"content": question})

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

                self.session.append(STEP_START, {"step": step + 1, "total": max_steps})

                # LLM 请求超时不能突破 max_wall_time：按剩余时间裁剪
                remaining = max(max_wall_time - (time.perf_counter() - run_start), 1.0)

                payload = {
                    "model": model,
                    "messages": self._get_messages(),
                    "temperature": self.llm_params["temperature"],
                    "max_tokens": self.llm_params["max_tokens"],
                }
                if tools:
                    payload["tools"] = tools
                    payload["tool_choice"] = "auto"
                if self.on_event:
                    payload["stream"] = True  # 有订阅者才流式，其余保持原行为
                    # usage 观测（Step 1）：SSE 默认不回 usage，显式请求；
                    # DeepSeek 在 [DONE] 前发一个纯 usage chunk（choices 为空）
                    payload["stream_options"] = {"include_usage": True}

                logger.info(
                    f"AgentLoop [{self.key}] step {step + 1}/{max_steps}, "
                    f"messages={len(self._get_messages())}, "
                    f"tools={[t['function']['name'] for t in tools]}"
                )

                if payload.get("stream"):
                    msg = await self._call_llm_stream(
                        session, base_url, api_key, payload, remaining,
                    )
                    llm_usage = msg.pop("usage", None)  # 流式：_call_llm_stream 从 SSE usage chunk 带回
                else:
                    async with session.post(
                        f"{base_url}/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=min(60, remaining)),
                    ) as resp:
                        result = await resp.json()
                    msg = result.get("choices", [{}])[0].get("message", {})
                    llm_usage = result.get("usage")  # 非流式：顶层 usage（provider 自动返回）

                self.session.append(ASSISTANT_MESSAGE, {
                    "content": msg.get("content") or "",
                    "tool_calls": msg.get("tool_calls") or [],
                })

                # usage 观测（Step 1）：llm/usage 事件紧跟本步 assistant/message 之后（log-only）
                if llm_usage:
                    self._record_usage(llm_usage, step)

                tool_calls = msg.get("tool_calls") or []

                if not tool_calls:
                    final_answer = msg.get("content", "")
                    self.session.append(STEP_END, {"step": step + 1})
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

                    self.session.append(TOOL_CALL, {
                        "tool": mcp_name,
                        "args": args,
                        "tool_call_id": tc["id"],
                    })

                    try:
                        # 执行统一走 ToolRuntime：timeout / transport / 生命周期事件由 Runtime 层负责
                        tool_result = await self._execute_tool(mcp_name, args, tc["id"])
                    except Exception as e:
                        tool_result = {"error": str(e)}

                    # tool 超时属于安全停止而非系统异常：已作为 error 结果给 LLM，同时记录供终止兜底
                    if isinstance(tool_result, dict) and "执行超时" in (tool_result.get("error") or ""):
                        last_tool_timeout_msg = tool_result["error"]

                    # tool result 原样 JSON 序列化入事件（Log 保全文，投影端按需还原）
                    result_data: dict = {
                        "tool": mcp_name,
                        "tool_call_id": tc["id"],
                        "content": json.dumps(tool_result, ensure_ascii=False),
                    }
                    # 沙箱事实结构化随事件落库（content 里也有一份供模型阅读）：
                    # 投影端（轨迹 / UI）直接读字段，不必解析 content。
                    sandbox = tool_result.get("sandbox") if isinstance(tool_result, dict) else None
                    if isinstance(sandbox, dict):
                        result_data["sandbox"] = sandbox
                    self.session.append(TOOL_RESULT, result_data)

                if tool_calls_exhausted or repeat_tool_stopped:
                    self.session.append(STEP_END, {"step": step + 1})
                    break

                self.session.append(STEP_END, {"step": step + 1})

            else:
                steps_exhausted = True

        except Exception as e:
            logger.warning(f"AgentLoop [{self.key}] LLM 调用失败: {e}")

        # turn/end 记录 stop_reason（正常完成/guard/异常都有值），收口进 Event Log；
        # TraceProjection 从 turn/end 事件投影 trace.stop_reason（对齐含 completed/error）。
        if tool_calls_exhausted:
            stop_reason = "max_tool_calls"
        elif repeat_tool_stopped:
            stop_reason = "repeat_tool"
        elif steps_exhausted:
            stop_reason = "max_steps"
        elif wall_time_exhausted:
            stop_reason = "max_wall_time"
        elif last_tool_timeout_msg:
            stop_reason = "tool_timeout"
        elif final_answer:
            stop_reason = "completed"
        else:
            stop_reason = "error"
        self.session.append(TURN_END, {"stop_reason": stop_reason})

        # 终止兜底：guard 安全停止都给出明确原因（不是「服务不可用」）。
        # stop_reason 已随 turn/end 事件入 Event Log，TraceProjection 从事件投影。
        if final_answer:
            return final_answer
        if tool_calls_exhausted:
            return f"工具调用次数过多（超过 {max_tool_calls} 次），已自动停止。请尝试把问题拆分成更小的步骤后重试。"
        if repeat_tool_stopped:
            return "检测到 Agent 连续重复调用相同工具，已自动停止。请尝试重新描述问题或拆分任务。"
        if steps_exhausted:
            # 轮数耗尽：明确告知而不是模糊的「服务不可用」
            return f"处理轮数过多（超过 {max_steps} 轮），已自动停止。请尝试把问题拆分成更小的步骤后重试。"
        if wall_time_exhausted:
            return f"处理时间过长（超过 {max_wall_time:.0f} 秒），已自动停止。请尝试把问题拆分成更小的步骤后重试。"
        if last_tool_timeout_msg:
            return f"{last_tool_timeout_msg}，请重试或拆分成更小的步骤。"
        return config.get("fallback_reply", "服务暂时不可用")

    def _get_messages(self) -> list[dict]:
        """LLM 消息来源：Session surface 派生（Event Log 是唯一事实源，无本地列表）"""
        return self.session.derive_messages()

    def _record_usage(self, usage: dict, step: int) -> None:
        """usage 观测（Step 1，纯旁路采集，不影响执行链）：
        provider 返回的 usage → Event Log llm/usage 事件（log-only，可持久化/回放）。

        data 取 DeepSeek 字段；其他 OpenAI 兼容网关可能缺 cache 字段（记 None 而非报错）。
        每步 LLM 调用一条，紧跟对应 assistant/message 事件之后；
        trace 侧的 usage 聚合/日志由 TraceProjection / AgentRuntime 派生。
        """
        data = {
            "step": step + 1,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "prompt_cache_hit_tokens": usage.get("prompt_cache_hit_tokens"),
            "prompt_cache_miss_tokens": usage.get("prompt_cache_miss_tokens"),
        }
        self.session.append(LLM_USAGE, data)

    async def _execute_tool(self, tool_name: str, args: dict, tool_call_id: str) -> dict:
        """执行一次 Tool：只构造 ToolContext（含事件出口）并交给 ToolRuntime。

        Tool 生命周期事件（started / progress / completed / failed）由 ToolRuntime /
        Executor 经 context.event_sink 发出，本 Loop 不感知、不拼装。
        """
        context = ToolContext(
            session_id=self.session.header.id,
            agent_id=self.key,
            event_sink=SessionToolEventSink(self.session, tool_call_id, forward=self.on_event),
            sandbox_policy=self._sandbox_policy(),
        )
        return await get_tool_runtime().execute(tool_name, args, context)

    def _sandbox_policy(self):
        """本会话一次调用的沙箱执行策略；沙箱未装配时 None（MCP 工具不受影响）。

        工作区根来自 SessionHeader.cwd，首次调用时落地——它确定性派生自
        session_id + 部署配置，因此不需要额外的事件记录（Event Log 保持
        「只记事实」）。会话覆盖来自 ``sandbox/mode`` 事件的投影（find-last）。
        沙箱工具在策略缺失时 fail-closed。
        """
        try:
            from backend.agents.runtime.session.sandbox_projection import project_sandbox_mode
            from backend.tool_system.sandbox.runtime import session_policy
            header = self.session.header
            policy = session_policy(
                header.id,
                header.cwd,
                session_override=project_sandbox_mode(self.session.events),
            )
            if policy is not None and header.cwd is None:
                header.cwd = policy.workspace_root
            return policy
        except Exception as e:
            logger.warning(f"沙箱策略解析失败（沙箱工具将拒绝执行）: {e}")
            return None

    async def _call_llm_stream(
        self, session, base_url: str, api_key: str, payload: dict, remaining: float,
    ) -> dict:
        """流式 LLM 请求（OpenAI 兼容 SSE）。逐 delta 广播事件，同时累积出完整 assistant message。

        delta 字段（deepseek 系网关）:
          reasoning_content → thinking 事件（assistant/chunk，只广播不入 content）
          content           → text 事件（assistant/chunk）
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
                # usage 观测：include_usage 后 provider 在 [DONE] 前发纯 usage chunk（choices 空），
                # 必须在本层循环捕获（break 在 [DONE] 处，放 break 之后会漏掉）
                if chunk.get("usage"):
                    msg["usage"] = chunk["usage"]
                delta = chunk.get("choices", [{}])[0].get("delta", {}) or {}

                # 推理过程（deepseek reasoning_content）— 只广播，不混入 content。
                # 增量以 assistant/chunk 入 Session Log（投影/回放可见）
                r = delta.get("reasoning_content")
                if r:
                    self.session.append(ASSISTANT_CHUNK, {"kind": "thinking", "delta": r})

                # 正文增量
                c = delta.get("content")
                if c:
                    msg["content"] = msg.get("content", "") + c
                    self.session.append(ASSISTANT_CHUNK, {"kind": "text", "delta": c})

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
            return msg  # usage（若 provider 返回）随 msg 带回，run() 统一记录 llm/usage 事件
