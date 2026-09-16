"""会话标题 —— 契约、净化、折叠与服务

标题 = 追加型日志事件 ``session/title``（不是可变字段，为了可重放）。三个来源，
新的赢：``fallback``（首条合格人类消息的前导词）→ ``provider``（LLM，至多一次）
→ ``user``（用户改名，**一旦改名即钉住**）。

  normalize.py    净化（去 OSC/CSI/ESC/控制符/零宽与双向）+ 按 UTF-8 **字节**截断，
                  不切码点。顺序即契约。
  projection.py   把 ``session/title`` fold 成「当前标题」；判定什么算**合格输入**
                  （只有人类 user/message 的**文本块** —— 纯图片不合格）。
  service.py      接受与钉住：同步补 fallback、异步排 LLM（不阻塞主回复）、
                  改名写 user 源事件。

**为什么不拆到 projections/**：这个文件夹是「标题」这件事的完整实现。只有 fold
是只读的，而 service 会写事件 —— 一个契约的两半分居两个文件夹，读的人得来回跳。
见 ``../projections/__init__.py`` 的说明。
"""
from backend.agents.runtime.session.title.normalize import (
    FALLBACK_MAX_BYTES,
    FALLBACK_MAX_WORDS,
    MAX_TITLE_BYTES,
    clean_title_text,
    fallback_session_title,
    normalize_session_title,
    truncate_title_utf8,
)
from backend.agents.runtime.session.title.projection import (
    SOURCE_FALLBACK,
    SOURCE_PROVIDER,
    SOURCE_USER,
    SessionTitle,
    TitleInput,
    TitleMessage,
    collect_title_messages,
    fold_session_title,
    fold_title_input,
    session_title_of,
    title_message_of,
)
from backend.agents.runtime.session.title.service import (
    SessionTitleService,
    get_session_title_service,
)

__all__ = [
    "FALLBACK_MAX_BYTES",
    "FALLBACK_MAX_WORDS",
    "MAX_TITLE_BYTES",
    "clean_title_text",
    "fallback_session_title",
    "normalize_session_title",
    "truncate_title_utf8",
    "SOURCE_FALLBACK",
    "SOURCE_PROVIDER",
    "SOURCE_USER",
    "SessionTitle",
    "TitleInput",
    "TitleMessage",
    "collect_title_messages",
    "fold_session_title",
    "fold_title_input",
    "session_title_of",
    "title_message_of",
    "SessionTitleService",
    "get_session_title_service",
]
