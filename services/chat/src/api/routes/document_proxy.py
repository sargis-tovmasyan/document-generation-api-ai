from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
import httpx
from starlette.background import BackgroundTask

from core.config import DOCUMENT_HTTP_BASE_URL


router = APIRouter(tags=["document-proxy"])
_client: httpx.AsyncClient | None = None
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0))
    return _client


async def close_document_proxy_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _body(response: httpx.Response) -> AsyncIterator[bytes]:
    if response.is_stream_consumed:
        yield response.content
        return
    async for chunk in response.aiter_raw():
        yield chunk


async def proxy_document_request(request: Request):
    query = f"?{request.url.query}" if request.url.query else ""
    url = f"{DOCUMENT_HTTP_BASE_URL}{request.url.path}{query}"
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _HOP_BY_HOP | {"host", "content-length"}
    }
    try:
        upstream_request = _get_client().build_request(
            request.method,
            url,
            headers=headers,
            content=await request.body(),
        )
        response = await _get_client().send(upstream_request, stream=True)
    except httpx.TimeoutException:
        return JSONResponse(
            status_code=504,
            content={
                "status": "document_timeout",
                "message": "Document service request timed out.",
            },
        )
    except httpx.RequestError:
        return JSONResponse(
            status_code=503,
            content={
                "status": "document_unavailable",
                "message": "Document service is temporarily unavailable.",
            },
        )

    response_headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in _HOP_BY_HOP | {"content-length"}
    }
    return StreamingResponse(
        _body(response),
        status_code=response.status_code,
        headers=response_headers,
        background=BackgroundTask(response.aclose),
    )


for path in (
    "/ai/invoice",
    "/ai/invoice/{path:path}",
    "/invoices",
    "/invoices/{path:path}",
    "/generated/{path:path}",
):
    router.add_api_route(
        path,
        proxy_document_request,
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
        include_in_schema=False,
    )
