import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

from chat_service.api.routes.ai_chat import ChatDecision
from chat_service.clients.document import (
    DocumentAnalysis,
    DocumentCompletion,
    DocumentCreated,
)
from chat_service.api.routes.ai_chat_memory import (
    AiChatMemoryRequest,
    _answer_chat_message_with_memory,
    _answer_prompt_with_memory,
    _clean_memory_safe_answer,
    _select_answer_context,
    _stream_answer_with_memory,
    chat,
    chat_stream,
)
from chat_service.schemas import InvoiceDraft, InvoiceListItem
from chat_service.db import schema as chat_schema
from chat_service.db.repositories import memory as knowledge_store
from chat_service.db.repositories.chat import DEFAULT_USER_ID, get_chat_thread, get_session_state, list_chat_messages
from chat_service.services.llm_metrics import record_llm_response


def _missing_fields(draft: InvoiceDraft) -> list[str]:
    missing = []
    for key, value in (
        ("invoice_number", draft.invoice_number),
        ("issue_date", draft.issue_date),
        ("currency", draft.currency),
        ("business.name", draft.business.name),
        ("client.name", draft.client.name),
    ):
        if value is None:
            missing.append(key)
    if not draft.items:
        missing.append("items")
    for index, item in enumerate(draft.items):
        if item.description is None:
            missing.append(f"items[{index}].description")
        if item.quantity is None:
            missing.append(f"items[{index}].quantity")
        if item.unit_price is None:
            missing.append(f"items[{index}].unit_price")
    return missing


async def _fake_completion(draft: InvoiceDraft, **kwargs) -> DocumentCompletion:
    missing = _missing_fields(draft)
    if missing:
        return DocumentCompletion(
            analysis=DocumentAnalysis(
                status="missing_fields",
                draft=draft,
                missing_fields=missing,
                fields_to_show=[],
            )
        )
    total = sum(item.quantity * item.unit_price for item in draft.items)
    return DocumentCompletion(
        created=DocumentCreated(
            document_id=42,
            invoice_number=draft.invoice_number,
            subtotal=total,
            total=total,
            currency=draft.currency,
            pdf_url="/generated/invoices/inv-010.pdf",
            download_url="/invoices/42/download",
        )
    )


class AiChatMemoryRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        import chat_service.db.connection as database

        database.DATABASE_PATH = Path(self.temp_dir.name) / "app.db"
        chat_schema._ready = False
        knowledge_store._ready = False
        chat_schema.ensure_chat_schema()
        knowledge_store.ensure_knowledge_schema()
        completion_patcher = patch(
            "chat_service.api.routes.ai_chat_memory.complete_invoice_draft",
            AsyncMock(side_effect=_fake_completion),
        )
        self.complete_mock = completion_patcher.start()
        self.addCleanup(completion_patcher.stop)

    async def test_chat_creates_thread_and_persists_messages(self) -> None:
        with (
            patch(
                "chat_service.api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="answer")),
            ),
            patch(
                "chat_service.api.routes.ai_chat_memory._answer_chat_message_with_memory",
                AsyncMock(return_value="Hi, how can I help?"),
            ),
            patch("chat_service.api.routes.ai_chat_memory._learn_from_turn", AsyncMock()),
        ):
            response = await chat(AiChatMemoryRequest(message="Hi"))

        self.assertEqual(response["status"], "answer")
        self.assertIn("chat_id", response)
        self.assertIsNotNone(get_chat_thread(response["chat_id"]))

        messages = list_chat_messages(response["chat_id"])
        self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
        self.assertEqual(messages[0]["content"], "Hi")

    async def test_streamed_invoice_reuses_decision_and_defers_learning(self) -> None:
        decision_mock = AsyncMock(return_value=ChatDecision(action="create_invoice"))
        learning_mock = AsyncMock()
        draft = InvoiceDraft.model_validate(
            {
                "invoice_number": "INV-STREAM-001",
                "client": {"name": "Beta LLC"},
            }
        )

        with (
            patch("chat_service.api.routes.ai_chat_memory._decide_chat_action", decision_mock),
            patch(
                "chat_service.api.routes.ai_chat_memory._extract_invoice_draft_for_chat",
                AsyncMock(return_value=draft),
            ),
            patch("chat_service.api.routes.ai_chat_memory._learn_from_turn", learning_mock),
        ):
            response = await chat_stream(
                AiChatMemoryRequest(message="Create invoice INV-STREAM-001 for Beta LLC")
            )
            chunks = [chunk async for chunk in response.body_iterator]

            body = "".join(
                chunk.decode() if isinstance(chunk, bytes) else chunk
                for chunk in chunks
            )
            self.assertIn('event: final', body)
            self.assertIn('"status": "missing_fields"', body)
            decision_mock.assert_awaited_once()
            learning_mock.assert_not_awaited()

            self.assertIsNotNone(response.background)
            await response.background()
            learning_mock.assert_awaited_once()

    async def test_stream_includes_request_diagnostics_and_persists_them(self) -> None:
        async def fake_stream(*_: object, **__: object):
            yield "Hello"
            record_llm_response(
                {
                    "model": "/models/Qwen2.5-3B-Instruct-Q4_K_M.gguf",
                    "tokens_evaluated": 12,
                    "tokens_predicted": 3,
                    "timings": {"predicted_ms": 1000},
                }
            )

        with (
            patch(
                "chat_service.api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="answer", context="none")),
            ),
            patch("chat_service.api.routes.ai_chat_memory.llm_client.stream_prompt", fake_stream),
            patch("chat_service.api.routes.ai_chat_memory.get_request_id", return_value="request-123"),
            patch("chat_service.api.routes.ai_chat_memory.get_trace_id", return_value="trace-456"),
            patch("chat_service.api.routes.ai_chat_memory._learn_from_turn", AsyncMock()),
        ):
            response = await chat_stream(AiChatMemoryRequest(message="Hi"))
            chunks = [chunk async for chunk in response.body_iterator]

        body = "".join(chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks)
        self.assertIn('event: start\ndata: {"chat_id":', body)
        self.assertIn('"request_id": "request-123"', body)
        self.assertIn('"trace_id": "trace-456"', body)
        self.assertIn('"total_tokens": 15', body)
        self.assertIn('"tokens_per_second": 3.0', body)

        chat_id = body.split('"chat_id": "', 1)[1].split('"', 1)[0]
        messages = list_chat_messages(chat_id)
        diagnostics = messages[-1]["metadata"]["diagnostics"]
        self.assertEqual(diagnostics["request_id"], "request-123")
        self.assertEqual(diagnostics["total_tokens"], 15)

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
                "chat_service.api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="create_invoice")),
            ),
            patch(
                "chat_service.api.routes.ai_chat_memory._extract_invoice_draft_for_chat",
                AsyncMock(return_value=first_draft),
            ),
            patch("chat_service.api.routes.ai_chat_memory._learn_from_turn", AsyncMock()),
        ):
            first_response = await chat(AiChatMemoryRequest(message="Create invoice INV-010 for Alex"))

        chat_id = first_response["chat_id"]
        self.assertEqual(first_response["status"], "missing_fields")
        self.assertEqual(get_session_state(chat_id)["draft"]["business"]["name"], "Sargis Studio")

        with (
            patch(
                "chat_service.api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="answer")),
            ),
            patch(
                "chat_service.api.routes.ai_chat_memory._extract_invoice_draft_for_chat",
                AsyncMock(return_value=second_draft),
            ),
            patch("chat_service.api.routes.ai_chat_memory._learn_from_turn", AsyncMock()),
        ):
            second_response = await chat(
                AiChatMemoryRequest(chat_id=chat_id, message="Issue date is 2026-07-04, USD, Design 300")
            )

        self.assertEqual(second_response["status"], "created")
        completed_draft = self.complete_mock.call_args.args[0]
        self.assertEqual(completed_draft.business.name, "Sargis Studio")
        self.assertEqual(completed_draft.client.name, "Alex")
        self.assertEqual(get_session_state(chat_id)["last_document_id"], 42)
        self.assertNotIn("draft", get_session_state(chat_id))

    async def test_non_invoice_message_does_not_call_invoice_endpoint(self) -> None:
        with (
            patch(
                "chat_service.api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="list_invoices")),
            ),
            patch(
                "chat_service.api.routes.ai_chat_memory._answer_chat_message_with_memory",
                AsyncMock(return_value="Sounds great. What details should we plan?"),
            ),
            patch("chat_service.api.routes.ai_chat_memory.list_invoices") as list_invoices_mock,
            patch("chat_service.api.routes.ai_chat_memory._learn_from_turn", AsyncMock()),
        ):
            response = await chat(AiChatMemoryRequest(message="Lets made a BBQ!"))

        self.assertEqual(response["status"], "answer")
        list_invoices_mock.assert_not_called()

    async def test_invoice_list_serializes_typed_grpc_results_for_chat_history(self) -> None:
        invoice = InvoiceListItem(
            id=17,
            invoice_number="INV-17",
            issue_date=date(2026, 8, 1),
            due_date=None,
            currency="USD",
            business_name="Doco",
            client_name="Customer",
            total=Decimal("11.00"),
            pdf_url="/generated/invoices/inv-17.pdf",
            created_at=datetime(2026, 8, 1, 9, 30),
        )
        with (
            patch(
                "chat_service.api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="list_invoices")),
            ),
            patch(
                "chat_service.api.routes.ai_chat_memory.list_invoices",
                AsyncMock(return_value=[invoice]),
            ),
            patch("chat_service.api.routes.ai_chat_memory._learn_from_turn", AsyncMock()),
        ):
            response = await chat(AiChatMemoryRequest(message="List my invoices"))

        self.assertEqual(response["invoices"][0]["invoice_number"], "INV-17")
        messages = list_chat_messages(response["chat_id"])
        self.assertEqual(
            messages[-1]["metadata"]["invoices"][0]["invoice_number"],
            "INV-17",
        )

    async def test_answer_prompt_uses_selected_memory_context(self) -> None:
        with patch(
            "chat_service.api.routes.ai_chat_memory.llm_client.complete_prompt",
            AsyncMock(side_effect=['{"context":"saved_memory"}', "Use USD for Alex."]),
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
        self.assertEqual(complete_mock.call_args_list[1].kwargs["max_tokens"], 1024)
        prompt = complete_mock.call_args_list[1].args[0]
        self.assertIn("Client Alex usually uses USD.", prompt)
        self.assertIn("You may reason internally", prompt)
        self.assertNotIn("Memory is handled by the backend", prompt)
        self.assertNotIn("Never say you do not have memory", prompt)

    def test_normal_answer_prompt_has_no_memory_wording_without_context(self) -> None:
        prompt = _answer_prompt_with_memory(
            message="name 5 flowers",
            session_state={},
            shared_memories=[],
            skill_memories=[],
            recent_messages=[],
        )

        self.assertEqual(prompt, "User: name 5 flowers\nAssistant:")

    async def test_greeting_uses_no_context_and_no_memory_text(self) -> None:
        with patch(
            "chat_service.api.routes.ai_chat_memory.llm_client.complete_prompt",
            AsyncMock(side_effect=['{"context":"none"}', "Hi, how can I help?"]),
        ) as complete_mock:
            answer = await _answer_chat_message_with_memory(
                message="Hi",
                session_state={},
                shared_memories=[{"content": "User asked me to remember number 42."}],
                suçÍô¶‰žËkºwµç]["metadata"]["retryable"])
        self.assertEqual(messages[0]["metadata"]["diagnostics"]["trace_id"], "trace-456")
        self.assertEqual(messages[0]["metadata"]["raw"]["events"][0]["type"], "start")
        self.assertEqual(messages[0]["metadata"]["raw"]["events"][1]["content"], "Partial")

    def test_rejects_error_for_unknown_chat(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            create_chat_error("missing", ChatErrorCreateRequest(message="Failed"))

        self.assertEqual(raised.exception.status_code, 404)
