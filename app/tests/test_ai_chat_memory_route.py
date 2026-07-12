import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.routes.ai_chat import ChatDecision
from app.routes.ai_chat_memory import (
    AiChatMemoryRequest,
    _answer_chat_message_with_memory,
    _answer_prompt_with_memory,
    _clean_memory_safe_answer,
    _select_answer_context,
    _stream_answer_with_memory,
    chat,
)
from app.schemas import InvoiceDraft
from app.services import chat_schema, knowledge_store
from app.services.chat_store import DEFAULT_USER_ID, get_chat_thread, get_session_state, list_chat_messages


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

    async def test_answer_prompt_uses_selected_memory_context(self) -> None:
        with patch(
            "app.routes.ai_chat_memory.llm_client.complete_prompt",
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
            "app.routes.ai_chat_memory.llm_client.complete_prompt",
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
            "app.routes.ai_chat_memory.llm_client.complete_prompt",
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
        with patch("app.routes.ai_chat_memory.llm_client.complete_prompt", AsyncMock()) as complete_mock:
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
                "app.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value='{"context":"none"}'),
            ) as complete_mock,
            patch("app.routes.ai_chat_memory.llm_client.stream_prompt", fake_stream),
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
            patch("app.routes.ai_chat_memory.llm_client.complete_prompt", AsyncMock(return_value='{"context":"none"}')),
            patch("app.routes.ai_chat_memory.llm_client.stream_prompt", fake_stream),
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

    async def test_stream_answer_removes_thinking_before_tokens(self) -> None:
        async def fake_stream(*_: object, **__: object):
            for chunk in ["<think>hidden", " reasoning</think>", "Hi", "!"]:
                yield chunk

        with (
            patch("app.routes.ai_chat_memory.llm_client.complete_prompt", AsyncMock(return_value='{"context":"none"}')),
            patch("app.routes.ai_chat_memory.llm_client.stream_prompt", fake_stream),
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

    async def test_stream_answer_hides_memory_disclaimer_chunks_for_normal_question(self) -> None:
        async def fake_stream(*_: object, **__: object):
            for chunk in ["I don't have access", " to memory. ", "There are two r letters."]:
                yield chunk

        with (
            patch("app.routes.ai_chat_memory.llm_client.complete_prompt", AsyncMock(return_value='{"context":"none"}')),
            patch("app.routes.ai_chat_memory.llm_client.stream_prompt", fake_stream),
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
            patch("app.routes.ai_chat_memory.llm_client.complete_prompt", AsyncMock(return_value='{"context":"none"}')),
            patch("app.routes.ai_chat_memory.llm_client.stream_prompt", fake_stream),
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
        self.assertEqual(knowledge_store.list_shared_memories(user_id=DEFAULT_USER_ID), [])

        with (
            patch(
                "app.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="recall_memory")),
            ),
            patch(
                "app.routes.ai_chat_memory.llm_client.complete_prompt",
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
                "app.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="remember_memory")),
            ),
            patch(
                "app.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value='{"has_memory":true,"memory":"number 1234"}'),
            ),
        ):
            await chat(AiChatMemoryRequest(message="remember number 1234"))

        with (
            patch(
                "app.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="recall_memory")),
            ),
            patch("app.routes.ai_chat_memory.llm_client.complete_prompt", AsyncMock()) as complete_mock,
        ):
            recall_response = await chat(AiChatMemoryRequest(message="what number did I ask you to remember?"))

        self.assertEqual(recall_response["status"], "answer")
        self.assertIn("do not have", recall_response["message"].lower())
        complete_mock.assert_not_called()

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
        with (
            patch(
                "app.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="recall_memory")),
            ),
            patch(
                "app.routes.ai_chat_memory.llm_client.complete_prompt",
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
                "app.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="remember_memory")),
            ),
            patch(
                "app.routes.ai_chat_memory.llm_client.complete_prompt",
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
                "app.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="recall_memory")),
            ),
            patch(
                "app.routes.ai_chat_memory.llm_client.complete_prompt",
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
                "app.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="answer")),
            ),
            patch(
                "app.routes.ai_chat_memory._answer_chat_message_with_memory",
                AsyncMock(return_value="1. Rose, 2. Sunflower, 3. Tulip, 4. Daisy, 5. Lily."),
            ),
            patch("app.routes.ai_chat_memory._learn_from_turn", AsyncMock()),
        ):
            first_response = await chat(AiChatMemoryRequest(message="Name me 5 flowers."))

        with (
            patch(
                "app.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="remember_memory", context="recent_chat")),
            ),
            patch(
                "app.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value="The 3rd flower was Tulip."),
            ),
            patch("app.routes.ai_chat_memory._learn_from_turn", AsyncMock()),
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
                "app.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="remember_memory")),
            ),
            patch(
                "app.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value='{"has_memory":true,"memory":"number: 58 Color: Blue"}'),
            ),
        ):
            response = await chat(AiChatMemoryRequest(message="please remember this two data number: 58 Color: Blue"))

        self.assertEqual(response["status"], "answer")
        self.assertEqual(response["message"], "Got it. I will remember that.")

    async def test_remembers_and_recalls_color(self) -> None:
        with (
            patch(
                "app.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="remember_memory")),
            ),
            patch(
                "app.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value='{"has_memory":true,"memory":"color Blue"}'),
            ),
        ):
            remember_response = await chat(AiChatMemoryRequest(message="remember color Blue"))

        with (
            patch(
                "app.routes.ai_chat_memory._decide_chat_action",
                AsyncMock(return_value=ChatDecision(action="recall_memory")),
            ),
            patch(
                "app.routes.ai_chat_memory.llm_client.complete_prompt",
                AsyncMock(return_value="The color you asked me to remember is Blue."),
            ),
        ):
            recall_response = await chat(
                AiChatMemoryRequest(chat_id=remember_response["chat_id"], message="what color did I ask you to remember?")
            )

        self.assertIn("Blue", recall_response["message"])


if __name__ == "__main__":
    unittest.main()
