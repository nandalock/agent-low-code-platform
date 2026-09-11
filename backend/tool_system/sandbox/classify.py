"""沙箱结果分类 —— 「沙箱挡住了」还是「沙箱坏了」

移植自 DeepSeek Harness `packages/shell/bash-sandbox/src/helpers.ts` 的
`matchesSignature` / `classifyRunnerFailure` / `classifyDenial`。

两个正交判定，**顺序不能反**：
  - ``runner_failed`` —— 沙箱自己坏了，命令根本没跑（退出码门控 + 排除
    信息行后的致命签名）
  - ``denied``        —— 沙箱正常工作，内核挡住了操作（该后端的 denial 方言）

反了会把「沙箱坏了」误报成「命令失败了」。
"""
from dataclasses import dataclass
from typing import Literal, Sequence

from backend.tool_system.sandbox.provider import ConfinedArgv, RunnerFailureRule

Outcome = Literal["normal", "denied", "runner_failed"]

# 拒绝标记文本（``sandbox_denial_marker``）住在 :mod:`.escalation`：它要与升权
# 提示成对出现，两者同属「使用点事实」。本模块只负责**分类**，不负责措辞。


@dataclass(frozen=True)
class RunnerFailureMatch:
    """命中的致命 runner 证据。``detail`` 是原始 stderr 行，保留用于错误详情。"""

    detail: str


def matches_signature(exit_code: int | None, stderr: str, signatures: Sequence[str]) -> bool:
    """非零退出，且 stderr 命中任一签名（不区分大小写）。

    ``exit_code`` 为 ``None`` 表示信号终止——不算拒绝。
    """
    if exit_code is None or exit_code == 0:
        return False
    lowered = stderr.lower()
    return any(sig.lower() in lowered for sig in signatures if sig.strip())


def classify_runner_failure(
    exit_code: int | None,
    stderr: str,
    rules: Sequence[RunnerFailureRule],
) -> RunnerFailureMatch | None:
    """按结构化规则判定 runner 是否在执行命令**之前**失败。

    每条规则要求：非零退出 + 可选退出码门控 + 排除整行信息性内容后的一行
    致命签名。
    """
    if exit_code is None or exit_code == 0:
        return None
    lines = stderr.splitlines()
    for rule in rules:
        if rule.allowed_exit_codes is not None and exit_code not in rule.allowed_exit_codes:
            continue
        informational = {line.lower() for line in rule.informational_lines}
        # 空白签名不是有效证据：忽略它，但保留旁边的有效签名。
        fatal = [sig.lower() for sig in rule.fatal_signatures if sig.strip()]
        if not fatal:
            continue
        for line in lines:
            lowered = line.lower()
            if lowered in informational:
                continue
            if any(sig in lowered for sig in fatal):
                return RunnerFailureMatch(detail=line)
    return None


def classify_outcome(
    exit_code: int | None,
    stderr: str,
    confined: ConfinedArgv,
) -> Outcome:
    """一次执行结果的归类。**runner 故障优先于拒绝。**"""
    if classify_runner_failure(exit_code, stderr, confined.runner_failure_rules) is not None:
        return "runner_failed"
    if matches_signature(exit_code, stderr, confined.denial_signatures):
        return "denied"
    return "normal"
