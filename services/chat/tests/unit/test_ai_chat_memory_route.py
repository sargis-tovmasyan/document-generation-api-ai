import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

from api.routes.ai_chat import ChatDecision
from clients.document import (
    DocumentAnalysis,
    DocumentCompletion,
    DocumentCreated,
)
from api.routes.ai_chat_memory import (
    AiChatMemoryRequest,
    _answer_chat_message_with_memory,
    _answer_prompt_with_memory,
    _clean_memory_safe_answer,
    _select_answer_context,
    _stream_answer_with_memory,
    chat,
    chat_stream,
)
from schemas import InvoiceDraft, InvoiceListItem
from db import schema as chat_schema
from db.repositories import memory as knowledge_store
from db.repositories.chat import DEFAULT_USER_ID, get_chat_thread, get_session_state, list_chat_messages
from services.llm_metrics import record_llm_response


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

        import db.connection as database

        database.DATABASE_PATH = Path(self.temp_dir.name) / "app.db"
        chat_schema._ready = False
        knowledge_store._ready = False
        chat_schema.ensure_chat_schema()
        knowledge_store.ensure_knowledge_schema()
        completion_patcher = patch(
            "api.routes.ai_chat_memory.complete_invoice_draft",
            AsyncMock(side_effect=_fake_completion),
        )
        self.complete_mock = completion_patcher.start()
        self.addCleanup(completion_patcher.stop)

    async def test_chat_creates_thread_and_persists_messages(self) -> None:
        with (
            patch(
                "api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="answer")),
            ),
            patch(
                "api.routes.ai_chat_memory._answer_chat_message_with_memory",
                AsyncMock(return_value="Hi, how can I help?"),
            ),
            patch("api.routes.ai_chat_memory._learn_from_turn", AsyncMock()),
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
            patch("api.routes.ai_chat_memory._decide_chat_action", decision_mock),
            patch(
                "api.routes.ai_chat_memory._extract_invoice_draft_for_chat",
                AsyncMock(return_value=draft),
            ),
            patch("api.routes.ai_chat_memory._learn_from_turn", learning_mock),
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
                "api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="answer", context="none")),
            ),
            patch("api.routes.ai_chat_memory.llm_client.stream_prompt", fake_stream),
            patch("api.routes.ai_chat_memory.get_request_id", return_value="request-123"),
            patch("api.routes.ai_chat_memory.get_trace_id", return_value="trace-456"),
            patch("api.routes.ai_chat_memory._learn_from_turn", AsyncMock()),
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
                "api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="create_invoice")),
            ),
            patch(
                "api.routes.ai_chat_memory._extract_invoice_draft_for_chat",
                AsyncMock(return_value=first_draft),
            ),
            patch("api.routes.ai_chat_memory._learn_from_turn", AsyncMock()),
        ):
            first_response = await chat(AiChatMemoryRequest(message="Create invoice INV-010 for Alex"))

        chat_id = first_response["chat_id"]
        self.assertEqual(first_response["status"], "missing_fields")
        self.assertEqual(get_session_state(chat_id)["draft"]["business"]["name"], "Sargis Studio")

        with (
            patch(
                "api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="answer")),
            ),
            patch(
                "api.routes.ai_chat_memory._extract_invoice_draft_for_chat",
                AsyncMock(return_value=second_draft),
            ),
            patch("api.routes.ai_chat_memory._learn_from_turn", AsyncMock()),
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
                "api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="list_invoices")),
            ),
            patch(
                "api.routes.ai_chat_memory._answer_chat_message_with_memory",
                AsyncMock(return_value="Sounds great. What details should we plan?"),
            ),
            patch("api.routes.ai_chat_memory.list_invoices") as list_invoices_mock,
            patch("api.routes.ai_chat_memory._learn_from_turn", AsyncMock()),
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
                "api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="list_invoices")),
            ),
            patch(
                "api.routes.ai_chat_memory.list_invoices",
                AsyncMock(return_value=[invoice]),
            ),
            patch("api.routes.ai_chat_memory._learn_from_turn", AsyncMock()),
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
            "api.routes.ai_chat_memory.llm_client.complete_prompt",
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
            "api.routes.ai_chat_memory.llm_client.complete_prompt",
            AsyncMock(side_effect=['{"context":"none"}', "Hi, how can I help?"]),
        ) as complete_mock:
            answer = await _answer_chat_message_with_memory(
                message="Hi",
                session_state={},
                shared_memories=[{"content": "User asked me to remember number 42."}],
                skill_memories=[],
                recent_messages=[{"role": "assistant", "content": "Previous answer."}],
            )

        self.assertEqual(answer, "Hi, how can I help?")
        prompt = complete_mock.call_args_list[1].args[0]
        self.assertNotIn("User asked me to remember number 42", prompt)
        self.assertNotIn("memory", answer.lower())

    async def test_flower_request_uses_no_context_and_no_memory_text(self) -> None:
        with patch(
            "api.routes.ai_chat_memory.llm_client.complete_prompt",
            AsyncMock(side_effect=['{"context":"none"}', "1. Rose, 2. Sunflower, 3. Tulip, 4. Daisy, 5. Lily."]),
        ) as complete_mock:
            answer = await _answer_chat_message_with_memory(
                message="name 5 flowers! and give them a number like 1. something, 2. something ...",
                session_state={},
                shared_memories=[{"content": "User asked me to remember color Blue."}],
                skill_memories=[],
                recent_messages=[{"role": "assistant", "content": "The number is 9876."}],
            )

        self.assertIn("1. Rose", answer)
        self.assertNotIn("memory", answer.lower())
        prompt = complete_mock.call_args_list[1].args[0]
        self.assertNotIn("User asked me to remember color Blue", prompt)

    async def test_context_selection_skips_llm_when_no_context_exists(self) -> None:
        with patch("api.routes.ai_chat_memory.llm_client.complete_prompt", AsyncMock()) as complete_mock:
            context = await _select_answer_context(
                message="name 5 flowers",
                recent_messages=[],
                shared_memories=[],
                skill_memories=[],
            )

        self.assertEqual(context, "none")
        complete_mock.assert_not_called()

    def test_normal_answer_removes_memory_disclaimer(self) -> None:
        answer = _clean_memory_safe_answer(
            'How many r in "raspberry"?',
            "I don't have access to memory. I can count letters in your message.",
        )

        self.assertNotIn("memory", answer.lower())
        self.assertEqual(answer, "I can count letters in your message.")

    async def test_answer_prompt_keeps_thinking_instruction_for_streaming(self) -> None:
        seen_max_tokens: int | None = None

        async def fake_stream(prompt: str, *_: object, **kwargs: object):
            nonlocal seen_max_tokens
            self.assertIn("You may reason internally", prompt)
            seen_max_tokens = int(kwargs["max_tokens"])
            yield "There are two r letters."

        with (
            patch(
                "api.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value='{"context":"none"}'),
            ) as complete_mock,
            patch("api.routes.ai_chat_memory.llm_client.stream_prompt", fake_stream),
        ):
            chunks = [
                chunk
                async for chunk in _stream_answer_with_memory(
                    message="Give me a short professional greeting.",
                    session_state={},
                    shared_memories=[],
                    skill_memories=[],
                    recent_messages=[],
                    thinking_enabled=True,
                    temperature_preset="low",
                    selected_context="none",
                )
            ]

        self.assertEqual("".join(chunks), "There are two r letters.")
        self.assertEqual(seen_max_tokens, 1024)
        complete_mock.assert_not_awaited()

    async def test_stream_answer_handles_letter_count_without_memory_disclaimer(self) -> None:
        async def fake_stream(*_: object, **__: object):
            yield 'There are 3 "r" letters in "raspberry".'

        with (
            patch("api.routes.ai_chat_memory.llm_client.complete_prompt", AsyncMock(return_value='{"context":"none"}')),
            patch("api.routes.ai_chat_memory.llm_client.stream_prompt", fake_stream),
        ):
            chunks = [
                chunk
                async for chunk in _stream_answer_with_memory(
                    message='How many r in "raspberry"?',
                    session_state={},
                    shared_memories=[],
                    skill_memories=[],
                    recent_messages=[],
                    thinking_enabled=False,
                    temperature_preset="low",
                )
            ]

        answer = "".join(chunks)
        self.assertNotIn("memory", answer.lower())
        self.assertEqual(answer, 'There are 3 "r" letters in "raspberry".')

    async def test_answer_removes_memory_context_leak(self) -> None:
        with patch(
            "api.routes.ai_chat_memory.llm_client.complete_prompt",
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

    async def test_stream_answer_removes_thinking_before_tokens(self) -> None:
        async def fake_stream(*_: object, **__: object):
            for chunk in ["<think>hidden", " reasoning</think>", "Hi", "!"]:
                yield chunk

        with (
            patch("api.routes.ai_chat_memory.llm_client.complete_prompt", AsyncMock(return_value='{"context":"none"}')),
            patch("api.routes.ai_chat_memory.llm_client.stream_prompt", fake_stream),
        ):
            chunks = [
                chunk
                async for chunk in _stream_answer_with_memory(
                    message="Hi",
                    session_state={},
                    shared_memories=[],
                    skill_memories=[],
                    recent_messages=[],
                    thinking_enabled=True,
                    temperature_preset="low",
                )
            ]

        self.assertEqual("".join(chunks), "Hi!")

    async def test_stream_answer_preserves_cpp_header_and_complete_code_block(self) -> None:
        async def fake_stream(*_: object, **__: object):
            for chunk in [
                "```cpp\n#include <iostream",
                ">\n\nint main() {\n",
                '    std::cout << "Hello, World!" << std::endl;\n',
                "    return 0;\n}\n```",
            ]:
                yield chunk

        with patch("api.routes.ai_chat_memory.llm_client.stream_prompt", fake_stream):
            chunks = [
                chunk
                async for chunk in _stream_answer_with_memory(
                    message="Format this C++ code in a code block.",
                    session_state={},
                    shared_memories=[],
                    skill_memories=[],
                    recent_messages=[],
                    thinking_enabled=False,
                    temperature_preset="low",
                    selected_context="none",
                )
            ]

        self.assertEqual(
            "".join(chunks),
            "```cpp\n#include <iostream>\n\nint main() {\n"
            '    std::cout << "Hello, World!" << std::endl;\n'
            "    return 0;\n}\n```",
        )

    async def test_stream_answer_hides_memory_disclaimer_chunks_for_normal_question(self) -> None:
        async def fake_stream(*_: object, **__: object):
            for chunk in ["I don't have access", " to memory. ", "There are two r letters."]:
                yield chunk

        with (
            patch("api.routes.ai_chat_memory.llm_client.complete_prompt", AsyncMock(return_value='{"context":"none"}')),
            patch("api.routes.ai_chat_memory.llm_client.stream_prompt", fake_stream),
        ):
            chunks = [
                chunk
                async for chunk in _stream_answer_with_memory(
                    message="What is a polite invoice reminder?",
                    session_state={},
                    shared_memories=[],
                    skill_memories=[],
                    recent_messages=[],
                    thinking_enabled=False,
                    temperature_preset="low",
                )
            ]

        answer = "".join(chunks)
        self.assertNotIn("memory", answer.lower())
        self.assertEqual(answer, "There are two r letters.")

    async def test_stream_answer_hides_incomplete_disclaimer_prefix(self) -> None:
        async def fake_stream(*_: object, **__: object):
            for chunk in ["I", " don", "'t"]:
                yield chunk

        with (
            patch("api.routes.ai_chat_memory.llm_client.complete_prompt", AsyncMock(return_value='{"context":"none"}')),
            patch("api.routes.ai_chat_memory.llm_client.stream_prompt", fake_stream),
        ):
            chunks = [
                chunk
                async for chunk in _stream_answer_with_memory(
                    message="What is a polite invoice reminder?",
                    session_state={},
                    shared_memories=[],
                    skill_memories=[],
                    recent_messages=[],
                    thinking_enabled=False,
                    temperature_preset="low",
                )
            ]

        answer = "".join(chunks)
        self.assertFalse(answer.startswith("I don't"))
        self.assertEqual(answer, "I can help with that.")

    async def test_memory_request_without_value_asks_for_value(self) -> None:
        with (
            patch(
                "api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="remember_memory")),
            ),
            patch(
                "api.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value='{"has_memory":false,"memory":""}'),
            ),
        ):
            response = await chat(AiChatMemoryRequest(message="can you memorize a number and remind me later?"))

        self.assertEqual(response["status"], "answer")
        self.assertIn("send me the number", response["message"])

    async def test_remembers_and_recalls_number(self) -> None:
        with (
            patch(
                "api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="remember_memory")),
            ),
            patch(
                "api.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value='{"has_memory":true,"memory":"number 1234"}'),
            ),
        ):
            remember_response = await chat(AiChatMemoryRequest(message="remember number 1234"))

        chat_id = remember_response["chat_id"]
        self.assertEqual(remember_response["status"], "answer")
        self.assertIn("remember", remember_response["message"].lower())
        self.assertEqual(knowledge_store.list_shared_memories(user_id=DEFAULT_USER_ID), [])

        with (
            patch(
                "api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="recall_memory")),
            ),
            patch(
                "api.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value="The number you asked me to remember is 1234."),
            ) as complete_mock,
        ):
            recall_response = await chat(
                AiChatMemoryRequest(chat_id=chat_id, message="what number did I ask you to remember?")
            )

        self.assertEqual(recall_response["status"], "answer")
        self.assertIn("1234", recall_response["message"])
        self.assertEqual(complete_mock.await_args.kwargs["max_tokens"], 32)
        self.assertIn("\n", complete_mock.await_args.kwargs["stop"])

        messages = list_chat_messages(chat_id)
        self.assertEqual([message["role"] for message in messages], ["user", "assistant", "user", "assistant"])

    async def test_requested_memory_does_not_leak_to_other_chats(self) -> None:
        with (
            patch(
                "api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="remember_memory")),
            ),
            patch(
                "api.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value='{"has_memory":true,"memory":"number 1234"}'),
            ),
        ):
            await chat(AiChatMemoryRequest(message="remember number 1234"))

        with (
            patch(
                "api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="recall_memory")),
            ),
            patch("api.routes.ai_chat_memory.llm_client.complete_prompt", AsyncMock()) as complete_mock,
        ):
            recall_response = await chat(AiChatMemoryRequest(message="what number did I ask you to remember?"))

        self.assertEqual(recall_response["status"], "answer")
        self.assertIn("do not have", recall_response["message"].lower())
        complete_mock.assert_not_called()

    async def test_remembers_number_when_extractor_misses_explicit_value(self) -> None:
        with (
            patch(
                "api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="remember_memory")),
            ),
            patch(
                "api.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value='{"has_memory":false,"memory":""}'),
            ),
        ):
            remember_response = await chat(AiChatMemoryRequest(message="remember number 1234"))

        chat_id = remember_response["chat_id"]
        with (
            patch(
                "api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="recall_memory")),
            ),
            patch(
                "api.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value="The number you asked me to remember is 1234."),
            ),
        ):
            recall_response = await chat(
                AiChatMemoryRequest(chat_id=chat_id, message="what number did I ask you to remember?")
            )

        self.assertIn("1234", recall_response["message"])

    async def test_pending_number_request_saves_follow_up_value(self) -> None:
        with (
            patch(
                "api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="remember_memory")),
            ),
            patch(
                "api.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value='{"has_memory":true,"memory":"for me the number and remind me when I ask"}'),
            ),
        ):
            first_response = await chat(
                AiChatMemoryRequest(message="can you remember for me the number and remind me when I ask?")
            )

        chat_id = first_response["chat_id"]
        self.assertIn("send me the number", first_response["message"])

        second_response = await chat(AiChatMemoryRequest(chat_id=chat_id, message="the number is 42"))

        self.assertEqual(second_response["message"], "Got it. I will remember that.")

        with (
            patch(
                "api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="recall_memory")),
            ),
            patch(
                "api.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value="The number you asked me to remember is 42."),
            ),
        ):
            recall_response = await chat(
                AiChatMemoryRequest(chat_id=chat_id, message="whats the number I asked to remember?")
            )

        self.assertIn("42", recall_response["message"])

    async def test_follow_up_ordinal_question_uses_recent_assistant_list(self) -> None:
        with (
            patch(
                "api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="answer")),
            ),
            patch(
                "api.routes.ai_chat_memory._answer_chat_message_with_memory",
                AsyncMock(return_value="1. Rose, 2. Sunflower, 3. Tulip, 4. Daisy, 5. Lily."),
            ),
            patch("api.routes.ai_chat_memory._learn_from_turn", AsyncMock()),
        ):
            first_response = await chat(AiChatMemoryRequest(message="Name me 5 flowers."))

        with (
            patch(
                "api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="remember_memory", context="recent_chat")),
            ),
            patch(
                "api.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value="The 3rd flower was Tulip."),
            ),
            patch("api.routes.ai_chat_memory._learn_from_turn", AsyncMock()),
        ):
            second_response = await chat(
                AiChatMemoryRequest(chat_id=first_response["chat_id"], message="what is the 3th flower you named?")
            )

        self.assertEqual(second_response["status"], "answer")
        self.assertIn("Tulip", second_response["message"])
        self.assertNotIn("remember", second_response["message"].lower())

    async def test_remembers_number_with_colon_value(self) -> None:
        with (
            patch(
                "api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="remember_memory")),
            ),
            patch(
                "api.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value='{"has_memory":true,"memory":"number: 58 Color: Blue"}'),
            ),
        ):
            response = await chat(AiChatMemoryRequest(message="please remember this two data number: 58 Color: Blue"))

        self.assertEqual(response["status"], "answer")
        self.assertEqual(response["message"], "Got it. I will remember that.")

    async def test_remembers_and_recalls_color(self) -> None:
        with (
            patch(
                "api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="remember_memory")),
            ),
            patch(
                "api.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value='{"has_memory":true,"memory":"color Blue"}'),
            ),
        ):
            remember_response = await chat(AiChatMemoryRequest(message="remember color Blue"))

        with (
            patch(
                "api.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="recall_memory")),
            ),
            patch(
                "api.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value="The color you asked me to remember is Blue."),
            ),
        ):
            recall_response = await chat(
                AiChatMemoryRequest(chat_id=remember_response["chat_id"], message="what color did I ask you to remember?")
            )

        self.assertIn("Blue", recall_response["message"])


if __name__ == "__main__":
    unittest.main()
