"""LLM 客户端（适配器）自检 —— wire 翻译 · 失败码 · 规范选项

patch 掉 aiohttp（`llm.openai_chat.get_http_session`），断言的全是**适配器边界**：

  · canonical 请求 → wire payload 的形状（工具 / 流选项 / 凭据只在头上）；
  · wire 帧 → canonical 分片（reasoning / text / 拍平的 tool_calls / usage / finish）；
  · provider 字段名 → canonical 名（prompt_cache_hit_tokens → cache_hit_tokens）；
  · HTTP 状态 → 结构化失败码（含 Retry-After）。

循环侧的收口（重试 / 落库 / 取消 / 半截正文）在 test_agent_loop.py，那边是端到端的。

Usage:
    docker compose exec backend python backend/agents/runtime/tests/test_llm_client.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from backend.agents.runtime.llm import (  # noqa: E402
    CODE_CLIENT,
    CODE_RATE_LIMIT,
    LlmError,
    LlmRequest,
    OpenAiChatClient,
)
from backend.agents.runtime.llm import openai_chat as client_mod  # noqa: E402

# ── 测试替身：HTTP ──


class _LineStream:
    """fake resp.content：逐行 SSEC（bytes）"""

    def __init__(self, lines: list[str]):
        self._lines = lines

    def __aiter__(self):
        async def gen():
            for line in self._lines:
                yield line.encode("utf-8")
        return gen()


class _FakeResp:
    def __init__(self, *, status: int = 200, payload=None, lines: list[str] | None = None, headers=None):
        self.status = status
        self.headers = headers or {}
        self._payload = payload
        self._lines = lines or []

    async def json(self):
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


class _FakeSession:
    """假 aiohttp session：记录每次 post 的 url / headers / json"""

    def __init__(self, resp: _FakeResp):
        self._resp = resp
        self.calls: list[dict] = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        return self._resp


def _client(resp: _FakeResp, **kwargs) -> tuple[OpenAiChatClient, _FakeSession]:
    session = _FakeSession(resp)

    async def _get_session():
        return session

    client_mod.get_http_session = _get_session
    client = OpenAiChatClient(
        api_key=kwargs.pop("api_key", "k"),
        base_url=kwargs.pop("base_url", "http://fake"),
        max_llm_retries=kwargs.pop("max_llm_retries", 2),
    )
    return client, session


def _request(**overrides) -> LlmRequest:
    return LlmRequest(model="m", messages=[{"role": "user", "content": "hi"}], max_tokens=16, **overrides)


def _collect(client: OpenAiChatClient, request: LlmRequest) -> list:
    async def _run():
        return [chunk async for chunk in client.stream(request)]

    return asyncio.run(_run())


def _sse(*frames: str) -> _FakeResp:
    return _FakeResp(lines=[f"data: {f}" for f in frames])


# ── canonical 请求 → wire ──


def test_complete_posts_wire_payload_and_maps_usage():
    """非流式：wire 形状 + provider usage 字段名 → canonical 名"""
    resp = _FakeResp(payload={
        "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 10, "completion_tokens": 2,
            "prompt_cache_hit_tokens": 8, "prompt_cache_miss_tokens": 2,
        },
    })
    client, session = _client(resp)

    out = asyncio.run(client.complete(_request()))

    assert out.message["content"] == "hi" and out.finish_reason == "stop"
    assert (out.usage.prompt_tokens, out.usage.cache_hit_tokens, out.usage.cache_miss_tokens) == (10, 8, 2)
    call = session.calls[0]
    assert call["url"] == "http://fake/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer k"
    assert call["json"]["model"] == "m" and call["json"]["messages"] == [{"role": "user", "content": "hi"}]
    assert "stream" not in call["json"]           # 非流式请求不声明 stream
    assert "api_key" not in json.dumps(call["json"])   # 凭据只在头上，不在请求体里


def test_wire_payload_declares_tools_and_stream_options():
    """流式 + 有工具：stream / stream_options / tool_choice 是 wire 侧的约定"""
    client, session = _client(_sse("[DONE]"))
    tools = [{"type": "function", "function": {"name": "echo", "parameters": {}}}]

    _collect(client, _request(tools=tools, stream=True))

    payload = session.calls[0]["json"]
    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}
    assert payload["tools"] == tools and payload["tool_choice"] == "auto"


def test_request_timeout_bounds_the_wire_timeout():
    """循环给的剩余预算进 aiohttp 的 total 超时（不突破 max_wall_time）"""
    client, session = _client(_FakeResp(payload={"choices": [{}]}))

    asyncio.run(client.complete(_request(timeout=0.5)))

    assert session.calls[0]["timeout"].total == 0.5


# ── wire 帧 → canonical 分片 ──


def test_stream_yields_canonical_chunks_in_wire_order():
    """顺序照 wire：thinking → text → tool_calls 片段 → finish → usage；[DONE] 之后不再产出"""
    client, _ = _client(_sse(
        '{"choices":[{"delta":{"reasoning_content":"想"}}]}',
        '{"choices":[{"delta":{"content":"正文"}}]}',
        '{"choices":[{"delta":{"tool_calls":'
        '[{"index":0,"id":"c1","function":{"name":"echo","arguments":"{\\"a\\""}}]}}]}',
        '{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":":1}"}}]}}]}',
        '{"choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
        '{"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":1,'
        '"prompt_cache_hit_tokens":4,"prompt_cache_miss_tokens":1}}',
        "[DONE]",
        '{"choices":[{"delta":{"content":"不该到"}}]}',
    ))

    chunks = _collect(client, _request(stream=True))

    assert [c.kind for c in chunks] == ["thinking", "text", "tool_calls", "tool_calls", "finish", "usage"]
    assert chunks[0].delta == "想" and chunks[1].delta == "正文"
    # tool_calls 已拍平（wire 的 function 嵌套在适配器里剥掉），拼接归循环侧累积器
    assert chunks[2].tool_calls[0] == {"index": 0, "id": "c1", "name": "echo", "arguments": '{"a"'}
    assert chunks[3].tool_calls[0] == {"index": 0, "id": "", "name": "", "arguments": ":1}"}
    assert chunks[4].finish_reason == "tool_calls"
    assert chunks[5].usage.cache_hit_tokens == 4


def test_stream_skips_frames_it_cannot_parse():
    """非 data: 行 / 解不出的 JSON 帧跳过（保真既有行为，不静默当成空回答）"""
    client, _ = _client(_FakeResp(lines=[
        "event: ping",
        "data: 不是 JSON",
        'data: {"choices":[{"delta":{"content":"ok"}}]}',
        "data: [DONE]",
    ]))

    assert [(c.kind, c.delta) for c in _collect(client, _request(stream=True))] == [("text", "ok")]


# ── 失败码 ──


def test_http_status_becomes_structured_failure_with_retry_after():
    """非 2xx：状态先于响应体判定 → 结构化失败码 + 对端退避建议"""
    client, _ = _client(_FakeResp(
        status=429, headers={"Retry-After": "3"}, payload={"error": "slow down"},
    ))

    try:
        asyncio.run(client.complete(_request()))
    except LlmError as e:
        assert (e.code, e.status, e.retry_after) == (CODE_RATE_LIMIT, 429, 3.0)
    else:
        raise AssertionError("429 必须结构化失败，而不是被当成正常响应")


def test_configured_needs_both_credentials_and_endpoint():
    """「配没配」由客户端回答 —— 循环不再自己看 api_key"""
    assert OpenAiChatClient(api_key="k", base_url="http://x", max_llm_retries=0).configured
    assert not OpenAiChatClient(api_key="  ", base_url="http://x", max_llm_retries=0).configured
    assert not OpenAiChatClient(api_key="k", base_url="", max_llm_retries=0).configured


def test_retry_policy_is_declared_by_the_provider_and_code_driven():
    """provider 声明默认策略：策略归客户端、执行仍归循环（对齐 A 的 providerRetryPolicy）"""
    policy = OpenAiChatClient(api_key="k", base_url="http://x", max_llm_retries=2).retry_policy
    rate = LlmError("限流", code=CODE_RATE_LIMIT)
    client_error = LlmError("鉴权", code=CODE_CLIENT)

    assert asyncio.run(policy(rate, 0)) is not None        # 可重试的码 + 还有次数
    assert asyncio.run(policy(rate, 2)) is None            # 次数用尽
    assert asyncio.run(policy(client_error, 0)) is None    # 不可重试的码


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
