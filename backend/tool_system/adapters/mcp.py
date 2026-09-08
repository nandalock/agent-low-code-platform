"""MCP 客户端 — 内部 agent 调 MCP 服务用，支持 HTTP / stdio 两种传输"""
import json, logging, asyncio
from typing import Literal
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.stdio import stdio_client, StdioServerParameters

logger = logging.getLogger(__name__)

MCP_URL = "http://localhost:9001/mcp"
MCP_CONNECT_TIMEOUT = 10
# 读超时：MCP 工具响应走 SSE 流，结果在工具跑完才发；重活工具（PDF 下载/LLM 总结）可跑几分钟。
# 之前误用了 10s（连接超时值），响应流被 httpx 读超时掐断 → SDK 静默丢弃 pending future → 调用永久挂起。
MCP_TOOL_TIMEOUT = 300  # 工具调用总超时（秒）

TransportType = Literal["http", "stdio"]


class McpClient:
    """MCP 客户端会话封装"""

    def __init__(
        self,
        transport: TransportType = "http",
        *,
        url: str = MCP_URL,
        command: str = "python",
        args: list[str] | None = None,
        env: dict | None = None,
    ):
        self._transport_type = transport
        self._url = url
        self._command = command
        self._args = args or ["-m", "backend.tool_packages.builtin.server"]
        self._env = env or {}
        self._tools: list[dict] = []
        self._session: ClientSession | None = None
        self._transport_ctx = None

    async def connect(self):
        """建立 MCP 会话，拉取工具列表"""
        if self._transport_type == "http":
            self._transport_ctx = streamablehttp_client(
                self._url,
                timeout=MCP_CONNECT_TIMEOUT,       # 连接/写 10s 快速失败
                sse_read_timeout=MCP_TOOL_TIMEOUT, # 响应流读超时 300s，覆盖重活工具
            )
        else:
            params = StdioServerParameters(command=self._command, args=self._args, env=self._env if self._env else None)
            self._transport_ctx = stdio_client(params)

        result = await self._transport_ctx.__aenter__()
        if len(result) == 3:
            read, write, _get_session_id = result
        else:
            read, write = result
        self._session = await ClientSession(read, write).__aenter__()
        await self._session.initialize()
        result = await self._session.list_tools()
        self._tools = [
            {"name": t.name, "description": t.description or "", "inputSchema": t.inputSchema}
            for t in result.tools
        ]
        logger.info(f"MCP 已连接 ({self._transport_type})，{len(self._tools)} 个工具")

    async def disconnect(self):
        if self._session:
            await self._session.__aexit__(None, None, None)
            self._session = None
        if self._transport_ctx:
            await self._transport_ctx.__aexit__(None, None, None)
            self._transport_ctx = None

    @property
    def tools(self) -> list[dict]:
        return self._tools

    async def call(self, name: str, args: dict) -> list[dict]:
        """调用 MCP 工具，返回结果行（带总超时，防止传输层挂死导致 await 永不返回）"""
        if not self._session:
            raise RuntimeError("MCP 未连接")
        result = await asyncio.wait_for(
            self._session.call_tool(name, args),
            timeout=MCP_TOOL_TIMEOUT,
        )
        rows: list[dict] = []
        for c in result.content:
            if c.type == "text":
                try:
                    data = json.loads(c.text)
                    rows.extend(data if isinstance(data, list) else [data])
                except json.JSONDecodeError:
                    rows.append({"text": c.text})
        return rows


# 按 URL 缓存客户端
_clients: dict[str, McpClient] = {}


async def get_mcp_client(url: str = MCP_URL) -> McpClient:
    """获取已连接的 MCP 客户端（按 URL 缓存）"""
    if url not in _clients or not _clients[url]._session:
        client = McpClient(url=url)
        try:
            await client.connect()
            _clients[url] = client
        except Exception:
            # 连接失败不缓存，下次重试
            raise
    return _clients[url]
