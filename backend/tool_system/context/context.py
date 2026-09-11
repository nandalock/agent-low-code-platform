"""ToolContext — Tool 执行的运行时上下文（Agent Runtime → Tool 的只读上下文）

由 AgentLoop 在发起 Tool 调用时构造，沿
    AgentLoop → ToolRuntime → Executor → Tool
单向传递。本阶段只做「携带」，不做任何解释：

  - 不实现权限 / RBAC 判定（后续阶段）
  - 不实现事件发送（event_sink 仅预留出口，Executor 不使用）
  - 不参与 Registry（Registry 只认 metadata，不感知 context）
  - 不改变 MCP 调用语义（MCP Server 无上下文概念，context 不进入 args）

字段全部可选：调用链当前未携带的能力（user_id / trace_id）为 None；
Tool / Executor 必须能在 context 为 None 时照常工作。
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.tool_system.sandbox.vocabulary import SandboxExecutionPolicy

if TYPE_CHECKING:  # 只用于类型标注：运行时不 import，避免 context 层绑定具体能力
    from backend.interaction.approval import ApprovalChannel


@dataclass(frozen=True)
class ToolContext:
    """一次 Tool 调用的运行时上下文（构造后只读）"""

    session_id: str | None = None   # Agent 多轮 Session id（Event Log 归属）
    tool_call_id: str | None = None  # 本次调用的 id（事件关联 / 审批卡片与调用配对）
    agent_id: str | None = None     # 发起调用的 Agent key
    user_id: str | None = None      # 终端用户标识（当前调用链未携带 → None）
    trace_id: str | None = None     # 运行追踪 id（当前调用链未携带 → None）
    event_sink: Any | None = None   # 预留：Tool 事件出口（本阶段不发送任何事件）
    # 本会话一次调用的沙箱执行策略（AgentLoop 解析后下传）。非沙箱工具忽略；
    # 沙箱工具缺失该字段时 fail-closed（不执行）。
    sandbox_policy: SandboxExecutionPolicy | None = None
    # 审批通道（interaction.approval 的通用能力，满足 ApprovalChannel 协议：
    # `async request(req) -> ApprovalOutcome`）。AgentLoop 装配后下传；
    # None = 当前部署没有裁决方 → 需要审批的能力（如沙箱升权）fail-closed 到拒绝。
    # 本层只携带、不解释；使用方（沙箱执行器）自己决定要不要用。
    approval: "ApprovalChannel | None" = None
