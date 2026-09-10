"""ToolDescriptor — Registry 与 Runtime 之间的唯一契约

Registry 只产出描述（元数据），Runtime / Executor 只消费描述执行。
描述里带齐执行所需的一切（transport / server 配置 / timeout），
Executor 因此不必回查 Registry，也不必自己读 DB。

type 是「执行器选择键」（"mcp" | "sandbox"）：Runtime 按 type 分发，
Registry 无需改动；新增执行器只需产出新的 type 并在 ToolRuntime 注册。

执行模式（``execution_mode``）是**同批调用之间**的并发约束，与 type 正交：
  parallel  —— 可与其他 parallel 调用同时执行（默认）
  exclusive —— 必须独占：启动前须等在途调用排空，形成调度屏障
默认 parallel 的理由：绝大多数工具（检索 / 查询 / RAG）无共享副作用。有共享状态的
工具（沙箱 bash / python 共写同一个会话工作区）必须由装配方显式声明 exclusive ——
默认值偏向可用性，安全性靠声明，二者都由 Registry 的元数据扩展点收敛。
"""
from dataclasses import dataclass, field
from typing import Literal

#: 完整的执行模式词汇（封闭）
ToolExecutionMode = Literal["parallel", "exclusive"]

PARALLEL: str = "parallel"
EXCLUSIVE: str = "exclusive"

EXECUTION_MODES: frozenset[str] = frozenset({PARALLEL, EXCLUSIVE})


def validate_execution_mode(mode: str) -> str:
    """构造期校验执行模式（封闭词汇）。

    Raises:
        ValueError: 取值不在词汇内。
    """
    if mode not in EXECUTION_MODES:
        raise ValueError(f"非法执行模式: {mode!r}；可选 {sorted(EXECUTION_MODES)}")
    return mode


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
    execution_mode: str = PARALLEL  # 同批并发约束："parallel" | "exclusive"（见模块头）

    def __post_init__(self) -> None:
        # 执行模式影响调度安全性，拼错不能静默降级 —— 构造即校验
        validate_execution_mode(self.execution_mode)
