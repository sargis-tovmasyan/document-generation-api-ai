from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import GENERATED_DIR, ensure_directories
from app.database import initialize_database
from app.middleware.request_logging import RequestLoggingMiddleware
from app.observability import configure_logging, configure_tracing, instrument_fastapi_app
from app.routes.ai_chat_memory import router as ai_chat_router
from app.routes.ai_invoice import router as ai_invoice_router
from app.routes.auth import router as auth_router
from app.routes.chat_threads import router as chat_threads_router
from app.routes.invoices import router as invoices_router
from app.routes.memories import router as memories_router
from app.services.auth_schema import ensure_auth_schema
from app.services.chat_schema import ensure_chat_schema
from app.services.knowledge_store import ensure_knowledge_schema


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database()
    ensure_auth_schema()
    ensure_chat_schema()
    ensure_knowledge_schema()
    yield


configure_logging()
configure_tracing()
ensure_directories()

app = FastAPI(title="Document Generation API", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestLoggingMiddleware)
app.include_router(auth_router)
app.include_router(ai_chat_router)
app.include_router(chat_threads_router)
app.include_router(memories_router)
app.include_router(ai_invoice_router)
app.include_router(invoices_router)
app.mount("/generated", StaticFiles(directory=GENERATED_DIR), name="generated")
instrument_fastapi_app(app)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
