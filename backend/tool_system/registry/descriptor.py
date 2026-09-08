"""ToolDescriptor — Registry 与 Runtime 之间的唯一契约

Registry 只产出描述（元数据），Runtime / Executor 只消费描述执行。
描述里带齐执行所需的一切（transport / server 配置 / timeout），
Executor 因此不必回查 Registry，也不必自己读 DB。

type 是「执行器选择键」（当前只有 "mcp"）：后续接入 Native / Sandbox
执行器时只需产出新的 type，Runtime 按 type 分发，Registry 无需改动。
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToolDescriptor:
    """一个可执行 Tool 的完整描述（不可变）"""

    name: str                      # 工具名（MCP server 侧原名，调用时原样透传）
    type: str                      # 执行器类型：当前仅 "mcp"
    transport: str                 # "http" | "stdio"
    server_id: int                 # 所属 MCP server id（日志 / 可观测用）
    schema: dict                   # MCP 原始 tool schema（含 inputSchema）
    server: dict = field(default_factory=dict)   # server 配置：url / command / args / env
    timeout: float | None = None   # 声明式超时（秒）；None → Executor 用 DEFAULT_TOOL_TIMEOUT
