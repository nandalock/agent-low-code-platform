"""记忆系统接入接口 — 编排层只依赖这个接口"""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class PromptContext:
    """记忆上下文 — 不同消费者各取所需"""
    profile: str = ""     # 用户画像
    summary: str = ""     # 压缩摘要
    recent: str = ""      # 滑动窗口（最近 N 轮原文）
    full: str = ""        # profile + summary + recent 拼好的完整文本

    @staticmethod
    def empty() -> "PromptContext":
        return PromptContext()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PromptContext":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class MemoryHook:
    """记忆系统标准接入点 — 空实现（默认不接入）"""

    async def on_assemble(
        self, messages: list, llm,
        system_prompt: str = "", **ctx,
    ) -> PromptContext:
        return PromptContext.empty()

    def quick_context(self, messages: list) -> PromptContext:
        return PromptContext.empty()
