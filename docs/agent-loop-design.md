# Agent Loop 设计（第三阶段：运行中输入 + 循环拦截点）

> 状态：**待拍板**（本文只定契约与形态，不含实现；§5、§6.3 的决策定了再动手）
> 参考：DeepSeek Harness `dsh-agent-loop`（`inbox.ts` 双队列 · `agent.ts` 四个 waterfall）· `dsh-agent`（事件契约）
> 关联代码：`backend/agents/runtime/agent_loop.py` · `agent_runtime.py` · `backend/api/agents.py`
> 前置：Phase 1（健壮性：finish_reason / 重试 / 闭合）· Phase 2（取消通道）已落地

## 1. 问题

B 的 `run(question, context)` 是**一次性入口**：问句进、答案出，中间没有任何输入窗口。
用户看着 Agent 跑偏，只能等它跑完（或点停止）再重问 —— 中间那几十秒的工具调用全白费。

A 用 inbox 解决：输入分三个动作、两个队列，在**步边界**被认领。本文把它的语义搬到 B。

## 2. 语义：三个动作，两个队列（照搬 A 的词汇）

| 动作 | 队列 | 唤醒 | 什么时候用 |
|---|---|---|---|
| `followup` | next-turn | 是 | "等我做完这个，再看这个" —— 本轮结束后作为**新的一轮**开始 |
| `steer` | next-step | 是 | "方向偏了，往这边走" —— 在**当前轮的下一步**并入上下文，模型立刻看到 |
| `inject` | next-step | 否 | 系统侧静默投递（只在已有轮次运行时生效，不唤醒） |

**认领时机 = 每步开头**（A 的 `preStep` → `inbox.claim(target, turn)`）：本轮第一步取
next-turn，之后每步取 next-step。认领到的消息在**本步的 LLM 请求之前**落成事实，
于是下一步的 `derive_messages()` 自然带上它。

**唤醒与闩锁**：空闲时 `followup`/`steer` 启动一条新轮；有轮次在跑/正在收尾时，
唤醒被闩住（`wakeRequested`），在轮次收敛后重放 —— B 的 Phase 2 取消引入了「轮次正在收尾」
这个状态，闩锁正是为它准备的。

## 3. 是否入 Event Log：**入，且分两层**（对齐 A 的 durable inbox）

A 的 inbox 是**持久化**的：待投递队列本身以 `agent/inbox/spliced` 事件回放，
由 `inbox` 投影 fold 出「还有哪些没投递」。

B 沿用同样的两层：

| 层 | 事件 | 类型 | 作用 |
|---|---|---|---|
| 队列 | `steer/queued`（`data: target + message`） | log-only | 「已受理、待投递」是**事实**，冷恢复后仍能投递 |
| 投递 | `user/message` | surface | 真正进 LLM 上下文（与现有注入路径同形，不新增 surface 类型） |

配套一个只读投影 `project_pending_input(events)`（放在 `session/projections/`）：
fold 出两个队列的当前内容，供 ① 认领时取队首 ② UI 显示"已发送、待投递" ③ 冷恢复后恢复队列。

**为什么不能只放内存**：Event Log 是执行事实的唯一来源（本仓库的第一原则）。队列里有什么、
什么时候投的，都是事实；只放内存意味着「进程重启后用户发过的消息凭空消失」，
而它明明已经被 202 受理过了。

代价：每次 steer 多一条 log-only 事件（零 token、不进 surface）。

## 4. 并发：同 session 单飞（**必须和 steer 同批做**）

**现状（未修复的既有漏洞）**：两个并发请求带同一个 `session_id`，`SessionStore.get()`
返回**同一个 Session 对象**，两个 Loop 同时往一个 Event Log 追加 —— turn/start 交错、
TraceProjection 的每轮 reset 互相踩、`derive_messages()` 的消息序错乱。
Phase 2 又添了一例：被取消的轮还在收尾，新的一轮已经开跑。

**方案：进程级 `RunRegistry`**（与 `SessionStore` 并列的 capability seam，同样是纯内存热区）

```python
class RunRegistry:
    def register(self, session_id: str, *, cancel: asyncio.Event) -> RunHandle   # 已被占用 → 抛 RunConflict
    def get(self, session_id: str) -> RunHandle | None
    def unregister(self, session_id: str) -> None                                # 在 reply() 的 finally 里
```

- 第二个 `reply()`（或第二个 `/chat/stream`）命中占用 → 抛领域错误（HTTP 层翻 409）。
- **拒绝而不是排队**：SSE 世界里排队 = "连接先挂着、轮次稍后才开始"，前端在排队期间拿不到
  任何反馈；真要排队，正确做法是 followup 队列（§2），不是 HTTP 层排队。
- 顺带成为**取消的统一入口**：Phase 2 的 cancel 信号现在由 SSE handler 自己 new 一个 Event，
  前端「停止」只能靠关连接（能工作，但语义上是副作用）。有了 registry：
  `POST /api/agents/sessions/{id}/cancel` → `registry.get(id).cancel.set()` ——
  停止按钮不必再依赖断开连接，也为"从别的页面停止"留了路。
- 生命周期与 listener 挂摘同规：`register` 在 reply 开头，`unregister` 在同一个 `finally`。

## 5. API 形态

```
POST /api/agents/{agent_key}/chat/steer
  body: { session_id, question, target?: "next-step" | "next-turn" }   # 缺省 next-step（= steer）
  202  { accepted: true, target, queued_seq }                          # 已受理（尚未投递）
  404  会话不存在
  409  该会话没有运行中的轮次（target=next-step 时）
```

- **单独端点，不扩 `/chat/stream`**：steer 不需要新的 SSE 流 —— 它喂的是**已经开着的那条**；
  返回「已受理」比再开一条连接诚实。
- 前端 `WorkspaceChat`：`sending` 时把发送键切成「并入当前轮」（走 steer 端点），
  否则走正常 `/chat/stream`。停止键（Phase 2 已接）保持不变。

### 5.1 需要拍板的三个决策

| # | 决策 | 选项 | 建议 |
|---|---|---|---|
| A | 投递的**可见性** | ① 只在后端静默投递；② 认领时往 SSE 流回一帧，前端把这条消息上屏 | ② —— 现在的 `user/message` 没有 UI 投影，静默投递会让用户"发完什么都没发生"。需要扩 `TrajectoryProjection`：`user/message` → `traj/open(user)` 节点 |
| B | `target=next-turn` 但**当前无运行轮次** | ① 直接当成一次新轮（A 的 followup 语义）；② 409 让客户端改用 `/chat` | ① —— followup 的语义本就是"下一轮"，没有当前轮时"下一轮"就是"现在" |
| C | 轮次结束时**未投递**的输入 | ① 丢弃（A 的 `cancel(keepInbox=false)` 默认）；② 提到下一轮并唤醒 | ② —— HTTP 已经回 202「已受理」，让它凭空消失是最差的体验；实现上就是把剩余 next-step 移到 next-turn 并唤醒 |

## 6. 循环拦截点（契约先行，实现排后）

### 6.1 两条通道分开

| 通道 | 现状 | 用途 |
|---|---|---|
| **观察** | `on_event`（UI 帧）+ `session_event_sink`（typed 事件 → 投影） | 只读广播，不改变执行 |
| **拦截** | **新增** `hooks`（构造 `AgentLoop` 时传入） | 在四个点上改变执行决策 |

两者刻意不合并：观察者可以随便加、坏了也不能影响执行（现有 listener 已按此实现）；
拦截者直接决定执行走向，必须显式装配、且装配方对后果负责。

### 6.2 四个点（对齐 A 的 waterfall）

| 点 | 签名 | 默认行为（不装配时） | 用途 |
|---|---|---|---|
| `pre_step` | `(view) -> PreStepDecision` | 继续（`{"kind": "enter"}`） | 拒答 / 改写本步输入（合规拦截、注入系统侧上下文） |
| `request` | `(view) -> dict` | 原样发送 | 改 payload：模型路由、max_tokens 动态升级、工具集裁剪 |
| `request_error` | `(err, attempt) -> bool` | `RETRYABLE_CODES` + `max_llm_retries` | 重试策略外置（现有策略成为第一个内置消费者） |
| `turn_stopping` | `(view) -> str \| None` | 收口 | 返回文本 = 注入一条用户消息**继续本轮**（"截断后自动续写"挂这里，Phase 1 商定） |

`hooks` 是**一个 dataclass，每点至多一个可调用对象**（None = 现行为）。B 没有 Cordis 的
waterfall 洋葱模型，也不需要：多元件要接同一点时，由装配方自己组合。

### 6.3 硬规矩（这些是契约的一部分）

1. **钩子抛异常 → 记 exception、按默认行为继续**。拦截器坏掉不能打死执行链 ——
   与 listener 同规。唯一的例外是取消（`CancelledError` 照常穿透）。
2. **钩子不产生事实**。Event Log 只由 Loop 写；钩子的决策通过既有事件体现
   （比如 `turn_stopping` 注入的文本会落成 `user/message`）。
3. **闭合不受钩子影响**：`turn/end`、`step/end` 仍在 `finally` 里，钩子再离谱也不会留下半途 turn。
4. 钩子是 async（允许 await IO），但每点每步最多调用一次；不得阻塞事件循环。
5. 拦截点**不改变** `stop_reason` 词表：钩子想表达"我否决了停止"，手段是注入输入让轮次继续，
   而不是发明新的停止原因。

### 6.4 落地顺序（建议）

1. **RunRegistry + 同 session 单飞** —— 先堵并发漏洞，独立于 steer，可以立刻做
2. **steer 端点**（§5 决策定了之后）：队列事件 + 投影 + 认领 + UI 上屏
3. **hooks 契约实现**：先做 `request_error`（把现有重试策略搬过去当第一个消费者），
   再用 `turn_stopping` 做"截断后自动续写"验证契约够用

## 7. 与已落地部分的关系

| 已落地 | 与本设计的关系 |
|---|---|
| Phase 1 的截断收口（`stop_reason=max_tokens`） | "自动续写"的实现点是 `turn_stopping`，不是重试 —— 契约定完就能挂 |
| Phase 2 的取消（`cancel` 信号 / `RunCancelled`） | §4 的 RunRegistry 是它更合适的持有者；取消语义不变 |
| Phase 2 的硬取消传播 | 钩子不得吞 `CancelledError`（§6.3 规矩 1） |
| 崩溃修复（`session/repair.py`） | steer 队列也需要 `repair.py` 同款待遇：冷恢复时未投递的输入不该消失（§3 的投影负责） |
