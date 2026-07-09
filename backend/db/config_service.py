"""Agent 配置读写"""
import json

from backend.db.connection import get_conn


def get_agent_definition(agent_key: str) -> dict | None:
    """读取 agent 定义（name, desc, config）"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT agent_key, name, description, config, status, agent_type FROM agent_definitions WHERE agent_key = %s",
                (agent_key,),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def list_agent_definitions() -> list[dict]:
    """列出所有 agent 定义"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT agent_key, name, description, config, status, agent_type FROM agent_definitions ORDER BY agent_key")
            return [dict(r) for r in cur.fetchall()]


def get_agent_config(agent_key: str) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT config FROM agent_configs WHERE agent_key = %s",
                (agent_key,),
            )
            row = cur.fetchone()
            return dict(row["config"]) if row else None


def save_agent_config(agent_key: str, config: dict) -> dict:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO agent_configs (agent_key, config, updated_at)
                   VALUES (%s, %s::jsonb, now())
                   ON CONFLICT (agent_key)
                   DO UPDATE SET config = EXCLUDED.config, updated_at = now()
                   RETURNING config""",
                (agent_key, json.dumps(config)),
            )
            return dict(cur.fetchone()["config"])


# ── L1 关键字 CRUD（独立表 router_l1_keywords） ──

def list_l1_keywords(router_key: str) -> list[dict]:
    """列出路由器的所有 L1 关键字规则，按 id 排序"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, router_key, keywords, target, created_at FROM router_l1_keywords WHERE router_key = %s ORDER BY id",
                (router_key,),
            )
            return [dict(r) for r in cur.fetchall()]


def create_l1_keyword(router_key: str, keywords: list[str], target: str) -> dict:
    """新增一条 L1 关键字规则"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO router_l1_keywords (router_key, keywords, target) VALUES (%s, %s, %s) RETURNING id, router_key, keywords, target, created_at",
                (router_key, keywords, target),
            )
            row = dict(cur.fetchone())
        conn.commit()
    return row


def update_l1_keyword(rule_id: int, router_key: str, keywords: list[str], target: str) -> dict | None:
    """更新一条 L1 关键字规则"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE router_l1_keywords SET keywords = %s, target = %s WHERE id = %s AND router_key = %s RETURNING id, router_key, keywords, target, created_at",
                (keywords, target, rule_id, router_key),
            )
            row = cur.fetchone()
        conn.commit()
    return dict(row) if row else None


def delete_l1_keyword(rule_id: int, router_key: str) -> bool:
    """删除一条 L1 关键字规则"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM router_l1_keywords WHERE id = %s AND router_key = %s",
                (rule_id, router_key),
            )
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted
