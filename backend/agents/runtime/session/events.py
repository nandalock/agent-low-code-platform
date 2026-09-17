"""Session 事件定义（DSH 思想：Session Event Log 是执行事实来源）

事件类型与 LLM messages 的映射由 Surface + derive_event_message 负责：
  - surface 事件（进入 LLM Context）: user/message, assistant/message, tool/result
    （session/seed 亦在集合内，但已退役、无写入方——见 SEED 注释）
  - 过程事件（仅 Event Log，不进入 LLM）: turn/start, step/start, assistant/chunk,
    tool/call, tool/progress, step/end, turn/end, llm/usage, llm/error, session/title

system prompt 不进 Event Log：它由 SystemPrompt 每轮组装，在 AgentLoop 派生 LLM
消息时前置为 messages[0]。session_headers.seed_length 是 seed 机制的历史字段，
新会话写 0 / NULL，无读取方。

按规格暂不实现：steering/message、todo/write、request/header、compaction、delegation。
"""
import uuid
from dataclasses import dataclass

# ── 事件类型常量 ──
SEED = "session/seed"                    # 初始 LLM 消息（data: role + content）。
                                         # **已退役**：system prompt 改由 SystemPrompt
                                         # 每轮组装、AgentLoop 前置注入，主链路无写入方。
                                         # 类型保留供内存构造（测试/兼容），冷恢复不回放
TURN_START = "turn/start"
TURN_END = "turn/end"
STEP_START = "step/start"
STEP_END = "step/end"
USER_MESSAGE = "user/message"
ASSISTANT_CHUNK = "assistant/chunk"      # 流式过程事件（data: kind=thinking|text + delta），不直接进 LLM
ASSISTANT_MESSAGE = "assistant/message"  # 完整 assistant message（含 tool_calls）
TOOL_CALL = "tool/call"
TOOL_RESULT = "tool/result"
TOOL_PROGRESS = "tool/progress"  # 长耗时 Tool 的阶段进度（data: tool + tool_call_id + stage + seconds），
                                 # 过程事件（log-only），不进 surface / 不参与 derive_messages
LLM_USAGE = "llm/usage"   # 每次 LLM 调用的 usage（provider 返回；data: step + prompt/cache token 统计），
                          # log-only 观测事件（Step 1），不进 surface / 不参与 derive_messages
LLM_ERROR = "llm/error"   # 一次**失败**的 LLM 尝试（data: step + attempt + code + status +
                          # message + retry_in）。对齐 A 的 assistant/attempt：重试是事实，
                          # 该能在 Event Log 里回放（问了几次、为什么重试、等了多久），
                          # 而不是只存在于应用日志文本里。log-only：失败的尝试没有产出任何
                          # LLM 可见内容，不进 surface / 不参与 derive_messages
SANDBOX_MODE = "sandbox/mode"  # 会话级沙箱模式覆盖（data: mode），log-only 配置事件：
                               # 「日志即存储」——SandboxModeProjection 折叠出当前有效覆盖，
                               # 生效值 = 覆盖 ?? 部署默认（见 tool_system/sandbox/policy.py）
APPROVAL_REQUEST = "approval/request"  # 升权申请进入人工裁决（data: approval_id + tool +
                                       # tool_call_id + from + to + justification）
                                       # log-only **记录**事件——它让「正在等谁批」这个
                                       # 状态可查（UI 显示待批准卡片、冷恢复后清陈旧卡片）。
                                       # 与 sandbox/mode 的区别同 sandbox/escalation：
                                       # 它是记录，不参与策略解析。
TITLE = "session/title"                   # 会话标题（data: title + message_seqs + source）。
                                         # **追加型日志事件，不是可变字段**——标题因此和
                                         # 其它事实一样可重放：fold 取**最后一条**即当前标题
                                         # （见 title_projection.fold_session_title）。
                                         # **log-only**：不在 SURFACE_EVENT_TYPES 里，
                                         # 永远不进模型输入，零 token、不影响前缀缓存。
                                         # 三个来源（新的赢）：fallback（首条消息前导词）→
                                         # provider（LLM 生成，至多一次）→ user（用户改名，
                                         # **一旦改名即钉住**，后续不再自动改）。
                                         # 写入方只有 title/service.py 一处。
SANDBOX_ESCALATION = "sandbox/escalation"  # 沙箱升权的批准/拒绝事实（data: from +
                                           # requested + to + granted + reason + justification）
                                           # log-only **记录**事件——刻意不被任何投影折叠成配置：
                                           # 一次性授权只对那次调用有效，若它参与策略解析，
                                           # 一次性就变成了持久放权。（与 sandbox/mode 的区别）

# 进入 LLM Context 的 surface 事件（其余事件只存在于 Event Log）
SURFACE_EVENT_TYPES = frozenset({SEED, USER_MESSAGE, ASSISTANT_MESSAGE, TOOL_RESULT})


@dataclass
class SessionEvent:
    """一条 Session 执行事实。data 结构由事件类型约定（见各 append 调用点）。"""
    type: str
    seq: int                                  # 全局递增序号（排序依据）
    time: float                               # 事件发生时间（wall clock）
    data: dict
    source_event_seqs: list[int] | None = None  # 派生来源（保留扩展位，暂无派生事件）


@dataclass
class SessionHeader:
    """Session 元信息。id / created_at 由 Session 创建时自动生成。"""
    version: int
    id: str
    created_at: float
    cwd: str | None = None
    parent_session: str | None = None
    seed_length: int | None = None
    delegation_depth: int | None = None


def new_session_id() -> str:
    return uuid.uuid4().hex
