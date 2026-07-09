# 子智能体


员工服务    ─┼─→ 数据源：知识库文档      → faqagent（已有）
│
售中订单    ──→ 数据源：订单系统 API     → order_agent
│
售后工单    ──→ 数据源：工单/CRM 系统    → ticket_agent
│
兜底        ──→ 转人工                  → human_handoff
