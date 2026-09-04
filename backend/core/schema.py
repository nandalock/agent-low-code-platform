import logging

from backend.core.connection import get_conn

logger = logging.getLogger(__name__)

TABLES_DDL = {
    "faqs": """
        CREATE TABLE IF NOT EXISTS faqs (
            id          SERIAL PRIMARY KEY,
            tenant_id   INT NOT NULL,
            question    TEXT NOT NULL,
            answer      TEXT NOT NULL,
            tags        TEXT[] DEFAULT '{}',
            is_active   BOOLEAN DEFAULT TRUE,
            created_at  TIMESTAMPTZ DEFAULT now(),
            updated_at  TIMESTAMPTZ DEFAULT now()
        )
    """,
    "agent_definitions": """
        CREATE TABLE IF NOT EXISTS agent_definitions (
            agent_key   TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            description TEXT DEFAULT '',
            config      JSONB NOT NULL DEFAULT '{}',
            status      TEXT DEFAULT 'active',
            created_at  TIMESTAMPTZ DEFAULT now(),
            updated_at  TIMESTAMPTZ DEFAULT now()
        )
    """,
    "agent_configs": """
        CREATE TABLE IF NOT EXISTS agent_configs (
            id          SERIAL PRIMARY KEY,
            agent_key   TEXT NOT NULL UNIQUE,
            config      JSONB NOT NULL DEFAULT '{}',
            created_at  TIMESTAMPTZ DEFAULT now(),
            updated_at  TIMESTAMPTZ DEFAULT now()
        )
    """,
    "orders": """
        CREATE TABLE IF NOT EXISTS orders (
            id              SERIAL PRIMARY KEY,
            tenant_id       INT NOT NULL,
            order_no        VARCHAR(50) NOT NULL,
            customer_name   VARCHAR(100),
            customer_phone  VARCHAR(30),
            customer_email  VARCHAR(100),
            status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                            CHECK(status IN ('pending','confirmed','processing','shipped','delivered','cancelled','returning','returned')),
            payment_status  VARCHAR(20) NOT NULL DEFAULT 'pending'
                            CHECK(payment_status IN ('pending','paid','refunding','refunded','partial')),
            payment_method  VARCHAR(30),
            total_amount    DECIMAL(12,2) DEFAULT 0,
            discount_amount DECIMAL(12,2) DEFAULT 0,
            shipping_fee    DECIMAL(10,2) DEFAULT 0,
            currency        VARCHAR(10) DEFAULT 'CNY',
            priority        VARCHAR(10) DEFAULT 'normal'
                            CHECK(priority IN ('low','normal','high','urgent')),
            customer_note   TEXT,
            internal_note   TEXT,
            created_at      TIMESTAMPTZ DEFAULT now(),
            updated_at      TIMESTAMPTZ DEFAULT now(),
            delivered_at    TIMESTAMPTZ,
            UNIQUE(tenant_id, order_no)
        )
    """,
    "order_items": """
        CREATE TABLE IF NOT EXISTS order_items (
            id            SERIAL PRIMARY KEY,
            order_id      INT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            product_name  VARCHAR(200) NOT NULL,
            product_sku   VARCHAR(50),
            quantity      INT NOT NULL DEFAULT 1,
            unit_price    DECIMAL(10,2) NOT NULL
        )
    """,
    "logistics": """
        CREATE TABLE IF NOT EXISTS logistics (
            id                  SERIAL PRIMARY KEY,
            order_id            INT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            tracking_no         VARCHAR(100) UNIQUE,
            carrier             VARCHAR(50) NOT NULL,
            shipping_method     VARCHAR(50),
            receiver_name       VARCHAR(50),
            receiver_phone      VARCHAR(30),
            province            VARCHAR(30),
            city                VARCHAR(30),
            district            VARCHAR(30),
            address_full        TEXT,
            weight_kg           DECIMAL(10,2),
            est_delivery_date   DATE,
            actual_delivery_date DATE,
            status              VARCHAR(20) NOT NULL DEFAULT 'pending'
                                CHECK(status IN ('pending','picked_up','in_transit','out_for_delivery','delivered','failed','returned')),
            signed_by           VARCHAR(50),
            created_at          TIMESTAMPTZ DEFAULT now(),
            updated_at          TIMESTAMPTZ DEFAULT now()
        )
    """,
    "logistics_tracking": """
        CREATE TABLE IF NOT EXISTS logistics_tracking (
            id            SERIAL PRIMARY KEY,
            logistics_id  INT NOT NULL REFERENCES logistics(id) ON DELETE CASCADE,
            location      VARCHAR(200),
            description   TEXT,
            status_code   VARCHAR(30),
            recorded_at   TIMESTAMPTZ DEFAULT now()
        )
    """,
    "order_status_log": """
        CREATE TABLE IF NOT EXISTS order_status_log (
            id          SERIAL PRIMARY KEY,
            order_id    INT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            old_status  VARCHAR(20),
            new_status  VARCHAR(20) NOT NULL,
            changed_by  VARCHAR(50),
            note        TEXT,
            created_at  TIMESTAMPTZ DEFAULT now()
        )
    """,
    "conversations": """
        CREATE TABLE IF NOT EXISTS conversations (
            id                      SERIAL PRIMARY KEY,
            tenant_id               INT NOT NULL,
            channel                 TEXT NOT NULL DEFAULT 'xianyu',
            channel_conversation_id TEXT,
            customer_name           TEXT,
            customer_id             TEXT,
            status                  TEXT NOT NULL DEFAULT 'active',
            agent_key               TEXT,
            assigned_to             TEXT,
            created_at              TIMESTAMPTZ DEFAULT now(),
            updated_at              TIMESTAMPTZ DEFAULT now()
        )
    """,
    "user_profiles": """
        CREATE TABLE IF NOT EXISTS user_profiles (
            tenant_id   INT NOT NULL,
            user_id     TEXT NOT NULL,
            facts       JSONB NOT NULL DEFAULT '[]',
            updated_at  TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (tenant_id, user_id)
        )
    """,
    "agent_mcp_bindings": """
        CREATE TABLE IF NOT EXISTS agent_mcp_bindings (
            agent_key  TEXT NOT NULL,
            tool_name  TEXT NOT NULL,
            PRIMARY KEY (agent_key, tool_name)
        )
    """,
    "mcp_servers": """
        CREATE TABLE IF NOT EXISTS mcp_servers (
            id          SERIAL PRIMARY KEY,
            tenant_id   INT NOT NULL DEFAULT 1,
            name        VARCHAR(200) NOT NULL,
            transport   VARCHAR(10) NOT NULL DEFAULT 'http',
            url         VARCHAR(500),
            command     VARCHAR(500),
            args        JSONB DEFAULT '[]',
            env         JSONB DEFAULT '{}',
            created_at  TIMESTAMPTZ DEFAULT now()
        )
    """,
    "messages": """
        CREATE TABLE IF NOT EXISTS messages (
            id              SERIAL PRIMARY KEY,
            tenant_id       INT NOT NULL,
            conversation_id INT NOT NULL REFERENCES conversations(id),
            role            TEXT NOT NULL,
            sender_name     TEXT,
            content         TEXT NOT NULL DEFAULT '',
            content_type    TEXT NOT NULL DEFAULT 'text',
            metadata        JSONB DEFAULT '{}',
            created_at      TIMESTAMPTZ DEFAULT now()
        )
    """,
    "router_l1_keywords": """
        CREATE TABLE IF NOT EXISTS router_l1_keywords (
            id           SERIAL PRIMARY KEY,
            router_key   VARCHAR(100) NOT NULL,
            keywords     TEXT[] NOT NULL DEFAULT '{}',
            target       VARCHAR(100) NOT NULL,
            created_at   TIMESTAMPTZ DEFAULT now()
        )
    """,
    "router_history": """
        CREATE TABLE IF NOT EXISTS router_history (
            id           SERIAL PRIMARY KEY,
            router_key   VARCHAR(100) NOT NULL,
            tenant_id    INTEGER NOT NULL DEFAULT 1,
            question     TEXT NOT NULL,
            target_agent VARCHAR(100) NOT NULL,
            route_level  VARCHAR(10) NOT NULL,
            confidence   FLOAT NOT NULL DEFAULT 1.0,
            embedding    vector(1024),
            created_at   TIMESTAMP DEFAULT now()
        )
    """,
    "workflows": """
        CREATE TABLE IF NOT EXISTS workflows (
            id          SERIAL PRIMARY KEY,
            tenant_id   INT NOT NULL DEFAULT 1,
            name        VARCHAR(200) NOT NULL DEFAULT '未命名工作流',
            status      VARCHAR(10) NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published')),
            nodes       JSONB NOT NULL DEFAULT '[]',
            edges       JSONB NOT NULL DEFAULT '[]',
            created_at  TIMESTAMPTZ DEFAULT now(),
            updated_at  TIMESTAMPTZ DEFAULT now(),
            UNIQUE(tenant_id, name)
        )
    """,
    # Agent Runtime Session(Event Log 持久化事实来源):
    #   session_headers — Session 元信息(与 events 分离,避免每行重复 header 字段)
    #   session_events  — 唯一真源 Event Log。session_id + seq 唯一确定顺序(UNIQUE 兜底并发);
    #                     data JSONB 保存事件 payload,不按事件类型拆业务表
    "session_headers": """
        CREATE TABLE IF NOT EXISTS session_headers (
            session_id       TEXT PRIMARY KEY,
            version          INT NOT NULL DEFAULT 1,
            created_at       DOUBLE PRECISION NOT NULL,
            cwd              TEXT,
            parent_session   TEXT,
            seed_length      INT,
            delegation_depth INT,
            updated_at       TIMESTAMPTZ DEFAULT now()
        )
    """,
    "session_events": """
        CREATE TABLE IF NOT EXISTS session_events (
            id               BIGSERIAL PRIMARY KEY,
            session_id       TEXT NOT NULL REFERENCES session_headers(session_id) ON DELETE CASCADE,
            seq              INT NOT NULL,
            event_type       TEXT NOT NULL,
            event_time       DOUBLE PRECISION NOT NULL,
            data             JSONB NOT NULL DEFAULT '{}',
            source_event_seqs JSONB,
            created_at       TIMESTAMPTZ DEFAULT now(),
            UNIQUE(session_id, seq)
        )
    """,
}

INDEXES_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_faq_tenant ON faqs(tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_faq_question ON faqs USING gin(question gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS idx_faq_embedding ON faqs USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10)",
    "CREATE INDEX IF NOT EXISTS idx_agent_configs_key ON agent_configs(agent_key)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_tenant ON conversations(tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_status ON conversations(tenant_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_conversations_channel_cid ON conversations(channel, channel_conversation_id)",
    "CREATE INDEX IF NOT EXISTS idx_messages_tenant ON messages(tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_messages_conv_time ON messages(conversation_id, created_at)",
    # orders
    "CREATE INDEX IF NOT EXISTS idx_orders_tenant ON orders(tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_orders_tenant_order_no ON orders(tenant_id, order_no)",
    "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(tenant_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_orders_customer_phone ON orders(tenant_id, customer_phone)",
    "CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(tenant_id, created_at)",
    # order_items
    "CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id)",
    # logistics
    "CREATE INDEX IF NOT EXISTS idx_logistics_order ON logistics(order_id)",
    "CREATE INDEX IF NOT EXISTS idx_logistics_tracking_no ON logistics(tracking_no)",
    "CREATE INDEX IF NOT EXISTS idx_logistics_status ON logistics(status)",
    # logistics_tracking
    "CREATE INDEX IF NOT EXISTS idx_logistics_tracking_logistics ON logistics_tracking(logistics_id)",
    # order_status_log
    "CREATE INDEX IF NOT EXISTS idx_order_status_log_order ON order_status_log(order_id)",
    # mcp_servers
    "CREATE INDEX IF NOT EXISTS idx_mcp_servers_tenant ON mcp_servers(tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_router_history_embedding ON router_history USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10)",
    "CREATE INDEX IF NOT EXISTS idx_l1_router_key ON router_l1_keywords(router_key)",
    "CREATE INDEX IF NOT EXISTS idx_l1_keywords_gin ON router_l1_keywords USING GIN(keywords)",
]

EXTENSIONS = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE EXTENSION IF NOT EXISTS vector",
]

MIGRATIONS = [
    "ALTER TABLE faqs ADD COLUMN IF NOT EXISTS embedding vector(1024)",
    # chat 域 conversation ↔ Agent Runtime Session 的持久映射（会话级记忆恢复用）：
    # 请求带 conversation_id 而无 session_id 时，API 层按此列冷恢复该场对话的 Session
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS session_id TEXT",
    "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS env JSONB DEFAULT '{}'",
    "ALTER TABLE mcp_servers ALTER COLUMN args TYPE JSONB USING COALESCE(args::jsonb, '[]'::jsonb)",
    "ALTER TABLE mcp_servers ALTER COLUMN args SET DEFAULT '[]'::jsonb",
    # 只在旧表还有 desc 列时才重命名
    """DO $$
    BEGIN
      IF EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='agent_definitions' AND column_name='desc') THEN
        ALTER TABLE agent_definitions RENAME COLUMN "desc" TO description;
      END IF;
    END $$;""",
    "ALTER TABLE agent_definitions ADD COLUMN IF NOT EXISTS agent_type VARCHAR(20) NOT NULL DEFAULT 'agent'",
    "ALTER TABLE agent_definitions ADD COLUMN IF NOT EXISTS cache_policy JSONB DEFAULT NULL",
    # 迁移 L1 关键字: JSONB config → router_l1_keywords 独立表（兼容新旧两种格式）
    """DO $$
    DECLARE
        src RECORD;
        rule JSONB;
        k TEXT;
        v JSONB;
    BEGIN
        -- agent_configs
        FOR src IN
            SELECT ac.agent_key, ac.config
            FROM agent_configs ac
            WHERE ac.config ? 'l1_keywords'
              AND NOT EXISTS (
                SELECT 1 FROM router_l1_keywords rlk
                WHERE rlk.router_key = ac.agent_key
              )
        LOOP
            IF jsonb_typeof(src.config->'l1_keywords') = 'array' THEN
                -- 新格式: [{"keywords":[], "target":"..."}]
                FOR rule IN SELECT * FROM jsonb_array_elements(src.config->'l1_keywords')
                LOOP
                    INSERT INTO router_l1_keywords (router_key, keywords, target)
                    VALUES (
                        src.agent_key,
                        ARRAY(SELECT * FROM jsonb_array_elements_text(rule->'keywords')),
                        rule->>'target'
                    );
                END LOOP;
            ELSIF jsonb_typeof(src.config->'l1_keywords') = 'object' THEN
                -- 旧格式: {"target": ["kw1","kw2"]}
                FOR k, v IN SELECT * FROM jsonb_each(src.config->'l1_keywords')
                LOOP
                    INSERT INTO router_l1_keywords (router_key, keywords, target)
                    VALUES (
                        src.agent_key,
                        ARRAY(SELECT * FROM jsonb_array_elements_text(v)),
                        k
                    );
                END LOOP;
            END IF;
        END LOOP;

        -- agent_definitions
        FOR src IN
            SELECT ad.agent_key, ad.config
            FROM agent_definitions ad
            WHERE ad.config ? 'l1_keywords'
              AND NOT EXISTS (
                SELECT 1 FROM router_l1_keywords rlk
                WHERE rlk.router_key = ad.agent_key
              )
        LOOP
            IF jsonb_typeof(src.config->'l1_keywords') = 'array' THEN
                FOR rule IN SELECT * FROM jsonb_array_elements(src.config->'l1_keywords')
                LOOP
                    INSERT INTO router_l1_keywords (router_key, keywords, target)
                    VALUES (
                        src.agent_key,
                        ARRAY(SELECT * FROM jsonb_array_elements_text(rule->'keywords')),
                        rule->>'target'
                    );
                END LOOP;
            ELSIF jsonb_typeof(src.config->'l1_keywords') = 'object' THEN
                FOR k, v IN SELECT * FROM jsonb_each(src.config->'l1_keywords')
                LOOP
                    INSERT INTO router_l1_keywords (router_key, keywords, target)
                    VALUES (
                        src.agent_key,
                        ARRAY(SELECT * FROM jsonb_array_elements_text(v)),
                        k
                    );
                END LOOP;
            END IF;
        END LOOP;
    END $$;""",
    # 修复重复名称 + 补 UNIQUE 约束
    """DO $$
    DECLARE
      dup RECORD;
    BEGIN
      FOR dup IN
        SELECT id FROM (
          SELECT id, ROW_NUMBER() OVER (PARTITION BY tenant_id, name ORDER BY id) AS rn
          FROM workflows
        ) sub WHERE rn > 1
      LOOP
        UPDATE workflows SET name = name || ' (' || dup.id || ')' WHERE id = dup.id;
      END LOOP;
      IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'workflows_tenant_id_name_key') THEN
        ALTER TABLE workflows ADD CONSTRAINT workflows_tenant_id_name_key UNIQUE (tenant_id, name);
      END IF;
    END $$;""",
]


SEED_SERVERS = [
    "INSERT INTO mcp_servers (tenant_id, name, transport, url) SELECT 1, '本地 MCP', 'http', 'http://localhost:9001/mcp' WHERE NOT EXISTS (SELECT 1 FROM mcp_servers WHERE url = 'http://localhost:9001/mcp')",
    "INSERT INTO mcp_servers (tenant_id, name, transport, url) SELECT 1, '论文 MCP', 'http', 'http://localhost:9002/mcp' WHERE NOT EXISTS (SELECT 1 FROM mcp_servers WHERE url = 'http://localhost:9002/mcp')",
]

SEED_CACHE_POLICIES = [
    """UPDATE agent_definitions SET cache_policy = '{
      "base_score": 0.50,
      "cacheable_intents": [],
      "block_entities": []
    }'::jsonb WHERE cache_policy IS NULL""",
]

SEED_AGENTS = [
    """INSERT INTO agent_definitions (agent_key, name, description, config)
       VALUES ('faqagent', 'FaqAgent', '基于 pg_trgm 直接匹配 + pgvector 语义检索 + reranker 精排 + LLM RAG 润色', '{
         "direct_threshold": 0.85,
         "vector_top_n": 10,
         "rerank_top_k": 3,
         "rerank_enabled": true,
         "system_prompt": "你是一个专业的客服助手。请根据下面的知识库内容回答用户问题。如果知识库内容不足以回答，请诚实告知并建议联系人工客服。回答要友好、简洁、准确。",
         "fallback_reply": "抱歉，我暂时无法回答这个问题，请转接人工客服获取帮助。",
         "api_key": "",
         "base_url": "",
         "model": ""
       }'::jsonb)
       ON CONFLICT (agent_key) DO NOTHING""",
    """INSERT INTO agent_definitions (agent_key, name, description, config)
       VALUES ('order_agent', 'OrderAgent', '订单查询、物流跟踪、催单、修改备注等售中流程处理', '{
         "system_prompt": "你是一个专业的订单客服助手。你的职责是帮助用户查询订单状态、跟踪物流信息、处理催单请求、修改订单备注等。请用友好、耐心的语气回答。如果无法处理，请建议用户转接人工客服。",
         "fallback_reply": "抱歉，我暂时无法处理您的订单请求，正在为您转接人工客服。",
         "max_steps": 5,
         "api_key": "",
         "base_url": "",
         "model": ""
       }'::jsonb)
       ON CONFLICT (agent_key) DO NOTHING""",
    """INSERT INTO agent_definitions (agent_key, name, description, config)
       VALUES ('ticket_agent', 'TicketAgent', '投诉处理、维修工单、售后查询', '{
         "system_prompt": "你是一个专业的售后客服助手。你的职责是处理客户投诉、创建维修工单、查询工单进度、协调换货退货等。请保持耐心和同理心。如果无法解决，请建议用户转接人工客服。",
         "fallback_reply": "抱歉，我暂时无法处理您的售后请求，正在为您转接人工客服。",
         "max_steps": 5,
         "api_key": "",
         "base_url": "",
         "model": ""
       }'::jsonb)
       ON CONFLICT (agent_key) DO NOTHING""",
    """INSERT INTO agent_definitions (agent_key, name, description, config)
       VALUES ('supervisor', 'Supervisor', '中央编排 Agent，三级意图路由 + LangGraph 多 Agent 协作', '{
         "system_prompt": "你是一个智能客服系统的 Supervisor。分析用户意图，路由到对应子 Agent，汇总结果。",
         "routable_agents": [
           {"key": "faqagent", "name": "FAQ 知识检索", "desc": "售前咨询 | 员工服务 — 知识库匹配 + LLM RAG"},
           {"key": "order_agent", "name": "订单查询", "desc": "售中订单 — 催单、改备注、查物流"},
           {"key": "ticket_agent", "name": "工单处理", "desc": "售后工单 — 退货、投诉、维修"},
           {"key": "human_handoff", "name": "转人工", "desc": "兜底 — 无法处理时转人工客服"}
         ],
         "fallback_reply": "抱歉，我暂时无法处理您的请求，正在为您转接人工客服。",
         "l1_keywords": {
           "faqagent": ["怎么", "如何", "是什么", "能不能", "几点", "在哪里", "有没有", "退款", "退货", "七天无理由", "怎么退", "退到哪里", "上班", "加班", "请假", "工资", "报销", "制度", "政策", "多少钱", "起投", "最低", "收益", "风险", "利率", "年化", "开户", "多久", "你好", "在吗", "hi", "hello"],
           "order_agent": ["快递", "物流", "发货", "运单号", "到哪了", "还没到", "什么时候到", "查一下单号", "催单", "改地址", "修改备注", "订单状态", "取消订单"],
           "ticket_agent": ["投诉", "工单", "催一下", "处理进度", "还没处理", "帮我催", "问题没解决", "维修", "换货", "质量", "坏了"],
           "human_handoff": ["转人工", "人工客服", "找真人", "找客服", "机器人没用", "听不懂", "叫人来", "投诉客服"]
         },
         "api_key": "",
         "base_url": "",
         "model": ""
       }'::jsonb)
       ON CONFLICT (agent_key) DO NOTHING""",
    """INSERT INTO agent_definitions (agent_key, name, description, config)
       VALUES ('human_handoff', '人工转接', '无法自动处理时转接人工客服，支持排队通知和安抚话术', '{
         "handoff_message": "已为您转接人工客服，请稍候...",
         "queue_message": "当前排队人数较多，预计等待 {wait_minutes} 分钟，人工客服将尽快为您服务。",
         "api_key": "",
         "base_url": "",
         "model": ""
       }'::jsonb)
       ON CONFLICT (agent_key) DO NOTHING""",
    """INSERT INTO agent_definitions (agent_key, name, description, config)
       VALUES ('paper_agent', 'PaperAgent', '学术论文研究助手 — arxiv 搜索 + PDF 全文总结成 markdown', '{
         "system_prompt": "你是学术论文研究助手。帮助用户查找 arXiv 论文并总结成 markdown。流程：先用 search_papers 搜索相关论文 → 对用户感兴趣的论文调 summarize_paper 生成结构化总结（标题/摘要/核心贡献/方法/关键结论/局限）→ 最后把总结完整输出给用户。一次可总结 1-3 篇，如果用户要多篇先问清楚优先级。",
         "fallback_reply": "抱歉，论文服务暂不可用，请稍后再试。",
         "max_steps": 8,
         "max_tokens": 3000,
         "temperature": 0.3,
         "api_key": "",
         "base_url": "",
         "model": ""
       }'::jsonb)
       ON CONFLICT (agent_key) DO NOTHING""",
]


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            for ext_sql in EXTENSIONS:
                cur.execute(ext_sql)

            for name, ddl in TABLES_DDL.items():
                cur.execute(ddl)
                logger.info(f"表 {name} 已就绪")

            for mig_sql in MIGRATIONS:
                cur.execute(mig_sql)
                logger.info(f"迁移: {mig_sql[:60]}...")

            for idx_sql in INDEXES_DDL:
                cur.execute(idx_sql)

            for seed_sql in SEED_SERVERS:
                cur.execute(seed_sql)

            for seed_sql in SEED_AGENTS:
                cur.execute(seed_sql)

            for seed_sql in SEED_CACHE_POLICIES:
                cur.execute(seed_sql)

        conn.commit()
    logger.info("数据库初始化完成")
