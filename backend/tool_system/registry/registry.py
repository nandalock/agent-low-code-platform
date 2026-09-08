"""MCP 工具全局注册表 — 启动时连所有 MCP server，建 {tool_name → info} 索引

支持两种传输模式:
  - HTTP: 长连接，client 缓存复用
  - STDIO: 按需连接，调完即断
"""
import json, logging
from backend.core.connection import get_conn
from backend.tool_system.adapters.mcp import McpClient, get_mcp_client, MCP_URL

logger = logging.getLogger(__name__)


class ToolRegistry:
    """全局 MCP 工具索引 + Agent 绑定查询"""

    def __init__(self):
        # tool_name → {server_id, schema, transport}
        self._index: dict[str, dict] = {}
        # server_id → McpClient (仅 HTTP)
        self._http_clients: dict[int, McpClient] = {}
        # server_id → server config (仅 STDIO，用于按需重连)
        self._stdio_configs: dict[int, dict] = {}
        # 工具进度查询（可选扩展点）：tool_name → fn(args) -> stage_str
        # 领域 MCP 可在装配层注册，AgentRuntime 执行工具期间轮询 → tool_progress 事件
        self._progress_queries: dict[str, object] = {}
        # 工具执行超时（可选扩展点）：tool_name → 秒；未注册的 Tool 由 AgentLoop 使用默认值
        self._tool_timeouts: dict[str, float] = {}
        self._ready = False

    # ── 工具进度查询（领域扩展点） ──

    def register_progress_query(self, tool_name: str, fn) -> None:
        """注册工具进度查询函数：fn(args: dict) -> 当前阶段描述字符串"""
        self._progress_queries[tool_name] = fn

    def get_progress_query(self, tool_name: str):
        return self._progress_queries.get(tool_name)

    # ── 工具执行超时（领域扩展点） ──

    def register_tool_timeout(self, tool_name: str, seconds: float) -> None:
        """注册工具执行超时（秒）。未注册的 Tool 由 AgentLoop 使用 DEFAULT_TOOL_TIMEOUT"""
        self._tool_timeouts[tool_name] = seconds

    def get_tool_timeout(self, tool_name: str) -> float | None:
        return self._tool_timeouts.get(tool_name)

    async def init(self):
        """启动时：遍历所有 server，连上并索引工具。HTTP 远程延迟加载。"""
        servers = self._list_servers()
        for srv in servers:
            transport = srv.get("transport", "http")
            url = srv.get("url")

            # HTTP 远程 → 延迟加载
            if transport == "http" and url and not url.startswith("http://localhost") and not url.startswith("http://127.0.0.1"):
                logger.info(f"MCP 服务 [{srv['id']}] {srv['name']} ({url}) — 远程，延迟加载")
                continue

            try:
                await self._connect_and_index(srv, cache_client=(transport == "http"))
            except Exception as e:
                logger.warning(f"MCP 服务 [{srv['id']}] {srv['name']} 连接失败: {e}")
        self._ready = True
        logger.info(f"ToolRegistry 初始化完成，共 {len(self._index)} 个工具")

    async def connect_server(self, srv: dict) -> int:
        """外部 API 调用：连接一个 server 并索引其工具。返回工具数。已连接过则跳过（幂等）。"""
        transport = srv.get("transport", "http")
        sid = srv["id"]
        # 幂等：该 server 已连接并索引过则直接返回，避免 tools/all 每次全量重连
        if (transport == "http" and sid in self._http_clients) or (transport == "stdio" and sid in self._stdio_configs):
            return sum(1 for v in self._index.values() if v["server_id"] == sid)
        try:
            await self._connect_and_index(srv, cache_client=(transport == "http"))
        except Exception as e:
            logger.warning(f"connect_server [{srv.get('name')}] 失败: {e}")
            raise
        return sum(1 for v in self._index.values() if v["server_id"] == srv["id"])

    async def _connect_and_index(self, srv: dict, cache_client: bool):
        """连接单个 server，拉工具列表写入 _index"""
        sid = srv["id"]
        transport = srv.get("transport", "http")

        if transport == "http":
            client = await get_mcp_client(srv.get("url") or MCP_URL)
            if cache_client:
                self._http_clients[sid] = client
            for t in client.tools:
                self._index[t["name"]] = {"server_id": sid, "schema": t, "transport": transport}
        else:
            # stdio: 临时连接拉 schema，然后断开
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

            self._stdio_configs[sid] = {"command": srv.get("command", ""), "args": args, "env": env}
            client = McpClient(transport="stdio", command=srv.get("command", ""), args=args, env=env)
            try:
                await client.connect()
                for t in client.tools:
                    self._index[t["name"]] = {"server_id": sid, "schema": t, "transport": transport}
            finally:
                await client.disconnect()

        logger.info(f"MCP 服务 [{sid}] {srv['name']} — 已索引 ({transport})")

    @staticmethod
    def _list_servers() -> list[dict]:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, transport, url, command, args, env FROM mcp_servers ORDER BY id")
                return [dict(r) for r in cur.fetchall()]

    # ── Agent 绑定查询 ──

    @staticmethod
    def get_bindings(agent_key: str) -> list[str]:
        """查 agent_mcp_bindings 表，返回该 agent 绑定的 tool_name 列表"""
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT tool_name FROM agent_mcp_bindings WHERE agent_key = %s ORDER BY tool_name",
                    (agent_key,),
                )
                return [r["tool_name"] for r in cur.fetchall()]

    # ── 给 AgentRuntime 用 ──

    async def get_schemas_for(self, agent_key: str) -> list[dict]:
        """返回该 agent 绑定工具的 OpenAI function calling 格式 schemas"""
        tool_names = self.get_bindings(agent_key)
        schemas = []
        for name in tool_names:
            entry = self._index.get(name)
            if not entry:
                entry = await self._lazy_load_tool(name)
            if entry:
                schemas.append(_to_openai_function(entry["schema"]))
        return schemas

    async def call_async(self, tool_name: str, args: dict) -> dict:
        """异步执行工具调用。HTTP 用缓存 client，stdio 临时连接后断开。"""
        entry = self._index.get(tool_name)
        if not entry:
            entry = await self._lazy_load_tool(tool_name)
        if not entry:
            raise KeyError(f"工具未注册: {tool_name}")

        transport = entry.get("transport", "http")
        if transport == "http":
            client = self._http_clients.get(entry["server_id"])
            if not client:
                raise RuntimeError(f"HTTP client 未连接: server {entry['server_id']}")
            rows = await client.call(tool_name, args)
        else:
            # stdio: 每次调用创建临时连接
            cfg = self._stdio_configs.get(entry["server_id"], {})
            client = McpClient(
                transport="stdio",
                command=cfg.get("command", ""),
                args=cfg.get("args", []),
                env=cfg.get("env", {}),
            )
            try:
                await client.connect()
                rows = await client.call(tool_name, args)
            finally:
                await client.disconnect()

        return {"tool": tool_name, "rows": rows, "count": len(rows)}

    async def _lazy_load_tool(self, tool_name: str) -> dict | None:
        """懒加载：遍历所有 MCP server，找到包含该 tool 的 server 并连接"""
        servers = self._list_servers()
        for srv in servers:
            transport = srv.get("transport", "http")
            try:
                if transport == "http":
                    url = srv.get("url") or MCP_URL
                    client = await get_mcp_client(url)
                    self._http_clients[srv["id"]] = client
                    for t in client.tools:
                        if t["name"] not in self._index:
                            self._index[t["name"]] = {"server_id": srv["id"], "schema": t, "transport": transport}
                else:
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
                    self._stdio_configs[srv["id"]] = {"command": srv.get("command", ""), "args": args, "env": env}
                    client = McpClient(transport="stdio", command=srv.get("command", ""), args=args, env=env)
                    try:
                        await client.connect()
                        for t in client.tools:
                            if t["name"] not in self._index:
                                self._index[t["name"]] = {"server_id": srv["id"], "schema": t, "transport": transport}
                    finally:
                        await client.disconnect()

                if tool_name in self._index:
                    logger.info(f"懒加载工具 [{tool_name}] 来自 {srv['name']}")
                    return self._index[tool_name]
            except Exception as e:
                logger.warning(f"懒加载 MCP [{srv['name']}] 失败: {e}")
        return None


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
    """MCP inputSchema → OpenAI function calling 格式"""
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
