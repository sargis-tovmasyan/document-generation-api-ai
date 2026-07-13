import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from app.routes.chat_threads import ChatErrorCreateRequest, create_chat_error
from app.services import chat_schema
from app.services.chat_store import create_chat_thread, list_chat_messages


class ChatThreadsRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        import app.database as database

        database.DATABASE_PATH = Path(self.temp_dir.name) / "app.db"
        chat_schema._ready = False
        chat_schema.ensure_chat_schema()

    def test_persists_client_error_in_chat_history(self) -> None:
        chat_id = create_chat_thread(title="Failed chat")["id"]

        created = create_chat_error(
            chat_id,
            ChatErrorCreateRequest(message="Stream ended without a final response.", retryable=True),
        )

        self.assertEqual(created["role"], "assistant")
        messages = list_chat_messages(chat_id)
        self.assertEqual(messages[0]["content"], "Stream ended without a final response.")
        self.assertEqual(messages[0]["metadata"]["status"], "error")
        self.assertTrue(messages[0]["metadata"]["retryable"])

    def test_rejects_error_for_unknown_chat(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            create_chat_error("missing", ChatErrorCreateRequest(message="Failed"))

        self.assertEqual(raised.exception.status_code, 404)
