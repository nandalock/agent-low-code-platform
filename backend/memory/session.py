"""会话记忆 — 上下文检查与渲染

参考 ReMe 的 ContextChecker：
  - compact_tool_result 先对所有消息做字符串级截断，最近 keep_n 条豁免
  - check_context 从新旧往旧分组，保障 tool block 不拆散
  - 预留 system_prompt 和 response 的 token 空间
"""
from __future__ import annotations


class SessionMemory:
    """会话记忆 — 消息上下文管理"""

    # ── Tool 输出截断 ──

    def compact_tool_result_for_all(
        self,
        messages: list,
        keep_n: int = 3,
        max_chars: int = 1000,
    ) -> list:
        """
        对所有消息中的 tool/function 输出做截断，最近 keep_n 条豁免。

        参考 ReMe：最近 N 条消息免除 tool 截断（保持完整），
        旧消息的 tool 输出做头尾保留。
        """
        result: list = []
        for i, msg in enumerate(messages):
            role = self._role(msg)
            content = self._content(msg)
            is_recent = i >= len(messages) - keep_n

            if role in ('tool', 'function') and not is_recent:
                result.append(self._with_content(msg, self._truncate(content, max_chars)))
            else:
                result.append(msg)
        return result

    @staticmethod
    def _truncate(content: str, max_chars: int) -> str:
        if len(content) <= max_chars:
            return content
        half = max_chars // 2
        return (
            f"{content[:half]}\n"
            f"... [截断 {len(content) - max_chars} 字符] ...\n"
            f"{content[-half:]}"
        )

    # ── 上下文检查 ──

    def check_context(
        self,
        messages: list,
        available_tokens: int,
    ) -> tuple[list, list]:
        """
        从新往旧分组，返回 (to_keep, to_compact)。

        参考 ReMe：
          - available_tokens = max_tokens - system_tokens - reserve_tokens
          - tool/system 与前面的 customer/agent 绑定为 block，不拆散
        """
        if not messages:
            return [], []

        to_keep: list = []
        to_compact: list = []
        tokens_used = 0

        i = len(messages) - 1
        while i >= 0:
            block, i = self._pop_block_backward(messages, i)
            block_tokens = sum(self._estimate_tokens(self._content(m)) for m in block)

            if tokens_used + block_tokens <= available_tokens:
                to_keep = block + to_keep
                tokens_used += block_tokens
            else:
                to_compact = block + to_compact

        return to_keep, to_compact

    # ── 渲染 ──

    def render(self, messages: list) -> str:
        """消息列表 → prompt 文本"""
        lines: list[str] = []
        for msg in messages:
            role = self._role(msg)
            content = self._content(msg)
            if role == 'customer':
                lines.append(f"用户：{content}")
            elif role == 'agent':
                lines.append(f"客服：{content}")
            elif role in ('tool', 'function'):
                lines.append(f"[{role}] {content}")
            elif role == 'system':
                lines.append(f"[system] {content}")
        return "\n".join(lines)

    # ── 内部 ──

    def _pop_block_backward(self, messages: list, cursor: int) -> tuple[list, int]:
        """
        从 cursor 往前取一个 block。
        tool/system/function 向前绑定到最近的 customer/agent。
        """
        block: list = []
        i = cursor
        # 辅助消息
        while i >= 0:
            role = self._role(messages[i])
            if role in ('tool', 'system', 'function'):
                block.insert(0, messages[i])
                i -= 1
            else:
                break
        # 主体消息（customer 或 agent）
        if i >= 0:
            block.insert(0, messages[i])
            i -= 1
        return block, i

    # LangChain type → 我们的角色名
    _ROLE_MAP = {"human": "customer", "ai": "agent", "tool": "tool", "system": "system"}

    @staticmethod
    def _role(msg) -> str:
        raw = ""
        if hasattr(msg, 'type'):
            raw = msg.type           # LangChain: human/ai/tool/system
        elif hasattr(msg, 'role'):
            raw = msg.role           # DB model: customer/agent/tool/system
        elif hasattr(msg, 'get'):
            raw = msg.get('role', '')
        return SessionMemory._ROLE_MAP.get(raw, raw)

    @staticmethod
    def _content(msg) -> str:
        if hasattr(msg, 'content'):
            return msg.content or ''
        if hasattr(msg, 'get'):
            return msg.get('content', '')
        return ''

    @staticmethod
    def _with_content(msg, new_content: str):
        """复制消息，替换 content"""
        if hasattr(msg, 'model_copy'):
            return msg.model_copy(update={"content": new_content})
        return msg

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return len(text) // 2
