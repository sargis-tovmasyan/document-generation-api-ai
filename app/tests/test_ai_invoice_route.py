import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.routes.ai_invoice import extract_invoice_draft, generate_invoice_from_message
from app.schemas import AiInvoiceExtractRequest, InvoiceDraft
from app.services.ai_invoice_extractor import AiInvoiceParseError
from app.services.invoice_service import InvoiceNumberConflictError
from app.services.llm_client import LlmServiceError


class AiInvoiceRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_missing_fields_from_backend_validation(self) -> None:
        draft = InvoiceDraft.model_validate(
            {
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

        with patch(
            "app.routes.ai_invoice.ai_invoice_extractor.extract",
            AsyncMock(return_value=draft),
        ):
            response = await extract_invoice_draft(
                AiInvoiceExtractRequest(message="Create an invoice for Alex.")
            )

        self.assertEqual(response.status, "missing_fields")
        self.assertIn("invoice_number", response.missing_fields)
        self.assertNotIn("client.email", response.missing_fields)
        self.assertIn("invoice_number", [field.key for field in response.fields_to_show])
        self.assertIn("business.name", [field.key for field in response.fields_to_show])

    async def test_returns_ready_for_complete_draft(self) -> None:
        draft = InvoiceDraft.model_validate(
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

        with patch(
            "app.routes.ai_invoice.ai_invoice_extractor.extract",
            AsyncMock(return_value=draft),
        ):
            response = await extract_invoice_draft(
                AiInvoiceExtractRequest(message="Create an invoice.")
            )

        self.assertEqual(response.status, "ready")
        self.assertEqual(response.missing_fields, [])
        self.assertEqual(response.fields_to_show, [])

    async def test_generates_invoice_from_complete_ai_draft(self) -> None:
        draft = InvoiceDraft.model_validate(
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

        with (
            patch(
                "app.routes.ai_invoice.ai_invoice_extractor.extract",
                AsyncMock(return_value=draft),
            ),
            patch(
                "app.routes.ai_invoice.create_invoice",
                return_value={
                    "id": 7,
                    "invoice_number": "INV-001",
                    "subtotal": Decimal("300.00"),
                    "total": Decimal("300.00"),
                    "currency": "USD",
                },
            ) as create_invoice_mock,
        ):
            response = await generate_invoice_from_message(
                AiInvoiceExtractRequest(message="Create an invoice.")
            )

        self.assertEqual(response.status, "created")
        self.assertEqual(response.invoice_id, 7)
        self.assertEqual(response.pdf_url, "/invoices/7/download")
        create_invoice_mock.assert_called_once()

    async def test_generate_returns_missing_fields_without_creating_invoice(self) -> None:
        draft = InvoiceDraft.model_validate({"client": {"name": "Alex"}})

        with (
            patch(
                "app.routes.ai_invoice.ai_invoice_extractor.extract",
                AsyncMock(return_value=draft),
            ),
            patch("app.routes.ai_invoice.create_invoice") as create_invoice_mock,
        ):
            response = await generate_invoice_from_message(
                AiInvoiceExtractRequest(message="Create an invoice.")
            )

        self.assertEqual(response.status, "missing_fields")
        self.assertIn("invoice_number", response.missing_fields)
        self.assertEqual(response.fields_to_show[0].key, "invoice_number")
        create_invoice_mock.assert_not_called()

    async def test_generate_maps_invoice_number_conflict_to_409(self) -> None:
        draft = InvoiceDraft.model_validate(
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

        with (
            patch(
                "app.routes.ai_invoice.ai_invoice_extractor.extract",
                AsyncMock(return_value=draft),
            ),
            patch(
                "app.routes.ai_invoice.create_invoice",
                side_effect=InvoiceNumberConflictError("Invoice number exists"),
            ),
        ):
            with self.assertRaises(HTTPException) as context:
                await generate_invoice_from_message(
                    AiInvoiceExtractRequest(message="Create an invoice.")
                )

        self.assertEqual(context.exception.status_code, 409)

    async def test_returns_llm_unavailable_response(self) -> None:
        with patch(
            "app.routes.ai_invoice.ai_invoice_extractor.extract",
            AsyncMock(side_effect=LlmServiceError("offline")),
        ):
            response = await extract_invoice_draft(
                AiInvoiceExtractRequest(message="Create an invoice.")
            )

        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 503)
        self.assertIn(b'"status":"llm_unavailable"', response.body)

    async def test_returns_parse_error_response(self) -> None:
        with patch(
            "app.routes.ai_invoice.ai_invoice_extractor.extract",
            AsyncMock(side_effect=AiInvoiceParseError("bad JSON")),
        ):
            response = await extract_invoice_draft(
                AiInvoiceExtractRequest(message="Create an invoice.")
            )

        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 422)
        self.assertIn(b'"status":"ai_parse_error"', response.body)


if __name__ == "__main__":
    unittest.main()
