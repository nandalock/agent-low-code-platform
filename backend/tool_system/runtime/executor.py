"""ToolExecutor 抽象 + MCPExecutor — Tool 执行的唯一落地层

职责边界：
  Registry    → 产出 ToolDescriptor（元数据 / schema / resolve）
  ToolRuntime → 按 descriptor.type 选 executor + 产生 Tool 生命周期事件
  Executor    → 真正执行：transport 调度 / client 生命周期 / MCP call / timeout / 进度事件

执行签名统一为 execute(descriptor, args, context=None)：
  context（ToolContext）沿 AgentLoop → Runtime → Executor → Tool 单向传递。
  Executor 只产生 tool.progress（长任务阶段进度），生命周期事件由 ToolRuntime 产生。

MCPExecutor 承接原 ToolRegistry.call_async() 的全部 MCP 执行逻辑；
adapters/mcp.py 与 MCP Server 协议未做任何改动。

SandboxExecutor 是第二种执行器（descriptor.type == "sandbox"）：把原始 argv
交给 SandboxProvider.confine()，spawn 返回的 argv，再按后端方言分类结果。
执行链与 DeepSeek Harness 的 dsh-bash-sandbox 同构。
"""
import asyncio
import logging
import time
from abc import ABC, abstractmethod

from backend.tool_system.adapters.mcp import MCP_URL, McpClient, get_mcp_client
from backend.tool_system.context import ToolContext
from backend.tool_system.events import APPROVAL_REQUEST, SANDBOX_ESCALATION, TOOL_PROGRESS, ToolEvent
from backend.tool_system.registry.descriptor import SandboxToolConfig, ToolDescriptor
from backend.interaction.approval import ApprovalRequest, can_ask_human
from backend.tool_system.sandbox.classify import classify_outcome
from backend.tool_system.sandbox.errors import SandboxUnavailableError
from backend.tool_system.sandbox.escalation import (
    EscalationDenied,
    EscalationError,
    EscalationInvalid,
    approve_escalation,
    escalation_hint_marker,
    sandbox_denial_marker,
)
from backend.tool_system.sandbox.policy import resolve_policy
from backend.tool_system.sandbox.provider import SandboxProvider
from backend.tool_system.sandbox.vocabulary import SandboxExecutionPolicy, SandboxPolicy

logger = logging.getLogger(__name__)

# 未声明 timeout 的 Tool 使用该默认值（秒）
DEFAULT_TOOL_TIMEOUT = 30
# 进度轮询间隔（秒）——与原 AgentLoop 内的轮询节奏一致
PROGRESS_POLL_INTERVAL = 2


class ToolExecutor(ABC):
    """执行器抽象：按 descriptor 执行一次 Tool 调用，返回结果 dict"""

    @abstractmethod
    async def execute(
        self,
        descriptor: ToolDescriptor,
        args: dict,
        context: ToolContext | None = None,
    ) -> dict:
        ...


class MCPExecutor(ToolExecutor):
    """MCP 工具执行器：server_id → transport → client → call，统一带 timeout

    超时语义与改造前一致：超时返回 error 结果（而非抛异常），
    由 ToolRuntime 转成 tool.failed 事件，AgentLoop 记录 stop_reason=tool_timeout。
    """

    async def execute(
        self,
        descriptor: ToolDescriptor,
        args: dict,
        context: ToolContext | None = None,
    ) -> dict:
        timeout = descriptor.timeout or DEFAULT_TOOL_TIMEOUT
        try:
            return await asyncio.wait_for(
                self._call_with_progress(descriptor, args, context), timeout=timeout,
            )
        except asyncio.TimeoutError:
            return {"error": f"工具 {descriptor.name} 执行超时（>{timeout:.0f}s），已取消"}

    async def _call_with_progress(
        self,
        descriptor: ToolDescriptor,
        args: dict,
        context: ToolContext | None,
    ) -> dict:
        """执行调用；有事件出口 + 领域注册了进度查询时，轮询阶段进度 → tool.progress

        无 sink（API 直调 / 未装配事件）时完全走原路径：不轮询、无事件。
        """
        progress_fn = self._progress_query(descriptor.name) if self._has_sink(context) else None
        if progress_fn is None:
            return await self._call(descriptor, args)

        start = time.perf_counter()
        task = asyncio.create_task(self._call(descriptor, args))
        last_stage = ""
        while not task.done():
            await asyncio.sleep(PROGRESS_POLL_INTERVAL)
            try:
                stage = progress_fn(args) or ""
            except Exception:
                stage = ""
            if stage and stage != last_stage:
                last_stage = stage
                await self._emit_progress(context, descriptor.name, stage, round(time.perf_counter() - start))
        return task.result()

    async def _call(self, descriptor: ToolDescriptor, args: dict) -> dict:
        if descriptor.transport == "http":
            rows = await self._call_http(descriptor, args)
        else:
            rows = await self._call_stdio(descriptor, args)
        return {"tool": descriptor.name, "rows": rows, "count": len(rows)}

    @staticmethod
    async def _call_http(descriptor: ToolDescriptor, args: dict) -> list[dict]:
        """HTTP：复用 adapter 按 URL 缓存的 client（长连接），session 掉线自动重连"""
        url = descriptor.server.get("url") or MCP_URL
        client = await get_mcp_client(url)
        return await client.call(descriptor.name, args)

    @staticmethod
    async def _call_stdio(descriptor: ToolDescriptor, args: dict) -> list[dict]:
        """STDIO：每次调用创建临时连接，调完即断（子进程生命周期归本次调用）"""
        cfg = descriptor.server
        client = McpClient(
            transport="stdio",
            command=cfg.get("command", ""),
            args=cfg.get("args", []),
            env=cfg.get("env", {}),
        )
        try:
            await client.connect()
            return await client.call(descriptor.name, args)
        finally:
            await client.disconnect()

    # ── 进度事件 ──

    @staticmethod
    def _has_sink(context: ToolContext | None) -> bool:
        return context is not None and context.event_sink is not None

    @staticmethod
    def _progress_query(tool_name: str):
        """领域进度查询（Registry 元数据，装配层注册）：fn(args) -> stage 字符串

        只读元数据，不涉及 Registry 的执行职责；未注册 / Registry 未初始化返回 None。
        """
        try:
            from backend.tool_system.registry.registry import get_registry
            return get_registry().get_progress_query(tool_name)
        except Exception:
            return None

    @staticmethod
    async def _emit_progress(
        context: ToolContext, tool_name: str, stage: str, seconds: int,
    ) -> None:
        try:
            await context.event_sink.emit(ToolEvent(
                type=TOOL_PROGRESS,
                tool_name=tool_name,
                session_id=context.session_id,
                trace_id=context.trace_id,
                data={"stage": stage, "seconds": seconds},
            ))
        except Exception as e:
            logger.warning(f"tool.progress 发送失败（不影响执行）: {tool_name}: {e}")


# ── 沙箱执行器 ──

#: 单次执行返回给模型的最大字符数（stdout / stderr 各自）。
MAX_OUTPUT_CHARS = 8000


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n…（输出被截断，共 {len(text)} 字符）"


def build_sandbox_argv(cfg: SandboxToolConfig, args: dict) -> list[str]:
    """按 runtime 构造沙箱内的原始 argv。

    Raises:
        ValueError: 缺少必需参数或 runtime 未知。
    """
    if cfg.runtime == "shell":
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("shell 工具需要非空字符串参数 command")
        return ["bash", "-c", command]
    if cfg.runtime == "python":
        code = args.get("code")
        if not isinstance(code, str) or not code.strip():
            raise ValueError("python 工具需要非空字符串参数 code")
        return ["python", "-c", code]
    raise ValueError(f"未知沙箱 runtime: {cfg.runtime!r}（可选 shell / python）")


class SandboxExecutor(ToolExecutor):
    """沙箱工具执行器：``descriptor.sandbox`` 决定 argv 形状，策略来自 ToolContext。

    执行链（与 DeepSeek `dsh-bash-sandbox` 同构）：

        构造原始 argv → provider.confine() → spawn 返回的 argv → 分类结果

    ``danger-full-access`` 的消费方直接 spawn 原始 argv，不经过 provider。
    结果里的 ``sandbox.outcome`` 区分 normal / denied / runner_failed。
    """

    def __init__(self, provider: SandboxProvider | None = None):
        # provider 缺省时在执行时从装配层取（避免装配顺序耦合）
        self._provider = provider

    # ── 升权 ──

    async def _apply_escalation(
        self,
        descriptor: ToolDescriptor,
        args: dict,
        policy: SandboxExecutionPolicy,
        context: ToolContext | None,
    ) -> tuple[SandboxExecutionPolicy, EscalationError | None]:
        """走完整条升权链（DSH ``approveEscalation`` 同构），把结果落到策略上。

        分工：本方法**只提供外部事实**（当前模式、审批方、agent、工具名、
        调用 id）并消费结果；链本身归 :func:`approve_escalation`。

        **获批只影响这一次调用**：授权只存在于本次 args 里，调用结束即消失，
        本方法与 escalation 都不保存任何状态。

        Returns:
            ``(policy, error)``。``error`` 非 None 表示这次调用确实申请过升权
            但没批下来 —— 调用方据此**抑制升权提示**（见 :meth:`execute`
            里 notice 的构造），否则等于请模型重试。
        """
        try:
            target = await approve_escalation(
                args,
                current=policy.mode,
                # 审批能力归 interaction.approval：本层只把 ctx 上的通道与上下文转交
                approval=context.approval if context is not None else None,
                agent=context.agent_id if context is not None else None,
                tool_name=descriptor.name,
                tool_call_id=context.tool_call_id if context is not None else None,
                # 挂起**之前**把申请推出去，否则用户在等待期间看不到待批准卡片
                on_request=lambda req: self._emit_approval_request(context, descriptor.name, req),
            )
        except EscalationInvalid as e:
            # 非法请求：整条调用作废并回一条可修正的错误（DSH 把
            # validateEscalationArgs 放在 validateBashArgs 顶部，同样是让调用
            # 直接失败而不是静默降级）。**不打扰人**——这一档根本不该弹审批。
            await self._emit_escalation(context, descriptor.name, policy.mode, target=None, error=e)
            raise
        except EscalationDenied as e:
            # 合法但没批下来：回退到原策略照常执行——模型该看到的是沙箱自己
            # 产出的拒绝事实（§10.2 的标记），而不是「升权被拒」这个替代错误。
            await self._emit_escalation(context, descriptor.name, policy.mode, target=None, error=e)
            return policy, e

        if target is None:
            return policy, None  # 没这回事：未请求 / 请求等同当前模式

        await self._emit_escalation(context, descriptor.name, policy.mode, target=target, error=None)
        # explicit_mode 是优先级链的最高位，故这次解析的结果必然是目标模式、
        # 其余字段原样携带。走 resolve_policy 而非 dataclasses.replace：
        # 「策略怎么解析」只有一份实现（sandbox/policy.py）。
        wider = resolve_policy(
            workspace_root=policy.workspace_root,
            session_id=policy.session_id,
            explicit_mode=target,
            read_roots=policy.read_roots,
        )
        return wider, None

    @staticmethod
    async def _emit_approval_request(
        context: ToolContext | None,
        tool_name: str,
        req: ApprovalRequest,
    ) -> None:
        """待裁决申请 → event_sink（装配层决定落成 Event Log + UI 流）。

        ``reason`` 与 ``metadata`` 原样透出：审批是通用能力，执行器**不解释**
        metadata 的内容（那里放的是沙箱自己的模式信息）。
        """
        sink = context.event_sink if context is not None else None
        if sink is None:
            return
        try:
            await sink.emit(ToolEvent(
                type=APPROVAL_REQUEST,
                tool_name=tool_name,
                session_id=context.session_id,
                trace_id=context.trace_id,
                data={
                    "approval_id": req.approval_id,
                    "tool_call_id": req.tool_call_id,
                    "reason": req.reason,
                    "metadata": req.metadata,
                },
            ))
        except Exception as e:
            logger.warning(f"approval.request 发送失败（不影响执行）: {tool_name}: {e}")

    @staticmethod
    async def _emit_escalation(
        context: ToolContext | None,
        tool_name: str,
        from_mode: str,
        *,
        target: str | None,
        error: EscalationError | None,
    ) -> None:
        """升权事实 → event_sink，与 tool.progress 同路径。

        Executor 只 emit，「落到哪里」归装配层（当前：Session Event Log 的
        sandbox/escalation + UI 事件流）。无 sink 时静默跳过，不影响执行。

        ``target`` 与 ``error`` 至多一个非 None（同为 None 时调用方不会走到这）。
        """
        sink = context.event_sink if context is not None else None
        if sink is None:
            return
        try:
            await sink.emit(ToolEvent(
                type=SANDBOX_ESCALATION,
                tool_name=tool_name,
                session_id=context.session_id,
                trace_id=context.trace_id,
                data={
                    "from": from_mode,
                    "requested": target or (error.requested if error is not None else None),
                    "to": target,
                    "granted": target is not None,
                    "reason": "granted" if target is not None else (error.reason if error else "denied"),
                    "justification": error.justification if error is not None else "",
                    # 与 approval.request 配对，供前端收起对应的待批准卡片
                    "approval_id": error.approval_id if error is not None else None,
                },
            ))
        except Exception as e:
            logger.warning(f"sandbox.escalation 发送失败（不影响执行）: {tool_name}: {e}")

    async def execute(
        self,
        descriptor: ToolDescriptor,
        args: dict,
        context: ToolContext | None = None,
    ) -> dict:
        cfg = descriptor.sandbox
        if cfg is None:
            return {"error": f"沙箱工具缺少 SandboxToolConfig: {descriptor.name}"}

        policy = context.sandbox_policy if context is not None else None
        if policy is None:
            # fail-closed：没有策略就不执行（沙箱未装配 / 上下文未携带）
            return {"error": f"沙箱策略缺失，拒绝执行 {descriptor.name}（沙箱未装配？）"}

        # argv 先构造（参数校验）：参数非法时命令根本跑不了，不该留下一条
        # 「已批准升权」的审计记录——事件记的是真实发生过的事实。
        try:
            argv = build_sandbox_argv(cfg, args)
        except ValueError as e:
            return {"error": str(e)}

        try:
            policy, escalation = await self._apply_escalation(descriptor, args, policy, context)
        except EscalationInvalid as e:
            # 升权参数本身非法 → 整条调用作废，把 DSH 的错误文本原样回给模型
            # （它照着改就行）。**不是**沙箱拒绝，不带 denied 语义。
            return {"error": str(e), "sandbox": {"mode": policy.mode, "escalation_invalid": e.reason}}

        confined = None
        if policy.mode == "danger-full-access":
            final_argv = argv
        else:
            provider = self._provider or get_sandbox_provider()
            if provider is None:
                return {"error": f"没有可用的沙箱后端，拒绝执行 {descriptor.name}"}
            try:
                confined = provider.confine(
                    argv,
                    SandboxPolicy(
                        mode=policy.mode,
                        workspace_root=policy.workspace_root,
                        session_id=policy.session_id,
                        read_roots=policy.read_roots,
                    ),
                )
            except SandboxUnavailableError as e:
                return {"error": str(e), "sandbox": {"unavailable": True}}
            final_argv = confined.argv

        try:
            proc = await asyncio.create_subprocess_exec(
                *final_argv,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError) as e:
            return {"error": f"沙箱 runner 不可执行: {e}"}

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=cfg.timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"error": f"沙箱执行超时（>{cfg.timeout_s:.0f}s），已取消"}
        except asyncio.CancelledError:
            # 取消（客户端断连 / 关停）：不能让命令在后台继续跑。
            # 注意 docker CLI 被 SIGKILL 时容器可能成为孤儿 —— 需要更强保证
            # 时改用 cidfile + docker kill（见 docs/sandbox-design.md 的后续项）。
            proc.kill()
            raise

        stdout_full = stdout_b.decode("utf-8", errors="replace")
        stderr_full = stderr_b.decode("utf-8", errors="replace")
        result: dict = {
            "tool": descriptor.name,
            "exit_code": proc.returncode,
            "stdout": _truncate(stdout_full),
            "stderr": _truncate(stderr_full),
        }

        if confined is not None:
            # 分类用完整 stderr（截断只影响展示）
            outcome = classify_outcome(proc.returncode, stderr_full, confined)
            result["sandbox"] = {
                "mode": policy.mode,
                "enforcement": confined.enforcement,
                "outcome": outcome,
            }
            if outcome == "denied":
                result["notice"] = sandbox_denial_marker(policy.mode)
                if escalation is not None:
                    # 本次调用**刚申请过升权且被拒**：绝不能再提示「可以升权」
                    # ——那等于请模型重试，接下去必然是同一份请求的第二次失败。
                    result["notice"] += (
                        f"\n[sandbox: escalation refused ({escalation.reason})"
                        " — do not retry this command as-is]"
                    )
                else:
                    # 升权提示在使用点出现（与拒绝标记同位置），不进系统提示词：
                    # 常驻提示词会让模型变保守（原则 7，DSH 实测过）。
                    # 无审批通道（升权恒不可用，等价 DSH 的 never）与最宽模式下
                    # 返回空串 —— 提示一个必然失败的动作只是让模型白烧一轮。
                    hint = escalation_hint_marker(policy.mode, can_ask_human=can_ask_human())
                    if hint:
                        result["notice"] = f"{result['notice']}\n{hint}"
            elif outcome == "runner_failed":
                result["notice"] = "沙箱基础设施故障：命令未执行，请勿重试同一命令。"
        return result


def get_sandbox_provider() -> SandboxProvider | None:
    """装配层 provider（延迟导入，避免执行器与装配层互相依赖）。"""
    from backend.tool_system.sandbox.runtime import get_sandbox_provider as _get
    return _get()
