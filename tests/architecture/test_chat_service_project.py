import os
from pathlib import Path
import subprocess
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[2]
CHAT_ROOT = BACKEND_ROOT / "chat-service"


def test_chat_service_initializes_only_chat_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "chat.db"
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": os.pathsep.join(
                [str(CHAT_ROOT), str(BACKEND_ROOT / "contracts" / "python")]
            ),
            "CHAT_DATABASE_PATH": str(database_path),
        }
    )
    program = """
import sqlite3
from fastapi.testclient import TestClient
from app.main import app

with TestClient(app):
    pass
with sqlite3.connect(r'%s') as connection:
    tables = [row[0] for row in connection.execute(
        \"SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%%' ORDER BY name\"
    )]
print(app.title)
print(','.join(tables))
""" % database_path

    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=BACKEND_ROOT / "tests",
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "Chat Service",
        (
            "chat_messages,chat_threads,memory_events,session_memories,"
            "shared_memories,skill_memories,user_ui_settings"
        ),
    ]
