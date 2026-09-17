"""崩溃修复自检（interrupted_turn_closers）

纯内存：手工构造「被中断的日志」，断言补出来的合成事件把它们补平 ——
闭合性（step/end、turn/end 成对）、消息合法性（每个 tool_call 都有配对的 tool 消息）、
不发明事实（结果如实说「未知/未执行」，不假装成功）、以及幂等（补过的日志再扫一遍无事）。

Usage:
    docker compose exec backend python backend/agents/runtime/session/tests/test_repair.py
    # 或本机（仓库根目录下）：
    python backend/agents/runtime/session/tests/test_repair.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))

from backend.agents.runtime.session import (  # noqa: E402
    ASSISTANT_MESSAGE,
    STEP_END,
    STEP_START,
    TURN_END,
    TURN_START,
    USER_MESSAGE,
    Session,
    SessionEvent,
    SessionHeader,
    interrupted_turn_closers,
)
from backend.agents.runtime.session.repair import (  # noqa: E402
    STOP_REASON_INTERRUPTED,
    TOOL_NOT_STARTED,
    TOOL_OUTCOME_UNKNOWN,
)
from backend.agents.runtime.session.events import TOOL_CALL, TOOL_RESULT  # noqa: E402

# ── 构造被中断的日志 ──


class _Builder:
    """按写入顺序生成事件（seq 自增、time 固定）：模拟「已落库的那段日志」"""

    def __init__(self):
        self.events: list[SessionEvent] = []
        self._seq = 0

    def add(self, type_: str, data: dict) -> "_Builder":
        self._seq += 1
        self.events.append(SessionEvent(type=type_, seq=self._seq, time=1000.0 + self._seq, data=data))
        return self

    def turn_start(self) -> "_Builder":
        return self.add(TURN_START, {"agent": "a", "limits": {"max_steps": 8}})

    def step_start(self, step: int = 1) -> "_Builder":
        return self.add(STEP_START, {"step": step, "total": 8})

    def step_end(self, step: int = 1) -> "_Builder":
        return self.add(STEP_END, {"step": step})

    def turn_end(self, reason: str = "completed") -> "_Builder":
        return self.add(TURN_END, {"stop_reason": reason})

    def assistant(self, content: str = "", calls: tuple = ()) -> "_Builder":
        return self.add(ASSISTANT_MESSAGE, {
            "content": content,
            "tool_calls": [
                {"id": cid, "type": "function", "function": {"name": name, "arguments": "{}"}}
                for cid, name in calls
            ],
        })

    def tool_call(self, call_id: str, tool: str = "echo") -> "_Builder":
        return self.add(TOOL_CALL, {"tool": tool, "args": {}, "tool_call_id": call_id})

    def tool_result(self, call_id: str, tool: str = "echo", content: str = '{"ok": true}') -> "_Builder":
        return self.add(TOOL_RESULT, {"tool": tool, "tool_call_id": call_id, "content": content})


def _closers(builder: _Builder) -> list[SessionEvent]:
    return interrupted_turn_closers(builder.events)


def _result_of(ev: SessionEvent) -> dict:
    return json.loads(ev.data["content"])


# ── 平衡的日志：什么都不补 ──


def test_balanced_log_needs_no_repair():
    b = (_Builder().turn_start().step_start().assistant("答完了").step_end().turn_end())
    assert _closers(b) == []


def test_empty_log_needs_no_repair():
    assert interrupted_turn_closers([]) == []


def test_open_turn_with_only_user_message():
    """turn 刚开始就崩：只补 turn/end（没有 step 要关）"""
    closers = _closers(_Builder().turn_start().add(USER_MESSAGE, {"content": "问题"}))

    assert [c.type for c in closers] == [TURN_END]
    assert closers[0].data["stop_reason"] == STOP_REASON_INTERRUPTED


# ── 未闭合的 step / turn ──


def test_open_step_is_closed_before_turn():
    """step 开着时不能直接关 turn：先补 step/end，再补 turn/end"""
    closers = _closers(_Builder().turn_start().step_start(2).assistant("说到一半"))

    assert [(c.type, c.data.get("step")) for c in closers] == [
        (STEP_END, 2), (TURN_END, None),
    ]
    assert closers[-1].data["stop_reason"] == STOP_REASON_INTERRUPTED


# ── 悬空的 tool_calls（消息序列合法性）──


def test_started_call_gets_outcome_unknown_result():
    """已记发起的调用：补「结果未知」的合成结果，并引用那次 tool/call 的 seq"""
    b = (_Builder().turn_start().step_start()
         .assistant("", calls=(("c1", "echo"),)).tool_call("c1"))
    call_seqs = [e.seq for e in b.events if e.type == TOOL_CALL]

    closers = _closers(b)

    result = closers[0]
    assert result.type == TOOL_RESULT
    assert result.data["tool_call_id"] == "c1"
    assert result.data["tool"] == "echo"
    assert _result_of(result)["code"] == TOOL_OUTCOME_UNKNOWN
    assert result.source_event_seqs == call_seqs       # 引用所补的那条 tool/call
    assert [c.type for c in closers] == [TOOL_RESULT, STEP_END, TURN_END]


def test_unstarted_call_gets_not_started_result():
    """连发起都没记上（未执行）：结果如实说「未执行」，且不引用任何 seq"""
    b = (_Builder().turn_start().step_start().assistant("", calls=(("c1", "echo"),)))

    closers = _closers(b)

    result = closers[0]
    assert _result_of(result)["code"] == TOOL_NOT_STARTED
    assert result.source_event_seqs is None
    assert "未执行" in _result_of(result)["error"]


def test_completed_call_is_not_repaired():
    """有配对的 tool/result 的调用不动它（不是所有调用都补）"""
    b = (_Builder().turn_start().step_start()
         .assistant("", calls=(("c1", "echo"), ("c2", "echo")))
         .tool_call("c1").tool_result("c1").tool_call("c2"))

    closers = _closers(b)

    assert [c.data["tool_call_id"] for c in closers if c.type == TOOL_RESULT] == ["c2"]


def test_pending_calls_keep_model_order():
    """多个悬空调用按模型给出的顺序补（顺序即消息序，不能乱）"""
    b = (_Builder().turn_start().step_start()
         .assistant("", calls=(("c1", "echo"), ("c2", "echo"), ("c3", "echo")))
         .tool_call("c1").tool_call("c2").tool_call("c3"))

    closers = _closers(b)

    assert [c.data["tool_call_id"] for c in closers if c.type == TOOL_RESULT] == ["c1", "c2", "c3"]


def test_calls_in_closed_step_do_not_leak_into_tail():
    """已闭合 step 里的调用不被尾巴修复牵连（step/end 清空游标）"""
    b = (_Builder().turn_start().step_start(1)
         .assistant("", calls=(("c_old", "echo"),)).step_end(1)   # 没配对就被关掉了：是别的问题
         .step_start(2).assistant("继续"))

    closers = _closers(b)

    assert [c.type for c in closers] == [STEP_END, TURN_END]
    assert all(c.data.get("tool_call_id") != "c_old" for c in closers)


# ── 合成事件的形态 ──


def test_closers_continue_seq_and_reuse_last_time():
    """seq 续在末尾、time 复用最后一条（不发明未来时间 —— 否则 trace 会算出巨大 latency）"""
    b = _Builder().turn_start().step_start().assistant("半截")
    last = b.events[-1]

    closers = _closers(b)

    assert [c.seq for c in closers] == [last.seq + 1, last.seq + 2]
    assert all(c.time == last.time for c in closers)


def test_repair_is_idempotent():
    """补过的日志再扫一遍：已平衡，不再补（冷恢复可能被反复触发）"""
    b = _Builder().turn_start().step_start().assistant("", calls=(("c1", "echo"),)).tool_call("c1")
    closers = _closers(b)
    assert closers

    b.events.extend(closers)

    assert interrupted_turn_closers(b.events) == []


# ── 与 Session 接线：补完的日志派生出的消息序合法 ──


def test_repaired_session_derives_legal_messages():
    """修复后 derive_messages 的每个 tool 消息都有对应的 tool_call（下一轮请求合法）"""
    b = (_Builder().turn_start().add(USER_MESSAGE, {"content": "帮我查"})
         .step_start().assistant("我先查一下", calls=(("c1", "echo"), ("c2", "echo")))
         .tool_call("c1").tool_result("c1").tool_call("c2"))
    # 走真实的冷恢复路径：from_events 重建（log + surface）→ 补记 → 派生
    session = Session.from_events(
        SessionHeader(version=1, id="s1", created_at=0.0, seed_length=0), b.events,
    )

    session.append_recovered(interrupted_turn_closers(session.events))

    messages = session.derive_messages()
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "tool"]
    call_ids = [c["id"] for c in messages[1]["tool_calls"]]
    assert [m["tool_call_id"] for m in messages[2:]] == call_ids
    assert messages[-1]["content"].startswith("{")     # 合成结果仍是 JSON 全文

    # trace 侧的闭合也成立（turn/end 有 stop_reason）
    from backend.agents.runtime.session.projections import TraceProjection
    projection = TraceProjection()
    for ev in session.events:
        projection.handle(ev)
    snapshot = projection.snapshot()
    assert snapshot["stop_reason"] == STOP_REASON_INTERRUPTED
    assert any(step.get("tool") == "echo" for step in snapshot["steps"])


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
