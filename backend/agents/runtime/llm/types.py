"""LLM 边界的 canonical 词汇：请求 / 响应 / 流分片 / token 统计

对齐 A 的 ``packages/llm/llm/src/types.ts``。核心一条：**循环发出的请求里没有凭据、
没有端点、没有 URL、没有 HTTP**。凭据、wire 字段名、SSE 组帧、状态码→失败码的翻译，
全部归适配器（见 openai_chat.py）。

与 A 的差异只有一处，是刻意的：A 的 ``StreamChunk`` 带块词汇（block-start / block-end，
它要表达图文混排、reasoning 块、tool-result 块），B 的 assistant message 就是 OpenAI
形状的 ``{content, tool_calls}``，所以流分片只到「某个字段的增量」这一档 —— 少一档，
不假装有块结构。
"""
from dataclasses import dataclass

# ── 流分片的种类（StreamChunk.kind）──

CHUNK_TEXT = "text"              # 正文增量
CHUNK_THINKING = "thinking"      # 推理过程增量（deepseek 的 reasoning_content）
CHUNK_TOOL_CALLS = "tool_calls"  # 工具调用片段（流式下按 index 分段到达）
CHUNK_USAGE = "usage"            # token 统计（多数 provider 在结尾单独发一帧）
CHUNK_FINISH = "finish"          # 结束原因（携带它的最后一个分片为准）


@dataclass(frozen=True)
class TokenUsage:
    """一次调用的 token 统计（**canonical 字段名**，provider 差异由适配器翻译）。

    缓存字段刻意与 ``prompt_tokens`` 分开存（对齐 A 的 TokenUsage）：各家对
    ``prompt_tokens`` 的口径不一致（DeepSeek 把命中缓存的也算进去），分开存才能让
    「命中率」这类派生指标只算一次、算在一个地方（trace 投影读这里）。
    """
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cache_hit_tokens: int | None = None
    cache_miss_tokens: int | None = None

    def fact(self, step: int) -> dict:
        """落 llm/usage 事件的 data（log-only 观测事实）—— 事件形状只在这里定义。"""
        return {
            "step": step,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_miss_tokens": self.cache_miss_tokens,
        }


@dataclass(frozen=True)
class LlmRequest:
    """一次模型请求（canonical）—— 循环能看见的全部输入。

    ``stream`` 是**规范选项**，不是临时开关：适配器据此决定 wire 形态（今天 = SSE 流 /
    一次性 JSON）。将来想改成「永远流式、由循环决定要不要广播增量」，只动循环侧
    （不再看 on_event），适配器一行不改。
    """
    model: str
    messages: list[dict]
    tools: list[dict] | None = None
    temperature: float = 0.0
    max_tokens: int = 1024
    stream: bool = False
    #: 本次请求允许的最长时间（秒），None = 适配器自己的上限。循环用它表达
    #: max_wall_time 剩余的那点预算 —— 与 A 用 signal 表达的是同一件事。
    timeout: float | None = None


@dataclass(frozen=True)
class LlmResponse:
    """一次**非流式**请求的结果。

    流式请求不产生它：分片由循环侧的累积器（loop/assistant_stream.py）装配成
    同样形状的 message，两条路的收口因此完全一致。
    """
    message: dict                        # OpenAI 形状：{"role": "assistant", "content", "tool_calls"}
    finish_reason: str | None = None
    usage: TokenUsage | None = None


@dataclass(frozen=True)
class StreamChunk:
    """一个流分片。

    适配器**只产出它**：不写 Session 事件、不认识取消、不认识 RunCancelled
    —— 落库与中断语义都是循环的事（见 loop/assistant_stream.py）。

    ``tool_calls`` 是**已拍平**的片段（``{"index", "id", "name", "arguments"}``，
    对齐 A 的 tool-call-delta）：wire 的 ``function.name`` 那层嵌套在适配器里剥掉，
    累积器只按 index 拼接。
    """
    kind: str
    delta: str = ""                       # kind=text / thinking
    tool_calls: tuple[dict, ...] = ()     # kind=tool_calls
    usage: TokenUsage | None = None       # kind=usage
    finish_reason: str | None = None      # kind=finish
