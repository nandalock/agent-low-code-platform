"""LLM 重试策略：退避曲线 + 默认 request_error 裁决

对齐 A 的 ``dsh-llm-retry``：**策略在这里，执行在 agent_loop.py**。

  决策（本模块）—— 「这次失败要不要重试、等多久」。
  执行（Loop）   —— 循环、``asyncio.sleep``、落 ``llm/error`` 事实、截止时刻约束。

这么切的目的：换退避曲线、改哪些失败码可重试、接一个「白天重试夜里不重试」的
策略，都只动本模块（或干脆在装配处传一个自定义 ``hooks.request_error``），
agent_loop.py 里再也看不到 RETRYABLE_CODES 与退避常数。

本模块是 ``hooks.request_error`` 的**第一个内置消费者**（docs/agent-loop-design.md
§6.4 第 3 条）。不装配钩子时 Loop 就用它，于是改造前后行为完全一致。
"""
import random

from backend.agents.runtime.llm.errors import LlmError
from backend.agents.runtime.llm.hooks import RequestErrorHook, RetryDecision

#: 退避参数（对齐 A 的缺省 backoff：初始 500ms、上限 10s、抖动 10%）。
#: 模块级常量而非硬编码：自检脚本把它们压到 0 以避免真实等待。
LLM_RETRY_INITIAL_DELAY = 0.5
LLM_RETRY_MAX_DELAY = 10.0
LLM_RETRY_JITTER = 0.1

#: 每次 step 的 LLM 调用重试次数上限（llm_params.max_llm_retries 覆盖，见 AgentRuntime._llm_params）
DEFAULT_MAX_LLM_RETRIES = 2


def retry_delay(attempt: int, retry_after: float = 0.0) -> float:
    """第 attempt 次重试前的退避时长：指数增长 + 对称抖动，取对端建议与本地退避的较大者。

    抖动是必要的：多个会话同时被同一个 429 挡住时，同时重试会再撞一次限流。
    """
    base = min(LLM_RETRY_INITIAL_DELAY * (2 ** attempt), LLM_RETRY_MAX_DELAY)
    delay = max(base, min(retry_after, LLM_RETRY_MAX_DELAY))
    return delay * (1.0 + (random.random() * 2.0 - 1.0) * LLM_RETRY_JITTER)


def make_retry_policy(max_retries: int) -> RequestErrorHook:
    """装配内置默认策略：可重试失败码 + 有限次数 + 指数退避。

    ``max_retries`` 在装配时固化（``max_llm_retries`` 配置，0 = 关掉重试）—— 它就是
    改造前 ``_request_llm`` 里那一行 ``attempt < max_retries`` 的边界，只是搬到了
    策略侧。返回的钩子按 ``RetryDecision | None`` 裁决，Loop 只负责执行。
    """
    retries = max(0, int(max_retries))

    async def policy(error: LlmError, attempt: int) -> RetryDecision | None:
        if not error.retryable or attempt >= retries:
            return None
        return RetryDecision(delay=retry_delay(attempt, error.retry_after), reason=error.code)

    return policy
