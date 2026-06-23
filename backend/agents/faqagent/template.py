"""FaqAgent 配置模板"""

TEMPLATE = {
    "name": "FaqAgent",
    "desc": "基于 pg_trgm 直接匹配 + pgvector 语义检索 + reranker 精排 + LLM RAG 润色",
    "direct_threshold": 0.85,
    "vector_top_n": 10,
    "rerank_top_k": 3,
    "rerank_enabled": True,
    "system_prompt": (
        "你是一个专业的客服助手。请根据下面的知识库内容回答用户问题。"
        "如果知识库内容不足以回答，请诚实告知并建议联系人工客服。"
        "回答要友好、简洁、准确。"
    ),
    "fallback_reply": "抱歉，我暂时无法回答这个问题，请转接人工客服获取帮助。",
    "api_key": "",
    "base_url": "",
    "model": "",
}
