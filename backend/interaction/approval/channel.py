"""进程内审批通道 —— :class:`ApprovalChannel` 的一种实现

``request()`` 挂起，裁决由 API 端点经 ``resolve()`` 唤醒：

    调用方（挂起）                    裁决方
      │                                │
      ├─ request(req) ── 事件推送 ────►│ UI 显示待确认
      │                                │
      │   await future ◄───────────────┤ POST /api/agents/approvals/{id}
      ▼                                │
    ApprovalOutcome ───────────────────┘

本模块**只回答「怎么等一个回答」**：不认识沙箱、不认识工具、不认识 session。

三条 fail-closed 规则（缺一不可）：

  - **超时 → ``unavailable``**。没有回应不等于同意。
  - **调用方取消 → 向上抛 ``CancelledError``**（不是返回结果）。调用链正在被
    拆除，必须让取消继续传播；挂起项靠 ``finally`` 清理，不留悬空授权。
  - **未知裁决值 → ``rejected``**。未知不是「还没决定」，不能让它永远悬着。
"""
import asyncio
import logging

from backend.interaction.approval.models import ApprovalOutcome, ApprovalRequest

logger = logging.getLogger(__name__)

#: 默认等待上限（秒）。超时按 ``unavailable`` 处理。
DEFAULT_APPROVAL_TIMEOUT_S = 300.0

#: 裁决方可提交的裁决值 → 通道结果。
#: ``cancel`` 留给「用户主动关掉提示」，与「超时」区分开（审计上不是一回事）。
RESOLVABLE_DECISIONS: dict[str, ApprovalOutcome] = {
    "allow-once": "allowed-once",
    "reject": "rejected",
    "cancel": "cancelled",
}


class InProcessApprovalChannel:
    """进程内通道：单进程单事件循环限定。

    与 ``SessionStore`` / ``ToolRegistry`` 同级的运行态组件。多副本部署要换成
    共享存储 —— 否则裁决落不到挂起的那台，只能等超时。
    """

    def __init__(self, timeout_s: float = DEFAULT_APPROVAL_TIMEOUT_S) -> None:
        self._timeout_s = timeout_s
        #: approval_id → (待裁决申请, 唤醒用 future)
        self._pending: dict[str, tuple[ApprovalRequest, asyncio.Future]] = {}

    async def request(self, req: ApprovalRequest) -> ApprovalOutcome:
        """挂起直到有人裁决或超时。**所有返回路径都是 fail-closed。**"""
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req.approval_id] = (req, fut)
        try:
            return await asyncio.wait_for(fut, timeout=self._timeout_s)
        except asyncio.TimeoutError:
            logger.warning(
                f"审批超时（{self._timeout_s:.0f}s），按不可用处理: "
                f"{req.tool_name or '-'} / {req.reason} (approval_id={req.approval_id})"
            )
            return "unavailable"
        except asyncio.CancelledError:
            # 客户端断连 / 服务关停：授权不能悬空，注册表也不能留残项
            logger.info(f"审批被取消: approval_id={req.approval_id}")
            raise
        finally:
            self._pending.pop(req.approval_id, None)

    def resolve(self, approval_id: str, decision: str) -> bool:
        """裁决一个待确认的申请（API 端点调用）。

        Args:
            decision: :data:`RESOLVABLE_DECISIONS` 的键；未知值按 ``rejected``
                处理 —— 失败的方向必须是「不放行」。

        Returns:
            True 表示裁决送达；False 表示该 id 不存在或已被处理（超时 / 取消）。
            重复点击与过期点击都走这里，调用方据此回 404 而不是静默成功。
        """
        entry = self._pending.get(approval_id)
        if entry is None:
            return False
        _, fut = entry
        if fut.done():
            return False
        fut.set_result(RESOLVABLE_DECISIONS.get(decision, "rejected"))
        return True

    def pending(self) -> list[ApprovalRequest]:
        """当前待裁决的申请（冷恢复用：进程重启后挂起全消失，前端据此清陈旧卡片）。"""
        return [req for req, _ in self._pending.values()]
