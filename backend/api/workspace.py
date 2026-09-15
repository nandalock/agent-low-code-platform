"""Workspace API —— 会话工作区的**只读**浏览器

沙箱是「进得去、出不来」的：模型在隔离目录里下载 PDF、写总结，文件躺在
``<SANDBOX_WORKSPACE_ROOT>/<session_id>/``，用户看不见也取不走。本模块是那扇
观察窗 —— 让人能看见、预览、下载沙箱的产出。

边界（第一原则）：**工作区是沙箱的地盘**。浏览器不提供删除 / 改名 / 移动 / 写
内容的接口 —— 少一个写接口，就少一整类「模型正在写、用户正在删」的竞态。

写入口只有两个，都是**窄**的：

  - **用户上传**：``.dsh-drops/`` 是给用户拖文件进会话的专属子目录（对齐
    DeepSeek Harness 的 drop 区）。上传与沙箱都能写它，模型（bash 工具）与
    用户都能读它。
  - **新建空条目**（``POST /{sid}/files`` 与 ``POST /folders``）：在已存在的
    目录里建**零字节空文件**或空目录。不写内容、不改名、不删除 —— 用户想往里
    填东西，要么上传覆盖，要么让模型写。

两者因此走与读不同的防护链：路径经 :func:`resolve_workspace_member` 挡「事前
种下的」符号链接，落盘经临时文件 + ``os.replace``（上传）或 ``O_EXCL``（新建）
挡「写入瞬间换上的」，且都**不覆盖任何既有条目**。

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
import itertools
import os
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from backend.agents import get_agent
from backend.agents.config_service import get_agent_definition
from backend.agents.runtime.session import get_session_persistence
from backend.agents.runtime.session.events import SessionHeader, new_session_id
from backend.core.connection import get_conn
from backend.tool_system.sandbox.errors import SandboxUnavailableError
from backend.tool_system.sandbox.workspace import (
    READ_ROOTS_ENV,
    WRITE_ROOTS_ENV,
    mount_name,
    normalize_host_path,
    parse_roots,
    path_in_roots,
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
#: 用户上传专属子目录（对齐 DeepSeek Harness 的 ``.dsh-drops`` 拖放区）。
#: 点开头：目录列表里排前面，人与模型一眼分清「人给的」与「模型产的」。
UPLOADS_SUBDIR = ".dsh-drops"
#: 单个上传文件的大小上限。对齐 DSH 拖放插件（dsh-web-preview-panel）的 64 MB。
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
#: 上传流式落盘的块大小（决定超限的检出粒度，不决定内存占用）。
UPLOAD_CHUNK = 1024 * 1024
#: PDF 预览的大小上限（FileResponse 流式返回不吃内存，上限只防病态超大文件）。
#: 与文本/图片的 1 MB 分开 —— 论文 PDF 常态 2~20 MB，套用 1 MB 等于关掉这个预览。
MAX_PDF_PREVIEW_BYTES = 100 * 1024 * 1024
#: 二进制嗅探窗口。只看头部 —— 大文件不该为了「判类型」被整个读一遍。
SNIFF_BYTES = 8192

#: 上传临时文件的全局序号（itertools.count 的 next 在 GIL 下原子）：
#: 同进程并发上传同名文件时，各自拿到不同的临时名，O_EXCL 不互相踩。
_UPLOAD_TMP_SEQ = itertools.count()

#: 写根在 backend 容器内的挂载基路径（compose 约定：``- D:/jk/Nexus:/srv/host-write/Nexus``）。
#: backend 是 Linux 容器，看不见宿主的 ``D:/jk/Nexus``——daemon 挂载只解决
#: **沙箱容器**的视角，API 进程读宿主目录必须走这条 compose 挂载。
_BACKEND_WRITE_MOUNT_BASE = "/srv/host-write"

#: 读根（``SANDBOX_READ_ROOTS``）在 backend 容器内的挂载基路径，镜像上面那条。
#: 只读挂载，不构成写面。读根**可以**被选为工作区（判据只看浏览根名与目录是否存在），
#: 只是那种会话默认落 read-only、写入要过升权——见 ``sandbox/runtime.py`` 的
#: :func:`session_default_mode` 与本模块的 :func:`_is_writable`。
#: 与沙箱容器的 ``/mnt/read/<name>`` 同名同序（同一个 :func:`mount_name`），
#: 用户在选择器里看到的盘符和模型在沙箱里看到的是同一个名字。
_BACKEND_READ_MOUNT_BASE = "/srv/host-read"


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


def _sniff_pdf(head: bytes) -> bool:
    """按魔数判定 PDF：``%PDF-`` 开头。

    与 :func:`_sniff_image` 同规则 —— 只认内容，不看文件名。``%PDF`` 不带
    ``-`` 不算：魔数按最小完整前缀收，宁可漏判（头部带垃圾字节的老 PDF 走
    415 下载）也不把恰巧以 ``%PDF`` 开头的文本当 PDF 送进 viewer。
    """
    return head.startswith(b"%PDF-")


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


def _is_symlink_or_false(p: Path) -> bool:
    """``p.is_symlink()`` 但绝不抛。

    **``Path.is_symlink()`` 会原样抛出 ``lstat`` 的 OSError**——吞掉异常的是
    ``os.path.islink``，别记混。整盘浏览必然撞上读不到元数据的系统文件
    （Windows 的 ``DumpStack.log.tmp``、``pagefile.sys``），一个条目读不到
    不该让整次列举失败。
    """
    try:
        return p.is_symlink()
    except OSError:
        return False


def _entry(root: Path, p: Path) -> dict:
    """一个目录项的元信息。

    ``outside`` 标记「指向工作区之外的符号链接」：这种条目照样列出来（诚实地
    显示它存在），但 ``is_dir`` 强制为 False —— 前端据此不让下钻，因为点进去
    只会被 :func:`resolve_workspace_member` 以 400 拒掉。
    """
    is_link = _is_symlink_or_false(p)
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
        # 浏览根优先：cwd 可能是「新建会话选了某个根目录」的宿主路径（写根如
        # D:/jk/Nexus/proj，读根如 C:/Users）——backend 容器看不到宿主的盘，
        # 文件操作走 compose 挂进来的 /srv/host-write|host-read/<名> 等价路径。
        backend_ws = _host_cwd_to_backend(_browse_roots(), cwd)
        if backend_ws is not None:
            return backend_ws
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


def _session_workspace_root(session_id: str, tenant_id: int) -> Path:
    """该会话的工作区根（宿主绝对路径），并确认它属于本租户。

    **只解析、不要求目录存在** —— 创建与否是读写端点的性质（读：不存在即
    404；写：可以建）。租户归属查 ``conversations.session_id``（session_headers
    没有 tenant 列），不属于即 404。

    Raises:
        HTTPException: 404 会话不存在 / 不属于本租户（不区分，不泄漏存在性）；
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
    return ws


def _workspace_root_for(session_id: str, tenant_id: int) -> Path:
    """读路径的工作区根：目录不存在即 404（读不得凭空建目录）。"""
    ws = _session_workspace_root(session_id, tenant_id)
    if not ws.is_dir():
        raise HTTPException(404, "会话工作区不存在")
    return ws


#: 内核挂载表，用来看某个路径所在的挂载有没有 ``ro`` 标志。
_MOUNTS_PATH = "/proc/self/mounts"


def _mount_is_read_only(path: str, *, mounts_path: str = _MOUNTS_PATH) -> bool | None:
    """``path`` 所在挂载是否只读；读不到挂载表时返回 ``None``（未知）。

    查挂载表并**取最长匹配的挂载点**看 ``ro`` 标志，而不是 ``os.access(W_OK)``：
    容器里的 backend 常以 root 跑，而 ``access(2)`` 对 root 的 ``W_OK`` 几乎
    恒真，探不出 ``:ro``。

    前缀按**段**对齐：``/srv/host-read/C2`` 不算落在 ``/srv/host-read/C`` 里。
    """
    try:
        with open(mounts_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return None
    best: tuple[int, bool] | None = None
    for line in lines:
        parts = line.split()
        if len(parts) < 4:
            continue
        mp = parts[1].replace("\\040", " ")   # 挂载表用空格分段，路径里的空格转义成 \040
        if not (path == mp or path.startswith(mp.rstrip("/") + "/")):
            continue
        if best is None or len(mp) > best[0]:
            best = (len(mp), "ro" in parts[3].split(","))
    return None if best is None else best[1]


def _is_read_only_mount(backend_path: Path) -> bool:
    """backend 容器内该路径是否落在**只读挂载**上。

    **这不是安全边界，只是提示**。真正拦下写入的是内核的 ``EROFS``，调用方的
    ``except OSError`` 会把它转成 400。它的用处有二：让前端把必然失败的按钮
    提前禁用，以及把内核术语换成一句人话。

    因此判不出来时一律当作**可写**：宁可漏判（用户点了、拿到一句
    「Read-only file system」），也不能误判成只读、把一个能写的位置挡在外面。
    早先这里是按容器路径前缀（``/srv/host-read``）判的 —— 那是把「读轴上挂的」
    当成了「写不进去」，两者只是**当时**恰好等价。
    """
    return _mount_is_read_only(str(backend_path)) is True


def _writable_session_root(session_id: str, tenant_id: int, action: str) -> Path:
    """写路径的会话工作区根；目录不存在就建。只读位置 → 400。

    上传与新建共用这条链，``action`` 只进错误文案 —— 「不能上传文件」和「不能
    新建文件或文件夹」是两件事，对着用户说错哪一件比不说还糟。
    """
    ws = _session_workspace_root(session_id, tenant_id)
    if _is_read_only_mount(ws):
        # 只读根当工作区：读没问题，建目录/落文件必然 EROFS。提前拦，说人话。
        raise HTTPException(
            400,
            f"该会话的工作区是只读位置，不能{action}；"
            "需要写入请在会话里批准一次升权，或把工作区换到可写目录。",
        )
    try:
        ws.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(400, f"无法创建会话工作区: {e}")
    return ws


def _workspace_root_for_upload(session_id: str, tenant_id: int) -> Path:
    """上传的工作区根：目录不存在就建。

    上传是用户自己的写入，把「目录存在才可写」放宽到「不存在就建」——沙箱
    第一次工具调用之前用户先拖文件进来，建的正是沙箱将来会用的同一目录
    （执行侧 ``session_policy`` 的 ``cwd or workspace_for_session`` 链，
    见 :func:`_resolve_session_workspace`），两边不会分家。
    """
    return _writable_session_root(session_id, tenant_id, "上传文件")


def _member_or_400(root: Path, rel: str) -> Path:
    """解析用户传入的相对路径，越界 → 400（不是 500）。"""
    try:
        return resolve_workspace_member(str(root), rel)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ── 上传（用户写区）──


def _sanitize_upload_name(raw: str | None) -> str:
    """上传文件名 → 纯 basename。

    文件名来自浏览器，但浏览器可以被改、请求可以被抓包重放，所以同样按
    不可信输入处理：反斜杠转正斜杠（Windows 客户端的 ``C:\\fakepath\\a.pdf``
    到此只剩 basename）、按 ``/`` 拆段取最后一段、剥首尾空白。空名 / 点段 /
    控制字符 / 超长（255 字节，POSIX NAME_MAX）一律拒。扩展名保留但不参与
    任何类型判定 —— 类型永远由魔数说话（:func:`_sniff_image` / :func:`_sniff_pdf`）。
    """
    if not raw:
        raise HTTPException(400, "文件名为空")
    name = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or name in (".", ".."):
        raise HTTPException(400, f"非法文件名: {raw!r}")
    if len(name.encode("utf-8")) > 255:
        raise HTTPException(400, f"文件名超过 255 字节: {name[:50]}…")
    if any(ord(c) < 32 for c in name):
        raise HTTPException(400, f"文件名含控制字符: {raw!r}")
    return name


async def _save_upload(target: Path, file, *, limit: int) -> tuple[int, bool]:
    """把上传流写到 ``target``。返回 ``(已写字节数, 是否超限)``；超限时半成品已删。

    落盘 = 同目录临时文件 + ``os.replace``。rename(2) 替换的是**符号链接本身**
    而不是它指向的目标：模型在 :func:`resolve_workspace_member` 通过之后、
    写入之前把目标换成指向 ``/etc/passwd`` 的链接（竞态窗口）时，被顶掉的
    是链接、不是 ``/etc/passwd``。resolve 挡「事前种下的」链接，os.replace
    挡「写入瞬间换上的」——前者是主力，后者便宜，两道口都收。
    """
    tmp = target.with_name(f".{target.name}.upload-{next(_UPLOAD_TMP_SEQ)}")
    written = 0
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except OSError as e:
        raise HTTPException(400, f"无法创建上传临时文件: {e}")
    over = False
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await file.read(UPLOAD_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    over = True
                    break
                out.write(chunk)
    except OSError as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise HTTPException(400, f"写入失败: {e}")
    if over:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return written, True
    os.replace(tmp, target)
    return written, False


# ── 新建（用户写区：空文件 / 空目录）──

#: 可新建的条目类型（与前端 ``EntryKind`` 一致）。``type`` 字段的值域收在这里。
ENTRY_KINDS = ("file", "dir")


def _validate_new_name(raw: str | None) -> str:
    """新建条目的名字校验，返回名字本身（**不取 basename**）。

    与 :func:`_sanitize_upload_name` 分工不同：上传是「浏览器塞来一个路径，我
    只要那个文件」，剥成 basename 是帮忙；新建是「用户要求**在这个目录里**
    建这个东西」，名字里带分隔符说明他要的是别的目录 —— 静默改写成另一个
    名字比报错更坏（他以为建在了 A，实际建在 B）。

    前导点**不拒**（``.gitignore`` / ``.env`` 是正当用法，上传侧也不禁）。
    """
    if not raw:
        raise HTTPException(400, "名字为空")
    name = raw.strip()
    if not name or name in (".", ".."):
        raise HTTPException(400, f"非法名字: {raw!r}")
    if "/" in name or "\\" in name:
        raise HTTPException(400, f"名字不能含路径分隔符: {raw!r}")
    if len(name.encode("utf-8")) > 255:
        raise HTTPException(400, f"名字超过 255 字节: {name[:50]}…")
    if any(ord(c) < 32 for c in name):
        raise HTTPException(400, f"名字含控制字符: {raw!r}")
    return name


def _validate_kind(raw: object) -> str:
    """``type`` 只能是 :data:`ENTRY_KINDS` 里的值；其它一律 400。

    **不给默认值**：把缺失/拼错的 type 猜成「文件」，等于在用户想要目录时
    悄悄建一个文件，他得先删掉再重来。
    """
    if raw not in ENTRY_KINDS:
        raise HTTPException(400, f"type 只能是 {' 或 '.join(ENTRY_KINDS)}: {raw!r}")
    return str(raw)


def _create_child(target: Path, kind: str) -> None:
    """在 ``target`` 建一个空文件（0o644）或空目录；已存在 → 400。

    两条纪律：

      - **不覆盖任何既有条目**。文件走 ``O_EXCL``、目录走 ``exist_ok=False``，
        并发同名只有一个请求能成功。``O_CREAT|O_EXCL`` 对符号链接**一律以
        EEXIST 拒绝**（不管链接指向哪、是否悬空），所以这里也不会顺着链接写出去。
      - **不建中间目录**（``parent.mkdir(parents=False)``）：整条路径凭空出现
        会把用户拼错的路径变成既成事实，不如报错。

    Raises:
        HTTPException: 400 已存在 / 其它 OSError 转人话。
    """
    try:
        if kind == "dir":
            target.mkdir(parents=False, exist_ok=False)
        else:
            os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
    except FileExistsError:
        raise HTTPException(400, f"已存在同名文件或文件夹: {target.name}")
    except OSError as e:
        raise HTTPException(400, f"无法创建「{target.name}」: {e}")


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


# ── 新建会话（选 agent + 选工作文件夹，对齐 DSH 的「选择工作区」） ──


def _write_roots() -> tuple[str, ...]:
    """部署配置的沙箱写根（``SANDBOX_WRITE_ROOTS``，逗号分隔宿主绝对路径）。"""
    return parse_roots(os.environ.get(WRITE_ROOTS_ENV))


def _read_roots() -> tuple[str, ...]:
    """部署配置的沙箱读根（``SANDBOX_READ_ROOTS``）。只读；也能当工作区（默认只读）。"""
    return parse_roots(os.environ.get(READ_ROOTS_ENV))


@dataclass(frozen=True)
class _BrowseRoot:
    """文件系统选择器里的一个根：它能看见的一块宿主路径。"""

    name: str          # 容器内挂载名，也是 ``path`` 的第一段
    host: str          # 宿主绝对路径（选中后写进 session cwd 的就是它）
    container: Path    # backend 容器内可见路径
    writable: bool     # 写入是否**无需授权**（是否落在写根内）；两类根都能当工作区


def _browse_roots() -> list[_BrowseRoot]:
    """选择器可浏览的根 = 写根 + 读根；**两类都能当工作区**。

    两类根的差别不在「能不能选」，而在**写要不要授权**（见
    :func:`_is_writable` 与 ``sandbox/runtime.py`` 的 ``session_default_mode``）：
    选读根当工作区，会话默认落 ``read-only``，模型要写就得走升权审批。这是
    「工作区 = 默认信任的边界，沙箱 = 执行时的硬限制」两层叠加的落地方式——
    选一个目录不等于把它的写权限打包送出。

    两条约定：

    - **只收容器内真的存在的根**。配了 ``SANDBOX_READ_ROOTS`` 却没在 compose 里
      挂进 backend 的根会被滤掉——否则选择器里会出现一个点进去必 404 的幽灵条目，
      用户只会以为坏了。
    - **写根优先**。同名时读根让位：写根本来就既可浏览又可写，重复列没有意义。
      同序也是 :func:`_folder_value` 的取名依据（先命中的根名即提交值）。
    """
    out: list[_BrowseRoot] = []
    seen: set[str] = set()
    for raw in _write_roots():
        host = normalize_host_path(raw)
        # 与 _backend_visible 同一套命名（裸 basename）
        name = host.rstrip("/").rsplit("/", 1)[-1]
        if name in seen:
            continue
        seen.add(name)
        out.append(_BrowseRoot(name, host, _backend_visible(host), True))
    for raw in _read_roots():
        host = normalize_host_path(raw)
        name = mount_name(host)
        if name in seen:
            continue
        container = Path(_BACKEND_READ_MOUNT_BASE) / name
        if not container.is_dir():
            continue
        seen.add(name)
        out.append(_BrowseRoot(name, host, container, False))
    return out


def _folder_value(host_path: str, roots: list[_BrowseRoot]) -> str | None:
    """宿主路径 → ``POST /sessions`` 认的 ``folder`` 值（**浏览根名** + 根内相对路径）。

    命名与 :func:`_browse_roots` 的根名同一套，于是「浏览路径长什么样，提交的就是
    什么」：``C`` / ``C/Users`` / ``Nexus/proj``，前端把 ``segments.join('/')``
    交回来即可。这里曾经要做「浏览路径 → 写根名」的翻译（从只读的 ``D:/`` 点进
    写根时，path 是 ``D/jk/Nexus`` 而建会话只认 ``Nexus``），那是因为当时只有写根
    能当工作区；现在读根同样可选，两套命名合并成一套，翻译那一层随之消失。

    仍留在后端而不是前端拼：**映射表在这里**，前端再实现一遍就是第二个事实源。

    ``roots`` 按 :func:`_browse_roots` 的顺序（写根在前）遍历，故写根内的路径
    取的是写根名（``Nexus`` 而非 ``D/jk/Nexus``）——两者都能解析回同一条宿主路径，
    短的更适合当会话标签。

    不在任何浏览根内返回 None，前端据此拦住提交。
    """
    p = normalize_host_path(host_path)
    for r in roots:
        base = normalize_host_path(r.host).rstrip("/")
        if p == base or p == base + "/":
            return r.name
        if p.startswith(base + "/"):
            return f"{r.name}/{p[len(base) + 1:]}"
    return None


def _is_writable(host_path: str, write_roots: tuple[str, ...]) -> bool:
    """宿主路径是否落在某个写根内（写**不需要**授权）。

    按**宿主路径**判，而不是「从哪个挂载点进来的」：读根可能整盘包含写根
    （``D:/`` 包含 ``D:/jk/Nexus``），从读根一路点进写根目录里，那里确实可写。

    注意它**不再**决定「能不能当工作区」——读根也能当，只是默认只读、写入走升权。
    这个标志如今只喂 UI 的措辞（"写入需授权" vs 直接可写）。
    """
    return path_in_roots(host_path, write_roots)


def _browse_root_by_name(roots: list[_BrowseRoot], name: str) -> _BrowseRoot | None:
    """按浏览根名找根；找不到返回 None（调用方转 400）。重名取第一个——映射表
    按名索引，两个同名根无法区分，这是本约定的诚实限制。"""
    return next((r for r in roots if r.name == name), None)


def _backend_visible(host_root: str) -> Path:
    """写根在 backend 容器内的挂载路径（compose 约定 ``/srv/host-write/<目录名>``）。"""
    name = host_root.rstrip("/").rsplit("/", 1)[-1]
    return Path(_BACKEND_WRITE_MOUNT_BASE) / name


def _host_cwd_to_backend(roots: list[_BrowseRoot], cwd: str) -> Path | None:
    """把浏览根内的宿主 cwd 映射为 backend 容器内等价路径；不在任何根内返回 None。

    backend 容器（Linux）看不见 ``D:/jk/Nexus``，文件操作必须走 compose 挂进来的
    等价路径：写根是 ``/srv/host-write/<名>``，读根是 ``/srv/host-read/<名>``。

    **读根必须一并认**：会话可以直接拿只读根（如 ``C:/``）当工作区，那时工作区
    文件树、预览、下载读的都是这条挂载；漏掉读根，这些端点就全成 404。

    cwd 是后端自己写进 session_headers 的，仍按不可信处理（纵深防御）：rest 拒绝
    ``..`` 段；映射后不 resolve——文件端点每次访问都会再走
    :func:`resolve_workspace_member` 的 inode 层校验。
    """
    if not cwd or not roots:
        return None
    norm = normalize_host_path(cwd)
    for r in roots:
        base = normalize_host_path(r.host).rstrip("/")
        if norm == base or norm == base + "/":
            return r.container
        if norm.startswith(base + "/"):
            rest = norm[len(base) + 1:]
            if ".." in PurePosixPath(rest).parts:
                return None
            return r.container / rest
    return None


def _create_target(
    roots: list[_BrowseRoot], host_path: str,
) -> tuple[Path, str] | None:
    """浏览位置 → 容器内 ``(挂载根, 根内相对路径)``；不在任何根内返回 None。

    **写根优先**。同一份宿主目录常常同时挂在两条轴上 —— ``D:/`` 是读根，
    ``D:/jk/Nexus`` 又是它内部的写根。从读根一路点进写根时取写根那条：
    相对路径更短，且将来若有人把读根改回 ``:ro``，这条路径依然写得进去。

    读根兜底是 2026-09-15 加的。此前只认写根，理由是读根在 backend 侧是
    ``:ro`` 挂载、照它创建只会换来 ``EROFS``。但那个理由把**沙箱**的只读轴
    （``SANDBOX_READ_ROOTS``：限制**模型**能写哪）当成了**用户**的写授权 ——
    用户自己在界面上建个文件夹，凭什么受模型的沙箱规则管。现在 compose 里
    读根对 backend 已放开成 rw（见 docker-compose.yml 的说明），这里跟着
    放开；真要只读的位置由 :func:`_is_read_only_mount` 按实际挂载标志判。

    返回的相对路径是**目标挂载内**的，不是在读根里看到的那个 ——
    ``D/jk/Nexus/sub`` 的浏览 rest 是 ``jk/Nexus/sub``，而写根 ``Nexus`` 内只剩
    ``sub``。两者不可混用，故成对返回，由调用方一起拿去 resolve。
    """
    p = normalize_host_path(host_path).rstrip("/")
    for want_write in (True, False):
        for r in roots:
            if r.writable != want_write:
                continue
            base = normalize_host_path(r.host).rstrip("/")
            if p == base:
                return r.container, ""
            if p.startswith(base + "/"):
                return r.container, p[len(base) + 1:]
    return None


def _list_dirs(
    target: Path,
    root: Path,
    path: str,
    *,
    writable: bool,
    host_path: str,
    folder: str | None,
) -> dict:
    """列出一个目录里的**子目录**（选择器挑的是工作区位置，不是文件）。

    ``root`` 是所属浏览根的容器内挂载点，交给 :func:`_entry` 判符号链接逃逸——
    越界判定必须相对**根**，相对当前目录会把「指向根内别处」的链接误判为越界。
    """
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
                e = _entry(root, Path(de.path))
                if e["is_dir"]:
                    # 子目录的写权限随当前目录：同一个根内没有「有的可写有的不可写」
                    e["writable"] = writable
                    entries.append(e)
    except OSError as e:
        raise HTTPException(400, f"无法读取目录: {e}")

    entries.sort(key=lambda e: e["name"].lower())
    return {
        "path": path, "entries": entries, "truncated": truncated,
        # 当前目录写入要不要授权（写根内 = 不要）；前端据此改措辞，不拦选择
        "writable": writable,
        # **用户**能不能在这里建东西。与 writable 是两件事：那个说的是**模型**
        # 写这里要不要过审批（按宿主路径是否落在写根判），这个说的是**人**点了
        # 「新建」会不会成功（按实际挂载标志判）。读根就是两者分家的地方：
        # 用户能建（backend 侧 rw），但模型要过审批。前端只在 can_create 为真时
        # 显示新建入口。
        "can_create": not _is_read_only_mount(target),
        # 宿主绝对路径（空串 = 根层或回落模式）。展示用——用户认的是宿主路径，
        # 不是容器里的 /srv/host-read/D
        "host_path": host_path,
        # **提交给 POST /sessions 的值**，由后端翻译（见 _folder_value）。
        # 前端不许自己拼：浏览路径（D/jk/Nexus）与写根名（Nexus）是两套命名
        "folder": folder,
    }


@router.get("/folders")
def api_list_workspace_folders(
    path: str = Query(default=""),
    x_tenant_id: int = Header(alias="X-Tenant-ID"),
) -> dict:
    """列出「新会话工作文件夹」的候选目录（文件夹选择器用）。

    ``path`` 第一段是**浏览根名**（空串 = 列根层），其后是该根下的相对路径。
    根分两类，对应沙箱的两条轴：

    - **写根**（``SANDBOX_WRITE_ROOTS``）——写入不需要授权
    - **读根**（``SANDBOX_READ_ROOTS``）——读得到的都能当工作区，但该会话默认
      落 ``read-only``，模型要写就得走升权审批

    两类都能当工作区，这是「工作区 = 默认信任的边界，沙箱 = 执行时的硬限制」
    两层叠加的语义：选目录是**划边界**，写权限的松紧由沙箱模式另算
    （见 docs/sandbox-design.md §9.2.1）。所以 ``writable`` 不再是"能不能选"，
    而是"写入要不要授权"，前端据此改措辞而非拦提交。

    只列**目录**。越界 / 符号链接逃逸与文件端点同一套校验。

    一个根都没配时（或配了却没挂进 backend 容器，见 :func:`_browse_roots`），
    回落为浏览部署工作区根——原行为，选择器在这种情况下依然可用。
    """
    roots = _browse_roots()
    if not roots:
        try:
            base = Path(resolve_workspace_root()).resolve()
        except (SandboxUnavailableError, ValueError) as e:
            raise HTTPException(503, f"沙箱工作区未配置: {e}")
        target = _member_or_400(base, path)
        # 回落模式：folder 就是部署根下的相对路径（原行为，没有再翻译一层）
        return _list_dirs(target, base, path, writable=True, host_path="", folder=path)

    by_name = {r.name: r for r in roots}
    parts = path.split("/") if path else []
    if not parts:
        # 根层：列浏览根本身（伪条目，点击进入）。根层自身不是可选的工作区——
        # 写根整体当 cwd 等于把整个盘交出去，得先点进去。
        entries = [{
            "name": r.name, "is_dir": True, "size": 0, "mtime": None,
            "is_link": False, "outside": False, "writable": r.writable,
        } for r in roots]
        return {
            "path": "", "entries": entries, "truncated": False,
            "writable": False, "host_path": "", "folder": None,
            # 根层建不了：POST /folders 要求先进入一个目录（根名是伪条目，
            # 拿它当 path 的第一段才有意义）
            "can_create": False,
        }

    root = by_name.get(parts[0])
    if root is None:
        raise HTTPException(400, f"浏览根不存在: {parts[0]!r}")
    rest = "/".join(parts[1:])
    target = _member_or_400(root.container, rest)
    # 拼宿主路径要先去掉根的尾斜杠：整盘根是 "D:/"，直接拼会得到 "D://jk"，
    # 而 _is_writable / _folder_value 都按前缀比对——多一个斜杠就匹配不上写根，
    # 可写的目录会被判成只读
    host_path = root.host.rstrip("/") + "/" + rest if rest else root.host
    return _list_dirs(
        target, root.container, path,
        writable=_is_writable(host_path, _write_roots()),
        host_path=host_path,
        folder=_folder_value(host_path, roots),
    )


@router.post("/folders", status_code=201)
def api_create_browse_entry(
    body: dict,
    x_tenant_id: int = Header(alias="X-Tenant-ID"),
) -> dict:
    """在选择器当前浏览的目录里新建一个空文件 / 空目录（**即时落盘**）。

    ``path`` 与 GET /folders 同一套命名（浏览根名 + 根内相对路径，空串 = 根层）。
    根层是伪条目（见 :func:`_browse_roots`），不能新建 —— 得先进入一个目录。

    与 :func:`api_create_session_entry` 的分工：那个建在**会话工作区**里（位置
    没有选择余地），这个建在**浏览中的宿主目录**里（为「先建个目录、再拿它当
    工作区」服务）。两者共用 :func:`_validate_new_name` / :func:`_validate_kind`
    / :func:`_create_child` —— 行为一致不是靠复制粘贴维持的。

    **读根也能建**：``SANDBOX_READ_ROOTS`` 限制的是**模型**能写哪（沙箱的只读
    轴），不是用户自己能不能在这个界面上建文件夹。真正只读的位置会被
    :func:`_create_child` 的 ``OSError`` 转成 400 人话。
    """
    path = (body.get("path") or "").strip()
    name = _validate_new_name(body.get("name"))
    kind = _validate_kind(body.get("type"))

    roots = _browse_roots()
    if not roots:
        # 回落模式：浏览的是部署工作区根（与 GET /folders 同一条回落）
        try:
            base = Path(resolve_workspace_root()).resolve()
        except (SandboxUnavailableError, ValueError) as e:
            raise HTTPException(503, f"沙箱工作区未配置: {e}")
        container_root, rel = base, path
    else:
        parts = path.split("/") if path else []
        if not parts:
            raise HTTPException(400, "请先进入一个目录再新建")
        root = _browse_root_by_name(roots, parts[0])
        if root is None:
            raise HTTPException(400, f"浏览根不存在: {parts[0]!r}")
        rest = "/".join(parts[1:])
        host_path = root.host.rstrip("/") + "/" + rest if rest else root.host
        target_root = _create_target(roots, host_path)
        if target_root is None:
            # 按名找到了根，它的 host 拼出来的路径却不在任何根内 —— 只可能来自
            # 配置自相矛盾（根名重复等），当输入错误处理
            raise HTTPException(400, f"无法定位该位置对应的挂载: {host_path!r}")
        container_root, rel = target_root

    parent = _member_or_400(container_root, rel)
    if not parent.is_dir():
        raise HTTPException(404, f"路径不存在: {path or '/'}")

    # 第二道 resolve：名字若撞上指向根外的符号链接，这里以「越界」拒掉，而不是
    # 等 _create_child 用「已存在」兜住 —— 那条消息说不清真正的问题在哪
    _member_or_400(container_root, f"{rel}/{name}" if rel else name)
    child = parent / name
    _create_child(child, kind)

    return {
        "ok": True, "path": path, "name": name, "type": kind,
        "entry": _entry(container_root, child),
    }


@router.post("/sessions", status_code=201)
def api_create_workspace_session(
    body: dict,
    x_tenant_id: int = Header(alias="X-Tenant-ID"),
) -> dict:
    """新建一个**绑定了工作文件夹**的会话（DSH「选择工作区 → 新建会话」）。

    落地方式：预创建 session_headers 行（``cwd`` = 所选文件夹），再建 conversation
    并回写 session 映射。首次 chat 前端带上这个 session_id，AgentRuntime 走冷恢复
    路径（persistence.load 命中 → 重建 Session）——沙箱执行侧 ``session_policy``
    的 ``cwd or workspace_for_session`` 链与读侧 ``_resolve_session_workspace``
    都会解析到同一个文件夹，两边不分家。

    **不写 seed**：system prompt 由 AgentRuntime 每轮组装（含上游 context 与工具
    指导），不属于会话历史。预创建因此只负责 header，不存在与执行侧逐字对齐的
    镜像代码——这正是本次重构消除的双处同步点。

    folder 缺省 / 空串 = 不设 cwd（会话工作区自动落在 ``<root>/<session_id>``，
    即原有行为）。
    """
    agent_key = (body.get("agent_key") or "").strip()
    folder = (body.get("folder") or "").strip()
    if not agent_key:
        raise HTTPException(400, "agent_key 不能为空")
    try:
        agent = get_agent(agent_key)
    except KeyError:
        raise HTTPException(400, f"Agent 不存在: {agent_key}")

    # 文件夹校验。folder 第一段是**浏览根名**（读写两类同一套命名，见
    # _folder_value），其后是根内相对路径；cwd 存**宿主路径**——沙箱容器由 daemon
    # 直接挂载它。未配任何浏览根时回落为部署根相对路径（原行为）。
    #
    # 读写两类根都收：选读根当工作区时，会话由 session_default_mode 落 read-only
    # （见 sandbox/runtime.py），模型要写走升权审批——「能不能选」与「能不能写」
    # 是两件事，这里只负责前者。
    cwd: str | None = None
    if folder:
        roots = _browse_roots()
        if roots:
            name, _, rest = folder.partition("/")
            root = _browse_root_by_name(roots, name)
            if root is None:
                raise HTTPException(400, f"浏览根不存在: {name!r}")
            try:
                target = resolve_workspace_member(str(root.container), rest)
            except ValueError as e:
                raise HTTPException(400, str(e))
            if root.writable:
                try:
                    target.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    raise HTTPException(400, f"无法创建工作区文件夹: {e}")
            elif not target.is_dir():
                # 读根在 backend 侧是 :ro 挂载，建不出来，只能选已经存在的目录
                raise HTTPException(400, "只读位置没有这个目录，无法作为工作区")
            # 根的尾斜杠要留着：整盘根 "C:/" 去掉斜杠会拼成 "C::"，
            # docker 的 -v 按 ':' 分段，整条命令起不来（同 workspace.py 的 _mount_name）
            cwd = root.host if not rest else root.host.rstrip("/") + "/" + rest
        else:
            try:
                root = Path(resolve_workspace_root()).resolve()
            except (SandboxUnavailableError, ValueError) as e:
                raise HTTPException(503, f"沙箱工作区未配置: {e}")
            try:
                target = resolve_workspace_member(str(root), folder)
            except ValueError as e:
                raise HTTPException(400, str(e))
            if target == root:
                raise HTTPException(400, "不能直接把部署根作为工作区文件夹（会跨会话可见）")
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise HTTPException(400, f"无法创建工作区文件夹: {e}")
            cwd = str(target)

    # conversation（channel=agent:<key>，会话归属与列表端点的解析约定一致）
    label = folder.rsplit('/', 1)[-1] if folder else "新会话"
    from backend.services.chat import service as chat_service
    conv = chat_service.create_conversation(
        x_tenant_id,
        chat_service.ConversationCreate(
            channel=f"agent:{agent_key}",
            customer_name=label,
        ),
    )

    # 预创建 Session header（runtime 首次 chat 冷恢复走这条）
    # 只写 header（含 cwd），不写 seed：system prompt 由 AgentRuntime 每轮组装，
    # 不再属于会话历史 —— 预创建与执行侧因此没有需要逐字对齐的镜像代码。
    sid = new_session_id()
    # seed_length=0：显式声明「无 seed」（该字段是 seed 机制的历史遗留，无读取方；
    # 0 = 新会话，≥1 = 改造前落库的旧数据，NULL = 更早的未知来源）
    header = SessionHeader(version=1, id=sid, created_at=time.time(), seed_length=0)
    header.cwd = cwd
    persistence = get_session_persistence()
    persistence.create(sid, header)

    chat_service.save_conversation_session_id(x_tenant_id, conv.id, sid)

    return {
        "ok": True,
        "session": {
            "session_id": sid,
            "conversation_id": conv.id,
            "agent_key": agent_key,
            "agent_name": getattr(agent, "name", agent_key),
            "label": label,
            "folder": folder or None,
        },
    }


@router.get("/{session_id}/files")
def api_list_files(
    session_id: str,
    path: str = Query(default=""),
    x_tenant_id: int = Header(alias="X-Tenant-ID"),
) -> dict:
    """列出工作区内某个目录。``path`` 是工作区内的相对路径，空串表示根。

    ``writable`` 说的是**这个会话的工作区能不能写**（是否来自读根那条 ``:ro``
    挂载），整棵树共用一个值 —— 会话工作区不可能有的子目录可写、有的不可写。
    前端据此禁用「新建」入口：让用户点一个必然 400 的按钮是最差的一种诚实。
    """
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
    return {
        "session_id": session_id, "path": path, "entries": entries,
        "truncated": truncated,
        "writable": not _is_read_only_mount(root),
    }


@router.post("/{session_id}/files", status_code=201)
def api_create_session_entry(
    session_id: str,
    body: dict,
    x_tenant_id: int = Header(alias="X-Tenant-ID"),
) -> dict:
    """在会话工作区的某个目录里新建空文件 / 空目录（**即时落盘**）。

    写路径与上传同一套：:func:`_writable_session_root` 负责「会话存在 + 属于本
    租户 + 不是只读挂载 + 目录不存在就建」。与读端点的 :func:`_workspace_root_for`
    只差最后一条 —— 读不得凭空建目录，写可以（用户先动手，沙箱将来用的正是同
    一个目录）。

    只建**空**条目：不写内容、不改名、不删除。用户想往里填东西，要么上传覆盖，
    要么让模型写（见模块 docstring 的「两个窄写入口」）。
    """
    ws = _writable_session_root(session_id, x_tenant_id, "新建文件或文件夹")
    dir_path = (body.get("path") or "").strip()
    name = _validate_new_name(body.get("name"))
    kind = _validate_kind(body.get("type"))

    parent = _member_or_400(ws, dir_path)
    if not parent.is_dir():
        raise HTTPException(404, f"路径不存在: {dir_path or '/'}")

    # 第二道 resolve（同 api_create_browse_entry）：撞上指向工作区外的符号链接时
    # 以「越界」拒掉，而不是让 O_EXCL 用「已存在」兜住
    _member_or_400(ws, f"{dir_path}/{name}" if dir_path else name)
    child = parent / name
    _create_child(child, kind)

    return {
        "ok": True, "session_id": session_id, "path": dir_path,
        "name": name, "type": kind, "entry": _entry(ws, child),
    }


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
    try:
        with open(target, "rb") as f:
            head = f.read(SNIFF_BYTES)
    except OSError as e:
        raise HTTPException(400, f"无法读取文件: {e}")

    headers["Content-Disposition"] = _content_disposition(name, inline=True)

    # 图片先判：它的 MIME 由魔数给出（见 _sniff_image），能安全内联
    image_mime = _sniff_image(head)
    if image_mime is not None:
        if size > MAX_PREVIEW_BYTES:
            raise HTTPException(
                413, f"文件 {size} 字节，超过预览上限 {MAX_PREVIEW_BYTES} 字节；请下载查看",
            )
        return FileResponse(target, media_type=image_mime, headers=headers)

    # PDF 次判：由浏览器内置 viewer 原生渲染（插件上下文，不进 DOM）。
    # 必须排在 _looks_binary 之前——PDF 正文常含 NUL 字节，按二进制判就永远
    # 预览不了。不加页面级 sandbox CSP：它会在部分浏览器阻断 viewer 自身；
    # PDF 内嵌脚本的风险由 viewer 沙箱承担，本 API 只保证不把 PDF 当文本内联。
    if _sniff_pdf(head):
        if size > MAX_PDF_PREVIEW_BYTES:
            raise HTTPException(
                413, f"文件 {size} 字节，超过 PDF 预览上限 {MAX_PDF_PREVIEW_BYTES} 字节；请下载查看",
            )
        return FileResponse(target, media_type="application/pdf", headers=headers)

    if size > MAX_PREVIEW_BYTES:
        raise HTTPException(
            413, f"文件 {size} 字节，超过预览上限 {MAX_PREVIEW_BYTES} 字节；请下载查看",
        )

    if _looks_binary(head):
        raise HTTPException(415, "二进制文件不支持预览；请下载")

    # 其余一律 text/plain：内容由模型书写，按文件名猜类型 = 把 API 源当 XSS 宿主。
    # CSP sandbox 是纵深防御 —— 万一有人直接开这个 URL，也跑不了脚本。
    headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return FileResponse(target, media_type="text/plain; charset=utf-8", headers=headers)


@router.post("/{session_id}/upload")
async def api_upload_files(
    session_id: str,
    files: list[UploadFile] = File(...),
    x_tenant_id: int = Header(alias="X-Tenant-ID"),
) -> dict:
    """把用户拖进会话的文件落到工作区的 ``.dsh-drops/`` 专属子目录。

    这是工作区**唯一**的用户写入口（边界见模块 docstring）。上传后文件立刻
    出现在文件树里；模型经 bash 工具在 ``/workspace/.dsh-drops`` 下直接读到
    它——这正是 DSH 拖放区的语义：人给料，模型取料。

    先验后写：全部文件名先过清洗与越界校验，任何一条非法就整批 400、一个字节
    都不落盘（不产生半批）。逐文件流式落盘，单文件超过
    :data:`MAX_UPLOAD_BYTES` 时删掉半成品并 413；该请求**已落盘的保留**，
    响应里列出来，前端据此刷新文件树。
    """
    ws = _workspace_root_for_upload(session_id, x_tenant_id)

    cleaned: list[tuple[UploadFile, str, str]] = []
    for f in files:
        name = _sanitize_upload_name(f.filename)
        cleaned.append((f, name, f"{UPLOADS_SUBDIR}/{name}"))

    # 逐条先过 inode 层校验（含符号链接逃逸），任何一条越界都整批 400
    targets = [_member_or_400(ws, rel) for _, _, rel in cleaned]

    drops = _member_or_400(ws, UPLOADS_SUBDIR)
    try:
        drops.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(400, f"无法创建上传目录: {e}")

    uploaded: list[dict] = []
    for (f, name, rel), target in zip(cleaned, targets):
        size, over = await _save_upload(target, f, limit=MAX_UPLOAD_BYTES)
        if over:
            raise HTTPException(
                413,
                f"文件 {name} 超过上传上限 {MAX_UPLOAD_BYTES} 字节；"
                f"已上传的 {len(uploaded)} 个文件保留",
            )
        uploaded.append({"name": name, "path": rel, "size": size})

    return {"session_id": session_id, "uploaded": uploaded}
