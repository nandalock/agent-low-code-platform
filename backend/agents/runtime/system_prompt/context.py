"""PromptContext — 一段动态运行时上下文（注册表输入）

与 `section.py` 的 PromptSection 结构同构，区别在**内容性质与排序位置**：

  section  跨轮稳定的提示词正文（身份、角色、工具使用指导）
  context  逐轮变化的运行时事实（上游节点输出、记忆摘要、会话环境）

context 的 order 恒大于全部 section 的 order（见 `system_prompt.CONTEXT_ORDERS`），
于是组装结果天然是「静态正文在前 + 动态上下文在尾部」——DeepSeek 的上下文缓存
按前缀命中，尾部变化不会让前部的稳定前缀失效。

**命名提示**：本类与 `backend.services.memory.hooks.PromptContext` 同名但不同义
（后者是记忆系统一次组装的状态对象，含 profile/summary/recent/full 四字段）。
同时需要两者的模块请用 import 别名区分。

空文本（`""`）不贡献任何内容——provider 据此实现「本轮无此上下文」。
"""
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型标注：assembler 与 context 互相引用，运行时不 import
    from backend.agents.runtime.system_prompt.assembler import AssembleContext

#: 上下文文本来源：静态字符串，或按组装上下文求值的 provider
ContextText = str | Callable[["AssembleContext"], str]


@dataclass(frozen=True)
class PromptContext:
    """一段动态运行时上下文（注册后不可变）"""

    name: str          # 唯一名；重复注册抛 ValueError
    order: int | float  # 升序排列；同 order 按 name 码点序
    text: ContextText  # 静态文本或 provider；空串不贡献内容
