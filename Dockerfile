# ── Build stage ───────────────────────────────────────────────────────────────
FROM python:3.13-slim AS builder

WORKDIR /build

RUN pip install --no-cache-dir uv==0.4.29

COPY pyproject.toml .
RUN uv pip install --system --no-cache .

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

WORKDIR /app

# System deps for asyncpg / aio-pika
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source
COPY src/ /app/src/
COPY migrations/ /app/migrations/
COPY alembic.ini /app/

ENV PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LOG_FORMAT=json

# Default command is the API server; override in docker-compose for worker
CMD ["uvicorn", "iara.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

EXPOSE 8000
