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
          "business_name": null,
          "business_email": null,
          "business_address": null,
          "client_name": "Alex",
          "client_email": null,
          "client_address": "Yerevan, Armenia",
          "item_description": "Website design",
          "item_quantity": 1,
          "item_unit_price": 300,
          "notes": null,
          "payment_terms": null
        }
        ```
        """

        draft = await AiInvoiceExtractor(client).extract(
            "Create an invoice for Alex for website design, 300 dollars."
        )

        self.assertEqual(draft.currency, "USD")
        self.assertEqual(draft.template_language, "en")
        self.assertEqual(draft.client.name, "Alex")
        self.assertEqual(draft.items[0].unit_price, 300)
        prompt = client.complete_prompt.await_args.args[0]
        self.assertIn("Return only JSON", prompt)
        self.assertNotIn("template_language", prompt)
        self.assertIn(
            "Create an invoice for Alex for website design, 300 dollars.",
            prompt,
        )
        self.assertNotIn("json_schema", client.complete_prompt.await_args.kwargs)

    async def test_selects_template_language_from_user_message(self) -> None:
        client = AsyncMock()
        client.complete_prompt.return_value = """
        {
          "document_type": "invoice",
          "currency": null,
          "business_name": null,
          "business_email": null,
          "business_address": null,
          "client_name": "Alex",
          "client_email": null,
          "client_address": null,
          "item_description": "Website design",
          "item_quantity": 1,
          "item_unit_price": 300,
          "notes": null,
          "payment_terms": null
        }
        """

        draft = await AiInvoiceExtractor(client).extract(
            "Create a Russian invoice for Alex for website design, 300 dollars."
        )

        self.assertEqual(draft.template_language, "ru")

    async def test_extracts_frontend_invoice_message_from_llm_json(self) -> None:
        client = AsyncMock()
        client.complete_prompt.return_value = """
        {
          "document_type": "invoice",
          "invoice_number": "INV-001",
          "issue_date": "2026-06-28",
          "due_date": "2026-07-05",
          "currency": "USD",
          "business_name": "Sargis Studio",
          "business_email": "hello@example.com",
          "business_address": "Yerevan, Armenia",
          "client_name": "Alex Johnson",
          "client_email": "alex@example.com",
          "client_address": null,
          "item_description": "website design",
          "item_quantity": 1,
          "item_unit_price": 300,
          "notes": "Thank you for your business",
          "payment_terms": "Payment due within 7 days"
        }
        """

        draft = await AiInvoiceExtractor(client).extract(
            "Create an invoice for Alex Johnson for website design, 300 dollars. "
            "The invoice number is INV-001. Issue date is 2026-06-28. "
            "Due date is 2026-07-05. My business name is Sargis Studio. "
            "My business email is hello@example.com. "
            "My business address is Yerevan, Armenia. "
            "Client email is alex@example.com. "
            "Add note: Thank you for your business. "
            "Payment terms: Payment due within 7 days."
        )

        self.assertEqual(draft.invoice_number, "INV-001")
        self.assertEqual(str(draft.issue_date), "2026-06-28")
        self.assertEqual(str(draft.due_date), "2026-07-05")
        self.assertEqual(draft.currency, "USD")
        self.assertEqual(draft.business.name, "Sargis Studio")
        self.assertEqual(draft.business.email, "hello@example.com")
        self.assertEqual(draft.business.address, "Yerevan, Armenia")
        self.assertEqual(draft.client.name, "Alex Johnson")
        self.assertEqual(draft.client.email, "alex@example.com")
        self.assertIsNone(draft.client.address)
        self.assertEqual(draft.items[0].description, "website design")
        self.assertEqual(draft.items[0].quantity, 1)
        self.assertEqual(draft.items[0].unit_price, 300)
        self.assertEqual(draft.notes, "Thank you for your business")
        self.assertEqual(draft.payment_terms, "Payment due within 7 days")

    async def test_extracts_multiple_invoice_items(self) -> None:
        client = AsyncMock()
        client.complete_prompt.return_value = """
        {
          "invoice_number": "INV-003",
          "issue_date": "2026-06-28",
          "due_date": "2026-07-12",
          "currency": "USD",
          "business_name": "Sargis Studio",
          "client_name": "John Smith",
          "items": [
            {
              "description": "Product A",
              "quantity": 3,
              "unit_price": 99
            },
            {
              "description": "Product B",
              "quantity": 2,
              "unit_price": 45
            }
          ]
        }
        """

        draft = await AiInvoiceExtractor(client).extract(
            "Invoice INV-003 from Sargis Studio for John Smith, issued "
            "2026-06-28, due 2026-07-12, USD - 3 x Product A at $99 each, "
            "2 x Product B at $45 each"
        )

        self.assertEqual(draft.invoice_number, "INV-003")
        self.assertEqual(str(draft.issue_date), "2026-06-28")
        self.assertEqual(str(draft.due_date), "2026-07-12")
        self.assertEqual(draft.currency, "USD")
        self.assertEqual(draft.business.name, "Sargis Studio")
        self.assertEqual(draft.client.name, "John Smith")
        self.assertEqual(len(draft.items), 2)
        self.assertEqual(draft.items[0].description, "Product A")
        self.assertEqual(draft.items[0].quantity, 3)
        self.assertEqual(draft.items[0].unit_price, 99)
        self.assertEqual(draft.items[1].description, "Product B")
        self.assertEqual(draft.items[1].quantity, 2)
        self.assertEqual(draft.items[1].unit_price, 45)

    async def test_accepts_rub_currency(self) -> None:
        client = AsyncMock()
        client.complete_prompt.return_value = """
        {
          "document_type": "invoice",
          "invoice_number": null,
          "issue_date": null,
          "due_date": null,
          "currency": "RUB",
          "business_name": null,
          "business_email": null,
          "business_address": null,
          "client_name": "Alex",
          "client_email": null,
          "client_address": null,
          "item_description": "Software development",
          "item_quantity": 1,
          "item_unit_price": 20000,
          "notes": null,
          "payment_terms": null
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
          "business_name": "Alex",
          "business_email": "fake@example.com",
          "business_address": "123 Main Street",
          "client_name": "John",
          "client_email": null,
          "client_address": null,
          "item_description": "web design",
          "item_quantity": 300,
          "item_unit_price": 300,
          "notes": "Example note",
          "payment_terms": "30 days"
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
        client.complete_prompt.return_value = "No invoice data found."

        with self.assertRaisesRegex(AiInvoiceParseError, "invalid invoice JSON"):
            await AiInvoiceExtractor(client).extract("Create an invoice.")

    async def test_rejects_invalid_draft_values(self) -> None:
        client = AsyncMock()
        client.complete_prompt.return_value = """
        {
          "issue_date": "2026-06-28",
          "due_date": "2026-06-20",
          "currency": "USD"
        }
        """

        with self.assertRaisesRegex(AiInvoiceParseError, "invalid invoice draft"):
            await AiInvoiceExtractor(client).extract(
                "Create an invoice. Issue date is 2026-06-28. "
                "Due date is 2026-06-20. Use USD."
            )


if __name__ == "__main__":
    unittest.main()
