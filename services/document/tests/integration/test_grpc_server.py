import os
from pathlib import Path
import subprocess
import sys


DOCUMENT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[4]


def test_document_grpc_server_handles_reset_over_a_real_channel() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [
            str(DOCUMENT_ROOT / "src"),
            str(BACKEND_ROOT / "packages" / "contracts" / "src"),
        ]
    )
    program = """
import asyncio
from transport.grpc.server import create_grpc_server
from backend_contracts.documents.v1 import document_service_pb2 as pb2
from backend_contracts.documents.v1 import document_service_pb2_grpc as pb2_grpc
import grpc

class FakeApplication:
    def reset_documents(self):
        return {"deleted_invoices": 3, "deleted_items": 7}

async def main():
    server, port = create_grpc_server(FakeApplication(), "127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = pb2_grpc.DocumentServiceStub(channel)
            response = await stub.ResetDocuments(
                pb2.ResetDocumentsRequest(request_id="request-17", document_type="invoice")
            )
            print(f"{response.deleted_documents},{response.deleted_items}")
    finally:
        await server.stop(None)

asyncio.run(main())
"""

    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=DOCUMENT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "3,7"


def test_document_grpc_server_maps_not_found_to_typed_status() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [
            str(DOCUMENT_ROOT / "src"),
            str(BACKEND_ROOT / "packages" / "contracts" / "src"),
        ]
    )
    program = """
import asyncio
from services.errors import DocumentNotFoundError
from transport.grpc.server import create_grpc_server
from backend_contracts.documents.v1 import document_service_pb2 as pb2
from backend_contracts.documents.v1 import document_service_pb2_grpc as pb2_grpc
import grpc

class FakeApplication:
    def get_document(self, document_id):
        raise DocumentNotFoundError("Invoice not found")

async def main():
    server, port = create_grpc_server(FakeApplication(), "127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = pb2_grpc.DocumentServiceStub(channel)
            try:
                await stub.GetDocument(
                    pb2.GetDocumentRequest(
                        request_id="request-17",
                        document_type="invoice",
                        document_id=404,
                    )
                )
            except grpc.aio.AioRpcError as error:
                print(f"{error.code().name}:{error.details()}")
    finally:
        await server.stop(None)

asyncio.run(main())
"""

    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=DOCUMENT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "NOT_FOUND:Invoice not found"
