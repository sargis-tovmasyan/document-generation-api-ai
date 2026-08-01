import tempfile
import unittest
from pathlib import Path

from app.services import chat_schema
from app.services.chat_store import (
    append_chat_message,
    create_chat_thread,
    list_chat_messages,
    merge_latest_assistant_metadata,
)


class ChatStoreMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        import app.database as database

        database.DATABASE_PATH = Path(self.temp_dir.name) / "app.db"
        chat_schema._ready = False
        chat_schema.ensure_chat_schema()

    def test_merges_diagnostics_into_latest_assistant_message(self) -> None:
        chat_id = create_chat_thread(title="Diagnostics")["id"]
        append_chat_message(chat_id=chat_id, role="user", content="Hi")
        append_chat_message(
            chat_id=chat_id,
            role="assistant",
            content="Hello",
            metadata={"status": "answer", "message": "Hello"},
        )

        merge_latest_assistant_metadata(
            chat_id,
            {"diagnostics": {"trace_id": "trace-123", "total_tokens": 42}},
        )

        messages = list_chat_messages(chat_id)
        self.assertEqual(messages[-1]["metadata"]["status"], "answer")
        self.assertEqual(messages[-1]["metadata"]["diagnostics"]["trace_id"], "trace-123")
        self.assertEqual(messages[-1]["metadata"]["diagnostics"]["total_tokens"], 42)

    def test_does_not_modify_an_assistant_message_from_an_earlier_turn(self) -> None:
        chat_id = create_chat_thread(title="Diagnostics")["id"]
        append_chat_message(
            chat_id=chat_id,
            role="assistant",
            content="Earlier answer",
            metadata={"status": "answer"},
        )
        append_chat_message(chat_id=chat_id, role="user", content="New request")

        updated = merge_latest_assistant_metadata(
            chat_id,
            {"diagnostics": {"trace_id": "wrong-turn"}},
        )

        self.assertIsNone(updated)
        self.assertNotIn("diagnostics", list_chat_messages(chat_id)[0]["metadata"])


if __name__ == "__main__":
    unittest.main()
