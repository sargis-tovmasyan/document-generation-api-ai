# Document Service

The Document service owns invoice extraction, validation, persistence, PDF generation, HTTP routes, and the `documents.v1` gRPC server.

From the backend workspace root:

```bash
uv sync --frozen --package doco-document-service
uv run --frozen --package doco-document-service pytest services/document/tests
uv run --frozen --package doco-document-service uvicorn document_service.main:app --port 8001
```
