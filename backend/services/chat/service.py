import json
from typing import Optional

from backend.core.connection import get_conn
from backend.services.chat.models import (
    ConversationCreate,
    ConversationUpdate,
    ConversationResponse,
    MessageCreate,
    MessageResponse,
)


def list_conversations_with_summary(
    tenant_id: int,
    channel: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> list[dict]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            params = [tenant_id]
            sql = """
                SELECT c.*,
                       (SELECT content FROM messages WHERE conversation_id = c.id ORDER BY created_at DESC LIMIT 1) as last_msg,
                       (SELECT created_at FROM messages WHERE conversation_id = c.id ORDER BY created_at DESC LIMIT 1) as last_time,
                       (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) as msg_count
                FROM conversations c
                WHERE c.tenant_id = %s
            """
            if channel:
                params.append(channel)
                sql += " AND c.channel = %s"
            if status:
                params.append(status)
                sql += " AND c.status = %s"
            sql += " ORDER BY c.updated_at DESC"
            sql += f" OFFSET {(page - 1) * page_size} LIMIT {page_size}"
            cur.execute(sql, params)
            return cur.fetchall()


def get_messages_by_channel_cid(
    tenant_id: int,
    channel_conversation_id: str,
    page: int = 1,
    page_size: int = 200,
) -> list[MessageResponse]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            sql = """
                SELECT m.* FROM messages m
                JOIN conversations c ON m.conversation_id = c.id
                WHERE m.tenant_id = %s AND c.channel_conversation_id = %s
                ORDER BY m.created_at ASC
            """
            sql += f" OFFSET {(page - 1) * page_size} LIMIT {page_size}"
            cur.execute(sql, (tenant_id, channel_conversation_id))
            rows = cur.fetchall()
            return [MessageResponse(**r) for r in rows]


def insert_message_raw(
    tenant_id: int,
    conversation_id: int,
    role: str,
    sender_name: Optional[str],
    content: str,
    content_type: str = "text",
    metadata: Optional[dict] = None,
) -> MessageResponse:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO messages (tenant_id, conversation_id, role, sender_name, content, content_type, metadata)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING *""",
                (
                    tenant_id,
                    conversation_id,
                    role,
                    sender_name,
                    content,
                    content_type,
                    json.dumps(metadata or {}, default=str),
                ),
            )
            row = cur.fetchone()
            return MessageResponse(**row)


def list_conversations(
    tenant_id: int,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> list[ConversationResponse]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            params = [tenant_id]
            sql = "SELECT * FROM conversations WHERE tenant_id = %s"
            if status:
                params.append(status)
                sql += " AND status = %s"
            sql += " ORDER BY updated_at DESC"
            sql += f" OFFSET {(page - 1) * page_size} LIMIT {page_size}"
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [ConversationResponse(**r) for r in rows]


def get_conversation(tenant_id: int, conversation_id: int) -> Optional[ConversationResponse]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM conversations WHERE tenant_id = %s AND id = %s",
                (tenant_id, conversation_id),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return ConversationResponse(**row)


def get_or_create_conversation(
    tenant_id: int,
    channel: str,
    channel_conversation_id: str,
    customer_name: Optional[str] = None,
    customer_id: Optional[str] = None,
) -> ConversationResponse:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM conversations WHERE tenant_id = %s AND channel = %s AND channel_conversation_id = %s",
                (tenant_id, channel, channel_conversation_id),
            )
            row = cur.fetchone()
            if row:
                return ConversationResponse(**row)

            cur.execute(
                """INSERT INTO conversations (tenant_id, channel, channel_conversation_id, customer_name, customer_id)
                   VALUES (%s, %s, %s, %s, %s)
                   RETURNING *""",
                (tenant_id, channel, channel_conversation_id, customer_name, customer_id),
            )
            new_row = cur.fetchone()
            return ConversationResponse(**new_row)


def create_conversation(
    tenant_id: int,
    data: ConversationCreate,
) -> ConversationResponse:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO conversations (tenant_id, channel, channel_conversation_id, customer_name, customer_id, agent_key, assigned_to)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING *""",
                (
                    tenant_id,
                    data.channel,
                    data.channel_conversation_id,
                    data.customer_name,
                    data.customer_id,
                    data.agent_key,
                    data.assigned_to,
                ),
            )
            row = cur.fetchone()
            return ConversationResponse(**row)


def update_conversation(
    tenant_id: int,
    conversation_id: int,
    data: ConversationUpdate,
) -> Optional[ConversationResponse]:
    updates = data.model_dump(exclude_none=True)
    if not updates:
        return get_conversation(tenant_id, conversation_id)

    updates["updated_at"] = "now()"

    set_clause = ", ".join(
        f"{col} = %s" if col != "updated_at" else f"{col} = now()"
        for col in updates
    )
    values = [v for k, v in updates.items() if k != "updated_at"]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE conversations SET {set_clause} WHERE tenant_id = %s AND id = %s RETURNING *",
                values + [tenant_id, conversation_id],
            )
            row = cur.fetchone()
            if row is None:
                return None
            return ConversationResponse(**row)


def list_messages(
    tenant_id: int,
    conversation_id: int,
    page: int = 1,
    page_size: int = 50,
    order: str = "ASC",
) -> list[MessageResponse]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            sql = "SELECT * FROM messages WHERE tenant_id = %s AND conversation_id = %s ORDER BY created_at " + order
            sql += f" OFFSET {(page - 1) * page_size} LIMIT {page_size}"
            cur.execute(sql, (tenant_id, conversation_id))
            rows = cur.fetchall()
            return [MessageResponse(**r) for r in rows]


def get_conversation_session_id(tenant_id: int, conversation_id: int) -> Optional[str]:
    """conversation → Agent Runtime session 映射读取（会话级记忆恢复用）。

    该映射属 chat 域（conversation 是 chat 资源），由 API 层维护：每次 chat 完成
    后把 reply.session_id 写回；请求无显式 session_id 时读它做冷恢复。与
    RequestContext.conversation_id 同层，AgentRuntime 不感知 conversation。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT session_id FROM conversations WHERE tenant_id = %s AND id = %s",
                (tenant_id, conversation_id),
            )
            row = cur.fetchone()
            return row["session_id"] if row else None


def save_conversation_session_id(tenant_id: int, conversation_id: int, session_id: str) -> None:
    """conversation → session 映射写回（幂等覆盖：该 conversation 最近一次 chat 的 session）"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE conversations SET session_id = %s, updated_at = now() "
                "WHERE tenant_id = %s AND id = %s",
                (session_id, tenant_id, conversation_id),
            )


def create_message(
    tenant_id: int,
    conversation_id: int,
    data: MessageCreate,
) -> MessageResponse:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO messages (tenant_id, conversation_id, role, sender_name, content, content_type, metadata)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING *""",
                (
                    tenant_id,
                    conversation_id,
                    data.role,
                    data.sender_name,
                    data.content,
                    data.content_type,
                    json.dumps(data.model_dump().get("metadata", {}), default=str),
                ),
            )
            row = cur.fetchone()
            return MessageResponse(**row)
