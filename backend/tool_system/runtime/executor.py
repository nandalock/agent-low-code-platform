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
from backend.tool_system.events import TOOL_PROGRESS, ToolEvent
from backend.tool_system.registry.descriptor import SandboxToolConfig, ToolDescriptor
from backend.tool_system.sandbox.classify import classify_outcome, denial_marker
from backend.tool_system.sandbox.errors import SandboxUnavailableError
from backend.tool_system.sandbox.provider import SandboxProvider
from backend.tool_system.sandbox.vocabulary import SandboxPolicy

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

        try:
            argv = build_sandbox_argv(cfg, args)
        except ValueError as e:
            return {"error": str(e)}

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
                result["notice"] = denial_marker(policy.mode)
            elif outcome == "runner_failed":
                result["notice"] = "沙箱基础设施故障：命令未执行，请勿重试同一命令。"
        return result


def get_sandbox_provider() -> SandboxProvider | None:
    """装配层 provider（延迟导入，避免执行器与装配层互相依赖）。"""
    from backend.tool_system.sandbox.runtime import get_sandbox_provider as _get
    return _get()
