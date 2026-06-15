import unittest
from unittest.mock import AsyncMock, patch

from fastapi.responses import JSONResponse

from app.routes.ai_invoice import extract_invoice_draft
from app.schemas import AiInvoiceExtractRequest, InvoiceDraft
from app.services.ai_invoice_extractor import AiInvoiceParseError
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
