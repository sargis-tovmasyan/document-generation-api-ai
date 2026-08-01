FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /workspace

COPY pyproject.toml uv.lock ./
COPY packages/contracts packages/contracts
COPY services/chat services/chat
RUN uv sync --frozen --no-dev --package doco-chat-service --no-editable

FROM python:3.12-slim

ENV PATH="/workspace/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

COPY --from=builder /workspace/.venv /workspace/.venv
COPY services/chat/alembic.ini /app/alembic.ini
COPY services/chat/migrations /app/migrations

RUN mkdir -p /app/chat-data

EXPOSE 8000

CMD ["uvicorn", "chat_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
