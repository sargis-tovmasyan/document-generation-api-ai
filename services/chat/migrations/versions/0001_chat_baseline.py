"""Baseline Chat database schema."""

from alembic import op


revision = "0001_chat_baseline"
down_revision = None
branch_labels = None
depends_on = None


TABLES = (
    """CREATE TABLE IF NOT EXISTS chat_threads (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, business_profile_id TEXT,
        client_id TEXT, title TEXT, archived_at TEXT, deleted_at TEXT,
        pinned_at TEXT, ui_order INTEGER,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS chat_messages (
        id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, role TEXT NOT NULL,
        content TEXT NOT NULL, metadata_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS session_memories (
        id TEXT PRIMARY KEY, chat_id TEXT NOT NULL UNIQUE, state_json TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1, expires_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS user_ui_settings (
        user_id TEXT PRIMARY KEY, settings_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS shared_memories (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, business_profile_id TEXT,
        client_id TEXT, source_chat_id TEXT, memory_type TEXT NOT NULL,
        content TEXT NOT NULL, structured_json TEXT,
        confidence REAL NOT NULL DEFAULT 0.0,
        status TEXT NOT NULL DEFAULT 'active', expires_at TEXT, last_used_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS skill_memories (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, business_profile_id TEXT,
        client_id TEXT, source_chat_id TEXT, scope TEXT NOT NULL DEFAULT 'user',
        title TEXT NOT NULL, description TEXT NOT NULL, trigger_text TEXT NOT NULL,
        steps_json TEXT NOT NULL, required_fields_json TEXT,
        confidence REAL NOT NULL DEFAULT 0.0,
        status TEXT NOT NULL DEFAULT 'active', last_used_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS memory_events (
        id TEXT PRIMARY KEY, shared_memory_id TEXT, skill_id TEXT, chat_id TEXT,
        event_type TEXT NOT NULL, before_json TEXT, after_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
)


def upgrade() -> None:
    for statement in TABLES:
        op.execute(statement)


def downgrade() -> None:
    for table in (
        "memory_events", "skill_memories", "shared_memories", "user_ui_settings",
        "session_memories", "chat_messages", "chat_threads",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
