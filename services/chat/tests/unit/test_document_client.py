from decimal import Decimal
import unittest
from chat_service.clients.document import DocumentClient
from chat_service.schemas import InvoiceDraft
from backend_contracts.documents.v1 import document_service_pb2 as pb2


class FakeStub:
    def __init__(self) -> None:
        self.complete_request = None

    async def ExtractDraft(self, request, **kwargs):
        return pb2.ExtractDraftResponse(
            analysis=pb2.DraftAnalysis(
                status="missing_fields",
                draft=pb2.DocumentDraft(
                    document_type="invoice",
                    client=pb2.Party(name="Customer"),
                ),
                missing_field_keys=["invoice_number"],
                fields_to_show=[
                    pb2.DraftField(
                        key="invoice_number",
                        label="Invoice number",
                        field_type="text",
                        required=True,
                    )
                ],
            )
        )

    async def CompleteDraft(self, request, **kwargs):
        self.complete_request = request
        return pb2.CompleteDraftResponse(
            created=pb2.DocumentCreated(
                document_id=17,
                document_type="invoice",
                invoice_number="INV-17",
                subtotal=pb2.Money(decimal_value="11.00", currency="USD"),
                total=pb2.Money(decimal_value="11.00", currency="USD"),
                pdf_url="/generated/invoices/inv-17.pdf",
                download_url="/invoices/17/download",
            )
        )


class DocumentClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_extract_returns_typed_missing_field_analysis(self) -> None:
        client = DocumentClient("unused:50051", timeout=2)
        client._stub = FakeStub()

        analysis = await client.extract_draft(
            "Create an invoice",
            request_id="request-17",
        )

        self.assertEqual(analysis.status, "missing_fields")
        self.assertEqual(analysis.draft.client.name, "Customer")
        self.assertEqual(analysis.missing_fields, ["invoice_number"])
        self.assertEqual(analysis.fields_to_show[0].key, "invoice_number")

    async def test_complete_maps_draft_and_created_result(self) -> None:
        stub = FakeStub()
        client = DocumentClient("unused:50051", timeout=2)
        client._stub = stub
        draft = InvoiceDraft(
            invoice_number="INV-17",
            issue_date="2026-08-01",
            currency="USD",
            template_language="en",
            business={"name": "Doco"},
            client={"name": "Customer"},
            items=[{"description": "Service", "quantity": "2", "unit_price": "5.50"}],
        )

        completion = await client.complete_draft(
            draft,
            request_id="request-17",
            idempotency_key="chat-17",
        )

        self.assertEqual(stub.complete_request.draft.items[0].quantity_decimal, "2")
        self.assertEqual(stub.complete_request.idempotency_key, "chat-17")
        self.assertEqual(completion.created.document_id, 17)
        self.assertEqual(completion.created.total, Decimal("11.00"))
