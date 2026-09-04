"""Session Event Log 持久化测试(PostgreSQL,项目主库 saas)

覆盖目标(对应 Session 架构改造的测试要求):
  1. 创建 Session → SessionStore(内存热区)
  2. Event append → append_events(pending)→ flush → PostgreSQL(session_events)
  3. flush 边界:flush 前不落库,flush 后落库;跨批次增量写
  4. reload:清空内存 → persistence.load → from_events replay → Session 等价恢复
  5. 进程重启模拟:进程 A 写库 → 新 store + 新 persistence(进程 B)→ 冷恢复 → 继续对话
  6. SessionNotFound:不存在的 session_id 行为明确(load→None;runtime 不静默续接,新建并告警)
  7. Event 顺序:seq 单调连续,恢复后继续 append 不重号
  8. 幂等:重复 append_events / 重复 flush(含游标丢失的新实例)不产生重复行

运行方式(backend 容器内,DB 为 compose 的 db 服务):
  pip install pytest && python -m pytest backend/tests -v
"""
import asyncio
import logging
import uuid

import pytest

from backend.agents.runtime import AgentRuntime
from backend.agents.runtime.session import (
    NoopPersistence,
    PostgresSessionPersistence,
    Session,
    SessionStore,
    set_session_persistence,
)
from backend.agents.runtime.session import store as store_mod
from backend.agents.runtime.session.events import (
    ASSISTANT_MESSAGE,
    SEED,
    TURN_END,
    USER_MESSAGE,
)
from backend.core.connection import get_conn
from backend.core.schema import init_db


def _new_sid() -> str:
    return f"test-{uuid.uuid4().hex}"


def _db_count(session_id: str) -> int:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS n FROM session_events WHERE session_id = %s", (session_id,),
            )
            return cur.fetchone()["n"]


def _db_events(session_id: str) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT seq, event_type AS type, data FROM session_events "
                "WHERE session_id = %s ORDER BY seq",
                (session_id,),
            )
            return list(cur.fetchall())


def _db_has_header(session_id: str) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM session_headers WHERE session_id = %s", (session_id,),
            )
            return cur.fetchone() is not None


def _cleanup(session_id: str) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM session_events WHERE session_id = %s", (session_id,))
            cur.execute("DELETE FROM session_headers WHERE session_id = %s", (session_id,))


@pytest.fixture(scope="module", autouse=True)
def db_ready():
    """模块级:确保 session_headers / session_events 已建(幂等,与服务 startup 相同入口)"""
    init_db()
    yield
    # 兜底清理:测试遗留数据不留在库里
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM session_events WHERE session_id LIKE 'test-%'")
            cur.execute("DELETE FROM session_headers WHERE session_id LIKE 'test-%'")


@pytest.fixture
def persistence():
    """每个测试独立的 PostgresSessionPersistence 实例(隔离 _pending/_written 游标)"""
    p = PostgresSessionPersistence()
    yield p


# ── 1. 创建 Session → SessionStore ──

def test_sessionstore_create_get_put_delete():
    store = SessionStore()
    s = store.create(init_messages=[{"role": "system", "content": "sys"}])
    sid = s.header.id
    assert store.get(sid) is s                    # create 即登记
    assert store.get("no-such-session") is None
    assert s.seq == 1 and s.log[0].type == SEED   # seed 进入 Event Log

    # put:登记已存在实例(lazy restore 路径)
    restored = Session.from_events(s.header, s.events)
    store.put(restored)
    assert store.get(sid) is restored             # put 覆盖登记

    assert store.delete(sid) is True
    assert store.get(sid) is None
    assert store.delete(sid) is False             # 二次删除返回 False


# ── 2. Session → Event → append_events → flush → PostgreSQL ──

def test_append_flush_writes_postgres(persistence):
    sid = _new_sid()
    try:
        s = SessionStore().create(init_messages=[{"role": "system", "content": "sys"}])
        sid = s.header.id
        persistence.create(sid, s.header)
        s.append(USER_MESSAGE, {"content": "你好"})
        s.append(ASSISTANT_MESSAGE, {"content": "你好！有什么可以帮你？", "tool_calls": []})

        persistence.append_events(sid, s.events)   # 只进 pending
        assert _db_count(sid) == 0                 # flush 前不落库
        assert _db_has_header(sid) is True         # header 随 create 落库

        persistence.flush(sid)
        rows = _db_events(sid)
        assert [r["type"] for r in rows] == [SEED, USER_MESSAGE, ASSISTANT_MESSAGE]
        assert [r["seq"] for r in rows] == [1, 2, 3]
        assert rows[0]["data"] == {"role": "system", "content": "sys"}
        assert rows[1]["data"] == {"content": "你好"}
    finally:
        _cleanup(sid)


# ── 3. flush 是持久化边界(批处理 + 跨批次增量) ──

def test_flush_batch_boundary_and_incremental(persistence):
    s = SessionStore().create()
    sid = s.header.id
    try:
        persistence.create(sid, s.header)
        s.append(USER_MESSAGE, {"content": "q1"})
        persistence.append_events(sid, s.events)
        s.append(USER_MESSAGE, {"content": "q2"})
        persistence.append_events(sid, s.events)   # 两批 pending 合并
        assert _db_count(sid) == 0

        persistence.flush(sid)
        assert _db_count(sid) == 2                 # 一次 flush = 全部 pending

        s.append(USER_MESSAGE, {"content": "q3"})
        persistence.append_events(sid, s.events)   # 全量传入,内部按游标 diff → 只写 seq=3
        persistence.flush(sid)
        assert _db_count(sid) == 3
    finally:
        _cleanup(sid)


# ── 4. reload:Event Log → replay → Session(不依赖进程内存) ──

def test_load_replay_restores_equivalent_session(persistence):
    s = SessionStore().create(init_messages=[{"role": "system", "content": "sys"}])
    sid = s.header.id
    try:
        s.append(USER_MESSAGE, {"content": "查一下订单"})
        s.append(ASSISTANT_MESSAGE, {"content": "好的", "tool_calls": []})
        persistence.create(sid, s.header)
        persistence.append_events(sid, s.events)
        persistence.flush(sid)
        before_msgs = s.derive_messages()          # seed + user + assistant

        loaded = persistence.load(sid)             # 冷读取(新进程视角)
        assert loaded is not None
        header, events = loaded
        assert [e.seq for e in events] == [1, 2, 3]
        assert header.id == sid

        s2 = Session.from_events(header, events)   # replay
        assert s2.seq == 3                         # _seq 恢复到 max
        assert s2.derive_messages() == before_msgs # 派生 LLM 消息与原 Session 完全一致

        store2 = SessionStore()
        store2.put(s2)
        assert store2.get(sid) is s2
    finally:
        _cleanup(sid)


# ── 5. 进程重启模拟:进程 A 落库 → 进程 B(全新 store + persistence)冷恢复继续 ──

def test_process_restart_cold_restore_continues(persistence):
    # ── 进程 A:对话一轮,flush ──
    s = SessionStore().create(init_messages=[{"role": "system", "content": "sys"}])
    sid = s.header.id
    persistence.create(sid, s.header)
    s.append(USER_MESSAGE, {"content": "第一问"})
    persistence.append_events(sid, s.events)
    persistence.flush(sid)
    assert _db_count(sid) == 2

    # 进程 A 状态全部丢弃(内存 Session / pending / 游标都随进程消失)

    # ── 进程 B:全新 store + 全新 persistence 实例 ──
    store_b = SessionStore()
    persistence_b = PostgresSessionPersistence()
    try:
        assert store_b.get(sid) is None
        loaded = persistence_b.load(sid)
        assert loaded is not None
        header, events = loaded
        assert [e.type for e in events] == [SEED, USER_MESSAGE]

        s_b = Session.from_events(header, events)
        store_b.put(s_b)
        # B 继续对话:事件从 seq=3 起,不重复旧事件
        s_b.append(USER_MESSAGE, {"content": "第二问"})
        persistence_b.append_events(s_b.header.id, s_b.events)
        persistence_b.flush(s_b.header.id)

        # ── 进程 C:再全量核对顺序完整无重复 ──
        persistence_c = PostgresSessionPersistence()
        _, events_c = persistence_c.load(sid)
        assert [e.seq for e in events_c] == [1, 2, 3]
        assert [e.type for e in events_c] == [SEED, USER_MESSAGE, USER_MESSAGE]
        assert events_c[2].data == {"content": "第二问"}
    finally:
        _cleanup(sid)


# ── 6. SessionNotFound:不存在的 session_id 行为明确 ──

def test_persistence_missing_session_returns_none(persistence):
    sid = _new_sid()
    assert persistence.exists(sid) is False
    assert persistence.load(sid) is None          # 明确的不存在,不是空 Session
    assert sid not in persistence.list()


def test_runtime_unknown_session_id_creates_new_and_warns(persistence, caplog):
    """用户要求的红线:未知 session_id 绝不能静默续接旧上下文。
    runtime 行为:告警(可观察)+ 新建 Session,并回传新 id 让客户端更新。"""
    set_session_persistence(persistence)
    new_sid = None
    try:
        rt = AgentRuntime(
            key="test-agent",
            definition={"config": {"session_enabled": True, "system_prompt": "sys-x",
                                   "fallback_reply": "服务未配置"}},
        )
        with caplog.at_level(logging.WARNING, logger="backend.agents.runtime.agent_runtime"):
            reply = asyncio.run(rt.reply(1, "hi", session_id="ghost-session-id"))
        assert reply.session_id is not None
        new_sid = reply.session_id
        assert new_sid != "ghost-session-id"            # 新会话,不是续接幽灵会话
        assert any("ghost-session-id" in r.message and "创建新 Session" in r.message
                   for r in caplog.records)             # 行为可观察:打了 warning

        # 新 Session 已落库(header + seed),可被后续冷恢复
        assert persistence.exists(new_sid)
        header, events = persistence.load(new_sid)
        assert events[0].type == SEED
        assert events[0].data == {"role": "system", "content": "sys-x"}
    finally:
        set_session_persistence(NoopPersistence())
        if new_sid:
            _cleanup(new_sid)


# ── 6b. runtime 冷恢复续接同一 Session(对应「情况 2:Store miss → Persistence 命中」) ──

def test_runtime_cold_restore_resumes_same_session(persistence, monkeypatch):
    set_session_persistence(persistence)
    try:
        rt = AgentRuntime(
            key="test-agent",
            definition={"config": {"session_enabled": True, "system_prompt": "sys-x",
                                   "fallback_reply": "服务未配置"}},
        )
        # 进程 A:首轮对话,产生 session_id 并落库
        r1 = asyncio.run(rt.reply(1, "第一问"))
        sid = r1.session_id
        assert persistence.load(sid) is not None

        # 进程重启:换全新 SessionStore(内存清空)+ 全新 persistence 实例
        monkeypatch.setattr(store_mod, "_store", SessionStore())
        set_session_persistence(PostgresSessionPersistence())

        # 进程 B:带同一 session_id → 冷恢复,续接同一会话(而不是新建)
        r2 = asyncio.run(rt.reply(1, "第二问", session_id=sid))
        assert r2.session_id == sid                # 同一会话续接
    finally:
        set_session_persistence(NoopPersistence())
        _cleanup(sid)


# ── 7. seq 顺序:单调连续、恢复后不重号 ──

def test_seq_monotonic_and_no_reset_after_restore(persistence):
    s = SessionStore().create()
    sid = s.header.id
    try:
        persistence.create(sid, s.header)
        for i in range(5):
            s.append(USER_MESSAGE, {"content": f"q{i}"})
        persistence.append_events(sid, s.events)
        persistence.flush(sid)

        # 冷恢复后继续 append → seq 从 6 起(不重置、不重复)
        _, events = persistence.load(sid)
        assert [e.seq for e in events] == [1, 2, 3, 4, 5]
        header, events = persistence.load(sid)
        s2 = Session.from_events(header, events)
        s2.append(USER_MESSAGE, {"content": "q6"})
        s2.append(TURN_END, {"stop_reason": "completed"})
        assert [e.seq for e in s2.log] == [1, 2, 3, 4, 5, 6, 7]   # 内存侧连续
    finally:
        _cleanup(sid)


# ── 8. 幂等:重复 append / 重复 flush 不产生重复行 ──

def test_idempotent_append_and_flush(persistence):
    s = SessionStore().create()
    sid = s.header.id
    try:
        persistence.create(sid, s.header)
        s.append(USER_MESSAGE, {"content": "q1"})
        persistence.append_events(sid, s.events)
        persistence.flush(sid)

        # 同实例重复 flush(游标已推进 → pending 空)
        persistence.flush(sid)
        assert _db_count(sid) == 1                 # 无 init_messages → 仅 user/message 1 条

        # 游标丢失的新实例重复写同一批 → DB 层 UNIQUE(session_id, seq) 兜底,不重复
        p2 = PostgresSessionPersistence()
        p2.append_events(sid, s.events)
        p2.flush(sid)
        assert _db_count(sid) == 1
    finally:
        _cleanup(sid)
