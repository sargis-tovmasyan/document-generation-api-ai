from pathlib import Path
import sqlite3
import subprocess
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_SCRIPT = BACKEND_ROOT / "scripts" / "migrate-monolith-db.py"


def _create_legacy_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE invoices (id INTEGER PRIMARY KEY, invoice_number TEXT NOT NULL);
            CREATE TABLE invoice_items (
                id INTEGER PRIMARY KEY,
                invoice_id INTEGER NOT NULL,
                description TEXT NOT NULL
            );
            CREATE TABLE chat_threads (id TEXT PRIMARY KEY, user_id TEXT NOT NULL);
            CREATE TABLE chat_messages (
                id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                content TEXT NOT NULL
            );
            INSERT INTO invoices VALUES (17, 'INV-17');
            INSERT INTO invoice_items VALUES (1, 17, 'Service');
            INSERT INTO chat_threads VALUES ('chat-17', 'user-17');
            INSERT INTO chat_messages VALUES ('message-17', 'chat-17', 'Hello');
            """
        )


def test_migration_splits_owned_tables_idempotently_and_preserves_source(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "app.db"
    chat = tmp_path / "chat.db"
    documents = tmp_path / "documents.db"
    _create_legacy_database(legacy)
    source_before = legacy.read_bytes()

    command = [
        sys.executable,
        str(MIGRATION_SCRIPT),
        "--legacy",
        str(legacy),
        "--chat",
        str(chat),
        "--documents",
        str(documents),
    ]
    first = subprocess.run(command, capture_output=True, text=True, check=False)
    second = subprocess.run(command, capture_output=True, text=True, check=False)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert legacy.read_bytes() == source_before
    with sqlite3.connect(chat) as connection:
        assert connection.execute("SELECT * FROM chat_threads").fetchall() == [
            ("chat-17", "user-17")
        ]
        assert connection.execute("SELECT COUNT(*) FROM _service_migrations").fetchone()[0] == 1
    with sqlite3.connect(documents) as connection:
        assert connection.execute("SELECT * FROM invoices").fetchall() == [
            (17, "INV-17")
        ]
        assert connection.execute("SELECT COUNT(*) FROM _service_migrations").fetchone()[0] == 1
