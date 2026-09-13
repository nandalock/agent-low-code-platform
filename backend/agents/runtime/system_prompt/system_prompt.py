"""SystemPrompt — 提示词贡献的注册表 + 具名 order 分配

移植自 DeepSeek Harness `packages/core/system-prompt/src/index.ts` 的
`SystemPrompt` 服务。四种贡献类型（section / context / variable / tool provider）
在此注册，组装在 `assembler.py` 完成。

**order 常量表为什么要具名**：段的位置是全局约定——identity 永远在最前、
工具指导在中间、动态上下文在最后。如果各处自己写数字，新增内容时会互相加塞、
改号，一次改动就断掉所有人的缓存前缀。因此集中分配：新增内容选一个空号段，
已有的号永不改动。

**为什么 context 的 order 恒大于全部 section**：DeepSeek 上下文缓存按前缀命中，
组装结果里「稳定正文在前、逐轮变化的上下文在尾部」这一形状保证尾部变化不会
让前部前缀失效。把 context 排在 section 中间会立刻破坏这个性质。

用法（业务侧注册在 `platform_sections.py`，框架层可被任何插件复用）：

    sp = get_system_prompt()
    sp.section(PromptSection(name="my:rule", order=500, text="……"))
    sp.variable("cwd", lambda ctx: ctx.session.header.cwd if ctx.session else None)
"""
import logging
import math

from backend.agents.runtime.system_prompt.assembler import (
    AssembleContext,
    PromptAssembly,
    assemble as _assemble,
)
from backend.agents.runtime.system_prompt.context import PromptContext
from backend.agents.runtime.system_prompt.section import PromptSection
from backend.agents.runtime.system_prompt.tool import ToolProvider
from backend.agents.runtime.system_prompt.variable import (
    VARIABLE_NAME,
    PromptVariable,
    VariableProvider,
)

logger = logging.getLogger(__name__)

#: 段位置：稀疏具名分配（-1000 最前，数值越大越靠后）
SECTION_ORDERS: dict[str, int] = {
    "AGENT_IDENTITY": -1000,    # 身份：从 agent name + description 生成
    "AGENT_PERSONA": 0,         # 角色与行为：config.system_prompt（管理台可编辑）
    "PLATFORM_BEHAVIOR": 500,   # 平台级通用行为约束（预留）
    "ENVIRONMENT": 800,         # 环境事实：沙箱模式/工作区（预留）
    "TOOL_GUIDANCE": 1000,      # 工具使用指导（全部同号，按工具名排序）
}

#: 动态上下文位置：数值恒 > 全部 SECTION_ORDERS（尾部保缓存前缀）
CONTEXT_ORDERS: dict[str, int] = {
    "MEMORY_CONTEXT": 8900,    # 记忆：画像/摘要/近期对话（预留）
    "UPSTREAM_CONTEXT": 9000,  # 上游节点输出（workflow 场景）
}


class SystemPrompt:
    """提示词贡献注册表（进程内单例，见 `get_system_prompt`）"""

    def __init__(self) -> None:
        self._sections: dict[str, PromptSection] = {}
        self._contexts: dict[str, PromptContext] = {}
        self._variables: dict[str, PromptVariable] = {}
        self._tool_providers: list[ToolProvider] = []

    # ── 注册 ──

    def section(self, section: PromptSection) -> None:
        """注册一段提示词

        Raises:
            ValueError: 同名段已注册 / order 非有限数
        """
        _validate_order(section.order, "段", section.name)
        if section.name in self._sections:
            raise ValueError(f"提示词段 {section.name!r} 已注册")
        self._sections[section.name] = section

    def context(self, context: PromptContext) -> None:
        """注册一段动态上下文

        Raises:
            ValueError: 同名上下文已注册 / order 非有限数
        """
        _validate_order(context.order, "上下文", context.name)
        if context.name in self._contexts:
            raise ValueError(f"动态上下文 {context.name!r} 已注册")
        self._contexts[context.name] = context

    def variable(self, name: str, provider: VariableProvider) -> None:
        """注册一个 `{{name}}` 变量

        Raises:
            ValueError: 变量名非法 / 同名变量已注册
        """
        if not VARIABLE_NAME.fullmatch(name):
            raise ValueError(
                f"非法变量名 {name!r}（须匹配 {VARIABLE_NAME.pattern}）"
            )
        if name in self._variables:
            raise ValueError(f"提示词变量 {name!r} 已注册")
        self._variables[name] = PromptVariable(name=name, provider=provider)

    def tools(self, provider: ToolProvider) -> None:
        """注册一个工具贡献方（匿名，可多个；跨 provider 工具重名在组装时抛错）"""
        self._tool_providers.append(provider)

    # ── 只读视图（assembler 用） ──

    def sections(self) -> dict[str, PromptSection]:
        return dict(self._sections)

    def contexts(self) -> dict[str, PromptContext]:
        return dict(self._contexts)

    def variables(self) -> dict[str, PromptVariable]:
        return dict(self._variables)

    def tool_providers(self) -> list[ToolProvider]:
        return list(self._tool_providers)

    # ── order 解析 ──

    @staticmethod
    def get_section_order(name: str) -> int:
        """取具名段位置

        Raises:
            KeyError: 名字不在 SECTION_ORDERS 内
        """
        if name not in SECTION_ORDERS:
            raise KeyError(
                f"未知的段位置 {name!r}；可用：{sorted(SECTION_ORDERS)}"
            )
        return SECTION_ORDERS[name]

    @staticmethod
    def get_context_order(name: str) -> int:
        """取具名上下文位置

        Raises:
            KeyError: 名字不在 CONTEXT_ORDERS 内
        """
        if name not in CONTEXT_ORDERS:
            raise KeyError(
                f"未知的上下文位置 {name!r}；可用：{sorted(CONTEXT_ORDERS)}"
            )
        return CONTEXT_ORDERS[name]

    # ── 组装 ──

    def assemble(self, ctx: AssembleContext) -> PromptAssembly:
        """执行一次组装（委托 assembler，见其流水线说明）"""
        return _assemble(self, ctx)


def _validate_order(order: int | float, kind: str, name: str) -> None:
    """order 必须是有限数（NaN/inf/布尔/字符串都拒绝）

    Raises:
        ValueError: 取值非法
    """
    if isinstance(order, bool) or not isinstance(order, (int, float)):
        raise ValueError(f"{kind} {name!r} 的 order 必须是数字，实际 {type(order).__name__}")
    if not math.isfinite(order):
        raise ValueError(f"{kind} {name!r} 的 order 必须是有限数，实际 {order!r}")


# ── 模块级单例与便捷函数 ──

_default = SystemPrompt()


def get_system_prompt() -> SystemPrompt:
    """取进程内单例注册表"""
    return _default


def section(s: PromptSection) -> None:
    """向单例注册一段提示词"""
    _default.section(s)


def context(c: PromptContext) -> None:
    """向单例注册一段动态上下文"""
    _default.context(c)


def variable(name: str, provider: VariableProvider) -> None:
    """向单例注册一个变量"""
    _default.variable(name, provider)


def tools(provider: ToolProvider) -> None:
    """向单例注册一个工具贡献方"""
    _default.tools(provider)


def assemble(ctx: AssembleContext) -> PromptAssembly:
    """用单例注册表执行一次组装"""
    return _default.assemble(ctx)
