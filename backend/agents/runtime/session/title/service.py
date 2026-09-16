"""SessionTitleService — 会话标题的接受、钉住与自动升级

契约（对齐 DSH 的 session-title，见该包 README 与 title_projection 的模块文档）：

  **标题 = 追加型日志事件 ``session/title``，不是可变字段**（为了可重放）。
  三个来源，新的赢：

    1. ``fallback`` —— 首条合格人类消息的**前导词**（5 词 / 40 字节）。
       **确定性、纯函数、无 IO**，所以能同步跑完，不存在「阻塞主回复」的问题。
    2. ``provider`` —— 一次 LLM 生成（本项目里至多一次，只在**首条**消息上跑）。
       **不阻塞主回复、不开新 turn**：见 :meth:`SessionTitleService.schedule`。
    3. ``user`` —— 用户显式改名。**一旦改名即钉住**，后续所有自动生成停摆
       （要解除钉住是刻意的动作，不是副作用）。

三条「别做」在本模块的落地方式：

  - **别让标题生成阻塞主回复**：fallback 在 turn 末**同步**补（纯函数，微秒级）；
    LLM 那一次走 ``asyncio.create_task`` 完全脱离主链路 —— ``settle`` / ``schedule``
    都不返回需要 await 的东西，调用方（AgentRuntime）拿不到可以等的东西，
    也就没法误 await。
  - **别用数据库列 + UPDATE**：标题只写 ``session/title`` 事件，读侧 fold（见
    title_projection）。没有任何一处 UPDATE 标题。
  - **别按字符数截断**：一切截断都走 ``truncate_title_utf8``（字节 + 不切码点）。

**supersede 与过期丢弃**：每个会话维护一个 ``revision``。新的一次自动生成会让
``revision += 1`` 并 cancel 掉在飞的任务；任务落地前再校验一次「我还是当前那一次
吗」，不是就丢弃 —— 用户改名、下一条消息、会话重建都会让在飞的任务作废。

**为什么 LLM 标题要自己 flush**：``_flush_session_events`` 的触发点是 turn 完成，
而自动标题（刻意）跑在 turn 之后，它那条事件常常晚于那次 flush。不补一次 flush
就只会躺在 persistence 的 pending 里，等下一次对话才顺带落库 —— 而「重启后端标题
仍在」要求它尽快 durable。
"""
import asyncio
import json
import logging
from dataclasses import dataclass

from backend.agents.runtime.session.events import TITLE
from backend.agents.runtime.session.persistence import (
    flush_session_events,
    get_session_persistence,
)
from backend.agents.runtime.session.title.normalize import (
    MAX_TITLE_BYTES,
    fallback_session_title,
    normalize_session_title,
)
from backend.agents.runtime.session.title.projection import (
    SOURCE_FALLBACK,
    SOURCE_PROVIDER,
    SOURCE_USER,
    SessionTitle,
    TitleMessage,
    fold_session_title,
    fold_title_input,
)
from backend.core.http import get_http_session

logger = logging.getLogger(__name__)

#: 本项目的 provider id（标题来源记在事件里，便于日后判断标题是怎么来的）。
TITLE_PROVIDER_LLM = "session-title-llm"

# ── LLM 生成的限额（对齐参考实现 cordis.patch.yml 的 session-title-llm 段）──

#: 非 CJK 语言的**目标**词数（不是硬限制——硬限制只有 80 字节）。
TARGET_WORDS = 5
#: CJK 的目标字数。
TARGET_CJK_CHARACTERS = 10
#: 最终 JSON 框起来的 user prompt 的 UTF-8 字节上限。**超了直接放弃，不截断**：
#: 截断会把一个结构化 JSON 数组切成非法 JSON，模型只会更糊涂。
MAX_INPUT_BYTES = 4096
#: 辅助生成的 token 上限。
#:
#: **比参考实现的 64 宽得多，这是刻意的。** 那边能开 64 是因为它的 LLM 适配层为
#: ``purpose: 'session-title'`` 关掉了思考；我们打的是 OpenAI 兼容端点，没有这个
#: 可移植的开关，于是**推理型模型会把预算烧在 reasoning 上**——实测
#: ``deepseek-reasoner`` 在 64 下 finish_reason 常常是 ``length``：可见 content 还没
#: 吐出来就被截断，标题于是**永远升不了级**（而且只留一条 WARNING，静默退化）。
#: 给足余量后实测 256 就能稳定 stop，这里取 512 留一档安全边际。
#:
#: 想省这笔钱的部署：把 ``session_title_model`` 指到一个非推理的小模型上
#: （标题只是一行，不需要推理），配额随之降下来。
MAX_OUTPUT_TOKENS = 512
#: 端到端超时（秒）。标题晚到没有意义，宁可放弃。
TIMEOUT_SECONDS = 60.0

#: 系统指令：**语言感知**，且明确禁止 Markdown / 引号 / 控制码。
#: 标题会直接显示在侧栏一行里，模型自带的「好的，这是标题：」前缀会原样露出。
_SYSTEM_PROMPT = "\n".join([
    "Create a concise title for an AI coding-assistant session from the supplied human messages.",
    "Return only the title on one line, **in plain text of natural language**, "
    "with no quotes, prefix, explanation, Markdown, XML, or terminal control codes. No code is allowed.",
    "Use the language of the messages.",
    f"Aim for about {TARGET_WORDS} words in non-CJK languages "
    f"or {TARGET_CJK_CHARACTERS} CJK characters.",
])


def frame_messages(messages: list[TitleMessage]) -> str:
    """把消息框成 JSON——用户文本因此**无法**破坏结构分隔符。

    不要改成「拼一段带分隔符的裸文本」：用户消息里出现分隔符就能把提示词注入
    到标题生成里，而这段文本是用户可控的。
    """
    return (
        "Generate the session title from this JSON array of human messages:\n"
        + json.dumps(
            [{"seq": m.seq, "text": m.text} for m in messages],
            ensure_ascii=False,
        )
    )


@dataclass
class _Work:
    """一个会话的自动生成并发状态。"""
    revision: int = 0
    task: "asyncio.Task | None" = None


class SessionTitleService:
    """标题服务：接受（fallback / provider）与钉住（user）。

    进程级单例——所有 AgentRuntime 共享一份在飞状态，否则同一会话被两个 Runtime
    先后处理时会各自以为「我是唯一在飞的那次」。
    """

    #: 可注入的 LLM 调用替身（测试用）：签名 ``(messages, route, config) -> str``。
    #: 为 None 时走 :meth:`_llm_title` 的真实 HTTP 调用。
    llm_call = None

    def __init__(self) -> None:
        self._work: dict[str, _Work] = {}

    # ── 读 ──

    def get(self, session) -> SessionTitle | None:
        """当前标题（fold 事件日志）；无标题返回 ``None``。"""
        return fold_session_title(session.events)

    # ── 写：turn 末的确定性 fallback ──

    def settle(self, session) -> SessionTitle | None:
        """turn 末：**同步**补一条 fallback 标题（若尚无标题且有合格输入）。

        同步是刻意的：这是个纯函数（取首条消息的前导词），没有 await 点，也就
        没有「阻塞主回复」的可能。放在 turn 末而不是收到消息的那一刻，是为了
        不打断 AgentLoop 的写事件节奏——用户看到标题的时机（看板刷新）本来就是
        turn 末。

        **已钉住则什么都不做**：任何既有标题（含 user 改名）都会让本方法提前返回，
        钉住因此不需要单独的判断分支——「有标题」与「不改」在这里是同一件事。

        Returns:
            当前标题（可能是既有标题，也可能是本次补上的 fallback）；无合格输入
            且无既有标题时返回 ``None``。
        """
        current = self.get(session)
        if current is not None:
            return current
        inp = fold_title_input(session.events)
        if inp.first is None:
            return None  # 还没有合格的人类文本（例如首条是纯图片）→ 继续等
        title = fallback_session_title(inp.first.text)
        if not title:
            return None  # 净化后为空（全空白 / 全控制符）→ 不写事件
        session.append(TITLE, {
            "title": title,
            "message_seqs": [inp.first.seq],
            "source": SOURCE_FALLBACK,
        })
        return self.get(session)

    # ── 写：用户改名（钉住）──

    def rename(self, session, title: str) -> SessionTitle:
        """接受一次显式改名，写入 ``user`` 源事件 —— 从此钉住。

        Raises:
            ValueError: 标题净化后为空（全是控制符 / 空白）。调用方翻译成 400。
        """
        normalized = normalize_session_title(title, MAX_TITLE_BYTES)
        if not normalized:
            raise ValueError("标题必须包含可见字符")
        sid = session.header.id
        self.supersede(sid, "用户改名取代了在飞的自动标题生成")
        session.append(TITLE, {
            "title": normalized,
            "message_seqs": [],
            "source": SOURCE_USER,
        })
        after = self.get(session)
        if after is None:  # pragma: no cover - 刚刚 append 过，fold 不可能为空
            raise RuntimeError("改名后的标题 fold 失败")
        return after

    def rename_by_id(self, session_id: str, title: str) -> SessionTitle:
        """按 session_id 改名（API 路径）：热区命中直接用，否则从事件日志冷恢复。

        冷恢复出来的 Session **登记回 SessionStore**：后续对话会走同一份实例，
        否则改完名再发消息，那个会话仍是「没有改名事件」的旧副本，标题会翻回自动版。

        Raises:
            LookupError: 该 session 既不在热区、持久化里也没有。
            ValueError: 标题净化后为空。
        """
        from backend.agents.runtime.session.session import Session
        from backend.agents.runtime.session.store import get_session_store

        store = get_session_store()
        session = store.get(session_id)
        if session is None:
            loaded = get_session_persistence().load(session_id)
            if loaded is None:
                raise LookupError(session_id)
            header, events = loaded
            session = Session.from_events(header, events)
            store.put(session)
        result = self.rename(session, title)
        # 改名是**用户动作**，必须立刻 durable —— 否则「重启后端标题仍在」不成立
        flush_session_events(get_session_persistence(), session)
        return result

    # ── 写：异步 LLM 升级（不阻塞主回复）──

    def schedule(self, session, config: dict) -> bool:
        """排一次 LLM 标题生成。**绝不阻塞调用方**，返回是否排上了。

        只有同时满足下面三条才排：

          - 开关开着（``session_title_enabled``，默认开）且模型凭据齐全；
          - 当前标题**不是** provider / user 源（已升级过、或被钉住 → 不再自动改）；
          - 合格人类消息**恰好一条**（对齐参考实现的 ``first-prompt`` 档：
            标题锚在第一句话上，不随后续对话漂移）。

        最后一条同时保证了幂等：turn 2 之后 count ≥ 2，不会再排；冷恢复后重放
        得出的判断与在线时完全一致（判断只依赖事件日志，不依赖内存状态）。

        本方法**不返回 awaitable** —— 调用方拿不到可以等的东西，也就没法误 await
        它去阻塞主回复。
        """
        if not config.get("session_title_enabled", True):
            return False
        if self.llm_call is None and not _llm_ready(config):
            return False  # 没配 api_key / base_url / model → 静默跳过（不是错误）
        sid = session.header.id
        current = self.get(session)
        if current is not None and current.source in (SOURCE_PROVIDER, SOURCE_USER):
            return False
        inp = fold_title_input(session.events)
        if inp.count != 1 or inp.first is None:
            return False
        work = self._work.setdefault(sid, _Work())
        work.revision += 1
        revision = work.revision
        if work.task is not None and not work.task.done():
            work.task.cancel()
        messages = [inp.first]
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # 没有事件循环（同步调用，如脚本/测试）→ 不排
            logger.debug(f"Session [{sid}] 无运行中的事件循环，跳过 LLM 标题生成")
            return False
        task = loop.create_task(self._run(session, work, revision, messages, dict(config)))
        work.task = task
        # 异常必须在这里被吃掉：任务没人 await，未处理异常只会在 loop 关闭时刷屏
        task.add_done_callback(lambda t: self._on_done(t, sid))
        return True

    async def _run(
        self, session, work: _Work, revision: int,
        messages: list[TitleMessage], config: dict,
    ) -> None:
        """执行并接受**当前那一次** provider 生成（过期的结果一律丢弃）。"""
        title = await self._generate(messages, config)
        if not title:
            return
        if not self._current(session, work, revision):
            logger.debug(f"Session [{session.header.id}] LLM 标题过期，丢弃")
            return
        session.append(TITLE, {
            "title": title,
            "message_seqs": [m.seq for m in messages],
            "source": {
                "kind": SOURCE_PROVIDER,
                "provider": TITLE_PROVIDER_LLM,
                "model": _model_of(config),
            },
        })
        # 自动标题跑在 turn 之后，赶不上 turn 末那次 flush —— 自己补一次
        flush_session_events(get_session_persistence(), session)
        logger.info(f"Session [{session.header.id}] 标题已升级为 LLM 版: {title!r}")

    def _current(self, session, work: _Work, revision: int) -> bool:
        """这一次生成还算数吗（会话没被换掉、还是最新一次、没被钉住）。"""
        if self._work.get(session.header.id) is not work or work.revision != revision:
            return False
        current = self.get(session)
        # 这中间用户改名了 → 作废。判定与 supersede 双保险：rename 会 bump revision，
        # 但「同一个 revision 里先落地了别人的标题」仍要靠这里拦。
        return not (current is not None and current.pinned)

    async def _generate(self, messages: list[TitleMessage], config: dict) -> str | None:
        """一次辅助 LLM 调用 → 净化后的标题；任何失败都返回 ``None``（不抛）。

        自动生成失败**只记日志**：标题是锦上添花，绝不能把失败冒泡到主链路。
        """
        try:
            framed = frame_messages(messages)
            if len(framed.encode("utf-8")) > MAX_INPUT_BYTES:
                logger.warning(
                    f"标题输入 {len(framed.encode('utf-8'))} 字节超过 {MAX_INPUT_BYTES}，放弃生成"
                )
                return None
            if self.llm_call is not None:  # 测试替身
                raw = await self.llm_call(messages, framed, config)
            else:
                raw = await self._llm_title(framed, config)
            title = normalize_session_title(raw, MAX_TITLE_BYTES)
            if not title:
                logger.warning("标题模型没有产出可见文本，保留原标题")
                return None
            return title
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"LLM 标题生成失败（保留原标题）: {e}")
            return None

    async def _llm_title(self, framed: str, config: dict) -> str:
        """真实 HTTP 调用：复用 agent 的凭据，模型可用 ``session_title_model`` 单独指定。"""
        import aiohttp

        base_url, api_key = _llm_endpoint(config)
        timeout = float(config.get("session_title_timeout", TIMEOUT_SECONDS))
        payload = {
            "model": _model_of(config),
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": framed},
            ],
            # 低温：标题要稳，不要每次刷新换一个说法
            "temperature": 0.3,
            "max_tokens": int(config.get("session_title_max_output_tokens", MAX_OUTPUT_TOKENS)),
        }
        http = await get_http_session()
        async with http.post(
            f"{base_url}/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            result = await resp.json()
        choice = (result.get("choices") or [{}])[0]
        # tool_calls / 非 stop finish_reason 一律拒绝（对齐参考实现：标题只收文本）
        if choice.get("finish_reason") not in (None, "stop"):
            raise RuntimeError(f"标题模型 finish_reason={choice.get('finish_reason')!r}")
        msg = choice.get("message") or {}
        if msg.get("tool_calls"):
            raise RuntimeError("标题输出必须只有文本，收到 tool_calls")
        return msg.get("content") or ""

    # ── 生命周期 ──

    def supersede(self, session_id: str, reason: str) -> None:
        """作废该会话在飞的自动生成（bump revision + cancel）。"""
        work = self._work.get(session_id)
        if work is None:
            return
        work.revision += 1
        if work.task is not None and not work.task.done():
            work.task.cancel()
            logger.debug(f"Session [{session_id}] 作废在飞标题生成: {reason}")

    def forget(self, session_id: str) -> None:
        """会话销毁时清理在飞状态（避免 _work 无限增长）。"""
        self.supersede(session_id, "会话已销毁")
        self._work.pop(session_id, None)

    def forget_all(self) -> None:
        """清空全部在飞状态。

        给测试用：``_work`` 是进程级单例上的可变状态，用例之间不隔离就会互相串
        （与 ``SessionStore`` 每个用例换一份是同一个理由）。生产上不调用 ——
        那边只有进程退出才需要，而进程一退这些东西本来就没了。
        """
        for sid in list(self._work):
            self.supersede(sid, "清空在飞标题状态")
        self._work.clear()

    @staticmethod
    def _on_done(task: "asyncio.Task", session_id: str) -> None:
        """吞掉任务异常 —— 没人 await 它，漏出去只会在 loop 关闭时刷屏。"""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning(f"Session [{session_id}] 标题任务异常: {exc}")


# ── 辅助 ──


def _llm_endpoint(config: dict) -> tuple[str, str]:
    """从 agent 配置里取 (base_url, api_key)——标题复用主模型那套凭据。"""
    base_url = (config.get("base_url") or "").strip().rstrip("/")
    api_key = (config.get("api_key") or "").strip()
    if not base_url or not api_key:
        raise RuntimeError("agent 未配置 base_url / api_key")
    return base_url, api_key


def _model_of(config: dict) -> str:
    """标题用哪个模型：``session_title_model`` 优先（可以挑个更便宜的），否则同主模型。"""
    return (config.get("session_title_model") or config.get("model") or "").strip()


def _llm_ready(config: dict) -> bool:
    return bool(_model_of(config) and (config.get("base_url") or "").strip()
                and (config.get("api_key") or "").strip())


# ── 进程级装配（与 get_session_store() / get_session_persistence() 同模式）──

_service = SessionTitleService()


def get_session_title_service() -> SessionTitleService:
    """当前进程装配的标题服务（进程级单例：在飞状态必须全局唯一）"""
    return _service
