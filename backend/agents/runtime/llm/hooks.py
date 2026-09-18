"""循环拦截通道的契约（对齐 A 的四个 waterfall，见 docs/agent-loop-design.md §6）

**两条通道刻意不合并**（§6.1）：

  观察 — ``on_event``（UI 帧）与 ``session_event_sink``（typed 事件 → 投影）：
         只读广播，不改变执行。观察者可以随便加，坏了也不能影响执行。
  拦截 — 本模块的 ``LoopHooks``：在指定点上**改变执行决策**。拦截者直接决定执行
         走向，所以必须由装配方显式传入、并对后果负责；不传 = 现行为。

A 用 Cordis 的 waterfall 洋葱模型做拦截（``agent.ts`` 发事件，``llm-retry`` 插件挂
监听）。B 没有插件总线，也不需要：``hooks`` 就是一个 dataclass，**每点至多一个
可调用对象**，多元件要接同一点时由装配方自己组合。钩子拿到的是与 A 同形的决策
出口，换策略不需要改 agent_loop.py。

规矩（§6.3，违反即契约破损）：

  1. 钩子抛异常 → Loop 记 exception、**按默认行为继续**，拦截器坏掉不能打死执行
     链（与 listener 同规）。唯一例外是取消：``asyncio.CancelledError`` 照常穿透。
  2. **钩子不产生事实**。Event Log 只由 Loop 写；钩子的决策通过既有事件体现
     （如 request_error 的重试由 Loop 落 ``llm/error``）。
  3. 闭合不受钩子影响：``turn/end`` / ``step/end`` 仍在 ``finally`` 里，钩子再离谱
     也不会留下半途 turn。
  4. 钩子是 async（允许 await IO），但每点每步最多调用一次；不得阻塞事件循环。
  5. **不改变 stop_reason 词表**：钩子想表达「我否决了停止」，手段是注入输入让轮次
     继续，而不是发明新的停止原因。
"""
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from backend.agents.runtime.llm.errors import LlmError


@dataclass(frozen=True)
class RetryDecision:
    """``request_error`` 的裁决：**返回它 = 就按这个退避重试**，返回 None = 放弃。

    把「等多久」和「重不重试」放在同一个返回值里，是让退避策略也能外置的关键 ——
    否则改退避曲线仍要动 agent_loop.py。对齐 A 的 ``RequestErrorAction``
    （``{kind: 'retry'}`` / ``undefined``），只是把 delay 从策略内部提到了契约上。

    ``reason`` 只进日志，**不落事实**（§6.3 规矩 2）。
    """

    delay: float = 0.0
    reason: str = ""


#: ``request_error`` 钩子：一次失败的请求 + 第几次尝试（0 起）→ 裁决。
#: A 的对应物是 ``agent.ts`` 的 ``agent/request-error`` 瀑布。
RequestErrorHook = Callable[[LlmError, int], Awaitable[RetryDecision | None]]


@dataclass(frozen=True)
class LoopHooks:
    """一次 AgentLoop 运行的拦截点集合；字段为 None = 该点走现行为。

    §6.2 一共定了四个点（``pre_step`` / ``request`` / ``request_error`` /
    ``turn_stopping``）。本轮只落地 ``request_error`` —— 重试策略是它的第一个
    内置消费者（§6.4 第 3 条）。其余三点的契约已定、实现排后，届时在此处加字段，
    AgentLoop 的构造签名不用再动。
    """

    #: 重试策略外置点。None = AgentLoop 用内置默认策略（llm_retry.make_retry_policy，
    #: 即改造前的现行为：RETRYABLE_CODES + max_llm_retries），行为零变化。
    request_error: RequestErrorHook | None = None
