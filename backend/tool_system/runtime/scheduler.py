"""ToolScheduler — 一批 Tool 调用的调度器（AgentLoop 不再直接执行 Tool）

调用链：
    AgentLoop
      → ToolScheduler.execute_tool_calls(plans)   ← 本层：调度 + 有序提交
          → ToolCallRecorder.record_call()        ← 装配方实现（写 Session Event Log）
          → ToolRuntime.execute()                 ← 执行原子；本层不解释 transport
          → ToolCallRecorder.record_result()
    AgentLoop 只产出「调用计划」（名字还原 / 参数解析 / guard 裁决），不碰执行顺序。

分层：本模块属于 tool_system，**不依赖 agents 层**。Session 的写入经
:class:`ToolCallRecorder` Protocol 反向注入，与 ``sandbox/runtime.py``
「只吃原始值、不接收 Session 对象」是同一条规则。

调度算法（对齐 DSH ``tool-calls.ts`` 的 ``runGroup``）：
  滚动池   —— 并发不超过 ``max_parallel_tools``，有调用完成即补新的
  屏障     —— ``exclusive`` 调用启动前须等在途调用排空，启动后独占单跑
  重分类   —— 每个调用在**真正启动前**才查执行模式（前序执行可能改注册表）
  有序提交 —— 执行可重叠，``record_result`` 严格按模型顺序推进前缀

有序提交的必要性：``tool/result`` 是 surface 事件（进 LLM Context），
``Session.derive_messages()`` 的消息顺序 = 事件写入顺序。并发执行时若按完成序
提交，``assistant.tool_calls`` 与 tool 消息会错位 —— OpenAI 兼容网关直接 400。
因此「执行可重叠、提交按模型顺序」是本层的核心不变式。

取消/异常同样保持事件完整：已启动的排空（保留真实结果）、未启动的补合成结果，
保证每个 ``tool/call`` 都有配对的 ``tool/result``（replay 与多轮会话都依赖它）。
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

from backend.tool_system.context import ToolContext
from backend.tool_system.registry.descriptor import EXCLUSIVE
from backend.tool_system.runtime.runtime import ToolRuntime, get_tool_runtime

logger = logging.getLogger(__name__)

#: 并发池默认上限（装配方可用 agent 配置 max_parallel_tools 覆盖）
DEFAULT_MAX_PARALLEL_TOOLS = 4

# ── 调用计划 ──

DISPATCH = "dispatch"   # 正常派发执行
SKIP = "skip"           # guard 命中，不执行（仍需成对记录 call/result）

SKIP_MAX_TOOL_CALLS = "max_tool_calls"
SKIP_REPEAT_TOOL = "repeat_tool"

#: guard 跳过时的合成错误文本（写入 tool/result，保证 replay / LLM messages 合法）
SKIP_MESSAGES = {
    SKIP_MAX_TOOL_CALLS: "工具调用次数已达上限（max_tool_calls），本次调用未执行",
    SKIP_REPEAT_TOOL: "检测到重复调用相同工具与参数，本次调用未执行",
}

#: 取消补记的合成错误文本（未启动 / 已启动被中断，两种事实分开表述）
ABORTED_MESSAGE = "工具调用已取消（未执行）"
ABORTED_STARTED_MESSAGE = "工具调用已取消（执行中被中断）"


@dataclass(frozen=True)
class PlannedCall:
    """一次 Tool 调用的计划（AgentLoop 产出，调度器消费）。

    ``action == SKIP`` 时携带 ``skip_reason``（取 SKIP_* 常量）：该调用不执行，
    但仍会成对记录 ``tool/call`` + 合成 ``tool/result``。
    """

    tool_call_id: str
    tool_name: str                 # 真名（name_map 已还原，非 LLM 侧安全名）
    args: dict
    action: str = DISPATCH
    skip_reason: str = ""


@dataclass(frozen=True)
class ToolCallOutcome:
    """一次 Tool 调用的调度结果（回给 AgentLoop 做 stop_reason 判定）"""

    tool_call_id: str
    tool_name: str
    result: dict
    #: 结果不是真实执行产物（guard 跳过 / 取消补记）—— 区别于「执行了并返回错误」
    skipped: bool = False

    @property
    def error(self) -> str:
        """结果里的 error 文本（无则空串）"""
        return (self.result.get("error") or "") if isinstance(self.result, dict) else ""


def _aborted_outcome(plan: PlannedCall, *, started: bool) -> ToolCallOutcome:
    """取消时补记的合成结果。

    ``started=True`` 表示该调用已派发但被中断（真实结果不可用）；
    False 表示尚未派发就被取消。两种事实在文本上区分，便于回放时判断。
    """
    return ToolCallOutcome(
        tool_call_id=plan.tool_call_id,
        tool_name=plan.tool_name,
        result={"error": ABORTED_STARTED_MESSAGE if started else ABORTED_MESSAGE},
        skipped=True,
    )


def _settled_outcome(plan: PlannedCall, task: asyncio.Task) -> ToolCallOutcome:
    """取消路径下取已派发调用的结果：已跑完用真实结果，否则补合成结果。

    真实结果优先 —— 取消不该把已经产出的事实改写成「未知」。任务以异常收场时
    也退回合成结果：取消路径本身不能再被二次异常打断（它还在补事件）。
    """
    if task.done() and not task.cancelled():
        try:
            return task.result()
        except BaseException:  # noqa: BLE001
            pass
    return _aborted_outcome(plan, started=True)


def _registry_mode_resolver(tool_name: str) -> str:
    """缺省模式解析：查 Registry 的统一索引（同在 tool_system 层，不越层）。

    Registry 未装配时抛 RuntimeError，由调用方按 fail-closed 处理（见
    :meth:`ToolScheduler._is_exclusive`）。
    """
    from backend.tool_system.registry.registry import get_registry
    return get_registry().execution_mode_of(tool_name)


@runtime_checkable
class ToolCallRecorder(Protocol):
    """调用事实的记录出口（装配方实现：Session Event Log / UI 流 / 持久化旁路）。

    两个方法都必须是**同步**的：``Session.append()`` 无 await 点，这是
    「listener 收到的事件顺序与 seq 完全一致」的前提；调度器在取消恢复路径上
    也依赖它（finally 里不能 await，否则会被二次取消打断）。
    """

    def record_call(self, call: PlannedCall) -> None:
        """记录一次 Tool 调用发起（写 ``tool/call``）。"""

    def record_result(self, call: PlannedCall, result: dict) -> None:
        """记录一次 Tool 调用结果（写 ``tool/result``，含合成结果）。"""


class ToolScheduler:
    """把一批 PlannedCall 调度成 ToolRuntime 调用，并按模型顺序提交结果"""

    def __init__(
        self,
        *,
        recorder: ToolCallRecorder,
        context_factory: Callable[[str], ToolContext],
        runtime: ToolRuntime | None = None,
        max_parallel_tools: int = DEFAULT_MAX_PARALLEL_TOOLS,
        mode_resolver: Callable[[str], str] | None = None,
    ):
        """
        Args:
            recorder: 调用事实出口（装配方实现）。
            context_factory: ``tool_call_id → ToolContext``；沙箱策略 / 事件出口
                在装配方构造（调度器不解释 context 内容）。
            runtime: 缺省用进程级单例 ``get_tool_runtime()``。
            max_parallel_tools: 并发池上限（>=1）；独占调用不受它约束（独占即单跑）。
            mode_resolver: ``tool_name → execution_mode``；缺省查 Registry。
                在每次派发前查询（而非预计算）—— 前面的执行可能改变注册表。
        """
        self._recorder = recorder
        self._context_factory = context_factory
        self._runtime = runtime
        self._max_parallel = max(1, max_parallel_tools)
        self._mode_resolver = mode_resolver or _registry_mode_resolver

    async def execute_tool_calls(self, plans: list[PlannedCall]) -> list[ToolCallOutcome]:
        """执行一批调用并有序提交，返回与 ``plans`` 等长的结果列表。

        调度算法（对齐 DSH tool-calls.ts 的 runGroup）：

          滚动池 —— 并发不超过 ``max_parallel_tools``，有调用完成即补新的；
          屏障   —— exclusive 调用启动前须等在途调用排空，启动后本轮不再填池；
          重分类 —— 每个调用在**真正启动前**才查询模式（前序执行可能改注册表）；
          有序提交 —— 执行可重叠，``record_result`` 严格按模型顺序推进前缀
                     （``committed`` 只在前缀连续时前进），保住 surface 消息序。

        guard 跳过的调用（``action == SKIP``）不发执行、不占并发槽、不形成屏障，
        直接记合成结果 —— 保证 ``assistant.tool_calls`` 的每一项都有对应的
        ``tool/result``。

        取消/异常：已启动的调用被取消并排空，未提交的调用一律补合成结果，
        最后按模型顺序提交完整前缀（同步写，不会被二次取消打断）后向上抛。
        """
        if not plans:
            return []

        slots: list[ToolCallOutcome | None] = [None] * len(plans)
        in_flight: dict[int, asyncio.Task] = {}
        next_to_start = 0
        committed = 0
        outcomes: list[ToolCallOutcome] = []

        def commit_ready() -> None:
            """有序提交：只提交从 committed 起连续就绪的前缀（同步，无 await 间隙）"""
            nonlocal committed
            while committed < len(plans) and slots[committed] is not None:
                outcome = slots[committed]
                assert outcome is not None  # 由 while 条件保证
                self._recorder.record_result(plans[committed], outcome.result)
                outcomes.append(outcome)
                committed += 1

        def start(index: int) -> None:
            """记录发起并派发。

            必须先 ``record_call`` 再 ``create_task``：本函数是同步的（无 await），
            因此任务体在本次填池结束前不会运行 —— tool/call 事件一定先于该调用的
            tool.started / tool/result 落库。
            """
            plan = plans[index]
            self._recorder.record_call(plan)
            if plan.action != SKIP:
                in_flight[index] = asyncio.create_task(self._dispatch(plan))

        def fill_pool() -> None:
            """填池：从 next_to_start 起按序启动，直到池满或撞上屏障"""
            nonlocal next_to_start
            while next_to_start < len(plans) and len(in_flight) < self._max_parallel:
                plan = plans[next_to_start]
                if plan.action == SKIP:
                    # 跳过项不执行：不占槽、不形成屏障，直接落合成结果
                    self._recorder.record_call(plan)
                    slots[next_to_start] = self._skipped_outcome(plan)
                    next_to_start += 1
                    commit_ready()
                    continue
                if self._is_exclusive(plan.tool_name):
                    if in_flight:
                        break            # 屏障：先让在途调用排空
                    start(next_to_start)  # 独占：单跑，且本轮不再填池
                    next_to_start += 1
                    commit_ready()
                    break
                start(next_to_start)
                next_to_start += 1
                commit_ready()

        try:
            while next_to_start < len(plans) or in_flight:
                fill_pool()
                if not in_flight:
                    continue
                done, _ = await asyncio.wait(
                    list(in_flight.values()), return_when=asyncio.FIRST_COMPLETED,
                )
                for index in [i for i, t in in_flight.items() if t in done]:
                    slots[index] = in_flight.pop(index).result()
                commit_ready()
        except BaseException:
            # 失败/取消：**先补全事实、再 await 排空**。顺序不能反 —— 若先 await
            # 排空，二次取消会打断事件写入，留下 replay 不完整的日志。
            # 下面每个槽一填就提交，保证 tool/call 与 tool/result 在日志里成对相邻。
            for task in in_flight.values():
                task.cancel()
            for index in sorted(in_flight):
                task = in_flight[index]
                slots[index] = _settled_outcome(plans[index], task)
                commit_ready()
            for index, plan in enumerate(plans):
                if slots[index] is None:
                    # 尚未发起：补记 tool/call（此前从未记过），再补合成结果
                    self._recorder.record_call(plan)
                    slots[index] = _aborted_outcome(plan, started=False)
                    commit_ready()
            try:
                await asyncio.gather(*in_flight.values(), return_exceptions=True)
            except BaseException:
                pass
            raise

        return outcomes

    # ── 内部 ──

    def _is_exclusive(self, tool_name: str) -> bool:
        """该工具是否必须独占执行（每次派发前查询，见 _mode_resolver 文档）"""
        try:
            return self._mode_resolver(tool_name) == EXCLUSIVE
        except Exception as e:  # noqa: BLE001
            # 查不到模式时按独占处理（fail-closed）：宁可少并发，不可让有共享
            # 状态的工具并发跑。与沙箱执行侧「策略缺失即拒绝」同一取向。
            logger.warning(f"工具 {tool_name} 执行模式查询失败，按 exclusive 处理: {e}")
            return True

    # ── 内部 ──

    async def _dispatch(self, plan: PlannedCall) -> ToolCallOutcome:
        """执行一次 Tool：构造 context 并交给 ToolRuntime。

        Tool 生命周期事件（started / progress / completed / failed）由 ToolRuntime /
        Executor 经 ``context.event_sink`` 发出，本层不感知、不拼装。
        """
        context = self._context_factory(plan.tool_call_id)
        try:
            result = await self._runtime_or_default().execute(plan.tool_name, plan.args, context)
        except Exception as e:
            # 执行异常不中断本批其余调用：错误作为结果交给 LLM（与重构前一致）
            result = {"error": str(e)}
        return ToolCallOutcome(
            tool_call_id=plan.tool_call_id,
            tool_name=plan.tool_name,
            result=result,
        )

    def _skipped_outcome(self, plan: PlannedCall) -> ToolCallOutcome:
        """guard 跳过的调用：不执行，产出合成错误结果（仍需成对记录）"""
        message = SKIP_MESSAGES.get(plan.skip_reason, f"调用未执行（{plan.skip_reason}）")
        logger.info(f"ToolScheduler 跳过 {plan.tool_name}（{plan.skip_reason}）")
        return ToolCallOutcome(
            tool_call_id=plan.tool_call_id,
            tool_name=plan.tool_name,
            result={"error": message},
            skipped=True,
        )

    def _runtime_or_default(self) -> ToolRuntime:
        return self._runtime if self._runtime is not None else get_tool_runtime()
