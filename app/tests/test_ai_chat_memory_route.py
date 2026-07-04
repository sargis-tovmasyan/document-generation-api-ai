import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.routes.ai_chat import ChatDecision
from app.routes.ai_chat_memory import AiChatMemoryRequest, _answer_chat_message_with_memory, chat
from app.schemas import InvoiceDraft
from app.services import chat_schema, knowledge_store
from app.services.chat_store import get_chat_thread, get_session_state, list_chat_messages


class AiChatMemoryRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        import app.database as database

        database.DATABASE_PATH = Path(self.temp_dir.name) / "app.db"
        chat_schema._ready = False
        knowledge_store._ready = False
        chat_schema.ensure_chat_schema()
        knowledge_store.ensure_knowledge_schema()

    async def test_chat_creates_thread_and_persists_messages(self) -> None:
        with (
            patch(
                "app.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="answer")),
            ),
            patch(
                "app.routes.ai_chat_memory._answer_chat_message_with_memory",
                AsyncMock(return_value="Hi, how can I help?"),
            ),
            patch("app.routes.ai_chat_memory._learn_from_turn", AsyncMock()),
        ):
            response = await chat(AiChatMemoryRequest(message="Hi"))

        self.assertEqual(response["status"], "answer")
        self.assertIn("chat_id", response)
        self.assertIsNotNone(get_chat_thread(response["chat_id"]))

        messages = list_chat_messages(response["chat_id"])
        self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
        self.assertEqual(messages[0]["content"], "Hi")

    async def test_session_draft_continues_across_turns(self) -> None:
        first_draft = InvoiceDraft.model_validate(
            {
                "invoice_number": "INV-010",
                "business": {"name": "Sargis Studio"},
                "client": {"name": "Alex"},
            }
        )
        second_draft = InvoiceDraft.model_validate(
            {
                "issue_date": "2026-07-04",
                "currency": "USD",
                "items": [{"description": "Design", "quantity": 1, "unit_price": 300}],
            }
        )

        with (
            patch(
                "app.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="create_invoice")),
            ),
            patch(
                "app.routes.ai_chat_memory._extract_invoice_draft_for_chat",
                AsyncMock(return_value=first_draft),
            ),
            patch("app.routes.ai_chat_memory._learn_from_turn", AsyncMock()),
        ):
            first_response = await chat(AiChatMemoryRequest(message="Create invoice INV-010 for Alex"))

        chat_id = first_response["chat_id"]
        self.assertEqual(first_response["status"], "missing_fields")
        self.assertEqual(get_session_state(chat_id)["draft"]["business"]["name"], "Sargis Studio")

        with (
            patch(
                "app.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="answer")),
            ),
            patch(
                "app.routes.ai_chat_memory._extract_invoice_draft_for_chat",
                AsyncMock(return_value=second_draft),
            ),
            patch(
                "app.routes.ai_chat_memory.create_invoice",
                return_value={
                    "id": 42,
                    "invoice_number": "INV-010",
                    "subtotal": Decimal("300.00"),
                    "total": Decimal("300.00"),
                    "currency": "USD",
                },
            ) as create_invoice_mock,
            patch("app.routes.ai_chat_memory._learn_from_turn", AsyncMock()),
        ):
            second_response = await chat(
                AiChatMemoryRequest(chat_id=chat_id, message="Issue date is 2026-07-04, USD, Design 300")
            )

        self.assertEqual(second_response["status"], "created")
        invoice = create_invoice_mock.call_args.args[0]
        self.assertEqual(invoice.business.name, "Sargis Studio")
        self.assertEqual(invoice.client.name, "Alex")
        self.assertEqual(get_session_state(chat_id)["last_document_id"], 42)
        self.assertNotIn("draft", get_session_state(chat_id))

    async def test_answer_prompt_uses_memory_context(self) -> None:
        with patch(
            "app.routes.ai_chat_memory.llm_client.complete_prompt",
            AsyncMock(return_value="Use USD for Alex."),
        ) as complete_mock:
            answer = await _answer_chat_message_with_memory(
                message="What currency should I use?",
                session_state={"current_intent": "create_invoice"},
                shared_memories=[{"content": "Client Alex usually uses USD."}],
                skill_memories=[{"title": "Monthly invoice", "description": "Ask for month."}],
                recent_messages=[{"role": "user", "content": "This is for Alex."}],
            )

        self.assertEqual(answer, "Use USD for Alex.")
        prompt = complete_mock.call_args.args[0]
        self.assertIn("Client Alex usually uses USD.", prompt)
        self.assertIn("This is for Alex.", prompt)


if __name__ == "__main__":
    unittest.main()
