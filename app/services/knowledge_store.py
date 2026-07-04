from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from app.database import database_connection

_ready = False


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def ensure_knowledge_schema() -> None:
    global _ready
    if _ready:
        return
    with database_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS shared_memories (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                business_profile_id TEXT,
                client_id TEXT,
                source_chat_id TEXT,
                memory_type TEXT NOT NULL,
                content TEXT NOT NULL,
                structured_json TEXT,
                confidence REAL NOT NULL DEFAULT 0.0,
                status TEXT NOT NULL DEFAULT 'active',
                expires_at TEXT,
                last_used_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS skill_memories (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                business_profile_id TEXT,
                client_id TEXT,
                source_chat_id TEXT,
                scope TEXT NOT NULL DEFAULT 'user',
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                trigger_text TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                required_fields_json TEXT,
                confidence REAL NOT NULL DEFAULT 0.0,
                status TEXT NOT NULL DEFAULT 'active',
                last_used_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_events (
                id TEXT PRIMARY KEY,
                shared_memory_id TEXT,
                skill_id TEXT,
                chat_id TEXT,
                event_type TEXT NOT NULL,
                before_json TEXT,
                after_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    _ready = True


def save_fact(
    *,
    user_id: str,
    source_chat_id: str,
    fact_type: str,
    content: str,
    structured: dict[str, Any] | None,
    confidence: float,
    business_profile_id: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    ensure_knowledge_schema()
    item_id = _id("fact")
    status = "active" if confidence >= 0.75 else "needs_review"
    with database_connection() as connection:
        connection.execute(
            """
            INSERT INTO shared_memories (
                id, user_id, business_profile_id, client_id, source_chat_id,
                memory_type, content, structured_json, confidence, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                user_id,
                business_profile_id,
                client_id,
                source_chat_id,
                fact_type,
                content,
                _json(structured or {}),
                confidence,
                status,
            ),
        )
        row = connection.execute("SELECT * FROM shared_memories WHERE id = ?", (item_id,)).fetchone()
        connection.execute(
            """
            INSERT INTO memory_events (id, shared_memory_id, chat_id, event_type, after_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (_id("event"), item_id, source_chat_id, "created", _json(dict(row))),
        )
    return dict(row)


def save_skill(
    *,
    user_id: str,
    source_chat_id: str,
    title: str,
    description: str,
    trigger_text: str,
    steps: list[str],
    required_fields: list[str] | None,
    confidence: float,
    scope: str = "user",
    business_profile_id: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    ensure_knowledge_schema()
    item_id = _id("skill")
    allowed_scope = scope if scope in {"user", "business", "client"} else "user"
    status = "active" if confidence >= 0.75 else "needs_review"
    with database_connection() as connection:
        connection.execute(
            """
            INSERT INTO skill_memories (
                id, user_id, business_profile_id, client_id, source_chat_id, scope, title, description,
                trigger_text, steps_json, required_fields_json, confidence, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                user_id,
                business_profile_id,
                client_id,
                source_chat_id,
                allowed_scope,
                title,
                description,
                trigger_text,
                _json(steps),
                _json(required_fields or []),
                confidence,
                status,
            ),
        )
        row = connection.execute("SELECT * FROM skill_memories WHERE id = ?", (item_id,)).fetchone()
        connection.execute(
            """
            INSERT INTO memory_events (id, skill_id, chat_id, event_type, after_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (_id("event"), item_id, source_chat_id, "created", _json(dict(row))),
        )
    return dict(row)


def list_shared_memories(
    *,
    user_id: str,
    business_profile_id: str | None = None,
    client_id: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    ensure_knowledge_schema()
    with database_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM shared_memories
            WHERE user_id = ?
              AND status = 'active'
              AND (client_id IS NULL OR client_id = ?)
              AND (business_profile_id IS NULL OR business_profile_id = ?)
            ORDER BY
              CASE WHEN client_id = ? THEN 0 ELSE 1 END,
              CASE WHEN business_profile_id = ? THEN 0 ELSE 1 END,
              confidence DESC,
              updated_at DESC
            LIMIT ?
            """,
            (
                user_id,
                client_id,
                business_profile_id,
                client_id,
                business_profile_id,
                limit,
            ),
        ).fetchall()
        for row in rows:
            connection.execute(
                "UPDATE shared_memories SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
                (row["id"],),
            )
    return [dict(row) for row in rows]


def list_skill_memories(
    *,
    user_id: str,
    business_profile_id: str | None = None,
    client_id: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    ensure_knowledge_schema()
    with database_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM skill_memories
            WHERE user_id = ?
              AND status = 'active'
              AND (client_id IS NULL OR client_id = ?)
              AND (business_profile_id IS NULL OR business_profile_id = ?)
            ORDER BY
              CASE WHEN client_id = ? THEN 0 ELSE 1 END,
              CASE WHEN business_profile_id = ? THEN 0 ELSE 1 END,
              confidence DESC,
              updated_at DESC
            LIMIT ?
            """,
            (
                user_id,
                client_id,
                business_profile_id,
                client_id,
                business_profile_id,
                limit,
            ),
        ).fetchall()
        for row in rows:
            connection.execute(
                "UPDATE skill_memories SET last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
                (row["id"],),
            )
    return [dict(row) for row in rows]
