import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.observability import request_id_context
from app.observability_events import log_event


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        token = request_id_context.set(request_id)
        started_at = time.perf_counter()

        log_event(
            "request.started",
            method=request.method,
            path=request.url.path,
            query=str(request.url.query) if request.url.query else None,
            client=request.client.host if request.client else "unknown",
            user_agent=request.headers.get("user-agent"),
        )

        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - started_at) * 1000
            response.headers["X-Request-ID"] = request_id
            log_event(
                "request.completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
            )
            return response
        except Exception:
            duration_ms = (time.perf_counter() - started_at) * 1000
            log_event(
                "request.failed",
                method=request.method,
                path=request.url.path,
                duration_ms=round(duration_ms, 2),
            )
            raise
        finally:
            request_id_context.reset(token)
