"""记忆模块配置 — 默认值 + DB 读写

模仿 FaqAgent 的 template + config_service 模式：
  - TEMPLATE 提供默认值
  - get_config / save_config 读写 DB（key="memory"）
"""
from __future__ import annotations

import json

from backend.core.connection import get_conn

# 默认值（参考 ReMe 的参数体系）
TEMPLATE = {
    "max_tokens": 4000,
    "compact_ratio": 0.7,
    "reserve_tokens": 800,
    "tool_keep_n": 3,
    "tool_max_chars": 1000,
}

MEMORY_KEY = "memory"


def get_config() -> dict:
    """读取记忆配置，不存在则返回默认值"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT config FROM agent_configs WHERE agent_key = %s",
                (MEMORY_KEY,),
            )
            row = cur.fetchone()
            if row:
                return {**TEMPLATE, **dict(row["config"])}
    return dict(TEMPLATE)


def save_config(config: dict) -> dict:
    """保存记忆配置，与默认值合并"""
    merged = {**TEMPLATE, **config}
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO agent_configs (agent_key, config, updated_at)
                   VALUES (%s, %s::jsonb, now())
                   ON CONFLICT (agent_key)
                   DO UPDATE SET config = EXCLUDED.config, updated_at = now()
                   RETURNING config""",
                (MEMORY_KEY, json.dumps(merged)),
            )
            return dict(cur.fetchone()["config"])
