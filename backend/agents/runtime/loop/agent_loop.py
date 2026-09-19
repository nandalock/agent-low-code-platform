"""AgentLoop — 事件驱动的 LLM → Tool → LLM 循环（对齐 A 的 ReactLoopAgent）。

输入不是一次性的 ``run(question)``：Loop 持有待投递队列（inbox）与一个 **driver**，
输入经三个动作投递 ——

    followup(text) → next-turn  本轮结束后作为**新的一轮**开始
    steer(text)    → next-step  当前轮的下一步并入上下文（唤醒 driver）
    inject(text)   → next-step  系统侧静默投递（不唤醒）

投递是同步的（不等待），等待是显式的（``when_idle()``），答案从事件里取
（``answer_text()``）；``cancel(cause)`` 清队列并置本轮 abort 信号。
状态机：``_Idle``（没有 driver）↔ ``_Running``（一个 driver 连续开轮，每轮一个 abort）。

没有 ``run(question) -> str`` 这种一次性入口：调用方自己投递、自己等、自己取答案
（三段式，见 AgentRuntime.reply 的用法）。

职责：Loop 只做「计划」与事实落库（认领输入、派生 messages、请求 LLM、产出 PlannedCall）；
执行顺序 / 并发 / tool/call·tool/result 成对落库归 ToolScheduler，运行环境与 LLM
参数由 AgentRuntime 注入。Loop 不认识 MCP，也不认识 provider 的失败码与退避曲线。

LLM 出口是 ``LlmClient``（llm/client.py）：本模块**不认识 api_key / base_url / HTTP /
SSE**，只发 canonical 请求（LlmRequest）、只收 canonical 结果（LlmResponse / StreamChunk）。
流式的增量落库与「半截正文」语义留在本层（loop/assistant_stream.py）—— 适配器只产出
分片，不认识 Session，也不认识取消。

事实源：Session Event Log 是唯一真相 —— Loop 只 append，消息由 derive_messages() 派生。

防失控：max_steps（每轮步数）/ max_tool_calls（轮级调用数）/ max_wall_time（每轮时长）/
tool timeout（Executor）/ repeat tool（相同 tool+参数连续 REPEAT_TOOL_THRESHOLD 次）。

失败与取消：step 内有限重试，失败尝试不落 surface 事实（只记 log-only 的 llm/error）；
截断（finish_reason=length）是正常结束、不重试，被截断的 tool_calls 一律丢弃；协作
取消收口为 cancelled 后正常返回，硬取消（CancelledError）记完事实后向上传播。
step/end 与 turn/end 由 finally 闭合，任何退出路径都不留半途 turn。
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass

from backend.agents.runtime.llm import (
    DEFAULT_MAX_LLM_RETRIES,
    LlmClient,
    LlmError,
    LlmRequest,
    LlmResponse,
    LoopHooks,
    RequestErrorHook,
    RetryDecision,
    TokenUsage,
    make_retry_policy,
    validate_response,
)
from backend.agents.runtime.loop.assistant_stream import AssistantStream
from backend.agents.runtime.loop.events import EventSink
from backend.agents.runtime.loop.inbox import NEXT_STEP, NEXT_TURN, ReactLoopInbox
from backend.agents.runtime.session import (
    ASSISTANT_MESSAGE,
    LLM_ERROR,
    LLM_USAGE,
    STEP_END,
    STEP_START,
    TURN_END,
    TURN_START,
    USER_MESSAGE,
    Session,
    SessionToolEventSink,
    SessionToolRecorder,
)
from backend.tool_system.context import ToolContext
from backend.tool_system.runtime.scheduler import (
    DEFAULT_MAX_PARALLEL_TOOLS,
    DISPATCH,
    SKIP,
    SKIP_MAX_TOOL_CALLS,
    SKIP_REPEAT_TOOL,
    PlannedCall,
    ToolScheduler,
)

logger = logging.getLogger(__name__)

# 相同 tool + 相同参数连续调用达到该次数后停止（前两次重复允许，第三次触发）
REPEAT_TOOL_THRESHOLD = 3

#: 取消时无可保留正文的兜底回复（与前端 WorkspaceChat 的本地兜底文案一致）。
CANCELLED_REPLY = "已停止生成。"


class RunCancelled(Exception):
    """协作取消：cancel 信号触发，本轮以 stop_reason=cancelled 收口。

    与 asyncio.CancelledError（硬取消）分开：前者本轮正常收口（持久化与 done 事件
    照常），后者必须继续向上传播。``partial`` 是信号到达时的 assistant message，正文
    保留为事实，tool_calls 一律丢弃（参数可能不完整，落库即 dangling）。
    """

    def __init__(self, partial: dict | None = None):
        super().__init__("本轮已被取消")
        self.partial = partial or {}


def _safe_name(tool_name: str) -> str:
    return tool_name.replace("/", "_").replace(".", "_")


@dataclass
class _RunState:
    """一轮的装配与防失控状态：_turn() 装配一次，_step / _plan_tool_calls 只认这一个对象
    （计数、guard 标记、终止原因、本轮 answer 都在里面）。"""
    # 装配（_turn() 组装）
    model: str
    max_steps: int
    max_tool_calls: int
    max_wall_time: float
    max_tokens: int
    deadline: float                  # 绝对截止时刻：LLM 超时与重试退避都不得突破
    tools: list[dict]
    name_map: dict[str, str]
    cancel: asyncio.Event            # 本轮的协作取消信号（= phase.abort，每轮各一个）
    # 执行中可变（_step 更新）
    final_answer: str = ""
    stop_reason: str | None = None    # 各决策点写入，收口时经 _resolve_stop_reason 解析
    tool_call_count: int = 0          # max_tool_calls 计数
    last_tool_signature: tuple | None = None  # repeat tool：上次调用的 tool+参数签名
    repeat_tool_count: int = 0        # 连续重复次数
    tool_calls_exhausted: bool = False  # guard 触发标记
    repeat_tool_stopped: bool = False
    last_tool_timeout_msg: str = ""   # 最后一次 tool 超时信息（供终止兜底）


@dataclass(frozen=True)
class _StepDecision:
    """一步的入参裁决（对齐 A 的 PreStepDecision）：本步并入了哪些输入。

    ``messages`` 由 _turn 在**认领时**落成 user/message 事实，_step 只按它组装请求 ——
    输入没有第二份状态（不再有「本轮的问句」这种东西），模型看到的永远是 Event Log
    派生出来的消息。将来的 ``pre_step`` 拦截点（§6.2）就在这一步改写它。
    """
    messages: list[str]


@dataclass(frozen=True)
class _StepOutcome:
    """一步的收口（stop_reason 词表与 turn/end 一致）；它的存在表示本轮到此结束。

    取消不在这里表达 —— RunCancelled / CancelledError 直接穿透到 _turn 收口。
    """
    stop_reason: str
    final_answer: str = ""


@dataclass
class _Idle:
    """空闲：没有 driver 在跑（last_turn = 已完成/已认领的轮次号，接续开轮用）。"""
    last_turn: int = 0


@dataclass
class _Running:
    """运行中：一个 driver 正在连开轮次（对齐 A 的 running phase）。

    ``abort`` 是**本轮**的协作取消信号 —— 每开一轮换一个：上一轮被取消，不代表新的一轮
    也取消（闩住的唤醒随之作废，活的 driver 自己会认领队列）。``step`` 是本轮已开的步数。
    """
    abort: asyncio.Event
    turn: int = 0
    step: int = 0
    wake_requested: bool = False


def _resolve_stop_reason(state: _RunState) -> str:
    """turn/end 的 stop_reason 收口。

    显式写入 state.stop_reason 的直接生效；未显式收口时（LLM 失败 / 意外异常 / 正常
    收敛），更早的 tool 超时事实压过 completed / error —— 超时对用户是「为什么停」
    更有信息量的答案。
    """
    if state.stop_reason in (None, "completed", "error") and state.last_tool_timeout_msg:
        return "tool_timeout"
    if state.stop_reason is not None:
        return state.stop_reason
    return "completed" if state.final_answer else "error"


class AgentLoop:
    """Agent 执行循环：LLM → Tool 交替直到收敛（受 max_steps 等 guard 约束）"""

    def __init__(
        self,
        *,
        key: str,
        llm: LlmClient,
        llm_params: dict,
        model: str = "",
        tool_schemas: list[dict],
        tenant_id: int,
        system_prompt: str = "",
        on_event: EventSink | None = None,
        session: Session,
        hooks: LoopHooks | None = None,
        fallback_reply: str = "",
    ):
        #: ``key`` 是 agent 的**业务标识**（paper_agent 这种），不是凭据 —— 它只进日志、
        #: turn/start 事件与 ToolContext（对齐 A 的 ``agent.ts`` 用 ``this.id`` 做同一件事）。
        self.key = key
        self.llm_params = llm_params
        self.tool_schemas = tool_schemas
        self.tenant_id = tenant_id
        #: 终止兜底文案（配置 fallback_reply）—— 循环**不持有整个 config**：凭据在客户端、
        #: 业务语义在各自层，它只用得到这一个字符串。
        self.fallback_reply = fallback_reply or ""
        # LLM 出口：本层只知道「发 canonical 请求、收 canonical 结果」；端点与凭据在它后面
        # （llm/openai_chat.py）。model 是**请求内容**（对齐 A 的 GenerateOptions.model），
        # 不是凭据 —— 由装配层随配置给进来。
        self._llm = llm
        self._model = (model or "").strip()
        # system prompt 由 Runtime 每轮组装，此处只在派生消息时前置；空串表示本轮无提示词。
        self.system_prompt = system_prompt
        self.on_event = on_event
        # 拦截通道 vs 观察通道：on_event 是只读广播，坏了不影响执行；hooks 改变执行走向，
        # 只有装配方显式传入才生效。不传 request_error 即内置默认策略 —— 「不传钩子 = 现行为」。
        self._default_request_error = make_retry_policy(
            llm_params.get("max_llm_retries", DEFAULT_MAX_LLM_RETRIES),
        )
        self._request_error: RequestErrorHook | None = None if hooks is None else hooks.request_error
        # Session 必填：Event Log 是唯一事实源，Loop 只 append。
        self.session = session
        # Tool 调度器：执行的顺序 / 并发 / 成对落库 / 有序提交归它，Loop 只产出调用计划
        self._scheduler = ToolScheduler(
            recorder=SessionToolRecorder(session),
            context_factory=self._tool_context,
            max_parallel_tools=llm_params.get("max_parallel_tools", DEFAULT_MAX_PARALLEL_TOOLS),
        )
        # 待投递输入（followup / steer / inject 的落点）与 driver 的相位机。
        # 轮次号从 Event Log 续（同会话多轮 / 冷恢复都不重号）：本 Loop 实例不持有轮次事实。
        self._inbox = ReactLoopInbox()
        self._phase: _Idle | _Running = _Idle(
            last_turn=sum(1 for ev in session.events if ev.type == TURN_START),
        )
        self._activity: asyncio.Task | None = None      # 当前 driver（when_idle 等它）
        self._last_state: _RunState | None = None       # 最后一轮的装配（兜底文案要读它）
        # 答案边界：本次 driver 活动从哪条 seq 起算 —— 没界定的活动是「上一次运行」的残留
        # （多轮会话里，被取消且没有半截正文的一轮会读到上一次问句的回答）
        self._answer_floor = session.seq

    # ── 投递：三个动作（同步、不被等待）──

    def followup(self, text: str) -> None:
        """投递到 next-turn：本轮结束后作为**新的一轮**开始（空闲时立即开轮）。"""
        self._send(text, NEXT_TURN, wake=True)

    def steer(self, text: str) -> None:
        """投递到 next-step：当前轮余下的**下一步**就并入上下文（空闲时立即开轮）。"""
        self._send(text, NEXT_STEP, wake=True)

    def inject(self, text: str) -> None:
        """投递到 next-step 但**不唤醒**：只在已有轮次运行时才可能被认领（系统侧静默投递）。"""
        self._send(text, NEXT_STEP, wake=False)

    def _send(self, text: str, target: str, *, wake: bool, abort: asyncio.Event | None = None) -> None:
        """投递一条输入。``abort`` 只作用于这次唤醒开出来的那一轮（见 run() 的兼容入参）。"""
        phase = self._phase
        if wake and isinstance(phase, _Running) and phase.abort.is_set():
            # 已被取消的活动兑现不了这次唤醒：输入落到下一轮，并在本轮收敛后重放唤醒（照搬 A）
            target = NEXT_TURN
            phase.wake_requested = True
        self._inbox.append(target, text)
        if wake:
            self.wake_driver(abort)

    def wake_driver(self, abort: asyncio.Event | None = None) -> None:
        """空闲则启动 driver；有轮次在跑就什么都不做 —— 输入已在队列里，运行中的 driver
        会在下一个步/轮边界自己认领它（已取消的轮次兑现不了，唤醒已闩住待收敛后重放）。

        ``abort`` = 开出来的这一轮的协作取消信号；不传则新建一个（cancel() 用后者）。
        """
        if not isinstance(self._phase, _Idle):
            return
        self._phase = _Running(abort=abort or asyncio.Event(), turn=self._phase.last_turn)
        self._answer_floor = self.session.seq    # 新活动：答案从这里往后算（见 answer_text）
        self._activity = asyncio.create_task(self._kick())

    async def when_idle(self) -> None:
        """等 driver 收敛（等待期间被唤醒的新轮次也要等完）；空闲时立即返回。

        硬取消（调用方 task.cancel()）在 await 点一并取消 driver —— 等待者必须知道自己
        等的东西被取消了（事实的收口在 _turn 的 finally 里照常完成）。
        """
        while True:
            activity = self._activity
            if activity is None:
                return
            await activity
            if activity is self._activity:
                return

    def cancel(self, cause: str = "cancelled", *, keep_inbox: bool = False) -> None:
        """协作取消本轮：清待投递队列（除非 keep_inbox）+ 置本轮的 abort 信号。

        信号一旦置位，在途的 LLM 请求 / 工具调用在下一个 await 点被取消，本轮以
        ``stop_reason=cancelled`` 正常收口（见 RunCancelled）—— 被取消的一轮照样留事实。
        ``cause`` 只进日志（「谁让它停的」，与 A 的 AgentCancelCause 同义）。
        """
        if not keep_inbox:
            self._inbox.clear()
            if isinstance(self._phase, _Running):
                self._phase.wake_requested = False
        if isinstance(self._phase, _Running):
            logger.info(f"AgentLoop [{self.key}] 取消本轮（{cause}）")
            self._phase.abort.set()

    def last_assistant_text(self, since_seq: int = 0) -> str:
        """Session 事件里**最后一条** assistant/message 的正文（空 = 没有可用回答）。

        ``since_seq`` 只看这条 seq 之后的事件（默认 0 = 全部）：多轮会话里一次活动的答案
        必须限定在**本次活动**产生的事件内，否则被取消的一轮（没有半截正文）会读到上一次
        问句的回答。``answer_text()`` 用的就是它 + 上面那条活动边界。
        """
        for event in reversed(self.session.events):
            if event.seq <= since_seq:
                break
            if event.type == ASSISTANT_MESSAGE:
                return event.data.get("content") or ""
        return ""

    def answer_text(self) -> str:
        """答案出口：**本次活动**给用户的文本 —— 驱动的最后一段，调用方取它就完事。

        答案不做成「运行返回值」的第二份状态（Event Log 是执行事实的唯一来源，答案只是它的
        一个投影，与 trace / UI 同源）；这里额外做一件 B 的产品决定：模型没产出正文时，按
        stop_reason 说清楚**为什么没有**（见 _fallback_reply）—— 被停止的、轮数用尽的、
        工具超时的，都不该显示成「服务不可用」。
        """
        answer = self.last_assistant_text(since_seq=self._answer_floor)
        if answer:
            return answer
        if self._last_state is None:
            # 一次轮次都没装配起来（唯一的路：LLM 未配置）—— 「服务未配置」而不是「不可用」
            return self.fallback_reply or "服务未配置"
        return self._fallback_reply(self._last_state)

    # ── driver ──

    async def _kick(self) -> None:
        """driver：连续开轮直到队列里没有待投递输入；普通异常在此收敛，不逃逸出 driver。

        硬取消照样向上传播：等待者（when_idle）必须知道自己等的东西被取消了 —— 事实的
        收口已在 _turn 的 finally 完成。
        """
        try:
            while await self._turn():
                pass
        except asyncio.CancelledError:
            logger.info(f"AgentLoop [{self.key}] driver 被硬取消")
            raise
        except Exception as e:  # noqa: BLE001 —— driver 边界：坏掉的轮次不该逃逸到调用方
            logger.exception(f"AgentLoop [{self.key}] driver 异常: {e}")
        finally:
            if isinstance(self._phase, _Running):
                phase = self._phase
                self._phase = _Idle(last_turn=phase.turn)
                # 收敛前闩住的唤醒在这里重放（投递落在「已取消 / 正在收尾」的轮次里）
                if phase.wake_requested and self._inbox.has_pending:
                    self.wake_driver()

    def _running(self) -> _Running:
        """当前轮次状态；不在运行时抛 —— 契约：只有 driver 在跑时才谈得上「本轮」。"""
        if not isinstance(self._phase, _Running):
            raise RuntimeError(f"AgentLoop [{self.key}]: 没有运行中的轮次")
        return self._phase

    async def _turn(self) -> bool:
        """执行一轮：认领输入 → step 循环 → turn/end 闭合；返回「是否还要再开一轮」。

        只负责「轮」这一层的边界：轮内的 RunCancelled / 意外异常在此收口（硬取消除外 ——
        记完事实后原样向上传播）。turn/end 由 finally 落库，闭合由结构保证，不依赖
        「哪条 return 路径记得写」。轮内再没有可执行的输入就收敛 —— turn/end 之后队列里
        仍有待投递输入，才接着开下一轮（见 _kick）。
        """
        phase = self._running()
        if not self._llm_ready():
            # 没有可执行的配置：不开轮（不开一个注定失败的 turn），driver 就此收敛
            logger.warning(f"AgentLoop [{self.key}] LLM 未配置，本轮不执行")
            return False

        turn = phase.turn + 1
        target = NEXT_TURN                    # 每轮第一步取 next-turn，之后每步取 next-step
        opened = False
        turn_ends: str | None = None
        state = self._assemble_state(phase.abort)
        self._last_state = state

        try:
            while True:
                # 认领 = 投递的终点（A 的 preStep → inbox.claim）
                claimed = self._inbox.claim(target, turn)
                if not claimed:
                    if not opened:
                        # 首轮没有可执行的输入（如投递被取消清掉）：不开轮、不烧一次 step。
                        # 但这仍是一次「被叫停的活动」—— 记下原因，答案文案才不会是「服务不可用」。
                        if phase.abort.is_set():
                            state.stop_reason = "cancelled"
                        return False
                    if turn_ends is not None:
                        break                 # 本轮已收口，且没有新的运行中输入
                if not opened:
                    self.session.append(TURN_START, {
                        "agent": self.key,
                        "turn": turn,
                        # 轮级运行限制入 turn/start 事件（log-only）：TraceProjection 从中投影
                        "limits": {
                            "max_steps": state.max_steps,
                            "max_tool_calls": state.max_tool_calls,
                            "max_wall_time": state.max_wall_time,
                        },
                    })
                    phase.turn = turn
                    opened = True
                for text in claimed:
                    # 认领即落事实（surface）：本步与后续所有轮次都从 Event Log 派生到它
                    self.session.append(USER_MESSAGE, {"content": text})

                # 取消检查点（step 间隙）：在开新 step 之前响应 —— 不为一个注定被取消的
                # step 落 step/start。首轮即被取消的照样开过 turn 边界（上面），turn 仍闭合。
                if phase.abort.is_set():
                    raise RunCancelled()
                # max_wall_time：整轮的总时长（区别于 max_steps 步数上限与 per-tool timeout）
                if time.perf_counter() >= state.deadline:
                    state.stop_reason = "max_wall_time"
                    break
                step = phase.step + 1
                if step > state.max_steps:
                    turn_ends = "max_steps"   # 步数用尽：本轮收口（余下的输入留给下一轮）
                    break

                self.session.append(STEP_START, {"step": step, "total": state.max_steps})
                phase.step = step
                try:
                    outcome = await self._step(state, _StepDecision(messages=claimed))
                    # max_tokens 是黏的：已经截断过的一轮，后续步的正常收口不得把它降级
                    if turn_ends != "max_tokens":
                        turn_ends = None if outcome is None else outcome.stop_reason
                        if outcome is not None:
                            state.final_answer = outcome.final_answer
                finally:
                    self.session.append(STEP_END, {"step": step})
                target = NEXT_STEP
        except RunCancelled:
            # 协作取消：正常收口后返回，不抛 —— 取消的 turn 也要走完持久化与 done 事件。
            # 半截正文已在 step 内落盘；步之间被取消的没有半截内容，就是一次干净的停止。
            state.stop_reason = "cancelled"
            logger.info(f"AgentLoop [{self.key}] 本轮被取消（cancel 信号）")
        except asyncio.CancelledError:
            # 硬取消：同样闭合事件，但**不吞取消** —— 记下事实后原样向上传播（调用方的
            # 任务状态必须正确）。这条路拿不到半截正文（随被取消的协程一起消失）。
            state.stop_reason = "cancelled"
            logger.info(f"AgentLoop [{self.key}] 本轮被硬取消（task.cancel）")
            raise
        except Exception as e:
            # 兜底：step 体内的意外异常（LLM 失败已在 step 内收口）—— 记 traceback 再收口，
            # 本轮仍以 turn/end 闭合。stop_reason 留到 finally 解析。
            logger.exception(f"AgentLoop [{self.key}] 循环异常: {e}")
        finally:
            # turn/end 记录 stop_reason（正常完成 / guard / 异常都有值）；
            # TraceProjection 从中投影 trace.stop_reason。
            if state.stop_reason is None and turn_ends is not None:
                state.stop_reason = turn_ends
            state.stop_reason = _resolve_stop_reason(state)
            if opened:
                self.session.append(TURN_END, {"stop_reason": state.stop_reason})

        if not opened or not self._inbox.has_pending:
            # 没开成轮（配置缺失 / 装配即失败）就不再空转 —— 待投递输入留给下一次唤醒
            return False
        # 队列里仍有待投递输入：接着开下一轮，配一个**崭新的** abort —— 上一轮的取消不该
        # 继续生效；闩住的唤醒随之作废（活的 driver 自己会认领队列）。
        phase.abort = asyncio.Event()
        phase.wake_requested = False
        phase.step = 0
        return True

    def _llm_ready(self) -> bool:
        """端点+凭据齐备（问客户端）且请求里有模型名才执行：缺一即不落任何事件
        （不开一个注定失败的 turn）。"""
        return self._llm.configured and bool(self._model)

    def _assemble_state(self, abort: asyncio.Event) -> _RunState:
        """一轮的装配：LLM 参数 + 工具 schema（safe name + tenant_id 强注入）+ 防失控上限。

        换轮 = 换一套计数与 deadline（旧的 run() 是每次调用装配一次）。
        """
        name_map = {}
        tools = []
        for ts in self.tool_schemas:
            tool_name = ts["function"]["name"]
            safe = _safe_name(tool_name)
            name_map[safe] = tool_name
            t = json.loads(json.dumps(ts))
            t["function"]["name"] = safe
            tools.append(t)

        for t in tools:
            # 注意：不要用 params 命名（会覆盖上面的 LLM 参数），这是给 LLM 的 tenant_id 强注入
            tool_params = t["function"]["parameters"]
            props = tool_params.setdefault("properties", {})
            props["tenant_id"] = {"type": "integer", "description": "租户ID，必须为1"}
            required = tool_params.setdefault("required", [])
            if "tenant_id" not in required:
                required.append("tenant_id")

        max_tool_calls = self.llm_params["max_tool_calls"]
        max_wall_time = self.llm_params["max_wall_time"]
        run_start = time.perf_counter()        # max_wall_time 起点（整轮，非单步）
        return _RunState(
            model=self._model,
            max_steps=self.llm_params["max_steps"],
            max_tool_calls=max_tool_calls,
            max_wall_time=max_wall_time,
            max_tokens=self.llm_params["max_tokens"],
            deadline=run_start + max_wall_time,  # 绝对截止时刻：LLM 超时与重试退避都不得突破
            tools=tools,
            name_map=name_map,
            cancel=abort,
        )

    async def _step(self, state: _RunState, decision: _StepDecision) -> _StepOutcome | None:
        """执行一步：LLM（step 内重试）→ 收敛 / 截断 / guard 判定 → 计划 + 调度。

        本步的入参（``decision.messages``）已由 _turn 在**认领时**落成 user/message 事实 ——
        这里只负责把它交给模型：payload 的 messages 由 derive_messages() 派生，「本步看到
        什么」永远等于认领到的事实，没有第二份输入状态。将来的 ``pre_step`` 拦截点就在
        decision 上改写它（§6.2）。

        返回 None 表示继续下一步，_StepOutcome 表示本轮到此收口。取消不在返回值里表达，
        直接穿透到 _turn；step/start 与 step/end 由 _turn 严格成对 —— 本步每一条退出路径
        （收敛 / guard / LLM 失败 / 异常 / 取消）都由它闭合。
        """
        step = self._running().step - 1     # 0-based：事件与日志口径是 1-based（同旧 _run_step）
        request = self._build_request(state)
        logger.info(
            f"AgentLoop [{self.key}] step {step + 1}/{state.max_steps}, "
            f"本轮新输入={len(decision.messages)}, "
            f"messages={len(request.messages)}, "
            f"tools={[t['function']['name'] for t in state.tools]}"
        )

        # 一次 step 级 LLM 请求：内部有限重试，重试耗尽抛 LlmError；失败的尝试不落
        # 任何 surface 事实，只有成功的那次才走到下面的落盘。（流式路径的增量例外：
        # 广播出去的 assistant/chunk 撤不回来 —— 它们确实发生过。）
        try:
            # 请求整体（含内部重试）跑在可取消包装里：信号一到就取消在途请求，
            # 不必等它自己那层超时（超时多久是适配器的事，本层不猜）
            response = await self._run_cancellable(
                self._request_llm(request, state.deadline, step, state.cancel),
                state.cancel,
            )
        except RunCancelled as e:
            # 取消落在本步：先把中断前已产出的正文落盘（仍在 step 内），再让它穿透到
            # _turn 收口。被停止的这轮照样留下事实。
            state.final_answer = self._settle_interrupted(e.partial)
            raise
        except LlmError as e:
            # 重试耗尽：本轮以 turn/end 收口。截断不走这条路（它是正常结束的一种），
            # 由下面的 finish_reason == "length" 分支处理。
            logger.warning(f"AgentLoop [{self.key}] step {step + 1} LLM 失败: {e}")
            return _StepOutcome("error")

        msg, finish_reason, llm_usage = response.message, response.finish_reason, response.usage

        # finish_reason=length：被 max_tokens 截断 —— 正常结束的一种，不是失败，因此
        # **绝不重试**，直接终止本轮。tool_calls 一律丢弃：参数可能被截断（落库即不可
        # 复现的事实），且落了不执行会留下无配对 tool/result 的 dangling 消息，下一轮
        # 请求直接非法。正文为空时不落任何 assistant 消息。
        if finish_reason == "length":
            final_answer = msg.get("content") or ""
            if msg.get("tool_calls"):
                logger.warning(
                    f"AgentLoop [{self.key}] step {step + 1} 被 max_tokens 截断，"
                    f"丢弃 {len(msg['tool_calls'])} 个不完整 tool_calls"
                )
            if final_answer:
                self._append_assistant(
                    content=final_answer, tool_calls=[], llm_usage=llm_usage, step=step,
                )
            return _StepOutcome("max_tokens", final_answer)

        self._append_assistant(
            content=msg.get("content") or "",
            tool_calls=msg.get("tool_calls") or [],
            llm_usage=llm_usage,
            step=step,
        )

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            return _StepOutcome("completed", msg.get("content") or "")

        # 计划阶段：只裁决「要不要执行、执行什么」，不执行也不落库（详见 _plan_tool_calls）
        plans = self._plan_tool_calls(tool_calls, state)

        # 执行 + 有序提交：顺序 / 生命周期事件 / tool_call 与 tool_result 的成对落库都归
        # ToolScheduler。取消信号经 asyncio 取消机制送进调度器 → 走它既有的取消恢复
        # 路径（排空在途、未启动的补合成结果），每个 tool/call 都保住配对。
        outcomes = await self._run_cancellable(
            self._scheduler.execute_tool_calls(plans), state.cancel,
        )
        for outcome in outcomes:
            # tool 超时属于安全停止而非系统异常：已作为 error 结果给 LLM，同时记录供终止兜底
            if "执行超时" in outcome.error:
                state.last_tool_timeout_msg = outcome.error

        if state.tool_calls_exhausted or state.repeat_tool_stopped:
            return _StepOutcome("max_tool_calls" if state.tool_calls_exhausted else "repeat_tool")
        return None

    def _plan_tool_calls(self, tool_calls: list[dict], state: _RunState) -> list[PlannedCall]:
        """计划阶段：只产出「要不要执行、执行什么」，不执行也不落库；计数与 guard 标记写进 state。

        guard 按 model order 裁决：命中后剩余调用全部 SKIP，但仍成对落 tool/call + 合成
        tool/result —— 否则下一轮 derive_messages 的消息序列非法。参数一定可解析（不可
        解析的 tool_calls 已在 validate_response 处按失败重试）。
        """
        plans: list[PlannedCall] = []
        for tc in tool_calls:
            safe = tc["function"]["name"]
            tool_name = state.name_map.get(safe, safe)
            args = json.loads(tc["function"]["arguments"])
            args["tenant_id"] = self.tenant_id  # 系统覆盖，不信任 LLM 传入值

            action, skip_reason = DISPATCH, ""
            if state.tool_calls_exhausted:
                # max_tool_calls：run 级计数已达上限，后续调用一律不执行
                action, skip_reason = SKIP, SKIP_MAX_TOOL_CALLS
            elif state.repeat_tool_stopped:
                action, skip_reason = SKIP, SKIP_REPEAT_TOOL
            elif state.tool_call_count >= state.max_tool_calls:
                state.tool_calls_exhausted = True
                action, skip_reason = SKIP, SKIP_MAX_TOOL_CALLS
            else:
                # repeat tool 检测：tool + 标准化 args 完全相同才计数，第三次触发停止
                signature = (tool_name, json.dumps(args, ensure_ascii=False, sort_keys=True))
                if signature == state.last_tool_signature:
                    state.repeat_tool_count += 1
                else:
                    state.last_tool_signature = signature
                    state.repeat_tool_count = 0
                if state.repeat_tool_count >= REPEAT_TOOL_THRESHOLD:
                    state.repeat_tool_stopped = True
                    action, skip_reason = SKIP, SKIP_REPEAT_TOOL
                else:
                    state.tool_call_count += 1

            plans.append(PlannedCall(
                tool_call_id=tc["id"],
                tool_name=tool_name,
                args=args,
                action=action,
                skip_reason=skip_reason,
            ))
        return plans

    def _build_request(self, state: _RunState) -> LlmRequest:
        """组装一次 LLM 请求（在 step 边界一次性冻结，messages 只派生一次）。

        ``stream`` 由「有没有订阅者」决定：没有订阅者走一次性 JSON（省掉 SSE 的开销与
        解析），有则流式、增量即刻广播。这是**循环侧的选择** —— 适配器只按这个规范选项
        决定 wire 形态，将来改成「永远流式」只动这里。
        ``timeout`` 把本轮的 max_wall_time 剩余预算带下去（适配器再与自己的上限取小）。
        """
        return LlmRequest(
            model=state.model,
            messages=self._get_messages(),
            tools=state.tools or None,
            temperature=self.llm_params["temperature"],
            max_tokens=state.max_tokens,
            stream=bool(self.on_event),
            timeout=max(state.deadline - time.perf_counter(), 1.0),
        )

    def _append_assistant(
        self, *, content: str, tool_calls: list, llm_usage: TokenUsage | None, step: int,
    ) -> None:
        """落一条 assistant/message（usage 有则紧随记录 llm/usage 事件，log-only）。

        三条落盘路径（正常 / max_tokens 截断 / 取消半截）收敛到这一个方法：正文为空但
        有 tool_calls 时仍要落（配对需要）；两者皆空不落（空消息不进历史）。
        """
        if not content and not tool_calls:
            return
        self.session.append(ASSISTANT_MESSAGE, {"content": content, "tool_calls": tool_calls})
        if llm_usage:
            self._record_usage(llm_usage, step)

    def _fallback_reply(self, state: _RunState) -> str:
        """终止兜底：guard 安全停止都给出明确原因（不是「服务不可用」）。

        按 stop_reason 分发；词表外（意外收口为 error）落回配置的 fallback_reply。
        """
        reason = state.stop_reason
        if reason == "cancelled":
            # 这轮是被停止的，不是坏掉的 —— 所以不给「服务暂时不可用」
            return CANCELLED_REPLY
        if reason == "max_tokens":
            # 走到这里说明截断那次没生成任何可用正文（正文非空时 run() 就返回了）
            return (f"模型输出被截断（达到 max_tokens={state.max_tokens} 上限），未生成可用回答。"
                    f"请重试，或调大该 Agent 的 max_tokens 配置。")
        if reason == "max_tool_calls":
            return f"工具调用次数过多（超过 {state.max_tool_calls} 次），已自动停止。请尝试把问题拆分成更小的步骤后重试。"
        if reason == "repeat_tool":
            return "检测到 Agent 连续重复调用相同工具，已自动停止。请尝试重新描述问题或拆分任务。"
        if reason == "max_steps":
            # 轮数耗尽：明确告知而不是模糊的「服务不可用」
            return f"处理轮数过多（超过 {state.max_steps} 轮），已自动停止。请尝试把问题拆分成更小的步骤后重试。"
        if reason == "max_wall_time":
            return f"处理时间过长（超过 {state.max_wall_time:.0f} 秒），已自动停止。请尝试把问题拆分成更小的步骤后重试。"
        if reason == "tool_timeout":
            return f"{state.last_tool_timeout_msg}，请重试或拆分成更小的步骤。"
        return self.fallback_reply or "服务暂时不可用"

    def _get_messages(self) -> list[dict]:
        """LLM 消息 = 组装的 system prompt（前置）+ Session surface 派生。

        system 恒为 messages[0]：前部段的渲染结果跨轮稳定（动态内容排在提示词尾部），
        因此 KV cache 前缀不受逐轮变化的上游输出影响。
        """
        messages = self.session.derive_messages()
        if not self.system_prompt:
            return messages
        return [{"role": "system", "content": self.system_prompt}, *messages]

    def _record_usage(self, usage: TokenUsage, step: int) -> None:
        """usage 旁路采集：canonical usage → llm/usage 事件（log-only，不影响执行链）。

        事件字段用 canonical 名（cache_hit_tokens / cache_miss_tokens）—— provider 的
        字段名（prompt_cache_hit_tokens）在适配器里就翻完了，这里不再认识任何一家；
        网关缺 cache 字段时是 None，不报错。每步一条，紧跟对应 assistant/message 之后。
        """
        self.session.append(LLM_USAGE, usage.fact(step + 1))

    def _record_llm_error(
        self, error: LlmError, *, step: int, attempt: int, retry_in: float | None,
    ) -> None:
        """一次失败的 LLM 尝试 → llm/error 事件（log-only）。

        失败的尝试没有产出任何 LLM 可见内容 —— 不落 surface 事件、进不了
        derive_messages、不影响前缀缓存。重试因此完全可回放。
        """
        self.session.append(LLM_ERROR, error.fact(step=step + 1, attempt=attempt, retry_in=retry_in))

    async def _run_cancellable(self, aw, cancel: asyncio.Event | None):
        """跑一个 awaitable，取消信号一到就取消它；信号胜出时抛 RunCancelled。

        取消**复用 asyncio 的取消机制**：被等的协程在自己的 await 点收到 CancelledError ——
        LLM 客户端中止在途请求、ToolScheduler 走进它既有的取消恢复路径，被取消方不需要认识
        「取消信号」这个概念。没有信号时不套包装（直接 await），该路径零开销、行为不变。
        """
        if cancel is None:
            return await aw

        work = asyncio.ensure_future(aw)
        stopper = asyncio.ensure_future(cancel.wait())
        try:
            await asyncio.wait({work, stopper}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            # 外部硬取消：把子任务一并收干净再抛 —— 不能留下孤儿任务继续跑工具 / 占着连接
            work.cancel()
            stopper.cancel()
            await asyncio.gather(work, stopper, return_exceptions=True)
            raise

        if work.done() and not work.cancelled():
            stopper.cancel()
            await asyncio.gather(stopper, return_exceptions=True)
            return work.result()

        # 信号胜出：取消在途工作，等它把事实补完（ToolScheduler 的合成结果就在这一步落库），
        # 再把它的「交代」原样带出来 —— 被取消方可能已经救出了半截正文（如 LLM 流）
        work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        failure = None if work.cancelled() else work.exception()
        raise failure if isinstance(failure, RunCancelled) else RunCancelled()

    def _settle_interrupted(self, partial: dict) -> str:
        """取消时保留中断前已产出的正文，返回该正文。

        ``tool_calls`` **一律丢弃**：取消多半落在参数没写完的时候，落库就是落不可复现的
        事实，而且会留下无配对 tool/result 的 dangling 消息（下一轮请求直接非法）。
        正文为空则什么都不落。
        """
        content = partial.get("content") or ""
        if not content:
            return ""
        self.session.append(ASSISTANT_MESSAGE, {"content": content, "tool_calls": []})
        logger.info(f"AgentLoop [{self.key}] 取消时保留已产出的 {len(content)} 字正文")
        return content

    async def _request_llm(
        self, request: LlmRequest, deadline: float, step: int,
        cancel: asyncio.Event | None = None,
    ) -> LlmResponse:
        """一次 step 级 LLM 请求（含有限重试）：返回 canonical 响应。

        重试边界在 **step 内**：不新开 step/start、不落 surface 事实，失败尝试只留 llm/error，
        耗尽抛 LlmError 由 step 体收口。截断不走这条路（正常结束，非失败）；取消也不走
        （RunCancelled / CancelledError 不是 LlmError，被取消的请求不该再重试）。
        """
        attempt = 0
        while True:
            try:
                return await self._call_llm_once(request, cancel)
            except LlmError as e:
                # 决策外置：钩子（或内置默认策略）回答「要不要重试、等多久」，这里只执行裁决
                decision = await self._ask_request_error(e, attempt)
                delay = 0.0 if decision is None else decision.delay
                if decision is not None and delay >= deadline - time.perf_counter():
                    # 退避等待会突破 max_wall_time：宁可早停。这条约束留在 Loop 而不是策略里
                    # —— 只有 Loop 知道本轮还剩多少时间。
                    decision, delay = None, 0.0
                self._record_llm_error(
                    e, step=step, attempt=attempt + 1,
                    retry_in=None if decision is None else delay,
                )
                if decision is None:
                    raise
                logger.info(f"AgentLoop [{self.key}] step {step + 1} LLM 重试（{e.code}）: {delay:.2f}s 后第 {attempt + 2} 次")
                await asyncio.sleep(delay)
                attempt += 1

    async def _ask_request_error(self, error: LlmError, attempt: int) -> RetryDecision | None:
        """问拦截通道「这次失败要不要重试、等多久」。

        未装配钩子 → 内置默认策略（行为与改造前一致）；钩子抛异常 → 记 exception 后按
        默认策略继续（拦截器坏掉不能打死执行链）。取消是唯一的例外，照常穿透。
        """
        hook = self._request_error
        if hook is None:
            return await self._default_request_error(error, attempt)
        try:
            return await hook(error, attempt)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(f"AgentLoop [{self.key}] request_error 钩子异常，按默认重试策略继续")
            return await self._default_request_error(error, attempt)

    async def _call_llm_once(
        self, request: LlmRequest, cancel: asyncio.Event | None = None,
    ) -> LlmResponse:
        """发一次 LLM 请求并校验响应；任何失败都抛 LlmError（调用方决定是否重试）。

        两条路收口到同一个 ``LlmResponse``：非流式由客户端直接给出完整 message；流式则
        经累积器装配 —— **增量落 assistant/chunk 事实就在这一步**（见 loop/assistant_stream.py）。

        传输层异常（超时 / 连接失败 → 失败码）的归一在适配器里做（那是唯一认识 aiohttp
        的地方），本层只认 ``LlmError``；取消异常不在此转换 —— 它们必须原样穿透。
        """
        response = (
            await self._consume_stream(request, cancel) if request.stream
            else await self._llm.complete(request)
        )
        validate_response(response.message, response.finish_reason)
        return response

    async def _consume_stream(
        self, request: LlmRequest, cancel: asyncio.Event | None,
    ) -> LlmResponse:
        """消费一次流式请求：分片 → 累积器（每个增量立刻落成事实）。

        取消落在流中途：已经广播出去的增量**照样是事实**（撤不回来），已累积的半截
        message 随 ``RunCancelled.partial`` 交给 _step 去 settle。适配器不认识取消，
        所以这里必须自己判 —— 判据是**信号本身**：没有信号（外部 task.cancel()）时
        原样穿透，硬取消不能在这里被吞掉。
        """
        stream = AssistantStream(self.session)
        try:
            async for chunk in self._llm.stream(request):
                if cancel is not None and cancel.is_set():
                    raise RunCancelled(partial=stream.message())
                stream.push(chunk)
        except asyncio.CancelledError:
            if cancel is not None and cancel.is_set():
                raise RunCancelled(partial=stream.message()) from None
            raise
        return LlmResponse(
            message=stream.message(),
            finish_reason=stream.finish_reason,
            usage=stream.usage,
        )

    def _tool_context(self, tool_call_id: str) -> ToolContext:
        """构造一次 Tool 调用的上下文（ToolScheduler 的 context_factory）。

        Tool 生命周期事件（started / progress / completed / failed）由 ToolRuntime /
        Executor 经 context.event_sink 发出，本 Loop 不感知；tool_call_id 在构造时绑定到
        sink（一次调用一个 sink）。
        """
        return ToolContext(
            session_id=self.session.header.id,
            tool_call_id=tool_call_id,
            agent_id=self.key,
            event_sink=SessionToolEventSink(self.session, tool_call_id, forward=self.on_event),
            sandbox_policy=self._sandbox_policy(),
            approval=self._approval_channel(),
        )

    @staticmethod
    def _approval_channel():
        """审批通道；未装配返回 None（需要审批的能力将 fail-closed）。

        与 _sandbox_policy 同模式：装配层的单例在这里惰性取用，Loop 只把它挂到
        ToolContext 上，不解释它是什么。
        """
        try:
            from backend.interaction.approval import get_approval_service
            return get_approval_service()
        except Exception as e:
            logger.warning(f"审批服务取用失败（需要审批的能力将一律被拒）: {e}")
            return None

    def _sandbox_policy(self):
        """本会话的沙箱执行策略；沙箱未装配时 None（MCP 工具不受影响）。

        工作区根来自 SessionHeader.cwd，确定性派生自 session_id + 部署配置，因此不需要
        额外的事件记录。会话覆盖来自 sandbox/mode 事件的投影（find-last）。沙箱工具在
        策略缺失时 fail-closed。
        """
        try:
            from backend.agents.runtime.session.projections import project_sandbox_mode
            from backend.tool_system.sandbox.runtime import session_policy
            header = self.session.header
            policy = session_policy(
                header.id,
                header.cwd,
                session_override=project_sandbox_mode(self.session.events),
            )
            if policy is not None and header.cwd is None:
                header.cwd = policy.workspace_root
            return policy
        except Exception as e:
            logger.warning(f"沙箱策略解析失败（沙箱工具将拒绝执行）: {e}")
            return None
