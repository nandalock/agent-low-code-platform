"""会话工作区 —— 路径解析、宿主/容器映射、启动功能探测

与 DeepSeek Harness `packages/sandbox/sandbox/src/roots.ts` 位置对应，但职责
不同：DeepSeek 的同世界后端里「宿主路径即命令路径」，工作区根直接进 profile；
Docker 后端把工作区根挂到容器内固定点，因此需要**路径映射**与 **daemon 侧
可见性探测**（Docker-outside-of-Docker，见 docs/sandbox-design.md §8）。

安全边界（§9.2）：工作区根必须是独立目录。配成仓库根等于把整个仓库——
包括 .env 与源码——挂进沙箱，因此 :func:`assert_root_is_safe` 在解析阶段就拒绝。
"""
import os
import subprocess
from pathlib import Path

from backend.tool_system.sandbox.errors import SandboxUnavailableError

#: 工作区在沙箱容器内的挂载点。命令看到的路径以它为准。
CONTAINER_WORKSPACE = "/workspace"

#: 功能探测用的最小镜像（只用来证明 daemon 侧能读写该路径）。
DEFAULT_PROBE_IMAGE = "alpine:3.20"

#: 部署配置的环境变量名。
WORKSPACE_ROOT_ENV = "SANDBOX_WORKSPACE_ROOT"

#: 探测文件写在根目录，探测后立即删除。
_PROBE_NAME = ".sandbox-probe"


def repo_root() -> Path:
    """本仓库根目录（`<repo>/backend/tool_system/sandbox/workspace.py` 向上三级）。"""
    return Path(__file__).resolve().parents[3]


def _contains(parent: Path, child: Path) -> bool:
    """``parent`` 是 ``child`` 本身或它的祖先。两个参数都应是已解析的绝对路径。"""
    return parent == child or parent in child.parents


def assert_root_is_safe(root: str) -> None:
    """拒绝会把源码或凭据暴露进沙箱的工作区根。

    两条规则：
      - 工作区根不能是仓库根的祖先（否则整个仓库被挂进沙箱）
      - 工作区根不能落在 ``backend/`` 内（否则后端源码被挂进沙箱）

    Args:
        root: 待校验的工作区根路径。

    Raises:
        ValueError: 路径不绝对，或命中上述任一规则。
    """
    root_path = Path(root)
    if not root_path.is_absolute():
        raise ValueError(f"{WORKSPACE_ROOT_ENV} 必须是绝对路径: {root!r}")

    resolved = root_path.resolve()
    repo = repo_root()
    backend_dir = repo / "backend"

    if _contains(resolved, repo):
        raise ValueError(
            f"{WORKSPACE_ROOT_ENV}={root!r} 会包含仓库根 {repo}；"
            "沙箱将挂载整个仓库（含 .env 与源码）。请改用独立目录，例如 /srv/agent/workspaces。"
        )
    if _contains(backend_dir, resolved):
        raise ValueError(
            f"{WORKSPACE_ROOT_ENV}={root!r} 落在 backend/ 内 {backend_dir}；"
            "沙箱将挂载后端源码。请改用独立目录。"
        )


def resolve_workspace_root(configured: str | None = None) -> str:
    """解析并校验工作区根，必要时创建目录。

    优先级：显式参数 > 环境变量 ``SANDBOX_WORKSPACE_ROOT``。

    Raises:
        SandboxUnavailableError: 未配置。
        ValueError: 配置的路径不安全或无法创建。
    """
    root = configured if configured is not None else os.environ.get(WORKSPACE_ROOT_ENV)
    if not root:
        raise SandboxUnavailableError(
            "workspace-write",
            f"未配置 {WORKSPACE_ROOT_ENV}；沙箱需要一份独立的工作区根目录",
        )
    assert_root_is_safe(root)
    path = Path(root).resolve()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ValueError(f"无法创建工作区根 {path}: {e}") from e
    return str(path)


def session_workspace(root: str, session_id: str) -> str:
    """某会话的工作区目录 ``<root>/<session_id>``，惰性创建。

    Raises:
        ValueError: session_id 含路径分隔符（防止越出工作区根）。
    """
    if not session_id or "/" in session_id or "\\" in session_id or session_id in (".", ".."):
        raise ValueError(f"非法 session_id: {session_id!r}")
    path = Path(root).resolve() / session_id
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def to_container_path(host_path: str, root: str) -> str:
    """把被挂载目录内的宿主路径映射为容器内路径。

    ``root`` 是**被挂载到 ``/workspace`` 的那个目录**——即会话工作区
    ``<SANDBOX_WORKSPACE_ROOT>/<session_id>``（docs/sandbox-design.md §8.2），
    它本身映射为 ``/workspace``，其下路径映射为 ``/workspace/<相对路径>``。
    返回给模型 / 前端的路径都应经过本函数，否则模型会按宿主路径去操作。

    Raises:
        ValueError: 路径不在该目录内。
    """
    root_path = Path(root).resolve()
    target = Path(host_path).resolve()
    if not _contains(root_path, target):
        raise ValueError(f"路径不在工作区根内: {host_path!r} (root={root!r})")
    rel = target.relative_to(root_path)
    if str(rel) == ".":
        return CONTAINER_WORKSPACE
    return f"{CONTAINER_WORKSPACE}/{rel.as_posix()}"


def probe_workspace_root(
    root: str,
    *,
    image: str = DEFAULT_PROBE_IMAGE,
    timeout: float = 60.0,
) -> None:
    """真的起一个容器，证明 docker daemon 侧看得见并能写这个路径。

    不是 ``docker --version`` 那种可用性检查——它验证的是**部署事实**：
    宿主路径与 daemon 视角是否一致（Docker-outside-of-Docker 最常见的坑）。

    Raises:
        SandboxUnavailableError: docker 不可执行、探测超时，或容器内读写失败。
    """
    cmd = [
        "docker", "run", "--rm",
        "-v", f"{root}:/w",
        image,
        "sh", "-c", f"touch /w/{_PROBE_NAME} && rm -f /w/{_PROBE_NAME}",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise SandboxUnavailableError("workspace-write", f"docker 不可执行: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise SandboxUnavailableError("workspace-write", f"工作区探测超时（{timeout}s）: {e}") from e
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise SandboxUnavailableError(
            "workspace-write",
            f"工作区在 docker daemon 侧不可见或不可写: {root} (exit={proc.returncode}) {detail}",
        )
