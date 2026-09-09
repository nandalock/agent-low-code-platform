"""SandboxModeProjection — Session typed events → 会话级沙箱模式（Config Projection）

Event Log 是唯一事实源；本模块把 ``sandbox/mode`` 事件 fold 成「当前有效覆盖」，
是「Session Event → 配置投影」这一派生消费者，不是第二套事实：

  - 不执行、不约束，只消费已经发生的 SessionEvent。
  - fold 形态是 **find-last**：取最后一条 ``sandbox/mode`` 的值；无事件 → None，
    调用方回落到部署默认（优先级链见 tool_system/sandbox/policy.py）。
  - ``project_sandbox_mode(events)`` 与增量 ``handle`` 严格同构。

与 trace_projection / trajectory_projection 并列：三者都消费同一 Event Log、
互不依赖；命名 = "<用途>_projection"。
"""
from backend.agents.runtime.session.events import SANDBOX_MODE, SessionEvent
from backend.tool_system.sandbox.vocabulary import ALL_MODES


class SandboxModeProjection:
    """增量投影：逐条消费 SessionEvent，维护「最后一条 sandbox/mode」。"""

    def __init__(self):
        self._mode: str | None = None

    def handle(self, ev: SessionEvent) -> None:
        """消费一条 SessionEvent（Session listener 签名，忽略返回值）。"""
        if ev.type != SANDBOX_MODE:
            return
        mode = ev.data.get("mode")
        # 词汇封闭性在写入侧校验；这里是读取侧防御（非法值忽略，不猜测）
        if mode in ALL_MODES:
            self._mode = mode

    def snapshot(self) -> str | None:
        """当前覆盖模式；无覆盖返回 None（调用方回落到部署默认）。"""
        return self._mode


def project_sandbox_mode(events: list[SessionEvent]) -> str | None:
    """回放 fold：``project_sandbox_mode(events) == 逐条 handle + snapshot``。

    冷恢复路径：Session.from_events 重建后对历史 Event Log fold 得覆盖值，
    与实时增量投影产出必须完全一致。
    """
    p = SandboxModeProjection()
    for ev in events:
        p.handle(ev)
    return p.snapshot()
