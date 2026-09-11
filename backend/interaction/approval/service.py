"""审批服务 —— 调用方唯一认识的入口（进程级单例）

    sandbox/escalation  ──┐
    （将来其他消费者）    ──┼──► ApprovalService ──► ApprovalChannel
                          ─┘        （装配 + 可用性）

**为什么有这一层**：消费者只该依赖「有没有人可问」和「怎么问」，不该去管
通道是怎么装配的。服务把这两件事收在一起：

  - ``available`` —— 当前部署有没有裁决方。没有时消费者应当 fail-closed，
    而不是发起一次必然失败的请求。
  - ``request()`` —— 转发给通道（本类因此满足 :class:`ApprovalChannel` 协议，
    消费者可以直接把它当作通道使用）。

**没有「不问就放」这一档**：``available=False`` 的含义是「升权/确认恒不可用」
（等价 DSH 的 ``approval=never``），而不是「默认同意」。
"""
import logging

from backend.interaction.approval.channel import (
    DEFAULT_APPROVAL_TIMEOUT_S,
    InProcessApprovalChannel,
)
from backend.interaction.approval.models import ApprovalOutcome, ApprovalRequest

logger = logging.getLogger(__name__)


class ApprovalService:
    """审批通道 + 可用性查询。实现 :class:`ApprovalChannel` 协议。"""

    def __init__(self, channel: InProcessApprovalChannel) -> None:
        self._channel = channel

    @property
    def available(self) -> bool:
        """当前部署有没有裁决方。

        消费者用它决定「要不要向模型宣传这条路」—— 提示一条必然失败的路径
        只会让它白烧一轮。
        """
        return True

    async def request(self, req: ApprovalRequest) -> ApprovalOutcome:
        return await self._channel.request(req)

    def resolve(self, approval_id: str, decision: str) -> bool:
        return self._channel.resolve(approval_id, decision)

    def pending(self) -> list[ApprovalRequest]:
        return self._channel.pending()


# ── 进程级单例（与 sandbox provider 同模式：装配层写入，消费方只读） ──

_service: ApprovalService | None = None


def set_approval_service(service: ApprovalService | None) -> None:
    global _service
    _service = service


def get_approval_service() -> ApprovalService | None:
    """当前审批服务；未装配返回 None（消费者将 fail-closed）。"""
    return _service


def init_approval_service(timeout_s: float = DEFAULT_APPROVAL_TIMEOUT_S) -> ApprovalService:
    """装配审批服务（``main.py`` 启动时调用）。"""
    service = ApprovalService(InProcessApprovalChannel(timeout_s=timeout_s))
    set_approval_service(service)
    logger.info(f"审批服务已装配（超时 {timeout_s:.0f}s，超时按不可用处理）")
    return service


def can_ask_human() -> bool:
    """当前有没有可用的裁决方（等价 ``get_approval_service() is not None``）。

    给「要不要提示模型可以申请升权」这类判断用。
    """
    return _service is not None
