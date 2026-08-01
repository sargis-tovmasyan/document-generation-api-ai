# Chat Service

Frontend-facing FastAPI service for chat, threads, memories, orchestration, and the existing document compatibility routes. It owns `data/chat.db`, runs on port 8000, calls Document over gRPC/HTTP, and calls llama.cpp over HTTP.

From the workspace root:

```bash
uv sync --frozen --package doco-chat-service
CHAT_DATABASE_PATH=services/chat/data/chat.db uv run --package doco-chat-service alembic -c services/chat/alembic.ini upgrade head
uv run --frozen --package doco-chat-service pytest services/chat/tests -q
uv run --frozen --package doco-chat-service uvicorn chat_service.main:app --port 8000
docker build -f services/chat/Dockerfile -t doco-chat-service .
```

Health endpoints are `/health` and `/ready`. Unit and integration tests live under `tests/`; migrations must finish before application startup. All existing HTTP routes and environment variables remain compatible.
