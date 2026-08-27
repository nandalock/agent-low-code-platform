FROM python:3.12-slim

WORKDIR /app

# Node.js + npm (for npx MCP servers)
RUN apt-get update && apt-get install -y curl gnupg && \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# uv (for uvx MCP servers)
RUN pip install uv

RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir \
    fastapi uvicorn pyjwt psycopg2-binary pydantic python-multipart \
    websockets aiohttp requests sentence-transformers \
    langchain-core langchain-openai langgraph mcp redis numpy

RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('BAAI/bge-reranker-v2-m3')"

COPY . .

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
