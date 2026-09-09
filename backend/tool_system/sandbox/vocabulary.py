"""沙箱词汇 —— 所有层共享的类型定义（无逻辑）

移植自 DeepSeek Harness `packages/sandbox/sandbox/src/index.ts` 的
`SandboxMode` / `ConfinedSandboxMode` / `SandboxEnforcement` /
`SandboxExecutionPolicy` / `SandboxPolicy`。

词汇只覆盖**文件效果**：read-only / workspace-write / danger-full-access。
网络、进程可见性、syscall、设备、凭据均不在词汇内——词汇越窄，后端能兑现
的可能性越高（设计依据见 docs/sandbox-design.md §3）。

本模块只定义类型与构造期校验，不含执行逻辑；任何层都可以 import 它，
但谁都不拥有它。
"""
from dataclasses import dataclass
from typing import Literal

# 完整的文件效果模式。danger-full-access 的消费方直接 spawn 原始 argv，不走 provider。
SandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]

# 可以交给 provider 的模式子集。
ConfinedSandboxMode = Literal["read-only", "workspace-write"]

# 强制执行完整度：后端报告的**事实**，不是承诺。
#   full    —— 后端管辖了该模式承诺的每一种文件效果
#   partial —— 只管辖其中一个子集（旧内核 ABI、平台固有限制）
SandboxEnforcement = Literal["full", "partial"]

ALL_MODES: frozenset[str] = frozenset({"read-only", "workspace-write", "danger-full-access"})
CONFINED_MODES: frozenset[str] = frozenset({"read-only", "workspace-write"})


@dataclass(frozen=True)
class SandboxExecutionPolicy:
    """一次能力调用解析出的完整策略。

    即使在不消费 root 的模式下也携带 workspace_root，使调用方可以只解析
    一次策略、再决定走哪条强制执行路径。
    """

    mode: SandboxMode
    workspace_root: str            # 宿主绝对路径；workspace-write 的可写边界
    session_id: str | None = None  # 调用会话标识；后端按会话维护状态时使用


@dataclass(frozen=True)
class SandboxPolicy(SandboxExecutionPolicy):
    """一次受限执行真正允许触碰的东西 —— 逐调用携带，不固定在 provider 上。

    两个消费方可以在同一时刻以不同策略约束；获批的升权重试是一次带更宽
    策略的新调用。默认值解析是消费方边界的显式步骤，provider 视策略为
    完全指定。
    """

    mode: ConfinedSandboxMode

    def __post_init__(self) -> None:
        if self.mode not in CONFINED_MODES:
            raise ValueError(
                f"非法沙箱模式: {self.mode!r}；受限策略只能取 {sorted(CONFINED_MODES)}"
            )
        if not self.workspace_root:
            raise ValueError("workspace_root 不能为空")


def is_confined_mode(mode: str) -> bool:
    """该模式是否需要经过 provider 约束。"""
    return mode in CONFINED_MODES
