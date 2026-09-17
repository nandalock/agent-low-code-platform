"""AgentRuntime — 统一 Agent 运行时：配置 + SystemPrompt + LLM 参数 + 执行调度

职责：加载 agent definition / config，组装 system prompt 与工具集，准备 LLM 参数，
创建并驱动 AgentLoop，返回最终结果。Agent 执行循环（LLM → Tool → LLM）在 AgentLoop 中。

每轮的请求装配顺序：
  ① 取 Session（热命中 / 冷恢复 / 新建，见下三段式）
  ② SystemPrompt.assemble —— 一次求值出提示词（段 + 动态上下文）、可见工具与其指导
  ③ render 成 system prompt 文本；assembly.tools 转 OpenAI tools 数组
  ④ 交给 AgentLoop：它把 system 前置为 messages[0]，对话历史由 Session 派生
工具 schema 与工具使用指导由此同源产出（都来自同一次工具求值），不会各自漂移。
"""
import asyncio
import logging
import time
from collections.abc import Callable

import aiohttp

from backend.agents.base import BaseAgent, AgentReply
from backend.agents.config_service import get_agent_definition, get_agent_config
from backend.agents.runtime.agent_loop import DEFAULT_MAX_LLM_RETRIES, AgentLoop
from backend.agents.runtime.events import EventSink
from backend.agents.runtime.session import (
    Session,
    SessionEvent,
    interrupted_turn_closers,
    flush_session_events,
    get_session_persistence,
    get_session_store,
    get_session_title_service,
)
from backend.agents.runtime.session.projections import TraceProjection
from backend.agents.runtime.system_prompt import (
    AssembleContext,
    assemble,
    render_system_prompt,
)
from backend.core.http import get_http_session
from backend.tool_system.runtime.scheduler import DEFAULT_MAX_PARALLEL_TOOLS

logger = logging.getLogger(__name__)


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
        session_event_sink: Callable[[SessionEvent], None] | None = None,
        cancel: asyncio.Event | None = None,
    ) -> AgentReply:
        t0 = time.perf_counter()
        config = self._cfg()

        # Session 开关（默认开）：
        # 开 → Session 生命周期走 SessionStore + Persistence（见下三段式）；
        # 关 → 创建「临时 Session」（不注册 SessionStore / 不落库 / 不返回 session_id）：
        #      Session 仍是 AgentLoop 的唯一执行事实源与 LLM 消息派生源（AgentLoop 必填），
        #      TraceProjection 照常工作；临时事件随 Session 丢弃，无外部副作用。
        #
        # 多轮 Session 三段式取 Session（SessionStore 只查内存热区，冷恢复编排在本层）：
        #   A. session_id + SessionStore.get() 命中 → 直接继续（进程内热 Session，跨请求保持上下文）
        #   B. session_id + Store miss + Persistence 命中 → load Event Log → from_events() replay
        #      → put 回 Store（lazy restore：按需从 PostgreSQL 恢复，启动不预载全部历史）
        #   C. session_id 无（首轮）或完全不存在（进程与 DB 均无）→ create() 新建。
        #      C 的「未知 session_id 被静默续接」正是要避免的失忆场景：命中 C 时打 warning
        #      日志（可观察），并把新 session_id 随 reply/done 回传，客户端应更新本地 id。
        # AgentRuntime 是无状态执行器：Session 生命周期归 SessionStore，Runtime 只取用不持有。
        # Persistence 是独立 capability（默认 Noop，main.py startup 装配 Postgres）：
        #   新建后先固化 header（含 cwd），turn 完成是 flush 持久化边界。
        persistence = get_session_persistence()
        session_enabled = bool(config.get("session_enabled", True))
        session: Session | None = None
        if session_enabled:
            store = get_session_store()
            if session_id:
                session = store.get(session_id)
                if session is None:
                    loaded = persistence.load(session_id)
                    if loaded is not None:
                        header, events = loaded
                        session = Session.from_events(header, events)
                        # 崩溃修复：持久化只保证日志**物理**合法，语义闭合是这层的事 ——
                        # 上一轮若被中断（进程没了），补合成 tool/result、step/end、turn/end，
                        # 否则这轮派生的消息序非法（assistant.tool_calls 没有配对的 tool 消息）。
                        repaired = self._repair_interrupted_tail(session, session_id)
                        store.put(session)
                        repair_note = f"，补记 {repaired} 条合成事件（上次运行中断）" if repaired else ""
                        logger.info(
                            f"AgentRuntime [{self.key}] 冷恢复 Session {session_id}: "
                            f"replay {len(events)} events（Event Log → from_events）{repair_note}"
                        )
                    else:
                        logger.warning(
                            f"AgentRuntime [{self.key}] session_id={session_id} 在 SessionStore "
                            f"与持久化存储中均不存在，将创建新 Session —— 旧上下文无法继续，"
                            f"客户端请改用本次返回的新 session_id"
                        )
            if session is None:
                session = store.create()
                # 新会话先固化 header（含 cwd）：沙箱策略链与工作区读取都靠它解析，
                # 进程中途崩溃时会话仍可被找回。
                try:
                    persistence.create(session.header.id, session.header)
                except Exception:
                    logger.exception(f"AgentRuntime [{self.key}] Session header 注册失败: {session.header.id}")
        else:
            session = Session()

        # 组装 system prompt（每轮一次）：
        #   ① SystemPrompt.assemble 求值全部贡献（工具 schema + 使用指导、身份、角色、
        #      上游上下文），段在前、动态上下文在尾部 —— 尾部变化不断前部的前缀缓存
        #   ② render 严格插值成最终文本（未知/无值变量在此报错，不发坏提示词给模型）
        #   ③ assembly.tools 即本 agent 可见工具，无需再单独查 Registry
        assembly = await assemble(AssembleContext(
            tenant_id=tenant_id,
            agent_key=self.key,
            agent_name=self.name,
            agent_description=self.desc,
            config=config,
            question=question,
            session=session,
            upstream_context=context,
        ))
        system_prompt = render_system_prompt(assembly)
        tool_schemas = [t.to_openai() for t in assembly.tools]

        # 准备运行环境，创建并驱动 AgentLoop（执行循环在 Loop 内部；Session 与
        # 组装好的 system prompt 由 Runtime 注入）
        loop = AgentLoop(
            key=self.key,
            config=config,
            llm_params=self._llm_params(config),
            tool_schemas=tool_schemas,
            tenant_id=tenant_id,
            system_prompt=system_prompt,
            on_event=on_event,
            session=session,
        )

        # 派生消费者装配：TraceProjection（→ AgentReply.trace / done.trace）与
        # session_event_sink（→ UI projection）都是 Session listener，不产生事实。
        # Session.append 同步回调、无 await 间隙 → 按 seq 收到全部事件；
        # Session 被 SessionStore 跨轮复用 → listener 只在 loop.run 期间挂载，
        # finally 统一摘除，保证不跨轮泄漏（事件不会迟到下一轮的投影/快照）。
        projection = TraceProjection()
        session.add_listener(projection.handle)
        attached_sink = session_event_sink is not None
        if attached_sink:
            session.add_listener(session_event_sink)
        try:
            answer = await loop.run(question, context, cancel=cancel)
        finally:
            session.remove_listener(projection.handle)
            if attached_sink:
                session.remove_listener(session_event_sink)

            # 会话标题（会话级事实，不是「本 turn 的产出」）：turn 末**同步**补一条
            # fallback（首条合格人类消息的前导词）——纯函数，无 IO 无模型，不会拖慢
            # 收尾；已钉住（用户改过名）或已有标题时它是 no-op。见 session/title/service.py。
            # 放在 flush **之前**：这条事件就跟本 turn 的其它事件一起落库，不需要额外
            # 一次事务。LLM 版标题晚点自己补 flush（它刻意跑在主链路之外）。
            #
            # 这两步在 finally 里：**取消也是「一轮的结束」**——硬取消（task.cancel()）
            # 会让异常穿过下面的代码，若不在这里落库，被取消的 turn 就只活在内存里。
            # turn/end 已经写好（Loop 的 finally 保证），这里只负责让它 durable。
            if session_enabled:
                get_session_title_service().settle(session)

                # turn 完成 = 持久化边界（仅正式 Session）：本 turn 的 Event Log
                # （turn/start → user → steps → turn/end）经 append_events + flush 落库
                # （一次 flush = 一个事务）。AgentLoop 不感知任何存储 —— 事件由 Session 收全，
                # 此处一次性交给 Persistence。done 事件在 flush 之后发出，客户端收到 done ≈
                # 本 turn 事件已 durable（flush 失败仅记日志，语义见 flush_session_events）。
                flush_session_events(persistence, session)

        if session_enabled:
            # 排一次 LLM 标题升级：**不在本函数里 await**（schedule 不返回 awaitable），
            # 所以它绝不会推迟 done 事件或拖长本轮耗时；生成完自己写事件 + flush。
            # 只对首条消息、且标题还没被升级/钉住时排得上（见 schedule 的三条判据）。
            # 硬取消路径走不到这里（异常已经穿过去了）—— 正在被拆掉的这轮不再排新工作；
            # 协作取消（信号）是正常返回，照常排。
            get_session_title_service().schedule(session, config)

        # trace = TraceProjection.snapshot()：派生观测视图（steps/limits/usage/stop_reason
        # 全部来自 Event Log 投影），不是第二套事实存储。AgentLoop 只产生事件，不写 trace。
        trace = projection.snapshot()
        # total_ms：运行时观测指标（含 session 装配/持久化开销），在投影之外维护
        trace["total_ms"] = round((time.perf_counter() - t0) * 1000)
        tier = "llm" if any(s.get("type") == "llm" for s in trace.get("steps", [])) else "fallback"
        # usage 汇总命中率日志（纯观测，从投影结果读取，语义同旧 AgentLoop 轮末日志）
        usage = trace.get("usage")
        if usage is not None:
            ratio = usage.get("cache_hit_ratio")
            if ratio is None:
                logger.info(f"AgentLoop [{self.key}] usage 无 cache 字段（非 DeepSeek 网关?）: {usage}")
            elif ratio < 0.5:
                logger.warning(
                    f"AgentLoop [{self.key}] 上下文缓存命中率偏低: {usage}"
                    f" —— 检查前缀是否变化（system prompt / 工具集改动会从改动点起断缓存）"
                )
            else:
                logger.info(f"AgentLoop [{self.key}] usage: {usage}")
        # session_id 返回给客户端：首轮为新建 Session 的 id，后续轮次客户端原样带回；
        # 临时 Session（session_enabled=False）不返回 id。
        sid = session.header.id if session_enabled else None
        if on_event:
            await on_event({"type": "done", "answer": answer, "tier": tier, "trace": trace, "session_id": sid})
        return AgentReply(answer=answer, tier=tier, trace=trace, session_id=sid)

    def _repair_interrupted_tail(self, session: Session, session_id: str) -> int:
        """冷恢复的语义修复：补齐未闭合的尾巴，返回补记的事件数（对齐 DSH session/repair.ts）。

        为什么需要它：事件在 **turn 末**批量 flush，但 flush 不止一个触发点（后台标题生成、
        同会话的另一轮都可能在某一轮进行中把它的前缀落库）—— 此刻进程挂掉，库里就留下一个
        没有 turn/end 的 turn，或一个永远等不到 tool/result 的 tool_calls。重放这样的日志
        会派生非法消息序，trace 也收不了口。

        补记的事件**按中断点的时间戳**写回 Event Log（见 repair.py），下一次 flush 一起落库；
        修复失败不能拖垮冷恢复 —— 记 exception 后放行（那本就是个坏日志，比连会话都打不开强）。
        """
        try:
            closers = interrupted_turn_closers(session.events)
            if not closers:
                return 0
            session.append_recovered(closers)
            logger.warning(
                f"AgentRuntime [{self.key}] Session {session_id} 上次运行被中断，"
                f"补记 {len(closers)} 条合成事件（{closers[-1].data.get('stop_reason')}）"
            )
            return len(closers)
        except Exception:
            logger.exception(f"AgentRuntime [{self.key}] Session {session_id} 崩溃修复失败（按原样继续）")
            return 0

    def _llm_params(self, config: dict) -> dict:
        """运行参数（LLM 调用 + 防失控限制），全部从 agent 配置读取，默认值=现行为（零破坏）"""
        return {
            "max_steps": int(config.get("max_steps", 5)),
            "max_tool_calls": int(config.get("max_tool_calls", 30)),
            "max_wall_time": float(config.get("max_wall_time", 300)),
            # 同批 Tool 调用的并发上限（ToolScheduler 的滚动池大小）；
            # 1 = 退化为串行，与调度器引入前行为一致
            "max_parallel_tools": int(config.get("max_parallel_tools", DEFAULT_MAX_PARALLEL_TOOLS)),
            "max_tokens": int(config.get("max_tokens", 1024)),
            "max_tokens_plain": int(config.get("max_tokens_plain", 512)),
            "temperature": float(config.get("temperature", 0.3)),
            # 单次 step 的 LLM 调用重试次数上限（429/5xx/超时/非法响应；4xx 不重试）。
            # 重试在 step 内进行，不占 max_steps 轮数，不落 surface 事实。
            "max_llm_retries": int(config.get("max_llm_retries", DEFAULT_MAX_LLM_RETRIES)),
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
