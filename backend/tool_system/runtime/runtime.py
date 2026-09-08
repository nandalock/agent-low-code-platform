"""ToolRuntime — Tool 执行的统一入口（AgentLoop 只认识它）

调用链：
    AgentLoop.tool_call
        → ToolRuntime.execute(tool_name, args, context)
            → ToolRegistry.resolve(tool_name)   → ToolDescriptor
            → Executor（按 descriptor.type 选）  → MCP Client → MCP Server

Runtime 自身不含 MCP / HTTP / STDIO / server_id 任何知识：路由到 executor 即止。
context（ToolContext）只是原样下传，Runtime 不解释其内容。

Tool 生命周期事件（tool.started / tool.completed / tool.failed）由本层产生：
    context.event_sink.emit(ToolEvent(...))
没有 context 或没有 event_sink 时不产生任何事件（行为与改造前一致）。
"""
import logging
import time

from backend.tool_system.context import ToolContext
from backend.tool_system.events import (
    TOOL_COMPLETED,
    TOOL_FAILED,
    TOOL_STARTED,
    ToolEvent,
)
from backend.tool_system.registry.registry import ToolRegistry, get_registry
from backend.tool_system.runtime.executor import MCPExecutor, ToolExecutor

logger = logging.getLogger(__name__)


class ToolRuntime:
    """按 descriptor.type 分发到对应执行器，并产生 Tool 生命周期事件"""

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        executors: dict[str, ToolExecutor] | None = None,
    ):
        self._registry = registry or get_registry()
        # type → executor。后续接入 Native / Sandbox 执行器时在此注册即可（本阶段不实现）。
        self._executors: dict[str, ToolExecutor] = executors or {"mcp": MCPExecutor()}

    async def execute(
        self,
        tool_name: str,
        args: dict,
        context: ToolContext | None = None,
    ) -> dict:
        """执行一次 Tool 调用。未注册的工具抛 KeyError（与原 call_async 一致）。

        context 可选：调用方（AgentLoop）提供运行时上下文，缺省 None 时行为与之前完全一致。
        生命周期事件：started → completed（无 error 结果）/ failed（异常或 error 结果）。
        """
        descriptor = await self._registry.resolve(tool_name)
        if descriptor is None:
            raise KeyError(f"工具未注册: {tool_name}")

        executor = self._executors.get(descriptor.type)
        if executor is None:
            raise RuntimeError(f"没有匹配的执行器: type={descriptor.type} (tool={tool_name})")

        start = time.perf_counter()
        await self._emit(context, TOOL_STARTED, tool_name, {})
        try:
            result = await executor.execute(descriptor, args, context)
        except Exception as e:
            await self._emit(context, TOOL_FAILED, tool_name, {
                "error": str(e),
                "duration_ms": round((time.perf_counter() - start) * 1000),
            })
            raise

        duration_ms = round((time.perf_counter() - start) * 1000)
        # 执行器不抛异常但返回 error 结果（如 timeout）→ 同样是失败生命周期
        error = result.get("error") if isinstance(result, dict) else None
        if error:
            await self._emit(context, TOOL_FAILED, tool_name, {"error": error, "duration_ms": duration_ms})
        else:
            await self._emit(context, TOOL_COMPLETED, tool_name, {
                "count": result.get("count") if isinstance(result, dict) else None,
                "duration_ms": duration_ms,
            })
        return result

    @staticmethod
    async def _emit(context: ToolContext | None, event_type: str, tool_name: str, data: dict) -> None:
        """发事件；无 sink 时静默跳过。事件失败不影响 Tool 执行（旁路观测）。"""
        sink = context.event_sink if context is not None else None
        if sink is None:
            return
        try:
            await sink.emit(ToolEvent(
                type=event_type,
                tool_name=tool_name,
                session_id=context.session_id,
                trace_id=context.trace_id,
                data=data,
            ))
        except Exception as e:
            logger.warning(f"ToolEvent 发送失败（不影响执行）: {event_type} {tool_name}: {e}")


# 进程级单例（与 get_registry() 同模式）
_runtime: ToolRuntime | None = None


def get_tool_runtime() -> ToolRuntime:
    if _runtime is None:
        raise RuntimeError("ToolRuntime 未初始化，请先调用 init_tool_runtime()")
    return _runtime


def init_tool_runtime(registry: ToolRegistry | None = None) -> ToolRuntime:
    global _runtime
    _runtime = ToolRuntime(registry)
    return _runtime
