import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
DATABASE_PATH = Path(os.getenv("CHAT_DATABASE_PATH", str(DATA_DIR / "chat.db")))
DOCUMENT_GRPC_TARGET = os.getenv("DOCUMENT_GRPC_TARGET", "127.0.0.1:50051")
DOCUMENT_HTTP_BASE_URL = os.getenv(
    "DOCUMENT_HTTP_BASE_URL", "http://127.0.0.1:8001"
).rstrip("/")
DOCUMENT_GRPC_TIMEOUT_SECONDS = float(
    os.getenv("DOCUMENT_GRPC_TIMEOUT_SECONDS", "30")
)

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
LLM_COMPLETION_ENDPOINT = os.getenv("LLM_COMPLETION_ENDPOINT", "/completion")
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "600"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "256"))
LLM_CHAT_MAX_TOKENS = int(os.getenv("LLM_CHAT_MAX_TOKENS", "1024"))
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
LLAMA_MODEL_FILE = os.getenv("LLAMA_MODEL_FILE", "unknown")

SERVICE_NAME = os.getenv("SERVICE_NAME", "chat-service")
DEPLOYMENT_ENVIRONMENT = os.getenv("DEPLOYMENT_ENVIRONMENT", "local")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

APP_LOG_DEBUG_PAYLOADS = os.getenv("APP_LOG_DEBUG_PAYLOADS", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
APP_LOG_FRONTEND_MESSAGES = os.getenv("APP_LOG_FRONTEND_MESSAGES", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
APP_LOG_LLM_RAW = os.getenv("APP_LOG_LLM_RAW", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
APP_LOG_RESPONSE_BODY = os.getenv("APP_LOG_RESPONSE_BODY", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
APP_LOG_MAX_FIELD_LENGTH = int(os.getenv("APP_LOG_MAX_FIELD_LENGTH", "2000"))

OTEL_ENABLED = os.getenv("OTEL_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
OTEL_TRACES_ENABLED = os.getenv("OTEL_TRACES_ENABLED", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv(
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "http://127.0.0.1:4318",
)


def ensure_directories() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
