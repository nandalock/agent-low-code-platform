"""一步的流式装配 —— StreamChunk 序列 → 完整 assistant message（对齐 A 的 assistant-stream.ts）

**SSE 解析在适配器，落库在循环**，本模块就是夹在中间的那一段。它同时收口两件事：

  1. **装配**：把 canonical 分片按 index 拼成一条完整 assistant message —— 流式下
     tool_calls 的 id / name / arguments 是**分段**到达的（同一个调用的参数会被切成
     很多帧），拼不齐就是一条不可复现的事实。
  2. **事实**：每个增量**立刻**落成 ``assistant/chunk`` 事件。广播出去的增量就是发生过
     的事实（前端已经看到过），重试时撤不回来、被取消时也不该从日志里消失。

取消语义随之归位：中断时它交出「已经产出到哪儿」的 message，循环把它包成
``RunCancelled.partial``（见 agent_loop._settle_interrupted）—— 适配器不认识取消，
也不认识 Session。一次**尝试**一个实例（重试不能把上一次的残片拼进这一次）。
"""
from backend.agents.runtime.llm import (
    CHUNK_FINISH,
    CHUNK_TEXT,
    CHUNK_THINKING,
    CHUNK_TOOL_CALLS,
    CHUNK_USAGE,
    StreamChunk,
    TokenUsage,
)
from backend.agents.runtime.session import ASSISTANT_CHUNK, Session


class AssistantStream:
    """一次流式尝试的装配器（对齐 A 的 AssistantStreamAttempt）。"""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._message: dict = {"role": "assistant"}
        self._finish_reason: str | None = None
        self._usage: TokenUsage | None = None

    def push(self, chunk: StreamChunk) -> None:
        """吃下一个分片：累积进 message + 把增量落成事实（能广播的都广播）。"""
        kind = chunk.kind
        if kind == CHUNK_TEXT:
            self._message["content"] = self._message.get("content", "") + chunk.delta
            self._session.append(ASSISTANT_CHUNK, {"kind": "text", "delta": chunk.delta})
        elif kind == CHUNK_THINKING:
            # 推理过程只广播、不混入 content（与 deepseek reasoning_content 的既有处理一致）
            self._session.append(ASSISTANT_CHUNK, {"kind": "thinking", "delta": chunk.delta})
        elif kind == CHUNK_TOOL_CALLS:
            for fragment in chunk.tool_calls:
                self._merge_tool_call(fragment)
        elif kind == CHUNK_USAGE:
            self._usage = chunk.usage
        elif kind == CHUNK_FINISH and chunk.finish_reason is not None:
            self._finish_reason = chunk.finish_reason   # 最后一个携带它的分片为准

    def message(self) -> dict:
        """已产出到现在的 message —— 正常结束是完整结果，中断时就是「半截正文」。

        浅拷贝：字段是只读消费（validate_response / _append_assistant / _settle_interrupted
        都只读），复制一层足够把累积器内部状态挡在外面。
        """
        return dict(self._message)

    @property
    def finish_reason(self) -> str | None:
        return self._finish_reason

    @property
    def usage(self) -> TokenUsage | None:
        return self._usage

    def _merge_tool_call(self, fragment: dict) -> None:
        """按 index 拼一个 tool_call 片段（``{"index", "id", "name", "arguments"}``）。

        只有非空片段才覆盖已有值：先到的 id 不能被后面的空串抹掉（wire 上 id/name 只在
        第一帧出现，arguments 逐帧追加）。
        """
        index = fragment.get("index", 0)
        calls = self._message.setdefault("tool_calls", [])
        while len(calls) <= index:
            calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
        slot = calls[index]
        if fragment.get("id"):
            slot["id"] = fragment["id"]
        if fragment.get("name"):
            slot["function"]["name"] = fragment["name"]
        if fragment.get("arguments"):
            slot["function"]["arguments"] += fragment["arguments"]
