"""LLM 请求子系统 — canonical 词汇（types）+ 客户端契约（client）+ 适配器（openai_chat）
+ 失败契约（errors）+ 拦截通道（hooks）+ 重试策略（retry）

对齐 A 的分层（`packages/llm/llm` 核心 + `packages/llm/llm-deepseek` 适配器）：

  types.py       — canonical 词汇：LlmRequest / LlmResponse / StreamChunk / TokenUsage
  client.py      — 循环唯一认识的 LLM 出口（LlmClient 协议）
  openai_chat.py — 适配器：凭据 · 端点 · HTTP · SSE · 状态码→失败码 · usage 字段名
  errors.py      — 失败类型、失败码分类、响应可用性校验（「发生了什么」）
  hooks.py       — 循环拦截通道的契约（「允许谁改变执行走向」）
  retry.py       — 退避曲线 + 默认 request_error 裁决（「该怎么办」）

**执行侧不在这里**：循环、`asyncio.sleep`、落 `llm/error` 事实、截止时刻约束仍在
agent_loop.py —— 它只认本包的门面符号，自己不认识任何失败码、退避常数、api_key
或 base_url。

对外只从本包取符号，不直接摸子模块 —— 内部怎么分文件是这一层的事（同 session/
与 system_prompt/ 的约定）。例外：测试要 patch 模块级退避常数时得直接拿定义它的
那个模块（`from backend.agents.runtime.llm import retry as retry_mod`），因为
`retry_delay()` 读的是自己模块里的全局量。
"""
from backend.agents.runtime.llm.errors import (
    CODE_CLIENT,
    CODE_EMPTY_RESPONSE,
    CODE_INVALID_RESPONSE,
    CODE_RATE_LIMIT,
    CODE_SERVER,
    CODE_TIMEOUT,
    CODE_TRANSPORT,
    CODE_UNKNOWN_FINISH,
    KNOWN_FINISH_REASONS,
    RETRYABLE_CODES,
    LlmError,
    classify_status,
    http_failure,
    invalid_response,
    retry_after_seconds,
    timeout_failure,
    transport_failure,
    validate_response,
)
from backend.agents.runtime.llm.hooks import LoopHooks, RequestErrorHook, RetryDecision
from backend.agents.runtime.llm.retry import (
    DEFAULT_MAX_LLM_RETRIES,
    LLM_RETRY_INITIAL_DELAY,
    LLM_RETRY_JITTER,
    LLM_RETRY_MAX_DELAY,
    make_retry_policy,
    retry_delay,
)
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
from backend.agents.runtime.llm.client import LlmClient
# 适配器最后 import：它依赖上面几个模块（package 初始化时的顺序即依赖顺序）
from backend.agents.runtime.llm.openai_chat import OpenAiChatClient

__all__ = [
    "CHUNK_FINISH",
    "CHUNK_TEXT",
    "CHUNK_THINKING",
    "CHUNK_TOOL_CALLS",
    "CHUNK_USAGE",
    "LlmClient",
    "LlmRequest",
    "LlmResponse",
    "OpenAiChatClient",
    "StreamChunk",
    "TokenUsage",
    "CODE_CLIENT",
    "CODE_EMPTY_RESPONSE",
    "CODE_INVALID_RESPONSE",
    "CODE_RATE_LIMIT",
    "CODE_SERVER",
    "CODE_TIMEOUT",
    "CODE_TRANSPORT",
    "CODE_UNKNOWN_FINISH",
    "DEFAULT_MAX_LLM_RETRIES",
    "KNOWN_FINISH_REASONS",
    "LLM_RETRY_INITIAL_DELAY",
    "LLM_RETRY_JITTER",
    "LLM_RETRY_MAX_DELAY",
    "LoopHooks",
    "LlmError",
    "RETRYABLE_CODES",
    "RequestErrorHook",
    "RetryDecision",
    "classify_status",
    "http_failure",
    "invalid_response",
    "make_retry_policy",
    "retry_after_seconds",
    "retry_delay",
    "timeout_failure",
    "transport_failure",
    "validate_response",
]
