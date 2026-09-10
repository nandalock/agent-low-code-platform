"""工具注册表 — 从所有 ToolProvider 收集工具，产出 ToolDescriptor

职责边界（Provider 抽象后）：
  Registry   只做「收集 + 合并 + 解析」：从注册的 ToolProvider 拿到描述，合并成
             一张 {name → ToolDescriptor} 表。它不再知道工具来自 MCP 还是代码。
  Provider   一种工具来源（MCPToolProvider / BuiltinToolProvider，见 providers.py）。
  Runtime    按 descriptor.type 分发到 executor（见 tool_system/runtime/）。

`type` 是「执行器选择键」（"mcp" | "sandbox"）：新增执行器只需产出新的 type
并在 ToolRuntime 注册，Registry 无需改动。
"""
import logging
from dataclasses import replace

from backend.tool_system.registry.descriptor import (
    PARALLEL,
    ToolDescriptor,
    validate_execution_mode,
)
from backend.tool_system.registry.providers import (
    BuiltinToolProvider,
    MCPToolProvider,
    ToolProvider,
)

logger = logging.getLogger(__name__)


class ToolRegistry:
    """工具来源的收集器 + 统一索引 + Agent 绑定查询"""

    def __init__(self, providers: list[ToolProvider] | None = None):
        # name → ToolDescriptor（所有来源合并后的唯一索引）
        self._by_name: dict[str, ToolDescriptor] = {}
        # 工具来源；默认装配 MCP（外部）与内建（代码）两种
        self._providers: list[ToolProvider] = providers or [MCPToolProvider(), BuiltinToolProvider()]
        # 工具进度查询（可选扩展点）：tool_name → fn(args) -> stage_str
        self._progress_queries: dict[str, object] = {}
        # 工具执行超时（可选扩展点）：tool_name → 秒；未声明的由 Executor 用默认值
        self._tool_timeouts: dict[str, float] = {}
        # 工具执行模式（可选扩展点）：tool_name → "parallel" | "exclusive"；
        # 未声明的用描述自带值（默认 parallel）
        self._execution_modes: dict[str, str] = {}
        self._ready = False

    # ── 来源注册 ──

    def register_provider(self, provider: ToolProvider) -> None:
        """注册一个工具来源；同名来源可并存（收集时按注册序合并）。"""
        self._providers.append(provider)

    def provider_of(self, cls: type[ToolProvider]) -> ToolProvider | None:
        """按类型取来源（MCP 专属管理接口用）。"""
        return next((p for p in self._providers if isinstance(p, cls)), None)

    def _builtin(self) -> BuiltinToolProvider:
        p = self.provider_of(BuiltinToolProvider)
        if p is None:
            p = BuiltinToolProvider()
            self.register_provider(p)
        return p  # type: ignore[return-value]

    def register_builtin(self, descriptor: ToolDescriptor) -> None:
        """注册一个内建工具（代码声明的能力，如沙箱 bash / python）。

        立即写入统一索引：内建工具通常在 ``init()`` 之后注册（装配顺序），
        只登记到 provider 的话，``descriptors()`` 看不到它（``resolve()`` 尚能
        靠懒加载兜底，于是出现「单查得到、列表里没有」的割裂）。
        """
        self._builtin().register(descriptor)
        self._by_name[descriptor.name] = self._with_metadata(descriptor)

    def register_native(self, descriptor: ToolDescriptor) -> None:
        """``register_builtin`` 的旧名（保留兼容）。"""
        self.register_builtin(descriptor)

    def unregister_builtin(self, names) -> None:
        """注销内建工具：同时从 provider 与统一索引摘掉。

        这是「能力停用」的落地方式——工具从索引消失后，``get_schemas_for()``
        就不会再把它发给模型（模型看不到 ≠ 看得到但调用失败）。
        """
        builtin = self.provider_of(BuiltinToolProvider)
        if builtin is not None:
            builtin.unregister(names)
        for n in names:
            self._by_name.pop(n, None)

    # ── 工具进度查询（领域扩展点） ──

    def register_progress_query(self, tool_name: str, fn) -> None:
        """注册工具进度查询函数：fn(args: dict) -> 当前阶段描述字符串"""
        self._progress_queries[tool_name] = fn

    def get_progress_query(self, tool_name: str):
        return self._progress_queries.get(tool_name)

    # ── 工具执行超时（领域扩展点） ──

    def register_tool_timeout(self, tool_name: str, seconds: float) -> None:
        """声明工具执行超时（秒）— 存的是元数据，执行时由 Executor 生效。
        未声明的 Tool 由 Executor 使用 DEFAULT_TOOL_TIMEOUT"""
        self._tool_timeouts[tool_name] = seconds

    def get_tool_timeout(self, tool_name: str) -> float | None:
        return self._tool_timeouts.get(tool_name)

    # ── 工具执行模式（领域扩展点） ──

    def register_execution_mode(self, tool_name: str, mode: str) -> None:
        """声明工具的并发约束（``parallel`` / ``exclusive``），**覆盖**描述自带值。

        有共享状态的 MCP 工具（写库 / 改文件）应在装配处显式声明 ``exclusive``
        —— 模型侧参数无法影响它。与 :meth:`register_tool_timeout` 同模式（存元数据、
        调度时生效），差别是这里**立即刷新统一索引**：声明晚于注册时不能让
        ``descriptors()`` 停留在旧值（同 register_builtin 的「单查得到、列表里没有」
        割裂问题 —— 元数据要么处处可见，要么处处不可见）。

        Raises:
            ValueError: mode 不在封闭词汇内。
        """
        self._execution_modes[tool_name] = validate_execution_mode(mode)
        d = self._by_name.get(tool_name)
        if d is not None:
            self._by_name[tool_name] = replace(d, execution_mode=mode)

    def execution_mode_of(self, tool_name: str) -> str:
        """工具当前的并发约束（同步读取，调度器在派发前逐次查询）。

        取值优先级：本层声明的覆盖值 → 统一索引里的描述（Provider 侧自带）→
        默认 ``parallel``。未索引的工具返回声明值/默认值，不触发懒加载
        —— 调度路径上不能有 I/O。
        """
        declared = self._execution_modes.get(tool_name)
        if declared is not None:
            return declared
        d = self._by_name.get(tool_name)
        return d.execution_mode if d is not None else PARALLEL

    # ── 收集 ──

    async def init(self) -> None:
        """从所有来源收集工具，合并成统一索引。单个来源失败不影响其他来源。"""
        for provider in self._providers:
            try:
                for d in await provider.discover():
                    self._by_name[d.name] = self._with_metadata(d)
            except Exception as e:
                logger.warning(f"工具来源 [{provider.name}] 发现失败: {e}")
        self._ready = True
        logger.info(f"ToolRegistry 初始化完成，共 {len(self._by_name)} 个工具")

    def _with_metadata(self, d: ToolDescriptor) -> ToolDescriptor:
        """把 Registry 级声明的元数据（超时 / 执行模式）盖到描述上。

        描述是冻结的，用 ``replace`` 派生；未声明或与自带值相同则不派生。
        """
        t = self._tool_timeouts.get(d.name)
        if t is not None and t != d.timeout:
            d = replace(d, timeout=t)
        m = self._execution_modes.get(d.name)
        if m is not None and m != d.execution_mode:
            d = replace(d, execution_mode=m)
        return d

    # ── 读取面 ──

    def descriptors(self) -> list[ToolDescriptor]:
        """全部工具描述（副本，只读）。"""
        return list(self._by_name.values())

    def source_label(self, descriptor: ToolDescriptor) -> str:
        """该工具在 UI 中的来源显示名（"builtin" / MCP server 名）。"""
        mcp = self.provider_of(MCPToolProvider)
        if descriptor.type == "mcp" and mcp is not None:
            return mcp.label_of(descriptor)
        builtin = self.provider_of(BuiltinToolProvider)
        return builtin.name if builtin is not None else descriptor.type

    async def resolve(self, tool_name: str) -> ToolDescriptor | None:
        """解析工具 → ToolDescriptor（未索引则依次问各来源懒加载）。

        这是 Registry 对执行侧的唯一出口：产出描述即止，不执行、不碰 transport。
        """
        d = self._by_name.get(tool_name)
        if d is not None:
            return d
        for provider in self._providers:
            d = await provider.lazy_load(tool_name)
            if d is not None:
                d = self._with_metadata(d)
                self._by_name[d.name] = d
                return d
        return None

    # ── Agent 绑定查询 ──

    @staticmethod
    def get_bindings(agent_key: str) -> list[str]:
        """查 agent_tool_bindings 表，返回该 agent 绑定的 tool_name 列表。

        绑定与工具来源正交：内建工具（沙箱 bash / python）与 MCP 工具走同一张表。
        """
        from backend.core.connection import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tool_name FROM agent_tool_bindings WHERE agent_key = %s ORDER BY tool_name",
                    (agent_key,),
                )
                return [r["tool_name"] for r in cur.fetchall()]

    async def get_schemas_for(self, agent_key: str) -> list[dict]:
        """返回该 agent 绑定工具的 OpenAI function calling 格式 schemas"""
        schemas = []
        for name in self.get_bindings(agent_key):
            d = await self.resolve(name)
            if d is not None:
                schemas.append(_to_openai_function(d.schema))
        return schemas


def _sanitize_schema(schema: dict) -> dict:
    """清洗 MCP inputSchema 为 OpenAI 兼容格式"""
    s = {}
    s["type"] = schema.get("type", "object")
    if "properties" in schema:
        s["properties"] = {}
        for k, v in schema["properties"].items():
            prop = {}
            # 去掉 anyOf，优先取第一个非 null type
            if "anyOf" in v:
                for opt in v["anyOf"]:
                    if opt.get("type") != "null":
                        prop["type"] = opt.get("type", "string")
                        break
                else:
                    prop["type"] = "string"
            else:
                prop["type"] = v.get("type", "string")
            if "description" in v:
                prop["description"] = v["description"]
            elif "title" in v:
                prop["description"] = v["title"]
            if "default" in v and v["default"] is not None:
                prop["default"] = v["default"]
            s["properties"][k] = prop
    if "required" in schema:
        s["required"] = schema["required"]
    return s


def _to_openai_function(schema: dict) -> dict:
    """工具 schema（含 name / description / inputSchema）→ OpenAI function 格式"""
    raw = schema.get("inputSchema", {})
    return {
        "type": "function",
        "function": {
            "name": schema["name"],
            "description": (schema.get("description") or "")[:1024],
            "parameters": _sanitize_schema(raw),
        },
    }


# 全局单例
_registry: ToolRegistry | None = None


def get_registry() -> ToolRegistry:
    if _registry is None:
        raise RuntimeError("ToolRegistry 未初始化，请先调用 init_registry()")
    return _registry


async def init_registry():
    global _registry
    _registry = ToolRegistry()
    await _registry.init()
