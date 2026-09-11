# 沙箱子系统设计

> **本文件描述「现在是什么」**：模块职责、不变量、一次调用的实际行为。
>
> **决策依据与分期路线在 `docs/sandbox-design.md`** —— 那里记的是「为什么这么设计」
> （词汇为什么只有一维、为什么用 Docker 而不是 bwrap、为什么不做命令预检）。
> 本文件不重复它的论证，只写改代码前必须知道的事实。

---

## 0. 一句话

用 Docker 短命容器把 agent 的 `bash` / `python` 关进一个**只挂会话工作区**的世界；
挡不住就**拒绝执行**，永远不裸跑。

---

## 1. 模块地图

| 文件 | 职责 | 谁消费 |
|---|---|---|
| `vocabulary.py` | `SandboxMode` 三档、`SandboxPolicy` / `SandboxExecutionPolicy`、构造期校验。**无逻辑，谁都不拥有它** | 所有层 |
| `provider.py` | `SandboxProvider` 抽象 seam + `ConfinedArgv` + `RunnerFailureRule` | 后端实现、executor |
| `backends/docker.py` | 唯一后端实现：`confine()` 把 argv 包成 `docker run`，并声明本后端的**拒绝方言**与 **runner 故障规则** | 装配层 |
| `policy.py` | 模式优先级链（纯函数） | `runtime.py`、executor |
| `escalation.py` | **完整升权链**：严格序表、参数校验、调用审批、两个模型可见标记。不认识 session / agent / **审批实现** | executor、`runtime.py` |
| `classify.py` | 结果分类：`denied` / `runner_failed` / `normal` | executor |
| `workspace.py` | 路径映射、工作区根解析与安全校验、**工作区内相对路径的越界校验**、只读挂载构造、功能探测 | 装配层、后端、`api/workspace.py` |
| `errors.py` | `SandboxUnavailableError` —— fail-closed 的唯一出口 | 全层 |
| `runtime.py` | 装配 + **能力开关** + 内建工具注册（`bash` / `python` 的 schema 在这里） | `main.py`、`api/tools.py` |
| `test_sandbox.py` | 自检：纯逻辑 + 真实 docker e2e（docker 不可用时自动跳过） | — |

---

## 2. 核心不变量

改这个子系统之前，这九条不能破：

1. **fail-closed 是硬约束**。`confine()` 只有两种结果：返回带约束的 argv，或抛
   `SandboxUnavailableError`。**静默的无约束透传永远不合法** —— 没有后端 ≠ 降级运行。
2. **策略随调用携带，不固定在 provider 上**。两个消费方可以同时用不同策略约束；
   「升权重试」只是**一次带更宽策略的新调用**。
3. **词汇封闭且只覆盖文件效果**。`read-only` / `workspace-write` / `danger-full-access`。
   网络、进程可见性、syscall、设备、凭据**都不在词汇内** —— 词汇越窄，后端能兑现的
   可能性越高、`enforcement` 才可判定。
4. **`enforcement` 是报告的事实，不是承诺**。后端诚实报告自己管辖了该模式承诺的
   全部文件效果（`full`）还是只管辖一个子集（`partial`）。
5. **拒绝与 runner 故障是两套正交方言，先查后者**。搞反了会把「沙箱坏了」误报成
   「命令失败了」。
6. **不做命令字符串启发式预检**。展开、子进程、符号链接无法静态理解 ——
   「真的跑一遍让内核裁决」是唯一可信的拒绝信号。
7. **不把沙箱模式写进系统提示词**。模型会因「我在只读沙箱里」而不敢尝试
   （上游实测：12 轮里 5 轮零工具调用）。拒绝标记与升权提示只在**使用点**出现。
8. **升权必须问人，没有「不问就放」这一档**。没有审批通道时升权恒不可用
   （等价 DSH 的 `approval=never`）—— 没有判定方 ≠ 默认批准。四条 fail-closed
   路径（无通道 / 无 agent / 超时 / 未知裁决值）全部落在「不开门」上。
9. **工作区只有一个写方（沙箱），读路径必须经 `resolve_workspace_member`**。
   用户侧的浏览器（`api/workspace.py`）**只读**，不提供写/删/改名 —— 少一个写接口
   就少一整类「模型正在写、用户正在删」的竞态。读侧的工作区内容是**模型**写的，
   即不可信输入源：绝对路径、`..`、以及 **`resolve()` 后越界的符号链接**
   （模型可以 `ln -s /etc/passwd evil`，字符串上毫无破绽）全部拒 400。
   纯字符串前缀检查**不合法**，必须 resolve 后再判 `is_relative_to`。

依赖方向：`tool_system/` **不依赖** `agents/` 层。会话相关的输入（session_id、cwd、
覆盖模式）由调用方以原始值传入。

---

## 3. 三档模式的实际行为（Docker 后端）

`confine()` 的差别就是 `docker run` 参数（`backends/docker.py`）：

| | `read-only` | `workspace-write` | `danger-full-access` |
|---|---|---|---|
| 根文件系统 | `--read-only` | `--read-only` | — |
| 工作区挂载 | `{root}:/workspace:ro` | `{root}:/workspace` | — |
| `/tmp` | 无 tmpfs（不可写） | `--tmpfs /tmp:rw,64m,exec` | — |
| 其余加固 | cap-drop ALL / no-new-privileges / pids / memory / cpus | 同左 | **无** |
| 走 provider | 是 | 是 | **否**（直接 spawn 原始 argv） |

**实测结论（别凭直觉改）**：

- **`2>/dev/null` 在 `read-only` 下照常可用**。Docker 把 `/dev` 挂成独立的可写 tmpfs，
  写设备节点不经 rootfs，`--read-only` 挡不住它。**不要"补"成 tmpfs `/dev`** ——
  那会连设备节点一起换掉。（上游 README 专门声明这一点，是因为它的同世界后端没有
  这层容器默认值；Docker 后端白送。）
- **`/tmp` 在 `read-only` 下确实不可写**，这是**设计意图**：临时区域是
  `workspace-write` 承诺的一部分，给了就模糊了两档的边界。
- `danger-full-access` **不是「更宽的沙箱」，是「完全不沙箱」**：命令以 backend 进程
  身份在 backend 容器里跑，能碰 `.env`、`DATABASE_URL`、`/var/run/docker.sock`
  （后者等价于宿主 root）。

**附加只读挂载**（`SANDBOX_READ_ROOTS`，对上游的刻意扩展）：每个宿主路径额外挂一条
`-v <root>:/mnt/read/<name>:ro`。挂载**恒为 `:ro`、与模式无关** —— 它只增加
「读得到」，不增加「写得进」，因此独立于 `SandboxMode`（模式词汇只描述写效果）。

---

## 4. 一次调用的完整链路

```
AgentLoop._tool_context(tool_call_id)
    ├─ session_policy()          → 解析「基线」模式，优先级：
    │      会话覆盖(sandbox/mode 事件投影) > SANDBOX_DEFAULT_MODE > 兜底 workspace-write
    └─ approval                  → 审批服务单例（未装配 → None → 升权恒被拒）
         ↓ ToolContext{policy, approval}
ToolRuntime.execute()            → 按 descriptor.type 选执行器 + 生命周期事件
         ↓
SandboxExecutor.execute()
    ├─ 1. build_sandbox_argv()   ← 先做参数校验（非法参数不留审计记录）
    ├─ 2. _apply_escalation()    ← 调 approve_escalation 走完整条链（见 §5）
    │     └─ approve_escalation()   校验 → 严格更宽 → 问人 → 返回目标模式
    │                              获批后优先级链的最后一段在这里生效：
    │                              resolve_policy(explicit_mode=...) —— 显式模式最高位
    │                              （非法请求 → 直接返回 error，命令不跑）
    ├─ 3. danger-full-access ? 直接 spawn : provider.confine() → spawn
    ├─ 4. classify_outcome()     ← runner_failed 优先于 denied
    └─ 5. 结果 = {exit_code, stdout, stderr, sandbox:{mode,enforcement,outcome}, notice?}
```

> 显式模式（升权）**不在** `session_policy()` 里 —— 它是逐调用的事实，只有执行器
> 同时握有 `args`、`policy`、`event_sink` 三样东西。`policy.py` 仍是唯一的解析实现，
> 执行器只是以 `explicit_mode` 再调它一次。

**工作区**是 `<SANDBOX_WORKSPACE_ROOT>/<session_id>`，首次调用时惰性创建，
容器内恒为 `/workspace`。宿主与 backend 容器**同路径挂载**，因为 `-v` 的源路径是
**宿主**路径（Docker-outside-of-Docker 的经典坑，见 `docs/sandbox-design.md` §8.1）。

---

## 5. 升权（escalation）

### 5.1 闸门过程

**先跑，被拒了才申请** —— 不做执行前预检：~90% 的命令在当前模式下本来就能跑通，
执行前问等于每条 `ls`、`cat` 都打断人。所以升权是**例外路径，不是常规路径**。

```
模型请求 sandbox_permissions
  │
  │  未请求 / 等同当前模式 → 直接返回 None（没事发生，审批方不会被调用）
  ▼
闸① 形状      两参数成对 + 理由非空整句      ✗ → EscalationInvalid
  ▼
闸② 严格更宽  查 WIDER_MODES 表             ✗ → EscalationInvalid
  ▼
闸③ 审批可用  无通道 → no-approval-channel
  │          无 agent → no-agent            ✗ → EscalationDenied
  ▼
闸④ 问人      approval.request(req)
  │          reason = "escalate sandbox to <mode>: <模型给的理由>"
  ▼
  allowed-once → 返回目标 SandboxMode        ← 唯一表示「同意」的值
  rejected     → human-refused
  cancelled    → cancelled
  unavailable  → unavailable（含超时）
```

**闸②要查的严格序**（`sandbox_permissions` 的 enum 不含 `read-only` —— 只往宽走）：

| 当前模式 | 可升到 |
|---|---|
| `read-only` | `workspace-write`、`danger-full-access` |
| `workspace-write` | `danger-full-access` |
| `danger-full-access` | **无条目**（已是最宽） |

**返回值只有三种**：获批 → 目标模式；没这回事 → `None`；失败 → 抛异常。
调用方因此不需要 `needs_human` / `pending` 之类的状态机 —— **审批不是状态，
是一次调用流程**。

**闸①②和闸③④的后果不同**，这个区别是刻意的：

- **闸①②（非法请求）→ 整条调用作废**：工具直接返回 error，命令不跑。与 DSH 同
  （`validateEscalationArgs` 在 `validateBashArgs` 顶部）—— 参数不对就不该跑命令，
  模型拿到错误文本照着改，而不是被静默降级。
- **闸③④（合法但没批下来）→ 回退到原策略照常执行**：模型该看到的是沙箱自己产出的
  拒绝标记，不是「升权被拒」这个替代错误。

### 5.2 四条要点

- **归一化不是可选优化**（上游 #4359 补丁）：去掉它，会话已在最宽模式时模型仍会
  反射性填字段 → 每次被打回 → 烧 token 甚至死循环。
- **没有「不问就放」**：上游审批策略只有 `ask`（问人）与 `never`（不问，直接拒）。
  没有通道时升权恒不可用 —— **没有判定方 ≠ 默认批准**。
- **一次性**：返回的 mode 只属于本次调用（只活在这次调用的 `args` 里）——
  不做 cache、不写持久权限、不需要任何清理。
- **回路熔断**：本次调用申请过升权且被拒 → 结果里**不再出现**升权提示，
  否则「被拒 → 提示 → 重试 → 又被拒」会成环。

### 5.3 审批能力在 interaction 层，不在 sandbox

```
sandbox/escalation      判断「这次要升到哪一档」   ← WIDER_MODES / 严格更宽 / 参数校验
        │  approval.request(req)
        ▼
interaction/approval    怎么问人、怎么等回答      ← Channel / Service / InProcessChannel
```

一句话分界：**sandbox 只管「是否需要提升权限」，approval 只管「如何请求批准、
如何等待决定」。** 沙箱的模式信息装进 `ApprovalRequest.metadata`，审批侧只透传、
不解释 —— 所以 approval 不认识 `SandboxMode` / `WIDER_MODES` / escalation 中的任何一个。

依赖方向单向，且**被测试守卫**：`test_approval_never_imports_a_domain_package`
扫 approval 目录源码，出现 `backend.tool_system` 之类即失败。换通道实现
（消息队列 / 外部审批服务）时升权链一行不用改。

三条 fail-closed：超时 → `unavailable`；调用方取消 → 抛 `CancelledError` 向上传播；
未知裁决值 → `rejected`（未知不是「还没决定」，不能让它悬着）。

### 5.4 审计：升权事件是**记录**，不是配置

| | `sandbox/mode` | `sandbox/escalation` |
|---|---|---|
| 性质 | **配置** —— 投影 fold 成有效覆盖，影响后续所有调用 | **记录** —— 只被审计读，**不进策略解析** |
| 数据 | `{mode}` | `{from, requested, to, granted, reason, justification, approval_id}` |

守住这条：一旦升权事件参与策略解析，**一次性授权就变成了持久放权**。
（有测试专门盯：`test_escalation_event_lands_in_session_log`。）

拒绝原因（机器可读，进事件）：`invalid-pairing` / `missing-justification` /
`empty-justification` / `not-strictly-wider` / `no-approval-channel` / `no-agent` /
`human-refused` / `cancelled` / `unavailable`。

---

## 6. 已知缺口

按严重度排：

1. **模式切换入口无认证**。`POST /api/agents/sessions/{id}/sandbox_mode` 无任何
   `Depends`。上游为此发过 CVE-2026-82533（9.4 分）：沙箱内的 agent 调**无认证的
   本地 Web 接口**把会话切成 `danger-full-access` 就逃逸了，**且不触发批准提示**
   —— 因为「改会话模式」不经过「严格更宽」检查。**同一个形状**。
2. **沙箱能直连数据库**。`--network bridge`（刻意不限网络）+ compose 把
   `5432:5432` 发布到宿主 + 明文凭据 `saas:saas` ⇒ 沙箱里能连宿主 5432。
   删掉 db 的 `ports` 发布即可（backend 走 compose 网络，不需要它）。
3. **binding 不进执行期校验**。`ToolRuntime.execute()` 只按名字 resolve，不查
   `agent_tool_bindings` —— 模型拼出未绑定的工具名照样能执行。绑定目前只是可见性。
4. **审批通道是进程内的**。多副本部署时裁决会落不到挂起的那台；需要换成共享存储。
   另外挂起期间 SSE 连接一直开着（与 DSH 的阻塞式弹窗同形态），长连接代理可能超时。
5. **`danger-full-access` 能通过升权到达**。人批了就放行 —— 而那一档是「把 docker.sock
   交给模型」。当前靠人工把关，语义上应当由 `ConfinedSandboxMode` 在词汇层划死。
6. **孤儿容器**。`docker CLI` 被 SIGKILL 时容器可能残留（`--rm` 依赖 CLI 存活）。
   需要更强保证时改用 `cidfile` + `docker kill`。
7. **镜像无 digest pin**。`python:3.12-slim` 是可变 tag。
8. **`read_roots` 不进事件**。`tool/result` 的 sandbox 事实只有 `{mode, enforcement,
   outcome}` —— 事后审计无法回答「这次会话读到了哪些宿主目录」。
9. **读路径有 TOCTOU 窗口**。`resolve_workspace_member` 先 `resolve()` 判越界，
   `FileResponse` 之后才真正打开文件 —— 中间模型可以在沙箱里把那个已解析的路径
   换成符号链接。威胁模型是「模型犯错」而非「本地攻击者抢 syscall」，故接受；
   要收紧得走 `O_NOFOLLOW` / `openat` 逐段打开，不值得现在的复杂度。

---

## 7. 改这个子系统时的检查表

- 新增模式？→ 改 `vocabulary.py` 的封闭词汇 + `WIDER_MODES`（**可升到**哪些档）。
  注意闸 4 恒问人，所以不需要「是否自动放行」的判断 —— 那一档不存在
- 新增后端？→ 实现 `SandboxProvider.confine()`，声明自己的 `denial_signatures` 与
  `runner_failure_rules`（**不要**跨后端取并集 —— 并集会声称本后端永远不会产生的拒绝）
- 改结果格式？→ 更新 `classify.py` 的标记 + `tool/result` 的 sandbox 事实 + 快照测试
- 动用户侧工作区读接口？→ 路径**只能**经 `resolve_workspace_member`（见不变量 9），
  别在 `api/workspace.py` 里自己拼路径；租户归属查 `conversations.session_id`，
  因为 `session_headers` 没有 tenant 列
- 动 `notice`？→ 记得走一遍「被拒 → 重试 → 再被拒」的时序，回路熔断在 `executor.py`
- 动升权？→ 三件事必须一起看：**链在 `approve_escalation` 里一次走完**（别拆回
  「先判断后审批」两段）、**`on_request` 回调必须在 await 之前**（否则前端看不到
  等待态）、**事件 schema 是记录不是配置**（进了策略解析就把一次性变成持久放权）
- 动审批？→ 那是 `backend/interaction/approval/` 的事，**不要在这里加实现**。
  本目录只 import 它的协议；越界会被 `test_approval_never_imports_a_domain_package`
  扫出来
- 跑测试：
  - `python -m backend.tool_system.sandbox.test_sandbox`（e2e 需 docker，不可用自动跳过）
  - `python -m backend.interaction.approval.test_approval`（审批本身，无依赖）
