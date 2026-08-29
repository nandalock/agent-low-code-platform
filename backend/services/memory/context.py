"""记忆上下文组装层 — 自管理缓冲，每次只收新消息"""
from __future__ import annotations

import logging

from backend.services.memory.session import SessionMemory
from backend.services.memory.compressor import MemoryCompressor
from backend.services.memory.summarizer import ProfileExtractor
from backend.services.memory.experience import ExperienceMemory
from backend.services.memory.hooks import MemoryHook, PromptContext
from backend.services.memory.config import get_config as get_memory_config

logger = logging.getLogger(__name__)


class MemoryContext(MemoryHook):
    """自管理缓冲 + 摘要，每次只收新增消息"""

    def __init__(
        self,
        session: SessionMemory | None = None,
        compressor: MemoryCompressor | None = None,
        summarizer: ProfileExtractor | None = None,
        experience: ExperienceMemory | None = None,
    ):
        self.session = session or SessionMemory()
        self.compressor = compressor or MemoryCompressor()
        self.summarizer = summarizer or ProfileExtractor()
        self.experience = experience or ExperienceMemory()
        self._llm = None
        self._buffer: list = []
        self._summary: str | None = None
        self._total = 0

    # ── 状态持久化 ──

    def load_state(self, state: dict):
        saved = PromptContext.from_dict(state.get("memory", {}))
        self._summary = saved.summary or None
        # 从未压缩部分重建缓冲
        processed = state.get("_mem_processed", 0)
        all_msgs = state.get("messages", [])
        self._buffer = list(all_msgs[processed:])
        self._total = len(all_msgs)

    def save_state(self) -> dict:
        return {"_mem_processed": self._total - len(self._buffer)}

    # ── Hook 接口 ──

    @property
    def buffer_len(self) -> int:
        return len(self._buffer)

    async def on_assemble(
        self, messages: list, llm,
        system_prompt: str = "", **ctx,
    ) -> PromptContext:
        self._llm = llm
        if messages:
            self._buffer.extend(messages)
        return await self._process(
            llm=llm, system_prompt=system_prompt,
            tenant_id=ctx.get("tenant_id", 0),
            user_id=ctx.get("user_id", ""),
        )

    async def on_persist(self, messages: list, **ctx):
        if self._llm and self.summarizer and ctx.get("user_id"):
            self.summarizer.schedule(
                self._llm, messages,
                ctx.get("tenant_id", 0),
                ctx.get("user_id", ""),
            )

    def quick_context(self, messages: list) -> PromptContext:
        cfg = get_memory_config()
        available = cfg["max_tokens"] - cfg["reserve_tokens"]
        compacted = self.session.compact_tool_result_for_all(
            messages, keep_n=cfg["tool_keep_n"], max_chars=cfg["tool_max_chars"],
        )
        to_keep, _ = self.session.check_context(compacted, available)
        recent = self.session.render(to_keep)
        return PromptContext(recent=recent, full=recent)

    # ── 内部 ──

    async def _process(
        self, llm,
        system_prompt: str = "",
        tenant_id: int = 0,
        user_id: str = "",
    ) -> PromptContext:
        cfg = get_memory_config()
        threshold = int(cfg["max_tokens"] * cfg["compact_ratio"] * 0.9)
        system_tokens = self.session._estimate_tokens(system_prompt)
        summary_tokens = self.session._estimate_tokens(self._summary or "")
        budget = max(threshold - system_tokens - summary_tokens - cfg["reserve_tokens"],
                     cfg["max_tokens"] // 2)

        buf_tokens = sum(self.session._estimate_tokens(
            self.session._content(m)) for m in self._buffer
        )

        if buf_tokens > budget:
            compacted = self.session.compact_tool_result_for_all(
                self._buffer,
                keep_n=cfg["tool_keep_n"],
                max_chars=cfg["tool_max_chars"],
            )
            to_keep, to_compact = self.session.check_context(compacted, budget)

            if to_compact:
                logger.info(f"压缩: {len(to_compact)} 条 → LLM")
                self._summary = await self.compressor.compact(
                    llm=llm, messages=to_compact,
                    previous_summary=self._summary,
                )
                if user_id:
                    self.summarizer.schedule(llm, to_compact, tenant_id, user_id)

            self._buffer = to_keep
        else:
            compacted = self.session.compact_tool_result_for_all(
                self._buffer,
                keep_n=cfg["tool_keep_n"],
                max_chars=cfg["tool_max_chars"],
            )
            to_keep = compacted

        recent = self.session.render(to_keep)
        profile_text = self.experience.render_profile(tenant_id, user_id)
        summary = self._summary or ""
        parts = [p for p in [profile_text, summary, recent] if p]
        full = "\n\n".join(parts)

        return PromptContext(profile=profile_text, summary=summary, recent=recent, full=full)
