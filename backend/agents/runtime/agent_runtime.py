"""AgentRuntime — 统一 Agent 运行时：配置 + ToolRegistry + LLM 参数 + 执行调度

职责：加载 agent definition / config，获取 ToolRegistry 与 tool schemas，准备 LLM 参数，
创建并驱动 AgentLoop，返回最终结果。Agent 执行循环（LLM → Tool → LLM）在 AgentLoop 中。
"""
import json
import logging
import time

import aiohttp

from backend.agents.base import BaseAgent, AgentReply
from backend.agents.config_service import get_agent_definition, get_agent_config
from backend.agents.runtime.agent_loop import AgentLoop
from backend.agents.runtime.events import EventSink
from backend.agents.runtime.session import (
    Session,
    get_session_persistence,
    get_session_store,
)
from backend.core.http import get_http_session
from backend.mcp_service.registry import get_registry

logger = logging.getLogger(__name__)


def _flush_session_events(persistence, session: Session) -> None:
    """持久化边界（尽力而为）：Session 事件 → append_events(pending) → flush(durable)。

    触发点 = turn 完成（AgentRuntime.reply 收尾 / 新会话 seed 固化）。一次 flush 一个
    事务（全部成功或全部失败）；失败仅记 error 不打断对话 —— 答案已生成，不让持久化
    故障影响用户体验；pending 保留在 Persistence 内，下次 flush 幂等重放
    （ON CONFLICT (session_id, seq) DO NOTHING，不产生重复行）。
    """
    try:
        persistence.append_events(session.header.id, session.events)
        persistence.flush(session.header.id)
    except Exception:
        logger.exception(
            f"Session [{session.header.id}] 持久化失败（本 turn 事件可能未落库，"
            f"后续 flush 将重试）"
        )


class AgentRuntime(BaseAgent):
    """通用 Agent 运行时 — 配置来自 agent_definitions + agent_configs 表"""

    def __init__(self, key: str, definition: dict | None = None):
        if definition is None:
            definition = get_agent_definition(key) or {}
        self.key = key
        self.name = definition.get("name", key)
        self.desc = definition.get("description", "")
        self.status = definition.get("status", "active")
        self._definition = definition

    def _cfg(self) -> dict:
        """合并 agent_definitions.config + agent_configs 覆盖"""
        base = self._definition.get("config", {}) if self._definition else {}
        overrides = get_agent_config(self.key) or {}
        return {**base, **overrides}

    async def reply(
        self, tenant_id: int, question: str,
        context: dict | None = None,
        on_event: EventSink | None = None,
        session_id: str | None = None,
    ) -> AgentReply:
        t0 = time.perf_counter()
        config = self._cfg()
        trace = {"steps": [], "total_ms": 0}

        try:
            registry = get_registry()
            tool_schemas = await registry.get_schemas_for(self.key)
        except RuntimeError:
            tool_schemas = []

        # Session 开关（默认开）：开 → 记录 Event Log 并派生 LLM 消息；关 → 保持原 messages 行为。
        # 多轮 Session 三段式取 Session（SessionStore 只查内存热区，冷恢复编排在本层）：
        #   A. session_id + SessionStore.get() 命中 → 直接继续（进程内热 Session，跨请求保持上下文）
        #   B. session_id + Store miss + Persistence 命中 → load Event Log → from_events() replay
        #      → put 回 Store（lazy restore：按需从 PostgreSQL 恢复，启动不预载全部历史）
        #   C. session_id 无（首轮）或完全不存在（进程与 DB 均无）→ create() 新建。
        #      C 的「未知 session_id 被静默续接」正是要避免的失忆场景：命中 C 时打 warning
        #      日志（可观察），并把新 session_id 随 reply/done 回传，客户端应更新本地 id。
        # init_messages（system prompt / 上游 context）只对「真正新建」注入为 seed 事件；
        # 恢复出的 Session 的 seed 已在 Event Log 里（Event Log 是唯一事实来源），不重复注入。
        # AgentRuntime 是无状态执行器：Session 生命周期归 SessionStore，Runtime 只取用不持有。
        # Persistence 是独立 capability（默认 Noop，main.py startup 装配 Postgres）：
        #   新建后先固化 header + seed（第 0 状态），turn 完成是 flush 持久化边界。
        persistence = get_session_persistence()
        session = None
        if bool(config.get("session_enabled", True)):
            store = get_session_store()
            if session_id:
                session = store.get(session_id)
                if session is None:
                    loaded = persistence.load(session_id)
                    if loaded is not None:
                        header, events = loaded
                        session = Session.from_events(header, events)
                        store.put(session)
                        logger.info(
                            f"AgentRuntime [{self.key}] 冷恢复 Session {session_id}: "
                            f"replay {len(events)} events（Event Log → from_events）"
                        )
                    else:
                        logger.warning(
                            f"AgentRuntime [{self.key}] session_id={session_id} 在 SessionStore "
                            f"与持久化存储中均不存在，将创建新 Session —— 旧上下文无法继续，"
                            f"客户端请改用本次返回的新 session_id"
                        )
            if session is None:
                init_messages = [{"role": "system", "content": config.get("system_prompt", "")}]
                if context:
                    context_str = json.dumps(context, ensure_ascii=False, indent=2)
                    init_messages.append({
                        "role": "system",
                        "content": f"【上游节点输出，供你参考】\n{context_str}",
                    })
                session = store.create(init_messages=init_messages)
                # 新会话先固化：header + seed 事件落库（幂等）。进程在 turn 中途崩溃时
                # DB 至少保有第 0 状态（system prompt / 上游 context），冷恢复不丢初始上下文。
                try:
                    persistence.create(session.header.id, session.header)
                except Exception:
                    logger.exception(f"AgentRuntime [{self.key}] Session header 注册失败: {session.header.id}")
                _flush_session_events(persistence, session)

        # 准备运行环境，创建并驱动 AgentLoop（执行循环在 Loop 内部；Session 由 Runtime 注入）
        loop = AgentLoop(
            key=self.key,
            config=config,
            llm_params=self._llm_params(config),
            tool_schemas=tool_schemas,
            tenant_id=tenant_id,
            trace=trace,
            on_event=on_event,
            session=session,
        )
        answer = await loop.run(question, context)

        # turn 完成 = 持久化边界：本 turn 的 Event Log（turn/start → user → steps → turn/end）
        # 经 append_events + flush 落库（一次 flush = 一个事务）。AgentLoop 不感知任何存储 ——
        # 事件由 Session 收全，此处一次性交给 Persistence。done 事件在 flush 之后发出，
        # 客户端收到 done ≈ 本 turn 事件已 durable（flush 失败仅记日志，语义见 _flush_session_events）。
        if session is not None:
            _flush_session_events(persistence, session)

        trace["total_ms"] = round((time.perf_counter() - t0) * 1000)
        tier = "llm" if any(s.get("type") == "llm" for s in trace.get("steps", [])) else "fallback"
        # session_id 返回给客户端：首轮为新建 Session 的 id，后续轮次客户端原样带回
        sid = session.header.id if session is not None else None
        if on_event:
            await on_event({"type": "done", "answer": answer, "tier": tier, "trace": trace, "session_id": sid})
        return AgentReply(answer=answer, tier=tier, trace=trace, session_id=sid)

    def _llm_params(self, config: dict) -> dict:
        """运行参数（LLM 调用 + 防失控限制），全部从 agent 配置读取，默认值=现行为（零破坏）"""
        return {
            "max_steps": int(config.get("max_steps", 5)),
            "max_tool_calls": int(config.get("max_tool_calls", 30)),
            "max_wall_time": float(config.get("max_wall_time", 300)),
            "max_tokens": int(config.get("max_tokens", 1024)),
            "max_tokens_plain": int(config.get("max_tokens_plain", 512)),
            "temperature": float(config.get("temperature", 0.3)),
        }

    async def _call_llm_plain(self, messages: list[dict], config: dict) -> tuple[str, bool]:
        """简单 LLM 调用（无 tools），FaqAgent RAG 用（非 Agent 循环，留在 Runtime）"""
        api_key = (config.get("api_key", "") or "").strip()
        base_url = (config.get("base_url", "") or "").strip()
        model = (config.get("model", "") or "").strip()
        if not api_key or not base_url or not model:
            return "", False
        params = self._llm_params(config)
        try:
            session = await get_http_session()
            async with session.post(
                f"{base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": model, "messages": messages, "temperature": params["temperature"], "max_tokens": params["max_tokens_plain"]},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                result = await resp.json()
                answer = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                return answer, bool(answer)
        except Exception as e:
            logger.warning(f"AgentRuntime [{self.key}] LLM plain 调用失败: {e}")
            return "", False
