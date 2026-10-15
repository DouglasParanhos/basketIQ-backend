# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./

# --- API: no OCR / LLM dependencies --------------------------------------------------
FROM base AS api
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
USER app
EXPOSE 8000
CMD ["uvicorn", "basketiq.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

# --- Worker: PaddleOCR (models baked into the image) and optional Agno/Ollama ----------
FROM base AS worker
ENV PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra ocr --extra llm --no-install-project
COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --extra ocr --extra llm
USER app
RUN python -m basketiq.pipeline.ocr.paddle --warmup
CMD ["dramatiq", "basketiq.workers.actors", "--processes", "1", "--threads", "2"]

# --- Test: everything, including dev tools ----------------------------------------------
FROM worker AS test
USER root
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --extra ocr --extra llm
COPY tests ./tests
COPY scripts ./scripts
USER app
CMD ["pytest"]
