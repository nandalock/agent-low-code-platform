"""SessionToolRecorder — ToolScheduler 的调用事实出口（写 Session Event Log）

ToolScheduler（tool_system 层）不依赖 agents 层：它只认
``tool_system.runtime.scheduler.ToolCallRecorder`` 这个 Protocol，
由本类在装配侧实现，把「一次调用发起 / 一次调用结果」落成 Event Log 事实：

    record_call   → session.append(tool/call)
    record_result → session.append(tool/result)

两个方法都是**同步**的：``Session.append()`` 无 await 点，这既保证 listener
（TraceProjection / UI projection）按 seq 收事件，也让调度器能在取消恢复路径
（finally，不能 await）里安全地写合成结果。

``tool/result`` 里除 ``content``（JSON 全文，供模型阅读）外，另带一份**结构化
沙箱事实**：投影端（轨迹 / UI）直接读字段，不必解析 content。
"""
import json

from backend.agents.runtime.session import TOOL_CALL, TOOL_RESULT, Session
from backend.tool_system.runtime.scheduler import PlannedCall


class SessionToolRecorder:
    """ToolCallRecorder 的 Session 实现：调用事实 → Event Log"""

    def __init__(self, session: Session):
        self._session = session

    def record_call(self, call: PlannedCall) -> None:
        self._session.append(TOOL_CALL, {
            "tool": call.tool_name,
            "args": call.args,
            "tool_call_id": call.tool_call_id,
        })

    def record_result(self, call: PlannedCall, result: dict) -> None:
        # tool result 原样 JSON 序列化入事件（Log 保全文，投影端按需还原）
        data: dict = {
            "tool": call.tool_name,
            "tool_call_id": call.tool_call_id,
            "content": json.dumps(result, ensure_ascii=False),
        }
        sandbox = result.get("sandbox") if isinstance(result, dict) else None
        if isinstance(sandbox, dict):
            data["sandbox"] = sandbox
        self._session.append(TOOL_RESULT, data)
