FROM python:3.12-slim AS base
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
RUN uv sync --no-dev --frozen

FROM base AS api
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "museums.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
