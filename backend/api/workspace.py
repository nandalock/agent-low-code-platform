"""Workspace API —— 会话工作区的**只读**浏览器

沙箱是「进得去、出不来」的：模型在隔离目录里下载 PDF、写总结，文件躺在
``<SANDBOX_WORKSPACE_ROOT>/<session_id>/``，用户看不见也取不走。本模块是那扇
观察窗 —— 让人能看见、预览、下载沙箱的产出。

边界（第一原则）：**工作区是沙箱的地盘，唯一写方是容器里的模型**。浏览器只读，
所以这里不提供任何写 / 删 / 改名接口 —— 少一个写接口，就少一整类
「模型正在写、用户正在删」的竞态。

安全模型：工作区内容是**模型**写的，模型不可信。因此：

  - 所有用户传入的路径都经
    :func:`~backend.tool_system.sandbox.workspace.resolve_workspace_member`
    解析并做越界校验（含符号链接）；越界返回 **400 而非 500** —— 越界是
    预期内的输入，不是服务器故障。
  - 预览恒以 ``text/plain`` 内联，绝不按文件名猜 Content-Type。猜成 ``text/html``
    就是拿 API 自身的源当存储型 XSS 的宿主（内容由模型书写）。
  - 二进制不内联，超大不给预览（但仍可下载 —— 下载是流式的，不吃内存）。

租户隔离：``session_headers`` 没有 tenant 列，会话归属写在 chat 域的
``conversations.session_id`` 上。故每个端点都先验「该 session 属于本租户」，
不属于即 404 —— 用 404 而非 403，不泄漏会话是否存在。当前所有 Session 都经
HTTP chat 创建并回写该映射（见 ``api/agents.py``），因此不会漏掉真实会话。
"""
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import FileResponse

from backend.core.connection import get_conn
from backend.tool_system.sandbox.errors import SandboxUnavailableError
from backend.tool_system.sandbox.workspace import (
    resolve_workspace_member,
    resolve_workspace_root,
    session_workspace_path,
)

router = APIRouter(prefix="/api/workspace", tags=["Workspace"])

#: 列目录返回的最大条目数。超出则截断并置 ``truncated``，**不静默吞掉**。
MAX_LIST_ENTRIES = 2000
#: 会话列表上限（侧栏用，不需要全量）。
MAX_SESSIONS = 100
#: 候选会话的扫描上限。判据在文件系统上，SQL 层没法先过滤，所以给个安全阀
#: 防止会话表长大后每次打开页面都全表扫。
_SESSION_SCAN_LIMIT = 1000
#: 超过此大小不给预览（只给下载）。
MAX_PREVIEW_BYTES = 1024 * 1024
#: 二进制嗅探窗口。只看头部 —— 大文件不该为了「判类型」被整个读一遍。
SNIFF_BYTES = 8192


# ── 内部工具 ──


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _content_disposition(name: str, *, inline: bool = False) -> str:
    """RFC 5987：ASCII 回退 + UTF-8 百分号编码。

    模型产出的文件名常含中文，只有 ``filename*`` 才能在浏览器里正确落地；
    但老客户端只认 ``filename=``，故两者都给。
    """
    disposition = "inline" if inline else "attachment"
    ascii_name = name.encode("ascii", "replace").decode("ascii").replace('"', "_").replace("\\", "_")
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


def _sniff_image(head: bytes) -> str | None:
    """按**魔数**判定这是不是一张可内联的图片；是则返回 MIME，否则 ``None``。

    类型只由内容决定，**完全不看文件名** —— 名字是模型起的，把类型决定权交给
    它就等于允许它把任意字节声明成任意类型。

    白名单刻意很短，且**永远不含 SVG**：SVG 是可携带脚本的 XML，内联它等于把
    模型写的内容当代码跑在 API 源上 —— 那是存储型 XSS，不是图片预览。
    """
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"BM"):
        return "image/bmp"
    return None


def _looks_binary(head: bytes) -> bool:
    """二进制嗅探：NUL 字节或 UTF-8 解码失败（git 同款启发式）。

    窗口尾部可能切断一个多字节字符 —— 那是**误判**而非二进制，得放过：
    只有在窗口读满（说明后面还有内容）且错误贴着末尾时才这么判。
    """
    if b"\x00" in head:
        return True
    if not head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as e:
        return not (len(head) >= SNIFF_BYTES and e.end >= len(head) - 4)
    return False


def _stat_or_none(p: Path) -> os.stat_result | None:
    """``stat`` 但绝不抛 —— 悬空符号链接、权限不足都只当「读不到」。"""
    try:
        return p.stat()
    except OSError:
        return None


def _entry(root: Path, p: Path) -> dict:
    """一个目录项的元信息。

    ``outside`` 标记「指向工作区之外的符号链接」：这种条目照样列出来（诚实地
    显示它存在），但 ``is_dir`` 强制为 False —— 前端据此不让下钻，因为点进去
    只会被 :func:`resolve_workspace_member` 以 400 拒掉。
    """
    is_link = p.is_symlink()
    outside = False
    if is_link:
        try:
            outside = not p.resolve().is_relative_to(root)
        except (OSError, RuntimeError):  # 自环 / 过长：一律当作越界（fail-closed）
            outside = True

    st = _stat_or_none(p)
    is_dir = bool(st and stat.S_ISDIR(st.st_mode)) and not outside
    return {
        "name": p.name,
        "is_dir": is_dir,
        "size": 0 if is_dir or st is None else st.st_size,
        "mtime": _iso(st.st_mtime) if st else None,
        "is_link": is_link,
        "outside": outside,
    }


def _agent_key_from_channel(channel: str | None) -> str | None:
    """会话的 agent 归属，从 ``channel`` 解出（``agent:<key>``，见 api/agents.py）。

    **别读 ``conversations.agent_key``** —— 那一列存在但从来没被写过，全是 NULL。
    channel 是当前唯一可靠的归属信号；非 ``agent:`` 前缀（xianyu / test 等其它
    通道）返回 None，由前端归到「其它」。
    """
    prefix = "agent:"
    if channel and channel.startswith(prefix):
        return channel[len(prefix):] or None
    return None


def _resolve_session_workspace(
    cwd: str | None, session_id: str, deployment_root: Path,
) -> Path | None:
    """会话工作区根；不可用（非法 / 越界 / 符号链接自环）返回 ``None``。

    **解析链必须与执行侧逐字一致** —— 那边是 `sandbox/runtime.py` 的
    ``session_policy()``：``root = cwd or workspace_for_session(session_id)``。
    读侧偏离这条链，就会出现「页面显示的目录 ≠ 沙箱实际写入的目录」。

    ``cwd`` 是**覆盖**，不是必填。这一点曾经被搞错过：``session_headers.cwd``
    当前**恒为 NULL** —— 它只在会话创建时写一次（``agent_runtime.py`` 的
    ``persistence.create``），而工作区是首次工具调用才落地的，那时 header 早已
    建好，且 ``create`` 是 ``ON CONFLICT DO NOTHING``，后续不会再改写。
    所以「读 cwd、不自己拼」是错的：会话真的用了沙箱时，它反而是空的那个。

    ``cwd`` 即便由后端写入也**按不可信处理**（纵深防御）：必须是部署根的**真子
    目录**。落在根上 = 跨会话可见；越出根 = 直读宿主。两者都拒。
    """
    if cwd:
        try:
            ws = Path(cwd).resolve()
        except (OSError, RuntimeError):
            return None
        if not ws.is_relative_to(deployment_root) or ws == deployment_root:
            return None
        return ws
    try:
        return session_workspace_path(str(deployment_root), session_id)
    except ValueError:
        return None


def _workspace_root_for(session_id: str, tenant_id: int) -> Path:
    """该会话的工作区根（宿主绝对路径），并确认它属于本租户。

    Raises:
        HTTPException: 404 会话不存在 / 无工作区 / 不属于本租户（三者不区分）；
            503 沙箱工作区未配置。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT sh.cwd FROM session_headers sh
                   WHERE sh.session_id = %s
                     AND EXISTS (SELECT 1 FROM conversations c
                                 WHERE c.session_id = sh.session_id AND c.tenant_id = %s)""",
                (session_id, tenant_id),
            )
            row = cur.fetchone()
    if row is None:
        raise HTTPException(404, f"会话不存在、无工作区，或不属于本租户: {session_id}")

    try:
        deployment_root = Path(resolve_workspace_root()).resolve()
    except (SandboxUnavailableError, ValueError) as e:
        raise HTTPException(503, f"沙箱工作区未配置: {e}")

    ws = _resolve_session_workspace(row["cwd"], session_id, deployment_root)
    if ws is None:
        raise HTTPException(404, "会话工作区不可用")
    if not ws.is_dir():
        raise HTTPException(404, "会话工作区不存在")
    return ws


def _member_or_400(root: Path, rel: str) -> Path:
    """解析用户传入的相对路径，越界 → 400（不是 500）。"""
    try:
        return resolve_workspace_member(str(root), rel)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── 端点 ──


@router.get("/sessions")
def api_list_workspace_sessions(x_tenant_id: int = Header(alias="X-Tenant-ID")) -> dict:
    """列出本租户**工作区目录存在**的会话（左栏导航用）。

    不能按 ``cwd IS NOT NULL`` 过滤：那一列当前恒为 NULL（见
    :func:`_resolve_session_workspace`），过滤了就等于永远返回空。判据只能是
    文件系统本身 —— 目录在不在。目录不在的会话跳过，因为列出来点进去必然报错。

    注意「目录存在」比「沙箱被用过」宽：``session_policy()`` 挂在每次工具调用
    上，所以会话只要调过任何工具（哪怕只是 MCP），目录就已建好。空工作区照样
    列出来（UI 有对应的空态），不假装它不存在。

    会话可能有多个 conversation，取最近一个当标签。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM (
                       SELECT DISTINCT ON (sh.session_id)
                              sh.session_id, sh.cwd, sh.updated_at,
                              c.customer_name, c.channel, c.id AS conversation_id
                       FROM session_headers sh
                       JOIN conversations c
                         ON c.session_id = sh.session_id AND c.tenant_id = %s
                       ORDER BY sh.session_id, c.id DESC
                   ) t ORDER BY t.updated_at DESC NULLS LAST
                   LIMIT %s""",
                (x_tenant_id, _SESSION_SCAN_LIMIT),
            )
            rows = cur.fetchall()

    try:
        deployment_root = Path(resolve_workspace_root()).resolve()
    except (SandboxUnavailableError, ValueError) as e:
        raise HTTPException(503, f"沙箱工作区未配置: {e}")

    items: list[dict] = []
    for r in rows:
        ws = _resolve_session_workspace(r["cwd"], r["session_id"], deployment_root)
        if ws is None or not ws.is_dir():
            continue  # 配置变更后的陈旧 cwd / 还没建过目录：跳过，别让侧栏出现死链接
        try:
            entry_count = len(os.listdir(ws))
        except OSError:
            entry_count = 0
        items.append({
            "session_id": r["session_id"],
            "agent_key": _agent_key_from_channel(r["channel"]),
            # 前端拿它续聊：带上 conversation_id 后端的 _resolve_conversation 才不会
            # 每轮新建一个会话，session_id 也才能续上同一个工作区
            "conversation_id": r["conversation_id"],
            "label": r["customer_name"] or r["channel"] or r["session_id"][:8],
            "channel": r["channel"],
            "updated_at": _iso(r["updated_at"].timestamp()) if r["updated_at"] else None,
            "entries": entry_count,
        })

    # 扫描触顶或结果超上限，两种截断都要说出来 —— 静默截断会被读成「就这些」
    truncated = len(rows) >= _SESSION_SCAN_LIMIT or len(items) > MAX_SESSIONS
    return {"items": items[:MAX_SESSIONS], "truncated": truncated}


@router.get("/{session_id}/files")
def api_list_files(
    session_id: str,
    path: str = Query(default=""),
    x_tenant_id: int = Header(alias="X-Tenant-ID"),
) -> dict:
    """列出工作区内某个目录。``path`` 是工作区内的相对路径，空串表示根。"""
    root = _workspace_root_for(session_id, x_tenant_id)
    target = _member_or_400(root, path)

    if not target.exists():
        raise HTTPException(404, f"路径不存在: {path or '/'}")
    if not target.is_dir():
        raise HTTPException(400, f"不是目录: {path}")

    entries: list[dict] = []
    truncated = False
    try:
        with os.scandir(target) as it:
            for i, de in enumerate(it):
                if i >= MAX_LIST_ENTRIES:
                    truncated = True
                    break
                entries.append(_entry(root, Path(de.path)))
    except OSError as e:
        raise HTTPException(400, f"无法读取目录: {e}")

    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return {"session_id": session_id, "path": path, "entries": entries, "truncated": truncated}


@router.get("/{session_id}/file")
def api_get_file(
    session_id: str,
    path: str = Query(default=""),
    inline: int = Query(default=0),
    x_tenant_id: int = Header(alias="X-Tenant-ID"),
):
    """下载工作区里的一个文件；``?inline=1`` 时按文本返回（预览用）。

    下载不限大小（``FileResponse`` 流式，不吃内存）；预览限：超大 → 413，
    二进制 → 415。两者都保留下载路径，用户不会走进死胡同。
    """
    root = _workspace_root_for(session_id, x_tenant_id)
    target = _member_or_400(root, path)

    if not target.exists():
        raise HTTPException(404, f"文件不存在: {path}")
    if not target.is_file():
        raise HTTPException(400, f"不是文件: {path}")

    name = target.name
    headers = {"X-Content-Type-Options": "nosniff"}

    if not inline:
        headers["Content-Disposition"] = _content_disposition(name)
        # 不按文件名猜类型：下载一律 octet-stream，让浏览器老实存盘
        return FileResponse(target, media_type="application/octet-stream", headers=headers)

    try:
        size = target.stat().st_size
    except OSError as e:
        raise HTTPException(400, f"无法读取文件: {e}")
    if size > MAX_PREVIEW_BYTES:
        raise HTTPException(
            413, f"文件 {size} 字节，超过预览上限 {MAX_PREVIEW_BYTES} 字节；请下载查看",
        )
    try:
        with open(target, "rb") as f:
            head = f.read(SNIFF_BYTES)
    except OSError as e:
        raise HTTPException(400, f"无法读取文件: {e}")

    headers["Content-Disposition"] = _content_disposition(name, inline=True)

    # 图片先判：它的 MIME 由魔数给出（见 _sniff_image），能安全内联
    image_mime = _sniff_image(head)
    if image_mime is not None:
        return FileResponse(target, media_type=image_mime, headers=headers)

    if _looks_binary(head):
        raise HTTPException(415, "二进制文件不支持预览；请下载")

    # 其余一律 text/plain：内容由模型书写，按文件名猜类型 = 把 API 源当 XSS 宿主。
    # CSP sandbox 是纵深防御 —— 万一有人直接开这个 URL，也跑不了脚本。
    headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return FileResponse(target, media_type="text/plain; charset=utf-8", headers=headers)
