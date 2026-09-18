"""LLM 请求子系统 — 失败契约（errors）+ 拦截通道（hooks）+ 重试策略（retry）

对齐 A 的分层（`packages/llm/` + `packages/llm/llm-retry/`）：

  errors.py — 失败类型、失败码分类、响应可用性校验（「发生了什么」）
  hooks.py  — 循环拦截通道的契约（「允许谁改变执行走向」）
  retry.py  — 退避曲线 + 默认 request_error 裁决（「该怎么办」）

**执行侧不在这里**：循环、`asyncio.sleep`、落 `llm/error` 事实、截止时刻约束仍在
agent_loop.py —— 它只认本包的门面符号，自己不认识任何失败码或退避常数。

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

__all__ = [
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
