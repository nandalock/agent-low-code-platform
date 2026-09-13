"""SystemPrompt 模块 — 分段注册的系统提示词组装

移植自 DeepSeek Harness `packages/core/system-prompt`。一次组装把注册好的
**段**（section）、**动态上下文**（context）、**变量**（variable）与
**工具**（tool provider）合成：

    messages[0].system  ← render_system_prompt(assembly)   ← 段 + 上下文（稳定前缀 + 动态尾部）
    payload["tools"]    ← [t.to_openai() for t in assembly.tools]

两种「工具相关」的产物刻意分开：wire 层的 tool schema 走 `tools` 参数
（给 API 解析），提示词层的工具使用指导走 `tool:<name>` 段（给模型阅读）。
详见 `tool.py` 的模块说明。

模块分层：
  section.py / context.py / variable.py / tool.py   数据结构（无逻辑）
  system_prompt.py                                  注册表 + order 常量分配
  assembler.py                                      组装流水线 + 严格渲染
  platform_sections.py                              平台内容（identity/persona/上游/工具指导）
"""
from backend.agents.runtime.system_prompt.assembler import (
    AssembleContext,
    AssembledContext,
    AssembledSection,
    PromptAssembly,
    interpolate,
    render_context_snapshot,
    render_prompt,
    render_system_prompt,
)
from backend.agents.runtime.system_prompt.context import PromptContext
from backend.agents.runtime.system_prompt.section import PromptSection
from backend.agents.runtime.system_prompt.system_prompt import (
    CONTEXT_ORDERS,
    SECTION_ORDERS,
    SystemPrompt,
    assemble,
    context,
    get_system_prompt,
    section,
    tools,
    variable,
)
from backend.agents.runtime.system_prompt.tool import (
    ToolProvider,
    ToolProviderResult,
    ToolSchema,
)
from backend.agents.runtime.system_prompt.variable import (
    VARIABLE_NAME,
    PromptVariable,
    VariableProvider,
)

__all__ = [
    # 数据结构
    "PromptSection",
    "PromptContext",
    "PromptVariable",
    "ToolSchema",
    "ToolProviderResult",
    "ToolProvider",
    "VariableProvider",
    "VARIABLE_NAME",
    # 注册表
    "SystemPrompt",
    "get_system_prompt",
    "section",
    "context",
    "variable",
    "tools",
    "SECTION_ORDERS",
    "CONTEXT_ORDERS",
    # 组装与渲染
    "AssembleContext",
    "AssembledSection",
    "AssembledContext",
    "PromptAssembly",
    "assemble",
    "render_prompt",
    "render_context_snapshot",
    "render_system_prompt",
    "interpolate",
]
