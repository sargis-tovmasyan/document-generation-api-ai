# Document Service

Invoice extraction, validation, SQLite persistence, packaged templates, PDF generation, HTTP routes, and the `documents.v1` gRPC server. It owns `data/documents.db` and `generated/`, serving HTTP on 8001 and gRPC on 50051.

From the workspace root:

```bash
uv sync --frozen --package doco-document-service
DOCUMENT_DATABASE_PATH=services/document/data/documents.db uv run --package doco-document-service alembic -c services/document/alembic.ini upgrade head
uv run --frozen --package doco-document-service pytest services/document/tests -q
uv run --frozen --package doco-document-service uvicorn document_service.main:app --port 8001
docker build -f services/document/Dockerfile -t doco-document-service .
```

Health endpoints are `/health` and `/ready`. Unit, contract, PDF, route, and real-gRPC integration tests live under `tests/`. Migrations must finish before startup. The wire package, decimal serialization, invoice formats, and download behavior remain compatible.
