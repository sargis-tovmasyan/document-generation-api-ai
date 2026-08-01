from fastapi.testclient import TestClient

from chat_service.main import app


def test_chat_service_registers_chat_and_stream_routes() -> None:
    paths = set(app.openapi()["paths"])

    assert "/ai/chat" in paths
    assert "/ai/chat/stream" in paths
    assert "/chat-threads" in paths
    assert "/shared-memories" in paths
    assert "/skill-memories" in paths


def test_document_proxy_reports_upstream_unavailable() -> None:
    with TestClient(app) as client:
        response = client.get("/invoices")

    assert response.status_code == 503
    assert response.json() == {
        "status": "document_unavailable",
        "message": "Document service is temporarily unavailable.",
    }
