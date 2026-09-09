"""DockerProvider —— 基于短命容器的沙箱后端

对应 DeepSeek Harness `packages/sandbox/sandbox-local`（profile 构造见其
`src/profiles.ts`）。差异只在「用什么建立约束」：DeepSeek 用 bwrap 挂载 /
Landlock 授权 / Seatbelt SBPL，本后端用 ``docker run`` 的参数。

映射关系：

    DeepSeek ``workspace-write`` = 「工作区根 + 后端定义的临时区域」
    本后端                        = 只读根文件系统 + 工作区绑定 + /tmp tmpfs

因此工作区外写入会撞上内核的 ``Read-only file system``（EROFS），与同世界
后端的拒绝签名同源，分类器形状无需改动。

**已知缺口**：``confine(argv, policy)`` 的签名里没有镜像——DeepSeek 的词汇
也没有。本后端因此在构造时固定一个镜像。将来要按工具切换镜像（bash 用通用
镜像、python 用带科学栈的镜像），需要扩展 seam（provider 每镜像一个实例，
或给 policy 加一个后端配置字段），阶段 1b 再定。
"""
import os
from typing import Sequence

from backend.tool_system.sandbox.errors import SandboxUnavailableError
from backend.tool_system.sandbox.provider import ConfinedArgv, RunnerFailureRule, SandboxProvider
from backend.tool_system.sandbox.vocabulary import SandboxPolicy
from backend.tool_system.sandbox.workspace import CONTAINER_WORKSPACE

#: 默认沙箱镜像。
DEFAULT_SANDBOX_IMAGE = "python:3.12-slim"

#: 本后端的**拒绝方言**：``--read-only`` 下写工作区外触发。
#: 容器内的 ``permission denied`` 太通用（任何命令都可能打印），不能作为签名。
DENIAL_SIGNATURES: tuple[str, ...] = ("read-only file system",)

#: 本后端的 **runner 失败证据**。``docker run`` 自身失败固定退出码 125，
#: 与 DeepSeek 的 Landlock launcher 同一种干净门控。
RUNNER_FAILURE_RULES: tuple[RunnerFailureRule, ...] = (
    RunnerFailureRule(
        allowed_exit_codes=(125,),
        fatal_signatures=(
            "cannot connect to the docker daemon",
            "permission denied while trying to connect to the docker daemon socket",
            "no such image",
            "executable file not found",
            "docker: command not found",
        ),
    ),
)

#: 模型提供的 argv **元素**不得等于这些 docker 参数。
#: 真正的边界是构造顺序——调用方 argv 全部位于镜像名之后，docker 把其后
#: 一切当作容器内命令，位置上是安全的。本检查是纵深防御，防的是未来重构
#: 把用户内容挪到镜像名之前。
_FORBIDDEN_ARGV_TOKENS = frozenset({
    "--privileged", "--network", "--net", "--mount", "--volume",
    "--cap-add", "--security-opt", "--device", "--userns", "--pid",
    "--ipc", "--uts", "--publish", "--user",
    "-v", "-p", "-P",
})


def assert_no_isolation_injection(argv: Sequence[str]) -> None:
    """拒绝含 docker 隔离参数的调用方 argv。

    Raises:
        ValueError: 命中任一禁止 token。
    """
    hit = [a for a in argv if a in _FORBIDDEN_ARGV_TOKENS]
    if hit:
        raise ValueError(f"沙箱参数注入被拒绝: {hit}")


class DockerProvider(SandboxProvider):
    """每次调用 ``docker run --rm`` 起一个临时容器：无状态、可回收。

    Args:
        image: 沙箱镜像。见模块文档的「已知缺口」。
        memory / cpus / pids_limit: 资源上限。
        tmpfs_size: ``/tmp`` 大小——``workspace-write`` 承诺的临时区域。
    """

    def __init__(
        self,
        *,
        image: str = DEFAULT_SANDBOX_IMAGE,
        memory: str = "1g",
        cpus: float = 1.0,
        pids_limit: int = 256,
        tmpfs_size: str = "64m",
        container_workspace: str = CONTAINER_WORKSPACE,
    ) -> None:
        self._image = image
        self._memory = memory
        self._cpus = cpus
        self._pids_limit = pids_limit
        self._tmpfs_size = tmpfs_size
        self._container_workspace = container_workspace

    @property
    def image(self) -> str:
        return self._image

    def confine(self, argv: Sequence[str], policy: SandboxPolicy) -> ConfinedArgv:
        """把 ``argv`` 包装成 ``docker run`` 调用。

        Raises:
            ValueError: argv 为空或含注入 token。
            SandboxUnavailableError: 工作区根不是绝对路径。
        """
        if not argv:
            raise ValueError("argv 不能为空")
        assert_no_isolation_injection(argv)

        root = policy.workspace_root
        if not os.path.isabs(root):
            raise SandboxUnavailableError(policy.mode, f"workspace_root 必须是绝对路径: {root!r}")

        # 工作区挂载随模式变化——绑定挂载会覆盖根文件系统的只读属性，因此
        # read-only 必须显式以 :ro 挂载，否则工作区仍然可写（同 bwrap 只在
        # workspace-write 时追加可写 bind）。临时区域同理只在 workspace-write 提供。
        if policy.mode == "workspace-write":
            workspace_mount = f"{root}:{self._container_workspace}"
            temp_args = ["--tmpfs", f"/tmp:rw,size={self._tmpfs_size},exec"]
        else:
            workspace_mount = f"{root}:{self._container_workspace}:ro"
            temp_args = []

        wrapped = [
            "docker", "run", "--rm", "-i",
            # v1 不限制网络（与 DeepSeek 一致）；显式指定，不依赖 daemon 默认值。
            "--network", "bridge",
            # 只读根文件系统 + 工作区绑定（+ workspace-write 的 /tmp tmpfs）
            # = DeepSeek 的「工作区根 + 后端定义的临时区域」。
            "--read-only",
            *temp_args,
            "-v", workspace_mount,
            "-w", self._container_workspace,
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--pids-limit", str(self._pids_limit),
            "--memory", self._memory,
            "--cpus", str(self._cpus),
            self._image,
            *argv,
        ]
        return ConfinedArgv(
            argv=wrapped,
            enforcement="full",
            denial_signatures=DENIAL_SIGNATURES,
            runner_failure_rules=RUNNER_FAILURE_RULES,
        )
