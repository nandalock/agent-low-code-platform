"""沙箱升权 —— 完整升权链（DSH ``approveEscalation`` 同构）

移植自 DeepSeek Harness `dsh-bash-sandbox` 的升权链。**本模块自己走完整条链**，
不把「判断」与「审批」拆成两个模块、不产出中间状态：

    模型请求 sandbox_permissions
        ↓
    validate_escalation_args()      形状：两参数成对 + 理由非空整句
        ↓
    归一化                          未请求 / 等同当前模式 → 返回 None（无事发生）
        ↓
    严格更宽                        查 :data:`WIDER_MODES`，同级 / 降级 / 未知一律拒
        ↓
    检查 approval service           无 → EscalationDenied(no-approval-channel)
        ↓
    检查 agent                      无 → EscalationDenied(no-agent)
        ↓
    approval.request(req)           构造 reason：``escalate sandbox to <mode>: <理由>``
        ↓                           （审批本身归 interaction.approval，见下）
    allowed-once → 返回目标 SandboxMode
    rejected     → EscalationDenied(human-refused)
    cancelled    → EscalationDenied(cancelled)
    unavailable  → EscalationDenied(unavailable)

**没有输出状态机。** 审批不是状态，是**一次调用流程**：成功返回 mode、失败抛异常、
没这回事返回 ``None``。调用方因此不需要 ``needs_human`` / ``pending`` 之类的中间态。

**一次性授权不靠状态实现。** 返回的 mode 只属于本次 tool call —— 本模块不保存
granted mode、不做 approval cache、不写任何持久权限。

**审批能力不在本模块，也不在 sandbox 里**：它归
:mod:`backend.interaction.approval`（通用的人机确认能力）。本模块只做两件事 ——
判断「这次要升到哪一档」，以及把沙箱的模式信息装进 ``ApprovalRequest.metadata``
后调用 ``approval.request()``。依赖方向单向：
**sandbox/escalation → interaction/approval**，反过来不成立。

**没有「不问就放」这一档。** 上游的审批策略只有 ``ask``（问人）与 ``never``
（不问，直接拒）；没有通道时升权恒不可用。**没有判定方 ≠ 默认批准**。

**归一化的由来**（不是可选优化）：DSH 的升权字段在 registry 里全局 advertise，
而生效模式是逐调用的 —— 会话已在最宽模式时模型仍看得到字段 → 反射性填上 →
每次被「不严格更宽」打回 → 烧 token、甚至死循环重试（deepseek-harness
#4359 / #4383）。归一化前移到校验最前面（DSH 的 ``normalizeEscalationMode``
同样在 ``validateEscalationArgs`` 之前），使「等同当前模式的请求」零成本通过。
"""
from backend.interaction.approval import (
    ALLOWED,
    ApprovalChannel,
    ApprovalRequest,
    new_approval_id,
)
from backend.tool_system.sandbox.vocabulary import SandboxMode

# ── 词汇（封闭） ──

#: ``sandbox_permissions`` 的合法取值 —— 工具 schema 里的 enum，也是本模块
#: 接受的请求目标。**不含 ``read-only``**：升权只往更宽走，请求最窄模式
#: 没有意义（DSH 的 enum 同样只有这两项）。
ESCALATION_TARGETS: tuple[str, ...] = ("workspace-write", "danger-full-access")

#: 严格更宽阶梯 —— 只有表里列出的目标才是合法升权。
#: ``danger-full-access`` 刻意**无条目**：它已是最宽，没有可升的目标
#: （与 DSH 的 ``WIDER_MODES`` 同构，含「缺条目」这一点）。
WIDER_MODES: dict[str, tuple[str, ...]] = {
    "read-only": ("workspace-write", "danger-full-access"),
    "workspace-write": ("danger-full-access",),
}


# ── 模型可见标记 ──

#: 被拒绝的调用在结果文本里携带的标记。
DENIAL_MARKER_TEMPLATE = "[sandbox: file access denied under {mode} mode]"

#: 升权提示 —— 在使用点告诉模型「这条路存在，以及怎么走」。措辞照搬 DSH。
#: 尾句 ``the approval prompt asks the user`` 恒出现：每一次升权都要问人，
#: 没有不问就放的档。
ESCALATION_HINT = (
    "[sandbox: escalation available — retry this exact command once with "
    "sandbox_permissions (the narrowest wider mode that suffices) + "
    "justification; the approval prompt asks the user]"
)


def sandbox_denial_marker(mode: str) -> str:
    """被拒调用结果里的标记（模型可见）。

    与 :func:`escalation_hint_marker` 是一对：同在使用点出现，**都不进系统
    提示词**（原则 7 —— 常驻提示词会让模型变保守，DSH 实测 12 轮里 5 轮零
    工具调用）。
    """
    return DENIAL_MARKER_TEMPLATE.format(mode=mode)


def escalation_hint_marker(mode: str, *, can_ask_human: bool) -> str:
    """被拒调用尾部的升权提示；不该提示时返回空串。

    两种「不该提示」：

    - ``can_ask_human=False`` —— 没有审批通道，升权恒不可用（等价 DSH 的
      ``never``）。提示一条必然失败的路只会让模型白烧一轮。
    - 已在最宽模式 —— ``WIDER_MODES`` 无条目，同样没有可升的目标。

    调用方在**本次调用刚被拒过升权**时也必须抑制它（否则等于请模型重试，
    见 ``SandboxExecutor`` 的 notice 构造）。
    """
    if not can_ask_human or not WIDER_MODES.get(mode):
        return ""
    return ESCALATION_HINT


# 审批的能力与词汇都不在本模块：见 backend/interaction/approval
# （ApprovalRequest / ApprovalOutcome / ApprovalChannel 协议）。
# 本模块只**消费**它 —— 沙箱的模式信息装进 ApprovalRequest.metadata。


# ── 异常 ──


class EscalationError(Exception):
    """升权未完成。``reason`` 机器可读，供审计事件使用。

    携带 ``requested`` / ``justification`` / ``approval_id`` 是为了让调用方
    在异常路径上仍能落一条完整的审计事实 —— 否则「谁在什么时候申请了什么、
    为什么没批」就只剩错误文本可查。
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        requested: str | None = None,
        justification: str = "",
        approval_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.requested = requested
        self.justification = justification
        self.approval_id = approval_id


class EscalationInvalid(EscalationError):
    """请求本身非法 —— **不打扰人**。

    DSH 把 ``validateEscalationArgs()`` 放在 ``approveEscalation()`` 之前，
    正是为了让这一档不产生审批提示：非法请求不值得占用人的注意力。
    """


class EscalationDenied(EscalationError):
    """请求合法，但「要审批」这件事没成。

    五种来源：没有审批通道（``no-approval-channel``）、没有 agent
    （``no-agent``）、人拒绝（``human-refused``）、审批被取消（``cancelled``）、
    审批方不可用含超时（``unavailable``）。**全部 fail-closed** ——
    没有判定方、没有回应、说不清楚，一律按不放行处理。
    """


# ── 校验（导出：schema 生成与测试直接使用） ──


def validate_escalation_args(args: dict) -> tuple[str | None, str]:
    """形状校验 —— 两参数成对、理由是非空整句。

    Args:
        args: 一次沙箱工具调用的原始参数。

    Returns:
        ``(requested_mode, justification)``；两者都缺省时返回 ``(None, "")``
        （普通调用，不是错误）。

    Raises:
        EscalationInvalid: 配对缺失或理由为空。三条错误文本逐字照搬 DSH
            ——它们是模型可见的协议反馈，措辞本身就是修正指引。
    """
    raw_perm = args.get("sandbox_permissions")
    raw_just = args.get("justification")

    if raw_perm is None and raw_just is None:
        return None, ""

    if raw_perm is None:
        raise EscalationInvalid(
            "invalid escalation: justification is only valid together with "
            "sandbox_permissions",
            reason="invalid-pairing",
            justification=_as_text(raw_just),
        )

    if raw_just is None or not isinstance(raw_just, str):
        raise EscalationInvalid(
            "invalid escalation: sandbox_permissions requires a justification",
            reason="missing-justification",
            requested=str(raw_perm),
        )

    if not raw_just.strip():
        raise EscalationInvalid(
            "invalid justification: expected a non-empty sentence",
            reason="empty-justification",
            requested=str(raw_perm),
        )

    return str(raw_perm), raw_just.strip()


def assert_strictly_wider(requested: str, current: str) -> None:
    """严格更宽检查 —— 目标必须是当前模式的严格更宽者。

    同级、降级、以及词汇外的值一律拒绝：未知值不在表里，自然 fail-closed，
    无需单独的存在性校验。

    Raises:
        EscalationInvalid: 不在 ``WIDER_MODES[current]`` 内。
    """
    if requested not in WIDER_MODES.get(current, ()):
        raise EscalationInvalid(
            f'sandbox escalation to "{requested}" is not strictly wider than '
            f'this call\'s current "{current}" mode',
            reason="not-strictly-wider",
            requested=requested,
        )


# ── 升权链 ──


async def approve_escalation(
    args: dict,
    *,
    current: str,
    approval: ApprovalChannel | None,
    agent: object | None = None,
    tool_name: str = "",
    tool_call_id: str | None = None,
    on_request=None,
) -> SandboxMode | None:
    """走完整条升权链（见模块头）。

    Args:
        args: 本次工具调用的原始参数。
        current: 本次调用当前生效的沙箱模式。
        approval: 审批通道；``None`` = 当前部署没有裁决方 → 直接拒绝。
            实现 :class:`ApprovalChannel` 协议的任意对象都行（通常是
            ``interaction.approval.ApprovalService``）。
        agent / tool_name / tool_call_id: 转交给审批侧的上下文事实。
        on_request: 可选异步回调，在**挂起等待审批之前**调用一次，参数是
            :class:`ApprovalRequest`。存在的理由：审批发生在本函数内部，
            调用方若不能在挂起前把申请推给 UI，用户就永远看不到「正在等谁批」。

    Returns:
        获批的目标模式；``None`` 表示**没这回事**（未请求 / 请求等同当前模式）。

    Raises:
        EscalationInvalid: 请求本身非法（形状 / 不严格更宽）。
        EscalationDenied: 合法但没批下来（无通道 / 无 agent / 被拒 / 取消 / 不可用）。
    """
    requested, justification = validate_escalation_args(args)

    # 归一化：未请求，或请求等同当前模式 → 没这回事。
    # 只归一化**等同**，不归一化**更窄** —— 降级请求仍要走下面的严格更宽检查
    # 被拒（变窄不是升权，不该被静默接受）。
    if requested is None or requested == current:
        return None

    assert_strictly_wider(requested, current)

    if approval is None:
        raise EscalationDenied(
            f'sandbox escalation to "{requested}" needs human approval, but no '
            f"approval channel is configured in this deployment",
            reason="no-approval-channel",
            requested=requested,
            justification=justification,
        )

    if agent is None:
        raise EscalationDenied(
            f'sandbox escalation to "{requested}" needs an agent to ask, but none '
            f"was provided",
            reason="no-agent",
            requested=requested,
            justification=justification,
        )

    req = ApprovalRequest(
        approval_id=new_approval_id(),
        # 给人看的一句话，DSH 的拼法 —— 模型自己写的理由就是它举证的全部内容
        reason=f"escalate sandbox to {requested}: {justification}",
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        agent=agent,
        # 领域信息：审批侧不解释，只透传给 UI
        metadata={
            "kind": "sandbox-escalation",
            "from": current,
            "to": requested,
            "justification": justification,
        },
    )
    if on_request is not None:
        await on_request(req)

    outcome = await approval.request(req)
    if outcome == ALLOWED:
        # 一次性授权：只属于本次调用，本模块不保存任何东西
        return requested  # type: ignore[return-value]

    raise EscalationDenied(
        _denied_message(outcome, requested),
        reason=_DENIED_REASONS.get(outcome, "unavailable"),
        requested=requested,
        justification=justification,
        approval_id=req.approval_id,
    )


#: 审批结果 → 机器可读原因。``allowed-once`` 不在表里（它不产生异常）。
_DENIED_REASONS: dict[str, str] = {
    "rejected": "human-refused",
    "cancelled": "cancelled",
    "unavailable": "unavailable",
}


def _denied_message(outcome: str, requested: str) -> str:
    if outcome == "rejected":
        return f'sandbox escalation to "{requested}" was refused by the approver'
    if outcome == "cancelled":
        return f'sandbox escalation to "{requested}" was cancelled during approval'
    return (
        f'sandbox escalation to "{requested}" could not be approved: '
        f"no approver was available (timeout or unavailability)"
    )


def _as_text(value) -> str:
    return value.strip() if isinstance(value, str) else ""
