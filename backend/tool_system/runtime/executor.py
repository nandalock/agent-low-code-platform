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
"""
import asyncio
import logging
import time
from abc import ABC, abstractmethod

from backend.tool_system.adapters.mcp import MCP_URL, McpClient, get_mcp_client
from backend.tool_system.context import ToolContext
from backend.tool_system.events import TOOL_PROGRESS, ToolEvent
from backend.tool_system.registry.descriptor import ToolDescriptor

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
