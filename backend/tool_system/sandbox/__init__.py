"""沙箱子系统 —— seam 与词汇。

对外只暴露 seam 词汇与抽象 provider；具体后端（如 DockerProvider）从
``backends`` 显式导入，避免上层依赖某个平台实现。

设计依据见 docs/sandbox-design.md。
"""
from backend.tool_system.sandbox.classify import (
    Outcome,
    classify_outcome,
    classify_runner_failure,
    matches_signature,
)
from backend.tool_system.sandbox.errors import SANDBOX_UNAVAILABLE, SandboxUnavailableError
from backend.tool_system.sandbox.escalation import (
    ESCALATION_TARGETS,
    WIDER_MODES,
    EscalationDenied,
    EscalationError,
    EscalationInvalid,
    approve_escalation,
    escalation_hint_marker,
    sandbox_denial_marker,
)
from backend.tool_system.sandbox.provider import ConfinedArgv, RunnerFailureRule, SandboxProvider
from backend.tool_system.sandbox.vocabulary import (
    ALL_MODES,
    CONFINED_MODES,
    ConfinedSandboxMode,
    SandboxEnforcement,
    SandboxExecutionPolicy,
    SandboxMode,
    SandboxPolicy,
    is_confined_mode,
)

__all__ = [
    "ALL_MODES",
    "CONFINED_MODES",
    "ConfinedArgv",
    "ConfinedSandboxMode",
    "ESCALATION_TARGETS",
    "Outcome",
    "RunnerFailureRule",
    "SANDBOX_UNAVAILABLE",
    "WIDER_MODES",
    "EscalationDenied",
    "EscalationError",
    "EscalationInvalid",
    "SandboxEnforcement",
    "SandboxExecutionPolicy",
    "SandboxMode",
    "SandboxPolicy",
    "SandboxProvider",
    "SandboxUnavailableError",
    "approve_escalation",
    "classify_outcome",
    "classify_runner_failure",
    "escalation_hint_marker",
    "is_confined_mode",
    "matches_signature",
    "sandbox_denial_marker",
]
