"""conversation ↔ Agent Runtime Session 映射测试（chat 域，PostgreSQL）

背景：历史会话从任意入口继续（重启/换浏览器/切会话）需要恢复 Agent 上下文。
conversations.session_id 记录该对话最近一次 chat 使用的 Session；
请求带 conversation_id 而无 session_id 时，API 层读此映射 → AgentRuntime lazy restore。
"""
import uuid

from backend.core.connection import get_conn
from backend.services.chat import service as chat_service

TEST_TENANT = 99991


def test_conversation_session_mapping_roundtrip():
    conv = chat_service.create_conversation(
        TEST_TENANT,
        chat_service.ConversationCreate(channel="agent:test_agent"),
    )
    try:
        # 初始无映射
        assert chat_service.get_conversation_session_id(TEST_TENANT, conv.id) is None

        # 写回（每轮 chat 完成后 API 层调用）
        sid = uuid.uuid4().hex
        chat_service.save_conversation_session_id(TEST_TENANT, conv.id, sid)
        assert chat_service.get_conversation_session_id(TEST_TENANT, conv.id) == sid

        # 覆盖更新：映射指向该 conversation 最近一次 chat 的 Session
        sid2 = uuid.uuid4().hex
        chat_service.save_conversation_session_id(TEST_TENANT, conv.id, sid2)
        assert chat_service.get_conversation_session_id(TEST_TENANT, conv.id) == sid2

        # 不存在的 conversation：读 None、写不报错（no-op）
        assert chat_service.get_conversation_session_id(TEST_TENANT, 999999999) is None
        chat_service.save_conversation_session_id(TEST_TENANT, 999999999, sid)
    finally:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM conversations WHERE id = %s", (conv.id,))
