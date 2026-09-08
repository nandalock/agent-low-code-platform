"""论文域 MCP — 独立进程 :9002

设计（参考 paper-qa 思路，零依赖自研）:
  - 搜索: arxiv API 直查（HTTP + Atom XML），无本地索引
  - 总结: 工具内自调 LLM（PAPER_LLM_* 环境变量），map-reduce 消化全文
  - 全文不进 AgentRuntime 循环，agent 只看到列表和成品 MD
"""

PORT = 9002
