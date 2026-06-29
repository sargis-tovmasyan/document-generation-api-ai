from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import GENERATED_DIR, ensure_directories
from app.database import initialize_database
from app.routes.ai_chat import router as ai_chat_router
from app.routes.ai_invoice import router as ai_invoice_router
from app.routes.invoices import router as invoices_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database()
    yield


ensure_directories()

app = FastAPI(title="Document Generation API", version="0.1.0", lifespan=lifespan)
app.include_router(ai_chat_router)
app.include_router(ai_invoice_router)
app.include_router(invoices_router)
app.mount("/generated", StaticFiles(directory=GENERATED_DIR), name="generated")


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
