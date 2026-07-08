import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from app.routes.ai_chat import ChatDecision, chat
from app.schemas import AiChatRequest, InvoiceDraft
from app.services.invoice_service import InvoiceNumberConflictError
from app.services.llm_client import LlmServiceError


class AiChatRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_simple_answer_from_llm_decision(self) -> None:
        with patch(
            "app.routes.ai_chat._decide_chat_action",
            AsyncMock(
                return_value=ChatDecision(
                    action="answer",
                    message="",
                )
            ),
        ), patch(
            "app.routes.ai_chat._answer_chat_message",
            AsyncMock(return_value="Hi, how can I help with your documents today?"),
        ):
            response = await chat(AiChatRequest(message="hi"))

        self.assertEqual(response.status, "answer")
        self.assertIn("documents", response.message)

    async def test_lists_invoices_when_llm_selects_invoice_list(self) -> None:
        with (
            patch(
                "app.routes.ai_chat._decide_chat_action",
                AsyncMock(
                    return_value=ChatDecision(
                        action="list_invoices",
                        message="I will fetch your invoices.",
                    )
                ),
            ),
            patch(
                "app.routes.ai_chat.list_invoices",
                return_value=[
                    {
                        "id": 2,
                        "invoice_number": "INV-002",
                        "issue_date": "2026-06-29",
                        "due_date": None,
                        "currency": "USD",
                        "business_name": "Sargis Studio",
                        "client_name": "Alex",
                        "total": Decimal("300.00"),
                        "pdf_url": "/generated/invoices/inv-002.pdf",
                        "created_at": "2026-06-29T10:00:00",
                    }
                ],
            ),
        ):
            response = await chat(AiChatRequest(message="show me all invoices"))

        self.assertEqual(response.status, "invoice_list")
        self.assertEqual(len(response.invoices), 1)
        self.assertEqual(response.invoices[0].invoice_number, "INV-002")

    async def test_lists_invoices_when_llm_outputs_action_token(self) -> None:
        with (
            patch(
                "app.routes.ai_chat.llm_client.complete_prompt",
                AsyncMock(return_value="list_invoices"),
            ) as complete_mock,
            patch("app.routes.ai_chat.list_invoices", return_value=[]),
        ):
            response = await chat(AiChatRequest(message="Show me all my invoices"))

        self.assertEqual(response.status, "invoice_list")
        self.assertEqual(response.invoices, [])
        complete_mock.assert_called_once()

    async def test_non_invoice_message_does_not_call_invoice_endpoint(self) -> None:
        with (
            patch(
                "app.routes.ai_chat._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="list_invoices")),
            ),
            patch(
                "app.routes.ai_chat._answer_chat_message",
                AsyncMock(return_value="Sounds great. What details should we plan?"),
            ),
            patch("app.routes.ai_chat.list_invoices") as list_invoices_mock,
        ):
            response = await chat(AiChatRequest(message="Lets made a BBQ!"))

        self.assertEqual(response.status, "answer")
        list_invoices_mock.assert_not_called()

    async def test_creates_invoice_when_llm_selects_create_invoice(self) -> None:
        draft = InvoiceDraft.model_validate(
            {
                "invoice_number": "INV-001",
                "issue_date": "2026-06-29",
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
                "app.routes.ai_chat._decide_chat_action",
                AsyncMock(
                    return_value=ChatDecision(
                        action="create_invoice",
                        message="I will create the invoice.",
                    )
                ),
            ),
            patch("app.routes.ai_chat._extract_draft_or_error", AsyncMock(return_value=draft)),
            patch(
                "app.routes.ai_chat.create_invoice",
                return_value={
                    "id": 7,
                    "invoice_number": "INV-001",
                    "subtotal": Decimal("300.00"),
                    "total": Decimal("300.00"),
                    "currency": "USD",
                },
            ),
        ):
            response = await chat(AiChatRequest(message="create invoice INV-001"))

        self.assertEqual(response.status, "created")
        self.assertEqual(response.invoice_id, 7)

    async def test_returns_missing_fields_without_creating_invoice(self) -> None:
        draft = InvoiceDraft.model_validate({"client": {"name": "Alex"}})

        with (
            patch(
                "app.routes.ai_chat._decide_chat_action",
                AsyncMock(
                    return_value=ChatDecision(
                        action="create_invoice",
                        message="I will create the invoice.",
                    )
                ),
            ),
            patch("app.routes.ai_chat._extract_draft_or_error", AsyncMock(return_value=draft)),
            patch("app.routes.ai_chat.create_invoice") as create_invoice_mock,
        ):
            response = await chat(AiChatRequest(message="create invoice for Alex"))

        self.assertEqual(response.status, "missing_fields")
        self.assertIn("invoice_number", response.missing_fields)
        create_invoice_mock.assert_not_called()

    async def test_returns_all_missing_fields_when_invoice_parse_fails(self) -> None:
        parse_error = JSONResponse(
            status_code=422,
            content={
                "status": "ai_parse_error",
                "message": "Could not extract invoice details.",
            },
        )

        with (
            patch(
                "app.routes.ai_chat._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="create_invoice")),
            ),
            patch(
                "app.routes.ai_chat._extract_draft_or_error",
                AsyncMock(return_value=parse_error),
            ),
            patch("app.routes.ai_chat.create_invoice") as create_invoice_mock,
        ):
            response = await chat(AiChatRequest(message="create a new invoice"))

        self.assertEqual(response.status, "missing_fields")
        self.assertIn("invoice_number", response.missing_fields)
        self.assertIn("business.name", response.missing_fields)
        self.assertIn("client.name", response.missing_fields)
        self.assertIn("items", response.missing_fields)
        create_invoice_mock.assert_not_called()

    async def test_prefills_fallback_draft_when_invoice_parse_fails(self) -> None:
        parse_error = JSONResponse(
            status_code=422,
            content={
                "status": "ai_parse_error",
                "message": "Could not extract invoice details.",
            },
        )

        with (
            patch(
                "app.routes.ai_chat._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="create_invoice")),
            ),
            patch(
                "app.routes.ai_chat._extract_draft_or_error",
                AsyncMock(return_value=parse_error),
            ),
        ):
            response = await chat(
                AiChatRequest(
                    message="lets create a invoice INV-005, for my client Artashi mot LLC"
                )
            )

        self.assertEqual(response.status, "missing_fields")
        self.assertEqual(response.draft.invoice_number, "INV-005")
        self.assertEqual(response.draft.client.name, "Artashi mot LLC")
        self.assertNotIn("invoice_number", response.missing_fields)
        self.assertNotIn("client.name", response.missing_fields)

    async def test_prefills_labeled_names_when_invoice_parse_fails(self) -> None:
        parse_error = JSONResponse(
            status_code=422,
            content={
                "status": "ai_parse_error",
                "message": "Could not extract invoice details.",
            },
        )

        with (
            patch(
                "app.routes.ai_chat._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="create_invoice")),
            ),
            patch(
                "app.routes.ai_chat._extract_draft_or_error",
                AsyncMock(return_value=parse_error),
            ),
        ):
            response = await chat(
                AiChatRequest(
                    message=(
                        "lets create a invoice! client is Grill.am, "
                        "my business name is Sargis Tovmasyan IE"
                    )
                )
            )

        self.assertEqual(response.status, "missing_fields")
        self.assertEqual(response.draft.client.name, "Grill.am")
        self.assertEqual(response.draft.business.name, "Sargis Tovmasyan IE")
        self.assertNotIn("client.name", response.missing_fields)
        self.assertNotIn("business.name", response.missing_fields)

    async def test_prefills_names_from_llm_when_message_has_no_items(self) -> None:
        draft = InvoiceDraft.model_validate(
            {
                "business": {"name": "Sargis Tovmasyan IE"},
                "client": {"name": "Grill.am"},
                "items": [{"description": "Grill.am"}],
            }
        )

        with (
            patch(
                "app.routes.ai_chat._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="create_invoice")),
            ),
            patch("app.routes.ai_chat._extract_draft_or_error", AsyncMock(return_value=draft)),
        ):
            response = await chat(
                AiChatRequest(
                    message=(
                        "lets create a invoice! client is Grill.am, "
                        "my business name is Sargis Tovmasyan IE"
                    )
                )
            )

        self.assertEqual(response.status, "missing_fields")
        self.assertEqual(response.draft.client.name, "Grill.am")
        self.assertEqual(response.draft.business.name, "Sargis Tovmasyan IE")
        self.assertEqual(response.draft.items, [])
        self.assertNotIn("client.name", response.missing_fields)
        self.assertNotIn("business.name", response.missing_fields)
        self.assertIn("items", response.missing_fields)

    async def test_merges_fallback_items_when_extractor_misses_them(self) -> None:
        draft = InvoiceDraft.model_validate({"client": {"name": "Alex"}})

        with (
            patch(
                "app.routes.ai_chat._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="create_invoice")),
            ),
            patch("app.routes.ai_chat._extract_draft_or_error", AsyncMock(return_value=draft)),
        ):
            response = await chat(
                AiChatRequest(
                    message="create invoice for Alex for website design 300 dollars"
                )
            )

        self.assertEqual(response.status, "missing_fields")
        self.assertEqual(response.draft.client.name, "Alex")
        self.assertEqual(response.draft.currency, "USD")
        self.assertEqual(response.draft.items[0].description, "website design")
        self.assertEqual(response.draft.items[0].unit_price, 300)
        self.assertNotIn("client.name", response.missing_fields)
        self.assertNotIn("items", response.missing_fields)

    async def test_maps_llm_unavailable_to_503(self) -> None:
        with patch(
            "app.routes.ai_chat._decide_chat_action",
            AsyncMock(side_effect=LlmServiceError("offline")),
        ):
            response = await chat(AiChatRequest(message="hi"))

        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 503)
        self.assertIn(b'"status":"llm_unavailable"', response.body)

    async def test_maps_invoice_conflict_to_409(self) -> None:
        draft = InvoiceDraft.model_validate(
            {
                "invoice_number": "INV-001",
                "issue_date": "2026-06-29",
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
                "app.routes.ai_chat._decide_chat_action",
                AsyncMock(
                    return_value=ChatDecision(
                        action="create_invoice",
                        message="I will create the invoice.",
                    )
                ),
            ),
            patch("app.routes.ai_chat._extract_draft_or_error", AsyncMock(return_value=draft)),
            patch(
                "app.routes.ai_chat.create_invoice",
                side_effect=InvoiceNumberConflictError("Invoice number exists"),
            ),
        ):
            with self.assertRaises(HTTPException) as context:
                await chat(AiChatRequest(message="create invoice INV-001"))

        self.assertEqual(context.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
