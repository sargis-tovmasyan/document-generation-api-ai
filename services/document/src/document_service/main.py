from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from document_service.core.config import DOCUMENT_GRPC_ADDRESS, GENERATED_DIR, ensure_directories
from document_service.db.connection import database_connection
from document_service.services.application import document_application
from document_service.grpc.server import create_grpc_server
from document_service.api.middleware.request_logging import RequestLoggingMiddleware
from document_service.core.telemetry import configure_logging, configure_tracing, instrument_fastapi_app
from document_service.api.routes.ai_invoice import router as ai_invoice_router
from document_service.api.routes.invoices import router as invoices_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    grpc_server, _ = create_grpc_server(
        document_application,
        DOCUMENT_GRPC_ADDRESS,
    )
    await grpc_server.start()
    try:
        yield
    finally:
        await grpc_server.stop(5)


configure_logging()
configure_tracing()
ensure_directories()

app = FastAPI(title="Document Service", version="1.0.0", lifespan=lifespan)
app.add_middleware(RequestLoggingMiddleware)
app.include_router(ai_invoice_router)
app.include_router(invoices_router)
app.mount("/generated", StaticFiles(directory=GENERATED_DIR), name="generated")
instrument_fastapi_app(app)


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "document-service"}


@app.get("/ready")
def readiness_check() -> dict[str, str]:
    with database_connection() as connection:
        connection.execute("SELECT 1").fetchone()
    return {"status": "ready", "service": "document-service"}
