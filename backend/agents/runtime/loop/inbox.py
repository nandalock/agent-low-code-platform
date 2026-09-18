"""运行中输入（Inbox）— Phase 3 的落点，**尚未实现**。

现在 run(question, context) 是一次性入口，中间没有输入窗口：Agent 跑偏了只能等它跑完
或点停止。契约已定、实现未做（见 docs/agent-loop-design.md §2–§4，对齐 A 的 inbox.ts）：

  followup → next-turn 队列，本轮结束后作为新的一轮开始
  steer    → next-step 队列，当前轮的下一步就并入上下文
  inject   → next-step 队列但不唤醒（系统侧静默投递）

每步开头认领；受理落 log-only 的 steer/queued（进程重启后仍能投递），投递走
user/message（surface，不新增 surface 类型）。同 session 单飞必须与它同批做 ——
两个并发请求带同一 session_id 会交错写同一份 Event Log。

本文件刻意不含实现：结构先就位，拍板后往里填。
"""
