"""记忆压缩 — 模仿 ReMe Compactor

ReMe 的做法：
  1. 不是简单的一次 LLM 调用，而是让 LLM 先思考再输出
  2. 结构化摘要：Goal / Constraints / Progress / KeyDecisions / NextSteps / CriticalContext
  3. 工具调用 → 提炼进 KeyDecisions（名称 + 核心结果，丢弃冗余输出）
  4. 支持增量更新（previous_summary）
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


COMPACT_SYSTEM_PROMPT = """你是一个对话记忆压缩器。接收多轮对话消息，输出结构化摘要。

## 你的任务
分析对话消息，提取关键信息，忽略闲聊和冗余。
## 压缩原则
- 用户目标：用户到底想要什么？为什么来咨询？
- 约束与偏好：用户提了什么条件？喜欢/不喜欢什么？
- 当前进展：已经完成了什么？走到哪一步了？
- 关键决策：做了什么决策？为什么？调了什么工具？工具返回了什么核心结果？（忽略工具输出的冗余细节）
- 后续步骤：还需要做什么？用户是否提出了待处理的问题？
- 重要上下文：工单号、订单号、错误信息、文件名等容易丢失的细节

## 工具消息处理
对话中 [工具结果|tool] 和 [工具结果|function] 标记的消息是工具调用的返回内容。
从中提取核心结果（如"查到3条记录"、"匹配分数0.95"），丢弃完整输出。

## 增量更新
如果提供了"上次摘要"，在此基础上补充新信息，不重复已有内容。

## 输出格式
严格按以下 Markdown 格式输出，不要额外内容：

## 用户目标
...

## 约束与偏好
...

## 当前进展
...

## 关键决策
...

## 后续步骤
...

## 重要上下文
..."""


COMPACT_USER_PROMPT = """{previous_summary}{conversation}

请先分析对话中的关键信息，然后输出结构化摘要。"""


class MemoryCompressor:
    """记忆压缩器 — 模仿 ReMe Compactor"""

    def __init__(self, system_prompt: str | None = None):
        self.system_prompt = system_prompt or COMPACT_SYSTEM_PROMPT

    async def compact(
        self,
        llm,  # ChatOpenAI 或兼容实例
        messages: list,
        previous_summary: str | None = None,
    ) -> str:
        """压缩消息列表 → 结构化摘要"""
        if not messages:
            return previous_summary or ""

        conversation = self._render(messages)
        prompt = self._build_prompt(conversation, previous_summary)

        from langchain_core.messages import SystemMessage, HumanMessage

        try:
            response = await llm.ainvoke([
                SystemMessage(content=self.system_prompt),
                HumanMessage(content=prompt),
            ])
            summary = response.content.strip()
            ratio = len(summary) / max(len(conversation), 1) * 100
            logger.info(f"压缩: {len(conversation)} → {len(summary)} 字符 ({ratio:.0f}%)")
            return summary
        except Exception as e:
            logger.warning(f"压缩失败: {e}")
            return conversation  # 降级

    # ── 内部 ──

    @staticmethod
    def _render(messages: list) -> str:
        """消息列表 → 纯文本。"""
        lines: list[str] = []
        for msg in messages:
            role = Compressor._role(msg)
            content = Compressor._content(msg)

            if role == 'customer':
                lines.append(f"用户：{content}")
            elif role == 'agent':
                lines.append(f"客服：{content}")
            elif role in ('tool', 'function'):
                lines.append(f"[工具结果|{role}] {content}")
            elif role == 'system':
                lines.append(f"[系统] {content}")
            else:
                lines.append(f"[{role}] {content}")
        return "\n".join(lines)

    @staticmethod
    def _build_prompt(conversation: str, previous_summary: str | None) -> str:
        prev = f"## 上次摘要\n{previous_summary}\n\n---\n\n" if previous_summary else ""
        return COMPACT_USER_PROMPT.format(
            previous_summary=prev,
            conversation=conversation,
        )

    _ROLE_MAP = {"human": "customer", "ai": "agent", "tool": "tool", "system": "system"}

    @staticmethod
    def _role(msg) -> str:
        raw = ""
        if hasattr(msg, 'type'):    raw = msg.type
        elif hasattr(msg, 'role'):  raw = msg.role
        elif hasattr(msg, 'get'):   raw = msg.get('role', '')
        return MemoryCompressor._ROLE_MAP.get(raw, raw)

    @staticmethod
    def _content(msg) -> str:
        if hasattr(msg, 'content'): return msg.content or ''
        if hasattr(msg, 'get'): return msg.get('content', '')
        return ''
