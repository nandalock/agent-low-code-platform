"""MCP API — 服务管理 + 代理 + 热拔插绑定"""
import json, traceback, logging, asyncio
from fastapi import APIRouter, Body, HTTPException
from backend.core.connection import get_conn
from backend.tool_system.adapters.mcp import get_mcp_client, McpClient, MCP_URL
from backend.tool_system.registry.registry import get_registry
from backend.tool_system.runtime.runtime import get_tool_runtime

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/mcp", tags=["MCP"])


# ━━ MCP 服务 CRUD ━━

@router.get("/servers")
def api_list_servers():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, tenant_id, name, transport, url, command, args, env, created_at FROM mcp_servers ORDER BY id")
            rows = []
            for r in cur.fetchall():
                d = dict(r)
                d["args"] = list(d["args"]) if d.get("args") else []
                d["env"] = dict(d["env"]) if d.get("env") else {}
                rows.append(d)
            return rows


@router.post("/servers")
def api_create_server(payload: dict = Body(...)):
    args = payload.get("args")
    if isinstance(args, list):
        args = json.dumps(args)
    env = payload.get("env")
    if isinstance(env, dict):
        env = json.dumps(env)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO mcp_servers (tenant_id, name, transport, url, command, args, env) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb) RETURNING id",
                (payload.get("tenant_id", 1), payload["name"], payload.get("transport", "http"),
                 payload.get("url"), payload.get("command"), args, env),
            )
            sid = cur.fetchone()["id"]
        conn.commit()
    return {"id": sid, "ok": True}


@router.delete("/servers/{sid}")
def api_delete_server(sid: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM mcp_servers WHERE id = %s", (sid,))
        conn.commit()
    return {"ok": True}


# ━━ 一键导入（Claude Desktop 格式） ━━

@router.post("/import")
async def api_import_servers(payload: dict = Body(...)):
    """导入 MCP Server 配置，支持 Claude Desktop 格式。

    格式 A — 单个 server:
      {"name":"github","command":"docker","args":[...],"env":{...}}

    格式 B — Claude Desktop 完整配置:
      {"mcpServers":{"github":{...},"filesystem":{...}}}
    """
    entries: list[tuple[str, dict]] = []

    if "mcpServers" in payload:
        for name, cfg in payload["mcpServers"].items():
            entries.append((name, cfg))
    elif "name" in payload:
        entries.append((payload["name"], payload))
    else:
        raise HTTPException(400, "请提供 mcpServers 对象或包含 name 的单个配置")

    created: list[dict] = []
    for name, cfg in entries:
        has_url = "url" in cfg and cfg["url"]
        transport = "http" if has_url else "stdio"

        args = cfg.get("args")
        if isinstance(args, list):
            args = json.dumps(args)
        env = cfg.get("env")
        if isinstance(env, dict):
            env = json.dumps(env)

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO mcp_servers (tenant_id, name, transport, url, command, args, env)
                       VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb) RETURNING id""",
                    (payload.get("tenant_id", 1), name, transport,
                     cfg.get("url"), cfg.get("command"), args, env),
                )
                sid = cur.fetchone()["id"]
            conn.commit()

        # 尝试连接并索引工具
        tools_count = 0
        try:
            registry = get_registry()
            tools_count = await registry.connect_server(dict(
                id=sid, name=name, transport=transport,
                url=cfg.get("url"), command=cfg.get("command"),
                args=cfg.get("args", []),
                env=cfg.get("env", {}),
            ))
        except Exception as e:
            logger.warning(f"导入 [{name}] 连接失败 (server id={sid}): {e}")

        created.append({"id": sid, "name": name, "transport": transport, "tools_count": tools_count})

    return {"ok": True, "created": created}


# ━━ 按 server ID 代理 ━━

def _lookup_server(sid: int) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, url, transport, command, args, env FROM mcp_servers WHERE id = %s", (sid,))
            row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"MCP 服务不存在: {sid}")
    r = dict(row)
    r["args"] = list(r["args"]) if r.get("args") else []
    r["env"] = dict(r["env"]) if r.get("env") else {}
    return r


async def _get_client_for(sid: int):
    srv = _lookup_server(sid)
    url = srv["url"] or MCP_URL
    return await get_mcp_client(url)


@router.post("/servers/{sid}/tools")
async def api_server_tools(sid: int):
    """列出 server 的工具列表（兼做连通性检测，http+stdio 都支持）"""
    try:
        srv = _lookup_server(sid)
    except HTTPException:
        raise
    transport = srv.get("transport", "http")
    try:
        if transport == "http":
            client = await _get_client_for(sid)
            return client.tools
        else:
            # stdio: 用 registry 查已索引的工具，不重复连接（避免触发 npx 下载）
            registry = get_registry()
            tools = [v["schema"] for v in registry._index.values() if v["server_id"] == sid]
            if tools:
                return tools
            # 还没索引，尝试连一次
            tools_count = await registry.connect_server(srv)
            if tools_count > 0:
                return api_server_tools(sid)
            raise HTTPException(502, "stdio 服务无可用工具")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"MCP 服务 [{sid}] 连接失败: {traceback.format_exc()}")
        raise HTTPException(502, f"无法连接 MCP 服务: {e}")


@router.post("/servers/{sid}/call")
async def api_server_call(sid: int, payload: dict = Body(...)):
    name = payload["name"]
    args = payload.get("args", {})
    try:
        srv = _lookup_server(sid)
    except HTTPException:
        raise
    transport = srv.get("transport", "http")
    try:
        if transport == "http":
            client = await _get_client_for(sid)
            try:
                rows = await client.call(name, args)
            except RuntimeError:
                await client.connect()
                rows = await client.call(name, args)
        else:
            # stdio: 通过 ToolRuntime 调用（Executor 负责临时子进程生命周期）
            result = await get_tool_runtime().execute(name, args)
            rows = result["rows"]
        return {"tool": name, "rows": rows, "count": len(rows)}
    except KeyError:
        raise HTTPException(404, f"工具不存在: {name}")
    except Exception as e:
        logger.error(f"MCP 调用失败 [{sid}/{name}]: {traceback.format_exc()}")
        raise HTTPException(502, f"MCP 调用失败: {e}")


# ━━ 通用 MCP proxy（传 URL，兼容旧前端） ━━

@router.post("/proxy/tools")
async def api_proxy_tools(payload: dict = Body(...)):
    url = payload.get("url", MCP_URL)
    try:
        client = await get_mcp_client(url)
        return client.tools
    except Exception as e:
        logger.error(f"MCP 连接失败 [{url}]: {traceback.format_exc()}")
        raise HTTPException(502, f"无法连接 MCP 服务: {e}")


@router.post("/proxy/call")
async def api_proxy_call(payload: dict = Body(...)):
    url = payload.get("url", MCP_URL)
    name = payload["name"]
    args = payload.get("args", {})
    try:
        client = await get_mcp_client(url)
    except Exception as e:
        logger.error(f"MCP 连接失败 [{url}]: {traceback.format_exc()}")
        raise HTTPException(502, f"无法连接 MCP 服务: {e}")
    try:
        rows = await client.call(name, args)
        return {"tool": name, "rows": rows, "count": len(rows)}
    except KeyError:
        raise HTTPException(404, f"工具不存在: {name}")
    except RuntimeError:
        try:
            await client.connect()
            rows = await client.call(name, args)
            return {"tool": name, "rows": rows, "count": len(rows)}
        except Exception as e:
            raise HTTPException(502, f"MCP 重连失败: {e}")
    except Exception as e:
        logger.error(f"MCP 调用失败 [{url}/{name}]: {traceback.format_exc()}")
        raise HTTPException(502, f"MCP 调用失败: {e}")


# ━━ 本地 MCP 快捷方式 ━━

@router.get("/tools")
async def api_list_tools():
    client = await get_mcp_client()
    return client.tools


@router.get("/tools/all")
async def api_list_all_tools():
    """列出所有已注册 MCP 工具（含来源 server 信息），尝试连接未索引的 server"""
    registry = get_registry()
    servers = registry._list_servers()
    name_map = {s["id"]: s["name"] for s in servers}

    # 尝试索引尚未连接的 server（本地立即成功，远程/stdio 失败则跳过）
    for srv in servers:
        try:
            await registry.connect_server(srv)
        except Exception:
            continue

    tools = []
    for name, entry in registry._index.items():
        schema = entry.get("schema") or {}
        tools.append({
            "name": name,
            "description": schema.get("description", ""),
            "inputSchema": schema.get("inputSchema", {}),
            "server_id": entry.get("server_id"),
            "server_name": name_map.get(entry.get("server_id"), f"server-{entry.get('server_id')}"),
            "transport": entry.get("transport", "http"),
        })
    tools.sort(key=lambda t: (t["server_name"], t["name"]))
    return tools


@router.get("/tools/{name}")
async def api_get_tool(name: str):
    """查询单个工具信息（未索引则懒加载）"""
    registry = get_registry()
    entry = registry._index.get(name)
    if not entry:
        entry = await registry._lazy_load_tool(name)
    if not entry:
        raise HTTPException(404, f"工具不存在: {name}")
    schema = entry.get("schema") or {}
    servers = registry._list_servers()
    name_map = {s["id"]: s["name"] for s in servers}
    return {
        "name": name,
        "description": schema.get("description", ""),
        "inputSchema": schema.get("inputSchema", {}),
        "server_id": entry.get("server_id"),
        "server_name": name_map.get(entry.get("server_id"), f"server-{entry.get('server_id')}"),
        "transport": entry.get("transport", "http"),
    }


@router.post("/tools/{name}/call")
async def api_call_tool(name: str, args: dict = Body(...)):
    """调用工具 — 经 ToolRuntime 执行（Registry 解析 → Executor 路由到正确的 server）"""
    try:
        result = await get_tool_runtime().execute(name, args)
    except KeyError:
        raise HTTPException(404, f"工具不存在: {name}")
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return result


# ━━ 热拔插绑定 ━━

@router.get("/bindings")
def api_list_bindings():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT agent_key, tool_name FROM agent_mcp_bindings ORDER BY agent_key")
            rows = cur.fetchall()
    result: dict[str, list[str]] = {}
    for r in rows:
        result.setdefault(r["agent_key"], []).append(r["tool_name"])
    return result


@router.put("/bindings/{agent_key}")
def api_save_bindings(agent_key: str, tools: list[str] = Body(...)):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM agent_mcp_bindings WHERE agent_key = %s", (agent_key,))
            for tn in tools:
                cur.execute("INSERT INTO agent_mcp_bindings (agent_key, tool_name) VALUES (%s, %s)", (agent_key, tn))
        conn.commit()
    return {"ok": True, "agent_key": agent_key, "tools": tools}
