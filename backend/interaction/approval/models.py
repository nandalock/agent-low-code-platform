"""审批的共享词汇 —— 通用的人机确认能力

**本层不认识任何业务域**：没有 `SandboxMode`、没有 `WIDER_MODES`、没有
escalation。调用方需要携带领域信息时放进 :attr:`ApprovalRequest.metadata`，
审批侧只做透传（它不解释 metadata 的内容，就像 ToolContext 不解释 context）。

一次审批的形状：

    ApprovalRequest   谁（agent）要用什么工具（tool_name / tool_call_id）
                      做什么（reason）+ 领域附加信息（metadata）
        ↓ channel.request(req)
    ApprovalOutcome   allowed-once / rejected / cancelled / unavailable

**一次性授权**：``allowed-once`` 只对发起它的那一次调用有效。审批侧不缓存
决定、不写持久权限 —— 需要再次确认时调用方重新发起即可。

**没有「不问就放」这一档**：审批策略只有 ask（问人）与 never（不问，直接拒）。
没有通道时调用方应当以「不可用」处理，而不是默认放行。
"""
import uuid
from dataclasses import dataclass, field
from typing import Literal, Protocol

#: 审批方的回答。
#:   allowed-once —— 一次性放行本次调用（唯一表示「同意」的值）
#:   rejected     —— 人明确拒绝
#:   cancelled    —— 审批过程被取消（用户主动关掉提示）
#:   unavailable  —— 审批方不可用（含超时）；**没有回应不等于同意**
ApprovalOutcome = Literal["allowed-once", "rejected", "cancelled", "unavailable"]

#: 只有这一个值表示同意 —— 其余一律 fail-closed，未知值也不例外。
ALLOWED: ApprovalOutcome = "allowed-once"


@dataclass(frozen=True)
class ApprovalRequest:
    """一次待确认的申请。

    字段刻意保持领域中立：``reason`` 是给人看的一句话（调用方自己拼），
    ``metadata`` 放领域细节（沙箱把 from/to 模式放进去），审批侧不解释其内容。
    """

    approval_id: str
    reason: str                              # 给人看的一句话；审批方原样展示
    tool_name: str = ""
    tool_call_id: str | None = None
    agent: object | None = None              # 发起方标识；None 表示调用链未携带
    metadata: dict = field(default_factory=dict)
    signal: object | None = None             # 取消信号（可选，实现方自定语义）

    def to_dict(self) -> dict:
        """API / UI 视图。agent 用 repr 兜底 —— 它是任意对象，不该泄进 JSON。"""
        return {
            "approval_id": self.approval_id,
            "reason": self.reason,
            "tool": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "agent": None if self.agent is None else str(self.agent),
            "metadata": self.metadata,
        }


class ApprovalChannel(Protocol):
    """审批通道协议（结构化类型，不要求继承）。

    实现方负责「怎么问人」——进程内 future、消息队列、外部审批服务都行。
    沙箱之类的消费者**只依赖这个协议**，不认识任何具体实现。
    """

    async def request(self, req: ApprovalRequest) -> ApprovalOutcome: ...


def new_approval_id() -> str:
    """审批 id：短、无歧义、够唯一（在一次审批的生命周期内配对申请与结果）。"""
    return uuid.uuid4().hex[:12]
