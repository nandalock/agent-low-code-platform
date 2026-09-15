"""平台内容 — 本项目的提示词段/上下文/变量/工具指导

框架六文件（section/context/variable/tool/system_prompt/assembler）镜像 DSH，
保持零业务依赖、可独立单测；**平台自己写什么提示词**全部收敛在本文件。
于是「改文案」与「改组装机制」彻底分开：调 identity 措辞不会碰到流水线代码，
改排序规则也不用翻提示词。

注册时机：``system_prompt/__init__.py`` 末尾 import 本模块（幂等，进程内只执行
一次）。任何 import 该包的代码自动获得这些注册，无需在 main.py startup 里接线。

一段事实一个归属方（DSH 的指导原则，本项目落地情况）：
  agent 叫什么/干什么   → agent_definitions 表（name/description）→ identity 段
  角色与行为约束        → config.system_prompt（管理台可编辑）→ persona 段
  工具的调用方式        → ToolDescriptor.schema → LLM 的 tools 参数
  工具的跨调用习惯      → ToolDescriptor.usage_guidance → tool:<name> 段
  工具不可手写时的指导  → 本文件的 PLATFORM_TOOL_GUIDANCE → tool:<name> 段
  上游节点输出          → RequestContext.context → platform:upstream 上下文
"""
import json
import logging

from backend.agents.runtime.system_prompt.assembler import AssembleContext
from backend.agents.runtime.system_prompt.context import PromptContext
from backend.agents.runtime.system_prompt.section import PromptSection
from backend.agents.runtime.system_prompt.system_prompt import (
    CONTEXT_ORDERS,
    SECTION_ORDERS,
    context,
    section,
    tools,
    variable,
)
from backend.agents.runtime.system_prompt.tool import ToolProviderResult, ToolSchema

logger = logging.getLogger(__name__)


# ── 工具使用指导（描述符留空时的兜底） ──
#
# 内置沙箱工具（bash/python）的指导写在各自的 ToolDescriptor 上（注册处直接可见）。
# 但平台自有的 MCP 工具没有这样的注册点——它们的 schema 由 FastMCP 从函数签名与
# docstring 自动派生（tool_packages/builtin/server.py），描述符在
# MCPToolProvider 里统一构造，无从附加。于是按工具名在这里登记。
#
# 解析优先级（见 _platform_tool_provider）：描述符自带 > 本表 > 无指导。
# 外部 MCP server 可以自己带 usage_guidance，无需改这里。

PLATFORM_TOOL_GUIDANCE: dict[str, str] = {
    "query_orders": (
        "查询订单用本工具而不是猜测数据。参数均可选，全空返回最近 50 笔；"
        "拿到订单号后继续调 get_order_detail 看明细。"
    ),
    "get_order_detail": (
        "查看单笔订单完整信息（商品明细 + 物流记录）。需要订单 id（整数），"
        "可先经 query_orders 获取；返回为空说明订单不存在或不属于该租户。"
    ),
    "track_logistics": (
        "按运单号查物流轨迹。运单号不是订单号；查不到运单时先确认单号来源，"
        "再向用户说明而不是编造状态。"
    ),
    "search_faqs": (
        "FAQ 检索是回答知识库问题的首选。结果带相似度分数，低分条目不可靠，"
        "不要当作确定答案复述。"
    ),
    "list_faq_tags": (
        "列出知识库全部标签。适合在用户不知道关键词时先了解覆盖范围，"
        "再按标签换关键词调 search_faqs。"
    ),
    "get_user_profile": (
        "查询用户画像（历史记忆与偏好）。user_id 来自会话上下文；"
        "画像为空时不要编造用户信息，引用时注明是「记忆中的信息」。"
    ),
}


# ── 段 ──


def _identity_text(ctx: AssembleContext) -> str:
    """身份段：从 agent 定义自动生成，不由人手写

    agent 叫什么、干什么只有一处事实（agent_definitions 表的 name/description），
    提示词从这里派生——改名字不需要同时改提示词。
    """
    name = (ctx.agent_name or ctx.agent_key or "").strip()
    desc = (ctx.agent_description or "").strip()
    if not name:
        return desc
    return f"你是「{name}」。{desc}" if desc else f"你是「{name}」。"


def _persona_text(ctx: AssembleContext) -> str:
    """角色段：管理台可编辑的自由文本（config.system_prompt）

    这是本项目低代码能力的入口——用户配置的角色指令直接成为提示词的一段。
    空文本不贡献内容（渲染后该段消失，不留空行）。
    """
    return (ctx.config.get("system_prompt") or "").strip()


section(PromptSection(
    name="platform:identity",
    order=SECTION_ORDERS["AGENT_IDENTITY"],
    text=_identity_text,
))
section(PromptSection(
    name="agent:persona",
    order=SECTION_ORDERS["AGENT_PERSONA"],
    text=_persona_text,
))


# ── 动态上下文（尾部，保缓存前缀） ──


def _upstream_text(ctx: AssembleContext) -> str:
    """上游节点输出（workflow 场景）

    格式与重构前的 ``_build_init_messages`` 第二段逐字一致——存量会话在改造后
    由本函数每轮重建，文本必须相同才谈得上「重建不出偏差」。
    """
    if not ctx.upstream_context:
        return ""
    context_str = json.dumps(ctx.upstream_context, ensure_ascii=False, indent=2)
    return f"【上游节点输出，供你参考】\n{context_str}"


context(PromptContext(
    name="platform:upstream",
    order=CONTEXT_ORDERS["UPSTREAM_CONTEXT"],
    text=_upstream_text,
))

# MEMORY_CONTEXT（8900）槽位预留：记忆系统（services/memory）尚未接入
# AgentRuntime 主链路，接入时在此注册一个 provider 即可，无需改组装代码。


# ── 变量 ──
#
# 前部段只应引用跨轮稳定的变量（否则每轮渲染变化会断掉 KV cache 前缀）；
# 逐轮变化的内容请用 context 走尾部。

variable("tenant_id", lambda ctx: str(ctx.tenant_id))
variable("agent_key", lambda ctx: ctx.agent_key or None)
# cwd：会话显式指定的工作文件夹（未指定则无值 —— 引用 {{cwd}} 的段会渲染失败，
# 这是刻意为之：与其渲染成空串让模型以为「工作目录是空」，不如响亮报错）
variable("cwd", lambda ctx: (ctx.session.header.cwd if ctx.session else None) or None)


# ── 工具来源 ──


async def _platform_tool_provider(ctx: AssembleContext) -> ToolProviderResult:
    """本 agent 绑定的工具：schema 走 tools 参数，指导合成 tool:<name> 段

    Registry 未装配时返回空（与重构前 AgentRuntime 的 try/except RuntimeError
    同行为）：装配顺序不该决定对话能否进行。
    """
    from backend.tool_system.registry.registry import (
        _to_openai_function,
        get_registry,
    )
    from backend.tool_system.sandbox.runtime import mounts_note

    try:
        registry = get_registry()
    except RuntimeError:
        logger.warning("ToolRegistry 未装配，本次组装不提供工具")
        return ToolProviderResult()

    # 会话工作区在**宿主**上的路径，写进沙箱工具描述（理由见 mounts_note 的
    # docstring：Docker Desktop 的挂载表只报盘符根，模型自己推不出来）。
    # 无会话 / 未绑定文件夹时为 None —— 自动分配的工作区要到首次工具调用才回写
    # header.cwd，在那之前只报容器内路径，不编一个猜的。
    workspace_host = ctx.session.header.cwd if ctx.session else None

    descriptors = await registry.get_descriptors_for(ctx.agent_key)
    schemas: list[ToolSchema] = []
    guidance: dict[str, str] = {}
    for d in descriptors:
        fn = _to_openai_function(d.schema)["function"]
        if d.sandbox is not None:
            # 挂载说明只在这里拼：注册发生在启动时、没有会话，两条轴虽说是部署
            # 事实，工作区那条却随会话变。按 d.sandbox 判而不是按名字——
            # 沙箱工具将来不止 bash/python。
            fn["description"] += mounts_note(workspace_host)
        schemas.append(ToolSchema(name=d.name, function=fn))
        # 描述符自带优先（外部 MCP 工具可自行声明），平台表兜底
        text = d.usage_guidance or PLATFORM_TOOL_GUIDANCE.get(d.name)
        if text:
            guidance[d.name] = text
    return ToolProviderResult(schemas=schemas, guidance=guidance)


tools(_platform_tool_provider)
