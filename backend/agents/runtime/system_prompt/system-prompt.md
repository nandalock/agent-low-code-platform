# 系统提示词组装（SystemPrompt）

分段注册 + 每轮组装的系统提示词模块，移植自 DeepSeek Harness 的
`packages/core/system-prompt`。

## 概述

system prompt 是每轮请求从已注册的贡献现算出来的产物，而不是一个预先存好的字符串：

```
AgentRuntime.reply
  ① 取 Session（热命中 / 冷恢复 / 新建）
  ② assemble(ctx)   ← 求值全部贡献：提示词段、动态上下文、可见工具及其使用指导
  ③ render          ← 严格插值成最终文本
  ↓
AgentLoop
  messages[0]    = system prompt（组装产物）
  messages[1..]  = session.derive_messages()（对话历史）
  payload["tools"] = assembly.tools（工具 schema）
```

四个设计要点：

| 要点 | 含义 |
|---|---|
| **四类贡献** | 段（section）、动态上下文（context）、变量（variable）、工具来源（tool provider）各自注册，组装时汇合 |
| **具名 order** | 位置集中分配：段在前、动态上下文在尾部——尾部变化不断前缀缓存 |
| **严格渲染** | 未知变量、无值变量、畸形引用一律抛错，绝不把格式错误的提示词发给模型 |
| **工具双通道** | schema 走 `payload["tools"]` 供 API 解析；使用指导走提示词供模型阅读 |

每轮重算带来三个直接好处：改配置立即生效（不必等新会话）、上游 context 与工具集
始终反映当前状态、预创建会话与执行侧共用同一份组装逻辑（无镜像代码）。

## 使用本模块

业务侧通常**不需要**写任何代码——`platform_sections.py` 已在包 import 时注册好
身份、角色、上游上下文、变量与工具来源。需要扩展时才注册新贡献：

### 贡献提示词段

```python
from backend.agents.runtime.system_prompt import PromptSection, section, SECTION_ORDERS

section(PromptSection(
    name="my:rule",                          # 唯一名，重复注册抛错
    order=SECTION_ORDERS["PLATFORM_BEHAVIOR"],
    text="涉及金额的操作必须先向用户确认。",   # 静态文本
))
```

`text` 也可以是 provider（每轮求值，读 `AssembleContext`）：

```python
section(PromptSection(
    name="my:dynamic",
    order=500,
    text=lambda ctx: f"当前租户是 {ctx.tenant_id}。",
))
```

### 贡献动态上下文

逐轮变化的内容放 context（排在提示词尾部，不破坏前缀缓存）：

```python
from backend.agents.runtime.system_prompt import PromptContext, context, CONTEXT_ORDERS

context(PromptContext(
    name="my:facts",
    order=CONTEXT_ORDERS["MEMORY_CONTEXT"],
    text=lambda ctx: render_facts(ctx) or "",   # 返回空串 = 本轮无此上下文
))
```

### 贡献提示词变量

段文本里写 `{{name}}`，取值由注册方提供：

```python
variable("cwd", lambda ctx: ctx.session.header.cwd if ctx.session else None)
```

- 变量名须匹配 `[a-z][a-z0-9_]*`
- 返回 `None` = 本轮无值 → 引用它的段**渲染失败**（刻意：与其渲染成空串误导
  模型，不如响亮报错）
- 返回 `""` 是合法值（插值为空，可能让整段消失）

### 贡献工具

```python
tools(my_provider)   # provider: (ctx) -> ToolProviderResult
```

`ToolProviderResult.schemas` 是发给 LLM API 的工具定义，`.guidance` 是
「工具名 → 使用指导」——后者会被组装成 `tool:<name>` 提示词段。**两者是两样
东西，永不合并**（详见下一节）。provider 允许 async（远程 MCP 工具要懒加载）。

### Order 常量表

| 段（`SECTION_ORDERS`） | 值 | 谁在用 |
|---|---|---|
| `AGENT_IDENTITY` | -1000 | identity（从 agent name+description 自动生成） |
| `AGENT_PERSONA` | 0 | persona（`config.system_prompt`，管理台可编辑） |
| `PLATFORM_BEHAVIOR` | 500 | 平台级通用规则（预留） |
| `ENVIRONMENT` | 800 | 环境事实，如沙箱模式/工作区（预留） |
| `TOOL_GUIDANCE` | 1000 | 全部工具指导（同号，按工具名排序保证确定性） |

| 上下文（`CONTEXT_ORDERS`） | 值 | 谁在用 |
|---|---|---|
| `MEMORY_CONTEXT` | 8900 | 记忆：画像/摘要/近期对话（预留） |
| `UPSTREAM_CONTEXT` | 9000 | 上游节点输出（workflow 场景） |

**context 的 order 恒大于全部 section** —— 「稳定前缀 + 动态尾部」由此成为
结构保证而非调用约定。新增内容选一个空号段，已有号永不改动（改号 = 所有人的
缓存前缀从改动点失效）。

## 理解实现

### 两种「工具相关」的产物

这是本模块最容易被误解的地方：

```
SystemPrompt                          ToolRegistry
└── tool:bash                         └── bash
      ↓                                    ↓
   bash 使用规则                        bash Tool Schema
   （自然语言，模型读）                  （JSON，API 解析）
      ↓                                    ↓
  messages[0].content                  payload["tools"]
```

同名，但**分属两层、走两条通道、永不合并**：

- schema 拼进提示词文本 → 变成需要模型自己解析的散文，准确率下降，且与
  `tools` 参数里的同一份定义重复（改一处漏一处）
- 「什么时候该用」「失败怎么办」这类跨调用习惯，根本写不成 JSON Schema

工具使用时，guidance 段只在**该 agent 绑定了该工具**时出现；工具被限制
（未绑定）→ schema 与说明书同时消失，不存在「说明书还在、工具没了」的漂移态。

### 源码地图

| 文件 | 职责 |
|---|---|
| `section.py` | `PromptSection` —— 一段提示词文本 |
| `context.py` | `PromptContext` —— 一段动态上下文（与 memory 的同名类不同义） |
| `variable.py` | `PromptVariable` + 变量名规则 |
| `tool.py` | `ToolSchema` / `ToolProviderResult` / `ToolProvider` |
| `system_prompt.py` | 注册表 + order 常量分配 + 模块级单例 |
| `assembler.py` | 组装流水线 + 严格渲染 |
| `platform_sections.py` | 平台内容（identity/persona/上游/变量/工具指导） |

框架六文件保持零业务依赖（可独立单测），平台文案全在 `platform_sections.py`
——「改文案」与「改组装机制」由此分开。

### 组装流水线

顺序不可调换：

```
① 求值 tool providers        → 可见工具集合 + guidance 字典
② 回填 ctx.visible_tool_names → section provider 依赖它做条件渲染
③ 求值 section providers + 为「可见且有 guidance 的工具」合成 tool:<name> 段
④ 按 (order, name) 排序
⑤ complete 裁决（>1 抛错；恰 1 → 成为唯一段）
⑥ 求值 context providers 并排序
⑦ 求值 variables
   ↓
PromptAssembly（已求值、未插值）
   ↓ render：插值 → 丢空段 → 空行连接
最终系统提示词
```

① 必须在 ③ 之前：段里的工具指导要知道「本 agent 实际可见哪些工具」。
排序同 order 时按名字的**码点序**（Python 字符串比较）——跨机器结果一致，
不依赖任何 locale。

### 严格渲染规则

`{{name}}` 的完整语义：

| 情况 | 行为 |
|---|---|
| 完整组且变量有值 | 替换 |
| 未注册的变量名 | **抛错**，列出全部已注册名 |
| 已注册但 provider 返回 `None` | **抛错**（"本轮无值"） |
| provider 返回 `""` | 合法，插值为空 |
| `{{}}` / `{{a b}}` 等组名非法 | **抛错**（附 16 字符片段） |
| `{{` 之后还有 `}}` 但组不完整 | **抛错**（畸形组） |
| 孤立未闭合的 `{{` | 视为字面行文 |
| 替换后的值里含 `{{` | 原样保留，**不再重扫描** |

错误消息一律指明 owner（段名/上下文名）。严格是刻意的：一个拼错的
`{{modle}}` 若被静默替换成空，会带着错误行文发给模型，只有事后翻记录才能
发现——格式错误的提示词比响亮失败更糟。

### 设计取舍

| 维度 | DSH（参考实现） | 本项目 |
|---|---|---|
| 作用域 | global + agent scope 遮蔽，waterfall 按 scope 分发 | 无 scope 层：agent 差异由 provider 读 `AssembleContext` 自行处理 |
| 动态上下文去向 | 渲染成独立的 user 角色快照，追加在历史之后 | 并入 system 消息的尾部（本项目消息结构更简单，够用） |
| 工具指导来源 | 每个工具插件自己调 `section()` 注册 | 内置工具写在 `ToolDescriptor.usage_guidance`；平台自有 MCP 工具在 `PLATFORM_TOOL_GUIDANCE` 兜底 |
| 组装时机 | 每个模型步骤 | 每个 turn（AgentLoop 内多步共用同一份） |
| complete 段 | 完整逃生阀 | 字段保留，平台层未注册 |

## 模型体验

### 模型看到什么

以 `paper_agent` 为例（`GET /api/agents/paper_agent/prompt-preview` 的实际输出）：

```
[-1000] 你是「PaperAgent」。学术论文研究助手 — arxiv 搜索 + PDF 全文总结成 markdown
[    0] 你是学术论文研究助手。帮助用户查找 arXiv 论文并总结成 markdown。流程：…
[ 1000] 优先用 bash 完成文件与进程操作。执行前先确认当前目录与目标路径；命令失败时先读
        输出里的退出码与 stderr，不要原样重试。
[ 1000] 用 python 做需要计算、解析或数据清洗的任务。脚本写入会话工作区后执行；报错带行
        号时先读对应代码行再修复，不要反复提交未修改的脚本。
[ 9000] 【上游节点输出，供你参考】…（仅 workflow 场景，且仅首轮有上游时有内容）
```

同时 `payload["tools"]` 收到 6 个工具 schema：`bash` / `python` /
`fetch_paper_text` / `list_papers` / `search_papers` / `summarize_paper`。

### Token 与 KV cache 影响

system prompt 每次请求都完整发送，成本随内容增长（固定开销，与对话轮数无关）。

**前缀稳定性是本模块的核心约束**：

- 前部段只应引用**跨轮稳定**的信息（部署事实、agent 配置、会话 cwd），
  逐轮变化的内容（上游输出、记忆、用户问题）放尾部 context
- 引用逐轮变化变量的段，会让 KV cache 前缀每轮从该点失效
- `AgentRuntime` 已有命中率观测（`llm_calls` / `cache_hit_ratio`），低于 0.5
  会打 warning —— 提示词或工具集改动会从改动点起断缓存，这是排查入口

实测（`paper_agent` 连续两轮）：首轮 `cache_hit_ratio=0.0`（无缓存），
第二轮 **0.84**（1280 tokens 前缀命中，仅新消息未命中）——稳定前缀被复用。

## 已知限制与延期工作

- **complete 段没有用户入口**：字段已实现（多个有效 complete 段使组装失败），
  平台层未注册；管理台若要支持「用户 prompt 完全接管」再启用
- **无 `{{` 字面量转义语法**：真实提示词若需要，再做
- **RouterRuntime / FaqAgent 未接入**：Router 的 L3 提示词仍是模板拼装
  （`router_runtime.py` 的 `_L3_BASE_PROMPT`），FaqAgent 的 RAG 仍自组 messages
  （`faqagent/agent.py`）。`prompt-preview` 对 router 类型返回 400 如实说明
- **记忆系统未接入**：`services/memory` 尚未进入 AgentRuntime 主链路；
  `MEMORY_CONTEXT`(8900) 槽位已留，接入时注册一个 provider 即可
- **工具指导的兜底表会漂移**：`PLATFORM_TOOL_GUIDANCE` 按工具名硬编码，MCP
  server 侧改名不会同步。外部 server 建议自带 `usage_guidance`

## 开发备注

### 自检

```bash
docker compose exec backend python backend/agents/runtime/system_prompt/test_system_prompt.py
docker compose exec backend python backend/agents/runtime/system_prompt/test_platform_sections.py
docker compose exec backend python backend/agents/runtime/session/test_session.py
docker compose exec backend python backend/agents/runtime/tests/test_agent_runtime_prompt.py
```

（容器内没有 pytest，测试文件自带 `main()` 运行器 —— 与仓库其它测试一致。）

覆盖：注册期校验（重名/非法 order/非法变量名/保留前缀/跨 provider 重名工具）、
组装期（complete 裁决、provider 返回类型、阶段序与可见工具回填）、渲染期
（严格插值的全部 8 种情况、排序确定性、上下文恒在段后）、平台层（identity 两种
形态、persona 空段消失、上游格式 golden、变量取值、Registry 未装配时的降级）、
集成（system 前置形态、不进历史、跨轮稳定、上游在尾部）。

### 排查提示词问题

```bash
curl -H "X-Tenant-ID: 1" "http://localhost:8000/api/agents/paper_agent/prompt-preview?question=xxx"
```

返回分段明细（名称/order/文本）、完整提示词、可见工具、变量取值。

### 注意

- `PromptContext` 与 `services/memory/hooks.py` 的同名类**不同义**（后者是记忆
  系统的状态对象）。同文件需要两者时用 import 别名
- 段名 `tool:` 前缀是保留名空间（留给工具指导段），注册段不得占用
- 改后端代码后 `--reload` 可能卡死，需 `docker compose restart backend`
