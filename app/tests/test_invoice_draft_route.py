import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.responses import FileResponse

from app.documents.errors import (
    DocumentConflictError,
    DocumentItemsInvalidError,
    DocumentItemsUnavailableError,
    DocumentNotFoundError,
)
from app.documents.models import (
    DocumentCreated,
    DownloadDescriptor,
    DraftAnalysis,
    DraftCompletion,
)
from app.routes.invoices import (
    complete_invoice_draft,
    create_invoice_endpoint,
    download_invoice_endpoint,
    list_invoices_endpoint,
    reset_invoices_endpoint,
)
from app.schemas import (
    InvoiceCreate,
    InvoiceDraft,
    InvoiceDraftCompleteRequest,
    InvoiceListItem,
)
from app.services.document_template_fields import invoice_fields_to_show


def complete_draft() -> InvoiceDraft:
    return InvoiceDraft.model_validate(
        {
            "invoice_number": "INV-001",
            "issue_date": "2026-06-15",
            "currency": "USD",
            "business": {"name": "Sargis Studio"},
            "client": {"name": "Alex"},
            "items": [
                {
                    "description": "Website design",
                    "quantity": 1,
                    "unit_price": 300,
                }
            ],
        }
    )


def complete_invoice() -> InvoiceCreate:
    return InvoiceCreate.model_validate(
        complete_draft().model_dump(
            exclude={"document_type", "raw_items"},
            exclude_none=True,
        )
    )


def created_document() -> DocumentCreated:
    return DocumentCreated(
        document_id=7,
        invoice_number="INV-001",
        subtotal=Decimal("300.00"),
        total=Decimal("300.00"),
        currency="USD",
        pdf_url="/generated/invoices/file.pdf",
        download_url="/invoices/7/download",
    )


class InvoiceRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_missing_fields_from_document_boundary(self) -> None:
        payload = InvoiceDraftCompleteRequest.model_validate(
            {"draft": {"client": {"name": "Alex"}}}
        )
        missing_fields = ["invoice_number", "issue_date", "currency", "business.name", "items"]
        analysis = DraftAnalysis(
            status="missing_fields",
            draft=payload.draft,
            missing_fields=missing_fields,
            fields_to_show=invoice_fields_to_show(missing_fields),
        )

        with patch("app.routes.invoices.document_application") as application:
            application.complete_draft = AsyncMock(
                return_value=DraftCompletion(analysis=analysis)
            )
            response = await complete_invoice_draft(payload)

        self.assertEqual(response.status, "missing_fields")
        self.assertIn("invoice_number", response.missing_fields)
        self.assertEqual(response.fields_to_show[0].key, "invoice_number")

    async def test_creates_invoice_from_complete_draft(self) -> None:
        payload = InvoiceDraftCompleteRequest(draft=complete_draft())

        with patch("app.routes.invoices.document_application") as application:
            application.complete_draft = AsyncMock(
                return_value=DraftCompletion(created=created_document())
            )
            response = await complete_invoice_draft(payload)

        self.assertEqual(response.status, "created")
        self.assertEqual(response.invoice_id, 7)
        self.assertEqual(response.pdf_url, "/invoices/7/download")

    async def test_successful_chat_completion_updates_and_clears_session(self) -> None:
        payload = InvoiceDraftCompleteRequest(
            draft=complete_draft(),
            chat_id="chat-1",
        )

        with (
            patch("app.routes.invoices.document_application") as application,
            patch(
                "app.routes.invoices.get_session_state",
                return_value={"current_intent": "create_invoice"},
            ),
            patch("app.routes.invoices.upsert_session_state") as upsert,
            patch("app.routes.invoices.clear_document_scope") as clear,
        ):
            application.complete_draft = AsyncMock(
                return_value=DraftCompletion(created=created_document())
            )
            await complete_invoice_draft(payload)

        self.assertEqual(
            upsert.call_args.args,
            (
                "chat-1",
                {"current_intent": "create_invoice", "last_document_id": 7},
            ),
        )
        clear.assert_called_once_with("chat-1")

    async def test_item_normalization_unavailable_maps_to_503(self) -> None:
        payload = InvoiceDraftCompleteRequest(draft=complete_draft())

        with patch("app.routes.invoices.document_application") as application:
            application.complete_draft = AsyncMock(
                side_effect=DocumentItemsUnavailableError("offline")
            )
            with self.assertRaises(HTTPException) as context:
                await complete_invoice_draft(payload)

        self.assertEqual(context.exception.status_code, 503)
        self.assertEqual(
            context.exception.detail,
            "AI assistant is temporarily unavailable. Please try again later.",
        )

    async def test_invalid_items_map_to_422(self) -> None:
        payload = InvoiceDraftCompleteRequest(draft=complete_draft())

        with patch("app.routes.invoices.document_application") as application:
            application.complete_draft = AsyncMock(
                side_effect=DocumentItemsInvalidError("bad items")
            )
            with self.assertRaises(HTTPException) as context:
                await complete_invoice_draft(payload)

        self.assertEqual(context.exception.status_code, 422)
        self.assertIn("Could not understand invoice items", context.exception.detail)

    def test_direct_create_preserves_generated_pdf_url(self) -> None:
        with patch("app.routes.invoices.document_application") as application:
            application.create_document.return_value = created_document()
            response = create_invoice_endpoint(complete_invoice())

        self.assertEqual(response.id, 7)
        self.assertEqual(response.pdf_url, "/generated/invoices/file.pdf")

    def test_direct_create_maps_conflict_to_409(self) -> None:
        with patch("app.routes.invoices.document_application") as application:
            application.create_document.side_effect = DocumentConflictError(
                "Invoice number exists"
            )
            with self.assertRaises(HTTPException) as context:
                create_invoice_endpoint(complete_invoice())

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.detail, "Invoice number exists")

    def test_list_and_reset_use_document_boundary(self) -> None:
        listed = InvoiceListItem(
            id=7,
            invoice_number="INV-001",
            issue_date=date(2026, 6, 15),
            due_date=None,
            currency="USD",
            business_name="Sargis Studio",
            client_name="Alex",
            total=Decimal("300.00"),
            pdf_url="/generated/invoices/file.pdf",
            created_at=datetime(2026, 7, 31, 12, 0, 0),
        )
        reset = {"deleted_invoices": 1, "deleted_items": 1}

        with patch("app.routes.invoices.document_application") as application:
            application.list_documents.return_value = [listed]
            application.reset_documents.return_value = reset

            self.assertEqual(list_invoices_endpoint(), [listed])
            self.assertEqual(reset_invoices_endpoint(), reset)

    def test_download_uses_boundary_descriptor(self) -> None:
        descriptor = DownloadDescriptor(
            document_id=7,
            filename="invoice.pdf",
            media_type="application/pdf",
            path=Path(__file__),
        )

        with patch("app.routes.invoices.document_application") as application:
            application.get_download_descriptor.return_value = descriptor
            response = download_invoice_endpoint(7)

        self.assertIsInstance(response, FileResponse)
        self.assertEqual(response.path, descriptor.path)
        self.assertEqual(response.media_type, "application/pdf")
        self.assertIn("invoice.pdf", response.headers["content-disposition"])

    def test_download_preserves_missing_record_and_file_details(self) -> None:
        with patch("app.routes.invoices.document_application") as application:
            for detail in ("Invoice not found", "Generated PDF file not found"):
                application.get_download_descriptor.side_effect = (
                    DocumentNotFoundError(detail)
                )
                with self.subTest(detail=detail):
                    with self.assertRaises(HTTPException) as context:
                        download_invoice_endpoint(7)
                    self.assertEqual(context.exception.status_code, 404)
                    self.assertEqual(context.exception.detail, detail)


if __name__ == "__main__":
    unittest.main()
