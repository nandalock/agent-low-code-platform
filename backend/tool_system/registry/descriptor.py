"""ToolDescriptor — Registry 与 Runtime 之间的唯一契约

Registry 只产出描述（元数据），Runtime / Executor 只消费描述执行。
描述里带齐执行所需的一切（transport / server 配置 / timeout），
Executor 因此不必回查 Registry，也不必自己读 DB。

type 是「执行器选择键」（"mcp" | "sandbox"）：Runtime 按 type 分发，
Registry 无需改动；新增执行器只需产出新的 type 并在 ToolRuntime 注册。
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SandboxToolConfig:
    """沙箱工具的配置（``type == "sandbox"`` 时必填）。

    镜像属于**工具配置**而非策略：策略只表达文件效果（见 sandbox/vocabulary.py），
    「用哪个执行环境」是工具自己的事。
    """

    runtime: str                   # "shell" | "python" —— 决定 argv 形状
    image: str                     # 沙箱镜像
    timeout_s: float = 30.0        # 单次执行超时（秒）
    memory: str = "1g"
    cpus: float = 1.0
    pids_limit: int = 256


@dataclass(frozen=True)
class ToolDescriptor:
    """一个可执行 Tool 的完整描述（不可变）"""

    name: str                      # 工具名（MCP server 侧原名 / 原生工具名，调用时原样透传）
    type: str                      # 执行器类型："mcp" | "sandbox"
    transport: str                 # "http" | "stdio"（native/sandbox 工具为 ""）
    server_id: int                 # 所属 MCP server id（日志 / 可观测用；原生工具为 0）
    schema: dict                   # 原始 tool schema（含 name / description / inputSchema）
    server: dict = field(default_factory=dict)   # server 配置：url / command / args / env
    timeout: float | None = None   # 声明式超时（秒）；None → Executor 用 DEFAULT_TOOL_TIMEOUT
    sandbox: SandboxToolConfig | None = None     # type == "sandbox" 时必填
