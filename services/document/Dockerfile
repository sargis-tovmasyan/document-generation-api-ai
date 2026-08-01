FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0
WORKDIR /workspace

COPY pyproject.toml uv.lock ./
COPY packages/contracts packages/contracts
COPY services/document services/document
RUN uv sync --frozen --no-dev --package doco-document-service --no-editable

FROM python:3.12-slim

ENV PATH="/workspace/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        libffi8 \
        libjpeg62-turbo \
        libopenjp2-7 \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /workspace/.venv /workspace/.venv

RUN mkdir -p /app/document-data /app/generated/invoices

EXPOSE 8001 50051

CMD ["uvicorn", "document_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
