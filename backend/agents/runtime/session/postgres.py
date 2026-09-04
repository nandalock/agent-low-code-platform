"""PostgresSessionPersistence — SessionPersistence 的 PostgreSQL 实现

表结构(core/schema.py init_db() 幂等建表,项目主库,不引入 SQLite):
  session_headers — Session 元信息(session_id PK)
  session_events  — Event Log 唯一事实来源;UNIQUE(session_id, seq) 兜底并发/幂等

进程内语义(刻意保持轻量,无队列 / 无 worker / 无跨进程锁):
  append_events → 收进 pending 缓冲(按 seq 与「已写游标」去重)
  flush         → 一次 flush = 一个事务,INSERT ... ON CONFLICT (session_id, seq) DO NOTHING
                  (全部成功或全部失败;失败保留 pending 可重放;重复 flush 幂等)
  load          → 读 durable 全量事件 ORDER BY seq(冷恢复 replay 源),并把 DB 最大 seq
                  记为已写游标 —— 恢复出的 Session 后续 flush 只写新事件

seq 并发保证:单个 Session 的 append 由 Session 内部自增且在单进程事件循环内同步执行
(无 await 间隙),进程内天然顺序;跨进程写同一 session_id 由 UNIQUE(session_id, seq)
作为最后一道约束拒绝冲突。本实现不引入分布式锁。

与 SessionStore 的边界:本类不感知 SessionStore;只做「事件的持久化」。恢复编排在
AgentRuntime(lazy restore:store.get miss → persistence.load → from_events → store.put)。
"""
import json
import logging

from backend.agents.runtime.session.events import SessionEvent, SessionHeader
from backend.agents.runtime.session.persistence import SessionPersistence
from backend.core.connection import get_conn

logger = logging.getLogger(__name__)


class PostgresSessionPersistence(SessionPersistence):
    """PostgreSQL 实现:create / append_events → pending / flush → durable / load / exists / list"""

    def __init__(self):
        # session_id → 未落库事件(按 seq 升序,append 顺序即到达顺序)
        self._pending: dict[str, list[SessionEvent]] = {}
        # session_id → 已确认落库的最大 seq(进程内游标;进程退出即弃,
        # DB 全量是持久真相,load 时按 DB 重建游标)
        self._written: dict[str, int] = {}

    # ── 写路径 ──

    def create(self, session_id: str, header: SessionHeader) -> None:
        """注册 Session 元信息。ON CONFLICT DO NOTHING:已存在的 header 不覆盖
        (同一 session_id 不应有第二个 header,防进程重启后重复 create 破坏首版)。"""
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO session_headers
                           (session_id, version, created_at, cwd, parent_session, seed_length, delegation_depth)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (session_id) DO NOTHING""",
                    (
                        session_id,
                        header.version,
                        header.created_at,
                        header.cwd,
                        header.parent_session,
                        header.seed_length,
                        header.delegation_depth,
                    ),
                )

    def append_events(self, session_id: str, events: list[SessionEvent]) -> None:
        """事件进入 pending 缓冲(不落盘)。按 seq 去重:跳过已落库(≤游标)与已入 pending 的。"""
        if not events:
            return
        base = self._written.get(session_id, 0)
        pending = self._pending.setdefault(session_id, [])
        seen = {e.seq for e in pending}
        new_events = [e for e in events if e.seq > base and e.seq not in seen]
        if new_events:
            pending.extend(new_events)

    def flush(self, session_id: str | None = None) -> None:
        """持久化边界:一次 flush 一个事务(session_id=None 时 flush 全部 pending)。

        INSERT ... ON CONFLICT (session_id, seq) DO NOTHING:
          - 幂等:重复 flush 同一批事件不产生重复行
          - 失败:事务回滚 + pending 保留,下次 flush 重放
        """
        targets = [session_id] if session_id is not None else list(self._pending)
        for sid in targets:
            events = self._pending.get(sid)
            if not events:
                continue
            try:
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        for e in events:
                            cur.execute(
                                """INSERT INTO session_events
                                       (session_id, seq, event_type, event_time, data, source_event_seqs)
                                   VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)
                                   ON CONFLICT (session_id, seq) DO NOTHING""",
                                (
                                    sid,
                                    e.seq,
                                    e.type,
                                    e.time,
                                    json.dumps(e.data, ensure_ascii=False, default=str),
                                    json.dumps(e.source_event_seqs, ensure_ascii=False)
                                    if e.source_event_seqs is not None else None,
                                ),
                            )
            except Exception:
                logger.exception(f"PostgresSessionPersistence flush 失败: session_id={sid}, "
                                 f"pending={len(events)}, 将保留 pending 等待重试")
                raise
            self._pending.pop(sid, None)
            self._written[sid] = max(e.seq for e in events)

    # ── 读路径(冷恢复 replay 源) ──

    def load(self, session_id: str) -> tuple[SessionHeader, list[SessionEvent]] | None:
        """读 durable 完整 Event Log(ORDER BY seq);无 header(或 header 已被删)返回 None。

        load 后把 DB 最大 seq 记为已写游标:恢复出的 Session 在内存继续 append → flush,
        只会写入该 Session 新产生的事件(旧事件已在 DB,无需重写)。
        """
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM session_headers WHERE session_id = %s", (session_id,),
                )
                h = cur.fetchone()
                if h is None:
                    return None
                cur.execute(
                    "SELECT * FROM session_events WHERE session_id = %s ORDER BY seq", (session_id,),
                )
                rows = cur.fetchall()

        header = SessionHeader(
            id=h["session_id"],
            version=h["version"],
            created_at=h["created_at"],
            cwd=h["cwd"],
            parent_session=h["parent_session"],
            seed_length=h["seed_length"],
            delegation_depth=h["delegation_depth"],
        )
        events = [
            SessionEvent(
                type=r["event_type"],
                seq=r["seq"],
                time=r["event_time"],
                data=r["data"],  # psycopg2 自动反序列化 JSONB → dict
                source_event_seqs=r["source_event_seqs"],
            )
            for r in rows
        ]
        if events:
            self._written[session_id] = events[-1].seq
        logger.info(f"PostgresSessionPersistence load: session_id={session_id}, events={len(events)}")
        return header, events

    def exists(self, session_id: str) -> bool:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM session_headers WHERE session_id = %s", (session_id,),
                )
                return cur.fetchone() is not None

    def list(self) -> list[str]:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT session_id FROM session_headers ORDER BY created_at DESC")
                return [r["session_id"] for r in cur.fetchall()]
