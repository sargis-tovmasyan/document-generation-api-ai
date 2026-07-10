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

    async def test_non_invoice_message_does_not_call_invoice_endpoint(self) -> None:
        with (
            patch(
                "app.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="list_invoices")),
            ),
            patch(
                "app.routes.ai_chat_memory._answer_chat_message_with_memory",
                AsyncMock(return_value="Sounds great. What details should we plan?"),
            ),
            patch("app.routes.ai_chat_memory.list_invoices") as list_invoices_mock,
            patch("app.routes.ai_chat_memory._learn_from_turn", AsyncMock()),
        ):
            response = await chat(AiChatMemoryRequest(message="Lets made a BBQ!"))

        self.assertEqual(response["status"], "answer")
        list_invoices_mock.assert_not_called()

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
                thinking_enabled=True,
            )

        self.assertEqual(answer, "Use USD for Alex.")
        prompt = complete_mock.call_args.args[0]
        self.assertIn("Client Alex usually uses USD.", prompt)
        self.assertIn("This is for Alex.", prompt)
        self.assertIn("You may reason internally", prompt)
        self.assertIn("You have access to saved memories and recent messages", prompt)
        self.assertIn("Do not claim you have no memory", prompt)

    async def test_answer_removes_memory_context_leak(self) -> None:
        with patch(
            "app.routes.ai_chat_memory.llm_client.complete_prompt",
            AsyncMock(
                return_value=(
                    "Sounds great! Let's plan the details together. "
                    "(memory context: The previous messages were about planning a BBQ.)"
                    "\n\nThe only current message is: Lets made a BBQ!"
                )
            ),
        ):
            answer = await _answer_chat_message_with_memory(
                message="Lets made a BBQ!",
                session_state={},
                shared_memories=[],
                skill_memories=[],
                recent_messages=[],
            )

        self.assertEqual(answer, "Sounds great! Let's plan the details together.")

    async def test_memory_request_without_value_asks_for_value(self) -> None:
        with (
            patch(
                "app.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="remember_memory")),
            ),
            patch(
                "app.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value='{"has_memory":false,"memory":""}'),
            ),
        ):
            response = await chat(AiChatMemoryRequest(message="can you memorize a number and remind me later?"))

        self.assertEqual(response["status"], "answer")
        self.assertIn("send me the number", response["message"])

    async def test_remembers_and_recalls_number(self) -> None:
        with (
            patch(
                "app.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="remember_memory")),
            ),
            patch(
                "app.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value='{"has_memory":true,"memory":"number 1234"}'),
            ),
        ):
            remember_response = await chat(AiChatMemoryRequest(message="remember number 1234"))

        chat_id = remember_response["chat_id"]
        self.assertEqual(remember_response["status"], "answer")
        self.assertIn("remember", remember_response["message"].lower())

        with patch(
            "app.routes.ai_chat_memory._decide_chat_action",
            AsyncMock(return_value=ChatDecision(action="recall_memory")),
        ):
            recall_response = await chat(
                AiChatMemoryRequest(chat_id=chat_id, message="what number did I ask you to remember?")
            )

        self.assertEqual(recall_response["status"], "answer")
        self.assertIn("1234", recall_response["message"])

        messages = list_chat_messages(chat_id)
        self.assertEqual([message["role"] for message in messages], ["user", "assistant", "user", "assistant"])

    async def test_remembers_number_when_extractor_misses_explicit_value(self) -> None:
        with (
            patch(
                "app.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="remember_memory")),
            ),
            patch(
                "app.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value='{"has_memory":false,"memory":""}'),
            ),
        ):
            remember_response = await chat(AiChatMemoryRequest(message="remember number 1234"))

        chat_id = remember_response["chat_id"]
        with patch(
            "app.routes.ai_chat_memory._decide_chat_action",
            AsyncMock(return_value=ChatDecision(action="recall_memory")),
        ):
            recall_response = await chat(
                AiChatMemoryRequest(chat_id=chat_id, message="what number did I ask you to remember?")
            )

        self.assertIn("1234", recall_response["message"])


if __name__ == "__main__":
    unittest.main()
