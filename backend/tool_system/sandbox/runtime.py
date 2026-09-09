"""沙箱运行时装配 —— provider 单例 + 会话策略入口

装配层（``backend/main.py``）调用 :func:`init_sandbox`；
AgentLoop 调用 :func:`session_policy` 拿到本会话的执行策略。

``tool_system`` 不依赖 ``agents`` 层，因此这里只吃原始值（session_id / cwd），
不接收 Session 对象。
"""
import json
import logging
import os

from backend.core.connection import get_conn
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

#: 设置 namespace —— 沙箱开关的值归本子系统所有（平台层只问结果，不解释原因）
SETTINGS_NS = "sandbox"

#: 内建沙箱工具名（停用时注销它们）
BUILTIN_SANDBOX_TOOLS = ("bash", "python")

_enabled: bool | None = None


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
    绑定仍走 ``agent_tool_bindings``（按 tool_name），API / UI 无需改动。

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


# ── 能力开关（settings.sandbox.enabled） ──
#
# 「沙箱开不开」是沙箱子系统自己的设置，不归平台层解释。停用时**注销**内建工具，
# 于是模型完全看不到它们（而不是看得到但一调就失败）。绑定记录保留——重新启用
# 即恢复，不需要重新勾选。


def _load_enabled() -> bool:
    """从 settings 读开关；缺省 true。

    读失败也按启用处理：开关是「能力可见性」，不是约束本身——沙箱执行侧无论
    如何都 fail-closed，所以这里放宽不会导致裸跑。
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM settings WHERE namespace = %s", (SETTINGS_NS,))
                row = cur.fetchone()
        raw = (row or {}).get("value")
        if isinstance(raw, str):
            raw = json.loads(raw)
        return bool((raw or {}).get("enabled", True))
    except Exception as e:
        logger.warning(f"读取沙箱开关失败，按启用处理: {e}")
        return True


def _save_enabled(enabled: bool) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO settings (namespace, value, updated_at) VALUES (%s, %s::jsonb, now()) "
                "ON CONFLICT (namespace) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                (SETTINGS_NS, json.dumps({"enabled": enabled})),
            )
        conn.commit()


def is_enabled() -> bool:
    """沙箱能力当前是否启用（进程内缓存，写入侧同步刷新）。"""
    global _enabled
    if _enabled is None:
        _enabled = _load_enabled()
    return _enabled


def set_enabled(enabled: bool) -> None:
    """启停沙箱能力：写 settings + 立即重注册工具（下一轮对话即生效，无需重启）。"""
    global _enabled
    _enabled = enabled
    _save_enabled(enabled)
    apply_enabled(enabled)
    logger.info(f"沙箱能力已{'启用' if enabled else '停用'}")


def apply_enabled(enabled: bool) -> None:
    """按开关调整内建工具的注册状态（模型可见性随之变化）。"""
    try:
        from backend.tool_system.registry.registry import get_registry
        registry = get_registry()
    except RuntimeError:
        return  # Registry 未装配（未启动 / 单测）
    if enabled:
        register_builtin_sandbox_tools(registry)
    else:
        registry.unregister_builtin(BUILTIN_SANDBOX_TOOLS)
