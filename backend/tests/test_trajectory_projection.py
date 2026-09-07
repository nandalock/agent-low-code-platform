"""TrajectoryProjection 投影测试（纯内存，无 DB 依赖）

覆盖目标（Agent Trajectory UI 的 Conversation Projection 层）：
  A   think/tool 严格交替保序（think A → tool A → think B → tool B）
  B   多个 reasoning delta 只产生一个 think node（delta 累积不炸 node）
  C   tool/call + tool/result（call_id 关联）→ 同一 node running → success
  D   tool error → status=error
  E   text delta + assistant/message 对账 → 单个 answer node，无重复
  E′  非流式（无 chunk）assistant/message → 直接建立 answer node（全文）
  E″  流式段不一致 → assistant/message 全量替换（不丢跨步累积）
  F   高频 reasoning delta → 每 delta 一条事件（前端批处理是前端职责）
  多 tool 同 step、乱序 result 回填、guard 异常轮收口、usage 透传、
  旧数据兼容（step/start 无 total）、增量 == 快照回放、多 turn、Session listener

运行方式（backend 容器内）:
  python -m pytest backend/tests/test_trajectory_projection.py -v
"""
import uuid

from backend.agents.runtime.session.events import SessionEvent
from backend.agents.runtime.session.trajectory_projection import TrajectoryProjection, project_trajectory
from backend.agents.runtime.session.session import Session


class _Seq:
    """事件构造器：seq 自增模拟真实 append 顺序"""

    def __init__(self):
        self.n = 0

    def ev(self, type_: str, data: dict) -> SessionEvent:
        self.n += 1
        return SessionEvent(type=type_, seq=self.n, time=0.0, data=data)


def _run(events):
    """折叠 handle：返回 (全部 UI 事件, 终态快照)"""
    p = TrajectoryProjection()
    out: list[dict] = []
    for ev in events:
        out.extend(p.handle(ev))
    return out, p.finish()


def _turn_open(s: _Seq):
    return [s.ev("turn/start", {"agent": "test"})]


def _step(s: _Seq, n: int, total: int = 5):
    return [s.ev("step/start", {"step": n, "total": total})]


def _think(s: _Seq, step: int, *deltas: str, total: int = 5):
    """step + 若干 thinking delta + assistant/message（纯思考步）"""
    events = _step(s, step, total)
    for d in deltas:
        events.append(s.ev("assistant/chunk", {"kind": "thinking", "delta": d}))
    events.append(s.ev("assistant/message", {"content": "", "tool_calls": []}))
    return events


def _tool_roundtrip(s: _Seq, step: int, call_id: str, tool: str, result_content: str):
    """同一 step 内一次 tool 调用：call → result（均带 call_id）"""
    return [
        s.ev("tool/call", {"tool": tool, "args": {"q": "x"}, "tool_call_id": call_id}),
        s.ev("tool/result", {"tool": tool, "tool_call_id": call_id, "content": result_content}),
    ]


def _nodes(snap: dict) -> list[dict]:
    return snap["nodes"]


def _kinds(snap: dict) -> list[str]:
    return [n["kind"] for n in _nodes(snap)]


# ── A: think/tool 交替保序 ──

def test_think_tool_alternation_order():
    s = _Seq()
    events = _turn_open(s)
    events += _think(s, 1, "先查文件是否存在。", "需要确认路径。")
    events += _tool_roundtrip(s, 1, "c1", "pwsh", '{"rows": [{"ok": true}]}')
    events.append(s.ev("step/end", {"step": 1}))
    events += _think(s, 2, "文件存在，需要读内容。")
    events += _tool_roundtrip(s, 2, "c2", "read_file", '{"rows": [{"text": "..."}]}')
    events.append(s.ev("step/end", {"step": 2}))
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))

    out, snap = _run(events)
    # 严格执行序：Think A → Tool A → Think B → Tool B
    assert [n["id"] for n in _nodes(snap)] == [
        "t1.s1.think", "t1.s1.tool.c1", "t1.s2.think", "t1.s2.tool.c2",
    ]
    assert [n["kind"] for n in _nodes(snap)] == ["think", "tool", "think", "tool"]
    # 两个 think 文本各自独立，未被合并
    assert _nodes(snap)[0]["text"].startswith("先查文件")
    assert _nodes(snap)[2]["text"] == "文件存在，需要读内容。"


# ── B: 多 delta 单 think node ──

def test_three_deltas_single_think_node():
    s = _Seq()
    events = _turn_open(s) + _think(s, 1, "Need", " extract", " text.")
    events.append(s.ev("step/end", {"step": 1}))
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))

    out, snap = _run(events)
    thinks = [n for n in _nodes(snap) if n["kind"] == "think"]
    assert len(thinks) == 1
    assert thinks[0]["text"] == "Need extract text."
    assert thinks[0]["status"] == "done"
    # open 一次 + 每 delta 一条（前端据此批处理；无每 delta 建 node）
    opens = [e for e in out if e["type"] == "traj/open"]
    deltas = [e for e in out if e["type"] == "traj/delta"]
    assert len(opens) == 1 and opens[0]["node"]["id"] == thinks[0]["id"]
    assert len(deltas) == 3 and [e["delta"] for e in deltas] == ["Need", " extract", " text."]


# ── C: tool running → success ──

def test_tool_running_to_success():
    s = _Seq()
    events = _turn_open(s)
    events += _step(s, 1)
    events.append(s.ev("tool/call", {"tool": "search", "args": {"q": "订单"}, "tool_call_id": "abc"}))
    events.append(s.ev("tool/progress", {"tool": "search", "tool_call_id": "abc", "stage": "查询中", "seconds": 2}))
    events.append(s.ev("tool/progress", {"tool": "search", "tool_call_id": "abc", "stage": "解析中", "seconds": 5}))
    events.append(s.ev("tool/result", {"tool": "search", "tool_call_id": "abc", "content": '{"rows": [1, 2, 3]}'}))
    events.append(s.ev("step/end", {"step": 1}))
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))

    out, snap = _run(events)
    tools = [n for n in _nodes(snap) if n["kind"] == "tool"]
    assert len(tools) == 1
    assert tools[0]["id"] == "t1.s1.tool.abc"
    assert tools[0]["status"] == "success"
    assert tools[0]["summary"] == "3 条结果"
    assert tools[0]["error"] is None
    # 阶段进度跟随最后一条 tool/progress
    assert tools[0]["stage"] == "解析中" and tools[0]["seconds"] == 5
    # 更新事件携带全量值（前端 reducer 纯函数式）
    updates = [e for e in out if e["type"] == "traj/update"]
    assert updates[0]["patch"] == {"stage": "查询中", "seconds": 2}


# ── D: tool error ──

def test_tool_error_status():
    s = _Seq()
    events = _turn_open(s)
    events += _step(s, 1)
    events.append(s.ev("tool/call", {"tool": "exec", "args": {"cmd": "x"}, "tool_call_id": "abc"}))
    events.append(s.ev("tool/result", {"tool": "exec", "tool_call_id": "abc", "content": '{"error": "执行超时（>30s），已取消"}'}))
    events.append(s.ev("step/end", {"step": 1}))
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))

    _, snap = _run(events)
    tool = [n for n in _nodes(snap) if n["kind"] == "tool"][0]
    assert tool["kind"] == "tool" and tool["status"] == "error"
    assert "执行超时" in tool["error"]


def test_tool_result_truncated_at_cap():
    s = _Seq()
    events = _turn_open(s)
    events += _step(s, 1)
    events.append(s.ev("tool/call", {"tool": "big", "args": {}, "tool_call_id": "abc"}))
    events.append(s.ev("tool/result", {"tool": "big", "tool_call_id": "abc", "content": "x" * 9000}))
    events.append(s.ev("step/end", {"step": 1}))
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))

    _, snap = _run(events)
    tool = [n for n in _nodes(snap) if n["kind"] == "tool"][0]
    assert tool["status"] == "success"
    assert tool["result"] is not None and len(tool["result"]) <= 4000 + 10  # cap + 截断后缀
    assert tool["result"].endswith("…(已截断)")


# ── E: answer 无重复 ──

def test_answer_no_duplication():
    s = _Seq()
    events = _turn_open(s)
    events += _step(s, 1)
    for d in ["Hello", ", world", "!"]:
        events.append(s.ev("assistant/chunk", {"kind": "text", "delta": d}))
    events.append(s.ev("assistant/message", {"content": "Hello, world!", "tool_calls": []}))
    events.append(s.ev("step/end", {"step": 1}))
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))

    out, snap = _run(events)
    answers = [n for n in _nodes(snap) if n["kind"] == "answer"]
    assert len(answers) == 1
    assert answers[0]["text"] == "Hello, world!"
    assert answers[0]["status"] == "done"
    # assistant/message 与累积一致 → 无额外 update（不重复不回写）
    updates = [e for e in out if e["type"] == "traj/update"]
    assert not updates


# ── E′: 非流式 assistant/message 直接建 answer ──

def test_answer_reconcile_full_content_no_chunks():
    s = _Seq()
    events = _turn_open(s)
    events += _step(s, 1)
    events.append(s.ev("assistant/message", {"content": "直接全文", "tool_calls": []}))
    events.append(s.ev("step/end", {"step": 1}))
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))

    _, snap = _run(events)
    answers = [n for n in _nodes(snap) if n["kind"] == "answer"]
    assert len(answers) == 1 and answers[0]["text"] == "直接全文"


# ── E″: 段不一致 → 全量替换且不丢跨步累积 ──

def test_answer_reconcile_replaces_diverged_segment():
    s = _Seq()
    events = _turn_open(s)
    # step1: 流式段 Bxy vs message 权威 Bz → 替换本段，answer 保持单节点
    events += _step(s, 1)
    for d in ["Bx", "y"]:
        events.append(s.ev("assistant/chunk", {"kind": "text", "delta": d}))
    events.append(s.ev("assistant/message", {"content": "Bz", "tool_calls": []}))
    events.append(s.ev("step/end", {"step": 1}))
    # step2: 文本 C 累积 → 跨步不丢 B 段的最终值
    events += _step(s, 2)
    events.append(s.ev("assistant/chunk", {"kind": "text", "delta": "C"}))
    events.append(s.ev("assistant/message", {"content": "C", "tool_calls": []}))
    events.append(s.ev("step/end", {"step": 2}))
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))

    out, snap = _run(events)
    answers = [n for n in _nodes(snap) if n["kind"] == "answer"]
    assert len(answers) == 1
    assert answers[0]["text"] == "BzC"  # 替换发生在段内，跨步累积保留
    # 替换经 traj/update 全量文本 patch
    replaces = [e for e in out if e["type"] == "traj/update"]
    assert any(e["patch"] == {"text": "Bz"} for e in replaces)


# ── F: 高频 delta（后端逐条出事件，前端才批处理） ──

def test_high_frequency_deltas():
    s = _Seq()
    events = _turn_open(s) + _think(s, 1, *[f"t{i}" for i in range(200)])
    events.append(s.ev("step/end", {"step": 1}))
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))

    out, snap = _run(events)
    deltas = [e for e in out if e["type"] == "traj/delta"]
    assert len(deltas) == 200
    thinks = [n for n in _nodes(snap) if n["kind"] == "think"]
    assert len(thinks) == 1  # 200 delta 仍只有 1 个 think node
    assert thinks[0]["text"] == "".join(f"t{i}" for i in range(200))


# ── 多 tool 同 step + result 乱序回填 ──

def test_multi_tool_same_step_out_of_order_results():
    s = _Seq()
    events = _turn_open(s)
    events += _step(s, 1)
    events.append(s.ev("tool/call", {"tool": "t1", "args": {}, "tool_call_id": "x"}))
    events.append(s.ev("tool/call", {"tool": "t2", "args": {}, "tool_call_id": "y"}))
    # result 乱序：y 先回、x 后回
    events.append(s.ev("tool/result", {"tool": "t2", "tool_call_id": "y", "content": '{"rows": [9]}'}))
    events.append(s.ev("tool/result", {"tool": "t1", "tool_call_id": "x", "content": '{"rows": [1, 2]}'}))
    events.append(s.ev("step/end", {"step": 1}))
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))

    _, snap = _run(events)
    tools = [n for n in _nodes(snap) if n["kind"] == "tool"]
    assert len(tools) == 2
    assert [t["call_id"] for t in tools] == ["x", "y"]  # call 顺序（执行序）
    assert tools[0]["status"] == "success" and tools[0]["summary"] == "2 条结果"
    assert tools[1]["status"] == "success" and tools[1]["summary"] == "1 条结果"


# ── 零工具轮 ──

def test_turn_zero_tools():
    s = _Seq()
    events = _turn_open(s)
    events += _step(s, 1)
    events.append(s.ev("assistant/chunk", {"kind": "text", "delta": "直接回答"}))
    events.append(s.ev("assistant/message", {"content": "直接回答", "tool_calls": []}))
    events.append(s.ev("step/end", {"step": 1}))
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))

    _, snap = _run(events)
    assert _kinds(snap) == ["answer"]


# ── guard 异常轮：turn/end 兜底关闭 open node ──

def test_guard_turn_end_closes_open_nodes():
    s = _Seq()
    events = _turn_open(s)
    events += _step(s, 1)
    events.append(s.ev("assistant/chunk", {"kind": "thinking", "delta": "思考中被打断"}))
    # 无 assistant/message / 无 step/end（异常中断）
    events.append(s.ev("turn/end", {"stop_reason": "max_wall_time"}))

    out, snap = _run(events)
    thinks = [n for n in _nodes(snap) if n["kind"] == "think"]
    assert len(thinks) == 1 and thinks[0]["status"] == "done"  # 兜底关闭
    closes = [e for e in out if e["type"] == "traj/close"]
    assert len(closes) == 1


def test_turn_end_cancels_running_tool():
    s = _Seq()
    events = _turn_open(s)
    events += _step(s, 1)
    events.append(s.ev("tool/call", {"tool": "long", "args": {}, "tool_call_id": "abc"}))
    # 无 tool/result（中断）
    events.append(s.ev("turn/end", {"stop_reason": "error"}))

    out, snap = _run(events)
    tool = [n for n in _nodes(snap) if n["kind"] == "tool"][0]
    assert tool["status"] == "cancelled"
    assert any(e["type"] == "traj/update" and e["patch"] == {"status": "cancelled"} for e in out)


# ── usage 透传 ──

def test_usage_passthrough():
    s = _Seq()
    events = _turn_open(s)
    events += _step(s, 1)
    events.append(s.ev("llm/usage", {
        "step": 1, "prompt_tokens": 912, "completion_tokens": 340,
        "prompt_cache_hit_tokens": 580, "prompt_cache_miss_tokens": 332,
    }))
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))

    out, snap = _run(events)
    usage_evs = [e for e in out if e["type"] == "usage"]
    assert len(usage_evs) == 1
    assert usage_evs[0]["prompt_cache_hit_tokens"] == 580
    assert snap["usage"][0]["prompt_tokens"] == 912


# ── 旧数据兼容：step/start 无 total ──

def test_old_step_data_without_total():
    s = _Seq()
    p = TrajectoryProjection()
    p.handle(s.ev("turn/start", {}))
    p.handle(s.ev("step/start", {"step": 1}))  # 旧日志无 total
    p.handle(s.ev("assistant/chunk", {"kind": "thinking", "delta": "x"}))
    p.handle(s.ev("step/end", {"step": 1}))
    p.handle(s.ev("turn/end", {}))
    # step UI 事件无 total 键；不抛异常
    p2 = TrajectoryProjection()
    step_evs = [e for e in (p2.handle(e) for e in [
        s.ev("turn/start", {}), s.ev("step/start", {"step": 1}),
    ])]
    assert step_evs[1] == [{"type": "step", "step": 1}]


# ── 增量 == 快照回放（确定性） ──

def test_replay_determinism():
    s = _Seq()
    events = _turn_open(s)
    for step in (1, 2):
        # 工具步（真实时序：thinking → assistant/message → tool 执行 → step/end）
        events += _think(s, step, *[f"s{step}-{i}" for i in range(3)])
        for cid in (f"c{step}a", f"c{step}b"):
            events += _tool_roundtrip(s, step, cid, "tool", f'{{"rows": [1], "ok": true}}')
        events.append(s.ev("step/end", {"step": step}))
        events.append(s.ev("llm/usage", {"step": step, "prompt_tokens": 10 * step, "completion_tokens": 2}))
    # 终步：thinking → text → message（无工具）
    events += _think(s, 3, "s3-0", "s3-1")
    events.append(s.ev("assistant/chunk", {"kind": "text", "delta": "最终"}))
    events.append(s.ev("assistant/chunk", {"kind": "text", "delta": "回答"}))
    events.append(s.ev("assistant/message", {"content": "最终回答", "tool_calls": []}))
    events.append(s.ev("step/end", {"step": 3}))
    events.append(s.ev("llm/usage", {"step": 3, "prompt_tokens": 30, "completion_tokens": 2}))
    events.append(s.ev("turn/end", {"stop_reason": "completed"}))

    out, incremental = _run(events)
    snapshot = project_trajectory(events)
    assert incremental["nodes"] == snapshot["nodes"]
    assert incremental["usage"] == snapshot["usage"]
    # 严格执行序：Think → Tool → Tool → Think → Tool → Tool → Think → Answer
    kinds = [n["kind"] for n in snapshot["nodes"]]
    assert kinds == ["think", "tool", "tool", "think", "tool", "tool", "think", "answer"]


# ── 多 turn：id 唯一 ──

def test_multi_turn_snapshot():
    s = _Seq()
    events = []
    for turn in (1, 2):
        events += _turn_open(s)
        events += _think(s, 1, f"turn{turn}思考")
        events.append(s.ev("assistant/chunk", {"kind": "text", "delta": f"turn{turn}答案"}))
        events.append(s.ev("assistant/message", {"content": f"turn{turn}答案", "tool_calls": []}))
        events.append(s.ev("step/end", {"step": 1}))
        events.append(s.ev("turn/end", {"stop_reason": "completed"}))

    _, snap = _run(events)
    nodes = _nodes(snap)
    ids = [n["id"] for n in nodes]
    assert len(ids) == len(set(ids))  # t1.* 与 t2.* 不冲突
    answers = [n for n in nodes if n["kind"] == "answer"]
    assert [a["text"] for a in answers] == ["turn1答案", "turn2答案"]


# ── Session listener 语义 ──

def test_session_listener_lifecycle():
    session = Session(init_messages=[{"role": "system", "content": "sys"}])
    got: list[str] = []

    def cb(ev):
        got.append(ev.type)

    session.add_listener(cb)
    session.append("user/message", {"content": "hi"})
    session.append("turn/start", {})
    assert got == ["user/message", "turn/start"]  # 按序收到（含 surface 过滤外的类型）

    session.remove_listener(cb)
    session.append("user/message", {"content": "again"})
    assert got == ["user/message", "turn/start"]  # 摘除后不再收

    # from_events 重建不触发 listener（replay 不广播）
    header, events = session.header, session.events
    rebuilt = Session.from_events(header, events)
    got2: list[str] = []
    rebuilt.add_listener(lambda ev: got2.append(ev.type))
    assert got2 == []


# ── open 快照语义（回归：活引用污染已入队事件） ──

def test_open_event_is_emit_time_snapshot():
    """traj/open 必须携带发射时刻的快照：后续 chunk 累积不得污染已入队事件。

    回归场景：_open 曾把内部 node 活引用放入事件，SSE 出队序列化晚于
    node['text'] += delta → open 负载已含首 delta，前端 open.text + 首条
    traj/delta 双份叠加（每段思考首词双写：TheThe / 用户用户）。
    """
    s = _Seq()
    p = TrajectoryProjection()
    p.handle(s.ev("turn/start", {"agent": "t"}))
    p.handle(s.ev("step/start", {"step": 1, "total": 5}))

    # 首 chunk：handle 返回 [traj/open, traj/delta]，模拟「open 已入队但未序列化」
    out = p.handle(s.ev("assistant/chunk", {"kind": "thinking", "delta": "用户"}))
    open_ev, first_delta = out[0], out[1]

    # 队列延迟期间后续 chunk 到达 → 投影器内部 node 被继续改写
    p.handle(s.ev("assistant/chunk", {"kind": "thinking", "delta": "想"}))
    p.handle(s.ev("assistant/chunk", {"kind": "thinking", "delta": "找"}))

    assert open_ev["type"] == "traj/open"
    assert open_ev["node"]["text"] == ""          # 发射时刻快照：未被后续 += 污染
    assert first_delta["delta"] == "用户"          # 内容只经 delta 推一次
    # 内部累积不受影响（真实事件流下 finish 快照仍是全文）
    assert p._find("t1.s1.think")["text"] == "用户想找"
    assert p.finish()["nodes"][0]["text"] == "用户想找"
