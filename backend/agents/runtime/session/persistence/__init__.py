"""SessionPersistence — Event Log 的落库 seam

「Session 不感知任何存储」这条边界就落在这个文件夹上：Session / SessionStore /
AgentLoop 全都只对着 :class:`SessionPersistence` 这个接口说话，换成 Postgres、
换回 Noop、或者塞一个测试替身，执行链一行都不用改。

  base.py     接口 + NoopPersistence + get/set 装配 + flush_session_events
  postgres.py PostgreSQL 实现（生产装配，main.py startup 注入）

这里的 re-export 是**文件夹的接口**：调用方可以只认
``from backend.agents.runtime.session.persistence import SessionPersistence``，
不必知道它在哪个文件里。这与 ``session/__init__.py`` 的分工一致 —— 那个是**整个
包**的对外界面，这个是**本文件夹**的。
"""
from backend.agents.runtime.session.persistence.base import (
    NoopPersistence,
    SessionPersistence,
    flush_session_events,
    get_session_persistence,
    set_session_persistence,
)
from backend.agents.runtime.session.persistence.postgres import PostgresSessionPersistence

__all__ = [
    "SessionPersistence",
    "NoopPersistence",
    "PostgresSessionPersistence",
    "get_session_persistence",
    "set_session_persistence",
    "flush_session_events",
]
