import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
GENERATED_DIR = BASE_DIR / "generated"
INVOICE_PDF_DIR = GENERATED_DIR / "invoices"
TEMPLATES_DIR = BASE_DIR / "templates"
DATABASE_PATH = DATA_DIR / "app.db"

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
LLM_COMPLETION_ENDPOINT = os.getenv("LLM_COMPLETION_ENDPOINT", "/completion")
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "120"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "256"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))

SERVICE_NAME = os.getenv("SERVICE_NAME", "document-generation-api")
DEPLOYMENT_ENVIRONMENT = os.getenv("DEPLOYMENT_ENVIRONMENT", "local")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
OTEL_ENABLED = os.getenv("OTEL_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv(
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "http://127.0.0.1:4318",
)


def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    INVOICE_PDF_DIR.mkdir(parents=True, exist_ok=True)
