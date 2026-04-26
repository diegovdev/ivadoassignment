# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Museum visitor correlation with city population. Fetches the list of most-visited museums (>2 M visitors/year) from Wikipedia, stores museum and city-population data in SQLite, and exposes a FastAPI REST API + Jupyter notebook with a linear regression model.

## Commands

```bash
# Setup
uv sync                        # install all dependencies
uv sync --extra notebook       # include Jupyter extras

# Run (dev)
uv run uvicorn museums.api.main:app --reload

# Tests
uv run pytest                  # all tests
uv run pytest tests/test_wikipedia.py          # single file
uv run pytest -k test_parse_visitors           # single test

# Format + lint
uv run black src/ tests/
uv run ruff check src/ tests/

# Docker
docker compose up --build      # start api + jupyter services
docker compose down -v         # stop and remove volumes

# Data ingestion (after services are up)
curl -X POST http://localhost:8000/ingest
```

## Architecture

`src/museums/` is a standard src-layout Python package with three layers:

- **`data/`** — I/O only. `wikipedia.py` fetches the museum list via the MediaWiki REST API (`/api/rest_v1/page/html/…`) and parses it with pandas; it also queries the Wikidata SPARQL endpoint for city populations. `db.py` owns SQLAlchemy models (`City`, `Museum`) and the engine/session factory.
- **`ml/`** — pure computation, no I/O. `regression.py` takes a plain list of dicts and returns a `RegressionResult` dataclass (slope, intercept, R², predictions).
- **`api/`** — FastAPI app in `main.py`. Three routes: `POST /ingest` (orchestrates fetch → store), `GET /museums`, `GET /cities`, `GET /regression`.

Data flow: `POST /ingest` → `wikipedia.py` fetches → `db.py` upserts → `GET /regression` reads DB → `regression.py` computes → response.

Docker Compose runs two services: `api` (port 8000) and `jupyter` (port 8888). Jupyter calls the API over the internal network using `API_URL=http://api:8000`. The SQLite file lives on a named volume mounted at `/app/data/`.

## Agents

Three principal-level subagents are configured in `.claude/agents/`:

- `@code-writer` — implements features
- `@code-reviewer` — reviews for correctness and security (read-only)
- `@test-writer` — writes and fixes pytest tests
