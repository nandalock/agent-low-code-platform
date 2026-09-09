# Sandbox 设计（第一阶段）

> 状态：设计已定稿，待实现
> 参考：DeepSeek Harness `dsh-sandbox`（seam / 词汇 / fail-closed / 分类方言）
> 关联代码：`backend/tool_system/`、`backend/agents/runtime/session/`

## 1. 背景与威胁模型

**威胁模型**：防止 Agent 自主执行工具时造成误操作。**不是**执行完全不可信的第三方代码。

这个区分决定了一切。DeepSeek 的 `dsh-sandbox` 是「同世界子进程约束」——命令与宿主共享内核和文件系统，只靠内核级 LSM / 命名空间 / ACL 挡住文件写。它明确声明：容器、microVM、远程执行**不是这个 seam 的后端**，它们要替换掉整个能力 seam。我们采用同样的定位。

**与 DeepSeek 场景的一处关键差异**：我们的 backend 跑在容器里，容器内有 DB 密码、LLM API key、源码。DeepSeek 的场景是「用户自己的机器，读什么都无所谓」；我们这里 `read-only` 仍然允许读整个文件系统。因此第一阶段就必须明确：**沙箱挡住的是「写」和「进程/网络逃逸」，不是「读」**。敏感信息的读取边界属于后续阶段（见 §9）。

**当前现状**：

| 事实 | 位置 |
|---|---|
| 项目无任何代码/命令执行工具 | `descriptor.type` 仅 `"mcp"`（`registry/descriptor.py:18`） |
| 预留了执行器扩展点 | `runtime/runtime.py:41-42` 的 `executors` dict |
| 唯一任意命令执行面是 MCP stdio，零隔离 | `adapters/mcp.py:49-50`，且 `mcp_servers.command` 可由 `/api/mcp/import` 写入 |
| 会话工作区未落地 | `SessionHeader.cwd` 恒为 `None`（`session/events.py:53`） |
| 无审批服务 | 升权机制因此延后到阶段 2 |

## 2. 设计原则

从 DeepSeek 借鉴（逐条对应其决策依据）：

1. **词汇窄到后端一定能兑现**。`SandboxMode` 只覆盖文件效果——不是能力不足，是让「后端是否兑现了承诺」可判定。
2. **fail-closed 是硬约束**。`confine()` 要么返回带约束的 argv，要么抛 `SandboxUnavailableError`。**永远不静默无约束放行**。没有后端 ≠ 降级运行。
3. **策略随调用携带，不固定在 provider 上**。两个消费方可同时用不同策略；升权重试只是「用更宽策略发起的一次新调用」。
4. **enforcement 是报告的事实，不是承诺**。`full` / `partial`，诚实报告缺口。
5. **拒绝与 runner 故障是两套正交方言**。拒绝 = 沙箱正常工作挡住了操作；runner 故障 = 沙箱自己坏了、命令根本没跑。消费方**先查后者**。
6. **不做命令字符串启发式预检**。展开、子进程、符号链接无法静态理解；「真的跑一遍让内核裁决」是唯一可信的拒绝信号。
7. **不把沙箱模式写进系统提示词**。DeepSeek 交付后回滚过：模型会因「我在只读沙箱里」而不敢尝试，12 个轮次里 5 个零工具调用。拒绝标记在使用点出现即可。

我们对 DeepSeek 的**刻意偏离**：

| 偏离 | 原因 |
|---|---|
| `policy.py` 做成纯函数而非服务 | `tool_system/` 不应依赖 `agents/runtime/session/` |
| 后端用 Docker 而非 bwrap/Landlock | 见 §2.1 |

### 2.1 为什么第一阶段用 Docker

- **沙箱世界 ≠ backend 世界**：backend 容器里挂着整个仓库（`docker-compose.yml:19` 的 `.:/app`）、`.env`、DB 密码。同世界约束会把沙箱正好安放在密钥和源码的所在地；Docker 沙箱只挂独立工作区，两者物理隔离。这是 Docker 方案最关键的价值（见 §9.2）。
- Windows 开发机与 Linux 生产环境行为一致（Landlock/bwrap 在 Windows 上不可用）
- 与现有 docker-compose 部署同构
- 文件 / 进程隔离一次到位（网络 v1 不限制，见 §3）
- 后续加 Landlock / Bubblewrap 后端时 `SandboxProvider` 接口不变

代价：每次调用 ~200-500ms 启动开销（v1 用 `--rm` 保干净，性能优化等有真实数据再做）；需要 docker socket（见 §9）。

## 3. 为什么不加网络轴

v1 **不引入网络轴**，与 DeepSeek 保持一致：`SandboxMode` 只覆盖文件效果，网络不在词汇内，命令照常联网。

这个决定不是随手选的，而是「词汇窄到后端一定能兑现」这条原则的直接后果，记录如下。

### 3.1 enforcement 字段的语义前提

DeepSeek 的 `ConfinedArgv` 只有**一个** `enforcement` 字段，它的语义是：

> `full` 表示**该后端管辖了这个模式承诺的每一种效果**。

「每一种」只有在**词汇是一维的**时候才成立。DeepSeek 能把词汇压到一维（只有文件效果），是因为它随附的每个后端——bwrap、Landlock、Seatbelt、Windows ACL——**都能管辖文件效果**；它们之间的差异只是同一维度内的完整度（旧内核 ABI、Windows 的 Everyone 缺口），而不是维度。

### 3.2 加网络轴会打破什么

**破坏点一：一个字段无法描述两个维度的不同完整度。** 将来的 `LandlockProvider` 会是「文件 full、网络 partial」，而 `enforcement` 只有一个格子——报 `full` 则对网络撒谎，报 `partial` 则对文件过度保守。修法是把 enforcement 拆成两个字段。

**破坏点二：`restricted` 这个取值本身兑现不了。** Docker 的 `--network` 只有两档（`none` / 有连通性）；「域名白名单 / 端口白名单」需要出口代理 sidecar 或 iptables 规则，**任何列出的后端都兑现不了**。一个无法兑现的枚举值会诱使后端「假装兑现」，而这正是 `enforcement` 机制想防的事。

**破坏点三（决定性）：Landlock 的网络能力只是部分的。** ABI v4（Linux 6.7+）才第一次加网络管控，只有 `LANDLOCK_ACCESS_NET_BIND_TCP` / `LANDLOCK_ACCESS_NET_CONNECT_TCP`，按端口授权，且只覆盖 `SOCK_STREAM`——**UDP、ICMP、raw socket 一律放行**，也不做 IP 级过滤。一旦词汇里有了网络，将来的 Linux 原生后端就必须长期报 `partial`。

### 3.3 曾考虑的替代方案

| 方案 | 词汇 | `ConfinedArgv` | agent 能力 | 结论 |
|---|---|---|---|---|
| **A. 不管网络**（采纳） | 1 维 | 与 DeepSeek 完全一致 | 完整联网 | 见下 |
| B. 网络轴，默认 `on` | 2 维 | 加 `network_enforcement` | 完整联网，可切 `none` | 否决 |
| C. 网络轴，固定 `none` | 1 维 | 与 DeepSeek 一致 | 无网 | 否决 |

**为什么选 A 而不是 B**：B 的唯一优势是「以后想收紧不用改结构」，但代价是现在就引入二维词汇——而 v1 的后端（Docker）在两个维度上永远同时报 `full`，这个字段当前不承载任何信息。等真有后端在某一维上只能做到 `partial` 时再加轴，是一次机械的字段扩展，不是架构返工。

**为什么选 A 而不是 C**：C 会让 agent 装不了包、下不了数据、调不了 API，「电脑操作能力」缩水成「离线文件操作能力」。而 DeepSeek 的定位本身就是允许联网——照搬它的语义就是 A。

**安全上的代价**：允许联网意味着工作区内容可以被发送到任意地址。对「防误操作」够用，对「防数据外泄」不够。这是明确接受的取舍。

**将来若要加轴**：`SandboxPolicy` 增加 `network` 字段、`ConfinedArgv` 增加 `network_enforcement` 字段，并让不具备网络能力的后端对 `none` 之外的取值 fail-closed。

## 4. 词汇与 seam

### 4.1 词汇

```python
# backend/tool_system/sandbox/vocabulary.py
SandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]
ConfinedSandboxMode = Literal["read-only", "workspace-write"]  # danger-* 不走 provider
SandboxEnforcement = Literal["full", "partial"]


@dataclass(frozen=True)
class SandboxPolicy:
    """一次受限执行的完整策略——逐调用携带，不固定在 provider 上。

    两个消费方可以在同一时刻以不同策略约束；获批的升权重试是一次
    带更宽策略的新调用。默认值解析是消费方边界的显式步骤。
    """
    mode: ConfinedSandboxMode
    workspace_root: str            # 宿主机绝对路径
    session_id: str | None = None  # 后端按会话维护状态时使用（v1 暂未用）
```

`danger-full-access` 的消费方直接 spawn 原始 argv，**不调用 `confine()`**。

### 4.2 seam

```python
# backend/tool_system/sandbox/provider.py
@dataclass(frozen=True)
class RunnerFailureRule:
    """识别 runner 在执行命令之前失败的证据。

    消费方先应用 allowed_exit_codes（若存在），再按 informational_lines
    整行精确匹配移除信息行，最后在剩余 stderr 行中做不区分大小写的
    fatal_signatures 匹配。退出状态本身永远不能证明 runner 失败。
    """
    fatal_signatures: tuple[str, ...]
    allowed_exit_codes: tuple[int, ...] | None = None
    informational_lines: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConfinedArgv:
    argv: list[str]
    enforcement: SandboxEnforcement
    denial_signatures: tuple[str, ...]
    runner_failure_rules: tuple[RunnerFailureRule, ...]


class SandboxUnavailableError(RuntimeError):
    """没有可用后端，或后端无法兑现请求的策略。fail-closed 的唯一出口。"""


class SandboxProvider(ABC):
    @abstractmethod
    def confine(self, argv: Sequence[str], policy: SandboxPolicy) -> ConfinedArgv:
        """把 argv 包装成受限形式；无法兑现时抛 SandboxUnavailableError。

        argv 是调用方即将 spawn 的精确 argv（程序 + 参数），不是 shell 字符串。
        shell 形状的消费方传 ["bash", "-c", command]。
        """
```

**seam 的形状在 Docker 下依然成立**：`confine()` 返回的仍是 argv，只是内容变成 `["docker","run",...,原 argv]`，调用方照常 `spawn`。这是 Docker 后端能复用同世界设计的关键。

## 5. 分层与模块划分

```
Tool（bash / python）
  → SandboxExecutor（type="sandbox"）
    → SandboxProvider（seam）
      → DockerProvider（v1 唯一后端）
        → docker run …（子进程）
```

```
backend/tool_system/sandbox/
├── __init__.py       # 对外只导出 seam 词汇
├── vocabulary.py     # SandboxMode / SandboxEnforcement / SandboxPolicy
├── errors.py         # SandboxUnavailableError（fail-closed 的唯一出口）
├── provider.py       # SandboxProvider(ABC) / ConfinedArgv / RunnerFailureRule
├── classify.py       # classify_outcome() → normal | denied | runner_failed
├── policy.py         # 纯函数：优先级解析（显式 > 会话覆盖 > 配置默认）
├── workspace.py      # 会话工作区解析 + 路径映射 + 安全校验 + 启动功能探测
├── runtime.py        # provider 单例 + 会话策略入口 + 内置工具注册
├── test_sandbox.py   # 自检
└── backends/
    └── docker.py     # DockerProvider（v1 唯一后端）

backend/tool_system/runtime/executor.py         # + SandboxExecutor / build_sandbox_argv
backend/tool_system/runtime/runtime.py          # executors 注册 "sandbox"
backend/tool_system/registry/descriptor.py      # + SandboxToolConfig / sandbox 字段
backend/tool_system/registry/registry.py        # Provider 抽象：从所有来源收集工具
backend/tool_system/registry/providers.py       # ToolProvider / MCPToolProvider / BuiltinToolProvider
backend/tool_system/context/context.py          # + sandbox_policy（策略下传）
backend/agents/runtime/agent_loop.py            # 解析策略 + 落地 SessionHeader.cwd
backend/api/mcp.py                              # 工具列表/详情含原生工具
backend/main.py                                 # 装配：init_sandbox + 注册 bash/python
docker-compose.yml / backend/Dockerfile         # socket + docker CLI + 工作区同路径挂载
```

阶段 1c 追加：`session/events.py`（`SANDBOX_MODE` 常量）、`session/sandbox_projection.py`
（find-last fold + 回放同构）、`session/trajectory_projection.py`（tool node 携带 sandbox 事实
与状态归类）、`agent_loop.py`（读取覆盖 + `tool/result` 结构化字段）、`api/agents.py`
（`GET/POST /sessions/{sid}/sandbox_mode`）、`frontend/lib/trajectory.ts` +
`TrajectoryTimeline.tsx`（轨迹里显示沙箱状态）。

### 5.1 关于 `policy.py` 的偏离

DeepSeek 的 `ctx.sandboxPolicy.resolve()` 是服务，直接读会话。我们的 `tool_system/` 不应依赖 `agents/runtime/session/`，所以 `policy.py` 是**纯函数**：

```python
def resolve_policy(
    *,
    explicit: SandboxMode | None,        # 升权重试的显式模式（阶段 2）
    session_override: SandboxMode | None, # 会话日志投影出的覆盖
    config_default: SandboxMode,          # 部署默认
    workspace_root: str,
) -> SandboxPolicy
```

优先级规则仍然只有一份、仍然可单测。AgentLoop 侧负责把会话投影读出来喂进去，解析结果挂在 `ToolContext` 上往下传——`ToolContext`（`context/context.py:19-27`）本来就是为这个设计的。

## 6. Descriptor 扩展

```python
# backend/tool_system/registry/descriptor.py
@dataclass(frozen=True)
class SandboxToolConfig:
    runtime: Literal["shell", "python"]   # 决定 argv 形状
    image: str
    timeout_s: float = 30.0
    memory: str = "1g"
    cpus: float = 1.0
    pids_limit: int = 256


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    type: str                             # "mcp" | "sandbox"
    ...
    sandbox: SandboxToolConfig | None = None   # type=="sandbox" 时必填
```

模型侧暴露**两个工具**（`bash` / `python`），都 `type="sandbox"`，只有 `runtime` 和镜像不同。这与 DeepSeek 的 `bash` / `pwsh` 两个消费方共用同一个 seam 同构。

## 7. DockerProvider

### 7.1 argv 构造

```python
["docker", "run", "--rm", "-i",
 "--network", "bridge",          # v1 不限制网络（与 DeepSeek 一致）；显式指定，不依赖 daemon 默认值
 "--read-only",
 *temp_args,                     # workspace-write 才有：--tmpfs /tmp
 "-v", workspace_mount,          # 随模式变化，见下
 "-w", "/workspace",
 "--cap-drop", "ALL",
 "--security-opt", "no-new-privileges",
 "--pids-limit", str(cfg.pids_limit),
 "--memory", cfg.memory,
 "--cpus", str(cfg.cpus),
 cfg.image, *argv]
```

**工作区挂载必须随模式变化**（`--read-only` 只让根文件系统只读，而绑定挂载会覆盖这一属性）：

| 模式 | 工作区挂载 | 临时区域 |
|---|---|---|
| `read-only` | `-v <root>:/workspace:ro` | 无 |
| `workspace-write` | `-v <root>:/workspace` | `--tmpfs /tmp` |

这与 DeepSeek 的 bwrap profile 同构——他们只在 `workspace-write` 时追加可写 bind。

> 实测教训：早期实现对所有模式都用可写绑定挂载，导致 `read-only` 下工作区仍可写
> （端到端测试发现，非单测能覆盖）。

因此：

- 工作区内写入（workspace-write）→ 成功
- 工作区外写入 → 内核返回 `Read-only file system`（EROFS）
- read-only 下写工作区 → 同样 EROFS
- **拒绝签名因此仍是内核级的**，与同世界后端一致，分类器形状不用改

### 7.2 拒绝签名与 runner 故障签名

```python
DENIAL_SIGNATURES = ("read-only file system",)

RUNNER_FAILURE_RULES = (
    RunnerFailureRule(
        allowed_exit_codes=(125,),          # docker run 自身失败固定 125
        fatal_signatures=(
            "cannot connect to the docker daemon",
            "permission denied while trying to connect to the docker daemon socket",
            "no such image",
            "executable file not found",
            "docker: command not found",
        ),
    ),
)
```

`docker run` 用 **125** 表示「docker 自己失败」，这与 DeepSeek 的 Landlock launcher 用 125 表示 launcher 失败是同一种干净门控。

> 注意：`denial_signatures` 的具体字符串需要在真实环境中标定（§11 的 e2e 测试）。容器内的 `permission denied` 太通用，任何用户命令都可能打印，不能作为拒绝签名。

### 7.3 功能探测

**不是** `docker --version`，而是真的跑一次：

```bash
docker run --rm -v <workspace_root>:/w alpine touch /w/.probe
```

这证明**docker daemon 侧**看得见这个路径（见 §8）。探测结果在 provider 生命周期内缓存——DeepSeek 的教训：探测用于在候选者之间仲裁，只有一个候选时也仍需验证，因为它验证的是「路径在 daemon 侧可见」这一部署事实，而非后端选择。

## 8. 工作区与路径

### 8.1 Docker-outside-of-Docker 的路径问题

backend 在容器里，docker daemon 在宿主机上。`-v /workspaces/x:/workspace` 里的 `/workspaces/x` 是**宿主机**路径，不是 backend 容器里的路径。这是最会在联调时浪费半天的问题。

**解法**：docker-compose 里把工作区根目录**以相同绝对路径**挂进 backend 容器：

```yaml
backend:
  volumes:
    - /srv/agent/workspaces:/srv/agent/workspaces
```

容器内外路径一致、零翻译。Windows 开发机走 Docker Desktop，直接用 `D:\...`。

### 8.2 会话工作区

- 配置 `SANDBOX_WORKSPACE_ROOT`（如 `/srv/agent/workspaces`）
- 每会话一个子目录：`<root>/<session_id>/`，首次沙箱调用时惰性创建
- `SessionHeader.cwd` 记录该路径（当前恒为 `None`，本阶段落地）
- 不可见/不可写 → `SandboxUnavailableError`，fail-closed

### 8.3 路径语义的变化

同世界后端里「宿主路径即命令路径」；Docker 后端里**只有容器内路径有意义**（`/workspace/...`）。因此：

- 工具返回值里的路径要翻译回宿主路径再给模型/前端
- 系统提示词或工具描述里要说明工作区挂载点（`/workspace`），否则模型会猜错

## 9. 安全边界

### 9.1 docker socket 等于宿主机 root

backend 挂 `/var/run/docker.sock` 之后，任何能构造 docker 调用的人都能 `-v /:/host` 逃逸。而模型恰好能执行命令。因此：

**`DockerProvider` 的公开方法只有 `confine(argv, policy)`；`-v` / `--network` / `--cap-drop` / `--privileged` 全部由 provider 内部按 `policy` 和 `SandboxToolConfig` 构造，模型参数只能进 `*argv` 尾部。**

这条要用**显式断言**守住，不能靠 code review：

```python
_FORBIDDEN = ("--privileged", "-v", "--volume", "--network", "--mount", "--pid", "--cap-add")
assert not any(a in argv for a in _FORBIDDEN), "模型参数不得影响隔离配置"
```

### 9.2 读侧边界由容器提供

这里与 DeepSeek 有一个重要差异，且对我们有利：

- **DeepSeek 是同世界约束**：命令与宿主共享文件系统，`read-only` 只挡写不挡读——宿主上有什么就能读什么。他们的场景是「用户自己的机器」，可以接受。
- **Docker 是另一个容器**：沙箱只挂载工作区，backend 的 `.env`、DB 密码、源码**根本不在那个容器里**。

因此读侧边界天然存在，但它**依赖一个配置约束**：

```
✅ SANDBOX_WORKSPACE_ROOT=/srv/agent/workspaces
   → 沙箱挂 /srv/agent/workspaces/<session_id>:/workspace
   → 看不到 .env、源码、DB 密码

❌ SANDBOX_WORKSPACE_ROOT=.（仓库根）
   → 沙箱挂整个仓库
   → .env 直接被挂进去
```

**必须实现校验**：`SANDBOX_WORKSPACE_ROOT` 启动时断言其不等于、也不包含仓库根或 `backend/` 目录。这条把「读侧不设防」从固有风险降级为配置错误。

进程内工具（fs 工具的跨族强制）仍不在本阶段范围。

### 9.3 fail-closed 的三种触发点

| 触发点 | 表现 |
|---|---|
| 无可用后端 / daemon 不可达 | `confine()` 抛 `SandboxUnavailableError`，命令永不 spawn |
| 工作区在 daemon 侧不可见 | 功能探测失败 → `SandboxUnavailableError` |
| runner 启动后拒绝 | 结构化 `runner_failure_rules` 匹配 → 归为 `runner_failed`，不当作普通命令失败 |

### 9.4 能力开关（`settings.sandbox.enabled`）

「沙箱开不开」是**沙箱子系统自己的设置**，不归平台层解释：值存在 `settings` 表
（`namespace='sandbox'`），缺省 `true`。停用时**注销**内建工具（bash / python）——
模型完全看不到它们，而不是看得到但一调就失败。

| 方面 | 设计 |
|---|---|
| 归属 | `sandbox/runtime.py` 提供 `is_enabled()` / `set_enabled()`；`main.py` 只问结果、不解释原因 |
| 生效 | **live**——`get_schemas_for()` 每请求重算，切换后下一轮对话即生效，无需重启 |
| 绑定 | 停用期间 `agent_tool_bindings` 不动；重新启用即恢复，不需要重新勾选 |
| API | `GET/PUT /api/tools/capabilities/sandbox` |
| UI | 工具绑定面板顶部一个开关（与「哪个 agent 能用」是两层） |

**不做通用 settings seam**：当前只有一个可配置项，DeepSeek 那套 namespace 注册 +
schema 序列化 + 三层解析是 SDK 形态的投入（他们要服务几十个第三方插件）。等出现
**第二个**需要配置的子系统时再抽象——那时才知道 seam 该长什么样。

## 10. 消费方：SandboxExecutor

```python
# backend/tool_system/runtime/executor.py
class SandboxExecutor(ToolExecutor):
    async def execute(self, descriptor, args, context) -> dict:
        # 1. 从 context 取已解析的 SandboxPolicy（无则 fail-closed）
        # 2. 按 descriptor.sandbox.runtime 构造 argv
        # 3. provider.confine(argv, policy) → ConfinedArgv
        # 4. asyncio.create_subprocess_exec(*confined.argv, ...)
        # 5. classify_outcome(exit_code, stderr, confined) → normal | denied | runner_failed
        # 6. 返回 {"stdout":…, "stderr":…, "exit_code":…, "sandbox": {...}}
```

注册进 `runtime/runtime.py:42` 的 `executors` dict：

```python
self._executors = {"mcp": MCPExecutor(), "sandbox": SandboxExecutor()}
```

### 10.1 分类器

```python
# backend/tool_system/sandbox/classify.py
Outcome = Literal["normal", "denied", "runner_failed"]

def classify_outcome(exit_code: int, stderr: str, confined: ConfinedArgv) -> Outcome:
    # 1. 先查 runner 失败：退出码门控 + 移除信息行后的一行致命签名
    # 2. 再查拒绝签名
    # 3. 否则 normal
```

**顺序很重要**：runner 失败意味着命令根本没跑，拒绝意味着沙箱正常工作并挡住了操作。搞反了会把「沙箱坏了」误报成「命令失败了」。

### 10.2 拒绝标记

被拒绝的调用，结果文本里带：

```
[sandbox: file access denied under <mode> mode]
```

阶段 1 还没有升权路径（那需要审批服务），但**标记必须从第一天就产出**。否则阶段 2 加升权时要回头改工具输出格式和模型侧描述。

## 11. 事件与投影

### 11.1 不新增事件类型

`sandbox` 事实挂在现有的 `tool/result` 上（DeepSeek 原则：事件只在它本身即为存储时才需要）：

```python
"tool/result": {
    ...,
    "sandbox": {
        "mode": "workspace-write",
        "enforcement": "full",
        "outcome": "normal" | "denied" | "runner_failed",
    }
}
```

### 11.2 会话模式投影

`session/events.py` 新增事件常量与类型：

```python
SANDBOX_MODE = "sandbox/mode"   # data: {"mode": "read-only" | "workspace-write" | "danger-full-access"}
```

`session/sandbox_projection.py` 照 `trace_projection.py` 的结构写：

- 增量投影器：`handle(ev)` 逐条消费，fold 形态为 **find-last**
- `project_sandbox_mode(events)` 给冷恢复回放用
- 二者严格同构（`project_sandbox_mode(events) == fold(handle)`）

读取面：

```python
effective(session) = sandbox_projection.stateOf(session) ?? config_default
```

阶段 1 可能只有配置默认值（没有 UI 切换入口），但**事件类型与投影先立住**，这样阶段 2 加运行时切换时不需要动结构。

## 12. 分期

| 阶段 | 内容 | 状态 |
|---|---|---|
| **1a** | 词汇 + seam + 分类器 + `DockerProvider` + 工作区功能探测 | ✅ 已落地 |
| **1b** | 工作区落地（`SessionHeader.cwd`）+ `SandboxExecutor` + `bash`/`python` 工具注册 | ✅ 已落地 |
| **1c** | `sandbox/mode` 事件 + 投影 + `tool/result` 的 sandbox 事实 + 前端展示 | ✅ 已落地 |
| **2** | 升权审批（`sandbox_permissions` + `justification` 一次性授权）+ 策略上下文消息 | 待做 |
| **3** | Landlock / Bubblewrap 后端、fs 工具跨族强制（届时再评估是否加网络轴） | 待做 |

**为什么升权放到阶段 2**：它是整套设计里唯一依赖审批服务的东西，而我们目前没有审批服务。先立 seam 和 fail-closed，别被审批阻塞。但阶段 1b 起，拒绝标记就必须产出（§10.2）。

## 13. 测试点

### 单元测试

- 词汇封闭性：非法 `mode` 在构造时被拒
- `resolve_policy` 的优先级链（显式 > 会话覆盖 > 配置默认）
- argv 构造：模型参数注入负例（`--privileged` / `-v` 必须被断言拦住）
- 分类器：125 退出码门控、信息行排除、denial 与 runner_failed 的优先级
- 路径映射与工作区解析
- `SANDBOX_WORKSPACE_ROOT` 指向仓库根时启动即拒绝（§9.2）

### 真实 docker e2e

- 工作区内写入成功 / 工作区外写入得到 EROFS
- daemon 不可达 → `SandboxUnavailableError`（命令永不 spawn）
- 镜像不存在 → `runner_failed` 而非 `denied`
- 工作区在 daemon 侧不可见 → 功能探测失败

### 快照

- 策略上下文消息
- `tool/result` 的 sandbox 事实
- 拒绝标记文本

### 会话

- 投影 fold 与冷恢复回放严格同构

## 14. 附录：与 DeepSeek 的差异对照

| 维度 | DeepSeek | 本设计 |
|---|---|---|
| 后端 | bwrap → Landlock / Seatbelt / Windows ACL | Docker（v1），后续加 Landlock / Bubblewrap |
| 词汇维度 | 文件效果（一维） | 同（一维） |
| 网络 | 不在词汇内，命令照常联网 | 同 |
| 策略解析 | `ctx.sandboxPolicy` 服务，直读会话 | 纯函数，由 AgentLoop 喂入 |
| 升权 | 阶段内交付 | 延后到阶段 2 |
| 读侧边界 | 不在范围内（用户自己的机器） | 明确不在阶段 1 范围（见 §9.2） |
| 拒绝标记 | `[sandbox: file access denied under <mode> mode]` | 同 |
| fail-closed | `SANDBOX_UNAVAILABLE` | `SandboxUnavailableError` |
| 拒绝 vs runner 故障 | 两套 stderr 方言，先查后者 | 同 |

## 参考

- DeepSeek Harness `dsh-sandbox`：`packages/sandbox/sandbox/README.zh.md`
- DeepSeek Harness 决策 Note：`.agents/notes/implemented/feature/2026-07-06-sandbox.zh.md`
- DeepSeek Harness 子系统文档：`docs/subsystems/sandbox.zh.md`
- Landlock ABI v4 网络支持：<https://github.com/torvalds/linux/commit/fff69fb03dde1dfa348cfdb74b13287dabe42c25>
