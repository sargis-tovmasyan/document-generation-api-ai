from __future__ import annotations

import json
from typing import Any, Literal
from uuid import uuid4

from app.database import database_connection

DEFAULT_USER_ID = "default_user"
ChatStatus = Literal["active", "archived", "deleted"]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _row_to_thread(row: Any) -> dict[str, Any]:
    return dict(row)


def create_chat_thread(
    *,
    user_id: str = DEFAULT_USER_ID,
    business_profile_id: str | None = None,
    client_id: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    chat_id = new_id("chat")
    with database_connection() as connection:
        connection.execute(
            """
            INSERT INTO chat_threads (id, user_id, business_profile_id, client_id, title)
            VALUES (?, ?, ?, ?, ?)
            """,
            (chat_id, user_id, business_profile_id, client_id, title or "New chat"),
        )
        row = connection.execute(
            "SELECT * FROM chat_threads WHERE id = ?",
            (chat_id,),
        ).fetchone()
    return _row_to_thread(row)


def get_chat_thread(chat_id: str) -> dict[str, Any] | None:
    with database_connection() as connection:
        row = connection.execute(
            "SELECT * FROM chat_threads WHERE id = ? AND deleted_at IS NULL",
            (chat_id,),
        ).fetchone()
    return _row_to_thread(row) if row is not None else None


def ensure_chat_thread(
    *,
    chat_id: str | None,
    user_id: str = DEFAULT_USER_ID,
    business_profile_id: str | None = None,
    client_id: str | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    if chat_id:
        existing = get_chat_thread(chat_id)
        if existing is not None:
            return existing

    return create_chat_thread(
        user_id=user_id,
        business_profile_id=business_profile_id,
        client_id=client_id,
        title=title,
    )


def list_chat_threads(
    *,
    user_id: str = DEFAULT_USER_ID,
    status: ChatStatus = "active",
) -> list[dict[str, Any]]:
    where = ["user_id = ?"]
    params: list[Any] = [user_id]

    if status == "active":
        where.append("deleted_at IS NULL")
        where.append("archived_at IS NULL")
    elif status == "archived":
        where.append("deleted_at IS NULL")
        where.append("archived_at IS NOT NULL")
    elif status == "deleted":
        where.append("deleted_at IS NOT NULL")

    with database_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT *
            FROM chat_threads
            WHERE {' AND '.join(where)}
            ORDER BY pinned_at IS NULL, pinned_at DESC, COALESCE(ui_order, 999999), updated_at DESC
            """,
            params,
        ).fetchall()
    return [_row_to_thread(row) for row in rows]


def update_chat_thread(
    chat_id: str,
    *,
    title: str | None = None,
    archived: bool | None = None,
    pinned: bool | None = None,
    ui_order: int | None = None,
) -> dict[str, Any] | None:
    assignments = ["updated_at = CURRENT_TIMESTAMP"]
    params: list[Any] = []

    if title is not None:
        assignments.append("title = ?")
        params.append(title.strip() or "Untitled chat")
    if archived is not None:
        assignments.append("archived_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END")
        params.append(1 if archived else 0)
    if pinned is not None:
        assignments.append("pinned_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END")
        params.append(1 if pinned else 0)
    if ui_order is not None:
        assignments.append("ui_order = ?")
        params.append(ui_order)

    params.append(chat_id)

    with database_connection() as connection:
        connection.execute(
            f"UPDATE chat_threads SET {', '.join(assignments)} WHERE id = ? AND deleted_at IS NULL",
            params,
        )
        row = connection.execute("SELECT * FROM chat_threads WHERE id = ?", (chat_id,)).fetchone()
    return _row_to_thread(row) if row is not None else None


def soft_delete_chat_thread(chat_id: str) -> None:
    with database_connection() as connection:
        connection.execute(
            """
            UPDATE chat_threads
            SET deleted_at = CURRENT_TIMESTAMP, archived_at = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ? AND deleted_at IS NULL
            """,
            (chat_id,),
        )


def append_chat_message(
    *,
    chat_id: str,
    role: Literal["user", "assistant", "system", "tool"],
    content: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message_id = new_id("msg")
    with database_connection() as connection:
        connection.execute(
            """
            INSERT INTO chat_messages (id, chat_id, role, content, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (message_id, chat_id, role, content, json_dumps(metadata or {})),
        )
        connection.execute(
            "UPDATE chat_threads SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (chat_id,),
        )
        row = connection.execute(
            "SELECT * FROM chat_messages WHERE id = ?",
            (message_id,),
        ).fetchone()
    result = dict(row)
    result["metadata"] = json_loads(result.pop("metadata_json", None), {})
    return result


def list_chat_messages(chat_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    with database_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM chat_messages
            WHERE chat_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (chat_id, limit),
        ).fetchall()
    messages = []
    for row in reversed(rows):
        item = dict(row)
        item["metadata"] = json_loads(item.pop("metadata_json", None), {})
        messages.append(item)
    return messages


def get_session_state(chat_id: str) -> dict[str, Any]:
    with database_connection() as connection:
        row = connection.execute(
            "SELECT state_json FROM session_memories WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
    return json_loads(row["state_json"], {}) if row is not None else {}


def upsert_session_state(chat_id: str, state: dict[str, Any]) -> dict[str, Any]:
    memory_id = new_id("session")
    serialized = json_dumps(state)
    with database_connection() as connection:
        connection.execute(
            """
            INSERT INTO session_memories (id, chat_id, state_json)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                state_json = excluded.state_json,
                version = version + 1,
                updated_at = CURRENT_TIMESTAMP
            """,
            (memory_id, chat_id, serialized),
        )
    return state


def clear_document_scope(chat_id: str) -> dict[str, Any]:
    state = get_session_state(chat_id)
    state.pop("active_document_type", None)
    state.pop("current_intent", None)
    state.pop("draft", None)
    state.pop("missing_fields", None)
    return upsert_session_state(chat_id, state)


def get_user_ui_settings(user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
    with database_connection() as connection:
        row = connection.execute(
            "SELECT settings_json FROM user_ui_settings WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return json_loads(row["settings_json"], {}) if row is not None else {}


def update_user_ui_settings(user_id: str, settings: dict[str, Any]) -> dict[str, Any]:
    current = get_user_ui_settings(user_id)
    current.update(settings)
    serialized = json_dumps(current)
    with database_connection() as connection:
        connection.execute(
            """
            INSERT INTO user_ui_settings (user_id, settings_json)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                settings_json = excluded.settings_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, serialized),
        )
    return current
