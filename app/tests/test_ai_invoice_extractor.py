import unittest
from unittest.mock import AsyncMock

from app.services.ai_invoice_extractor import (
    AiInvoiceExtractor,
    AiInvoiceParseError,
)


GROUNDED_BASIC = """
{
  "document_type": "invoice",
  "invoice_number": null,
  "issue_date": null,
  "due_date": null,
  "currency": "USD",
  "template_language": "en",
  "business_name": null,
  "business_email": null,
  "business_address": null,
  "client_name": "Alex",
  "client_email": null,
  "client_address": null,
  "items": [{"description": "Website design", "quantity": 1, "unit_price": 300}],
  "notes": null,
  "payment_terms": null
}
"""


class AiInvoiceExtractorTests(unittest.IsolatedAsyncioTestCase):
    async def test_extracts_grounded_invoice_in_one_schema_constrained_call(self) -> None:
        client = AsyncMock()
        client.complete_prompt.return_value = GROUNDED_BASIC

        draft = await AiInvoiceExtractor(client).extract(
            "Create an invoice for Alex for website design, 300 dollars."
        )

        self.assertEqual(draft.currency, "USD")
        self.assertEqual(draft.template_language, "en")
        self.assertEqual(draft.client.name, "Alex")
        self.assertEqual(draft.items[0].unit_price, 300)
        client.complete_prompt.assert_awaited_once()

        prompt = client.complete_prompt.await_args.args[0]
        kwargs = client.complete_prompt.await_args.kwargs

        self.assertIn("Return only JSON", prompt)
        self.assertIn("Create an invoice for Alex for website design, 300 dollars.", prompt)
        self.assertIn("Never invent values", prompt)
        self.assertLess(len(prompt), 1200)
        self.assertIn("json_schema", kwargs)
        self.assertEqual(
            kwargs["json_schema"]["properties"]["document_type"],
            {"type": "string", "enum": ["invoice"]},
        )
        self.assertIn("business_name", kwargs["json_schema"]["properties"])
        self.assertNotIn("business", kwargs["json_schema"]["properties"])

    async def test_generic_invoice_request_keeps_all_unknown_fields_empty(self) -> None:
        client = AsyncMock()
        client.complete_prompt.return_value = """
        {
          "document_type": "invoice",
          "invoice_number": null,
          "issue_date": null,
          "due_date": null,
          "currency": null,
          "template_language": "en",
          "business_name": null,
          "business_email": null,
          "business_address": null,
          "client_name": null,
          "client_email": null,
          "client_address": null,
          "items": [],
          "notes": null,
          "payment_terms": null
        }
        """

        draft = await AiInvoiceExtractor(client).extract("Create a new invoice.")

        self.assertIsNone(draft.invoice_number)
        self.assertIsNone(draft.business.name)
        self.assertIsNone(draft.client.name)
        self.assertEqual(draft.items, [])
        prompt = client.complete_prompt.await_args.args[0]
        self.assertIn("generic invoice", prompt)
        self.assertIn("[] when no items", prompt)

    async def test_supports_semantic_template_language_grounding(self) -> None:
        client = AsyncMock()
        client.complete_prompt.return_value = GROUNDED_BASIC.replace(
            '"template_language": "en"',
            '"template_language": "ru"',
        )

        draft = await AiInvoiceExtractor(client).extract(
            "Create a Russian invoice for Alex for website design, 300 dollars."
        )

        self.assertEqual(draft.template_language, "ru")

    async def test_accepts_multiple_items_after_llm_grounding(self) -> None:
        client = AsyncMock()
        client.complete_prompt.return_value = """
            {
              "document_type": "invoice",
              "invoice_number": "INV-003",
              "issue_date": "2026-06-28",
              "due_date": "2026-07-12",
              "currency": "USD",
              "template_language": "en",
              "business_name": "Sargis Studio",
              "business_email": null,
              "business_address": null,
              "client_name": "John Smith",
              "client_email": null,
              "client_address": null,
              "items": [
                {"description": "Product A", "quantity": 3, "unit_price": 99},
                {"description": "Product B", "quantity": 2, "unit_price": 45}
              ],
              "notes": null,
              "payment_terms": null
            }
            """

        draft = await AiInvoiceExtractor(client).extract(
            "Invoice INV-003 from Sargis Studio for John Smith, issued "
            "2026-06-28, due 2026-07-12, USD - 3 x Product A at $99 each, "
            "2 x Product B at $45 each"
        )

        self.assertEqual(draft.invoice_number, "INV-003")
        self.assertEqual(len(draft.items), 2)
        self.assertEqual(draft.items[0].description, "Product A")
        self.assertEqual(draft.items[1].unit_price, 45)

    async def test_maps_all_explicit_fields_from_flat_grounding_response(self) -> None:
        client = AsyncMock()
        client.complete_prompt.return_value = """
            {
              "document_type": "invoice",
              "invoice_number": "TEST-PR13-FIX-0714",
              "issue_date": "2026-07-14",
              "due_date": "2026-07-21",
              "currency": "USD",
              "template_language": "en",
              "business_name": "QA Studio",
              "business_email": null,
              "business_address": null,
              "client_name": "Gamma LLC",
              "client_email": null,
              "client_address": null,
              "items": [
                {"description": "website audit", "quantity": 1, "unit_price": 125}
              ],
              "notes": null,
              "payment_terms": null
            }
            """

        draft = await AiInvoiceExtractor(client).extract(
            "Create invoice TEST-PR13-FIX-0714 from QA Studio for client Gamma LLC, "
            "issued 2026-07-14, due 2026-07-21, USD, one website audit x1 at 125 USD."
        )

        self.assertEqual(draft.invoice_number, "TEST-PR13-FIX-0714")
        self.assertEqual(str(draft.issue_date), "2026-07-14")
        self.assertEqual(str(draft.due_date), "2026-07-21")
        self.assertEqual(draft.business.name, "QA Studio")
        self.assertEqual(draft.client.name, "Gamma LLC")
        self.assertEqual(draft.items[0].description, "website audit")
        client.complete_prompt.assert_awaited_once()

    async def test_rejects_markdown_or_invalid_extraction_json(self) -> None:
        client = AsyncMock()
        client.complete_prompt.return_value = "No invoice data found."

        with self.assertRaisesRegex(AiInvoiceParseError, "invalid invoice JSON"):
            await AiInvoiceExtractor(client).extract("Create an invoice.")

    async def test_rejects_invalid_grounded_draft_values(self) -> None:
        client = AsyncMock()
        client.complete_prompt.return_value = """
            {
              "document_type": "invoice",
              "invoice_number": null,
              "issue_date": "2026-06-28",
              "due_date": "2026-06-20",
              "currency": "USD",
              "template_language": "en",
              "business_name": null,
              "business_email": null,
              "business_address": null,
              "client_name": null,
              "client_email": null,
              "client_address": null,
              "items": [],
              "notes": null,
              "payment_terms": null
            }
            """

        with self.assertRaisesRegex(AiInvoiceParseError, "invalid invoice draft"):
            await AiInvoiceExtractor(client).extract(
                "Create an invoice. Issue date is 2026-06-28. "
                "Due date is 2026-06-20. Use USD."
            )


if __name__ == "__main__":
    unittest.main()
