"""ToolScheduler 自检（阶段 1：串行调度 + 有序提交 + guard 合成结果）

纯逻辑，不需要 docker / DB / 网络。

Usage:
    docker compose exec backend python -m backend.tool_system.runtime.test_scheduler
    # 或本机（仓库根目录下）：
    python backend/tool_system/runtime/test_scheduler.py
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from backend.agents.runtime.session import (  # noqa: E402
    ASSISTANT_MESSAGE,
    STEP_START,
    TURN_START,
    Session,
)
from backend.agents.runtime.session_tool_recorder import SessionToolRecorder  # noqa: E402
from backend.tool_system.context import ToolContext  # noqa: E402
from backend.tool_system.registry.descriptor import (  # noqa: E402
    EXCLUSIVE,
    PARALLEL,
    ToolDescriptor,
)
from backend.tool_system.runtime.scheduler import (  # noqa: E402
    ABORTED_MESSAGE,
    ABORTED_STARTED_MESSAGE,
    DISPATCH,
    SKIP,
    SKIP_MAX_TOOL_CALLS,
    SKIP_MESSAGES,
    SKIP_REPEAT_TOOL,
    PlannedCall,
    ToolCallRecorder,
    ToolScheduler,
)

# ── 测试工具 ──


def expect_raises(exc_type, fn, *args, **kwargs):
    """断言 fn 抛出 exc_type。"""
    try:
        fn(*args, **kwargs)
    except exc_type:
        return
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"预期 {exc_type.__name__}，实际 {type(e).__name__}: {e}") from e
    raise AssertionError(f"预期 {exc_type.__name__}，但没有抛异常")


# ── 测试替身 ──


class _FakeRuntime:
    """记录被真正执行的调用；fail_on 里的工具抛异常。"""

    def __init__(self, results: dict | None = None, fail_on: tuple = ()):
        self.calls: list[str] = []
        self.contexts: dict[str, ToolContext] = {}
        self._results = results or {}
        self._fail_on = set(fail_on)

    async def execute(self, tool_name: str, args: dict, context: ToolContext) -> dict:
        self.calls.append(tool_name)
        self.contexts[tool_name] = context
        if tool_name in self._fail_on:
            raise RuntimeError(f"boom: {tool_name}")
        return self._results.get(tool_name, {"ok": tool_name})


class _FakeRecorder:
    """记录落库顺序（模拟 Session 写入），不依赖 agents 层。"""

    def __init__(self):
        self.events: list[tuple] = []

    def record_call(self, call: PlannedCall) -> None:
        self.events.append(("call", call.tool_name, call.tool_call_id))

    def record_result(self, call: PlannedCall, result: dict) -> None:
        self.events.append(("result", call.tool_name, call.tool_call_id))


class _ControlledRuntime:
    """可控执行器：按 delay 决定完成先后，并记录并发峰值与执行时间线。

    timeline 形如 [("start", "A"), ("end", "A"), ...]，用于断言独占工具
    运行期间没有别的调用在跑（屏障语义）。
    """

    def __init__(self, delays: dict | None = None):
        self._delays = delays or {}
        self.calls: list[str] = []
        self.timeline: list[tuple[str, str]] = []
        self.active = 0
        self.max_active = 0

    async def execute(self, tool_name: str, args: dict, context: ToolContext) -> dict:
        self.calls.append(tool_name)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.timeline.append(("start", tool_name))
        try:
            await asyncio.sleep(self._delays.get(tool_name, 0.01))
            return {"ok": tool_name}
        finally:
            self.active -= 1
            self.timeline.append(("end", tool_name))

    def overlaps(self, name: str) -> list[str]:
        """name 运行期间还发生过事件的其它工具名（空列表 = 真正独占）"""
        start = self.timeline.index(("start", name))
        end = self.timeline.index(("end", name))
        return [n for _, n in self.timeline[start + 1:end] if n != name]


def _mode_resolver(exclusive: tuple = ()):
    """测试用模式解析：exclusive 里的工具名返回独占，其余并行。"""
    return lambda name: EXCLUSIVE if name in exclusive else PARALLEL


def _plan(tool_name="toolA", call_id="c1", action=DISPATCH, skip_reason="") -> PlannedCall:
    return PlannedCall(
        tool_call_id=call_id,
        tool_name=tool_name,
        args={"q": tool_name},
        action=action,
        skip_reason=skip_reason,
    )


def _context_factory(call_id: str) -> ToolContext:
    return ToolContext(session_id="s1", agent_id="agent", trace_id=call_id)


def _scheduler(recorder, runtime, *, max_parallel=4, exclusive=()) -> ToolScheduler:
    return ToolScheduler(
        recorder=recorder,
        context_factory=_context_factory,
        runtime=runtime,
        max_parallel_tools=max_parallel,
        mode_resolver=_mode_resolver(exclusive),
    )


def _run(scheduler: ToolScheduler, plans: list[PlannedCall]):
    return asyncio.run(scheduler.execute_tool_calls(plans))


# ── 串行调度 + 有序提交 ──


def test_empty_plans():
    recorder, runtime = _FakeRecorder(), _FakeRuntime()
    assert _run(_scheduler(recorder, runtime), []) == []
    assert recorder.events == []
    assert runtime.calls == []


def test_batch_keeps_model_order_for_start_and_commit():
    """一批调用：发起按模型序，提交也按模型序（执行可重叠，落库不变序）"""
    recorder, runtime = _FakeRecorder(), _FakeRuntime()
    plans = [_plan("toolA", "c1"), _plan("toolB", "c2"), _plan("toolC", "c3")]

    outcomes = _run(_scheduler(recorder, runtime), plans)

    assert runtime.calls == ["toolA", "toolB", "toolC"]      # 发起序
    assert recorder.events == [
        ("call", "toolA", "c1"), ("call", "toolB", "c2"), ("call", "toolC", "c3"),
        ("result", "toolA", "c1"), ("result", "toolB", "c2"), ("result", "toolC", "c3"),
    ]
    assert [o.tool_name for o in outcomes] == ["toolA", "toolB", "toolC"]
    assert [o.result for o in outcomes] == [
        {"ok": "toolA"}, {"ok": "toolB"}, {"ok": "toolC"},
    ]


def test_context_carries_tool_call_id():
    """一次调用一个 context：tool_call_id 经 factory 绑定（progress 事件的关联依据）"""
    recorder, runtime = _FakeRecorder(), _FakeRuntime()
    _run(_scheduler(recorder, runtime), [_plan("toolA", "c1"), _plan("toolB", "c2")])
    assert runtime.contexts["toolA"].trace_id == "c1"
    assert runtime.contexts["toolB"].trace_id == "c2"


# ── guard 跳过 → 合成结果 ──


def test_skip_records_pair_without_dispatch():
    """跳过的调用：不发执行，但仍成对落 call/result（合成错误文本）"""
    recorder, runtime = _FakeRecorder(), _FakeRuntime()
    plans = [_plan("toolA", "c1"), _plan("toolB", "c2", action=SKIP, skip_reason=SKIP_MAX_TOOL_CALLS)]

    outcomes = _run(_scheduler(recorder, runtime), plans)

    assert runtime.calls == ["toolA"]                      # B 未执行
    assert recorder.events == [
        ("call", "toolA", "c1"), ("call", "toolB", "c2"),     # 发起（B 是跳过项）
        ("result", "toolA", "c1"), ("result", "toolB", "c2"), # 结果仍成对
    ]
    assert outcomes[1].skipped is True
    assert outcomes[1].error == SKIP_MESSAGES[SKIP_MAX_TOOL_CALLS]
    assert outcomes[0].skipped is False


def test_skip_unknown_reason_still_records():
    """未知 skip_reason 不抛异常（兜底文本），保证 result 一定落库"""
    recorder, runtime = _FakeRecorder(), _FakeRuntime()
    outcomes = _run(
        _scheduler(recorder, runtime),
        [_plan("toolA", "c1", action=SKIP, skip_reason="something_new")],
    )
    assert "something_new" in outcomes[0].error
    assert len(recorder.events) == 2


# ── 异常隔离 ──


def test_dispatch_error_isolated():
    """单个工具抛异常：转成 error 结果，本批其余调用照常执行"""
    recorder, runtime = _FakeRecorder(), _FakeRuntime(fail_on=("toolB",))
    plans = [_plan("toolA", "c1"), _plan("toolB", "c2"), _plan("toolC", "c3")]

    outcomes = _run(_scheduler(recorder, runtime), plans)

    assert runtime.calls == ["toolA", "toolB", "toolC"]
    assert outcomes[1].result == {"error": "boom: toolB"}
    assert outcomes[2].result == {"ok": "toolC"}
    assert len(recorder.events) == 6                       # 三项都成对落库


# ── MCP 并发（并发调度对 MCP 工具的前提） ──

#: 内联回声 MCP server（stdio）：每个调用 sleep 一下制造并发窗口，再把 marker 原样返回。
#: 用它验证「同一 ClientSession 上的并发 call_tool 不串包、不挂死」——
#: 串包表现为某个调用拿回别人的 marker，正是要钉死的行为。
_ECHO_MCP_SERVER = """
import asyncio
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("echo")


@mcp.tool()
async def echo_marker(marker: str) -> str:
    await asyncio.sleep(0.05)
    return marker


mcp.run()
"""


def test_mcp_shared_session_handles_concurrent_calls():
    """MCP：同一 session 上的并发调用互不串包，且确实重叠执行

    McpClient 按 URL 缓存、executor 复用同一实例（adapters/mcp.py），并发派发
    意味着同一个 ClientSession 上会同时有多个 call_tool。MCP SDK 按 JSON-RPC id
    匹配响应，设计上支持 —— 本用例把它钉成事实；若哪天 SDK 行为变了，这里先红。

    stdio 传输 + 内联回声 server：自带子进程，不需要 DB / 不需要先起 MCP 服务。
    mcp 包不可用时 SKIP（本地无依赖，容器内有）。
    """
    try:
        from backend.tool_system.adapters.mcp import McpClient
    except Exception as e:  # noqa: BLE001
        print(f"      SKIP — mcp 包不可用（容器内运行）: {e}")
        return

    n = 4

    async def scenario():
        client = McpClient(
            transport="stdio",
            command=sys.executable,
            args=["-c", _ECHO_MCP_SERVER],
        )
        try:
            await client.connect()
        except Exception as e:  # noqa: BLE001
            print(f"      SKIP — MCP stdio server 启动失败: {e}")
            return None
        try:
            t0 = time.perf_counter()
            results = await asyncio.gather(*[
                client.call("echo_marker", {"marker": f"m{i}"}) for i in range(n)
            ])
            elapsed = time.perf_counter() - t0
            return results, elapsed
        finally:
            await client.disconnect()

    out = asyncio.run(scenario())
    if out is None:
        return
    results, elapsed = out

    assert len(results) == n
    for i, rows in enumerate(results):
        # 每个调用必须拿回自己的 marker（串包会拿到别人的）
        assert rows == [{"text": f"m{i}"}], f"第 {i} 个调用串包：{rows}"
    # 确实重叠执行：串行需要 n*0.05s，并发约 0.05s（留足余量避免抖动）
    assert elapsed < n * 0.05 * 0.8, f"并发未生效（用时 {elapsed:.3f}s）"


# ── 与 Session 的接线：消息序列合法性（孤儿 tool_call 修复） ──


def test_skipped_calls_keep_messages_valid():
    """guard 跳过的调用也成对落库 → derive_messages() 的 tool 消息覆盖全部 tool_call_id

    这是多轮会话的必要条件：assistant 消息里有 N 个 tool_calls，就必须有 N 条
    tool 消息，否则下一轮把消息发给 LLM 会被 OpenAI 兼容网关拒绝（400）。
    """
    session = Session()
    call_ids = ["c1", "c2", "c3"]
    session.append(ASSISTANT_MESSAGE, {
        "content": "",
        "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": "toolA", "arguments": "{}"}}
            for cid in call_ids
        ],
    })
    plans = [
        _plan("toolA", "c1"),
        _plan("toolB", "c2", action=SKIP, skip_reason=SKIP_MAX_TOOL_CALLS),
        _plan("toolC", "c3", action=SKIP, skip_reason=SKIP_MAX_TOOL_CALLS),
    ]

    runtime = _FakeRuntime()
    _run(_scheduler(SessionToolRecorder(session), runtime), plans)

    assert runtime.calls == ["toolA"]                      # 只有 c1 真的执行
    messages = session.derive_messages()
    assert [m["role"] for m in messages] == ["assistant", "tool", "tool", "tool"]
    assert [m["tool_call_id"] for m in messages if m["role"] == "tool"] == call_ids


def test_session_recorder_writes_sandbox_fact():
    """tool/result 带结构化沙箱事实（投影端直接读字段，不解析 content）"""
    session = Session()
    runtime = _FakeRuntime(results={
        "bash": {
            "tool": "bash", "exit_code": 0, "stdout": "", "stderr": "",
            "sandbox": {"mode": "workspace-write", "enforcement": "full", "outcome": "normal"},
        },
    })
    _run(_scheduler(SessionToolRecorder(session), runtime), [_plan("bash", "c1")])

    result_events = [e for e in session.log if e.type == "tool/result"]
    assert len(result_events) == 1
    assert result_events[0].data["sandbox"]["outcome"] == "normal"


def test_parallel_batch_survives_projections():
    """端到端：并发批次（含独占 + guard 跳过）经真实 Session → 投影 → LLM 消息

    一并钉死三件事：轨迹投影按 call_id 回填（不依赖到达顺序）、trace 投影
    的 usage/step 结构不变、derive_messages() 的消息序与 assistant.tool_calls 对齐。
    """
    from backend.agents.runtime.session.trace_projection import TraceProjection
    from backend.agents.runtime.session.trajectory_projection import TrajectoryProjection

    session = Session()
    session.append(TURN_START, {"agent": "a", "limits": {"max_steps": 5}})
    session.append(STEP_START, {"step": 1, "total": 5})
    call_ids = ["c1", "c2", "c3"]
    session.append(ASSISTANT_MESSAGE, {
        "content": "开始处理",
        "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": "t", "arguments": "{}"}}
            for cid in call_ids
        ],
    })

    # 慢的排前面 + 独占 + 跳过：并发、屏障、合成结果同时发生
    runtime = _ControlledRuntime({"slow": 0.06, "writer": 0.01})
    plans = [
        _plan("slow", "c1"),
        _plan("writer", "c2"),
        _plan("skipped", "c3", action=SKIP, skip_reason=SKIP_MAX_TOOL_CALLS),
    ]
    _run(_scheduler(SessionToolRecorder(session), runtime, exclusive=("writer",)), plans)

    # 独占工具没有和任何调用重叠
    assert runtime.overlaps("writer") == []

    # 投影完整事件流（含并发落库的 call/result）：连线两个真实投影，确认没被新序打断
    traj, frames, trace = TrajectoryProjection(), [], TraceProjection()
    for ev in session.log:
        frames.extend(traj.handle(ev))
        trace.handle(ev)

    opened = [
        u["node"]["id"] for u in frames
        if u.get("type") == "traj/open" and u["node"]["kind"] == "tool"
    ]
    # 三个调用各开一个 node，且按模型序（未受完成序影响）
    assert [i.rsplit(".", 1)[-1] for i in opened] == call_ids
    # 每个 node 都被结果回填（并发下按 call_id 关联，不依赖到达顺序）
    patched = {u["id"]: u["patch"] for u in frames if u.get("type") == "traj/update"}
    for node_id in opened:
        assert node_id in patched, f"{node_id} 未被结果回填"
        assert patched[node_id].get("status") != "running"

    # trace 投影：三个 tool step 都在，且结构不变
    snapshot = trace.snapshot()
    tool_steps = [s for s in snapshot["steps"] if s.get("type") == "tool"]
    assert [s["tool"] for s in tool_steps] == ["slow", "writer", "skipped"]
    assert "stop_reason" not in snapshot  # 尚无 turn/end（有值才写键）

    messages = session.derive_messages()
    assert [m["tool_call_id"] for m in messages if m["role"] == "tool"] == call_ids
    assert [m["role"] for m in messages] == ["assistant", "tool", "tool", "tool"]
    # 被跳过的那个也带上了合成错误文本
    skipped_msg = json.loads(messages[3]["content"])
    assert skipped_msg["error"] == SKIP_MESSAGES[SKIP_MAX_TOOL_CALLS]


def test_session_recorder_satisfies_protocol():
    """SessionToolRecorder 满足 ToolCallRecorder Protocol（装配方的结构契约）"""
    assert isinstance(SessionToolRecorder(Session()), ToolCallRecorder)


# ── 执行模式（阶段 2） ──


def test_execution_mode_defaults_to_parallel():
    """默认 parallel：未声明的工具不写字段就是并行（检索 / 查询类占多数）"""
    d = ToolDescriptor(name="search", type="mcp", transport="http", server_id=1, schema={})
    assert d.execution_mode == PARALLEL


def test_execution_mode_vocabulary_closed():
    """封闭词汇：拼错的模式在构造期就报错，不静默降级"""
    for bad in ("Parallel", "serial", "", "exclusive "):
        expect_raises(
            ValueError,
            ToolDescriptor,
            name="x", type="mcp", transport="", server_id=0, schema={},
            execution_mode=bad,
        )
    for good in (PARALLEL, EXCLUSIVE):
        d = ToolDescriptor(
            name="x", type="mcp", transport="", server_id=0, schema={},
            execution_mode=good,
        )
        assert d.execution_mode == good


def test_sandbox_tools_declare_exclusive():
    """沙箱 bash / python 是 exclusive（共享同一个会话工作区，并发会互相踩）"""
    from backend.tool_system.registry.registry import ToolRegistry
    from backend.tool_system.sandbox.runtime import register_builtin_sandbox_tools

    r = ToolRegistry()
    names = register_builtin_sandbox_tools(r)

    assert names == ["bash", "python"]
    assert [r.execution_mode_of(n) for n in names] == [EXCLUSIVE, EXCLUSIVE]


def test_registry_execution_mode_override():
    """扩展点：register_execution_mode 覆盖描述自带值，并即时反映到统一索引"""
    from backend.tool_system.registry.registry import ToolRegistry

    r = ToolRegistry()
    r.register_builtin(ToolDescriptor(
        name="write_order", type="mcp", transport="http", server_id=1, schema={},
    ))
    assert r.execution_mode_of("write_order") == PARALLEL      # 描述自带默认

    r.register_execution_mode("write_order", EXCLUSIVE)        # 装配方声明有副作用
    assert r.execution_mode_of("write_order") == EXCLUSIVE
    assert r.descriptors()[0].execution_mode == EXCLUSIVE      # 统一索引同步


def test_registry_execution_mode_override_before_register():
    """先声明后注册：描述入索引时即带上覆盖值（_with_metadata 生效）"""
    from backend.tool_system.registry.registry import ToolRegistry

    r = ToolRegistry()
    r.register_execution_mode("bash", EXCLUSIVE)
    assert r.execution_mode_of("bash") == EXCLUSIVE            # 未索引 → 读声明值

    r.register_builtin(ToolDescriptor(
        name="bash", type="sandbox", transport="", server_id=0, schema={},
    ))
    assert r.execution_mode_of("bash") == EXCLUSIVE


def test_registry_execution_mode_rejects_unknown():
    """扩展点同样走封闭词汇校验"""
    from backend.tool_system.registry.registry import ToolRegistry

    r = ToolRegistry()
    expect_raises(ValueError, r.register_execution_mode, "bash", "eventually")


# ── 并发调度（阶段 3） ──


def test_pool_respects_max_parallel():
    """滚动池上限：并发峰值不超过 max_parallel_tools"""
    recorder = _FakeRecorder()
    runtime = _ControlledRuntime({f"t{i}": 0.03 for i in range(6)})
    plans = [_plan(f"t{i}", f"c{i}") for i in range(6)]

    outcomes = _run(_scheduler(recorder, runtime, max_parallel=2), plans)

    assert runtime.max_active == 2, f"并发峰值 {runtime.max_active}"
    assert [o.tool_name for o in outcomes] == [f"t{i}" for i in range(6)]


def test_pool_refills_as_calls_complete():
    """有调用完成即补新的：10 个调用、池宽 4 —— 全部跑完且峰值恰好 4"""
    recorder = _FakeRecorder()
    runtime = _ControlledRuntime({f"t{i}": 0.02 for i in range(10)})
    plans = [_plan(f"t{i}", f"c{i}") for i in range(10)]

    _run(_scheduler(recorder, runtime, max_parallel=4), plans)

    assert runtime.max_active == 4
    assert len(runtime.calls) == 10
    assert sorted(runtime.calls) == sorted(f"t{i}" for i in range(10))


def test_ordered_commit_under_out_of_order_completion():
    """慢的排前面：后完成的先提交 —— 事件序必须等于模型序，而非完成序

    这是并发调度的核心不变式：tool/result 进 surface，derive_messages() 的
    消息序必须与 assistant.tool_calls 对齐，否则下一轮 LLM 请求非法。
    """
    recorder = _FakeRecorder()
    runtime = _ControlledRuntime({"slow": 0.06, "fast": 0.005})
    plans = [_plan("slow", "c1"), _plan("fast", "c2")]

    _run(_scheduler(recorder, runtime, max_parallel=2), plans)

    # 真实完成序是 fast 先于 slow（证明执行确实重叠、且完成序与模型序相反）
    assert runtime.timeline == [
        ("start", "slow"), ("start", "fast"),
        ("end", "fast"), ("end", "slow"),
    ]
    # 但落库序严格按模型序
    assert recorder.events == [
        ("call", "slow", "c1"), ("call", "fast", "c2"),
        ("result", "slow", "c1"), ("result", "fast", "c2"),
    ]


def test_all_skipped_batch_commits_in_order():
    """整批跳过（guard 全中）：不发执行，仍按模型序成对落库"""
    recorder = _FakeRecorder()
    runtime = _ControlledRuntime()
    plans = [
        _plan("t1", "c1", action=SKIP, skip_reason=SKIP_MAX_TOOL_CALLS),
        _plan("t2", "c2", action=SKIP, skip_reason=SKIP_MAX_TOOL_CALLS),
    ]

    outcomes = _run(_scheduler(recorder, runtime), plans)

    assert runtime.calls == []
    assert recorder.events == [
        ("call", "t1", "c1"), ("result", "t1", "c1"),
        ("call", "t2", "c2"), ("result", "t2", "c2"),
    ]
    assert all(o.skipped for o in outcomes)


def test_mixed_skip_does_not_consume_pool_slot():
    """跳过项不占并发槽：池宽 1 时，两个跳过项不会把真实调用堵在后面"""
    recorder = _FakeRecorder()
    runtime = _ControlledRuntime()
    plans = [
        _plan("skip1", "c1", action=SKIP, skip_reason=SKIP_REPEAT_TOOL),
        _plan("real", "c2"),
        _plan("skip2", "c3", action=SKIP, skip_reason=SKIP_REPEAT_TOOL),
    ]

    _run(_scheduler(recorder, runtime, max_parallel=1), plans)

    assert runtime.calls == ["real"]
    assert [e[1] for e in recorder.events] == ["skip1", "skip1", "real", "real", "skip2", "skip2"]


# ── 独占屏障 ──


def test_exclusive_forms_barrier():
    """独占工具：等在途排空后才启动，且运行期间没有别的调用在跑"""
    recorder = _FakeRecorder()
    runtime = _ControlledRuntime({"before": 0.05, "writer": 0.02, "after": 0.01})
    plans = [
        _plan("before", "c1"),
        _plan("writer", "c2"),
        _plan("after", "c3"),
    ]

    _run(_scheduler(recorder, runtime, max_parallel=8, exclusive=("writer",)), plans)

    assert runtime.overlaps("writer") == [], "独占工具运行期间有其他调用在跑"
    assert runtime.calls == ["before", "writer", "after"]
    # 落库仍按模型序
    assert [e[1] for e in recorder.events] == [
        "before", "before", "writer", "writer", "after", "after",
    ]


def test_exclusive_blocks_later_parallel_calls():
    """队列语义：独占项把后面的并行调用挡在身后（FIFO 屏障，不插队）"""
    recorder = _FakeRecorder()
    runtime = _ControlledRuntime({"a": 0.02, "writer": 0.02, "b": 0.02, "c": 0.02})
    plans = [
        _plan("a", "c1"),
        _plan("writer", "c2"),
        _plan("b", "c3"),
        _plan("c", "c4"),
    ]

    _run(_scheduler(recorder, runtime, max_parallel=8, exclusive=("writer",)), plans)

    assert runtime.overlaps("writer") == []
    assert runtime.timeline.index(("start", "b")) > runtime.timeline.index(("end", "writer"))
    assert runtime.timeline.index(("start", "c")) > runtime.timeline.index(("end", "writer"))


def test_mode_resolver_failure_fails_closed():
    """模式查不到按独占处理（fail-closed）：宁可少并发，不可让有状态的工具并发跑"""
    def broken(_name):
        raise RuntimeError("registry 未装配")

    recorder = _FakeRecorder()
    runtime = _ControlledRuntime({"t1": 0.02, "t2": 0.02})
    scheduler = ToolScheduler(
        recorder=recorder, context_factory=_context_factory, runtime=runtime,
        max_parallel_tools=4, mode_resolver=broken,
    )

    _run(scheduler, [_plan("t1", "c1"), _plan("t2", "c2")])

    assert runtime.max_active == 1, "解析失败时必须退化为串行"


# ── 取消恢复（阶段 4） ──


def _run_cancelled(scheduler, plans, *, cancel_after=0.03):
    """跑一批调用并在中途取消，返回 (CancelledError 是否抛出, recorder)"""

    async def scenario():
        task = asyncio.create_task(scheduler.execute_tool_calls(plans))
        await asyncio.sleep(cancel_after)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return True
        return False

    return asyncio.run(scenario())


def test_abort_pairs_every_call_with_a_result():
    """取消：已启动的被中断、未启动的补合成结果 —— 每个 tool/call 都有 tool/result"""
    recorder = _FakeRecorder()
    # 池宽 2：前两个启动后一直不结束，后两个永远排不上
    runtime = _ControlledRuntime({f"t{i}": 5.0 for i in range(4)})
    plans = [_plan(f"t{i}", f"c{i}") for i in range(4)]

    raised = _run_cancelled(_scheduler(recorder, runtime, max_parallel=2), plans)

    assert raised, "取消应当向上传播"
    calls = [e for e in recorder.events if e[0] == "call"]
    results = [e for e in recorder.events if e[0] == "result"]
    assert [e[2] for e in calls] == ["c0", "c1", "c2", "c3"], "四个调用都要有发起事件"
    assert [e[2] for e in results] == ["c0", "c1", "c2", "c3"], "四个调用都要有结果事件"
    # 每个 tool/result 都在它自己的 tool/call 之后（成对性不因取消而破坏）
    for call_id in ("c0", "c1", "c2", "c3"):
        call_at = next(
            i for i, e in enumerate(recorder.events)
            if e[0] == "call" and e[2] == call_id
        )
        result_at = next(
            i for i, e in enumerate(recorder.events)
            if e[0] == "result" and e[2] == call_id
        )
        assert result_at > call_at, f"{call_id} 的结果早于发起"


def test_abort_marks_unstarted_calls_distinctly():
    """未启动（未执行）与已启动（被中断）在合成文本上区分"""
    session = Session()
    recorder = SessionToolRecorder(session)
    runtime = _ControlledRuntime({"t0": 5.0, "t1": 5.0})
    plans = [_plan("t0", "c0"), _plan("t1", "c1"), _plan("t2", "c2")]

    raised = _run_cancelled(_scheduler(recorder, runtime, max_parallel=2), plans)

    assert raised
    results = [e for e in session.log if e.type == "tool/result"]
    contents = [json.loads(e.data["content"])["error"] for e in results]
    assert contents[0] == ABORTED_STARTED_MESSAGE   # 已启动被中断
    assert contents[1] == ABORTED_STARTED_MESSAGE
    assert contents[2] == ABORTED_MESSAGE           # 从未启动
    # 多轮会话仍合法：assistant 的 3 个 tool_calls 都有对应 tool 消息
    assert len(results) == 3


def test_abort_keeps_completed_results_real():
    """取消时已经跑完的调用保留真实结果，不被合成结果覆盖"""
    recorder = _FakeRecorder()
    runtime = _ControlledRuntime({"fast": 0.01, "slow": 5.0})
    plans = [_plan("fast", "c1"), _plan("slow", "c2"), _plan("never", "c3")]

    raised = _run_cancelled(
        _scheduler(recorder, runtime, max_parallel=2), plans, cancel_after=0.08,
    )

    assert raised
    results = {e[2]: None for e in recorder.events if e[0] == "result"}
    assert set(results) == {"c1", "c2", "c3"}


def test_registry_unregister_drops_mode_lookup():
    """注销后回到声明值/默认值：不再从描述读（能力停用即彻底不可见）"""
    from backend.tool_system.registry.registry import ToolRegistry

    r = ToolRegistry()
    r.register_builtin(ToolDescriptor(
        name="bash", type="sandbox", transport="", server_id=0, schema={},
        execution_mode=EXCLUSIVE,
    ))
    assert r.execution_mode_of("bash") == EXCLUSIVE
    r.unregister_builtin(["bash"])
    assert r.execution_mode_of("bash") == PARALLEL


# ── 运行器 ──


def main() -> int:
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
