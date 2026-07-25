# Repository Guidelines

## Project Structure & Module Organization

The backend lives at the repository root. `main.py` is the FastAPI entrypoint; all backend code lives under `app/`:

- `app/agent/` — LangGraph workflow (`graph.py`, `state.py`, `context.py`) and exactly 12 nodes in `app/agent/nodes/`, named per SRS 4.2: `extract_keywords`, `recall_column`, `recall_metric`, `recall_value`, `merge_retrieved_info`, `filter_table`, `filter_metric`, `add_extra_context`, `generate_sql`, `validate_sql`, `correct_sql`, `run_sql`.
- `app/api/routes/` — HTTP endpoints (`ask.py`, `health.py`, `config_api.py`, `metadata.py`, `admin.py`, `history.py`, `stats.py`).
- `app/clients/` — Adapters for MySQL, FAISS, ES/FTS5, local BGE embeddings, LLM, and query cache.
- `app/services/`, `app/repositories/`, `app/models/`, `app/entities/`, `app/prompt/`, `app/core/` (config, logger, lifespan, request_context, metrics).
- `conf/` — OmegaConf layered YAML (`default.yaml`, `local.yaml`, `prod.yaml`). Do not commit secrets here.
- `scripts/` — Setup scripts (`init_meta_mysql.py`, `init_dw_sample_data.py`, `build_knowledge_index.py`, `start_dev.ps1`).
- `frontend/` — Vite + React + TypeScript + Tailwind app with three routes (`/`, `/stats`, `/samples`).
- `docs/requirements/` — Source markdown specifications (do not edit).
- `data/` and `logs/` — Runtime artifacts (FAISS, FTS5, loguru output); gitignored.

## Build, Test, and Development Commands

Backend uses `uv`; frontend uses `pnpm`.

| Task | Command |
| --- | --- |
| Install backend deps | `uv sync` |
| Initialize MySQL schema (creates `meta`, `dw`, readonly user) | `uv run python scripts/init_meta_mysql.py` |
| Load DW sample data | `uv run python scripts/init_dw_sample_data.py` |
| Build FAISS + FTS5 indexes | `uv run python scripts/build_knowledge_index.py` |
| Run backend | `uv run uvicorn main:app --reload --port 8000` |
| Run workflow smoke test | `uv run python -m app.agent.graph` |
| Install frontend deps | `cd frontend && pnpm install` |
| Run frontend dev server | `cd frontend && pnpm dev` |
| Backend tests | `uv run pytest` |
| SQL accuracy regression | `uv run pytest tests/test_sql_accuracy.py` |

## Coding Style & Naming Conventions

- Python: 4-space indent, type hints, `snake_case` modules/functions/variables, `PascalCase` for Pydantic models and ORM classes. Format and lint with `uv run ruff format .` and `uv run ruff check .`.
- TypeScript: 2-space indent, `camelCase` for variables/functions, `PascalCase` for React components. Pages live under `frontend/src/pages/`, components under `frontend/src/components/`.
- LangGraph node files in `app/agent/nodes/` match the node key exactly (e.g. `generate_sql.py`).
- Do not commit `.env`, `data/`, `logs/`, or `frontend/node_modules/`.

## Testing Guidelines

- Tests live in `tests/` mirroring `app/`. Use `pytest` + `pytest-asyncio`.
- Name files `test_<module>.py`; name functions `test_<behavior>`.
- Required minimum coverage: SQL safety guard in `mysql_client.execute_readonly`, cache hit/miss in `cache_client`, every LangGraph node in isolation with a mocked `runtime`, and the SQL accuracy fixture set `tests/fixtures/nl2sql_cases.json` (≥50 cases, target ≥85% per SRS 10.1.2).
- Always include `request_id` in test logs so failures can be traced.

## Commit & Pull Request Guidelines

- Commit messages follow Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`).
- Branch names use `codex/<short-topic>` (e.g. `codex/stats-dashboard`).
- PRs must include: a one-paragraph summary, the linked issue (if any), and either screenshots or a terminal transcript for any user-visible change (UI, SSE output, or `/stats` dashboard).

## Architecture Overview

The query path is `user question → extract_keywords → {recall_column, recall_metric, recall_value} (parallel) → merge → {filter_table, filter_metric} (parallel) → add_extra_context → generate_sql → validate_sql → {correct_sql | run_sql} → END`. Progress is streamed to the frontend via `runtime.stream_writer` and SSE on `POST /api/ask`; event types follow SRS 7.3.1 (`progress`, `sql_generated`, `sql_corrected`, `result`, `error`, `done`). MySQL holds structured metadata, FAISS holds two vector collections (`column_info`, `metric_info`), ES/FTS5 holds field values under index `value_info`. Every LLM call is logged to `meta.llm_call_log` and surfaced on `/stats`.

## Security & Configuration

- Secrets (`LLM_API_KEY`, `MYSQL_*_PASSWORD`) come from environment variables only; never commit them. `conf/*.yaml` must stay free of secrets.
- `scripts/init_meta_mysql.py` creates a dedicated `readonly` account for the `dw` database; all SQL execution must go through that connection (`MYSQL_RO_USER`).
- Each HTTP request receives/returns an `X-Request-ID` header; preserve it through SSE events and logs.
- CORS defaults to `*` for local development only; set `CORS_ALLOW_ORIGINS` in `.env` (or `conf/prod.yaml`) before any non-local deployment.