"""Session 自检（事件 → surface → derive_messages；seed 退役后的冷恢复行为）

纯内存，不需要 docker / DB / 网络。

Usage:
    docker compose exec backend python backend/agents/runtime/session/tests/test_session.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))

from backend.agents.runtime.session import (  # noqa: E402
    ASSISTANT_MESSAGE,
    SEED,
    TOOL_RESULT,
    TURN_START,
    USER_MESSAGE,
    Session,
    SessionEvent,
    SessionHeader,
    SessionStore,
)


def _header(sid: str = "s1") -> SessionHeader:
    return SessionHeader(version=1, id=sid, created_at=time.time(), seed_length=0)


def _ev(seq: int, type_: str, data: dict) -> SessionEvent:
    return SessionEvent(type=type_, seq=seq, time=time.time(), data=data)


# ── 消息派生 ──


def test_derive_messages_from_conversation_events():
    """对话事件派生为 OpenAI messages；过程事件（turn/start）被过滤"""
    session = Session(header=_header())
    session.append(TURN_START, {"agent": "x"})
    session.append(USER_MESSAGE, {"content": "你好"})
    session.append(ASSISTANT_MESSAGE, {"content": "在的", "tool_calls": []})

    assert session.derive_messages() == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "在的"},
    ]


def test_derive_messages_keeps_tool_pairing():
    """assistant.tool_calls 与 tool/result 成对派生（序列合法性）"""
    session = Session(header=_header())
    session.append(ASSISTANT_MESSAGE, {
        "content": "",
        "tool_calls": [{"id": "c1", "type": "function",
                        "function": {"name": "bash", "arguments": "{}"}}],
    })
    session.append(TOOL_RESULT, {"tool_call_id": "c1", "content": "ok"})

    messages = session.derive_messages()
    assert messages[0]["tool_calls"][0]["id"] == "c1"
    assert messages[1] == {"role": "tool", "tool_call_id": "c1", "content": "ok"}


def test_seed_still_derives_in_memory():
    """内存构造时 seed 仍派生成 system（签名兼容，供测试与特殊场景）"""
    session = Session(header=_header(), init_messages=[{"role": "system", "content": "旧提示词"}])
    assert session.derive_messages()[0] == {"role": "system", "content": "旧提示词"}


# ── 冷恢复：seed 退役 ──


def test_from_events_drops_seed():
    """冷恢复丢弃 seed：不再出现旧 system，避免与前置的新 system 形成双 system"""
    events = [
        _ev(1, SEED, {"role": "system", "content": "旧 system prompt"}),
        _ev(2, SEED, {"role": "system", "content": "【上游节点输出，供你参考】\n{}"}),
        _ev(3, TURN_START, {"agent": "x"}),
        _ev(4, USER_MESSAGE, {"content": "你好"}),
        _ev(5, ASSISTANT_MESSAGE, {"content": "在的", "tool_calls": []}),
    ]
    session = Session.from_events(_header("cold"), events)

    messages = session.derive_messages()
    assert messages == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "在的"},
    ]
    assert all(m["role"] != "system" for m in messages)


def test_from_events_drops_seed_from_log_too():
    """被丢弃的 seed 不进 log —— 否则每次 flush 都会把死事件重新写库"""
    events = [
        _ev(1, SEED, {"role": "system", "content": "旧"}),
        _ev(2, USER_MESSAGE, {"content": "你好"}),
    ]
    session = Session.from_events(_header("cold"), events)

    assert [e.type for e in session.log] == [USER_MESSAGE]
    assert session.init_messages == []


def test_from_events_without_seed_is_unchanged():
    """无 seed 的历史照常重建（seq 连续、顺序保持）"""
    events = [
        _ev(1, USER_MESSAGE, {"content": "a"}),
        _ev(2, ASSISTANT_MESSAGE, {"content": "b", "tool_calls": []}),
    ]
    session = Session.from_events(_header("plain"), events)

    assert session.seq == 2
    assert [e.seq for e in session.log] == [1, 2]
    assert len(session.derive_messages()) == 2


def test_from_events_sorts_by_seq():
    """重建按 seq 排序（持久化读取顺序不作保证）"""
    events = [
        _ev(2, ASSISTANT_MESSAGE, {"content": "b", "tool_calls": []}),
        _ev(1, USER_MESSAGE, {"content": "a"}),
    ]
    session = Session.from_events(_header("unsorted"), events)
    assert [m["role"] for m in session.derive_messages()] == ["user", "assistant"]


def test_store_create_without_seed():
    """新链路：Store.create() 不带 init_messages → 会话从空历史开始"""
    store = SessionStore()
    session = store.create()

    assert session.derive_messages() == []
    assert session.init_messages == []
    assert store.get(session.header.id) is session


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
