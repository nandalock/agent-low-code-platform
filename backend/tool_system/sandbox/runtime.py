"""沙箱运行时装配 —— provider 单例 + 会话策略入口

装配层（``backend/main.py``）调用 :func:`init_sandbox`；
AgentLoop 调用 :func:`session_policy` 拿到本会话的执行策略。

``tool_system`` 不依赖 ``agents`` 层，因此这里只吃原始值（session_id / cwd），
不接收 Session 对象。
"""
import json
import logging
import os

from backend.core.connection import get_conn
from backend.tool_system.registry.descriptor import EXCLUSIVE, SandboxToolConfig, ToolDescriptor
from backend.tool_system.sandbox.backends.docker import DEFAULT_SANDBOX_IMAGE, DockerProvider
from backend.tool_system.sandbox.escalation import ESCALATION_TARGETS
from backend.tool_system.sandbox.policy import DEFAULT_MODE_ENV, resolve_policy, validate_mode
from backend.tool_system.sandbox.provider import SandboxProvider
from backend.tool_system.sandbox.vocabulary import SandboxExecutionPolicy, SandboxMode
from backend.tool_system.sandbox.workspace import (
    CONTAINER_WORKSPACE,
    READ_ROOTS_ENV,
    WRITE_ROOTS_ENV,
    build_read_mounts,
    build_write_mounts,
    parse_roots,
    path_in_roots,
    probe_workspace_root,
    resolve_workspace_root,
    session_workspace,
)

logger = logging.getLogger(__name__)

_provider: SandboxProvider | None = None

#: 设置 namespace —— 沙箱开关的值归本子系统所有（平台层只问结果，不解释原因）
SETTINGS_NS = "sandbox"

#: 内建沙箱工具名（停用时注销它们）
BUILTIN_SANDBOX_TOOLS = ("bash", "python")

_enabled: bool | None = None


# ── provider 单例 ──


def set_sandbox_provider(provider: SandboxProvider | None) -> None:
    global _provider
    _provider = provider


def get_sandbox_provider() -> SandboxProvider | None:
    """当前 provider；未装配返回 None（沙箱工具将 fail-closed）。"""
    return _provider


def init_sandbox(*, image: str | None = None, probe: bool = True) -> SandboxProvider:
    """装配沙箱：校验工作区根 → 功能探测 → 构造 provider。

    Raises:
        SandboxUnavailableError: 工作区未配置 / 在 daemon 侧不可见。
        ValueError: 工作区根配置不安全。
    """
    root = resolve_workspace_root()
    img = image or DEFAULT_SANDBOX_IMAGE
    if probe:
        probe_workspace_root(root, image=img)
    provider = DockerProvider(image=img)
    set_sandbox_provider(provider)
    logger.info(f"沙箱已装配：root={root} image={img}")
    return provider


# ── 会话策略 ──


def default_mode() -> SandboxMode | None:
    """部署默认模式（``SANDBOX_DEFAULT_MODE``）；未配置返回 None。

    Raises:
        ValueError: 环境变量取值不在封闭词汇内。
    """
    raw = os.environ.get(DEFAULT_MODE_ENV)
    if not raw:
        return None
    return validate_mode(raw)


def read_roots() -> tuple[str, ...]:
    """部署配置的只读挂载根（``SANDBOX_READ_ROOTS``，逗号分隔）；未配置返回 ()。

    这是**部署事实**而非会话策略——哪些宿主目录对沙箱只读可见，跟镜像、内存
    上限同类，因此不进 settings、不做会话覆盖、不参与模式优先级链。
    """
    return parse_roots(os.environ.get(READ_ROOTS_ENV))


def write_roots() -> tuple[str, ...]:
    """部署配置的可写挂载根（``SANDBOX_WRITE_ROOTS``，逗号分隔）；未配置返回 ()。

    与只读根同为**部署事实**（不进 settings、不做会话覆盖）：哪些宿主目录可写，
    跟镜像、内存上限同类。差异只在消费侧 —— 读根恒为 ``:ro``，写根只在
    ``workspace-write`` 落地（见 ``docker.DockerProvider.confine``）。
    """
    return parse_roots(os.environ.get(WRITE_ROOTS_ENV))


def workspace_for_session(session_id: str) -> str:
    """本会话的工作区目录 ``<SANDBOX_WORKSPACE_ROOT>/<session_id>``（惰性创建）。"""
    return session_workspace(resolve_workspace_root(), session_id)


def session_default_mode(cwd: str | None) -> SandboxMode | None:
    """本会话的**部署默认**模式（读根 cwd 感知）。

    默认模式本该是纯部署事实（``SANDBOX_DEFAULT_MODE``），这里只有一条例外：
    ``cwd`` 落在**只读根**内时收窄为 ``read-only``。

    为什么必须收窄：只读根是「可读全域」的授权，**不是写授权**；而 docker 后端
    把 cwd 直接挂成 ``/workspace``，rw 还是 ro **取决于模式**
    （``backends/docker.py``）。用户在文件夹选择器里点一下 ``C:/``，若默认模式
    仍是 workspace-write，整个 C 盘就无声地变成了可写——一个读授权被当成了写授权。

    落在**写根内**的 cwd 不在此列：从只读根 ``D:/`` 一路点进写根 ``D:/jk/Nexus``，
    那里本就是可写的，那正是写根存在的意义（先判写根，再判读根）。

    **这只是默认值，不是天花板**：模式优先级链（``policy.py``）里
    ``session_override``（Composer 切换）与 ``explicit_mode``（升权获批）都在它之上，
    所以"想写就写"的路仍然通畅——只是要过一次人的批准。

    ``cwd`` 为 None（未绑定文件夹，工作区自动落在 ``<root>/<session_id>``）时
    按部署默认：那种路径由 ``SANDBOX_WORKSPACE_ROOT`` 决定，与读根是两回事，
    即便两者在磁盘上重叠也不该被读根牵着走。
    """
    if cwd and not path_in_roots(cwd, write_roots()) and path_in_roots(cwd, read_roots()):
        return "read-only"
    return default_mode()


def session_policy(
    session_id: str,
    cwd: str | None = None,
    session_override: SandboxMode | None = None,
) -> SandboxExecutionPolicy | None:
    """解析本会话一次工具调用的执行策略；沙箱未装配时返回 None。

    ``session_override`` 来自 ``sandbox/mode`` 事件的投影（调用方提供，
    本层不依赖 agents 层）；优先级见 :mod:`backend.tool_system.sandbox.policy`。

    默认值走 :func:`session_default_mode`（读根 cwd → read-only），不是裸的
    ``default_mode()``——两个消费方（执行侧与 ``api/agents.py`` 的模式端点）
    必须用同一个函数，否则页面上显示的模式和沙箱实际用的是两回事。
    """
    if _provider is None:
        return None
    root = cwd or workspace_for_session(session_id)
    return resolve_policy(
        workspace_root=root,
        session_id=session_id,
        session_override=session_override,
        config_default=session_default_mode(cwd),
        read_roots=read_roots(),
        write_roots=write_roots(),
    )


# ── 内置沙箱工具注册 ──

_BASH_SCHEMA = {
    "name": "bash",
    "description": (
        "在受限沙箱中执行 shell 命令。工作目录是会话工作区；写到可写位置之外"
        "会被内核拒绝（可写位置见下方说明）。返回 stdout / stderr / exit_code。"
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "要执行的 shell 命令"}},
        "required": ["command"],
    },
}

# ── 工具使用指导（进 system prompt，与 schema 分开） ──
#
# schema（上面的 *_SCHEMA）走 LLM 的 tools 参数，描述「怎么调」；
# 这里的指导进 system prompt 的 tool:<name> 段，描述「什么时候用、失败怎么办」。
# 两者永不合并（理由见 agents/runtime/system_prompt/tool.py）。
# 仅有「跨调用习惯」值得写在这里——单次调用的参数说明属于 schema 的 description。

_BASH_GUIDANCE = (
    "优先用 bash 完成文件与进程操作。执行前先确认当前目录与目标路径；"
    "命令失败时先读输出里的退出码与 stderr，不要原样重试。"
)

_PYTHON_GUIDANCE = (
    "用 python 做需要计算、解析或数据清洗的任务。脚本写入会话工作区后执行；"
    "报错带行号时先读对应代码行再修复，不要反复提交未修改的脚本。"
)


_PYTHON_SCHEMA = {
    "name": "python",
    "description": (
        "在受限沙箱中执行 Python 代码。工作目录是会话工作区；写到可写位置之外"
        "会被内核拒绝（可写位置见下方说明）。返回 stdout / stderr / exit_code。"
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"code": {"type": "string", "description": "要执行的 Python 代码"}},
        "required": ["code"],
    },
}

#: 升权参数的 schema 片段。**静态全量 advertise，不做按会话投影**：
#: DSH 的坑（全局 advertise + 逐调用模式 → 模型反复填、每次报错）由闸2
#: 去重解决——那正是上游的官方修法（#4359 ``normalizeEscalationMode``）；
#: 而按模式投影 schema 会把「当前模式」从 enum 里泄漏给模型，与原则 7
#: 冲突。故此处的 enum 是**建议面**而非授权面：执行时校验才是权威
#: （DSH 原话：schema visibility is not an instruction to always populate
#: the field）。
_ESCALATION_PROPERTIES = {
    "sandbox_permissions": {
        "type": "string",
        "enum": list(ESCALATION_TARGETS),
        "description": (
            "仅在**本条命令被沙箱拒绝之后**重试同一条命令时使用：请求的目标模式，"
            "必须比当前模式更宽，且取足以放行的最窄者。必须与 justification 同时给出。"
            "命令之外的一切都不能借它完成——它只让这一条命令再跑一次。"
        ),
    },
    "justification": {
        "type": "string",
        "description": (
            "一句话说明这条命令为什么必须写到工作区之外。与 sandbox_permissions "
            "成对出现；单独给出会被拒绝。"
        ),
    },
}


def _with_escalation(schema: dict) -> dict:
    """给沙箱工具 schema 副本挂上升权参数（不改动模块级常量）。"""
    return {
        **schema,
        "inputSchema": {
            **schema["inputSchema"],
            "properties": {**schema["inputSchema"]["properties"], **_ESCALATION_PROPERTIES},
        },
    }


def mounts_note(workspace_host: str | None = None) -> str:
    """把**可写位置**与两条挂载轴的映射写进工具描述。**恒非空。**

    与「不把沙箱模式写进系统提示词」（docs/sandbox-design.md §2 原则 7）不冲突：
    那条针对的是**模式**（说了模型会变保守、不敢尝试），这里给的是**路径事实**
    ——不说，模型就不知道那些目录存在，只会去猜 ``D:\\...`` 然后撞
    ``No such file or directory``，并误判成「文件不存在」而不是「路径写法不对」。

    恒非空是因为「哪儿能写」这件事随部署配置变：基础描述只能说「写到可写位置
    之外会被拒绝」，而可写轴会**打开新的可写面**。这里不说全，模型就按基础描述
    行动 —— 以为除了工作区哪儿都不能写，于是放着 /mnt/write 里的目录不去改，
    绕回工作区里造副本再让用户手动搬（实测发生过）。

    **两条轴分开写**，各有各的措辞：只报路径不给权限，模型会默认它和只读根
    一样只能看；合并成「可用目录」则会把读写差别整个吞掉。

    ``workspace_host`` 是本会话工作区在**宿主**上的绝对路径（即会话 cwd）；
    给了就与另外两条轴一样写成 ``/workspace（宿主 D:/jk/Nexus/proj）``。

    为什么要显式给：Docker Desktop 用 9p/drvfs 挂 Windows 盘时，``mount`` 只报
    **盘符根**（``D:\\ on /workspace type 9p (…aname=drvfs;path=D:\\…)``），子路径
    无法从挂载表反推——实测绑 ``D:/jk/Nexus/test1`` 与绑 ``D:/jk/Nexus`` 在
    ``mount`` 里显示一模一样。模型手上没有权威答案时会去挂载表里找，然后必然
    推断成「整个 D 盘」并这样转述给用户（实际发生过）。本模块在 tool_system 层、
    拿不到 Session，故 cwd 由组装侧传入（``system_prompt/platform_sections.py``
    的工具 provider）。

    不给 host（无 cwd 的自动工作区，且首次工具调用之前）时只报容器内路径——
    与本参数出现之前的行为一致。
    """
    head = f"可写位置：会话工作区 {CONTAINER_WORKSPACE}"
    head += f"（宿主 {workspace_host}）。" if workspace_host else "。"
    lines = [head]
    read = build_read_mounts(read_roots())
    if read:
        items = "、".join(f"{c}（宿主 {h}）" for h, c in read)
        lines.append(f"可读目录（只读，可读不可写）：{items}。")
    write = build_write_mounts(write_roots())
    if write:
        items = "、".join(f"{c}（宿主 {h}）" for h, c in write)
        lines.append(f"可写目录（可直接修改，与工作区同等可写）：{items}。")
    return "\n" + "".join(lines)


def register_builtin_sandbox_tools(
    registry,
    *,
    image: str | None = None,
    timeout_s: float = 60.0,
) -> list[str]:
    """把内置沙箱工具（bash / python）注册进 Registry。

    工具本身始终注册；能否执行取决于沙箱是否装配（未装配时执行 fail-closed）。
    绑定仍走 ``agent_tool_bindings``（按 tool_name），API / UI 无需改动。

    Returns:
        注册的工具名列表。
    """
    img = image or DEFAULT_SANDBOX_IMAGE
    specs = (
        ("bash", "shell", _BASH_SCHEMA, _BASH_GUIDANCE),
        ("python", "python", _PYTHON_SCHEMA, _PYTHON_GUIDANCE),
    )
    names: list[str] = []
    for name, runtime, schema, guidance in specs:
        registry.register_native(ToolDescriptor(
            name=name,
            type="sandbox",
            transport="",
            server_id=0,
            # 挂载说明**不在这里**拼：工作区那条要写会话 cwd（宿主路径），而注册
            # 发生在启动时、拿不到会话。由组装侧按会话调用 mounts_note() 补上
            # （见 system_prompt/platform_sections.py 的工具 provider）。
            schema=_with_escalation(schema),
            timeout=timeout_s,
            sandbox=SandboxToolConfig(runtime=runtime, image=img, timeout_s=timeout_s),
            usage_guidance=guidance,
            # 独占执行：bash / python 共写同一个会话工作区，并发跑会互相踩文件
            # （A 建目录 B 删、A 写文件 B 读），且模型无法声明调用间的依赖关系。
            # 并发收益不值得用工作区一致性换 —— 故沙箱工具恒为 exclusive 屏障。
            execution_mode=EXCLUSIVE,
        ))
        names.append(name)
    logger.info(f"已注册沙箱工具: {names}")
    return names


# ── 能力开关（settings.sandbox.enabled） ──
#
# 「沙箱开不开」是沙箱子系统自己的设置，不归平台层解释。停用时**注销**内建工具，
# 于是模型完全看不到它们（而不是看得到但一调就失败）。绑定记录保留——重新启用
# 即恢复，不需要重新勾选。


def _load_enabled() -> bool:
    """从 settings 读开关；缺省 true。

    读失败也按启用处理：开关是「能力可见性」，不是约束本身——沙箱执行侧无论
    如何都 fail-closed，所以这里放宽不会导致裸跑。
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM settings WHERE namespace = %s", (SETTINGS_NS,))
                row = cur.fetchone()
        raw = (row or {}).get("value")
        if isinstance(raw, str):
            raw = json.loads(raw)
        return bool((raw or {}).get("enabled", True))
    except Exception as e:
        logger.warning(f"读取沙箱开关失败，按启用处理: {e}")
        return True


def _save_enabled(enabled: bool) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO settings (namespace, value, updated_at) VALUES (%s, %s::jsonb, now()) "
                "ON CONFLICT (namespace) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
                (SETTINGS_NS, json.dumps({"enabled": enabled})),
            )
        conn.commit()


def is_enabled() -> bool:
    """沙箱能力当前是否启用（进程内缓存，写入侧同步刷新）。"""
    global _enabled
    if _enabled is None:
        _enabled = _load_enabled()
    return _enabled


def set_enabled(enabled: bool) -> None:
    """启停沙箱能力：写 settings + 立即重注册工具（下一轮对话即生效，无需重启）。"""
    global _enabled
    _enabled = enabled
    _save_enabled(enabled)
    apply_enabled(enabled)
    logger.info(f"沙箱能力已{'启用' if enabled else '停用'}")


def apply_enabled(enabled: bool) -> None:
    """按开关调整内建工具的注册状态（模型可见性随之变化）。"""
    try:
        from backend.tool_system.registry.registry import get_registry
        registry = get_registry()
    except RuntimeError:
        return  # Registry 未装配（未启动 / 单测）
    if enabled:
        register_builtin_sandbox_tools(registry)
    else:
        registry.unregister_builtin(BUILTIN_SANDBOX_TOOLS)
