# 项目设计文档

## FAQ

### 数据表

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE faqs (
    id          SERIAL PRIMARY KEY,
    tenant_id   INT NOT NULL,
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    tags        TEXT[] DEFAULT '{}',
    is_active   BOOLEAN DEFAULT TRUE,BGE-M3
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_faq_tenant ON faqs(tenant_id);
CREATE INDEX idx_faq_question ON faqs USING gin(question gin_trgm_ops);
```

### 匹配策略

混合检索：关键词 + 语义向量。

| 层 | 方式 | 技术 |
|---|---|---|
| 1. 精确 | `question = input` | SQL 直查 |
| 2. 模糊 | `similarity(question, input) > 0.6` | pg_trgm |
| 3. 语义 | 向量相似度 ≥ 0.85 | pgvector + BGE-M3 |

三层按序执行，命中即返回固定 `answer`，不调 LLM。
    
### Embedding 模型

BGE-M3，通过 Infinity 容器独立部署，不在项目代码内存储模型文件。

### FAQ 管理 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/faqs` | 新增 |
| PUT | `/api/faqs/{id}` | 编辑 |
| DELETE | `/api/faqs/{id}` | 删除 |
| GET | `/api/faqs` | 列表（标签筛选、分页） |
| POST | `/api/faqs/match` | 匹配查询（输入用户问题，返回命中结果） |

### Docker 部署

开发阶段：PostgreSQL + Infinity 两个容器。

```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: saas
      POSTGRES_USER: saas
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./migrations:/docker-entrypoint-initdb.d
    ports:
      - "5432:5432"

  embedding:
    image: michaelfeil/infinity:latest
    volumes:
      - model_cache:/app/data
    command: --model-name BAAI/bge-m3 --port 8005
    ports:
      - "8005:8005"

volumes:
  pgdata:
  model_cache:
```
