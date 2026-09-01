"""FaqAgent：知识库匹配 + DeepSeek RAG — 基于 AgentRuntime"""
import json, logging, time
from backend.agents.base import AgentReply
from backend.agents.runtime import AgentRuntime
from backend.services.faq.service import match_faq, vector_search
from backend.core.rag import embed, rerank

logger = logging.getLogger(__name__)


class FaqAgent(AgentRuntime):
    def __init__(self):
        super().__init__(key="faqagent")

    async def reply(self, tenant_id: int, question: str, context: dict | None = None, session_id: str | None = None) -> AgentReply:
        t0 = time.perf_counter()
        config = self._cfg()
        direct_threshold = config["direct_threshold"]
        vector_top_n = config.get("vector_top_n", 10)
        rerank_top_k = config.get("rerank_top_k", 3)
        rerank_enabled = config.get("rerank_enabled", True)

        trace = {
            "pg_trgm_score": 0, "vector_top_n": vector_top_n,
            "rerank_top_k": rerank_top_k, "rerank_enabled": rerank_enabled,
            "candidates_count": 0, "rough_chunks": [], "fine_chunks": [],
            "llm_called": False, "llm_model": "", "llm_ms": 0, "total_ms": 0,
        }

        # Layer 1: pg_trgm
        result = match_faq(tenant_id, question, direct_threshold)
        trace["pg_trgm_score"] = result.get("_score", 0) if result else 0
        if result and result.get("_tier") == "direct":
            faq_item = self._to_faq_item(result)
            trace["total_ms"] = round((time.perf_counter() - t0) * 1000)
            return AgentReply(answer=faq_item["answer"], sources=[faq_item], tier="direct", trace=trace)

        # Layer 2: pgvector 语义搜索 + reranker
        sources = []
        query_vector = await embed(question)
        if query_vector:
            candidates = vector_search(tenant_id, query_vector, vector_top_n)
            trace["candidates_count"] = len(candidates)
            if candidates:
                trace["rough_chunks"] = [
                    {"question": c["question"], "answer": c["answer"][:100], "score": round(c.get("_score", 0), 4)}
                    for c in candidates
                ]
                if rerank_enabled:
                    sources = rerank(question, candidates, rerank_top_k)
                    trace["fine_chunks"] = [
                        {"question": s["question"], "answer": s["answer"][:100], "score": round(s.get("_score", 0), 4)}
                        for s in sources
                    ]
                else:
                    sources = candidates[:rerank_top_k]
                    trace["fine_chunks"] = trace["rough_chunks"][:rerank_top_k]

        if not sources:
            trace["total_ms"] = round((time.perf_counter() - t0) * 1000)
            return AgentReply(answer=config["fallback_reply"], tier="fallback", trace=trace)

        # Layer 3: LLM RAG — 用 AgentRuntime 的 plain LLM call
        knowledge = "\n\n".join(
            f"【来源 {i+1}】\n问题：{s['question']}\n答案：{s['answer']}"
            for i, s in enumerate(sources)
        )
        user_prompt = (
            f"知识库匹配到的内容：\n\n{knowledge}\n\n"
            f"用户问题：{question}\n\n"
            f"请根据以上知识库内容回答用户问题："
        )
        messages = [
            {"role": "system", "content": config["system_prompt"]},
        ]
        if context:
            context_str = json.dumps(context, ensure_ascii=False, indent=2)
            messages.append({
                "role": "system",
                "content": f"【上游节点输出，供你参考】\n{context_str}",
            })
        messages.append({"role": "user", "content": user_prompt})
        el_llm = time.perf_counter()
        answer, llm_ok = await self._call_llm_plain(messages, config)
        trace["llm_called"] = llm_ok
        trace["llm_model"] = config.get("model", "")
        trace["llm_ms"] = round((time.perf_counter() - el_llm) * 1000)
        trace["total_ms"] = round((time.perf_counter() - t0) * 1000)

        return AgentReply(
            answer=answer or sources[0]["answer"],
            sources=sources, tier="rag", trace=trace,
        )

    def _to_faq_item(self, row: dict) -> dict:
        return {
            "question": row.get("question", ""),
            "answer": row.get("answer", ""),
            "tags": row.get("tags", []),
            "score": row.get("_score", 0),
        }
