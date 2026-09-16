"""派生视图 —— 消费 Event Log，不产生事实

三个投影并列、互不依赖，共同的性质是**只读**：把已经发生的 SessionEvent 折叠成
某个用途的视图，没有一条会 append 事件。Event Log 是唯一事实源，这些是它的派生
物 —— 删掉任何一个，执行链照跑，只是少了一双看它的眼睛。

  sandbox.py      sandbox/mode    → 会话级沙箱模式覆盖（配置投影）
  trace.py        turn/step/usage → 运行统计（AgentReply.trace / done.trace）
  trajectory.py   全量事件        → UI 可直接渲染的节点流（SSE 轨迹）

**会话标题刻意不在这里**：它虽然是 fold ``session/title`` 得来的，但同时自带一个
**会写事件**的服务（fallback 补位 / LLM 升级 / 改名钉住），是「标题」这个完整特性
的一半。把它的 fold 放这儿、写入放过那儿，只会让一个契约散在两个文件夹里；它
因此和净化、服务一起待在 ``../title/``。

命名：文件夹名已经说了「这是投影」，文件名就不再重复后缀（``sandbox.py`` 而非
``sandbox_projection.py``）。对比 ``../title/projection.py`` —— 那里的后缀是有
信息量的，因为要和同目录的 ``normalize.py`` / ``service.py`` 区分开。
"""
from backend.agents.runtime.session.projections.sandbox import (
    SandboxModeProjection,
    project_sandbox_mode,
)
from backend.agents.runtime.session.projections.trace import TraceProjection
from backend.agents.runtime.session.projections.trajectory import (
    TrajectoryProjection,
    project_trajectory,
)

__all__ = [
    "SandboxModeProjection",
    "project_sandbox_mode",
    "TraceProjection",
    "TrajectoryProjection",
    "project_trajectory",
]
