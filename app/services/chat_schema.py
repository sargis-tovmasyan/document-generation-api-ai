from app.database import database_connection

_ready = False


def ensure_chat_schema() -> None:
    global _ready
    if _ready:
        return
    with database_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_threads (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                business_profile_id TEXT,
                client_id TEXT,
                title TEXT,
                archived_at TEXT,
                deleted_at TEXT,
                pinned_at TEXT,
                ui_order INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    _ready = True
