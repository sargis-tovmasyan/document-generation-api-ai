from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from document_service.main import app


def test_application_lifespan_owns_grpc_server() -> None:
    grpc_server = AsyncMock()

    with patch(
        "document_service.main.create_grpc_server",
        return_value=(grpc_server, 50051),
    ):
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200

    grpc_server.start.assert_awaited_once_with()
    grpc_server.stop.assert_awaited_once_with(5)
