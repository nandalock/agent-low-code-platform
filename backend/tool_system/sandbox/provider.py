"""SandboxProvider —— 进程沙箱的抽象 seam

移植自 DeepSeek Harness `packages/sandbox/sandbox/src/index.ts` 的
`SandboxProvider` / `ConfinedArgv` / `RunnerFailureRule`。

约定（全部照搬，未做改动）：
  - ``confine(argv, policy)`` 返回调用方应当 spawn 的替代 argv，使进程及其
    所有子进程在约束下运行；无法兑现时抛 ``SandboxUnavailableError``。
  - **静默的无约束透传永远不合法。**
  - 策略逐调用携带，不在 provider 上固定。
  - ``ConfinedArgv`` 同时携带两种**正交**的 stderr 分类器：
    ``denial_signatures`` 识别「沙箱正常工作、挡住了操作」，
    ``runner_failure_rules`` 识别「沙箱自己坏了、命令根本没跑」。
    消费方先查后者。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Sequence

from backend.tool_system.sandbox.vocabulary import SandboxEnforcement, SandboxPolicy


@dataclass(frozen=True)
class RunnerFailureRule:
    """识别 runner 在执行命令**之前**失败的证据。

    消费方先应用 ``allowed_exit_codes``（若存在），再按 ``informational_lines``
    整行不区分大小写精确匹配移除信息行，最后在剩余 stderr 行中做不区分
    大小写的 ``fatal_signatures`` 子串匹配。**退出状态本身永远不能证明
    runner 失败。**
    """

    fatal_signatures: tuple[str, ...]
    allowed_exit_codes: tuple[int, ...] | None = None
    informational_lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConfinedArgv:
    """``confine()`` 的结果：替代 argv + 强制执行事实 + 两套分类方言。"""

    argv: list[str]
    """应当替代 spawn 的 argv（runner、profile、分隔符、然后是调用方 argv）。"""

    enforcement: SandboxEnforcement
    """所选后端为该策略达到的强制执行完整度。"""

    denial_signatures: tuple[str, ...]
    """所选后端的**拒绝方言**：该后端拒绝一次文件效果时 stderr 出现的子串。

    消费方只与本后端自己的方言比对，而不是跨后端取并集——并集会声称某些
    本后端永远不会产生的拒绝。
    """

    runner_failure_rules: tuple[RunnerFailureRule, ...]
    """结构化的 runner 失败证据规则。runner 失败意味着命令根本没跑，
    而拒绝意味着约束生效并挡住了它。"""


class SandboxProvider(ABC):
    """进程沙箱服务。

    ``confine`` 必须在包装期或 runner 执行期返回带约束的 argv，或 fail-closed。
    """

    @abstractmethod
    def confine(self, argv: Sequence[str], policy: SandboxPolicy) -> ConfinedArgv:
        """把 ``argv`` 包装成在 ``policy`` 下受限执行的形式。

        Args:
            argv: 调用方即将 spawn 的精确 argv（程序 + 参数），**不是 shell
                字符串**——shell 形状的消费方传 ``["bash", "-c", command]``。
            policy: 本次执行的文件效果策略，逐调用携带。

        Returns:
            应当替代 spawn 的 argv，以及后端为它达到的强制执行完整度。

        Raises:
            SandboxUnavailableError: 没有可用后端，或后端无法兑现该模式。
        """
