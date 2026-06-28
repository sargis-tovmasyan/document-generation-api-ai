import json
import unittest

from pydantic import ValidationError

from app.schemas import InvoiceDraft
from app.services.invoice_draft_validator import (
    find_missing_invoice_fields,
    invoice_draft_to_create,
)


class InvoiceDraftValidatorTests(unittest.TestCase):
    def test_rejects_due_date_before_issue_date(self) -> None:
        with self.assertRaisesRegex(
            ValidationError,
            "due_date must be on or after issue_date",
        ):
            InvoiceDraft.model_validate(
                {
                    "issue_date": "2026-06-15",
                    "due_date": "2026-06-14",
                }
            )

    def test_serializes_item_numbers_as_json_numbers(self) -> None:
        draft = InvoiceDraft.model_validate(
            {
                "items": [
                    {
                        "description": "Website design",
                        "quantity": 1,
                        "unit_price": 300,
                    }
                ]
            }
        )

        serialized = json.loads(draft.model_dump_json())

        self.assertEqual(serialized["items"][0]["quantity"], 1.0)
        self.assertEqual(serialized["items"][0]["unit_price"], 300.0)

    def test_finds_backend_required_fields(self) -> None:
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

        self.assertEqual(
            find_missing_invoice_fields(draft),
            [
                "invoice_number",
                "issue_date",
                "currency",
                "business.name",
            ],
        )

    def test_reports_missing_item_fields_by_index(self) -> None:
        draft = InvoiceDraft.model_validate(
            {
                "invoice_number": "INV-001",
                "issue_date": "2026-06-15",
                "currency": "USD",
                "business": {"name": "Sargis Studio"},
                "client": {"name": "Alex"},
                "items": [{}],
            }
        )

        self.assertEqual(
            find_missing_invoice_fields(draft),
            [
                "items[0].description",
                "items[0].quantity",
                "items[0].unit_price",
            ],
        )

    def test_converts_complete_draft_to_invoice(self) -> None:
        draft = InvoiceDraft.model_validate(
            {
                "invoice_number": "INV-001",
                "issue_date": "2026-06-15",
                "currency": "usd",
                "template_language": "en",
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

        invoice = invoice_draft_to_create(draft)

        self.assertEqual(invoice.currency, "USD")
        self.assertEqual(invoice.template_language, "en")
        self.assertEqual(invoice.client.name, "Alex")

    def test_defaults_template_language_when_missing(self) -> None:
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

        invoice = invoice_draft_to_create(draft)

        self.assertEqual(invoice.template_language, "ru")


if __name__ == "__main__":
    unittest.main()
