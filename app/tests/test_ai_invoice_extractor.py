import unittest
from unittest.mock import AsyncMock

from app.services.ai_invoice_extractor import (
    AiInvoiceExtractor,
    AiInvoiceParseError,
)


class AiInvoiceExtractorTests(unittest.IsolatedAsyncioTestCase):
    async def test_parses_and_validates_invoice_json(self) -> None:
        client = AsyncMock()
        client.complete_prompt.return_value = """
        {
          "document_type": "invoice",
          "invoice_number": null,
          "issue_date": null,
          "due_date": null,
          "currency": "usd",
          "business": {"name": null, "email": null, "address": null},
          "client": {"name": "Alex", "email": null, "address": null},
          "items": [
            {
              "description": "Website design",
              "quantity": 1,
              "unit_price": 300
            }
          ],
          "notes": null,
          "payment_terms": null,
          "missing_fields": ["wrong.model.value"]
        }
        """

        draft = await AiInvoiceExtractor(client).extract(
            "Create an invoice for Alex for website design, 300 dollars."
        )

        self.assertEqual(draft.currency, "USD")
        self.assertEqual(draft.client.name, "Alex")
        self.assertEqual(draft.items[0].unit_price, 300)
        prompt = client.complete_prompt.await_args.args[0]
        self.assertIn("Return only one JSON object", prompt)
        self.assertIn(
            "Create an invoice for Alex for website design, 300 dollars.",
            prompt,
        )
        self.assertIn(
            "json_schema",
            client.complete_prompt.await_args.kwargs,
        )

    async def test_accepts_rub_currency(self) -> None:
        client = AsyncMock()
        client.complete_prompt.return_value = """
        {
          "document_type": "invoice",
          "invoice_number": null,
          "issue_date": null,
          "due_date": null,
          "currency": "RUB",
          "business": {"name": null, "email": null, "address": null},
          "client": {"name": "Alex", "email": null, "address": null},
          "items": [
            {
              "description": "Software development",
              "quantity": 1,
              "unit_price": 20000
            }
          ],
          "notes": null,
          "payment_terms": null,
          "missing_fields": []
        }
        """

        draft = await AiInvoiceExtractor(client).extract(
            "Create an invoice for Alex for Software development, 20000 rubles."
        )

        self.assertEqual(draft.currency, "RUB")
        self.assertEqual(draft.items[0].unit_price, 20000)

    async def test_clears_hallucinations_and_deduplicates_items(self) -> None:
        client = AsyncMock()
        client.complete_prompt.return_value = """
        {
          "document_type": "invoice",
          "invoice_number": "12345",
          "issue_date": "2023-01-01",
          "due_date": "2023-01-15",
          "currency": "USD",
          "business": {
            "name": "Alex",
            "email": "fake@example.com",
            "address": "123 Main Street"
          },
          "client": {"name": "John", "email": null, "address": null},
          "items": [
            {
              "description": "web design",
              "quantity": 300,
              "unit_price": 300
            },
            {
              "description": "web design",
              "quantity": 300,
              "unit_price": 300
            }
          ],
          "notes": "Example note",
          "payment_terms": "30 days",
          "missing_fields": []
        }
        """

        draft = await AiInvoiceExtractor(client).extract(
            "Create an invoice for Alex for website design, 300 dollars."
        )

        self.assertIsNone(draft.invoice_number)
        self.assertIsNone(draft.issue_date)
        self.assertIsNone(draft.business.name)
        self.assertEqual(draft.client.name, "Alex")
        self.assertEqual(len(draft.items), 1)
        self.assertEqual(draft.items[0].quantity, 1)
        self.assertEqual(draft.items[0].unit_price, 300)
        self.assertIsNone(draft.notes)

    async def test_rejects_markdown_or_invalid_json(self) -> None:
        client = AsyncMock()
        client.complete_prompt.return_value = "```json\n{}\n```"

        with self.assertRaisesRegex(AiInvoiceParseError, "invalid invoice JSON"):
            await AiInvoiceExtractor(client).extract("Create an invoice.")

    async def test_rejects_invalid_draft_values(self) -> None:
        client = AsyncMock()
        client.complete_prompt.return_value = """
        {
          "document_type": "receipt",
          "currency": "DOLLARS",
          "business": {},
          "client": {},
          "items": []
        }
        """

        with self.assertRaisesRegex(AiInvoiceParseError, "invalid invoice draft"):
            await AiInvoiceExtractor(client).extract("Create an invoice.")


if __name__ == "__main__":
    unittest.main()
