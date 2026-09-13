"""ToolSchema / ToolProviderResult / ToolProvider — 工具在组装里的两种身份

**核心区分：工具 schema 与工具使用指导是两样东西，同名但不同层。**

  ToolSchema（wire 层）    `{"type": "function", "function": {name, description, parameters}}`
                          发给 LLM API 的 `tools` 参数，供 **API 解析**成可调用接口
  usage_guidance（提示词层）自然语言，例如「检查结果里的 [exit code: N] 标记；
                          失败先排查再继续」，供 **模型阅读**理解何时用、怎么用

两者永不合并——schema 拼进 system prompt 文本会变成需要模型自己解析的散文，
且与 `tools` 参数里的同一份定义重复（改一处漏一处）；而「什么时候该用」这种
跨调用习惯根本写不成 JSON Schema。

**guidance 放在 ToolProviderResult 而非 ToolSchema**：指导文本是提示词层事实，
由 assembler 合成 `tool:<name>` 段（见 `assembler.assemble`），与发给 API 的
schema 解耦后 `ToolSchema.to_openai()` 保持纯净。

工具被限制（未绑定该 agent）时，其 schema 与 guidance 段同时消失——不存在
「说明书还在、工具没了」的漂移态。
"""
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型标注：assembler 与 tool 互相引用，运行时不 import
    from backend.agents.runtime.system_prompt.assembler import AssembleContext


@dataclass(frozen=True)
class ToolSchema:
    """一个工具对 LLM API 的可见定义（wire 层）"""

    name: str      # 工具名（与 guidance 的键、`tool:<name>` 段名对应）
    function: dict  # OpenAI function 子对象：{name, description, parameters}

    def to_openai(self) -> dict:
        """→ AgentLoop 直接消费的 OpenAI tools 数组元素"""
        return {"type": "function", "function": self.function}


@dataclass(frozen=True)
class ToolProviderResult:
    """一次组装中某个 provider 贡献的工具集合"""

    schemas: list[ToolSchema] = field(default_factory=list)  # 本 agent 可见的工具
    #: tool_name → 使用指导文本；无指导的工具不出现在这里
    guidance: dict[str, str] = field(default_factory=dict)


#: 工具贡献方：每轮组装时求值，返回本次可见的工具集合
ToolProvider = Callable[["AssembleContext"], ToolProviderResult]
