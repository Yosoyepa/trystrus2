# Multi-stage Python 3.13 + uv build for Aval backend microservices
FROM python:3.13-slim AS builder

# Install uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install project dependencies with layer caching
COPY pyproject.toml uv.lock ./
RUN uv venv /app/.venv && \
    uv sync --frozen --no-install-project --no-dev

# Copy source and fixtures
COPY src/ /app/src/
COPY aval/ /app/aval/
COPY alembic.ini /app/alembic.ini
COPY alembic/ /app/alembic/

# Production runner image
FROM python:3.13-slim AS runner

RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libmagic1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy virtualenv and application assets from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder --chown=appuser:appuser /app/src /app/src
COPY --from=builder --chown=appuser:appuser /app/aval /app/aval
COPY --from=builder --chown=appuser:appuser /app/alembic.ini /app/alembic.ini
COPY --from=builder --chown=appuser:appuser /app/alembic /app/alembic

# Create writable runtime directories for keys, tokens, and logs
RUN mkdir -p /app/secrets /app/var && chown -R appuser:appuser /app/secrets /app/var

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src:/app"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=8001
ENV APP_MODULE=src.api.main:app

EXPOSE 8001 8002 8003 8010 8080

USER appuser

HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

CMD ["sh", "-c", "uvicorn ${APP_MODULE} --host 0.0.0.0 --port ${PORT}"]
