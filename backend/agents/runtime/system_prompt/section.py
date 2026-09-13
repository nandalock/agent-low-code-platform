"""PromptSection — 一段系统提示词文本（注册表输入）

移植自 DeepSeek Harness `packages/core/system-prompt/src/index.ts` 的
`PromptSection`。一次组装把注册好的段按 `order` 升序拼接成完整系统提示词。

字段语义：
  name      唯一名（重复注册抛错）；同 order 的段按名字码点序排列，保证跨机器确定
  order     排序位；数值越大越靠后。平台内置段统一取 `system_prompt.SECTION_ORDERS`
            里的具名常量（稀疏分配，新增内容不加塞、不改号）
  text      静态字符串，或每轮组装时求值的 provider（读 AssembleContext 决定内容）
  complete  v1 保留字段：标记该段为「完整提示词」（组装后成为唯一段）。
            有效 complete 段多于一个时组装失败

**段的顺序即缓存前缀的顺序**：前部段的内容每轮变化会让 DeepSeek 上下文缓存从
变化点起失效，因此前部段只应引用跨轮稳定的信息（部署事实、agent 配置），
逐轮变化的内容（上游输出、记忆、用户问题）放尾部 context（见 `context.py`）。

本模块只定义数据结构，不含组装逻辑（组装在 `assembler.py`）。
"""
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型标注：assembler 与 section 互相引用，运行时不 import
    from backend.agents.runtime.system_prompt.assembler import AssembleContext

#: 段文本来源：静态字符串，或按组装上下文求值的 provider
SectionText = str | Callable[["AssembleContext"], str]


@dataclass(frozen=True)
class PromptSection:
    """一段系统提示词文本（注册后不可变）"""

    name: str          # 唯一名；重复注册抛 ValueError
    order: int | float  # 升序拼接；同 order 按 name 码点序
    text: SectionText  # 静态文本或 provider
    complete: bool = False  # True = 完整提示词段（v1 保留，平台层未注册）
