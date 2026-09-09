"""工具来源（ToolProvider）—— 让 Registry 不再以 MCP 为默认假设

在 Provider 抽象之前，ToolRegistry 以 MCP 索引为主、以 ``_native`` 作补丁，
结构上暗示「MCP 是正道，内建是例外」。抽象之后：

    ToolRegistry
      ├─ MCPToolProvider      连 MCP server 发现工具（外部接入）
      └─ BuiltinToolProvider  代码声明内建工具（沙箱 bash / python 等）

Registry 只负责「从所有 provider 收集并合并成一张表」，不再知道工具从哪来。
加第三种来源（工作流节点工具、子 agent 工具……）时写个新 provider 注册即可。

**为什么内建工具不走 MCP**：策略（会话 cwd、沙箱模式）经 ``ToolContext``
沿 AgentLoop → Runtime → Executor 下传，而 MCP 调用协议只有 ``(name, args)``——
上下文过不去。让模型自己传 mode 等于让它声明自己的沙箱权限。因此需要
上下文的工具必须在进程内，MCP 只承载外部接入的能力。
"""
import json
import logging
from abc import ABC, abstractmethod

from backend.core.connection import get_conn
from backend.tool_system.adapters.mcp import MCP_URL, McpClient, get_mcp_client
from backend.tool_system.registry.descriptor import ToolDescriptor

logger = logging.getLogger(__name__)


def parse_stdio_config(srv: dict) -> dict:
    """解析 DB 里的 stdio 配置（args / env 可能是 JSON 字符串）"""
    args = srv.get("args") or []
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, TypeError):
            args = []
    env = srv.get("env") or {}
    if isinstance(env, str):
        try:
            env = json.loads(env)
        except (json.JSONDecodeError, TypeError):
            env = {}
    return {"command": srv.get("command", ""), "args": args, "env": env}


class ToolProvider(ABC):
    """一种工具来源。Registry 只从它收集描述，不解释其内部。"""

    #: 来源标识（UI 分组 / 日志用）
    name: str = "?"

    @abstractmethod
    async def discover(self) -> list[ToolDescriptor]:
        """列出本来源当前可用的工具。"""

    async def lazy_load(self, tool_name: str) -> ToolDescriptor | None:
        """按需解析一个未索引的工具；默认不支持（返回 None）。"""
        return None

    def label_of(self, descriptor: ToolDescriptor) -> str:
        """该工具在 UI 中的来源显示名；默认就是来源标识。"""
        return self.name


class BuiltinToolProvider(ToolProvider):
    """代码声明的内建工具（沙箱 bash / python 等）。

    内建工具与 AgentLoop 同进程——见模块文档「为什么内建工具不走 MCP」。
    """

    name = "builtin"

    def __init__(self) -> None:
        self._tools: dict[str, ToolDescriptor] = {}

    def register(self, descriptor: ToolDescriptor) -> None:
        """注册一个内建工具；同名覆盖。"""
        self._tools[descriptor.name] = descriptor

    def unregister(self, names) -> None:
        """注销若干内建工具（能力停用）；不存在的名字静默忽略。"""
        for n in names:
            self._tools.pop(n, None)

    def descriptors(self) -> list[ToolDescriptor]:
        return list(self._tools.values())

    async def discover(self) -> list[ToolDescriptor]:
        return self.descriptors()

    async def lazy_load(self, tool_name: str) -> ToolDescriptor | None:
        return self._tools.get(tool_name)


class MCPToolProvider(ToolProvider):
    """连 MCP server 发现工具（外部接入的能力）。

    支持 HTTP（长连接，client 缓存复用）与 STDIO（按需连接，拉完 schema 即断）
    两种传输。Registry 用 McpClient 只为了发现工具列表，不参与任何工具调用。
    """

    name = "mcp"

    def __init__(self) -> None:
        # tool_name → ToolDescriptor
        self._descriptors: dict[str, ToolDescriptor] = {}
        # server_id → server config（索引时缓存，resolve 时填进描述）
        self._server_configs: dict[int, dict] = {}

    # ── ToolProvider ──

    async def discover(self) -> list[ToolDescriptor]:
        """遍历所有 server 并索引工具。HTTP 远程延迟加载。"""
        for srv in self.list_servers():
            transport = srv.get("transport", "http")
            url = srv.get("url")
            if transport == "http" and url and not url.startswith(("http://localhost", "http://127.0.0.1")):
                logger.info(f"MCP 服务 [{srv['id']}] {srv['name']} ({url}) — 远程，延迟加载")
                continue
            try:
                await self.connect(srv)
            except Exception as e:
                logger.warning(f"MCP 服务 [{srv['id']}] {srv['name']} 连接失败: {e}")
        return self.descriptors()

    async def lazy_load(self, tool_name: str) -> ToolDescriptor | None:
        """懒加载：遍历所有 MCP server，找到包含该 tool 的 server 并索引。"""
        for srv in self.list_servers():
            try:
                await self.connect(srv)
                if tool_name in self._descriptors:
                    logger.info(f"懒加载工具 [{tool_name}] 来自 {srv['name']}")
                    return self._descriptors[tool_name]
            except Exception as e:
                logger.warning(f"懒加载 MCP [{srv['name']}] 失败: {e}")
        return None

    def label_of(self, descriptor: ToolDescriptor) -> str:
        return self.server_name(descriptor.server_id)

    # ── MCP 专属管理（api/mcp.py 用）──

    async def connect(self, srv: dict) -> int:
        """连接一个 server 并索引其工具。返回工具数。已连接过则跳过（幂等）。"""
        sid = srv["id"]
        if sid in self._server_configs:
            return len(self.descriptors_of_server(sid))
        await self._connect_and_index(srv)
        return len(self.descriptors_of_server(sid))

    def descriptors(self) -> list[ToolDescriptor]:
        return list(self._descriptors.values())

    def descriptors_of_server(self, server_id: int) -> list[ToolDescriptor]:
        return [d for d in self._descriptors.values() if d.server_id == server_id]

    def server_name(self, server_id: int) -> str:
        cfg = self._server_configs.get(server_id)
        return cfg["name"] if cfg else f"server-{server_id}"

    @staticmethod
    def list_servers() -> list[dict]:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, name, transport, url, command, args, env FROM mcp_servers ORDER BY id"
                )
                return [dict(r) for r in cur.fetchall()]

    # ── 内部 ──

    async def _connect_and_index(self, srv: dict) -> None:
        """连接单个 server，拉工具列表写入索引，并缓存 server 配置。"""
        sid = srv["id"]
        transport = srv.get("transport", "http")

        if transport == "http":
            url = srv.get("url") or MCP_URL
            client = await get_mcp_client(url)  # adapter 按 URL 缓存连接，executor 复用同一实例
            self._server_configs[sid] = {"id": sid, "name": srv["name"], "transport": transport, "url": url}
            tools = client.tools
        else:
            # stdio: 临时连接拉 schema，然后断开
            cfg = parse_stdio_config(srv)
            self._server_configs[sid] = {"id": sid, "name": srv["name"], "transport": transport, **cfg}
            client = McpClient(transport="stdio", **cfg)
            try:
                await client.connect()
                tools = client.tools
            finally:
                await client.disconnect()

        server_cfg = self._server_configs[sid]
        for t in tools:
            self._descriptors[t["name"]] = ToolDescriptor(
                name=t["name"],
                type="mcp",
                transport=transport,
                server_id=sid,
                schema=t,
                server=server_cfg,
            )
        logger.info(f"MCP 服务 [{sid}] {srv['name']} — 已索引 ({transport})")
