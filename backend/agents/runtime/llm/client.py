"""LLM 客户端契约 —— 循环唯一认识的 LLM 出口（对齐 A 的 LlmRuntime）

契约（本模块）与实现（openai_chat.py：一个端点 + 一份凭据 + HTTP/SSE/失败码）刻意分开：
循环只发 canonical 请求、只收 canonical 结果，**发到哪个端点、用什么凭据、payload 长什么
样，它一概不知道**。换 provider = 换一个实现，循环一行不改。

``retry_policy`` 沿用 A 的分工（providerRetryPolicy）：**策略归 provider、执行归循环**。
装配层在没传 ``hooks.request_error`` 时把它塞进 LoopHooks —— 于是「换 provider 顺带换
退避曲线」也不用动 agent_loop.py。
"""
from collections.abc import AsyncIterator
from typing import Protocol

from backend.agents.runtime.llm.hooks import RequestErrorHook
from backend.agents.runtime.llm.types import LlmRequest, LlmResponse, StreamChunk


class LlmClient(Protocol):
    """一个 provider 端点 + 一份凭据的客户端。"""

    @property
    def configured(self) -> bool:
        """凭据与端点是否齐备 —— 循环据此决定「这一轮跑不跑」（不再自己看 api_key）。"""

    @property
    def retry_policy(self) -> RequestErrorHook:
        """provider 声明的默认重试策略。

        装配层把它当 ``hooks.request_error`` 传进 LoopHooks（除非装配方给了自己的钩子）；
        循环照旧负责循环、``asyncio.sleep`` 与截止约束 —— 策略与执行的分工不变。
        """

    async def complete(self, request: LlmRequest) -> LlmResponse:
        """发一次非流式请求。任何失败都抛 ``LlmError``（要不要重试由策略裁决）。"""

    def stream(self, request: LlmRequest) -> AsyncIterator[StreamChunk]:
        """发一次流式请求，逐分片产出。

        契约两条：**不写 Session 事件**（落库是循环的事，见 loop/assistant_stream.py）、
        **不吞取消**（``asyncio.CancelledError`` 原样穿透 —— 由循环判定它是协作取消
        （转 RunCancelled + 交出半截正文）还是硬取消）。
        """
