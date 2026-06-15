import unittest

from pydantic import ValidationError

from app.schemas import AiTestRequest, InvoiceCreate


class InvoiceCreateSchemaTests(unittest.TestCase):
    def test_normalizes_text_and_currency(self) -> None:
        invoice = InvoiceCreate.model_validate(
            {
                "invoice_number": " INV-001 ",
                "issue_date": "2026-06-15",
                "currency": " usd ",
                "business": {"name": " Sargis Studio "},
                "client": {"name": " Alex Johnson "},
                "items": [
                    {
                        "description": " Website design ",
                        "quantity": 1,
                        "unit_price": 300,
                    }
                ],
            }
        )

        self.assertEqual(invoice.invoice_number, "INV-001")
        self.assertEqual(invoice.currency, "USD")
        self.assertEqual(invoice.business.name, "Sargis Studio")
        self.assertEqual(invoice.items[0].description, "Website design")

    def test_rejects_due_date_before_issue_date(self) -> None:
        with self.assertRaisesRegex(
            ValidationError,
            "due_date must be on or after issue_date",
        ):
            InvoiceCreate.model_validate(
                {
                    "invoice_number": "INV-001",
                    "issue_date": "2026-06-15",
                    "due_date": "2026-06-14",
                    "currency": "USD",
                    "business": {"name": "Sargis Studio"},
                    "client": {"name": "Alex Johnson"},
                    "items": [
                        {
                            "description": "Website design",
                            "quantity": 1,
                            "unit_price": 300,
                        }
                    ],
                }
            )

    def test_rejects_empty_items(self) -> None:
        with self.assertRaises(ValidationError):
            InvoiceCreate.model_validate(
                {
                    "invoice_number": "INV-001",
                    "issue_date": "2026-06-15",
                    "currency": "USD",
                    "business": {"name": "Sargis Studio"},
                    "client": {"name": "Alex Johnson"},
                    "items": [],
                }
            )


class AiTestRequestSchemaTests(unittest.TestCase):
    def test_strips_message(self) -> None:
        payload = AiTestRequest(message="  Create an invoice note.  ")

        self.assertEqual(payload.message, "Create an invoice note.")

    def test_rejects_whitespace_only_message(self) -> None:
        with self.assertRaisesRegex(ValidationError, "message must not be empty"):
            AiTestRequest(message="   ")


if __name__ == "__main__":
    unittest.main()
