"""AgentRuntime × SystemPrompt 集成自检（主链路切换的回归网）

验证每轮组装的 system prompt 真的进了 LLM 请求：mock 掉 LLM HTTP 调用，
捕获 payload，断言 messages[0] 的形态与内容。

不连 DB / 网络：config 走 definition 注入，Registry 未装配（工具为空），
LLM 调用被 monkeypatch 拦截。

Usage:
    docker compose exec backend python backend/agents/runtime/test_agent_runtime_prompt.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from backend.agents.runtime import agent_loop as loop_mod  # noqa: E402
from backend.agents.runtime import agent_runtime as runtime_mod  # noqa: E402
from backend.agents.runtime.agent_runtime import AgentRuntime  # noqa: E402
from backend.agents.runtime.session import (  # noqa: E402
    NoopPersistence,
    get_session_title_service,
    set_session_persistence,
)
from backend.agents.runtime.session import store as store_mod  # noqa: E402

captured: list[dict] = []


class _FakeResp:
    """假 aiohttp 响应：status/headers 是 AgentLoop 判失败码要读的字段，必须照实提供"""

    def __init__(self, payload: dict):
        self._payload = payload
        self.status = 200
        self.headers: dict = {}

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def post(self, url, **kwargs):
        captured.append(kwargs.get("json") or {})
        return _FakeResp({
            "choices": [{"message": {"content": "好的", "tool_calls": []}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        })


def _install_fakes():
    """拦截 LLM HTTP 调用（AgentLoop 与 AgentRuntime 各自 import 了 get_http_session）"""
    captured.clear()
    fake = _async_return(_FakeSession())
    runtime_mod.get_http_session = fake
    loop_mod.get_http_session = fake
    set_session_persistence(NoopPersistence())
    store_mod._store = store_mod.SessionStore()  # 每个用例干净的 SessionStore
    # 标题服务的在飞状态也是进程级单例，同样每用例清一遍
    get_session_title_service().forget_all()


def _async_return(value):
    async def _inner():
        return value
    return _inner


def _runtime(agent_key: str = "test_agent", **config) -> AgentRuntime:
    definition = {
        "name": "测试助手",
        "description": "用于自检的 agent",
        "status": "active",
        "config": {
            "api_key": "k", "base_url": "http://fake", "model": "m",
            # **关掉 LLM 标题**：本文件测的是 system prompt 的组装，而标题生成是
            # 第三个 LLM 调用方（AgentLoop / AgentRuntime 之外的 title.service），
            # 它不认上面那份 fake，会真的发 HTTP 并在 asyncio.run 收尾时炸出
            # 「Event loop is closed」。fallback 标题照常补（纯函数、无 IO），
            # 标题本身的逻辑在 session/tests/test_title.py 里单独测。
            "session_title_enabled": False,
            **config,
        },
    }
    agent = AgentRuntime(key=agent_key, definition=definition)
    return agent


def _reply(agent: AgentRuntime, question: str = "你好", **kwargs):
    return asyncio.run(agent.reply(tenant_id=1, question=question, **kwargs))


# ── 主链路 ──


def test_first_message_is_assembled_system_prompt():
    """messages[0] 是本轮组装的 system prompt：身份在前、persona 在后"""
    _install_fakes()
    agent = _runtime(system_prompt="你只回答订单问题。")
    _reply(agent)

    messages = captured[0]["messages"]
    assert messages[0]["role"] == "system", messages[0]
    system = messages[0]["content"]
    assert system.startswith("你是「测试助手」。"), system
    assert "你只回答订单问题。" in system
    assert system.index("你是「测试助手」") < system.index("你只回答订单问题")
    # 用户消息紧跟其后
    assert messages[1] == {"role": "user", "content": "你好"}


def test_system_prompt_not_persisted_into_history():
    """system prompt 不在 Session 里：第二轮仍只有一条 system，且历史是纯对话"""
    _install_fakes()
    agent = _runtime(system_prompt="人设")
    first = _reply(agent, "第一问")
    _reply(agent, "第二问", session_id=first.session_id)

    messages = captured[1]["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert sum(1 for m in messages if m["role"] == "system") == 1


def test_system_prompt_stable_across_turns():
    """跨轮 system 文本一致（前缀缓存友好的前提）"""
    _install_fakes()
    agent = _runtime(system_prompt="人设")
    first = _reply(agent, "第一问")
    _reply(agent, "第二问", session_id=first.session_id)

    assert captured[0]["messages"][0]["content"] == captured[1]["messages"][0]["content"]


def test_upstream_context_appended_at_tail():
    """上游输出进尾部上下文，不挤占前部稳定前缀"""
    _install_fakes()
    agent = _runtime(system_prompt="人设")
    _reply(agent, context={"节点A": {"output": "上游结果", "type": "agent"}})

    system = captured[0]["messages"][0]["content"]
    assert system.index("人设") < system.index("【上游节点输出，供你参考】")
    assert json.dumps({"节点A": {"output": "上游结果", "type": "agent"}},
                      ensure_ascii=False, indent=2) in system


def test_no_upstream_context_leaves_no_trace():
    """无上游输出时不留空段"""
    _install_fakes()
    agent = _runtime(system_prompt="人设")
    _reply(agent)
    assert "上游节点输出" not in captured[0]["messages"][0]["content"]


def test_empty_persona_does_not_add_empty_block():
    """persona 为空 → 只有身份段，无多余空行"""
    _install_fakes()
    agent = _runtime(system_prompt="")
    _reply(agent)

    system = captured[0]["messages"][0]["content"]
    assert system == "你是「测试助手」。用于自检的 agent", repr(system)


def test_tools_come_from_assembly():
    """Registry 未装配 → tools 参数不出现（而不是空数组）"""
    _install_fakes()
    agent = _runtime(system_prompt="人设")
    _reply(agent)
    assert "tools" not in captured[0]
    assert captured[0]["model"] == "m"


def test_broken_variable_fails_loudly():
    """段里引用不存在的变量 → 本轮报错（不发坏提示词给模型）"""
    _install_fakes()
    from backend.agents.runtime.system_prompt import PromptSection, get_system_prompt

    sp = get_system_prompt()
    sp.section(PromptSection(name="test:broken", order=700, text="目录 {{不存在的变量}}"))
    try:
        _reply(_runtime(system_prompt="人设"))
    except ValueError as e:
        assert "test:broken" in str(e), e
    else:
        raise AssertionError("应抛 ValueError")
    finally:
        sp._sections.pop("test:broken", None)


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
