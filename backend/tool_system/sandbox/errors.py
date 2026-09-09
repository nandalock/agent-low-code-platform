"""沙箱错误 —— fail-closed 的载体

移植自 DeepSeek Harness `packages/sandbox/sandbox/src/index.ts` 的
`SANDBOX_UNAVAILABLE` / `SandboxUnavailableError`。

`SandboxProvider.confine()` 只有两种结果：返回带约束的 argv，或抛本异常。
**没有第三种**——静默返回无约束 argv 是被禁止的。
"""

SANDBOX_UNAVAILABLE = "SANDBOX_UNAVAILABLE"


class SandboxUnavailableError(RuntimeError):
    """没有可用后端，或后端无法兑现请求的模式。

    fail-closed 的唯一出口。消息里指明缺失的后端，使「沙箱坏了」与
    「命令失败了」可以区分。
    """

    code = SANDBOX_UNAVAILABLE

    def __init__(self, mode: str, detail: str | None = None) -> None:
        message = (
            f'sandbox mode "{mode}" is requested but no sandbox backend is usable on this host; '
            "refusing to run the command unconfined. "
            "Ensure the docker daemon is reachable from the backend container and that "
            "SANDBOX_WORKSPACE_ROOT is visible to it — otherwise switch the consumer to "
            "danger-full-access."
        )
        if detail is not None:
            message = f"{message} Runner failure: {detail}"
        super().__init__(message)
        self.mode = mode
        self.detail = detail
