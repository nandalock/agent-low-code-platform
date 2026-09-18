"""LLM 边界的失败契约：结构化错误 + 失败码分类 + 响应可用性校验

从 agent_loop.py 拆出（原文件顶部那一整段「结构化分类 + 重试策略」的前半）。
本模块只回答「这次请求**发生了什么**」——把 HTTP 状态、响应形态、结束原因翻译成
稳定的失败码；至于「**该怎么办**」（要不要重试、等多久），是 retry.py 的事，
本模块一个字都不提策略。

对齐 A 的分层（见 docs/agent-loop-design.md §6.2）：A 把失败码的判定放在 provider
适配器（`llm-deepseek/adapter.ts` 的 `httpErrorCode`）、把策略放在 `llm-retry` 插件；
B 没有 provider 插件体系，于是两件事各占一个模块（`runtime/llm/` 下），agent_loop.py
只抛与收 LlmError，不再认识任何失败码或退避常数。
"""
import json

# ── 失败码：由 HTTP 状态与响应形态判定，不猜 provider 的文本 ──

CODE_RATE_LIMIT = "RATE_LIMIT"              # 429：限流，退避后重试
CODE_SERVER = "SERVER"                      # 5xx：对端故障，有限重试
CODE_CLIENT = "CLIENT"                      # 其余 4xx：鉴权/参数/额度，重试无用
CODE_TIMEOUT = "TIMEOUT"                    # 请求或传输超时
CODE_TRANSPORT = "TRANSPORT"                # 连接层失败（DNS / 断连）
CODE_INVALID_RESPONSE = "INVALID_RESPONSE"  # 非 JSON / 结构缺失 / tool_calls 不可解析
CODE_EMPTY_RESPONSE = "EMPTY_RESPONSE"      # 2xx 但既无正文也无 tool_calls（网关抖动）
CODE_UNKNOWN_FINISH = "UNKNOWN_FINISH"      # finish_reason 不在词表内（content_filter 等）

#: 可重试的失败码（对齐 A retryableCodes：RATE_LIMIT / SERVER / TIMEOUT / TRANSPORT；
#: B 另把「非法响应 / 空响应」也纳入 —— 它们是同一次请求的瞬时故障，重发常能自愈）。
#: 注意：这只是**默认策略**的取值，不是硬编码的判定 —— 真正决定重试与否的是
#: hooks.request_error（见 retry.make_retry_policy 与 hooks.py）。
RETRYABLE_CODES = frozenset({
    CODE_RATE_LIMIT,
    CODE_SERVER,
    CODE_TIMEOUT,
    CODE_TRANSPORT,
    CODE_INVALID_RESPONSE,
    CODE_EMPTY_RESPONSE,
})

#: 认识的结束原因（对齐 A mapFinishReason：stop / tool_calls / length，其余一律当失败）。
#: None = 网关没给 finish_reason（部分网关如此），**不判**，保持对既有网关的兼容。
KNOWN_FINISH_REASONS = frozenset({"stop", "tool_calls", "function_call", "length"})


class LlmError(RuntimeError):
    """一次 LLM 请求的结构化失败（对齐 A 的 LlmError / LlmFailure）。

    ``code`` 是失败码（见上方 CODE_* 与 RETRYABLE_CODES），``status`` 是 HTTP 状态码
    （传输层失败为 None），``retry_after`` 是网关 Retry-After 头换算出的秒数。
    失败被结构化后「重试什么、为什么停」都由字段决定，不靠解析错误文本。

    基类取 ``RuntimeError`` 是本仓既有的分类习惯（不是从 A 搬来的 —— 那边是 JS 的
    ``extends Error``）：**运行环境/对端的问题用 RuntimeError**（同
    ``SandboxUnavailableError``、「Registry 未装配」），**调用方要处理的领域错误用
    Exception**（同 ``GatewayError`` / ``EscalationError``）。429 / 5xx / 网关返 HTML
    都不是调用方传错了参数，属于前者。

    注意：它只该被 ``except LlmError`` 捕获 —— 别在 LLM 调用链上写 ``except RuntimeError``，
    那会把这里一并吞掉。
    """

    def __init__(self, message: str, *, code: str, status: int | None = None, retry_after: float = 0.0):
        super().__init__(message)
        self.code = code
        self.status = status
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        """是否是「重发可能自愈」的失败（429 / 5xx / 超时 / 传输 / 非法响应 / 空响应）

        这是**默认策略**的判据（RETRYABLE_CODES），不是普遍真理：装了自定义
        request_error 钩子后，它可能根本不被读（见 hooks.py）。
        """
        return self.code in RETRYABLE_CODES

    def fact(self, *, step: int, attempt: int, retry_in: float | None) -> dict:
        """结构化事实（落 llm/error 事件）：投影端直接读字段，不解析文本。"""
        return {
            "step": step,
            "attempt": attempt,
            "code": self.code,
            "status": self.status,
            "message": str(self),
            "retry_in": None if retry_in is None else round(retry_in, 2),
        }


def classify_status(status: int) -> str:
    """HTTP 状态码 → 失败码（重试决策的唯一依据）"""
    if status == 429:
        return CODE_RATE_LIMIT
    if status >= 500:
        return CODE_SERVER
    if status == 408:
        return CODE_TIMEOUT
    return CODE_CLIENT


def retry_after_seconds(headers) -> float:
    """Retry-After 头 → 秒数（429 常用的对端退避建议；缺失/非法一律 0）"""
    try:
        value = float(str((headers or {}).get("Retry-After", "")).strip())
    except (TypeError, ValueError):
        return 0.0
    return value if value > 0 else 0.0


def http_failure(status: int, body: str, headers) -> LlmError:
    """一次非 2xx 响应的结构化失败（两个 HTTP 调用点共用，避免分类逻辑各写一遍）。

    HTTP 状态必须**先于**响应体判定：非 2xx 时网关返回的是错误 JSON（甚至 HTML），
    照常当成功解析会把错误体当成 assistant message 落库。
    """
    return LlmError(
        f"LLM 请求失败: HTTP {status} {body[:200]}",
        code=classify_status(status),
        status=status,
        retry_after=retry_after_seconds(headers),
    )


def timeout_failure(error) -> LlmError:
    """请求 / 传输超时 → 结构化失败"""
    return LlmError(f"LLM 请求超时: {error}", code=CODE_TIMEOUT)


def transport_failure(error) -> LlmError:
    """连接层失败（DNS / 断连）→ 结构化失败"""
    return LlmError(f"LLM 连接失败: {error}", code=CODE_TRANSPORT)


def invalid_response(message: str, *, status: int | None = None) -> LlmError:
    """响应形态不可用（非 JSON / 顶层不是对象）→ 按非法响应处理

    状态码能带上就带上（诊断用）；它不影响失败码 —— 2xx 却给出不可解析的 body，
    和对端返 HTML 是同一类故障。
    """
    return LlmError(message, code=CODE_INVALID_RESPONSE, status=status)


def invalid_tool_calls(tool_calls: list[dict]) -> list[str]:
    """挑出结构不完整 / arguments 不可解析的 tool_call，返回其标签（有 id 用 id，否则用下标）。

    校验范围刻意覆盖「计划阶段会用到的每一个字段」（id / function.name /
    function.arguments → dict）：这些调用是**不可复现的事实**，既不能落 tool/call
    （args 已经残缺），也不能落进 assistant/message（会留下无配对 tool/result 的
    dangling tool_calls，让下一轮请求非法）。宁可在落库前按结构化失败重试。
    """
    bad: list[str] = []
    for index, tc in enumerate(tool_calls):
        fn = tc.get("function") if isinstance(tc, dict) else None
        if not tc.get("id") or not isinstance(fn, dict) or not fn.get("name"):
            bad.append(tc.get("id") or f"#{index}")
            continue
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError):
            bad.append(tc.get("id") or f"#{index}")
            continue
        if not isinstance(args, dict):
            bad.append(tc.get("id") or f"#{index}")
    return bad


def validate_response(msg: dict, finish_reason: str | None) -> None:
    """校验响应是否可用；不可用即抛 LlmError（是否重试由失败码 + 策略决定）。

    三步，顺序即优先级：

      ① 结束原因词表 —— 对齐 A ``mapFinishReason`` 的 default 分支：**不认识的结束
         原因一律当失败**，不许冒充正常收尾（content_filter 之类的截断不是答案）。
         None（网关没给）不判，保持对既有网关的兼容。
      ② 截断（length）—— 到这里就返回：tool_calls 由调用方**整体丢弃**（对齐 A 的
         装配器掩码：截断时把 tool-call 块筛掉、正文照常保留），所以不校验也不报错。
      ③ tool_calls 完整性 —— **必须在落 assistant/message 之前**判定：Event Log 是
         append-only，一旦落进一条带残缺 tool_calls 的 assistant 消息就再也回退不了，
         它会留下无配对 tool/result 的 dangling 消息，让下一轮 derive_messages 非法。
    """
    if finish_reason is not None and finish_reason not in KNOWN_FINISH_REASONS:
        raise LlmError(f"模型以未知原因结束: {finish_reason}", code=CODE_UNKNOWN_FINISH)

    if finish_reason == "length":
        return

    bad = invalid_tool_calls(msg.get("tool_calls") or [])
    if bad:
        raise LlmError(f"tool_calls 参数不可解析: {', '.join(bad)}", code=CODE_INVALID_RESPONSE)
    if not msg.get("content") and not msg.get("tool_calls"):
        # 200 却既无正文也无调用：网关抖动的典型形态，重发常能自愈
        raise LlmError("LLM 返回空响应（无 content 也无 tool_calls）", code=CODE_EMPTY_RESPONSE)
