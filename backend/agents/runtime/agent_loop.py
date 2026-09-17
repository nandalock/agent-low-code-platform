"""AgentLoop — 驱动 Agent 核心执行循环：LLM → Tool/MCP → LLM ... 直到 final answer

与 AgentRuntime 的职责边界：
  Runtime 负责提供运行环境（config / tool schemas / llm params / Session / on_event）
  并装配派生消费者（TraceProjection / session_event_sink）；
  Loop 负责驱动 Agent 执行（derive messages、调用 LLM、消费 stream、判断 tool_calls、
  产出调用计划交给 ToolScheduler、生成最终 answer）。
  Loop 不认识 MCP / HTTP / STDIO / server_id —— 这些都在 ToolRuntime + Executor 里；
  Loop 也不感知 Tool 生命周期事件（started / progress / completed / failed）——
  只负责每次调用构造 ToolContext（session_id / agent_id / event_sink），事件由 Runtime 层发出。

Tool 调度边界（见 tool_system/runtime/scheduler.py）：
  Loop 只做「计划」——名字还原、参数解析、tenant 注入、guard 裁决（max_tool_calls /
  repeat tool），产出一批 PlannedCall；执行顺序、并发/串行、tool/call · tool/result
  的成对落库与**有序提交**都归 ToolScheduler。

事实记录原则：Session Event Log 是 Agent 执行事实的唯一 Source of Truth ——
  Loop 只 session.append(...) 产生事实，不再维护任何第二套执行 trace
  （Trace / Telemetry 由 TraceProjection 从 Event Log 投影派生，见 projections/trace.py）。

防失控（简化版 harness，职责划分）：
  max_steps      — LLM→Tool 循环轮数上限（AgentRuntime._llm_params 配置）
  max_tool_calls — 整个 run 的 Tool 调用次数上限（AgentRuntime._llm_params 配置）
  max_wall_time  — 整个 run 的总时长上限（AgentRuntime._llm_params 配置）
  tool timeout   — 单个 Tool 的最大执行时间（Executor 生效：声明式 timeout 或 DEFAULT_TOOL_TIMEOUT）
  repeat tool    — 连续重复调用相同 Tool + 标准化参数达到 REPEAT_TOOL_THRESHOLD 后停止
  Session        — 执行事实源（AgentRuntime 创建并注入，必填）：Loop append 事件，
                   LLM 消息由 session.derive_messages() 派生

system prompt 的位置：不在 Session 里，由 AgentRuntime 每轮组装后经 `system_prompt`
参数注入，`_get_messages()` 把它前置为 messages[0]（见 agents/runtime/system_prompt/）。
于是「提示词」与「对话历史」是两条独立来源，在组装 LLM 请求时才汇合。

LLM 请求失败（对齐 A 的 step 重试循环与结构化失败）：
  有限重试   — 每次 step 的 LLM 调用在 **step 内**重试（llm_params.max_llm_retries，
               缺省 2）：429 / 5xx / 超时 / 传输失败 / 非法响应（tool_calls 参数不可解析）
               / 空响应 可重试；其余 4xx 与未知结束原因不重试，直接终止本步；指数退避
               （初始 500ms、上限 10s、抖动 10%），429 认 Retry-After，且退避等待不得
               突破 max_wall_time。
  不落脏事实 — 失败的尝试**不 append 任何 surface 事实**：assistant/message 没落库，
               derive_messages 派生出的历史天然干净（这正是不污染多轮会话的机制）。
               只落一条 log-only 的 llm/error 事件（A 的 assistant/attempt 对应物）：
               重试是事实，不该只出现在应用日志里。
  失败结构化 — 失败分类记在 LlmError.code / status 上（HTTP 状态码驱动，不猜 provider 文本），
               重试耗尽后由 step 体收口为「本步失败 → 本轮终止」，stop_reason=error。
               已知边界（留给 Phase 4 的流式层重构）：流式尝试在失败前已经把
               assistant/chunk 广播出去且不可撤回，重试后同一 step 会留下两段 chunk。
               Event Log 是诚实的（那些增量确实发生过），assistant/message 的段对账
               （TrajectoryProjection._seg_buf）也能把 answer 文本收敛回权威值，但
               thinking 节点在主链路视图里会是两段拼接 —— A 用 attempt/revision 机制
               解决，那属于流式层，不在本轮。
  finish_reason — 每次响应都读（流式与非流式两条路径），并分两类对待：
               **"length"（截断）是正常结束的一种，不是失败** —— 对齐 A 的类型划分
               （max-tokens 不携带 failure，进不了请求失败的处理链），所以它**不重试**：
               立即终止本轮（stop_reason=max_tokens），已生成的正文照常作为答案返回，
               被截断的 tool_calls 一律不落库、不执行（参数不可复现，且会留下无配对
               tool/result 的 dangling 消息，让下一轮请求非法）；正文为空时按 A 的
               surface 规则不落任何 assistant 消息。
               **其余未知结束原因（content_filter 等）是失败** —— 对齐 A mapFinishReason
               的 default 分支：不许冒充正常收尾，按 LlmError 走失败处理（不重试）。
               网关没给 finish_reason 时不判（保持对既有网关的兼容）。

闭合语义（fail-closed）：STEP_END / TURN_END 由 finally 保障 —— 任何退出路径
  （正常 / guard 安全停止 / LLM 失败 / 意外异常 / 取消）都必须闭合，不留半途 turn。
  turn/end 的 stop_reason 词表：completed / max_steps / max_tool_calls / max_wall_time /
  repeat_tool / tool_timeout / max_tokens / cancelled / error（TraceProjection 原样投影）。

取消（对齐 A 的 AbortSignal：任意 await 点可被打断），两条来源分别处理：
  协作取消 — run(cancel=asyncio.Event)：取消信号由装配层持有并 set（B 的 HTTP 装配层里
             最自然的来源是 SSE 生成器被关闭，即客户端断开 / 前端 aborted fetch）。
             循环在**检查点**响应它（每个 await 点：每步开头、LLM 请求、工具批次），
             本轮以 stop_reason=cancelled 收口后**正常返回** —— 取消也是个「有始有终的
             turn」，后续的持久化 / done 事件照常走，半截正文按 A 的 interrupted 语义
             保留（tool_calls 一律丢弃，避免 dangling 消息）。
  硬取消   — 外部 task.cancel()（进程收尾 / 驱动方显式取消；**不是** HTTP 断开 ——
             实测 uvicorn 在客户端断开时并不 cancel 非流式 handler，那边会照常在后台跑完，
             与接线前行为一致）：CancelledError 是 BaseException，**不会被这里的
             except Exception 吞掉**。循环记完事实（同样闭合 step/end、turn/end）后原样
             向上传播 —— 不吞取消，调用方的任务状态必须正确。代价是这条路上拿不到半截
             正文（它随被取消的协程一起没了），但 assistant/chunk 事实仍在 Event Log 里。
             这条路是防御性的：它保证「任何取消都不会留下半途 turn」，而不是 B 当前的
             主要取消来源。
  传播方式 — 取消不用轮询兜底：`_run_cancellable` 用 **asyncio 取消机制**把信号送进
             在途的协程（aiohttp 请求任务 / ToolScheduler），因此 ToolScheduler 既有的
             取消恢复路径（排空在途 + 未启动的补合成结果，见 scheduler.py）被原样复用，
             一条工具调用都不会丢配对。
"""
import asyncio
import json
import logging
import random
import time

import aiohttp

from backend.agents.runtime.events import EventSink
from backend.agents.runtime.session import (
    ASSISTANT_CHUNK,
    ASSISTANT_MESSAGE,
    LLM_ERROR,
    LLM_USAGE,
    STEP_END,
    STEP_START,
    TURN_END,
    TURN_START,
    USER_MESSAGE,
    Session,
)
from backend.agents.runtime.session_tool_recorder import SessionToolRecorder
from backend.agents.runtime.tool_event_sink import SessionToolEventSink
from backend.core.http import get_http_session
from backend.tool_system.context import ToolContext
from backend.tool_system.runtime.scheduler import (
    DEFAULT_MAX_PARALLEL_TOOLS,
    DISPATCH,
    SKIP,
    SKIP_MAX_TOOL_CALLS,
    SKIP_REPEAT_TOOL,
    PlannedCall,
    ToolScheduler,
)

logger = logging.getLogger(__name__)

# 连续重复调用相同 Tool + 相同参数达到该次数后触发保护（前两次重复允许，第三次停止）
REPEAT_TOOL_THRESHOLD = 3

# ── LLM 请求失败：结构化分类 + 重试策略（对齐 A 的 LlmError / retry-policy.ts）──

#: 失败码由 HTTP 状态与响应形态判定，不猜 provider 的文本
CODE_RATE_LIMIT = "RATE_LIMIT"              # 429：限流，退避后重试
CODE_SERVER = "SERVER"                      # 5xx：对端故障，有限重试
CODE_CLIENT = "CLIENT"                      # 其余 4xx：鉴权/参数/额度，重试无用
CODE_TIMEOUT = "TIMEOUT"                    # 请求或传输超时
CODE_TRANSPORT = "TRANSPORT"                # 连接层失败（DNS / 断连）
CODE_INVALID_RESPONSE = "INVALID_RESPONSE"  # 非 JSON / 结构缺失 / tool_calls 不可解析
CODE_EMPTY_RESPONSE = "EMPTY_RESPONSE"      # 2xx 但既无正文也无 tool_calls（网关抖动）
CODE_UNKNOWN_FINISH = "UNKNOWN_FINISH"      # finish_reason 不在词表内（content_filter 等）

#: 可重试的失败码（对齐 A retryableCodes：RATE_LIMIT / SERVER / TIMEOUT / TRANSPORT；
#: B 另把「非法响应 / 空响应」也纳入 —— 它们是同一次请求的瞬时故障，重发常能自愈）
RETRYABLE_CODES = frozenset({
    CODE_RATE_LIMIT,
    CODE_SERVER,
    CODE_TIMEOUT,
    CODE_TRANSPORT,
    CODE_INVALID_RESPONSE,
    CODE_EMPTY_RESPONSE,
})

#: 认识的结束原因（对齐 A mapFinishReason：stop / tool_calls / length，其余一律当失败）。
#: None = 网关没给 finish_reason（部分网关如此），**不判**，保持对既有网关的兼容。
KNOWN_FINISH_REASONS = frozenset({"stop", "tool_calls", "function_call", "length"})

#: 退避参数（对齐 A 的缺省 backoff：初始 500ms、上限 10s、抖动 10%）。
#: 模块级常量而非硬编码：自检脚本把它们压到 0 以避免真实等待。
LLM_RETRY_INITIAL_DELAY = 0.5
LLM_RETRY_MAX_DELAY = 10.0
LLM_RETRY_JITTER = 0.1

#: 每次 step 的 LLM 调用重试次数上限（llm_params.max_llm_retries 覆盖，见 AgentRuntime._llm_params）
DEFAULT_MAX_LLM_RETRIES = 2

#: 取消时的兜底回复（没有可保留的半截正文时用它 —— 比「服务不可用」诚实：这轮是被停止的）。
#: 与前端 WorkspaceChat 的本地兜底文案一致：同一件事在两条路径上说法相同。
CANCELLED_REPLY = "已停止生成。"


class RunCancelled(Exception):
    """协作取消：run(cancel=...) 的信号被触发，本轮以 stop_reason=cancelled 收口。

    与 ``asyncio.CancelledError``（外部硬取消）分开，是因为两者对调用方的语义不同：
    协作取消是「有始有终的一轮」—— run() 记完事实**正常返回**，持久化与 done 事件照常；
    硬取消则记完事实后必须**继续向上传播**（调用方的任务状态要正确）。

    ``partial`` 是信号到达那一刻已经累积出的 assistant message（可能为空）：照 A 的
    interrupted 语义，中断前已经产出的正文保留为事实（tool_calls 一律丢弃 —— 参数
    可能不完整，落库会留下无配对 tool/result 的 dangling 消息）。
    """

    def __init__(self, partial: dict | None = None):
        super().__init__("本轮已被取消")
        self.partial = partial or {}


def _safe_name(tool_name: str) -> str:
    return tool_name.replace("/", "_").replace(".", "_")


class LlmError(RuntimeError):
    """一次 LLM 请求的结构化失败（对齐 A 的 LlmError / LlmFailure）。

    ``code`` 决定能否重试（见 RETRYABLE_CODES），``status`` 是 HTTP 状态码
    （传输层失败为 None），``retry_after`` 是网关 Retry-After 头换算出的秒数。
    失败被结构化后「重试什么、为什么停」都由字段决定，不靠解析错误文本。
    """

    def __init__(self, message: str, *, code: str, status: int | None = None, retry_after: float = 0.0):
        super().__init__(message)
        self.code = code
        self.status = status
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        """是否是「重发可能自愈」的失败（429 / 5xx / 超时 / 传输 / 非法响应）"""
        return self.code in RETRYABLE_CODES

    def fact(self, *, step: int, attempt: int, retry_in: float | None) -> dict:
        """结构化事实（落 llm/error 事件）：投影端直接读字段，不解析文本。"""
        return {
            "step": step,
            "attempt": attempt,
            "code": self.code,
            "status": self.status,
            "message": str(self),
            "retry_in": None if retry_in is None else round(retry_in, 2),
        }


def _classify_status(status: int) -> str:
    """HTTP 状态码 → 失败码（重试决策的唯一依据）"""
    if status == 429:
        return CODE_RATE_LIMIT
    if status >= 500:
        return CODE_SERVER
    if status == 408:
        return CODE_TIMEOUT
    return CODE_CLIENT


def _retry_after_seconds(headers) -> float:
    """Retry-After 头 → 秒数（429 常用的对端退避建议；缺失/非法一律 0）"""
    try:
        value = float(str((headers or {}).get("Retry-After", "")).strip())
    except (TypeError, ValueError):
        return 0.0
    return value if value > 0 else 0.0


def _retry_delay(attempt: int, retry_after: float = 0.0) -> float:
    """第 attempt 次重试前的退避时长：指数增长 + 对称抖动，取对端建议与本地退避的较大者。

    抖动是必要的：多个会话同时被同一个 429 挡住时，同时重试会再撞一次限流。
    """
    base = min(LLM_RETRY_INITIAL_DELAY * (2 ** attempt), LLM_RETRY_MAX_DELAY)
    delay = max(base, min(retry_after, LLM_RETRY_MAX_DELAY))
    return delay * (1.0 + (random.random() * 2.0 - 1.0) * LLM_RETRY_JITTER)


def _invalid_tool_calls(tool_calls: list[dict]) -> list[str]:
    """挑出结构不完整 / arguments 不可解析的 tool_call，返回其标签（有 id 用 id，否则用下标）。

    校验范围刻意覆盖「计划阶段会用到的每一个字段」（id / function.name /
    function.arguments → dict）：这些调用是**不可复现的事实**，既不能落 tool/call
    （args 已经残缺），也不能落进 assistant/message（会留下无配对 tool/result 的
    dangling tool_calls，让下一轮请求非法）。宁可在落库前按结构化失败重试。
    """
    bad: list[str] = []
    for index, tc in enumerate(tool_calls):
        fn = tc.get("function") if isinstance(tc, dict) else None
        if not tc.get("id") or not isinstance(fn, dict) or not fn.get("name"):
            bad.append(tc.get("id") or f"#{index}")
            continue
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError):
            bad.append(tc.get("id") or f"#{index}")
            continue
        if not isinstance(args, dict):
            bad.append(tc.get("id") or f"#{index}")
    return bad


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
        system_prompt: str = "",
        on_event: EventSink | None = None,
        session: Session,
    ):
        self.key = key
        self.config = config
        self.llm_params = llm_params
        self.tool_schemas = tool_schemas
        self.tenant_id = tenant_id
        # system prompt：由 Runtime 每轮组装（SystemPrompt.assemble → render），
        # 本 Loop 只负责在派生消息时前置。空串表示本轮无提示词（不发送空 system）。
        self.system_prompt = system_prompt
        self.on_event = on_event
        # Session（必填）：Event Log 是执行事实来源，LLM 消息由 session.derive_messages()
        # 派生；Loop 只 append 事件，不维护第二套 trace（TraceProjection 负责投影）。
        self.session = session
        # Tool 调度器：执行的顺序 / 并发 / 成对落库 / 有序提交归它，Loop 只产出调用计划
        self._scheduler = ToolScheduler(
            recorder=SessionToolRecorder(session),
            context_factory=self._tool_context,
            max_parallel_tools=llm_params.get("max_parallel_tools", DEFAULT_MAX_PARALLEL_TOOLS),
        )

    async def run(
        self, question: str, context: dict | None = None, cancel: asyncio.Event | None = None,
    ) -> str:
        """驱动完整 Agent 循环，返回最终 answer（含 guard 安全停止 / 取消 / fallback 兜底）。

        ``cancel`` 是协作取消信号（可选）：装配层持有并在需要停止时 ``set()``。
        信号在每个 await 点被检查（步首 / LLM 请求 / 工具批次），一旦触发：
        本轮以 stop_reason=cancelled 收口、已产出的正文保留，函数**正常返回**
        （不抛异常 —— 取消过的 turn 也要走完持久化与 done 事件）。
        没有信号传入时是零开销的直接 await，行为与不传完全一致。
        """
        config = self.config
        api_key = (config.get("api_key", "") or "").strip()
        base_url = (config.get("base_url", "") or "").strip()
        model = (config.get("model", "") or "").strip()

        if not api_key or not base_url or not model:
            return config.get("fallback_reply", "服务未配置")

        max_steps = self.llm_params["max_steps"]
        max_tool_calls = self.llm_params["max_tool_calls"]
        max_wall_time = self.llm_params["max_wall_time"]
        max_tokens = self.llm_params["max_tokens"]

        # run 级防失控状态
        run_start = time.perf_counter()        # max_wall_time 起点（整个 run，非单步）
        deadline = run_start + max_wall_time   # 绝对截止时刻：LLM 超时与重试退避都不得突破
        tool_call_count = 0                    # max_tool_calls 计数
        last_tool_signature = None             # repeat tool：上次调用的 tool+参数签名
        repeat_tool_count = 0                  # 连续重复次数
        tool_calls_exhausted = False           # guard 触发标记
        repeat_tool_stopped = False
        wall_time_exhausted = False
        max_tokens_stopped = False             # finish_reason=length：本轮被 max_tokens 截断
        cancelled = False                      # 本轮被取消（协作信号或外部硬取消）
        last_tool_timeout_msg = ""             # 最后一次 tool 超时信息（供终止兜底）

        name_map = {}
        tools = []
        for ts in self.tool_schemas:
            tool_name = ts["function"]["name"]
            safe = _safe_name(tool_name)
            name_map[safe] = tool_name
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
                # 取消检查点（step 间隙）：在开新 step 之前响应信号 —— 已闭合的步骤
                # 不受影响，也不为一个注定被取消的 step 落 step/start
                if cancel is not None and cancel.is_set():
                    raise RunCancelled()
                # max_wall_time：整个 run 的总时长，每个 step 开始前检查
                # （区别于 max_steps 轮数上限与 per-tool timeout）
                if time.perf_counter() >= deadline:
                    wall_time_exhausted = True
                    break

                self.session.append(STEP_START, {"step": step + 1, "total": max_steps})

                # 本步的每一条退出路径（正常收敛 / guard 停止 / LLM 失败 / 意外异常）
                # 都由 finally 闭合 step/end —— step/start 与 step/end 严格成对。
                try:
                    payload = {
                        "model": model,
                        "messages": self._get_messages(),
                        "temperature": self.llm_params["temperature"],
                        "max_tokens": max_tokens,
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

                    # 一次 step 级 LLM 请求：内部有限重试，重试耗尽抛 LlmError（含失败码）。
                    # 失败尝试不落任何 surface 事实 —— 只有成功的那次才走到下面的 append。
                    try:
                        # 请求整体（含内部重试）跑在可取消的包装里：信号一到就取消在途
                        # HTTP 请求，不必等 aiohttp 的 timeout
                        msg, finish_reason, llm_usage = await self._run_cancellable(
                            self._request_llm(session, base_url, api_key, payload, deadline, step, cancel),
                            cancel,
                        )
                    except RunCancelled as e:
                        # 取消落在本步：先把中断前已产出的正文落盘（仍在 step 内 —— 与 A 的
                        # settle 位置一致），再让它穿透到外层收口。被停止的这轮照样留下事实。
                        final_answer = self._settle_interrupted(e.partial)
                        raise
                    except LlmError as e:
                        # 重试耗尽的失败：失败的尝试不落任何 surface 事实（历史保持干净），
                        # 本轮以 turn/end 收口。**截断不走这条路** —— 它是「正常结束的一种」
                        # （对齐 A：max-tokens 类型上不携带 failure），由下面的
                        # finish_reason == "length" 分支处理，所以这里不会看到截断。
                        logger.warning(f"AgentLoop [{self.key}] step {step + 1} LLM 失败: {e}")
                        break

                    # finish_reason=length：输出被 max_tokens 截断 —— 这是**正常结束的一种**，
                    # 不是失败（对齐 A：max-tokens 在类型上不携带 failure，进不了请求失败的
                    # 处理链，也绝不重试）。逐层对应 A 的六层：
                    #   装配器（assembler.ts）  截断时把 tool-call 块整体筛掉 —— 本分支丢弃
                    #                          本步全部 tool_calls：参数可能被截断（落库就是
                    #                          落不可复现的事实），且落了不执行会留下无配对
                    #                          tool/result 的 dangling 消息，下一轮直接非法；
                    #   循环（agent.ts:484）    落盘 assistant/message 后早返回 —— 这里先落正文；
                    #   派生（surface.ts）      空消息不进历史 —— 正文为空时不落任何消息；
                    #   终态（agent.ts:310）    粘住不降级 —— 本分支直接 break 终止本轮；
                    #   UI（locale.ts）         渲染「已达到输出 token 上限」—— 前端
                    #                          STOP_REASON_LABELS["max_tokens"] 对应。
                    # 想要「截断后自动续写」，扩展点是 Phase 3 的 turn-stopping hook
                    # （A 在 agent.ts:315 留了插话口），不是在这里重试。
                    if finish_reason == "length":
                        max_tokens_stopped = True
                        final_answer = msg.get("content") or ""
                        if msg.get("tool_calls"):
                            logger.warning(
                                f"AgentLoop [{self.key}] step {step + 1} 被 max_tokens 截断，"
                                f"丢弃 {len(msg['tool_calls'])} 个不完整 tool_calls"
                            )
                        if final_answer:
                            self.session.append(ASSISTANT_MESSAGE, {"content": final_answer, "tool_calls": []})
                            if llm_usage:
                                self._record_usage(llm_usage, step)
                        break

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
                        break

                    # 计划阶段：只产出「要不要执行、执行什么」，不执行也不落库。
                    # guard 按 model order 裁决：一旦命中，本 step 剩余调用全部记为 SKIP ——
                    # 它们仍会成对落 tool/call + 合成 tool/result，保证 assistant.tool_calls
                    # 的每一项都有对应结果（否则下一轮 derive_messages 产出的消息序列非法）。
                    # 参数解析的安全性由 _validate_response 前置保证（不可解析的 tool_calls
                    # 在落 assistant/message 之前就按失败重试了，走不到这里）。
                    plans: list[PlannedCall] = []
                    for tc in tool_calls:
                        safe = tc["function"]["name"]
                        tool_name = name_map.get(safe, safe)
                        args = json.loads(tc["function"]["arguments"])
                        args["tenant_id"] = self.tenant_id  # 系统覆盖，不信任 LLM 传入值

                        action, skip_reason = DISPATCH, ""
                        if tool_calls_exhausted:
                            # max_tool_calls：run 级计数达到上限，后续调用一律不执行
                            action, skip_reason = SKIP, SKIP_MAX_TOOL_CALLS
                        elif repeat_tool_stopped:
                            action, skip_reason = SKIP, SKIP_REPEAT_TOOL
                        elif tool_call_count >= max_tool_calls:
                            tool_calls_exhausted = True
                            action, skip_reason = SKIP, SKIP_MAX_TOOL_CALLS
                        else:
                            # repeat tool 检测：tool + 标准化 args 完全相同才计数；
                            # 前两次重复允许，第三次停止
                            signature = (tool_name, json.dumps(args, ensure_ascii=False, sort_keys=True))
                            if signature == last_tool_signature:
                                repeat_tool_count += 1
                            else:
                                last_tool_signature = signature
                                repeat_tool_count = 0
                            if repeat_tool_count >= REPEAT_TOOL_THRESHOLD:
                                repeat_tool_stopped = True
                                action, skip_reason = SKIP, SKIP_REPEAT_TOOL
                            else:
                                tool_call_count += 1

                        plans.append(PlannedCall(
                            tool_call_id=tc["id"],
                            tool_name=tool_name,
                            args=args,
                            action=action,
                            skip_reason=skip_reason,
                        ))

                    # 执行 + 有序提交：顺序 / 生命周期事件 / tool_call 与 tool_result 的成对落库
                    # 都归 ToolScheduler（见 tool_system/runtime/scheduler.py）。
                    # 取消信号经 asyncio 取消机制送进调度器 → 走它既有的取消恢复路径
                    # （排空在途、未启动的补合成结果），每个 tool/call 都保住配对。
                    outcomes = await self._run_cancellable(
                        self._scheduler.execute_tool_calls(plans), cancel,
                    )
                    for outcome in outcomes:
                        # tool 超时属于安全停止而非系统异常：已作为 error 结果给 LLM，同时记录供终止兜底
                        if "执行超时" in outcome.error:
                            last_tool_timeout_msg = outcome.error

                    if tool_calls_exhausted or repeat_tool_stopped:
                        break
                finally:
                    self.session.append(STEP_END, {"step": step + 1})

            else:
                steps_exhausted = True

        except RunCancelled:
            # 协作取消（cancel 信号）：正常收口后**返回**，不抛 —— 取消的 turn 也要走完
            # 持久化与 done 事件。半截正文已在 step 内落盘（见上面的 RunCancelled 分支）；
            # 步与步之间被取消的没有半截内容，那就是一次干净的停止。
            cancelled = True
            logger.info(f"AgentLoop [{self.key}] 本轮被取消（cancel 信号）")
        except asyncio.CancelledError:
            # 硬取消（外部 task.cancel()）：同样闭合事件，但**不吞取消** —— 记下事实后
            # 原样向上传播（调用方的任务状态必须正确）。这条路拿不到半截正文：它随被
            # 取消的协程一起消失了（assistant/chunk 事实仍在 Event Log 里）。
            cancelled = True
            logger.info(f"AgentLoop [{self.key}] 本轮被硬取消（task.cancel）")
            raise
        except Exception as e:
            # 兜底：step 体内的意外异常（LLM 失败已在 step 内收口）—— 记 traceback 再收口，
            # 本轮仍然以 turn/end 闭合，绝不留半途 turn。
            logger.exception(f"AgentLoop [{self.key}] 循环异常: {e}")
        finally:
            # turn/end 记录 stop_reason（正常完成/guard/异常都有值），收口进 Event Log；
            # TraceProjection 从 turn/end 事件投影 trace.stop_reason（对齐含 completed/error）。
            # 放在 finally：turn/start 一旦落库，turn/end 就必须落库 —— 闭合由结构保证，
            # 不依赖「哪条 return 路径记得写」。
            # 优先级：最新的终止原因赢（取消与 max_tokens 都是最后发生的事，压过更早的 guard 标记）。
            if cancelled:
                stop_reason = "cancelled"
            elif max_tokens_stopped:
                stop_reason = "max_tokens"
            elif tool_calls_exhausted:
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
        if cancelled:
            # 取消前没有产出正文（或硬取消拿不到正文）：给明确的停止文案，
            # 而不是「服务暂时不可用」—— 这轮是被停止的，不是坏掉的
            return CANCELLED_REPLY
        if max_tokens_stopped:
            # 走到了这里说明截断那次没生成任何可用正文（正文非空时上面就返回了）
            return (f"模型输出被截断（达到 max_tokens={max_tokens} 上限），未生成可用回答。"
                    f"请重试，或调大该 Agent 的 max_tokens 配置。")
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
        """LLM 消息来源：组装的 system prompt + Session surface 派生

        两条来源在汇合：提示词来自 SystemPrompt（每轮组装、不在会话历史里），
        其余来自 Event Log 派生（唯一事实源，无本地消息列表）。

        system 恒为 messages[0]：前部段的渲染结果跨轮稳定（动态内容排在提示词
        尾部），因此 KV cache 前缀不受逐轮变化的上游输出影响。
        """
        messages = self.session.derive_messages()
        if not self.system_prompt:
            return messages
        return [{"role": "system", "content": self.system_prompt}, *messages]

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

    def _record_llm_error(
        self, error: LlmError, *, step: int, attempt: int, retry_in: float | None,
    ) -> None:
        """一次失败的 LLM 尝试 → Event Log llm/error 事件（log-only，对齐 A 的 assistant/attempt）。

        **只记观测事实**：失败的尝试没有产出任何 LLM 可见内容 —— 没有 surface 事件，
        进不了 derive_messages，也不影响前缀缓存。重试因此完全可回放：翻 Event Log 就能
        看出这一轮问了几次、每次为什么失败、下次什么时候重试。
        """
        self.session.append(LLM_ERROR, error.fact(step=step + 1, attempt=attempt, retry_in=retry_in))

    async def _run_cancellable(self, aw, cancel: asyncio.Event | None):
        """跑一个 awaitable，取消信号一到就把它取消掉；信号胜出时抛 RunCancelled。

        取消**复用 asyncio 的取消机制**，不另发明一套协议：被等的协程在它自己的 await
        点上收到 CancelledError —— aiohttp 请求随之中止，ToolScheduler 也正好走进它既有的
        取消恢复路径（排空在途 + 未启动的补合成结果）。于是被取消方完全不需要认识
        「取消信号」这个概念，scheduler.py 的对外契约一个字都不用改。

        没有信号时不套任何包装（直接 await）：不传 cancel 的调用路径零开销、行为不变。
        """
        if cancel is None:
            return await aw

        work = asyncio.ensure_future(aw)
        stopper = asyncio.ensure_future(cancel.wait())
        try:
            await asyncio.wait({work, stopper}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            # 外部硬取消：把子任务一并收干净再抛 —— 不能留下孤儿任务继续跑工具 / 占着连接
            work.cancel()
            stopper.cancel()
            await asyncio.gather(work, stopper, return_exceptions=True)
            raise

        if work.done() and not work.cancelled():
            stopper.cancel()
            await asyncio.gather(stopper, return_exceptions=True)
            return work.result()   # 成功或失败都原样交给调用方（这里不解释结果）

        # 信号胜出：取消在途工作，等它把事实补完（ToolScheduler 的合成结果就在这一步落库），
        # 再把它的「交代」原样带出来 —— 被取消方可能已经救出了半截正文（如 LLM 流）
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        failure = None if work.cancelled() else work.exception()
        raise failure if isinstance(failure, RunCancelled) else RunCancelled()

    def _settle_interrupted(self, partial: dict) -> str:
        """取消时保留中断前已产出的正文（对齐 A 的 interrupted assistant message），返回该正文。

        ``tool_calls`` **一律丢弃**：取消多半落在参数没写完的时候，落库就是落不可复现的
        事实，而且会留下无配对 tool/result 的 dangling 消息（下一轮请求直接非法）。
        正文为空则什么都不落 —— 对齐 A 的 surface 规则（空消息不进历史）。
        """
        content = partial.get("content") or ""
        if not content:
            return ""
        self.session.append(ASSISTANT_MESSAGE, {"content": content, "tool_calls": []})
        logger.info(f"AgentLoop [{self.key}] 取消时保留已产出的 {len(content)} 字正文")
        return content

    async def _request_llm(
        self, session, base_url: str, api_key: str, payload: dict, deadline: float, step: int,
        cancel: asyncio.Event | None = None,
    ) -> tuple[dict, str | None, dict | None]:
        """一次 step 级 LLM 请求（含有限重试）：返回 (assistant message, finish_reason, usage)。

        重试边界在 **step 内**（对齐 A 的 step 重试循环）：不新开 step/start、不落任何
        surface 事实，失败的尝试只留 llm/error 事件。重试耗尽后抛 LlmError，由 step 体
        收口成「本步失败 → 本轮终止」。

        **截断（finish_reason=length）不走这条路** —— 对齐 A 的类型划分：max-tokens 在
        类型上就不携带 failure，进不了请求失败的处理链（A 的 agent/request-error 瀑布
        只对 error / aborted 触发）。截断由调用方的 finish_reason 分支收口。

        取消不走这条路：RunCancelled（协作信号）与 CancelledError（硬取消）都不是
        LlmError，直接穿透重试循环 —— 被取消的请求不该再重试。
        """
        max_retries = max(0, int(self.llm_params.get("max_llm_retries", DEFAULT_MAX_LLM_RETRIES)))
        attempt = 0
        while True:
            try:
                return await self._call_llm_once(session, base_url, api_key, payload, deadline, cancel)
            except LlmError as e:
                retry = e.retryable and attempt < max_retries
                delay = _retry_delay(attempt, e.retry_after) if retry else 0.0
                if retry and delay >= deadline - time.perf_counter():
                    # 退避等待会突破 max_wall_time：宁可早停，也不让本轮跑过截止时刻
                    retry, delay = False, 0.0
                self._record_llm_error(e, step=step, attempt=attempt + 1, retry_in=delay if retry else None)
                if not retry:
                    raise
                logger.info(f"AgentLoop [{self.key}] step {step + 1} LLM 重试（{e.code}）: {delay:.2f}s 后第 {attempt + 2} 次")
                await asyncio.sleep(delay)
                attempt += 1

    async def _call_llm_once(
        self, session, base_url: str, api_key: str, payload: dict, deadline: float,
        cancel: asyncio.Event | None = None,
    ) -> tuple[dict, str | None, dict | None]:
        """发一次 LLM 请求并校验响应；任何失败都抛 LlmError（调用方决定是否重试）。

        传输层异常在这里归一成 LlmError（超时 / 连接失败），于是「重试什么」只由
        失败码决定，上层不必认识 aiohttp 的异常谱系。取消异常（RunCancelled /
        CancelledError）不在此转换 —— 它们必须原样穿透。
        """
        try:
            if payload.get("stream"):
                msg, finish_reason = await self._call_llm_stream(
                    session, base_url, api_key, payload, deadline, cancel,
                )
                usage = msg.pop("usage", None)  # 流式：SSE usage chunk 由解析层塞进 message
            else:
                msg, finish_reason, usage = await self._call_llm_json(session, base_url, api_key, payload, deadline)
        except LlmError:
            raise
        except asyncio.TimeoutError as e:
            raise LlmError(f"LLM 请求超时: {e}", code=CODE_TIMEOUT) from e
        except aiohttp.ClientError as e:
            raise LlmError(f"LLM 连接失败: {e}", code=CODE_TRANSPORT) from e

        self._validate_response(msg, finish_reason)
        return msg, finish_reason, usage

    @staticmethod
    def _validate_response(msg: dict, finish_reason: str | None) -> None:
        """校验响应是否可用；不可用即抛 LlmError（是否重试由失败码决定）。

        三步，顺序即优先级：

          ① 结束原因词表 —— 对齐 A ``mapFinishReason`` 的 default 分支：**不认识的结束
             原因一律当失败**，不许冒充正常收尾（content_filter 之类的截断不是答案）。
             None（网关没给）不判，保持对既有网关的兼容。
          ② 截断（length）—— 到这里就返回：tool_calls 由调用方**整体丢弃**（对齐 A 的
             装配器掩码：截断时把 tool-call 块筛掉、正文照常保留），所以不校验也不报错。
          ③ tool_calls 完整性 —— **必须在落 assistant/message 之前**判定：Event Log 是
             append-only，一旦落进一条带残缺 tool_calls 的 assistant 消息就再也回退不了，
             它会留下无配对 tool/result 的 dangling 消息，让下一轮 derive_messages 非法。
        """
        if finish_reason is not None and finish_reason not in KNOWN_FINISH_REASONS:
            raise LlmError(f"模型以未知原因结束: {finish_reason}", code=CODE_UNKNOWN_FINISH)

        if finish_reason == "length":
            return

        bad = _invalid_tool_calls(msg.get("tool_calls") or [])
        if bad:
            raise LlmError(f"tool_calls 参数不可解析: {', '.join(bad)}", code=CODE_INVALID_RESPONSE)
        if not msg.get("content") and not msg.get("tool_calls"):
            # 200 却既无正文也无调用：网关抖动的典型形态，重发常能自愈
            raise LlmError("LLM 返回空响应（无 content 也无 tool_calls）", code=CODE_EMPTY_RESPONSE)

    def _tool_context(self, tool_call_id: str) -> ToolContext:
        """构造一次 Tool 调用的上下文（ToolScheduler 的 context_factory）。

        Tool 生命周期事件（started / progress / completed / failed）由 ToolRuntime /
        Executor 经 context.event_sink 发出，本 Loop 不感知、不拼装；
        tool_call_id 在构造时绑定到 sink（一次调用一个 sink）。
        """
        return ToolContext(
            session_id=self.session.header.id,
            tool_call_id=tool_call_id,
            agent_id=self.key,
            event_sink=SessionToolEventSink(self.session, tool_call_id, forward=self.on_event),
            sandbox_policy=self._sandbox_policy(),
            approval=self._approval_channel(),
        )

    @staticmethod
    def _approval_channel():
        """审批通道；未装配返回 None（需要审批的能力将 fail-closed）。

        与 :meth:`_sandbox_policy` 同模式：装配层的单例在这里惰性取用，
        Loop 只负责把它挂到 ToolContext 上，不解释它是什么 —— 审批是
        interaction 层的通用能力，Loop 不认识它的实现。
        """
        try:
            from backend.interaction.approval import get_approval_service
            return get_approval_service()
        except Exception as e:
            logger.warning(f"审批服务取用失败（需要审批的能力将一律被拒）: {e}")
            return None

    def _sandbox_policy(self):
        """本会话一次调用的沙箱执行策略；沙箱未装配时 None（MCP 工具不受影响）。

        工作区根来自 SessionHeader.cwd，首次调用时落地——它确定性派生自
        session_id + 部署配置，因此不需要额外的事件记录（Event Log 保持
        「只记事实」）。会话覆盖来自 ``sandbox/mode`` 事件的投影（find-last）。
        沙箱工具在策略缺失时 fail-closed。
        """
        try:
            from backend.agents.runtime.session.projections import project_sandbox_mode
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

    async def _call_llm_json(
        self, session, base_url: str, api_key: str, payload: dict, deadline: float,
    ) -> tuple[dict, str | None, dict | None]:
        """非流式 LLM 请求（OpenAI 兼容 /v1/chat/completions）：返回 (message, finish_reason, usage)。

        HTTP 状态必须**先于**响应体判定：非 2xx 时网关返回的是错误 JSON（甚至 HTML），
        照常当成功解析会把错误体当成 assistant message 落库。
        """
        remaining = max(deadline - time.perf_counter(), 1.0)
        async with session.post(
            f"{base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=aiohttp.ClientTimeout(total=min(60, remaining)),
        ) as resp:
            if resp.status >= 400:
                raise LlmError(
                    f"LLM 请求失败: HTTP {resp.status} {(await resp.text())[:200]}",
                    code=_classify_status(resp.status),
                    status=resp.status,
                    retry_after=_retry_after_seconds(resp.headers),
                )
            try:
                result = await resp.json()
            except Exception as e:  # noqa: BLE001 — 网关返 HTML/空体：按非法响应重试
                raise LlmError(f"LLM 返回非 JSON 响应: {e}", code=CODE_INVALID_RESPONSE, status=resp.status) from e

        if not isinstance(result, dict):
            raise LlmError(
                f"LLM 返回结构异常（顶层不是对象）: {type(result).__name__}",
                code=CODE_INVALID_RESPONSE, status=resp.status,
            )
        # choices 可能缺失或为空数组（网关错误体的常见形态）—— 统一退化成空 message，
        # 由 _validate_response 判成 EMPTY_RESPONSE（可重试），而不是在这里 IndexError
        choice = (result.get("choices") or [{}])[0] or {}
        msg = dict(choice.get("message") or {})
        return msg, choice.get("finish_reason"), result.get("usage")

    async def _call_llm_stream(
        self, session, base_url: str, api_key: str, payload: dict, deadline: float,
        cancel: asyncio.Event | None = None,
    ) -> tuple[dict, str | None]:
        """流式 LLM 请求（OpenAI 兼容 SSE）。逐 delta 广播事件，同时累积出完整 assistant message。

        返回 ``(message, finish_reason)``：finish_reason 取最后一个携带它的 chunk
        （"stop" / "tool_calls" / "length"）—— 截断判定依赖它，不能丢；
        usage（若 provider 返回）留在 ``message["usage"]``，由调用方取走后统一记录。

        取消：``cancel`` 每帧检查一次（流式是逐帧到达的，检查点的粒度就是 token 粒度），
        被取消时把已累积的 message 随 RunCancelled 带走 —— 它就是 A 的 interrupted 内容。
        ``except asyncio.CancelledError`` 同理：驱动它的任务被取消（协作信号取消了这次
        请求 / 外部硬取消）时，也要把半截事实交出去，而不是让它随协程一起消失。

        delta 字段（deepseek 系网关）:
          reasoning_content → thinking 事件（assistant/chunk，只广播不入 content）
          content           → text 事件（assistant/chunk）
          tool_calls        → 按 index 累积拼装
        """
        remaining = max(deadline - time.perf_counter(), 1.0)
        async with session.post(
            f"{base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=aiohttp.ClientTimeout(total=min(300, remaining)),  # 流式长任务放宽，但不能突破 max_wall_time
        ) as resp:
            if resp.status >= 400:
                # 与 _call_llm_json 同规：错误体不是 SSE，先看状态再看流
                raise LlmError(
                    f"LLM 请求失败: HTTP {resp.status} {(await resp.text())[:200]}",
                    code=_classify_status(resp.status),
                    status=resp.status,
                    retry_after=_retry_after_seconds(resp.headers),
                )
            msg: dict = {}
            finish_reason = None
            try:
                async for raw_line in resp.content:
                    if cancel is not None and cancel.is_set():
                        raise RunCancelled(partial=msg)
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
                    # choices 可能是空数组（usage chunk）—— 取 choice 前先兜住，否则 [0] 直接 IndexError
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0] or {}
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
                    delta = choice.get("delta") or {}

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
            except asyncio.CancelledError:
                # 是**协作信号**取消了这次请求（信号已置位）：把已累积的 message 一起交出去 ——
                # 不带上它，中断前的产出就真的没了，而那些 delta 明明已经广播给了用户。
                # 判据必须是信号本身：没有它（外部 task.cancel()），这里什么都不该拦 ——
                # 硬取消不是「决定停下来」，它必须原样传播给调用方。
                if cancel is not None and cancel.is_set():
                    raise RunCancelled(partial=msg) from None
                raise

            msg["role"] = "assistant"
            return msg, finish_reason  # usage 随 msg 带回，调用方统一记录 llm/usage 事件
