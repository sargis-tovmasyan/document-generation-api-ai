from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.clients.document_client import document_client
from app.config import ensure_directories
from app.database import database_connection, initialize_database
from app.middleware.request_logging import RequestLoggingMiddleware
from app.observability import configure_logging, configure_tracing, instrument_fastapi_app
from app.routes.ai_chat_memory import router as ai_chat_router
from app.routes.chat_threads import router as chat_threads_router
from app.routes.document_proxy import close_document_proxy_client
from app.routes.document_proxy import router as document_proxy_router
from app.routes.memories import router as memories_router
from app.services.chat_schema import ensure_chat_schema
from app.services.knowledge_store import ensure_knowledge_schema


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database()
    ensure_chat_schema()
    ensure_knowledge_schema()
    try:
        yield
    finally:
        await document_client.close()
        await close_document_proxy_client()


configure_logging()
configure_tracing()
ensure_directories()

app = FastAPI(title="Chat Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(RequestLoggingMiddleware)
app.include_router(ai_chat_router)
app.include_router(chat_threads_router)
app.include_router(memories_router)
app.include_router(document_proxy_router)
instrument_fastapi_app(app)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "chat-service"}


@app.get("/ready")
def readiness_check() -> dict[str, str]:
    with database_connection() as connection:
        connection.execute("SELECT 1").fetchone()
    return {"status": "ready", "service": "chat-service"}
