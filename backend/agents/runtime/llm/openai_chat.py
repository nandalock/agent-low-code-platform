"""OpenAI 兼容 chat/completions 客户端 —— 唯一认识 wire 的地方

对齐 A 的 ``packages/llm/llm-deepseek``：**凭据、端点、payload 字段名、SSE 组帧、
状态码→失败码、usage 字段名**全部收在这里。循环（agent_loop.py）只发 canonical 请求
（LlmRequest）、只收 canonical 结果（LlmResponse / StreamChunk）。

本模块认识的 provider 差异（都是 wire 事实，不是执行策略）：
  · ``{base_url}/v1/chat/completions`` + Bearer 头；
  · payload 字段名与 ``tool_choice: auto``；流式要 ``stream_options.include_usage``
    （SSE 默认不回 usage，DeepSeek 在 [DONE] 前补一帧纯 usage，choices 为空）；
  · ``choices[0].delta`` 的嵌套 → 拍平成 canonical 分片；
  · ``prompt_cache_hit_tokens`` → ``cache_hit_tokens``（canonical 名，循环与投影只认后者）。

**不做的两件事**（契约见 client.py）：
  · 不写 Session 事件（增量落库在 loop/assistant_stream.py）；
  · 不吞取消（``asyncio.CancelledError`` 原样穿透，由循环判定协作取消 / 硬取消）。
"""
import asyncio
import json
import logging
from collections.abc import AsyncIterator

import aiohttp

from backend.agents.runtime.llm.client import LlmClient
from backend.agents.runtime.llm.errors import (
    LlmError,
    http_failure,
    invalid_response,
    timeout_failure,
    transport_failure,
)
from backend.agents.runtime.llm.hooks import RequestErrorHook
from backend.agents.runtime.llm.retry import make_retry_policy
from backend.agents.runtime.llm.types import (
    CHUNK_FINISH,
    CHUNK_TEXT,
    CHUNK_THINKING,
    CHUNK_TOOL_CALLS,
    CHUNK_USAGE,
    LlmRequest,
    LlmResponse,
    StreamChunk,
    TokenUsage,
)
from backend.core.http import get_http_session

logger = logging.getLogger(__name__)

#: 单次请求的默认超时上限（秒）：非流式收快，流式放长；实际取 min(上限, request.timeout)
#: —— request.timeout 是循环给的 max_wall_time 剩余预算，谁小听谁的。
DEFAULT_JSON_TIMEOUT = 60.0
DEFAULT_STREAM_TIMEOUT = 300.0


class OpenAiChatClient(LlmClient):
    """OpenAI 兼容网关的 chat/completions 客户端（DeepSeek 等同一族协议）。"""

    def __init__(self, *, api_key: str, base_url: str, max_llm_retries: int) -> None:
        # 凭据只在本模块出现：归一（去空白）与「有没有配」都归客户端回答
        self._api_key = (api_key or "").strip()
        base_url = (base_url or "").strip()
        self._endpoint = f"{base_url}/v1/chat/completions" if base_url else ""
        # provider 声明的默认重试策略：装配层在没传 hooks.request_error 时用它（策略/执行分离）
        self._retry_policy = make_retry_policy(max_llm_retries)

    # ── 契约 ──

    @property
    def configured(self) -> bool:
        """端点与凭据齐备才发得出去（缺一即「服务未配置」，不落任何事件）。"""
        return bool(self._endpoint and self._api_key)

    @property
    def retry_policy(self) -> RequestErrorHook:
        return self._retry_policy

    async def complete(self, request: LlmRequest) -> LlmResponse:
        """非流式请求：一次 POST 拿完整 message。

        HTTP 状态必须**先于**响应体判定：非 2xx 时网关返回的是错误 JSON（甚至 HTML），
        照常当成功解析会把错误体当成 assistant message 落库。
        """
        timeout = min(DEFAULT_JSON_TIMEOUT, request.timeout or DEFAULT_JSON_TIMEOUT)
        try:
            http = await get_http_session()
            async with http.post(
                self._endpoint, headers=self._headers(), json=self._wire_payload(request),
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status >= 400:
                    raise http_failure(resp.status, await resp.text(), resp.headers)
                try:
                    result = await resp.json()
                except Exception as e:  # noqa: BLE001 —— 网关返 HTML/空体：按非法响应重试
                    raise invalid_response(f"LLM 返回非 JSON 响应: {e}", status=resp.status) from e
        except LlmError:
            raise
        except asyncio.TimeoutError as e:
            raise timeout_failure(e) from e
        except aiohttp.ClientError as e:
            raise transport_failure(e) from e

        if not isinstance(result, dict):
            raise invalid_response(
                f"LLM 返回结构异常（顶层不是对象）: {type(result).__name__}",
                status=resp.status,
            )
        # choices 可能缺失或为空数组（网关错误体的常见形态）—— 退化成空 message，由
        # validate_response 判成 EMPTY_RESPONSE（可重试），而不是在这里 IndexError
        choice = (result.get("choices") or [{}])[0] or {}
        msg = dict(choice.get("message") or {})
        return LlmResponse(
            message=msg,
            finish_reason=choice.get("finish_reason"),
            usage=self._usage(result.get("usage")),
        )

    async def stream(self, request: LlmRequest) -> AsyncIterator[StreamChunk]:
        """流式请求（OpenAI 兼容 SSE）：逐帧翻成 canonical 分片。

        ``[DONE]`` 之前把 ``choices`` 为空的 usage 帧与携带 finish_reason 的帧分别产出
        （顺序照 wire 原样）；非 ``data:`` 行与解不出的 JSON 帧跳过（保真既有行为）。
        """
        timeout = min(DEFAULT_STREAM_TIMEOUT, request.timeout or DEFAULT_STREAM_TIMEOUT)
        try:
            http = await get_http_session()
            async with http.post(
                self._endpoint, headers=self._headers(), json=self._wire_payload(request),
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status >= 400:
                    # 与 complete 同规：错误体不是 SSE，先看状态再看流
                    raise http_failure(resp.status, await resp.text(), resp.headers)
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
                    # usage 观测：include_usage 后 provider 在 [DONE] 前发纯 usage 帧
                    # （choices 空）—— 必须在这层捕获（break 在 [DONE] 处，放后面会漏）
                    if chunk.get("usage"):
                        yield StreamChunk(kind=CHUNK_USAGE, usage=self._usage(chunk["usage"]))
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0] or {}
                    if choice.get("finish_reason"):
                        yield StreamChunk(kind=CHUNK_FINISH, finish_reason=choice["finish_reason"])
                    delta = choice.get("delta") or {}

                    # 推理过程（deepseek reasoning_content）—— 只广播、不混入 content
                    if delta.get("reasoning_content"):
                        yield StreamChunk(kind=CHUNK_THINKING, delta=delta["reasoning_content"])
                    if delta.get("content"):
                        yield StreamChunk(kind=CHUNK_TEXT, delta=delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        # wire 的 function 嵌套在这里拍平；分段拼接归循环侧累积器
                        fn = tc.get("function") or {}
                        yield StreamChunk(kind=CHUNK_TOOL_CALLS, tool_calls=({
                            "index": tc.get("index", 0),
                            "id": tc.get("id") or "",
                            "name": fn.get("name") or "",
                            "arguments": fn.get("arguments") or "",
                        },))
        except LlmError:
            raise
        except asyncio.TimeoutError as e:
            raise timeout_failure(e) from e
        except aiohttp.ClientError as e:
            raise transport_failure(e) from e

    # ── wire（本模块之外没人需要知道这些）──

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _wire_payload(request: LlmRequest) -> dict:
        """canonical 请求 → OpenAI 兼容 payload。"""
        payload = {
            "model": request.model,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if request.tools:
            payload["tools"] = request.tools
            # 有工具就让模型自己决定用不用（canonical 上暂不暴露 tool_choice；
            # router 那种 required 等下一批迁移时再加字段）
            payload["tool_choice"] = "auto"
        if request.stream:
            payload["stream"] = True
            # SSE 默认不回 usage；DeepSeek 在 [DONE] 前发一个纯 usage chunk（choices 为空）
            payload["stream_options"] = {"include_usage": True}
        return payload

    @staticmethod
    def _usage(raw: dict | None) -> TokenUsage | None:
        """provider usage → canonical TokenUsage（**字段名翻译只在这一处**）。"""
        if not raw:
            return None
        return TokenUsage(
            prompt_tokens=raw.get("prompt_tokens"),
            completion_tokens=raw.get("completion_tokens"),
            cache_hit_tokens=raw.get("prompt_cache_hit_tokens"),
            cache_miss_tokens=raw.get("prompt_cache_miss_tokens"),
        )
