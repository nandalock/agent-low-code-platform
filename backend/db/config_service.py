"""Agent 配置读写"""
import json

from backend.db.connection import get_conn


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
