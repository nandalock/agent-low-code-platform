"""沙箱运行时装配 —— provider 单例 + 会话策略入口

装配层（``backend/main.py``）调用 :func:`init_sandbox`；
AgentLoop 调用 :func:`session_policy` 拿到本会话的执行策略。

``tool_system`` 不依赖 ``agents`` 层，因此这里只吃原始值（session_id / cwd），
不接收 Session 对象。
"""
import logging
import os

from backend.tool_system.registry.descriptor import SandboxToolConfig, ToolDescriptor
from backend.tool_system.sandbox.backends.docker import DEFAULT_SANDBOX_IMAGE, DockerProvider
from backend.tool_system.sandbox.policy import DEFAULT_MODE_ENV, resolve_policy, validate_mode
from backend.tool_system.sandbox.provider import SandboxProvider
from backend.tool_system.sandbox.vocabulary import SandboxExecutionPolicy, SandboxMode
from backend.tool_system.sandbox.workspace import (
    probe_workspace_root,
    resolve_workspace_root,
    session_workspace,
)

logger = logging.getLogger(__name__)

_provider: SandboxProvider | None = None


# ── provider 单例 ──


def set_sandbox_provider(provider: SandboxProvider | None) -> None:
    global _provider
    _provider = provider


def get_sandbox_provider() -> SandboxProvider | None:
    """当前 provider；未装配返回 None（沙箱工具将 fail-closed）。"""
    return _provider


def init_sandbox(*, image: str | None = None, probe: bool = True) -> SandboxProvider:
    """装配沙箱：校验工作区根 → 功能探测 → 构造 provider。

    Raises:
        SandboxUnavailableError: 工作区未配置 / 在 daemon 侧不可见。
        ValueError: 工作区根配置不安全。
    """
    root = resolve_workspace_root()
    img = image or DEFAULT_SANDBOX_IMAGE
    if probe:
        probe_workspace_root(root, image=img)
    provider = DockerProvider(image=img)
    set_sandbox_provider(provider)
    logger.info(f"沙箱已装配：root={root} image={img}")
    return provider


# ── 会话策略 ──


def default_mode() -> SandboxMode | None:
    """部署默认模式（``SANDBOX_DEFAULT_MODE``）；未配置返回 None。

    Raises:
        ValueError: 环境变量取值不在封闭词汇内。
    """
    raw = os.environ.get(DEFAULT_MODE_ENV)
    if not raw:
        return None
    return validate_mode(raw)


def workspace_for_session(session_id: str) -> str:
    """本会话的工作区目录 ``<SANDBOX_WORKSPACE_ROOT>/<session_id>``（惰性创建）。"""
    return session_workspace(resolve_workspace_root(), session_id)


def session_policy(
    session_id: str,
    cwd: str | None = None,
    session_override: SandboxMode | None = None,
) -> SandboxExecutionPolicy | None:
    """解析本会话一次工具调用的执行策略；沙箱未装配时返回 None。

    ``session_override`` 来自 ``sandbox/mode`` 事件的投影（调用方提供，
    本层不依赖 agents 层）；优先级见 :mod:`backend.tool_system.sandbox.policy`。
    """
    if _provider is None:
        return None
    root = cwd or workspace_for_session(session_id)
    return resolve_policy(
        workspace_root=root,
        session_id=session_id,
        session_override=session_override,
        config_default=default_mode(),
    )


# ── 内置沙箱工具注册 ──

_BASH_SCHEMA = {
    "name": "bash",
    "description": (
        "在受限沙箱中执行 shell 命令。工作目录是会话工作区，只有该目录可写"
        "（写到其他路径会被内核拒绝）。返回 stdout / stderr / exit_code。"
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "要执行的 shell 命令"}},
        "required": ["command"],
    },
}

_PYTHON_SCHEMA = {
    "name": "python",
    "description": (
        "在受限沙箱中执行 Python 代码。工作目录是会话工作区，只有该目录可写"
        "（写到其他路径会被内核拒绝）。返回 stdout / stderr / exit_code。"
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"code": {"type": "string", "description": "要执行的 Python 代码"}},
        "required": ["code"],
    },
}


def register_builtin_sandbox_tools(
    registry,
    *,
    image: str | None = None,
    timeout_s: float = 60.0,
) -> list[str]:
    """把内置沙箱工具（bash / python）注册进 Registry。

    工具本身始终注册；能否执行取决于沙箱是否装配（未装配时执行 fail-closed）。
    绑定仍走 ``agent_mcp_bindings``（按 tool_name），API / UI 无需改动。

    Returns:
        注册的工具名列表。
    """
    img = image or DEFAULT_SANDBOX_IMAGE
    specs = (
        ("bash", "shell", _BASH_SCHEMA),
        ("python", "python", _PYTHON_SCHEMA),
    )
    names: list[str] = []
    for name, runtime, schema in specs:
        registry.register_native(ToolDescriptor(
            name=name,
            type="sandbox",
            transport="",
            server_id=0,
            schema=schema,
            timeout=timeout_s,
            sandbox=SandboxToolConfig(runtime=runtime, image=img, timeout_s=timeout_s),
        ))
        names.append(name)
    logger.info(f"已注册沙箱工具: {names}")
    return names
