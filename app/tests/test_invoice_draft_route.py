import unittest
from decimal import Decimal
from unittest.mock import patch

from app.routes.invoices import complete_invoice_draft
from app.schemas import InvoiceDraftCompleteRequest


class InvoiceDraftRouteTests(unittest.TestCase):
    def test_returns_missing_fields_without_creating_invoice(self) -> None:
        payload = InvoiceDraftCompleteRequest.model_validate(
            {"draft": {"client": {"name": "Alex"}}}
        )

        with patch("app.routes.invoices.create_invoice") as create_invoice:
            response = complete_invoice_draft(payload)

        self.assertEqual(response.status, "missing_fields")
        self.assertIn("invoice_number", response.missing_fields)
        create_invoice.assert_not_called()

    def test_creates_invoice_from_complete_draft(self) -> None:
        payload = InvoiceDraftCompleteRequest.model_validate(
            {
                "draft": {
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
            }
        )

        with patch(
            "app.routes.invoices.create_invoice",
            return_value={
                "id": 7,
                "invoice_number": "INV-001",
                "subtotal": Decimal("300.00"),
                "total": Decimal("300.00"),
                "currency": "USD",
                "pdf_url": "/generated/invoices/file.pdf",
            },
        ):
            response = complete_invoice_draft(payload)

        self.assertEqual(response.status, "created")
        self.assertEqual(response.invoice_id, 7)
        self.assertEqual(response.pdf_url, "/invoices/7/download")


if __name__ == "__main__":
    unittest.main()
