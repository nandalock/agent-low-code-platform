"""AgentLoop 自检（Phase 1：finish_reason / step 级重试 / 闭合语义）

mock 掉 LLM HTTP（llm.openai_chat.get_http_session —— 唯一发请求的地方），不连 DB /
网络 / MCP：响应按脚本逐个给出，覆盖 200 正常 / length 截断 / 429 / 5xx / 4xx / 非法
响应 / 中途异常，断言三件事：

  1. Event Log 闭合 —— 每个 step/start 都有配对的 step/end，turn 有且仅有一条 turn/end；
  2. 消息序列合法 —— assistant.tool_calls 的每一项都有配对的 tool 消息；
  3. 失败的尝试不落 surface 事实 —— 重试后 Event Log 里没有半截 assistant。

驱动方式 = **生产路径**（见 `_drive`）：投递 → 等 driver 收敛 → 取答案，Loop 没有
``run(question) -> str`` 这种一次性入口。

Usage:
    docker compose exec backend python backend/agents/runtime/tests/test_agent_loop.py
    # 或本机（仓库根目录下）：
    python backend/agents/runtime/tests/test_agent_loop.py
"""
import asyncio
import json
import logging
import os
import sys

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from backend.agents.runtime.llm import (  # noqa: E402
    CHUNK_FINISH,
    CHUNK_TEXT,
    CHUNK_THINKING,
    CHUNK_TOOL_CALLS,
    LoopHooks,
    OpenAiChatClient,
    RetryDecision,
    StreamChunk,
)
from backend.agents.runtime.llm import openai_chat as client_mod  # noqa: E402
from backend.agents.runtime.llm import retry as retry_mod  # noqa: E402
from backend.agents.runtime.loop.agent_loop import CANCELLED_REPLY, AgentLoop  # noqa: E402
from backend.agents.runtime.loop.assistant_stream import AssistantStream  # noqa: E402
from backend.agents.runtime.loop.inbox import (  # noqa: E402
    NEXT_STEP,
    NEXT_TURN,
    ReactLoopInbox,
)
from backend.agents.runtime.session import (  # noqa: E402
    ASSISTANT_MESSAGE,
    LLM_ERROR,
    STEP_END,
    STEP_START,
    TOOL_CALL,
    TOOL_RESULT,
    TURN_END,
    TURN_START,
    USER_MESSAGE,
    Session,
    SessionToolRecorder,
)
from backend.tool_system.registry.descriptor import PARALLEL  # noqa: E402
from backend.tool_system.runtime.scheduler import (  # noqa: E402
    ABORTED_STARTED_MESSAGE,
    ToolScheduler,
)

# ── 测试替身：HTTP ──


class _Boom:
    """流里的「炸点」：读到它时抛异常，模拟读到一半连接断了"""

    def __init__(self, exc: Exception):
        self.exc = exc


class _Pause:
    """流里的「停顿」：读到它时 await 一会儿，给取消信号一个落地窗口"""

    def __init__(self, seconds: float):
        self.seconds = seconds


class _LineStream:
    """fake resp.content：异步行迭代器。元素照原样 yield（给非 bytes 即模拟解析层炸）。"""

    def __init__(self, lines: list):
        self._lines = lines

    def __aiter__(self):
        async def gen():
            for line in self._lines:
                if isinstance(line, _Boom):
                    raise line.exc
                if isinstance(line, _Pause):
                    await asyncio.sleep(line.seconds)
                    continue
                yield line
        return gen()


class _Resp:
    """假 aiohttp 响应：非流式读 status/json/text，流式读 content（逐行 SSE）

    status / headers 是真实响应一定有、而失败判定要读的字段。
    """

    def __init__(self, *, status: int = 200, payload=None, lines: list | None = None, headers: dict | None = None):
        self.status = status
        self.headers = headers or {}
        self._payload = payload
        self._lines = lines or []

    async def json(self):
        if self._payload is None:
            raise ValueError("响应体不是 JSON")
        return self._payload

    async def text(self):
        return json.dumps(self._payload, ensure_ascii=False) if self._payload is not None else ""

    @property
    def content(self):
        return _LineStream(self._lines)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeHTTP:
    """按脚本顺序发响应并记录每次请求 payload；脚本只剩一条时重复使用它。"""

    def __init__(self, responses: list):
        assert responses, "至少给一个响应"
        self._responses = list(responses)
        self.requests: list[dict] = []

    def post(self, url, **kwargs):
        self.requests.append(kwargs.get("json") or {})
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


def _install(responses: list) -> _FakeHTTP:
    """把 LLM HTTP 调用换成本用例的脚本化假响应

    patch 点是**适配器**（llm/openai_chat.py）—— 它是唯一 import get_http_session 的地方，
    循环不再碰 HTTP（见 llm/client.py 的契约）。
    """
    http = _FakeHTTP(responses)

    async def _get_session():
        return http

    client_mod.get_http_session = _get_session
    return http


def _answer(text: str = "好的", finish_reason: str = "stop", tool_calls: list | None = None) -> _Resp:
    """正常响应（非流式）"""
    return _Resp(payload={
        "choices": [{
            "message": {"role": "assistant", "content": text, "tool_calls": tool_calls or []},
            "finish_reason": finish_reason,
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    })


def _status(status: int, *, retry_after=None, payload=None) -> _Resp:
    """错误响应（非流式）：带 HTTP 状态与可选 Retry-After"""
    headers = {} if retry_after is None else {"Retry-After": str(retry_after)}
    return _Resp(status=status, headers=headers, payload=payload or {"error": f"HTTP {status}"})


def _sse(*frames: dict, finish_reason: str | None = None) -> _Resp:
    """流式响应：正文帧 + finish_reason 帧 + usage 帧（choices 空）+ [DONE]

    顺序照 DeepSeek 的实际形状：usage chunk 在 [DONE] 之前，且它的 choices 是空数组。
    非 dict 的帧参数原样透传给 _LineStream（_Pause / _Boom 这类控制元素不编码）。
    """
    lines = [
        f"data: {json.dumps(f)}\n".encode("utf-8") if isinstance(f, dict) else f
        for f in frames
    ]
    if finish_reason:
        lines.append(f'data: {json.dumps({"choices": [{"delta": {}, "finish_reason": finish_reason}]})}\n'.encode("utf-8"))
    lines.append(f'data: {json.dumps({"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 1}})}\n'.encode("utf-8"))
    lines.append(b"data: [DONE]\n")
    return _Resp(lines=lines)


def _text_chunk(text: str) -> dict:
    return {"choices": [{"delta": {"content": text}}]}


# ── 测试替身：工具执行 ──


class _FakeRuntime:
    """假 ToolRuntime：记录调用并回固定结果（工具执行本身由 test_scheduler 覆盖）"""

    def __init__(self, *, delay: float = 0.0, on_call=None):
        self.calls: list[str] = []
        self._delay = delay
        self._on_call = on_call      # 执行中的副作用钩子（用来在「两步之间」制造取消）

    async def execute(self, tool_name: str, args: dict, context) -> dict:
        self.calls.append(tool_name)
        if self._on_call is not None:
            self._on_call()
        if self._delay:
            await asyncio.sleep(self._delay)
        return {"ok": tool_name, "args": args}


# ── 装配 ──

_TOOL_SCHEMA = {
    "type": "function",
    "function": {"name": "echo", "description": "回声", "parameters": {"type": "object", "properties": {}}},
}


def _loop(*, session=None, config=None, llm_params=None, on_event=None, tool_schemas=None, hooks=None):
    session = session if session is not None else Session()
    cfg = {"api_key": "k", "base_url": "http://fake", "model": "m", **(config or {})}
    params = {
        "max_steps": 5, "max_tool_calls": 30, "max_wall_time": 300.0, "max_parallel_tools": 4,
        "max_tokens": 1024, "temperature": 0.3, "max_llm_retries": 2, **(llm_params or {}),
    }
    # 装配 LLM 出口：循环只拿到一个 LlmClient，凭据与端点在这一层搬完就交出去
    llm = OpenAiChatClient(
        api_key=cfg.get("api_key", ""), base_url=cfg.get("base_url", ""),
        max_llm_retries=params["max_llm_retries"],
    )
    loop = AgentLoop(
        key="test_agent", llm_params=params, llm=llm, model=cfg.get("model", ""),
        tool_schemas=tool_schemas or [], tenant_id=1, system_prompt="", on_event=on_event,
        session=session, hooks=hooks, fallback_reply=cfg.get("fallback_reply", ""),
    )
    return loop, session


async def _bridge_cancel(loop: AgentLoop, cancel: asyncio.Event) -> None:
    """外部信号 → loop.cancel（与 AgentRuntime._bridge_cancel 同形）。

    ``keep_inbox=True``：转发只负责叫停，不替投递方丢弃还没投出去的东西。
    """
    await cancel.wait()
    loop.cancel("测试信号", keep_inbox=True)


async def _drive(loop: AgentLoop, question: str = "问题", cancel: asyncio.Event | None = None) -> str:
    """三段式跑一轮：**投递 → 等 driver 收敛 → 取答案**（与 AgentRuntime._drive 同形）。

    自检跑的就是生产路径：Loop 没有一次性入口，谁用谁自己投递、自己等。
    """
    bridge = asyncio.create_task(_bridge_cancel(loop, cancel)) if cancel is not None else None
    try:
        loop.followup(question)
        await loop.when_idle()
    finally:
        if bridge is not None:
            bridge.cancel()          # 不 await：它等的信号可能永远不来
    return loop.answer_text()


def _run(loop: AgentLoop, question: str = "问题") -> str:
    return asyncio.run(_drive(loop, question))


def _run_cancelling(
    loop: AgentLoop, *, after: float = 0.05, question: str = "问题", cancel: asyncio.Event | None = None,
) -> str:
    """跑一轮并在 after 秒后 set 协作取消信号（兜底），返回本次运行的答案。

    ``cancel`` 由调用方给时**必须就是交给 driver 的那一个**：取消靠的是「同一个 Event
    被 set」，钩子 set 一个没人读的 Event 等于没取消（假轮次只有亚毫秒，兜底计时器来不及）。
    """
    cancel = asyncio.Event() if cancel is None else cancel

    async def scenario():
        async def _fire():
            await asyncio.sleep(after)
            cancel.set()

        answer, _ = await asyncio.gather(_drive(loop, question, cancel), _fire())
        return answer

    return asyncio.run(scenario())


def _run_with_hard_cancel(loop: AgentLoop, *, after: float = 0.05, question: str = "问题") -> bool:
    """跑一轮并在 after 秒后 task.cancel()（硬取消），返回 CancelledError 是否向上传播"""

    async def scenario():
        task = asyncio.create_task(_drive(loop, question))
        await asyncio.sleep(after)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return True
        return False

    return asyncio.run(scenario())


def _collect_events():
    """on_event 收集器（有订阅者 → Loop 走流式路径）"""
    events: list[dict] = []

    async def _sink(ev: dict):
        events.append(ev)

    return events, _sink


# ── 断言工具 ──


def _stop_reason(session: Session) -> str:
    ends = [e.data["stop_reason"] for e in session.log if e.type == TURN_END]
    assert len(ends) == 1, f"应有且仅有一条 turn/end，实际 {len(ends)}"
    return ends[0]


def _assert_closed(session: Session) -> None:
    """闭合不变式：step/start 与 step/end 严格成对，每个 turn/start 都有配对的 turn/end"""
    started = [e.data["step"] for e in session.log if e.type == STEP_START]
    ended = [e.data["step"] for e in session.log if e.type == STEP_END]
    assert ended == started, f"step 未配对: start={started} end={ended}"
    turns = [e.type for e in session.log if e.type in (TURN_START, TURN_END)]
    starts, ends = turns.count(TURN_START), turns.count(TURN_END)
    assert starts == ends >= 1, f"turn 未配对: start={starts} end={ends}"


def _assert_messages_legal(session: Session) -> list[dict]:
    """assistant.tool_calls 的每一项都要有配对的 tool 消息（否则下一轮请求非法）"""
    messages = session.derive_messages()
    pending: list[str] = []
    for m in messages:
        if m["role"] == "assistant" and m.get("tool_calls"):
            pending.extend(c["id"] for c in m["tool_calls"])
        elif m["role"] == "tool":
            assert pending and pending.pop(0) == m["tool_call_id"], "tool 消息与 tool_calls 不对齐"
    assert not pending, f"有未配对的 tool_calls: {pending}"
    return messages


def _errors(session: Session) -> list[dict]:
    return [e.data for e in session.log if e.type == LLM_ERROR]


# ── 正常路径 ──


def test_completed_answer_closes_turn():
    http = _install([_answer("最终答案")])
    loop, session = _loop()

    assert _run(loop) == "最终答案"

    assert _stop_reason(session) == "completed"
    _assert_closed(session)
    _assert_messages_legal(session)
    assert _errors(session) == []          # 一次成功，无失败尝试
    assert len(http.requests) == 1         # 没重试
    # assistant 消息与 llm/usage 都在（观测链不变）
    assert [e.type for e in session.log] == [
        "turn/start", "user/message", "step/start", "assistant/message", "llm/usage", "step/end", "turn/end",
    ]


def test_missing_config_returns_fallback_without_open_turn():
    """未配置 LLM：直接回落 fallback，不落任何事件（不开一个没有执行的 turn）"""
    session = Session()
    loop, _ = _loop(session=session, config={"api_key": ""})
    assert _run(loop) == "服务未配置"
    assert session.log == []


def test_wall_time_exhausted_before_first_step():
    """max_wall_time 用尽：不发请求，仍以 turn/end 收口"""
    http = _install([_answer()])
    loop, session = _loop(llm_params={"max_wall_time": 0.0})

    answer = _run(loop)

    assert _stop_reason(session) == "max_wall_time"
    _assert_closed(session)
    assert http.requests == []
    assert "处理时间过长" in answer


# ── finish_reason=length（max-tokens sticky）──


def test_length_truncation_returns_partial_answer():
    """正文被截断：本轮终止，答案就是已生成的截断内容；截断不是失败，不重试"""
    http = _install([_answer("这是一段被截断的回答", finish_reason="length")])
    loop, session = _loop()

    assert _run(loop) == "这是一段被截断的回答"

    assert _stop_reason(session) == "max_tokens"
    _assert_closed(session)
    _assert_messages_legal(session)
    assert len(http.requests) == 1
    assert _errors(session) == []
    # 落库的 assistant 消息不带任何 tool_calls（截断的调用一律不落）
    assert [e.data["tool_calls"] for e in session.log if e.type == ASSISTANT_MESSAGE] == [[]]


def test_length_truncated_tool_calls_are_dropped_not_retried():
    """截断的 tool_calls：不落库、不执行、**也不重试**（截断是正常结束的一种，不是失败）

    对齐 A：max-tokens 在类型上就不携带 failure，进不了请求失败的处理链；装配器
    在落盘前把 tool-call 块整体筛掉，只留正文。
    """
    truncated = _answer("思考到一半", finish_reason="length", tool_calls=[
        {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": '{"a": '}},
    ])
    http = _install([truncated])
    loop, session = _loop()

    assert _run(loop) == "思考到一半"          # 正文是模型的真实产出，照常保留

    assert _stop_reason(session) == "max_tokens"
    _assert_closed(session)
    messages = _assert_messages_legal(session)
    assert not any(m.get("tool_calls") for m in messages)   # 没有 dangling tool_calls
    assert not [e for e in session.log if e.type in (TOOL_CALL, TOOL_RESULT)]  # 也没执行
    assert len(http.requests) == 1                          # 截断不重试
    assert _errors(session) == []                           # 截断不是失败：不落 llm/error


def test_length_with_empty_content_leaves_no_assistant_message():
    """纯 tool_calls 截断（正文为空）：不落 assistant 消息（对齐 A 的 surface 规则）"""
    _install([_answer("", finish_reason="length", tool_calls=[
        {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": '{"a"'}},
    ])])
    loop, session = _loop()

    answer = _run(loop)

    assert _stop_reason(session) == "max_tokens"
    _assert_closed(session)
    _assert_messages_legal(session)
    assert [e for e in session.log if e.type == ASSISTANT_MESSAGE] == []
    assert "max_tokens" in answer and "调大" in answer


def test_invalid_tool_calls_retry_then_succeed():
    """非截断的坏 tool_calls（finish_reason=tool_calls）：按非法响应重试，成功那次才落库"""
    bad = _answer("", finish_reason="tool_calls", tool_calls=[
        {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": '{"a"'}},
    ])
    http = _install([bad, _answer("重试成功")])
    loop, session = _loop()

    assert _run(loop) == "重试成功"

    assert _stop_reason(session) == "completed"
    _assert_closed(session)
    _assert_messages_legal(session)
    assert len(http.requests) == 2
    # 只有成功那次落了 assistant/message（失败尝试没留下任何 surface 事实）
    assistants = [e.data for e in session.log if e.type == ASSISTANT_MESSAGE]
    assert [a["content"] for a in assistants] == ["重试成功"]
    # 失败尝试仍然可回放：llm/error 事件 + 第二次请求的 messages 里没有半截内容
    assert [d["code"] for d in _errors(session)] == ["INVALID_RESPONSE"]
    assert not any(m["role"] == "assistant" for m in http.requests[1]["messages"])


def test_unknown_finish_reason_is_a_failure_not_an_answer():
    """未知结束原因（如 content_filter）当失败，不许冒充正常收尾"""
    _install([_answer("被过滤后剩下的半句", finish_reason="content_filter")])
    loop, session = _loop()

    answer = _run(loop)

    assert len([e for e in _errors(session)]) == 1        # 不重试：未知原因不是瞬时故障
    assert [d["code"] for d in _errors(session)] == ["UNKNOWN_FINISH"]
    assert _stop_reason(session) == "error"
    _assert_closed(session)
    # 那半句没有进历史（A 的 error finish 同样不 settle assistant/message）
    assert [m["role"] for m in session.derive_messages()] == ["user"]
    assert answer == "服务暂时不可用"


# ── 失败分类与重试 ──


def test_429_is_retried_then_succeeds():
    """429 限流：退避重试（认 Retry-After），第二次成功"""
    http = _install([_status(429, retry_after=0), _answer("限流后成功")])
    loop, session = _loop()

    assert _run(loop) == "限流后成功"

    assert _stop_reason(session) == "completed"
    assert len(http.requests) == 2
    errors = _errors(session)
    assert [(d["code"], d["status"]) for d in errors] == [("RATE_LIMIT", 429)]
    assert errors[0]["retry_in"] is not None      # 记下了下次重试的时间（可回放）


def test_server_error_retries_are_bounded_then_turn_closes():
    """5xx：重试 max_llm_retries 次后放弃，仍以 turn/end 收口（stop_reason=error）"""
    http = _install([_status(503)])
    loop, session = _loop(llm_params={"max_llm_retries": 2})

    answer = _run(loop)

    assert _stop_reason(session) == "error"
    _assert_closed(session)
    assert len(http.requests) == 3                 # 1 次 + 2 次重试
    assert [d["code"] for d in _errors(session)] == ["SERVER"] * 3
    assert _errors(session)[-1]["retry_in"] is None  # 最后一次是放弃，不再排重试
    assert answer == "服务暂时不可用"                # 兜底文案（既有行为）
    # 失败的尝试不落任何 surface 事实：历史里只有用户的问
    assert [m["role"] for m in session.derive_messages()] == ["user"]


def test_max_llm_retries_zero_disables_retry():
    """max_llm_retries=0：只发一次（配置可关掉重试）"""
    http = _install([_status(500)])
    loop, _ = _loop(llm_params={"max_llm_retries": 0})

    _run(loop)

    assert len(http.requests) == 1


def test_client_error_is_not_retried():
    """其余 4xx：鉴权/参数错误重试无用，直接终止本步"""
    http = _install([_status(401, payload={"error": "invalid api key"})])
    loop, session = _loop()

    _run(loop)

    assert len(http.requests) == 1
    assert _stop_reason(session) == "error"
    _assert_closed(session)
    assert [(d["code"], d["status"]) for d in _errors(session)] == [("CLIENT", 401)]


def test_empty_response_is_treated_as_retryable_failure():
    """200 但既无正文也无 tool_calls：按空响应重试，而不是当成空答案返回"""
    http = _install([_answer("")])
    loop, session = _loop()

    assert _run(loop) == "服务暂时不可用"

    assert _stop_reason(session) == "error"
    assert [d["code"] for d in _errors(session)] == ["EMPTY_RESPONSE"] * 3
    assert len(http.requests) == 3


def test_retry_delay_is_bounded_and_honors_retry_after():
    """退避：指数增长、上限截断、抖动有界、Retry-After 取较大者"""
    old = retry_mod.LLM_RETRY_INITIAL_DELAY
    retry_mod.LLM_RETRY_INITIAL_DELAY = 0.5
    try:
        assert 0.45 <= retry_mod.retry_delay(0) <= 0.55          # 500ms ± 10%
        assert 1.8 <= retry_mod.retry_delay(2) <= 2.2            # 2s ± 10%
        assert 9.0 <= retry_mod.retry_delay(9) <= 11.0           # 上限 10s（不再翻倍）
        assert 2.7 <= retry_mod.retry_delay(0, retry_after=3.0) <= 3.3   # 认对端建议
    finally:
        retry_mod.LLM_RETRY_INITIAL_DELAY = old


# ── 拦截通道（hooks.request_error）──


def test_request_error_hook_overrides_builtin_policy():
    """钩子说了算：401（CLIENT，内置策略本不重试）被钩子放行，重试后成功"""
    calls: list[tuple[str, int]] = []

    async def hook(error, attempt):
        calls.append((error.code, attempt))
        return RetryDecision(delay=0.0, reason="放行") if attempt == 0 else None

    http = _install([_status(401), _answer("钩子放行后成功")])
    loop, session = _loop(hooks=LoopHooks(request_error=hook))

    assert _run(loop) == "钩子放行后成功"

    assert calls == [("CLIENT", 0)]                  # 只问过一次：第二次钩子自己拒了
    assert len(http.requests) == 2
    assert [d["code"] for d in _errors(session)] == ["CLIENT"]
    assert _stop_reason(session) == "completed"
    _assert_closed(session)


def test_request_error_hook_failure_falls_back_to_builtin_policy():
    """钩子抛异常：记 exception 后按默认策略继续 —— 拦截器坏掉不打死执行链（规矩 1）"""
    async def broken_hook(error, attempt):
        raise RuntimeError("钩子坏了")

    http = _install([_status(429, retry_after=0), _answer("默认策略兜住了")])
    loop, session = _loop(hooks=LoopHooks(request_error=broken_hook))

    logging.disable(logging.CRITICAL)                # 这条路的 logger.exception 是预期输出
    try:
        assert _run(loop) == "默认策略兜住了"
    finally:
        logging.disable(logging.NOTSET)

    assert len(http.requests) == 2                   # 429 可重试，默认策略放了行
    assert [d["code"] for d in _errors(session)] == ["RATE_LIMIT"]
    assert _stop_reason(session) == "completed"


# ── 流式路径 ──


def test_stream_reads_finish_reason_and_survives_usage_chunk():
    """流式：usage chunk（choices 空）不炸、正文照常累积、finish_reason 被读出"""
    http = _install([_sse(_text_chunk("流式"), _text_chunk("回答"), finish_reason="length")])
    events, sink = _collect_events()
    loop, session = _loop(on_event=sink)

    assert _run(loop) == "流式回答"

    assert _stop_reason(session) == "max_tokens"     # finish_reason 真的被读了
    _assert_closed(session)
    assert http.requests[0]["stream"] is True
    assert [e.data["kind"] for e in session.log if e.type == "assistant/chunk"] == ["text", "text"]
    assert [e.type for e in session.log if e.type == "llm/usage"] == ["llm/usage"]


def test_stream_http_error_is_structured_not_silent():
    """流式请求拿到 5xx：按失败码重试并收口，而不是静默当成空回答"""
    http = _install([_Resp(status=500, payload={"error": "boom"})])
    events, sink = _collect_events()
    loop, session = _loop(on_event=sink)

    answer = _run(loop)

    assert _stop_reason(session) == "error"
    _assert_closed(session)
    assert [d["code"] for d in _errors(session)] == ["SERVER"] * 3
    assert len(http.requests) == 3
    assert answer == "服务暂时不可用"


def test_stream_transport_failure_is_retried():
    """流读到一半连接断：按 TRANSPORT 重试，成功那次照常收敛

    同时钉住已知边界（见模块头）：失败尝试已经广播出去的 chunk 撤不回来，
    日志里两段 chunk 都在 —— 它们确实发生过，Event Log 不该替它们消失。
    """
    broken = _Resp(lines=[
        f"data: {json.dumps(_text_chunk('半截'))}\n".encode("utf-8"),
        _Boom(aiohttp.ClientError("connection reset")),
    ])
    http = _install([broken, _sse(_text_chunk("完整回答"), finish_reason="stop")])
    events, sink = _collect_events()
    loop, session = _loop(on_event=sink)

    assert _run(loop) == "完整回答"

    assert _stop_reason(session) == "completed"
    _assert_closed(session)
    assert [d["code"] for d in _errors(session)] == ["TRANSPORT"]
    assert len(http.requests) == 2
    assert [e.data["delta"] for e in session.log if e.type == "assistant/chunk"] == ["半截", "完整回答"]


def test_unexpected_exception_mid_step_still_closes_events():
    """step 中途炸出非 LLM 异常：step/end 与 turn/end 仍然闭合（finally 保障）"""
    bad = _Resp(lines=[None])          # 解析层拿到非 bytes → AttributeError（非 LlmError）
    _install([bad])
    events, sink = _collect_events()
    loop, session = _loop(on_event=sink)

    _run(loop)

    assert _stop_reason(session) == "error"
    _assert_closed(session)            # 关键：异常路径也闭合
    assert [e.data["step"] for e in session.log if e.type == STEP_START] == [1]


# ── 工具往返（重构不能打断既有执行链）──


def _with_fake_tools(
    loop: AgentLoop, session: Session, *, runtime: _FakeRuntime | None = None, max_parallel: int = 2,
) -> _FakeRuntime:
    """把 Loop 的调度器换成假执行器（工具执行本身由 test_scheduler.py 覆盖）"""
    runtime = runtime or _FakeRuntime()
    loop._scheduler = ToolScheduler(
        recorder=SessionToolRecorder(session),
        context_factory=loop._tool_context,
        runtime=runtime,
        max_parallel_tools=max_parallel,
        mode_resolver=lambda name: PARALLEL,
    )
    return runtime


def test_tool_roundtrip_keeps_message_sequence_legal():
    """一次工具往返：tool/call 与 tool/result 成对，消息序合法，最终答案收敛"""
    first = _answer("", finish_reason="tool_calls", tool_calls=[
        {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": '{"q": "hi"}'}},
    ])
    http = _install([first, _answer("工具跑完了")])
    loop, session = _loop(tool_schemas=[_TOOL_SCHEMA])
    runtime = _with_fake_tools(loop, session)

    assert _run(loop) == "工具跑完了"

    assert runtime.calls == ["echo"]
    assert _stop_reason(session) == "completed"
    _assert_closed(session)
    assert [e.data["step"] for e in session.log if e.type == STEP_START] == [1, 2]
    messages = _assert_messages_legal(session)
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "assistant"]
    # tenant_id 仍然被系统强注入（不信任 LLM 传参）
    call_event = next(e for e in session.log if e.type == TOOL_CALL)
    assert call_event.data["args"]["tenant_id"] == 1


def test_max_tool_calls_guard_skips_and_stops():
    """guard 命中：超限的调用不执行，但仍成对落合成结果（消息序列保持合法）"""
    first = _answer("", finish_reason="tool_calls", tool_calls=[
        {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": "{}"}},
    ])
    second = _answer("", finish_reason="tool_calls", tool_calls=[
        {"id": "c2", "type": "function", "function": {"name": "echo", "arguments": "{}"}},
    ])
    _install([first, second])
    loop, session = _loop(tool_schemas=[_TOOL_SCHEMA], llm_params={"max_tool_calls": 1})
    runtime = _with_fake_tools(loop, session)

    answer = _run(loop)

    assert runtime.calls == ["echo"]                  # 第二次没执行
    assert _stop_reason(session) == "max_tool_calls"
    _assert_closed(session)
    _assert_messages_legal(session)
    results = [e.data for e in session.log if e.type == TOOL_RESULT]
    assert len(results) == 2                          # 合成的那个也落库了
    assert "上限" in json.loads(results[1]["content"])["error"]
    assert "工具调用次数过多" in answer


# ── 取消（Phase 2）──


def test_cancel_between_steps_stops_before_next_step():
    """取消落在 step 间隙：已完成的步骤保留真实结果，不再开新 step"""
    cancel = asyncio.Event()
    first = _answer("", finish_reason="tool_calls", tool_calls=[
        {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": "{}"}},
    ])
    http = _install([first, _answer("不该走到这里")])
    loop, session = _loop(tool_schemas=[_TOOL_SCHEMA])
    # 工具执行期间 set 信号 = 「第 1 步做完了，第 2 步还没开始」这个时刻
    runtime = _FakeRuntime(on_call=cancel.set)
    _with_fake_tools(loop, session, runtime=runtime)

    # 信号由工具钩子 set（after 只是兜底）—— 它必须就是交给 run() 的那一个
    answer = _run_cancelling(loop, after=0.05, cancel=cancel)

    assert runtime.calls == ["echo"]
    assert _stop_reason(session) == "cancelled"
    _assert_closed(session)
    assert [e.data["step"] for e in session.log if e.type == STEP_START] == [1]
    assert len(http.requests) == 1               # 没有第 2 次请求
    # 第 1 步的工具结果是**真实**的（没有被取消改写）
    result = json.loads([e.data for e in session.log if e.type == TOOL_RESULT][0]["content"])
    assert result == {"ok": "echo", "args": {"tenant_id": 1}}
    assert answer == CANCELLED_REPLY


def test_cancel_during_llm_stream_keeps_partial_answer():
    """取消落在 LLM 流中途：半截正文照样落库（A 的 interrupted 语义），turn 闭合"""
    _install([_sse(_text_chunk("半截回答"), _Pause(5.0), _text_chunk("永远到不了"))])
    events, sink = _collect_events()
    loop, session = _loop(on_event=sink)

    answer = _run_cancelling(loop)

    assert answer == "半截回答"                   # 已产出的正文保留下来
    assert _stop_reason(session) == "cancelled"
    _assert_closed(session)
    _assert_messages_legal(session)
    assistants = [e.data for e in session.log if e.type == ASSISTANT_MESSAGE]
    assert [(a["content"], a["tool_calls"]) for a in assistants] == [("半截回答", [])]
    # 广播出去的增量仍是事实（前端已经看到过）
    assert [e.data["delta"] for e in session.log if e.type == "assistant/chunk"] == ["半截回答"]


def test_cancel_during_llm_stream_drops_incomplete_tool_calls():
    """取消时若正处在 tool_call 中途：参数可能没写完 → 整体丢弃，绝不留 dangling"""
    _install([_sse(
        {"choices": [{"delta": {"content": "我先查一下"}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c1", "function": {"name": "echo", "arguments": '{"q"'}},
        ]}}]},
        _Pause(5.0),
    )])
    events, sink = _collect_events()
    loop, session = _loop(on_event=sink)

    assert _run_cancelling(loop) == "我先查一下"

    assert _stop_reason(session) == "cancelled"
    _assert_closed(session)
    messages = _assert_messages_legal(session)
    assert not any(m.get("tool_calls") for m in messages)
    assert not [e for e in session.log if e.type in (TOOL_CALL, TOOL_RESULT)]


def test_cancel_during_tool_execution_records_synthetic_results():
    """取消落在工具执行中途：调度器的取消恢复路径生效 —— 未完成的调用补合成结果"""
    first = _answer("", finish_reason="tool_calls", tool_calls=[
        {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": "{}"}},
        {"id": "c2", "type": "function", "function": {"name": "echo", "arguments": "{}"}},
    ])
    http = _install([first, _answer("不该走到这里")])
    loop, session = _loop(tool_schemas=[_TOOL_SCHEMA])

    # 池宽 2：两个调用一起启动后一直不结束；第二个也跑起来之后 set 信号 —— 于是取消
    # 一定落在「两个都已启动、都还没完成」的时刻（不靠 sleep 赌时序）
    cancel = asyncio.Event()
    started = {"n": 0}

    def _on_call():
        started["n"] += 1
        if started["n"] >= 2:
            cancel.set()

    _with_fake_tools(
        loop, session, runtime=_FakeRuntime(delay=5.0, on_call=_on_call), max_parallel=2,
    )

    # 信号由工具钩子 set，after=2.0 只是兜底（钩子没触发也不会挂死用例）
    answer = _run_cancelling(loop, after=2.0, cancel=cancel)

    assert _stop_reason(session) == "cancelled"
    _assert_closed(session)
    assert len(http.requests) == 1
    # 两个调用都成对落库，且都是「已启动被中断」的合成结果（scheduler 的取消路径）
    assert len([e for e in session.log if e.type == TOOL_CALL]) == 2
    results = [json.loads(e.data["content"])["error"] for e in session.log if e.type == TOOL_RESULT]
    assert results == [ABORTED_STARTED_MESSAGE, ABORTED_STARTED_MESSAGE]
    _assert_messages_legal(session)
    assert answer == CANCELLED_REPLY


def test_cancel_before_first_step_closes_turn_without_request():
    """投递后立刻叫停（driver 还没开第一步）：不发请求、不开 step，turn 照常闭合"""
    http = _install([_answer()])
    loop, session = _loop()

    async def scenario():
        loop.followup("问题")                      # 投递：driver 已排班但还没跑
        loop.cancel("测试信号", keep_inbox=True)    # 同一 tick 内叫停
        await loop.when_idle()
        return loop.answer_text()

    answer = asyncio.run(scenario())

    assert _stop_reason(session) == "cancelled"
    _assert_closed(session)
    assert http.requests == []
    assert [e.type for e in session.log if e.type == STEP_START] == []
    assert answer == CANCELLED_REPLY


def test_hard_cancel_closes_events_and_propagates():
    """硬取消（task.cancel()）：事件照样闭合，但 CancelledError 必须继续向上传播"""
    _install([_sse(_text_chunk("被打断"), _Pause(5.0))])
    events, sink = _collect_events()
    loop, session = _loop(on_event=sink)

    raised = _run_with_hard_cancel(loop)

    assert raised, "硬取消必须向上传播（不能吞掉 CancelledError）"
    assert _stop_reason(session) == "cancelled"
    _assert_closed(session)
    _assert_messages_legal(session)


def test_unset_cancel_signal_is_transparent():
    """传了信号但没人 set：行为与不传完全一致（包装层不改变结果）"""
    cancel = asyncio.Event()
    first = _answer("", finish_reason="tool_calls", tool_calls=[
        {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": "{}"}},
    ])
    http = _install([first, _answer("正常完成")])
    loop, session = _loop(tool_schemas=[_TOOL_SCHEMA])
    runtime = _with_fake_tools(loop, session)

    answer = asyncio.run(_drive(loop, cancel=cancel))

    assert answer == "正常完成"
    assert _stop_reason(session) == "completed"
    _assert_closed(session)
    assert runtime.calls == ["echo"]
    assert len(http.requests) == 2


def test_cancel_signal_does_not_swallow_llm_failure():
    """信号未触发时的 LLM 失败仍按失败码处理（包装层不吞异常）"""
    cancel = asyncio.Event()
    http = _install([_status(401)])
    loop, session = _loop(llm_params={"max_llm_retries": 0})

    answer = asyncio.run(_drive(loop, cancel=cancel))

    assert answer == "服务暂时不可用"
    assert _stop_reason(session) == "error"
    assert [(d["code"], d["status"]) for d in _errors(session)] == [("CLIENT", 401)]
    assert len(http.requests) == 1


# ── 运行中输入（Phase 3：followup / steer / inject）──


def test_inbox_claim_semantics():
    """claim：先清空全部 next-step；target=next-turn 时再取队首 1 条 next-turn"""
    inbox = ReactLoopInbox()
    inbox.append(NEXT_TURN, "a")
    inbox.append(NEXT_TURN, "b")
    inbox.append(NEXT_STEP, "s1")
    inbox.append(NEXT_STEP, "s2")

    assert inbox.has_pending and inbox.next_step_length == 2
    assert inbox.claim(NEXT_STEP, 1) == ["s1", "s2"]     # 只认领 next-step，next-turn 一条不动
    assert inbox.next_step_length == 0
    assert inbox.claim(NEXT_TURN, 1) == ["a"]            # 队首 1 条 next-turn
    assert inbox.claim(NEXT_TURN, 2) == ["b"]
    assert not inbox.has_pending
    assert inbox.claim(NEXT_TURN, 3) == []               # 空队列认领是幂等的


def test_inbox_splice_and_clear():
    """splice 是唯一的写原语（越界收敛），clear 两条队列一起清"""
    inbox = ReactLoopInbox()
    for t in ("t1", "t2"):
        inbox.append(NEXT_TURN, t)
    for s in ("s1", "s2", "s3"):
        inbox.append(NEXT_STEP, s)

    assert inbox.splice(NEXT_STEP, 1, 1, ["s9"]) == ["s2"]        # 标准 splice：删 1 插 1
    assert inbox.splice(NEXT_STEP, 0, 0, []) == []
    assert inbox.splice(NEXT_STEP, 99, 1, []) == []               # 越界不报错，按实际长度收敛

    inbox.clear()
    assert not inbox.has_pending


def test_three_step_protocol_is_the_entry():
    """投递 → 等收敛 → 取答案：三段式就是唯一入口（没有 run() 那种一次性包装）"""
    _install([_answer("事件驱动的答案")])
    loop, session = _loop()

    async def scenario():
        loop.followup("问题")            # 投递是同步的：不阻塞、不用 await
        await loop.when_idle()           # 等待是显式的
        return loop.last_assistant_text()

    assert asyncio.run(scenario()) == "事件驱动的答案"
    assert _stop_reason(session) == "completed"
    _assert_closed(session)
    _assert_messages_legal(session)


def test_inject_does_not_wake_but_is_delivered_on_the_next_wake():
    """inject 不唤醒：空闲时投它什么都不会发生；下一次唤醒（run / followup）才被认领"""
    http = _install([_answer("答案")])
    loop, session = _loop()

    loop.inject("系统侧上下文")
    assert session.log == []             # 没有唤醒 → 不开轮、不落任何事件

    assert _run(loop) == "答案"

    # next-step 认领在 next-turn **之前**（照搬 A）：注入的内容排在问句前面
    assert http.requests[0]["messages"] == [
        {"role": "user", "content": "系统侧上下文"},
        {"role": "user", "content": "问题"},
    ]


def test_steer_is_claimed_at_the_next_step():
    """跑工具期间 steer 一条：当前轮的第 2 步就带着它（不打断本轮）"""
    first = _answer("", finish_reason="tool_calls", tool_calls=[
        {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": "{}"}},
    ])
    http = _install([first, _answer("按新方向做完了")])
    loop, session = _loop(tool_schemas=[_TOOL_SCHEMA])
    _with_fake_tools(loop, session, runtime=_FakeRuntime(on_call=lambda: loop.steer("换个方向")))

    assert _run(loop) == "按新方向做完了"

    assert [e.data["content"] for e in session.log if e.type == USER_MESSAGE] == ["问题", "换个方向"]
    assert [e.data["step"] for e in session.log if e.type == STEP_START] == [1, 2]
    assert {"role": "user", "content": "换个方向"} in http.requests[1]["messages"]
    _assert_closed(session)
    _assert_messages_legal(session)


def test_followup_runs_as_a_second_turn():
    """跑工具期间 followup 一条：本轮收敛后作为**新的一轮**开始（轮次号与步号都重新计）"""
    first = _answer("", finish_reason="tool_calls", tool_calls=[
        {"id": "c1", "type": "function", "function": {"name": "echo", "arguments": "{}"}},
    ])
    _install([first, _answer("第一轮答案"), _answer("第二轮答案")])
    loop, session = _loop(tool_schemas=[_TOOL_SCHEMA])
    _with_fake_tools(loop, session, runtime=_FakeRuntime(on_call=lambda: loop.followup("顺带再做一件事")))

    assert _run(loop) == "第二轮答案"     # run() 取的是**最后一条** assistant 正文

    assert [e.data["turn"] for e in session.log if e.type == TURN_START] == [1, 2]
    assert [e.data["stop_reason"] for e in session.log if e.type == TURN_END] == ["completed", "completed"]
    assert [e.data["step"] for e in session.log if e.type == STEP_START] == [1, 2, 1]
    assert [e.data["content"] for e in session.log if e.type == USER_MESSAGE] == ["问题", "顺带再做一件事"]
    _assert_closed(session)
    _assert_messages_legal(session)


def test_steer_after_cancel_runs_as_the_next_turn():
    """取消生效后投的 steer：本轮兑现不了它 → 落到 next-turn，成为下一轮的第一条输入"""
    _install([
        _sse(_text_chunk("半截回答"), _Pause(5.0)),
        _sse(_text_chunk("按 steer 走的答案"), finish_reason="stop"),
    ])
    events, sink = _collect_events()
    loop, session = _loop(on_event=sink)

    async def scenario():
        loop.followup("问题")
        await asyncio.sleep(0.05)
        loop.cancel("用户点了停止")
        loop.steer("其实是新的问题")       # 本轮已 abort：并入不了，转成下一轮
        await loop.when_idle()

    asyncio.run(scenario())

    assert [e.data["stop_reason"] for e in session.log if e.type == TURN_END] == ["cancelled", "completed"]
    assert [e.data["content"] for e in session.log if e.type == USER_MESSAGE] == ["问题", "其实是新的问题"]
    _assert_closed(session)
    _assert_messages_legal(session)


def test_cancel_clears_inbox_and_stops_the_turn():
    """cancel()：清掉还没投递的输入 + 置 abort 信号 —— 本轮正常收口成 cancelled"""
    _install([_sse(_text_chunk("半截回答"), _Pause(5.0))])
    events, sink = _collect_events()
    loop, session = _loop(on_event=sink)

    async def scenario():
        loop.followup("问题")
        loop.followup("还没投递的下一个问题")     # 排在队列里，会被 cancel 清掉
        await asyncio.sleep(0.05)
        loop.cancel("用户点了停止")
        await loop.when_idle()

    asyncio.run(scenario())

    assert _stop_reason(session) == "cancelled"      # 有且仅有一轮
    _assert_closed(session)
    # 被清掉的那条从没进过 LLM 上下文（它不是事实，也没有人承诺过「已投递」）
    assert [e.data["content"] for e in session.log if e.type == USER_MESSAGE] == ["问题"]
    assert loop.last_assistant_text() == "半截回答"   # 半截正文照常落盘


def test_cancelled_turn_does_not_reuse_the_previous_turn_answer():
    """多轮会话：被取消的一轮没有半截正文 → 答案必须是停止文案，而不是上一轮的回答"""
    _install([
        _sse(_text_chunk("第一轮的答案"), finish_reason="stop"),
        _sse(_Pause(5.0)),
    ])
    events, sink = _collect_events()
    loop, session = _loop(on_event=sink)

    assert _run(loop, "第一个问题") == "第一轮的答案"

    cancel = asyncio.Event()

    async def scenario():
        async def _fire():
            await asyncio.sleep(0.05)
            cancel.set()

        answer, _ = await asyncio.gather(_drive(loop, "第二个问题", cancel), _fire())
        return answer

    assert asyncio.run(scenario()) == CANCELLED_REPLY

    assert [e.data["stop_reason"] for e in session.log if e.type == TURN_END] == ["completed", "cancelled"]
    # 第二轮确实什么都没落下（上一轮的正文还在，但它不属于本轮）
    assert [e.data["content"] for e in session.log if e.type == ASSISTANT_MESSAGE] == ["第一轮的答案"]
    _assert_closed(session)


# ── LLM 出口：流式累积器 + usage 归一 ──


def test_assistant_stream_merges_fragments_and_records_chunk_events():
    """累积器：分片按 index 拼成完整 message；每个增量**立刻**落成 assistant/chunk 事实"""
    session = Session()
    stream = AssistantStream(session)
    for chunk in (
        StreamChunk(kind=CHUNK_THINKING, delta="想"),
        StreamChunk(kind=CHUNK_TEXT, delta="先查"),
        StreamChunk(kind=CHUNK_TOOL_CALLS, tool_calls=(
            {"index": 0, "id": "c1", "name": "echo", "arguments": '{"q"'},
        )),
        StreamChunk(kind=CHUNK_TOOL_CALLS, tool_calls=(
            {"index": 0, "id": "", "name": "", "arguments": ': "hi"}'},   # 后续帧只有参数增量
        )),
        StreamChunk(kind=CHUNK_FINISH, finish_reason="tool_calls"),
    ):
        stream.push(chunk)

    # 拼出来的就是可落库的 assistant message（id/name 不被后续空串抹掉，参数逐帧追加）
    assert stream.message() == {
        "role": "assistant",
        "content": "先查",
        "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "echo", "arguments": '{"q": "hi"}'},
        }],
    }
    assert stream.finish_reason == "tool_calls"
    # 推理过程只广播、不混入正文
    assert [(e.data["kind"], e.data["delta"]) for e in session.log] == [("thinking", "想"), ("text", "先查")]


def test_usage_event_carries_canonical_token_fields():
    """usage 落库用 canonical 名（cache_hit_tokens），provider 名在适配器里就翻完了"""
    lines = [
        f"data: {json.dumps(_text_chunk('好'))}\n".encode("utf-8"),
        f"data: {json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}]})}\n".encode("utf-8"),
        f"data: {json.dumps({'choices': [], 'usage': {'prompt_tokens': 9, 'completion_tokens': 3, 'prompt_cache_hit_tokens': 7, 'prompt_cache_miss_tokens': 2}})}\n".encode("utf-8"),
        b"data: [DONE]\n",
    ]
    _install([_Resp(lines=lines)])
    events, sink = _collect_events()
    loop, session = _loop(on_event=sink)

    assert _run(loop) == "好"

    assert [e.data for e in session.log if e.type == "llm/usage"] == [{
        "step": 1,
        "prompt_tokens": 9,
        "completion_tokens": 3,
        "cache_hit_tokens": 7,
        "cache_miss_tokens": 2,
    }]


# ── 运行器 ──


def main() -> int:
    # 自检不做真实等待：退避压到 0（retry_delay 的边界本身由用例单独钉）
    retry_mod.LLM_RETRY_INITIAL_DELAY = 0.0
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")]
    failed = 0
    for name, fn in tests:
        print(f"  {name} ...", end="", flush=True)
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"\r  ✗ {name}: {type(e).__name__}: {e}")
        else:
            print(f"\r  ✓ {name}")
    print()
    if failed:
        print(f"{failed}/{len(tests)} 失败 ✗")
        return 1
    print(f"全部通过 ✓ ({len(tests)} 项)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
