import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from document_service.services.errors import (
    DocumentConflictError,
    DocumentExtractionInvalidError,
    DocumentExtractionUnavailableError,
)
from document_service.schemas.results import DocumentCreated, DraftAnalysis, DraftCompletion
from document_service.api.routes.ai_invoice import extract_invoice_draft, generate_invoice_from_message
from document_service.schemas import AiInvoiceExtractRequest, InvoiceDraft
from document_service.services.document_template_fields import invoice_fields_to_show


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


class AiInvoiceRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_missing_fields_from_document_boundary(self) -> None:
        draft = InvoiceDraft.model_validate({"client": {"name": "Alex"}})
        missing_fields = [
            "invoice_number",
            "issue_date",
            "currency",
            "business.name",
            "items",
        ]
        analysis = DraftAnalysis(
            status="missing_fields",
            draft=draft,
            missing_fields=missing_fields,
            fields_to_show=invoice_fields_to_show(missing_fields),
        )

        with patch("document_service.api.routes.ai_invoice.document_application") as application:
            application.extract_and_analyze = AsyncMock(return_value=analysis)
            response = await extract_invoice_draft(
                AiInvoiceExtractRequest(message="Create an invoice for Alex.")
            )

        self.assertEqual(response.status, "missing_fields")
        self.assertIn("invoice_number", response.missing_fields)
        self.assertIn("invoice_number", [field.key for field in response.fields_to_show])

    async def test_returns_ready_for_complete_draft(self) -> None:
        analysis = DraftAnalysis(
            status="ready",
            draft=complete_draft(),
            missing_fields=[],
            fields_to_show=[],
        )

        with patch("document_service.api.routes.ai_invoice.document_application") as application:
            application.extract_and_analyze = AsyncMock(return_value=analysis)
            response = await extract_invoice_draft(
                AiInvoiceExtractRequest(message="Create an invoice.")
            )

        self.assertEqual(response.status, "ready")
        self.assertEqual(response.missing_fields, [])
        self.assertEqual(response.fields_to_show, [])

    async def test_generates_invoice_from_complete_ai_draft(self) -> None:
        completion = DraftCompletion(created=created_document())

        with patch("document_service.api.routes.ai_invoice.document_application") as application:
            application.generate_from_message = AsyncMock(return_value=completion)
            response = await generate_invoice_from_message(
                AiInvoiceExtractRequest(message="Create an invoice.")
            )

        self.assertEqual(response.status, "created")
        self.assertEqual(response.invoice_id, 7)
        self.assertEqual(response.pdf_url, "/invoices/7/download")

    async def test_generate_returns_missing_fields(self) -> None:
        draft = InvoiceDraft.model_validate({"client": {"name": "Alex"}})
        analysis = DraftAnalysis(
            status="missing_fields",
            draft=draft,
            missing_fields=["invoice_number"],
            fields_to_show=invoice_fields_to_show(["invoice_number"]),
        )

        with patch("document_service.api.routes.ai_invoice.document_application") as application:
            application.generate_from_message = AsyncMock(
                return_value=DraftCompletion(analysis=analysis)
            )
            response = await generate_invoice_from_message(
                AiInvoiceExtractRequest(message="Create an invoice.")
            )

        self.assertEqual(response.status, "missing_fields")
        self.assertEqual(response.missing_fields, ["invoice_number"])
        self.assertEqual(response.fields_to_show[0].key, "invoice_number")

    async def test_generate_maps_document_conflict_to_409(self) -> None:
        with patch("document_service.api.routes.ai_invoice.document_application") as application:
            application.generate_from_message = AsyncMock(
                side_effect=DocumentConflictError("Invoice number exists")
            )
            with self.assertRaises(HTTPException) as context:
                await generate_invoice_from_message(
                    AiInvoiceExtractRequest(message="Create an invoice.")
                )

        self.assertEqual(context.exception.status_code, 409)
        self.assertEqual(context.exception.detail, "Invoice number exists")

    async def test_returns_llm_unavailable_response(self) -> None:
        with patch("document_service.api.routes.ai_invoice.document_application") as application:
            application.extract_and_analyze = AsyncMock(
                side_effect=DocumentExtractionUnavailableError("offline")
            )
            response = await extract_invoice_draft(
                AiInvoiceExtractRequest(message="Create an invoice.")
            )

        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 503)
        self.assertIn(b'"status":"llm_unavailable"', response.body)

    async def test_returns_parse_error_response(self) -> None:
        with patch("document_service.api.routes.ai_invoice.document_application") as application:
            application.extract_and_analyze = AsyncMock(
                side_effect=DocumentExtractionInvalidError("bad JSON")
            )
            response = await extract_invoice_draft(
                AiInvoiceExtractRequest(message="Create an invoice.")
            )

        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 422)
        self.assertIn(b'"status":"ai_parse_error"', response.body)


if __name__ == "__main__":
    unittest.main()
