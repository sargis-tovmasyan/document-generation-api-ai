from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from clients.document import document_client
from core.config import ensure_directories
from db.connection import database_connection
from api.middleware.request_logging import RequestLoggingMiddleware
from core.telemetry import configure_logging, configure_tracing, instrument_fastapi_app
from api.routes.ai_chat_memory import router as ai_chat_router
from api.routes.chat_threads import router as chat_threads_router
from api.routes.document_proxy import close_document_proxy_client
from api.routes.document_proxy import router as document_proxy_router
from api.routes.memories import router as memories_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
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
