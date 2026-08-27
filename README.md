<div align="center">

# 🤖 Agent Low-Code Platform

**A low-code multi-agent platform for building intelligent customer-service bots.**

Design multi-agent workflows on a visual canvas, plug in RAG knowledge bases, semantic caching,
long-term memory and MCP tools — no code required.

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.1xx-009688?logo=fastapi&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-14-black?logo=next.js&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16+_pgvector-4169E1?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7-red?logo=redis&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)

</div>

---

## ✨ Features

- 🎨 **Visual workflow builder** — drag & drop agents onto a React Flow canvas, connect them,
  save and run the whole pipeline with one click.
- 🤖 **Multi-agent runtime** — FAQ agent (RAG), intent **Router**, **Supervisor** and
  **Human handoff** agents ship out of the box; define new agents directly from the database.
- 📚 **RAG knowledge base** — FAQ management with Ollama embeddings + `pgvector` + reranker.
- ⚡ **Semantic cache** — Redis-backed caching middleware with a policy engine (TTL, confidence
  scorers, cacheable intents) that turns repeat questions into instant answers.
- 🧠 **Long-term memory** — per-user session context, summarization, compression and
  experience memory that makes agents smarter over time.
- 🔌 **MCP support** — built-in Model Context Protocol server + registry; import and manage
  external MCP servers from the dashboard.
- 💬 **Real-time chat** — WebSocket streaming conversations in the dashboard.
- 🏢 **Multi-tenant** — `X-Tenant-ID` based isolation, ready for SaaS.

## 🧱 Tech Stack

| Layer      | Technology                                                              |
| ---------- | ----------------------------------------------------------------------- |
| Frontend   | Next.js 14 · React 18 · TypeScript · React Flow · TanStack Query        |
| Backend    | FastAPI · Python 3.11 · uvicorn                                         |
| Database   | PostgreSQL 16 + `pgvector` (embeddings) · SQLAlchemy                    |
| Cache      | Redis 7 (redis-stack)                                                   |
| LLM        | Ollama (local models, `:11434`)                                         |
| Protocol   | Model Context Protocol (MCP) · WebSocket                                |
| Infra      | Docker Compose                                                          |

## 🚀 Quick Start

> **Prerequisite:** a running [Ollama](https://ollama.com/) instance with your preferred
> chat & embedding models pulled.

```bash
# 1. Start the whole stack (DB + Redis + Backend + Frontend)
docker compose up --build

# 2. Open the dashboard
open http://localhost:3003
```

| Service    | URL                    |
| ---------- | ---------------------- |
| Frontend   | http://localhost:3003  |
| Backend API| http://localhost:8000  |
| MCP Server | http://localhost:9001  |
| RedisInsight | http://localhost:5540 |

## 🏗️ Architecture

```mermaid
flowchart LR
    U[👤 User] -->|HTTP / WebSocket| F[Next.js Frontend :3003]
    F -->|REST| A[FastAPI Backend :8000]
    A -->|Chat / Workflow / Agents| E[Agent Runtime & Workflow Engine]
    E --> M[Memory System]
    E --> C[Semantic Cache]
    E --> K[RAG · FAQ + Embeddings]
    E --> T[MCP Registry & Server :9001]
    A --> P[(PostgreSQL + pgvector)]
    C --> R[(Redis)]
    K --> P
    M --> P
    E --> O[Ollama :11434]
```

## 📁 Project Structure

```
agent-low-code-platform/
├── backend/
│   ├── agents/          # Agent implementations (faq, supervisor, router, human_handoff)
│   ├── api/             # REST & WebSocket endpoints
│   ├── auth/            # JWT authentication
│   ├── cache/           # Redis client + semantic cache + caching policy engine
│   ├── chat/            # Conversation models & service
│   ├── db/              # Database connection, init & config service
│   ├── engine/          # Workflow engine (builder, handlers, state, middlewares)
│   ├── faq/             # Knowledge-base / FAQ service
│   ├── memory/          # Session context, summarizer, compressor, experience
│   ├── mcp_service/     # MCP client, registry & built-in server
│   ├── rag/             # Ollama embeddings & reranker
│   └── xianyu/          # (optional) 3rd-party channel integration
├── frontend/
│   └── app/
│       ├── dashboard/   # agents · chat · knowledge · mcp · memory · workflow
│       └── agents/      # per-agent configuration pages
├── docker-compose.yml   # db · redis · backend · frontend
└── main.py
```

## 📡 API Overview

| Method | Endpoint                          | Description                    |
| ------ | --------------------------------- | ------------------------------ |
| POST   | `/api/auth/login`                 | JWT login                      |
| GET    | `/api/agents`                     | List agents                    |
| POST   | `/api/agents`                     | Create agent (DB-defined)      |
| POST   | `/api/agents/{key}/chat`          | Chat with an agent             |
| POST   | `/api/agents/{key}/route-test`    | Test router intents            |
| GET    | `/api/workflows`                  | List workflows                 |
| POST   | `/api/workflows`                  | Create workflow                |
| POST   | `/api/workflows/{id}/run`         | Run a workflow                 |
| GET    | `/api/faqs` · POST `/api/faqs`    | Knowledge-base CRUD            |
| GET    | `/api/conversations`              | Chat conversations & messages  |
| GET    | `/api/mcp/servers` · POST `…/import` | Manage MCP servers          |
| GET    | `/api/memory/users`               | Long-term memory profiles      |
| WS     | `/api/ws/chat`                    | Real-time chat                 |

Full interactive docs at `http://localhost:8000/docs` (Swagger UI).

## ⚙️ Configuration

Environment variables (see `docker-compose.yml`):

| Variable             | Default                          | Description                  |
| -------------------- | -------------------------------- | ---------------------------- |
| `DATABASE_URL`       | `postgresql://saas:saas@db:5432/saas` | PostgreSQL connection   |
| `JWT_SECRET`         | `dev-secret`                     | Token signing secret         |
| `OLLAMA_HOST`        | `http://host.docker.internal:11434` | Local LLM endpoint       |
| `NEXT_PUBLIC_API_URL`| `http://localhost:8000`          | Frontend → backend base URL  |

## 🗺️ Roadmap

- [x] Visual workflow builder & runner
- [x] Multi-agent runtime (FAQ / Router / Supervisor / Human handoff)
- [x] RAG knowledge base with semantic cache
- [x] Long-term memory system
- [ ] Versioning & rollback for workflow drafts
- [ ] Streaming token-level chat UX
- [ ] Plugin marketplace for MCP servers
- [ ] Analytics dashboard (turns, cache hit-rate, handoff rate)

## 🤝 Contributing

Contributions are welcome! Please open an issue to discuss changes before opening a PR,
or pick up anything in the roadmap.

1. Fork the repo & create your branch (`git checkout -b feat/amazing`)
2. Commit your changes (`git commit -m 'feat: add amazing thing'`)
3. Push and open a Pull Request

## 📄 License

[MIT](LICENSE) © 2026

---

<div align="center">
⭐ If this project helps you, consider giving it a star!
</div>
