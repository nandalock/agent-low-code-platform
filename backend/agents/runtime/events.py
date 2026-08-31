"""流式事件定义（deepseek harness 风格）

事件类型:
  thinking       → LLM 推理过程增量（reasoning_content）
  text           → LLM 文本增量（思考间隙的输出）
  step           → agent 循环步进
  tool_call      → 工具调用发起
  tool_progress  → 工具执行进度（领域注册的 progress query 轮询）
  tool_result    → 工具调用结果
  answer         → 最终回答增量
  done           → 结束（含最终 answer / tier）
"""
from typing import Awaitable, Callable

# 事件回调签名：上层 UI / SSE 订阅 AgentLoop 事件
EventSink = Callable[[dict], Awaitable[None]]

# 工具调用轮询总超时（秒）：task 被挂死时的最后兜底（client.call 内部另有 300s 超时）
TOOL_POLL_TIMEOUT = 600
