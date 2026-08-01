from datetime import date, datetime
from decimal import Decimal
import unittest

from pathlib import Path

from app.documents.models import DocumentCreated, DownloadDescriptor, DraftAnalysis, DraftCompletion
from app.grpc.servicer import DocumentGrpcServicer
from app.schemas import InvoiceCreate, InvoiceDraft, InvoiceListItem
from documents.v1 import document_service_pb2 as pb2


class FakeApplication:
    completed_draft: InvoiceDraft | None = None

    async def extract_and_analyze(self, message: str) -> DraftAnalysis:
        return DraftAnalysis(
            status="ready",
            draft=InvoiceDraft(
                invoice_number="INV-17",
                issue_date=date(2026, 8, 1),
                currency="USD",
                business={"name": "Doco"},
                client={"name": "Customer"},
                items=[{"description": "Service", "quantity": "2", "unit_price": "5.50"}],
            ),
            missing_fields=[],
            fields_to_show=[],
        )

    def list_documents(self) -> list[InvoiceListItem]:
        return [
            InvoiceListItem(
                id=17,
                invoice_number="INV-17",
                issue_date=date(2026, 8, 1),
                due_date=None,
                currency="USD",
                business_name="Doco",
                client_name="Customer",
                total=Decimal("11.00"),
                pdf_url="/generated/invoices/inv-17.pdf",
                created_at=datetime(2026, 8, 1, 9, 30),
            )
        ]

    async def complete_draft(self, draft: InvoiceDraft) -> DraftCompletion:
        self.completed_draft = draft
        return DraftCompletion(
            created=DocumentCreated(
                document_id=17,
                invoice_number=draft.invoice_number or "",
                subtotal=Decimal("11.00"),
                total=Decimal("11.00"),
                currency=draft.currency or "USD",
                pdf_url="/generated/invoices/inv-17.pdf",
                download_url="/invoices/17/download",
            )
        )

    def get_document(self, document_id: int) -> InvoiceListItem:
        return self.list_documents()[0]

    def create_document(self, invoice: InvoiceCreate) -> DocumentCreated:
        return DocumentCreated(
            document_id=17,
            invoice_number=invoice.invoice_number,
            subtotal=Decimal("11.00"),
            total=Decimal("11.00"),
            currency=invoice.currency,
            pdf_url="/generated/invoices/inv-17.pdf",
            download_url="/invoices/17/download",
        )

    def get_download_descriptor(self, document_id: int) -> DownloadDescriptor:
        return DownloadDescriptor(
            document_id=document_id,
            filename="inv-17.pdf",
            media_type="application/pdf",
            path=Path("/private/generated/inv-17.pdf"),
        )


class DocumentGrpcServicerTests(unittest.IsolatedAsyncioTestCase):
    async def test_extract_draft_maps_domain_analysis_to_contract(self) -> None:
        servicer = DocumentGrpcServicer(FakeApplication())

        response = await servicer.ExtractDraft(
            pb2.ExtractDraftRequest(
                request_id="request-17",
                document_type="invoice",
                message="Create invoice INV-17",
            ),
            None,
        )

        self.assertEqual(response.analysis.status, "ready")
        self.assertEqual(response.analysis.draft.invoice_number, "INV-17")
        self.assertEqual(response.analysis.draft.items[0].quantity_decimal, "2")
        self.assertEqual(response.analysis.draft.items[0].unit_price.decimal_value, "5.50")

    async def test_list_documents_preserves_decimal_money(self) -> None:
        servicer = DocumentGrpcServicer(FakeApplication())

        response = await servicer.ListDocuments(
            pb2.ListDocumentsRequest(request_id="request-17", document_type="invoice"),
            None,
        )

        self.assertEqual(len(response.documents), 1)
        self.assertEqual(response.documents[0].document_id, 17)
        self.assertEqual(response.documents[0].total.decimal_value, "11.00")
        self.assertEqual(response.documents[0].created_at, "2026-08-01T09:30:00")

    async def test_complete_draft_maps_contract_both_directions(self) -> None:
        application = FakeApplication()
        servicer = DocumentGrpcServicer(application)

        response = await servicer.CompleteDraft(
            pb2.CompleteDraftRequest(
                request_id="request-17",
                document_type="invoice",
                idempotency_key="chat-17",
                draft=pb2.DocumentDraft(
                    document_type="invoice",
                    invoice_number="INV-17",
                    issue_date="2026-08-01",
                    currency="USD",
                    business=pb2.Party(name="Doco"),
                    client=pb2.Party(name="Customer"),
                    items=[
                        pb2.DraftItem(
                            description="Service",
                            quantity_decimal="2",
                            unit_price=pb2.Money(decimal_value="5.50", currency="USD"),
                        )
                    ],
                ),
            ),
            None,
        )

        self.assertEqual(application.completed_draft.invoice_number, "INV-17")
        self.assertEqual(application.completed_draft.items[0].unit_price, Decimal("5.50"))
        self.assertEqual(response.WhichOneof("result"), "created")
        self.assertEqual(response.created.total.decimal_value, "11.00")

    async def test_download_descriptor_never_exposes_filesystem_path(self) -> None:
        servicer = DocumentGrpcServicer(FakeApplication())

        response = await servicer.GetDownloadDescriptor(
            pb2.GetDownloadDescriptorRequest(
                request_id="request-17",
                document_type="invoice",
                document_id=17,
            ),
            None,
        )

        self.assertEqual(response.descriptor.filename, "inv-17.pdf")
        self.assertEqual(response.descriptor.download_url, "/invoices/17/download")
        self.assertNotIn("private", str(response))

    async def test_create_and_get_document_use_typed_contract_results(self) -> None:
        servicer = DocumentGrpcServicer(FakeApplication())
        draft = pb2.DocumentDraft(
            document_type="invoice",
            invoice_number="INV-17",
            issue_date="2026-08-01",
            currency="USD",
            template_language="en",
            business=pb2.Party(name="Doco"),
            client=pb2.Party(name="Customer"),
            items=[
                pb2.DraftItem(
                    description="Service",
                    quantity_decimal="2",
                    unit_price=pb2.Money(decimal_value="5.50", currency="USD"),
                )
            ],
        )

        created = await servicer.CreateDocument(
            pb2.CreateDocumentRequest(
                request_id="request-17",
                document_type="invoice",
                idempotency_key="chat-17",
                draft=draft,
            ),
            None,
        )
        fetched = await servicer.GetDocument(
            pb2.GetDocumentRequest(
                request_id="request-17",
                document_type="invoice",
                document_id=17,
            ),
            None,
        )

        self.assertEqual(created.created.document_id, 17)
        self.assertEqual(created.created.total.decimal_value, "11.00")
        self.assertEqual(fetched.document.invoice_number, "INV-17")
