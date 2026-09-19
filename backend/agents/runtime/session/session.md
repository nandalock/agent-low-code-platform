# 会话（Session）

一次对话的执行事实日志（Event Log），DSH 思想。对话历史、执行 trace、冷恢复全部由它
派生——不存在第二套存储。

文件头把边界钉死了：**不感知任何外部存储**（持久化是独立 seam）、**system prompt 不在
Event Log 里**（每轮组装）、**不负责**运行统计（trace）、Context Compaction、SubAgent
Session、Session 生命周期与持久化——这些都归各自的模块。

## 概述

```
SessionStore ──dict[str, Session]──> Session ──append──> Event Log ──> derive_messages ──> LLM
SessionPersistence（独立 capability seam）──append_events / flush──> PostgreSQL
                                            (session_headers + session_events)
```

五个设计要点：

| 要点 | 含义 |
|---|---|
| **Event Log 是唯一事实源** | LLM 消息不单独保存，`derive_messages()` 每次现派生——存了就有两个源，必然会漂移 |
| **Surface 过滤** | 只有 `user/message`、`assistant/message`、`tool/result` 进 LLM；过程事件留在 log 里 |
| **热冷分离** | Store 管运行态（内存热区），Persistence 管落库；`store.py` 明确禁止出现 `save_to_postgres` 之类的职责 |
| **system prompt 不在历史里** | 每轮由 SystemPrompt 组装、AgentLoop 前置为 `messages[0]`（见 [system-prompt.md](../system_prompt/system-prompt.md)） |
| **只增不减** | 目前没有 Compaction——消息历史没有任何裁剪、摘要或窗口管理（见「已知限制」） |

同一份 Event Log 服务四个消费者：LLM 消息（surface 派生）、执行 trace（TraceProjection）、
冷恢复（`from_events` replay）、UI 流式投影（listener 旁路）。四者都**消费**事实，不产生事实。

## 使用本模块

### 取 Session：三段式

恢复编排在 `AgentRuntime`（`agent_runtime.py:92-124`），Store 只提供原语：

| 情况 | 路径 | 说明 |
|---|---|---|
| **A** `session_id` + Store 命中 | `store.get()` | 进程内热 Session，跨请求保持上下文 |
| **B** Store miss + DB 命中 | `persistence.load()` → `Session.from_events()` → `store.put()` | **lazy restore**：启动不预载历史，按需恢复 |
| **C** 无 id / 两边都没有 | `store.create()` | 首轮新建；未知 id 命中 C 时打 **warning** 并把新 id 回传 |

C 的 warning 是刻意的：「未知 session_id 被静默续接」正是要避免的失忆场景——客户端必须
换用返回的新 id，否则每轮都在开新会话。

新建后**先固化 header 再跑对话**（`persistence.create`），因为沙箱策略链与工作区读取都靠
`header.cwd` 解析；进程中途崩溃时会话仍可被找回。

### 追加事件

唯一写入点：

```python
session.append(USER_MESSAGE, {"content": "..."})   # 返回 SessionEvent
```

自增 seq → 记 wall clock → 写 log → 过 surface → 同步广播 listener。listener 抛异常会被
隔离（只记日志），**一个死掉的 listener 不能断掉执行链**。

### 派生 LLM 消息

```python
messages = session.derive_messages()   # [{role: user|assistant|tool, ...}]
```

不缓存、不存储——同一条历史事件派生多少次结果都一样。

### 挂 listener（旁路消费）

```python
session.add_listener(on_event)
...
session.remove_listener(on_event)      # 幂等（不存在时静默）
```

典型用法见 `agent_runtime.py` 的 `reply()`：`TraceProjection` 与 UI 的 `session_event_sink`
只在**一次驱动**（`loop.followup()` → `await loop.when_idle()`）期间挂载，`finally` 统一
摘除——Session 被 SessionStore 跨轮复用，不摘会跨轮泄漏。

### 预创建会话（工作区场景）

`POST /api/workspace/sessions`（`workspace.py:553`）先建 `session_headers` 行（`cwd` = 所选
文件夹），再建 conversation 并回写映射。首次 chat 前端带上该 `session_id`，走**冷恢复路径**
B——沙箱执行侧的 `cwd or workspace_for_session` 链与读侧 `_resolve_session_workspace` 解析到
同一个文件夹，两边不分家。

**不写 seed**：预创建只负责 header，不存在与执行侧逐字对齐的镜像代码。

## 理解实现

### 源码地图

**顶层放的全是「核心」**——事件定义、Session 本身、Surface、Store。其余按用途分到
四个子包；`__init__.py` 是**整个包对外的唯一界面**，外部一律
`from backend.agents.runtime.session import X`，不必知道 X 在哪个子包里。

| 文件 | 职责 |
|---|---|
| `events.py` | 事件类型常量 · `SessionEvent` · `SessionHeader` · `SURFACE_EVENT_TYPES` |
| `session.py` | `Session` —— Event Log · Surface · listener 广播 · `derive_messages` |
| `surface.py` | `SurfaceManager` —— 决定哪些事件对 LLM 可见（规则集中，就一个 `SURFACE_EVENT_TYPES` 判断） |
| `store.py` | `SessionStore` —— 运行态生命周期 + 进程级单例 |

| 子包 | 职责 |
|---|---|
| `persistence/` | 落库 seam：`base.py` 接口 + `NoopPersistence` + 装配 + `flush_session_events`；`postgres.py` 生产实现 |
| `projections/` | **只读**派生视图：`sandbox.py` 沙箱模式覆盖 · `trace.py` 运行统计 · `trajectory.py` UI 轨迹 |
| `title/` | 会话标题：`normalize.py` 净化与字节截断 · `projection.py` fold 与合格输入 · `service.py` 接受与钉住 |
| `tests/` | 自检脚本（`tests/test_session.py` · `tests/test_title.py`） |

四个子包各自的 `__init__.py` 是**该文件夹的界面**：只做 re-export，逻辑留在具体文件里。

Store 与 Persistence 都不感知对方：`store.get()` 只查内存，冷恢复编排由调用方负责。

### 事件分类

`SURFACE_EVENT_TYPES = {SEED, USER_MESSAGE, ASSISTANT_MESSAGE, TOOL_RESULT}`

| 分类 | 事件 | 去处 |
|---|---|---|
| **surface**（进 LLM） | `user/message` · `assistant/message` · `tool/result` | `derive_messages()` |
| **过程**（log-only） | `turn/start` · `turn/end` · `step/start` · `step/end` · `assistant/chunk` · `tool/call` · `tool/progress` | UI 流式投影 / trace |
| **观测**（log-only） | `llm/usage` · `llm/error` | trace 聚合、缓存命中率；失败尝试（重试是事实，可回放） |
| **配置**（log-only） | `sandbox/mode` | `sandbox_projection` 折叠出当前有效覆盖 |
| **记录**（log-only） | `approval/request` · `sandbox/escalation` | 可查，**不参与策略解析** |
| **退役** | `session/seed` | 类型保留供内存构造，冷恢复丢弃 |

`assistant/chunk` 与 `assistant/message` 并存不是冗余：chunk 给前端打字机效果，完整
message 才是事实源。两者都进 surface 会让同一段话重复出现在上下文里。

`sandbox/escalation` **刻意不被任何投影折叠成配置**——一次性授权只对那次调用有效，若它参与
策略解析，一次性就变成了持久放权。（`sandbox/mode` 相反，它就是要被折叠的配置。）

### 冷恢复：`from_events`

```python
session = Session.from_events(header, events)   # 纯内存，不涉及存储
```

- 直接重建 `_log` / `_surface` / `_seq`，**不回放 `append()`**（避免重写 seq/time）
- **丢弃 SEED 事件**：system prompt 已不属于会话历史，照常回放会与前置的新 system 形成双
  system 消息（语义重复，且旧 persona 可能已被改过）
- 丢弃是**既不入 log 也不入 surface**——留着只会在每次 flush 时把死事件重新写库
- 冷恢复是唯一收口点：进程重启后 SessionStore 热区为空，存量会话必走此路径

紧接着还有一步**语义修复**（`repair.py` 的 `interrupted_turn_closers` → `append_recovered`）：
持久化只保证日志**物理**合法，上一轮若被中断（进程没了），库里可能留下没有 `turn/end`
的 turn、或永远等不到结果的 `tool_calls`。装配层（`AgentRuntime._repair_interrupted_tail`）
补合成 `tool/result`（结论如实写「结果未知 / 未执行」，不假装成功）、`step/end`、
`turn/end`，seq 续在末尾、**time 复用中断那一刻**（用恢复时的钟会把陈年崩溃渲染成巨大 latency）。
补记的事件随下一次 flush 落库，`stop_reason=interrupted`（与 `cancelled` 区分：前者是进程没了，后者是有人让它停）。

### 持久化边界

```
turn 完成（AgentRuntime.reply 收尾）
  → session.events
  → persistence.append_events(session_id, events)   # 进 pending，不承诺落盘
  → persistence.flush(session_id)                   # pending → durable，一个事务
  → 发出 done 事件
```

`append_events` 与 `flush` 分开是刻意的：一次 flush 是一个事务（全成功或全失败），失败时
pending 保留、下次幂等重放。**AgentLoop 不感知任何存储**——事件由 Session 收全，Runtime
一次性交给 Persistence。

`done` 在 flush **之后**发，所以客户端收到 done ≈ 本 turn 已 durable（flush 失败仅记日志）。

### 两张表（`core/schema.py`）

`session_headers` —— 一行一个会话：

```sql
session_id       TEXT PRIMARY KEY,
version          INT NOT NULL DEFAULT 1,
created_at       DOUBLE PRECISION NOT NULL,
cwd              TEXT,          -- 会话绑定的工作文件夹（沙箱策略链靠它解析）
parent_session   TEXT,          -- SubAgent 预留，无消费方
seed_length      INT,           -- 已退役字段：新会话写 0 / NULL
delegation_depth INT,           -- SubAgent 预留
updated_at       TIMESTAMPTZ DEFAULT now()
```

`session_events` —— 一行一个事件（Event Log 本体）：

```sql
id                BIGSERIAL PRIMARY KEY,
session_id        TEXT NOT NULL REFERENCES session_headers(session_id) ON DELETE CASCADE,
seq               INT NOT NULL,           -- 会话内自增序号，排序依据
event_type        TEXT NOT NULL,          -- 'user/message' / 'assistant/chunk' / 'tool/result' ...
event_time        DOUBLE PRECISION NOT NULL,   -- wall clock，latency 由相邻事件时间差推导
data              JSONB NOT NULL DEFAULT '{}', -- 事件 payload，按类型约定结构
source_event_seqs JSONB,                  -- 派生来源（扩展位，暂无派生事件）
created_at        TIMESTAMPTZ DEFAULT now(),
UNIQUE(session_id, seq)
```

**为什么拆两张**：header 一行，事件 N 行；合在一张表里等于每条事件都重复一遍 `cwd` /
`created_at` / `version`。

**为什么 `data` 用 JSONB 而不是按事件类型拆表**：`schema.py` 的注释写明了——「不按事件
类型拆业务表」。事件类型有十几种且还在增（见「事件分类」一节），拆表意味着每加一种类型
都要写迁移。

**两条约束各有用途**：

| 约束 | 作用 |
|---|---|
| `UNIQUE(session_id, seq)` | 兜底并发/幂等——配合 `INSERT ... ON CONFLICT DO NOTHING`，重复 flush 天然安全；跨进程写同一会话时拒绝冲突（本实现不引入分布式锁） |
| `ON DELETE CASCADE` | 删 header 连带删该会话全部事件（⚠️ 别拿它当删单条事件的手段） |

**本表是原始事实，不是投影**：投影（trace / trajectory / 沙箱模式）每次读时现算，不落库
（三个 projection 文件里零 INSERT）。所以行数 ≈ 事件条数，不是对话长度——多数行是
`assistant/chunk` 这类过程事件。

### ⚠️ 别混淆：`messages` 表不是 Event Log

库里另有一张 `messages` 表（`schema.py:174`），名字很像但**完全是另一回事**：

| | `session_events` | `messages` |
|---|---|---|
| 写入方 | `Session.append()`（执行侧） | `chat_service.create_message()`（API 层，`api/agents.py:300/323`） |
| 粒度 | 全量事件（含 chunk、usage、工具细节） | 只存用户问 + Agent 答 |
| 读取方 | LLM 上下文 / trace / 冷恢复 | UI 会话列表与消息列表（`services/chat/service.py`） |
| 关系 | **唯一事实源** | 独立副本，**不是它的投影** |

两条写入路径各写各的。看「上下文」查 `session_events`，看「UI 显示成什么样」查
`messages`。

## 模型体验

### 15 条事件 → 4 条消息

一轮「问行数 → 调 bash → 回答」的完整事件流：

```
 1  turn/start        {turn: 1}
 2  user/message      "data.csv 有多少行？"              ← surface
 3  step/start        {step: 1, total: 10}
 4  assistant/chunk   {"kind": "text", "delta": "我看"}
 5  assistant/chunk   {"kind": "text", "delta": "一下"}
 6  assistant/message {"content": "我看一下", "tool_calls": [...]}   ← surface
 7  tool/call         {tool_call_id: "call_a1", name: "bash"}
 8  tool/result       {tool_call_id: "call_a1", "content": "[exit code: 0]\n42 data.csv"}  ← surface
 9  step/end          {step: 1}
10  step/start        {step: 2, total: 10}
11  assistant/chunk   {"kind": "text", "delta": "有 42 行"}
12  assistant/message {"content": "data.csv 有 42 行。"}            ← surface
13  step/end          {step: 2}
14  turn/end          {stop_reason: "stop"}
15  llm/usage         {prompt_tokens: 812, ...}
```

`derive_messages()` 输出：

```python
[
  {"role": "user", "content": "data.csv 有多少行？"},
  {"role": "assistant", "content": "我看一下",
   "tool_calls": [{"id": "call_a1", "type": "function",
                   "function": {"name": "bash", "arguments": "..."}}]},
  {"role": "tool", "tool_call_id": "call_a1", "content": "[exit code: 0]\n42 data.csv"},
  {"role": "assistant", "content": "data.csv 有 42 行。"},
]
```

`AgentLoop._get_messages()` 再前置 system prompt 组成完整请求。几个要点：

- `tool/call` 也在日志里但**不在 surface**——工具调用由 `assistant/tool_calls` + `tool/result`
  配对表达就够了，第三条冗余
- `llm/usage` 不进上下文（观测数据）
- 第二轮追问时前面 4 条**原样重现**，新消息接在后面——「消息不存、每次派生」的实际效果

## 已知限制与延期工作

- **没有 Context Compaction**：模块 docstring 明确列为「不负责」。上下文只增不减，没有裁剪/
  摘要/窗口管理——`tool/result` 里的沙箱输出、文件内容全量进 messages。长会话会一直涨到
  撞模型窗口
- **`session/seed` 已退役**：主链路无写入方，`session_headers.seed_length` 恒 0 / NULL，
  冷恢复不回放。类型保留只为内存构造（测试/兼容）
- **`delete()` 只移出热区**，不删库——历史事件永久保留（由 Persistence 决定，当前不删）
- **冷恢复是全量 replay**：`load` 读完整 Event Log，成本随会话长度增长
- **记忆系统未接入**：`services/memory` 尚未进入主链路，跨会话记忆不存在
- **按规格暂不实现**（`events.py` 文件头列明）：`steering/message`、`todo/write`、
  `request/header`、`compaction`、`delegation`
- **SubAgent 只有字段**：`header.parent_session` / `delegation_depth` 预留，无消费方

## 开发备注

### 自检

```bash
docker compose exec backend python backend/agents/runtime/session/test_session.py
```

覆盖：从会话事件派生消息、tool 配对保持（assistant.tool_calls ↔ tool 结果）、seed 内存派生
仍可用、`from_events` 丢弃 seed（surface 与 log 两处都不留）、无 seed 重建不变、按 seq 排序、
Store 建会话不带 seed。

（容器内没有 pytest，测试文件自带 `main()` 运行器——与仓库其它测试一致。）

### 排查

```bash
# 历史会话回放：think/tool/answer 序列 + usage
curl -H "X-Tenant-ID: 1" http://localhost:8000/api/agents/sessions/<session_id>/trajectory

# 会话级沙箱模式
curl -H "X-Tenant-ID: 1" http://localhost:8000/api/agents/sessions/<session_id>/sandbox_mode
```

直接查库（库/用户/密码都是 `saas`）：

```bash
docker compose exec db psql -U saas -d saas

# 事件类型分布（行数大头通常是 assistant/chunk，不是对话消息数）
SELECT event_type, count(*) FROM session_events GROUP BY 1 ORDER BY 2 DESC;

# 某会话的完整事件流
SELECT seq, event_type, data FROM session_events
WHERE session_id = '<id>' ORDER BY seq;

# 只看真正进 LLM 的三类
SELECT seq, event_type, data FROM session_events
WHERE session_id = '<id>'
  AND event_type IN ('user/message','assistant/message','tool/result') ORDER BY seq;
```

日志线索：

- `冷恢复 Session ...: replay N events` —— 走了路径 B，注意 N 的增长
- `冷恢复丢弃 N 条 seed 事件` —— 存量会话带了旧 seed（debug 级）
- `session_id=... 在 SessionStore 与持久化存储中均不存在，将创建新 Session` —— 命中路径 C，
  客户端该换 id 了

### 注意

- **改事件类型要同时看三处**：`SURFACE_EVENT_TYPES`（是否进 LLM）、各 projection 的消费、
  `postgres.py` 的 `data` 落库（`::jsonb` 写入 + psycopg2 自动反序列化回 dict，注意
  `default=str` 兜底非 JSON 类型）
- **listener 必须成对挂/摘**：Session 跨轮复用，漏摘会跨轮泄漏（`agent_runtime.py` 的
  `finally` 是范例）
- **`Session` 不感知存储**：不要在 Session / Store 里加任何落库调用，持久化是独立 seam
- 改后端代码后 `--reload` 可能卡死，需 `docker compose restart backend`
