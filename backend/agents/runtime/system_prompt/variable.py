"""PromptVariable — 段文本中 `{{name}}` 引用的取值来源

移植自 DeepSeek Harness `packages/core/system-prompt/src/index.ts` 的变量机制。
owner（注册变量的一方）与消费者（在段文本里写 `{{name}}` 的一方）由此解耦：
写 prompt 的人不需要知道当前 agent 叫什么、工作目录在哪，只写占位符。

渲染规则（严格，见 `assembler.interpolate`）：
  - 未注册的变量名 → 抛错（并列出全部已注册名）
  - 已注册但 provider 返回 None → 抛错（"本轮无值"）
  - 返回空串 `""` 是**合法值**（插值为空，可能让整段变空而消失）
  - 替换后的值不再重扫描（值里的 `{{` 原样保留）

严格是刻意的：一个拼错的 `{{modle}}` 若被静默放过或替换成空，就会带着错误
行文发给模型，只有事后翻记录才能发现——格式错误的提示词比响亮失败更糟。
"""
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 仅类型标注：assembler 与 variable 互相引用，运行时不 import
    from backend.agents.runtime.system_prompt.assembler import AssembleContext

#: 合法的变量名：小写字母开头，后接小写字母/数字/下划线
VARIABLE_NAME_PATTERN = r"[a-z][a-z0-9_]*"
VARIABLE_NAME = re.compile(VARIABLE_NAME_PATTERN)

#: 变量 provider：每轮组装时求值；返回 None 表示本轮无值（被引用时渲染失败）
VariableProvider = Callable[["AssembleContext"], "str | None"]


@dataclass(frozen=True)
class PromptVariable:
    """一个 `{{name}}` 引用的取值来源（注册后不可变）"""

    name: str                 # 必须匹配 VARIABLE_NAME；注册时校验
    provider: VariableProvider  # 每轮求值
