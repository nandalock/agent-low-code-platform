"""runtime 层自检脚本 — 不连 DB / 网络，mock 掉 LLM HTTP。

按路径直接跑，不走 pytest（仓库无 pytest 配置）：

    docker compose exec backend python backend/agents/runtime/tests/test_agent_loop.py
"""
