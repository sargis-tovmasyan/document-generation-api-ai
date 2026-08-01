# Chat Service

The Chat service is the frontend-facing API for conversations, threads, memories, and compatibility proxy routes. It calls Document over the shared gRPC contract and uses the shared llama.cpp inference service.

From the backend workspace root:

```bash
uv sync --frozen --package doco-chat-service
uv run --frozen --package doco-chat-service pytest services/chat/tests
uv run --frozen --package doco-chat-service uvicorn chat_service.main:app --port 8000
```
