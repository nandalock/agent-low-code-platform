"""SessionTitleProjection — Session 事件 → 会话标题（对照 DSH 的 session-title）

Event Log 是唯一事实源；本模块把 ``session/title`` 事件 fold 成「当前标题」，
并与 ``sandbox_projection`` / ``trace_projection`` / ``trajectory_projection``
并列消费同一份 Event Log。

**为什么是事件而不是数据库列 + UPDATE**：标题是「这个会话现在叫什么」的**事实**，
追加型事件让它和其余执行事实一样可重放——冷恢复、fork、分页读到的标题完全一致，
不需要第二套「标题表」再和会话历史对账。代价只是每次列表多一次 fold（见
``api/workspace.py`` 的批量查询：一次 SQL 取回全部标题事件再 fold）。

**本模块是纯函数，没有增量投影类**：与 sandbox_projection 的差别在这里刻意——
标题只在两个时刻被读（turn 末补 fallback、列会话时算显示值），都是「扫一遍
日志」的成本，而日志是内存 list 或一次已取回的查询结果。为它维护一份增量
状态只是多一处可能与 fold 漂移的实现，收益为零。

**合格输入的定义**（与参考实现逐条对齐，别放宽）：

  - 只有 ``user/message`` 且来源是**人**（``data.source.kind == "user"``，缺省即人）
  - 只看**文本块**——``content`` 是字符串，或 OpenAI 多模态的块列表里
    ``type == "text"`` 的那些；纯图片消息因此**不合格**（标题保持不变，等后续输入）
  - 净化后为空（纯空白 / 纯控制符）同样不合格

**log-only 保证**：``session/title`` 不在 ``SURFACE_EVENT_TYPES`` 里，所以
``SurfaceManager`` 不会收它，``derive_messages()`` 也派生不出它——标题**永不进入
模型输入**（零 token、不改变前缀缓存，见 events.py 的 TITLE 注释）。
"""
from dataclasses import dataclass, field

from backend.agents.runtime.session.events import TITLE, USER_MESSAGE, SessionEvent
from backend.agents.runtime.session.title.normalize import clean_title_text

#: 标题来源：首条合格人类消息的前导词（确定性、同步、无模型）。
SOURCE_FALLBACK = "fallback"
#: 标题来源：注册的 provider（本项目里是 LLM）生成。**至多一次**。
SOURCE_PROVIDER = "provider"
#: 标题来源：用户显式改名。**一旦写入即钉住**，后续自动生成全部停摆。
SOURCE_USER = "user"

SOURCES = (SOURCE_FALLBACK, SOURCE_PROVIDER, SOURCE_USER)


@dataclass(frozen=True)
class TitleMessage:
    """一条合格的人类文本消息（标题的可用输入）。"""
    seq: int
    text: str


@dataclass(frozen=True)
class TitleInput:
    """折叠出的标题输入概况。

    ``first`` 是 fallback 的唯一输入（「首条」是契约：标题锚在第一句话上，
    不随对话漂移）；``count`` 决定 LLM provider 要不要跑（只跑首条）；
    ``last_seq`` 是 provider 生成时的输入上界。
    """
    first: TitleMessage | None = None
    count: int = 0
    last_seq: int | None = None


@dataclass(frozen=True)
class SessionTitle:
    """fold 出的当前标题 + 它那条事件的持久化事实。"""
    title: str
    message_seqs: tuple[int, ...] = ()
    source: str = SOURCE_FALLBACK
    provider: str | None = None
    model: str | None = None
    event_seq: int = 0
    updated_at: float = 0.0
    #: 该标题事件里折叠出的原始 data（列表端点直接回传给前端用，避免二次查询）
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def pinned(self) -> bool:
        """用户改过名 → 钉住：后续任何自动生成都不得覆盖。"""
        return self.source == SOURCE_USER


def message_text_of(content: object) -> str:
    """从一条 user/message 的 ``content`` 里抽出**文本块**并拼接。

    ``content`` 是字符串（本项目当前形态），或 OpenAI 多模态的块列表。
    列表形态下只取 ``type == "text"`` 的块、用 ``\\n`` 连接（对齐参考实现）；
    其余块（image_url / input_audio 等）**一概丢弃**——纯图片消息因此得到空串，
    在下游被判为「不合格输入」。
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if isinstance(p, str))
    return ""


def title_message_of(event: SessionEvent) -> TitleMessage | None:
    """一条事件能否作为标题输入；不合格返回 ``None``。

    参考实现在这里用 ``normalizeSessionTitle(text, MAX_SAFE_INTEGER).length === 0``
    判「洗出来还有没有可见文本」；:func:`clean_title_text` 就是它去掉限额的那一步，
    语义等价且不用传一个假的上限。
    """
    if event.type != USER_MESSAGE:
        return None
    data = event.data if isinstance(event.data, dict) else {}
    source = data.get("source")
    # 缺省视为人：本项目当前所有 user/message 都是人写的（无 steering / 工具注入）
    if isinstance(source, dict) and source.get("kind", SOURCE_USER) != SOURCE_USER:
        return None
    text = message_text_of(data.get("content"))
    if not clean_title_text(text):
        return None
    return TitleMessage(seq=event.seq, text=text)


def collect_title_messages(
    events: list[SessionEvent], through_seq: int | None = None,
) -> list[TitleMessage]:
    """按 seq 序收集全部合格人类文本消息（可给一个含上界）。

    参考实现只在**一次 provider 生成**时物化完整前缀，平时靠增量投影保持 O(1)；
    这里没有增量投影（见模块文档），所以每次直接扫——调用点都是低频的。
    """
    out: list[TitleMessage] = []
    for ev in events:
        if through_seq is not None and ev.seq > through_seq:
            break
        msg = title_message_of(ev)
        if msg is not None:
            out.append(msg)
    return out


def fold_title_input(events: list[SessionEvent]) -> TitleInput:
    """折叠标题输入概况：**首条**合格消息 + 总数 + 最新 seq。"""
    first: TitleMessage | None = None
    count = 0
    last_seq: int | None = None
    for ev in events:
        msg = title_message_of(ev)
        if msg is None:
            continue
        if first is None:
            first = msg
        count += 1
        last_seq = msg.seq
    return TitleInput(first=first, count=count, last_seq=last_seq)


def session_title_of(event: SessionEvent) -> SessionTitle | None:
    """一条 ``session/title`` 事件 → :class:`SessionTitle`；其它事件返回 ``None``。

    读取侧对 data 做**防御性解析**：库里可能躺着旧版本写的事件（或被人手改过的
    行），字段缺失不该让整个会话列表 500。非法/空标题一律当作「没有标题」。
    """
    if event.type != TITLE:
        return None
    data = event.data if isinstance(event.data, dict) else {}
    title = data.get("title")
    if not isinstance(title, str) or not title:
        return None
    source = data.get("source")
    kind = source if isinstance(source, str) else (
        source.get("kind", SOURCE_FALLBACK) if isinstance(source, dict) else SOURCE_FALLBACK
    )
    if kind not in SOURCES:
        kind = SOURCE_FALLBACK
    provider = data.get("provider")
    model = data.get("model")
    if isinstance(source, dict):  # provider 来源的 route 记在 source 里
        provider = provider or source.get("provider")
        model = model or source.get("model")
    seqs = data.get("message_seqs")
    return SessionTitle(
        title=title,
        message_seqs=tuple(s for s in seqs if isinstance(s, int)) if isinstance(seqs, list) else (),
        source=kind,
        provider=provider if isinstance(provider, str) else None,
        model=model if isinstance(model, str) else None,
        event_seq=event.seq,
        updated_at=event.time,
        raw=data,
    )


def fold_session_title(events: list[SessionEvent]) -> SessionTitle | None:
    """fold 出**最新**标题（find-last）；无标题事件返回 ``None``。

    冷恢复路径：``Session.from_events`` 重建后对历史 Event Log fold 的标题，
    与实时写入时读到的必须完全一致——这正是标题存成事件而非可变字段的回报。
    """
    latest: SessionTitle | None = None
    for ev in events:
        found = session_title_of(ev)
        if found is not None:
            latest = found
    return latest
