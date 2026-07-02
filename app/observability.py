import logging
import os
from contextvars import ContextVar

from opentelemetry import trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

from app.config import (
    DEPLOYMENT_ENVIRONMENT,
    LOG_LEVEL,
    OTEL_ENABLED,
    OTEL_EXPORTER_OTLP_ENDPOINT,
    SERVICE_NAME,
)

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    return request_id_context.get()


class RequestIdLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        record.service_name = SERVICE_NAME
        record.environment = DEPLOYMENT_ENVIRONMENT
        return True


def configure_logging() -> None:
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()

    request_filter = RequestIdLogFilter()
    formatter = logging.Formatter(
        "%(asctime)s level=%(levelname)s service=%(service_name)s "
        "env=%(environment)s request_id=%(request_id)s "
        "logger=%(name)s message=%(message)s"
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(request_filter)
    root_logger.addHandler(console_handler)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if not OTEL_ENABLED:
        return

    resource = Resource.create(
        {
            "service.name": SERVICE_NAME,
            "deployment.environment": DEPLOYMENT_ENVIRONMENT,
        }
    )

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=_otlp_endpoint("/v1/logs"))
        )
    )
    set_logger_provider(logger_provider)

    otel_handler = LoggingHandler(level=level, logger_provider=logger_provider)
    otel_handler.addFilter(request_filter)
    root_logger.addHandler(otel_handler)


def configure_tracing() -> None:
    if not OTEL_ENABLED:
        return

    resource = Resource.create(
        {
            "service.name": SERVICE_NAME,
            "deployment.environment": DEPLOYMENT_ENVIRONMENT,
        }
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(endpoint=_otlp_endpoint("/v1/traces"))
        )
    )
    trace.set_tracer_provider(provider)


def instrument_fastapi_app(app: object) -> None:
    if not OTEL_ENABLED:
        return

    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()


def _otlp_endpoint(path: str) -> str:
    endpoint = OTEL_EXPORTER_OTLP_ENDPOINT.rstrip("/")
    if endpoint.endswith(path):
        return endpoint
    return f"{endpoint}{path}"
