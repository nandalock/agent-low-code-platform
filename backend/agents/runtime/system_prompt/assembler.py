"""assembler — 真正执行组装：求值 → 排序 → 严格插值 → 拼接

两阶段（对齐 DSH `assemble()` / `renderPrompt()` 的分离）：

  assemble(ctx)      求值全部 provider、排定顺序、裁决 complete，返回 PromptAssembly
                     （段文本已求值但**尚未插值**）
  render_*(assembly) 插值 `{{变量}}`、丢弃空段、以空行连接成最终文本

分两阶段的理由：工具 provider 与 section provider 的求值顺序有依赖——工具指导段
需要知道「本 agent 实际可见哪些工具」，因此 assemble 必须先把工具求值完、回填
`visible_tool_names`，再求值 section。而插值是纯函数变换，放第二阶段可让同一份
assembly 被渲染多次（如 prompt-preview 同时展示分段明细与完整文本）。

流水线顺序（不可调换）：
  ① 求值 tool providers → 可见工具集合 + guidance 字典
  ② 回填 ctx.visible_tool_names（section provider 依赖它做条件渲染）
  ③ 求值 section providers + 为「可见且有 guidance 的工具」合成 tool:<name> 段
  ④ 段按 (order, name) 排序
  ⑤ complete 裁决（>1 抛错；恰 1 → 成为唯一段）
  ⑥ 求值 context providers 并按 (order, name) 排序
  ⑦ 求值 variables
"""
import inspect
import re
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from backend.agents.runtime.system_prompt.tool import ToolProviderResult, ToolSchema
from backend.agents.runtime.system_prompt.variable import VARIABLE_NAME

if TYPE_CHECKING:  # 仅类型标注：system_prompt 运行时会 import 本模块
    from backend.agents.runtime.session import Session
    from backend.agents.runtime.system_prompt.system_prompt import SystemPrompt

#: 合成工具指导段的名字前缀（保留名空间：注册段不得以此开头）
TOOL_SECTION_PREFIX = "tool:"

#: 一个完整的 `{{name}}` 引用组（组内不含花括号）
_GROUP_AT = re.compile(r"\{\{([^{}]*)\}\}")


# ── 组装输入 ──


@dataclass
class AssembleContext:
    """一次组装请求的上下文

    字段所有权：
      tenant_id / agent_key / agent_name / agent_description / config / question /
      session / upstream_context  —— 调用方（AgentRuntime.reply）填
      visible_tool_names           —— assembler 在阶段 ② 回填，调用方不填

    组装不改写调用方传入的对象：阶段 ② 用 dataclasses.replace 派生新上下文，
    调用方的实例始终保持 visible_tool_names=None。
    """

    tenant_id: int = 0
    agent_key: str = ""
    agent_name: str = ""
    agent_description: str = ""
    config: dict = field(default_factory=dict)   # AgentRuntime._cfg() 的合并结果
    question: str = ""                            # 当前用户问题（尾部 context 可引用）
    session: "Session | None" = None              # 运行态 Session（header.cwd 等）
    upstream_context: dict | None = None          # 上游节点输出（workflow 场景）
    #: 本 agent 可见的工具名集合；assembler 回填（tool provider 求值时看到的仍是 None）
    visible_tool_names: frozenset[str] | None = None


# ── 组装产物 ──


@dataclass(frozen=True)
class AssembledSection:
    """一段已求值、未插值的提示词段"""

    name: str
    text: str
    complete: bool = False


@dataclass(frozen=True)
class AssembledContext:
    """一段已求值、未插值的动态上下文"""

    name: str
    text: str


@dataclass
class PromptAssembly:
    """一次组装的完整产物（sections / contexts / tools 均已排序）"""

    sections: list[AssembledSection] = field(default_factory=list)
    contexts: list[AssembledContext] = field(default_factory=list)
    tools: list[ToolSchema] = field(default_factory=list)
    variables: dict[str, str | None] = field(default_factory=dict)


# ── 组装 ──


async def assemble(sp: "SystemPrompt", ctx: AssembleContext) -> PromptAssembly:
    """执行一次组装（求值 + 排序 + complete 裁决，不插值）

    异步只因为工具来源可能有 I/O（远程 MCP 工具懒加载）；文本 provider 同步。

    Raises:
        ValueError: 跨 provider 工具重名 / 段名占用保留前缀 / complete 段多于一个
        TypeError: provider 返回类型不符
    """
    # ① 工具：先求值，section 的条件渲染依赖它
    schemas, guidance = await _resolve_tools(sp, ctx)

    # ② 回填可见工具名（派生新 ctx，不改调用方对象）
    ctx = replace(ctx, visible_tool_names=frozenset(t.name for t in schemas))

    # ③④ 段：注册段求值 + 工具指导段合成 + 排序
    sections = _resolve_sections(sp, ctx, guidance)

    # ⑤ complete 裁决
    sections = _arbitrate_complete(sections)

    # ⑥⑦ 上下文与变量
    return PromptAssembly(
        sections=sections,
        contexts=_resolve_contexts(sp, ctx),
        tools=schemas,
        variables={name: var.provider(ctx) for name, var in sp.variables().items()},
    )


async def _resolve_tools(
    sp: "SystemPrompt", ctx: AssembleContext
) -> tuple[list[ToolSchema], dict[str, str]]:
    """求值全部工具 provider；跨 provider 重名工具抛错

    provider 返回 awaitable 时 await（允许同步实现，便于简单来源与测试）。
    """
    schemas: list[ToolSchema] = []
    guidance: dict[str, str] = {}
    seen: set[str] = set()
    for provider in sp.tool_providers():
        result = provider(ctx)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, ToolProviderResult):
            raise TypeError(
                f"tool provider 必须返回 ToolProviderResult，实际 {type(result).__name__}"
            )
        for schema in result.schemas:
            if schema.name in seen:
                raise ValueError(f"工具 {schema.name!r} 被多个 provider 重复提供")
            seen.add(schema.name)
            schemas.append(schema)
        guidance.update(result.guidance)
    schemas.sort(key=lambda t: t.name)
    return schemas, guidance


def _resolve_sections(
    sp: "SystemPrompt", ctx: AssembleContext, guidance: dict[str, str]
) -> list[AssembledSection]:
    """注册段求值 + 工具指导段合成，返回按 (order, name) 排好序的段"""
    ordered: list[tuple[int | float, AssembledSection]] = []

    for section in sp.sections().values():
        if section.name.startswith(TOOL_SECTION_PREFIX):
            raise ValueError(
                f"段名 {section.name!r} 使用了保留前缀 {TOOL_SECTION_PREFIX!r}"
                f"（该前缀留给工具指导段）"
            )
        ordered.append((
            section.order,
            AssembledSection(
                name=section.name,
                text=_eval_text(section.text, ctx, section.name),
                complete=section.complete,
            ),
        ))

    # 为「可见且有 guidance」的工具合成指导段：工具不可见 → 说明书一并消失
    visible = ctx.visible_tool_names or frozenset()
    for tool in sorted(guidance):
        if tool not in visible:
            continue
        ordered.append((
            sp.get_section_order("TOOL_GUIDANCE"),
            AssembledSection(name=f"{TOOL_SECTION_PREFIX}{tool}", text=guidance[tool]),
        ))

    return [s for _, s in sorted(ordered, key=lambda item: (item[0], item[1].name))]


def _resolve_contexts(sp: "SystemPrompt", ctx: AssembleContext) -> list[AssembledContext]:
    """上下文 provider 求值，返回按 (order, name) 排好序的上下文"""
    ordered = [
        (
            context.order,
            AssembledContext(
                name=context.name, text=_eval_text(context.text, ctx, context.name),
            ),
        )
        for context in sp.contexts().values()
    ]
    return [c for _, c in sorted(ordered, key=lambda item: (item[0], item[1].name))]


def _arbitrate_complete(sections: list[AssembledSection]) -> list[AssembledSection]:
    """complete 段裁决：多于一个抛错；恰一个则成为唯一段"""
    complete = [s for s in sections if s.complete]
    if len(complete) > 1:
        raise ValueError(
            "存在多个 complete 提示词段，组装失败："
            + "、".join(repr(s.name) for s in complete)
        )
    return complete or sections


def _eval_text(text, ctx: AssembleContext, owner: str) -> str:
    """求值段/上下文的文本来源（静态串或 provider）"""
    value = text(ctx) if callable(text) else text
    if not isinstance(value, str):
        raise TypeError(
            f"提示词 {owner!r} 的文本 provider 必须返回 str，实际 {type(value).__name__}"
        )
    return value


# ── 渲染 ──


def render_prompt(assembly: PromptAssembly) -> str:
    """渲染提示词段（不含动态上下文）"""
    return _join_blocks(
        interpolate(s.text, assembly.variables, kind="section", owner=s.name)
        for s in assembly.sections
    )


def render_context_snapshot(assembly: PromptAssembly) -> str:
    """渲染动态上下文（不含提示词段）"""
    return _join_blocks(
        interpolate(c.text, assembly.variables, kind="context", owner=c.name)
        for c in assembly.contexts
    )


def render_system_prompt(assembly: PromptAssembly) -> str:
    """渲染最终系统提示词：段在前、动态上下文在尾部，空块丢弃

    「稳定前缀 + 动态尾部」由 CONTEXT_ORDERS 的数值保证（见
    `system_prompt.CONTEXT_ORDERS`），是结构保证而非调用约定。
    全空时返回 `""`（调用方不应发送空的 system 消息）。
    """
    return _join_blocks(
        interpolate(item.text, assembly.variables, kind=kind, owner=item.name)
        for kind, items in (
            ("section", assembly.sections),
            ("context", assembly.contexts),
        )
        for item in items
    )


def _join_blocks(blocks: Iterable[str]) -> str:
    """以空行连接非空块"""
    return "\n\n".join(block for block in blocks if block)


def interpolate(
    text: str,
    variables: dict[str, str | None],
    *,
    kind: str,
    owner: str,
) -> str:
    """严格插值 `{{name}}` 引用

    规则（对齐 DSH `interpolate`）：
      - 完整组且变量已注册有值 → 替换
      - 未注册名 / 已注册无值 / 组名非法（含 `{{}}`）→ 抛错
      - `{{` 之后还有 `}}` 但组不完整 → 抛错（畸形组）
      - 孤立未闭合的 `{{`（其后无 `}}`）→ 视为字面行文
      - 替换后的值不再重扫描

    Raises:
        ValueError: 未注册 / 无值 / 畸形引用（错误消息指明 owner 与片段）
    """
    result: list[str] = []
    last = 0
    open_at = text.find("{{")
    while open_at >= 0:
        group = _GROUP_AT.match(text, open_at)
        if group is None:
            if text.find("}}", open_at + 2) >= 0:
                raise ValueError(
                    f"{kind} {owner!r} 中存在畸形的变量引用："
                    f"{text[open_at:open_at + 16]!r}（应为完整的 {{{{name}}}} 形式）"
                )
            # 孤立 {{ —— 当作字面行文，跳过这两个字符继续扫
            result.append(text[last:open_at + 2])
            last = open_at + 2
        else:
            name = group.group(1)
            result.append(
                text[last:open_at] + _resolve_variable(name, variables, kind=kind, owner=owner)
            )
            last = group.end()
        open_at = text.find("{{", last)

    result.append(text[last:])
    return "".join(result)


def _resolve_variable(
    name: str, variables: dict[str, str | None], *, kind: str, owner: str
) -> str:
    """校验并取变量值（严格）"""
    if not VARIABLE_NAME.fullmatch(name):
        raise ValueError(
            f"{kind} {owner!r} 中存在畸形的变量引用 {{{{{name}}}}}"
            f"（变量名须匹配 {VARIABLE_NAME.pattern}）"
        )
    if name not in variables:
        known = "、".join(sorted(variables)) or "(无)"
        raise ValueError(
            f"{kind} {owner!r} 引用了未注册的变量 {name!r}；已注册的变量：{known}"
        )
    value = variables[name]
    if value is None:
        raise ValueError(f"{kind} {owner!r} 引用的变量 {name!r} 在本次组装中没有值")
    return value
