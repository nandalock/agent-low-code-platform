"""approval —— 通用的人工审批能力

「这个操作要不要打断人、怎么问、怎么等回答」。**不认识任何业务域** ——
沙箱之类的调用方自己把领域信息塞进 ``ApprovalRequest.metadata``。

分层：

    models.py    共享词汇：ApprovalRequest / ApprovalOutcome / ApprovalChannel 协议
    channel.py   InProcessApprovalChannel —— 一种「怎么等一个回答」的实现
    service.py   ApprovalService 单例 —— 消费者唯一认识的入口

依赖方向是单向的：**业务侧 import 本模块，本模块不 import 任何业务侧**。
"""
from backend.interaction.approval.channel import (
    DEFAULT_APPROVAL_TIMEOUT_S,
    RESOLVABLE_DECISIONS,
    InProcessApprovalChannel,
)
from backend.interaction.approval.models import (
    ALLOWED,
    ApprovalChannel,
    ApprovalOutcome,
    ApprovalRequest,
    new_approval_id,
)
from backend.interaction.approval.service import (
    ApprovalService,
    can_ask_human,
    get_approval_service,
    init_approval_service,
    set_approval_service,
)

__all__ = [
    "ALLOWED",
    "DEFAULT_APPROVAL_TIMEOUT_S",
    "RESOLVABLE_DECISIONS",
    "ApprovalChannel",
    "ApprovalOutcome",
    "ApprovalRequest",
    "ApprovalService",
    "InProcessApprovalChannel",
    "can_ask_human",
    "get_approval_service",
    "init_approval_service",
    "new_approval_id",
    "set_approval_service",
]
