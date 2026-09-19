"""运行中输入（Inbox）— 两条待投递队列 + 三个投递动作的落点（对齐 A 的 inbox.ts）

动作与队列（docs/agent-loop-design.md §2）：

    followup → next-turn  本轮结束后作为**新的一轮**开始（唤醒 driver）
    steer    → next-step  当前轮的**下一步**就并入上下文（唤醒 driver）
    inject   → next-step  系统侧静默投递（不唤醒）

认领时机 = **每步开头**（``claim``）：本轮第一步取 next-turn，之后每步取 next-step。
认领到的消息由 AgentLoop 立刻落成 ``user/message``（surface）事实，于是本步的
``derive_messages()`` 自然带上它 —— 队列只是「已受理、还没投递」，不是第二份对话历史。

为什么这一版只在内存里：先把**唤醒与认领的时序**跑通（与 SessionStore 同层的进程内
热区）。「队列本身也是执行事实、进程重启后仍能投递」需要 Event Log + 投影两层
（见 docs/agent-loop-design.md §3），属于下一批工作 —— 在此之前 API 层还没有对外开放
的投递端点，所以「重启丢待投递输入」对用户尚不可见。队列用 list 而非 deque：认领是
整段取走（splice），两端都要动，list 的分片语义正好是 A 的 splice。
"""
import logging

logger = logging.getLogger(__name__)

#: 两条待投递队列（对齐 A 的 InboxTarget）
NEXT_TURN = "next-turn"
NEXT_STEP = "next-step"


class ReactLoopInbox:
    """待投递的运行中输入：两条 list（next-turn / next-step），认领即取走。"""

    def __init__(self) -> None:
        self._queues: dict[str, list[str]] = {NEXT_TURN: [], NEXT_STEP: []}

    # ── 只读视图 ──

    @property
    def has_pending(self) -> bool:
        """两条队列是否还有待投递输入（driver 是否要接着开轮）。"""
        return bool(self._queues[NEXT_TURN] or self._queues[NEXT_STEP])

    @property
    def next_step_length(self) -> int:
        """next-step 的长度（「本轮还该不该再来一步」看它，认领前查询用）。"""
        return len(self._queues[NEXT_STEP])

    # ── 写入 ──

    def splice(self, target: str, start: int, delete_count: int, inserted: list[str]) -> list[str]:
        """标准 splice 语义（负索引 / 越界收敛），返回被移除的消息 —— 队列唯一的写原语。

        其余写操作都在它之上表达（append / claim / clear），语义与 A 的 ReactLoopInbox
        一致：越界不报错，按实际长度收敛。
        """
        queue = self._queue(target)
        offset = len(queue) + start if start < 0 else min(start, len(queue))
        offset = max(offset, 0)
        count = min(max(delete_count, 0), len(queue) - offset)
        removed = queue[offset:offset + count]
        queue[offset:offset + count] = list(inserted)
        return removed

    def append(self, target: str, text: str) -> None:
        """投递一条输入到指定队列尾部（受理即入队，尚未成为 LLM 可见的事实）。"""
        self.splice(target, len(self._queue(target)), 0, [text])

    def claim(self, target: str, turn: int) -> list[str]:
        """认领一批输入：**清空全部 next-step**；``target=next-turn`` 时再取队首 1 条 next-turn。

        顺序 = next-step 在前、next-turn 在后（照搬 A）：next-step 里的 steer 是更早投
        进来的，按时间顺序读给模型最自然。``turn`` 只进日志 —— 认领发生在哪一轮是排障时
        最想知道的那条信息（A 用 ``agent/inbox/claimed`` 事件做同一件事）。
        """
        claimed = self.splice(NEXT_STEP, 0, self.next_step_length, [])
        if target == NEXT_TURN:
            claimed += self.splice(NEXT_TURN, 0, 1, [])
        if claimed:
            logger.info(f"Inbox 认领 {len(claimed)} 条待投递输入（turn {turn}）")
        return claimed

    def clear(self) -> None:
        """丢弃全部待投递输入（先 next-step 后 next-turn，与 A 的 clear 同序）。

        调用方：AgentLoop.cancel（取消本轮即作废还没投递的输入）。被丢弃的消息不落
        任何事实 —— 它们从没进过 LLM 上下文，也没有人向用户承诺过「已投递」。
        """
        self.splice(NEXT_STEP, 0, self.next_step_length, [])
        self.splice(NEXT_TURN, 0, len(self._queues[NEXT_TURN]), [])

    # ── 内部 ──

    def _queue(self, target: str) -> list[str]:
        try:
            return self._queues[target]
        except KeyError:
            raise ValueError(
                f"未知的投递目标 {target!r}（应为 {NEXT_TURN!r} / {NEXT_STEP!r}）"
            ) from None
