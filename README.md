# Shopkeeper Agent (电商问数)

Local-only NL2SQL analytics agent for ecommerce data. Implements the 12-node LangGraph workflow defined in `docs/requirements/`.

## Stack
- Backend: Python 3.13 + FastAPI + LangGraph + SQLAlchemy
- Frontend: Vite + React + TypeScript + Tailwind CSS
- Storage: MySQL (meta + dw), local FAISS, SQLite FTS5 (Elasticsearch fallback)
- Embedding: local `sentence-transformers` + `bge-st` model

## Quick Start

```powershell
# 1. Install dependencies
uv sync

# 2. Configure
Copy-Item .env.example .env
# edit .env with your MySQL password and LLM credentials

# 3. Initialize databases
uv run python scripts/init_meta_mysql.py
uv run python scripts/init_dw_sample_data.py
uv run python scripts/build_knowledge_index.py

# 4. Run
uv run uvicorn main:app --reload --port 8000
# in another terminal
cd frontend
pnpm install
pnpm dev
```

## Routes
- `/` — query workbench (POST /api/ask with SSE)
- `/stats` — token / LLM call / cache hit dashboard
- `/samples` — sample questions covering SRS 3.2 scenarios

## Source Specifications
See `docs/requirements/` for the 5 chapter docs and the V1.0 enterprise SRS.