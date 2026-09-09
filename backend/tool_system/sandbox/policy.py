"""沙箱策略解析 —— 纯函数，不含会话依赖

对应 DeepSeek Harness `packages/sandbox/sandbox-policy/src/index.ts` 的
`SandboxPolicyService.resolve()`，但按本仓库分层做成纯函数：``tool_system``
不依赖 ``agents`` 层，会话相关的输入（会话覆盖、cwd）由调用方提供。

优先级（高 → 低）：

    显式模式（升权重试） > 会话覆盖（sandbox/mode 事件投影） > 部署默认 > 内置兜底

优先级规则只有这一份实现，bash 与 python 消费方都不重复。
"""
from backend.tool_system.sandbox.vocabulary import (
    ALL_MODES,
    SandboxExecutionPolicy,
    SandboxMode,
)

#: 部署默认模式的环境变量名。
DEFAULT_MODE_ENV = "SANDBOX_DEFAULT_MODE"

#: 三者都未提供时的兜底模式。
FALLBACK_MODE: SandboxMode = "workspace-write"


def validate_mode(mode: str) -> SandboxMode:
    """词汇封闭性校验。

    Raises:
        ValueError: 不在 ``SandboxMode`` 的封闭取值内。
    """
    if mode not in ALL_MODES:
        raise ValueError(f"非法沙箱模式: {mode!r}；可选 {sorted(ALL_MODES)}")
    return mode  # type: ignore[return-value]


def resolve_mode(
    *,
    explicit_mode: SandboxMode | None = None,
    session_override: SandboxMode | None = None,
    config_default: SandboxMode | None = None,
) -> SandboxMode:
    """按优先级解析出一次执行的有效模式。"""
    for candidate in (explicit_mode, session_override, config_default):
        if candidate is not None:
            return validate_mode(candidate)
    return FALLBACK_MODE


def resolve_policy(
    *,
    workspace_root: str,
    session_id: str | None = None,
    explicit_mode: SandboxMode | None = None,
    session_override: SandboxMode | None = None,
    config_default: SandboxMode | None = None,
) -> SandboxExecutionPolicy:
    """解析一次能力调用的完整策略。

    返回 ``SandboxExecutionPolicy``（可能含 ``danger-full-access``）——消费方
    只解析一次，再决定走约束路径还是直接 spawn 原始 argv。
    """
    return SandboxExecutionPolicy(
        mode=resolve_mode(
            explicit_mode=explicit_mode,
            session_override=session_override,
            config_default=config_default,
        ),
        workspace_root=workspace_root,
        session_id=session_id,
    )
