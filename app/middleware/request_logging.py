import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.observability import request_id_context

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = request_id_context.set(request_id)
        started_at = time.perf_counter()

        logger.info(
            "request.started method=%s path=%s client=%s",
            request.method,
            request.url.path,
            request.client.host if request.client else "unknown",
        )

        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - started_at) * 1000
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "request.completed method=%s path=%s status_code=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
            )
            return response
        except Exception:
            duration_ms = (time.perf_counter() - started_at) * 1000
            logger.exception(
                "request.failed method=%s path=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                duration_ms,
            )
            raise
        finally:
            request_id_context.reset(token)
