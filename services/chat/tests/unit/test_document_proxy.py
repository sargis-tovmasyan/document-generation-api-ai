import httpx
from fastapi.testclient import TestClient
from chat_service.main import app
from chat_service.api.routes import document_proxy


def test_proxy_preserves_request_and_upstream_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "http://127.0.0.1:8001/invoices?dry=1"
        assert request.headers["x-test-correlation"] == "request-17"
        assert await request.aread() == b'{"invoice_number":"INV-17"}'
        return httpx.Response(
            201,
            content=b"created-document",
            headers={
                "content-type": "application/octet-stream",
                "content-disposition": 'attachment; filename="invoice.pdf"',
            },
        )

    document_proxy._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    )

    with TestClient(app) as client:
        response = client.post(
            "/invoices?dry=1",
            content=b'{"invoice_number":"INV-17"}',
            headers={
                "content-type": "application/json",
                "x-test-correlation": "request-17",
            },
        )

    assert response.status_code == 201
    assert response.content == b"created-document"
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"] == 'attachment; filename="invoice.pdf"'
