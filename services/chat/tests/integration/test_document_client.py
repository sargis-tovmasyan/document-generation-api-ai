import os
from pathlib import Path
import subprocess
import sys


CHAT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[4]


def test_chat_document_client_calls_real_grpc_stub() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [
            str(CHAT_ROOT / "src"),
            str(BACKEND_ROOT / "packages" / "contracts" / "src"),
        ]
    )
    program = """
import asyncio
from chat_service.clients.document import DocumentClient
from backend_contracts.documents.v1 import document_service_pb2 as pb2
from backend_contracts.documents.v1 import document_service_pb2_grpc as pb2_grpc
import grpc

class FakeServicer(pb2_grpc.DocumentServiceServicer):
    async def ListDocuments(self, request, context):
        return pb2.ListDocumentsResponse(documents=[pb2.DocumentSummary(
            document_id=17,
            document_type="invoice",
            invoice_number="INV-17",
            issue_date="2026-08-01",
            currency="USD",
            business_name="Doco",
            client_name="Customer",
            total=pb2.Money(decimal_value="11.00", currency="USD"),
            pdf_url="/generated/invoices/inv-17.pdf",
            created_at="2026-08-01T09:30:00",
        )])

async def main():
    server = grpc.aio.server()
    pb2_grpc.add_DocumentServiceServicer_to_server(FakeServicer(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with DocumentClient(f"127.0.0.1:{port}", timeout=2) as client:
            documents = await client.list_documents(request_id="request-17")
            print(f"{documents[0].id},{documents[0].invoice_number},{documents[0].total}")
    finally:
        await server.stop(None)

asyncio.run(main())
"""

    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=CHAT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "17,INV-17,11.00"


def test_chat_document_client_maps_unavailable_status() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [
            str(CHAT_ROOT / "src"),
            str(BACKEND_ROOT / "packages" / "contracts" / "src"),
        ]
    )
    program = """
import asyncio
from chat_service.clients.document import DocumentClient, DocumentUnavailableError
from backend_contracts.documents.v1 import document_service_pb2_grpc as pb2_grpc
import grpc

class FakeServicer(pb2_grpc.DocumentServiceServicer):
    async def ListDocuments(self, request, context):
        await context.abort(grpc.StatusCode.UNAVAILABLE, "document service offline")

async def main():
    server = grpc.aio.server()
    pb2_grpc.add_DocumentServiceServicer_to_server(FakeServicer(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with DocumentClient(f"127.0.0.1:{port}", timeout=2) as client:
            try:
                await client.list_documents(request_id="request-17")
            except DocumentUnavailableError as error:
                print(str(error))
    finally:
        await server.stop(None)

asyncio.run(main())
"""

    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=CHAT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "document service offline"
