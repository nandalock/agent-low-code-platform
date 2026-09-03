"""RequestContext — 请求/运行上下文（非 Session）

只携带一次外部驱动运行的请求边界信息。不是 Session，不持有
LLM / AgentRuntime / AgentLoop / Memory / Tool / 数据库连接。

生命周期：一次 Gateway 运行（gateway.chat 一次调用）即一个 RequestContext。

字段职责：
  run_id          本次运行唯一 id（供后续 run ownership / cancel / 追踪使用；当前仅标识）
  tenant_id       租户（必填，API 来自 X-Tenant-ID，Workflow 来自 workflows 行）
  agent_key       目标 agent（必填，经注册表解析）
  channel         驱动通道：http / workflow / xianyu / ...
  session_id      AgentRuntime 多轮 Session id（首轮 None → runtime 新建并回传）
  conversation_id HTTP 通道的会话记录 id（会话历史的持久化属 services/chat，
                  conversation↔session 的持久映射属 Gateway 后续阶段，本阶段不新增）
  visitor_id      终端用户标识（Workflow 中来自 payload.user_id）
  context         上游上下文（workflow 上游节点输出等），runtime 首轮注入为 seed 事件
"""
import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RequestContext:
    tenant_id: int
    agent_key: str
    channel: str = "http"
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    session_id: str | None = None
    conversation_id: int | None = None
    visitor_id: str | None = None
    context: dict | None = None
