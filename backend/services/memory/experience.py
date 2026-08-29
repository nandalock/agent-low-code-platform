"""经验记忆 — 跨会话检索用户画像和历史摘要"""
from __future__ import annotations

from backend.core.connection import get_conn


class ExperienceMemory:
    """跨会话经验检索"""

    def get_profile(self, tenant_id: int, user_id: str) -> list[str]:
        """读用户画像（facts 列表）"""
        if not user_id:
            return []
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT facts FROM user_profiles WHERE tenant_id = %s AND user_id = %s",
                    (tenant_id, user_id),
                )
                row = cur.fetchone()
                return list(row["facts"]) if row else []

    def get_recent_conversations(
        self, tenant_id: int, user_id: str, limit: int = 3,
    ) -> list[dict]:
        """该用户最近的会话摘要"""
        if not user_id:
            return []
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, channel, status,
                              (SELECT content FROM messages
                               WHERE conversation_id = c.id
                               ORDER BY created_at ASC LIMIT 1) as first_msg,
                              created_at
                       FROM conversations c
                       WHERE tenant_id = %s AND customer_id = %s
                       ORDER BY created_at DESC LIMIT %s""",
                    (tenant_id, user_id, limit),
                )
                return cur.fetchall()

    def render_profile(self, tenant_id: int, user_id: str) -> str:
        """画像 → prompt 文本"""
        facts = self.get_profile(tenant_id, user_id)
        if not facts:
            return ""
        lines = [f"- {f}" for f in facts]
        return f"## 用户画像\n" + "\n".join(lines)
