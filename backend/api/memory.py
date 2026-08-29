"""记忆系统 API"""
import json
import time
import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI

from backend.core.connection import get_conn
from backend.services.memory import MemoryContext, SessionMemory, MemoryCompressor, ExperienceMemory
from backend.services.memory.config import get_config as get_memory_config
from backend.services.memory.summarizer import EXTRACT_PROMPT
from backend.agents.config_service import get_agent_definition, get_agent_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memory", tags=["Memory"])


# ── Pydantic models ──

class DebugMessage(BaseModel):
    role: str       # customer | agent | system | tool
    content: str


class DebugRunRequest(BaseModel):
    messages: list[DebugMessage] = []
    conversation_id: Optional[int] = None
    system_prompt: str = ""
    user_id: str = "debug_user"
    llm_override: Optional[dict] = None


# ── Existing endpoints ──

@router.get("/users")
def list_user_profiles(
    x_tenant_id: int = Header(alias="X-Tenant-ID"),
) -> list[dict]:
    """获取所有有画像的用户列表"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT up.*, c.customer_name
                   FROM user_profiles up
                   LEFT JOIN conversations c ON c.tenant_id = up.tenant_id
                     AND c.customer_id = up.user_id
                     AND c.id = (
                       SELECT id FROM conversations
                       WHERE tenant_id = up.tenant_id AND customer_id = up.user_id
                       ORDER BY updated_at DESC LIMIT 1
                     )
                   WHERE up.tenant_id = %s
                   ORDER BY up.updated_at DESC""",
                (x_tenant_id,),
            )
            rows = cur.fetchall()
            return [
                {
                    "user_id": r["user_id"],
                    "customer_name": r.get("customer_name") or r["user_id"],
                    "facts_count": len(r["facts"]),
                    "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                }
                for r in rows
            ]


@router.get("/users/{user_id}")
def get_user_profile(
    user_id: str,
    x_tenant_id: int = Header(alias="X-Tenant-ID"),
) -> dict:
    """获取单个用户的画像详情"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM user_profiles WHERE tenant_id = %s AND user_id = %s",
                (x_tenant_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "用户画像不存在")
            return {
                "user_id": row["user_id"],
                "facts": row["facts"],
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            }


# ── Debug endpoint ──


@router.post("/debug/run")
async def debug_run_memory(
    body: DebugRunRequest,
    x_tenant_id: int = Header(alias="X-Tenant-ID"),
) -> dict:
    """运行 MemoryContext 管道并返回所有中间状态"""
    t0 = time.perf_counter()
    try:
        # 1. Resolve messages
        raw_messages: list[dict] = []
        if body.conversation_id is not None:
            from backend.services.chat import service as chat_service
            conv = chat_service.get_conversation(x_tenant_id, body.conversation_id)
            if not conv:
                raise HTTPException(404, "会话不存在")
            db_msgs = chat_service.list_messages(
                x_tenant_id, body.conversation_id, page=1, page_size=500
            )
            raw_messages = [
                {"role": m.role, "content": m.content}
                for m in db_msgs
            ]
        else:
            raw_messages = [{"role": m.role, "content": m.content} for m in body.messages]

        if not raw_messages:
            return {"ok": False, "error": "没有消息", "result": None}

        # 2. Create LLM (reuse supervisor config)
        definition = get_agent_definition("supervisor") or {}
        base = definition.get("config", {})
        config = {**base, **(get_agent_config("supervisor") or {})}
        overrides = body.llm_override or {}
        llm = ChatOpenAI(
            model=overrides.get("model") or config.get("model") or "deepseek-chat",
            api_key=overrides.get("api_key") or config.get("api_key") or "sk-xxx",
            base_url=overrides.get("base_url") or config.get("base_url") or "https://api.deepseek.com",
            temperature=0,
        )

        # 3. Create fresh MemoryContext
        session = SessionMemory()
        compressor = MemoryCompressor()
        experience = ExperienceMemory()
        ctx = MemoryContext(session=session, compressor=compressor, experience=experience)

        # 4. Capture intermediate states (replicating _process() with debug hooks)
        intermediate: dict = {}

        # 4a. Config snapshot
        cfg = get_memory_config()
        intermediate["config"] = dict(cfg)
        threshold = int(cfg["max_tokens"] * cfg["compact_ratio"] * 0.9)
        system_tokens = session._estimate_tokens(body.system_prompt)
        intermediate["threshold"] = threshold
        intermediate["system_tokens"] = system_tokens

        # 4b. Buffer setup
        ctx._buffer = list(raw_messages)

        # 4c. Token estimates
        buf_tokens = sum(session._estimate_tokens(session._content(m)) for m in ctx._buffer)
        intermediate["buf_tokens"] = buf_tokens
        budget = max(
            threshold - system_tokens - cfg["reserve_tokens"],
            cfg["max_tokens"] // 2,
        )
        intermediate["budget"] = budget
        intermediate["needs_compression"] = buf_tokens > budget

        # 4d. Tool truncation
        tool_before = len(ctx._buffer)
        compacted = session.compact_tool_result_for_all(
            ctx._buffer,
            keep_n=cfg["tool_keep_n"],
            max_chars=cfg["tool_max_chars"],
        )
        truncated_count = sum(
            1 for i, m in enumerate(ctx._buffer)
            if session._role(m) in ("tool", "function") and i < max(0, len(ctx._buffer) - cfg["tool_keep_n"])
        )
        intermediate["tool_compaction"] = {
            "keep_n": cfg["tool_keep_n"],
            "max_chars": cfg["tool_max_chars"],
            "before_total": tool_before,
            "after_total": len(compacted),
            "truncated_count": truncated_count,
        }

        # 4e. Context check
        to_keep, to_compact = session.check_context(compacted, budget)
        intermediate["context_check"] = {
            "to_keep_count": len(to_keep),
            "to_compact_count": len(to_compact),
            "to_keep_roles": [session._role(m) for m in to_keep],
            "to_compact_roles": [session._role(m) for m in to_compact],
        }

        # 4f. Run compression if needed
        compressed_summary = ""
        profile_facts: list[str] = []
        extraction_raw = ""
        if to_compact:
            compressed_summary = await compressor.compact(
                llm=llm, messages=to_compact,
                previous_summary=None,
            )

            # Profile extraction (inline, skip DB write)
            try:
                history = compressor._render(to_compact)
                response = await llm.ainvoke([
                    SystemMessage(content=EXTRACT_PROMPT),
                    HumanMessage(content=history),
                ])
                extraction_raw = response.content.strip()
                if extraction_raw and not extraction_raw.startswith("SKIP") and len(extraction_raw) >= 10:
                    try:
                        data = json.loads(extraction_raw)
                        profile_facts = data.get("facts", [])
                    except json.JSONDecodeError:
                        if "```json" in extraction_raw:
                            jtext = extraction_raw.split("```json", 1)[1].split("```", 1)[0]
                            data = json.loads(jtext)
                            profile_facts = data.get("facts", [])
            except Exception as e:
                extraction_raw = f"提取失败: {e}"
                logger.warning(f"Profile extraction failed in debug: {e}")

        intermediate["compressed_summary"] = compressed_summary
        intermediate["profile_facts"] = profile_facts
        intermediate["extraction_raw"] = extraction_raw

        # 4g. Render output
        recent = session.render(to_keep)
        profile_text = experience.render_profile(x_tenant_id, body.user_id)
        parts = [p for p in [profile_text, compressed_summary, recent] if p]
        full = "\n\n".join(parts)

        intermediate["rendering"] = {
            "recent_char_count": len(recent),
            "recent_message_count": len(to_keep),
            "profile_char_count": len(profile_text),
            "summary_char_count": len(compressed_summary),
            "full_char_count": len(full),
        }

        elapsed_ms = round((time.perf_counter() - t0) * 1000)
        intermediate["processing_time_ms"] = elapsed_ms

        return {
            "ok": True,
            "result": {
                "profile": profile_text,
                "summary": compressed_summary,
                "recent": recent,
                "full": full,
                "intermediate": intermediate,
            },
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.error(f"Debug run failed: {traceback.format_exc()}")
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "result": None,
        }
