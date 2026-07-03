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
            CREATE TABLE IF NOT EXISTS long_term_facts (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                business_profile_id TEXT,
                client_id TEXT,
                source_chat_id TEXT,
                fact_type TEXT NOT NULL,
                content TEXT NOT NULL,
                structured_json TEXT,
                confidence REAL NOT NULL DEFAULT 0.0,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_skills (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                owner_user_id TEXT,
                tenant_id TEXT,
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
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
) -> dict[str, Any]:
    ensure_knowledge_schema()
    item_id = _id("fact")
    status = "active" if confidence >= 0.75 else "needs_review"
    with database_connection() as connection:
        connection.execute(
            """
            INSERT INTO long_term_facts (
                id, user_id, source_chat_id, fact_type, content, structured_json, confidence, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (item_id, user_id, source_chat_id, fact_type, content, _json(structured or {}), confidence, status),
        )
        row = connection.execute("SELECT * FROM long_term_facts WHERE id = ?", (item_id,)).fetchone()
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
) -> dict[str, Any]:
    ensure_knowledge_schema()
    item_id = _id("skill")
    allowed_scope = scope if scope in {"user", "business"} else "user"
    status = "active" if confidence >= 0.75 else "needs_review"
    with database_connection() as connection:
        connection.execute(
            """
            INSERT INTO workflow_skills (
                id, user_id, owner_user_id, source_chat_id, scope, title, description,
                trigger_text, steps_json, required_fields_json, confidence, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (item_id, user_id, user_id, source_chat_id, allowed_scope, title, description, trigger_text, _json(steps), _json(required_fields or []), confidence, status),
        )
        row = connection.execute("SELECT * FROM workflow_skills WHERE id = ?", (item_id,)).fetchone()
    return dict(row)
