import logging

from backend.db.connection import get_conn

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
    "agent_configs": """
        CREATE TABLE IF NOT EXISTS agent_configs (
            id          SERIAL PRIMARY KEY,
            agent_key   TEXT NOT NULL UNIQUE,
            config      JSONB NOT NULL DEFAULT '{}',
            created_at  TIMESTAMPTZ DEFAULT now(),
            updated_at  TIMESTAMPTZ DEFAULT now()
        )
    """,
}

INDEXES_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_faq_tenant ON faqs(tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_faq_question ON faqs USING gin(question gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS idx_faq_embedding ON faqs USING ivfflat (embedding vector_cosine_ops) WITH (lists = 10)",
    "CREATE INDEX IF NOT EXISTS idx_agent_configs_key ON agent_configs(agent_key)",
]

EXTENSIONS = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE EXTENSION IF NOT EXISTS vector",
]

MIGRATIONS = [
    "ALTER TABLE faqs ADD COLUMN IF NOT EXISTS embedding vector(1024)",
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

        conn.commit()
    logger.info("数据库初始化完成")
