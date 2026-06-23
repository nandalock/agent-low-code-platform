"""FaqAgent：知识库匹配 + DeepSeek RAG"""
import logging

import aiohttp

from backend.agents.base import BaseAgent, AgentReply
from backend.agents.faqagent.template import TEMPLATE
from backend.db.config_service import get_agent_config
from backend.faq.service import match_faq, vector_search
from backend.rag import embed, rerank

logger = logging.getLogger(__name__)


class FaqAgent(BaseAgent):
    key = "faqagent"
    name = TEMPLATE["name"]
    desc = TEMPLATE["desc"]
    template = TEMPLATE

    def _get_config(self) -> dict:
        db_config = get_agent_config(self.key) or {}
        return {**self.template, **db_config}

    async def reply(self, tenant_id: int, question: str) -> AgentReply:
        config = self._get_config()
        direct_threshold = config["direct_threshold"]
        vector_top_n = config.get("vector_top_n", 10)
        rerank_top_k = config.get("rerank_top_k", 3)
        rerank_enabled = config.get("rerank_enabled", True)

        trace = {
            "pg_trgm_score": 0,
            "vector_top_n": vector_top_n,
            "rerank_top_k": rerank_top_k,
            "rerank_enabled": rerank_enabled,
            "candidates_count": 0,
            "chunks": [],
        }

        # Layer 1: pg_trgm
        result = match_faq(tenant_id, question, direct_threshold)
        trace["pg_trgm_score"] = result.get("_score", 0) if result else 0

        if result and result.get("_tier") == "direct":
            faq_item = self._to_faq_item(result)
            return AgentReply(
                answer=faq_item["answer"], sources=[faq_item], tier="direct",
                trace=trace,
            )

        # Layer 2: pgvector 语义搜索 + reranker
        sources = []
        query_vector = await embed(question)
        if query_vector:
            candidates = vector_search(tenant_id, query_vector, vector_top_n)
            trace["candidates_count"] = len(candidates)
            if candidates:
                trace["chunks"] = [
                    {"question": c["question"], "answer": c["answer"][:100], "score": round(c.get("_score", 0), 4)}
                    for c in candidates
                ]
                if rerank_enabled:
                    sources = rerank(question, candidates, rerank_top_k)
                else:
                    sources = candidates[:rerank_top_k]

        if not sources:
            return AgentReply(answer=config["fallback_reply"], tier="fallback", trace=trace)

        llm_answer = await self._call_llm(question, sources, config)
        if rerank_enabled:
            trace["chunks"] = [
                {"question": c["question"], "answer": c["answer"][:100], "score": round(c.get("_score", 0), 4)}
                for c in sources
            ]
        return AgentReply(answer=llm_answer, sources=sources, tier="rag", trace=trace)

    def _to_faq_item(self, row: dict) -> dict:
        return {
            "question": row.get("question", ""),
            "answer": row.get("answer", ""),
            "tags": row.get("tags", []),
            "score": row.get("_score", 0),
        }

    async def _call_llm(self, question: str, sources: list[dict], config: dict) -> str:
        api_key = config.get("api_key", "")
        base_url = config.get("base_url", "")
        model = config.get("model", "")

        if not api_key:
            return sources[0]["answer"] if sources else config["fallback_reply"]

        knowledge = "\n\n".join(
            f"【来源 {i+1}】\n问题：{s['question']}\n答案：{s['answer']}"
            for i, s in enumerate(sources)
        )
        user_prompt = (
            f"知识库匹配到的内容：\n\n{knowledge}\n\n"
            f"用户问题：{question}\n\n"
            f"请根据以上知识库内容回答用户问题："
        )

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{base_url}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": config["system_prompt"]},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.3,
                        "max_tokens": 512,
                    },
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    result = await resp.json()
                    answer = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if answer:
                        return answer
        except Exception as e:
            logger.warning(f"FaqAgent LLM 调用失败: {e}")

        return sources[0]["answer"]
