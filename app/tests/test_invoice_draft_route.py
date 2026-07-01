import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.routes.invoices import complete_invoice_draft
from app.schemas import InvoiceDraftCompleteRequest


class InvoiceDraftRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_missing_fields_without_creating_invoice(self) -> None:
        payload = InvoiceDraftCompleteRequest.model_validate(
            {"draft": {"client": {"name": "Alex"}}}
        )

        with patch("app.routes.invoices.create_invoice") as create_invoice:
            response = await complete_invoice_draft(payload)

        self.assertEqual(response.status, "missing_fields")
        self.assertIn("invoice_number", response.missing_fields)
        create_invoice.assert_not_called()

    async def test_creates_invoice_from_complete_draft(self) -> None:
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
            response = await complete_invoice_draft(payload)

        self.assertEqual(response.status, "created")
        self.assertEqual(response.invoice_id, 7)
        self.assertEqual(response.pdf_url, "/invoices/7/download")

    async def test_normalizes_raw_items_with_llm_before_creating_invoice(self) -> None:
        payload = InvoiceDraftCompleteRequest.model_validate(
            {
                "draft": {
                    "invoice_number": "INV-005",
                    "issue_date": "2026-07-01",
                    "currency": "AMD",
                    "business": {"name": "Sargis Studio"},
                    "client": {"name": "Artashi mot LLC"},
                    "raw_items": "Sharuma x1 - 1200, Qyabab 2 times - 1100",
                }
            }
        )

        with (
            patch(
                "app.routes.invoices.llm_client.complete_prompt",
                AsyncMock(
                    return_value=(
                        '{"items": ['
                        '{"description": "Sharuma", "quantity": 1, "unit_price": 1200},'
                        '{"description": "Qyabab", "quantity": 2, "unit_price": 1100}'
                        "]}"
                    )
                ),
            ) as complete_prompt,
            patch(
                "app.routes.invoices.create_invoice",
                return_value={
                    "id": 9,
                    "invoice_number": "INV-005",
                    "subtotal": Decimal("3400.00"),
                    "total": Decimal("3400.00"),
                    "currency": "AMD",
                    "pdf_url": "/generated/invoices/file.pdf",
                },
            ) as create_invoice,
        ):
            response = await complete_invoice_draft(payload)

        complete_prompt.assert_awaited_once()
        created_invoice = create_invoice.call_args.args[0]
        self.assertEqual(created_invoice.items[0].description, "Sharuma")
        self.assertEqual(created_invoice.items[0].quantity, 1)
        self.assertEqual(created_invoice.items[0].unit_price, 1200)
        self.assertEqual(created_invoice.items[1].description, "Qyabab")
        self.assertEqual(created_invoice.items[1].quantity, 2)
        self.assertEqual(created_invoice.items[1].unit_price, 1100)
        self.assertEqual(response.total, Decimal("3400.00"))


if __name__ == "__main__":
    unittest.main()
