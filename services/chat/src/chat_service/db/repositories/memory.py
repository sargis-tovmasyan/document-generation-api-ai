from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from chat_service.db.connection import database_connection

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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?-øë›h‘éì¶»§q«^uá}±•¹Ñ ôÄÀÀ¤4(€€€¥ÍÍÕ•}‘…Ñ”è‘…Ñ”ð9½¹”€ô9½¹”4(€€€‘Õ•}‘…Ñ”è‘…Ñ”ð9½¹”€ô9½¹”4(€€€ÕÉÉ•¹äèÍÑÈð9½¹”€ô¥•±¡‘•™…Õ±Ðõ9½¹”°µ…á}±•¹Ñ ôÌ¤4(€€€Ñ•µÁ±…Ñ•}±…¹Õ…”è%¹Ù½¥•Q•µÁ±…Ñ•1…¹Õ…”ð9½¹”€ô9½¹”4(€€€‰ÕÍ¥¹•ÍÌè%¹Ù½¥•É…™ÑA…ÉÑä€ô¥•±¡‘•™…Õ±Ñ}™…Ñ½Éäõ%¹Ù½¥•É…™ÑA…ÉÑä¤4(€€€±¥•¹Ðè%¹Ù½¥•É…™ÑA…ÉÑä€ô¥•±¡‘•™…Õ±Ñ}™…Ñ½Éäõ%¹Ù½¥•É…™ÑA…ÉÑä¤4(€€€¥Ñ•µÌè±¥ÍÑm%¹Ù½¥•É…™Ñ%Ñ•µt€ô¥•±¡‘•™…Õ±Ñ}™…Ñ½Éäõ±¥ÍÐ°µ…á}±•¹Ñ ôÄÀÀ¤4(€€€É…Ý}¥Ñ•µÌèÍÑÈð9½¹”€ô¥•±¡‘•™…Õ±Ðõ9½¹”°µ…á}±•¹Ñ ôÔÀÀÀ¤4(€€€¹½Ñ•ÌèÍÑÈð9½¹”€ô¥•±¡‘•™…Õ±Ðõ9½¹”°µ…á}±•¹Ñ ôÔÀÀÀ¤4(€€€Á…åµ•¹Ñ}Ñ•ÉµÌèÍÑÈð9½¹”€ô¥•±¡‘•™…Õ±Ðõ9½¹”°µ…á}±•¹Ñ ôÈÀÀÀ¤4(4(€€€™¥•±‘}Ù…±¥‘…Ñ½È ‰¥¹Ù½¥•}¹Õµ‰•Èˆ°€‰É…Ý}¥Ñ•µÌˆ°€‰¹½Ñ•Ìˆ°€‰Á…åµ•¹Ñ}Ñ•ÉµÌˆ°µ½‘”ô‰‰•™½É”ˆ¤4(€€€±…ÍÍµ•Ñ¡½4(€€€‘•˜ÍÑÉ¥Á}½ÁÑ¥½¹…±}Ñ•áÐ¡±Ì°Ù…±Õ”èÍÑÈð9½¹”¤€´øÍÑÈð9½¹”è4(€€€€€€€¥˜Ù…±Õ”¥Ì9½¹”è4(€€€€€€€€€€€É•ÑÕÉ¸9½¹”4(€€€€€€€ÍÑÉ¥ÁÁ•€ôÙ…±Õ”¹ÍÑÉ¥À ¤4(€€€€€€€É•ÑÕÉ¸ÍÑÉ¥ÁÁ•½È9½¹”4(4(€€€™¥•±‘}Ù…±¥‘…Ñ½È ‰ÕÉÉ•¹äˆ°µ½‘”ô‰‰•™½É”ˆ¤4(€€€±…ÍÍµ•Ñ¡½4(€€€‘•˜¹½Éµ…±¥é•}½ÁÑ¥½¹…±}ÕÉÉ•¹ä¡±Ì°Ù…±Õ”èÍÑÈð9½¹”¤€´øÍÑÈð9½¹”è4(€€€€€€€¥˜Ù…±Õ”¥Ì9½¹”è4(€€€€€€€€€€€É•ÑÕÉ¸9½¹”4(€€€€€€€¹½Éµ…±¥é•€ôÙ…±Õ”¹ÍÑÉ¥À ¤¹ÕÁÁ•È ¤4(€€€€€€€¥˜¹½Ð¹½Éµ…±¥é•è4(€€€€€€€€€€€É•ÑÕÉ¸9½¹”4(€€€€€€€¥˜±•¸¡¹½Éµ…±¥é•¤€„ô€Ì½È¹½Ð¹½Éµ…±¥é•¹¥Í…±Á¡„ ¤è4(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‰ÕÉÉ•¹äµÕÍÐ½¹Ñ…¥¸•á…Ñ±äÑ¡É•”±•ÑÑ•ÉÌˆ¤4(€€€€€€€É•ÑÕÉ¸¹½Éµ…±¥é•4(4(€€€µ½‘•±}Ù…±¥‘…Ñ½È¡µ½‘”ô‰…™Ñ•Èˆ¤4(€€€‘•˜Ù…±¥‘…Ñ•}‘Õ•}‘…Ñ”¡Í•±˜¤€´ø€‰%¹Ù½¥•É…™Ðˆè4(€€€€€€€¥˜€ 4(€€€€€€€€€€€Í•±˜¹¥ÍÍÕ•}‘…Ñ”¥Ì¹½Ð9½¹”4(€€€€€€€€€€€…¹Í•±˜¹‘Õ•}‘…Ñ”¥Ì¹½Ð9½¹”4(€€€€€€€€€€€…¹Í•±˜¹‘Õ•}‘…Ñ”€ðÍ•±˜¹¥ÍÍÕ•}‘…Ñ”4(€€€€€€€€¤è4(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‰‘Õ•}‘…Ñ”µÕÍÐ‰”½¸½È…™Ñ•È¥ÍÍÕ•}‘…Ñ”ˆ¤4(€€€€€€€É•ÑÕÉ¸Í•±˜4(4(4)±…ÍÌå¹…µ¥½Éµ¥•±¡	…Í•5½‘•°¤è4(€€€­•äèÍÑÈ€ô¥•±¡µ¥¹}±•¹Ñ ôÄ°µ…á}±•¹Ñ ôÄÈÀ¤4(€€€±…‰•°èÍÑÈ€ô¥•±¡µ¥¹}±•¹Ñ ôÄ°µ…á}±•¹Ñ ôÄÈÀ¤4(€€€ÑåÁ”èå¹…µ¥½Éµ¥•±‘QåÁ”4(€€€É•ÅÕ¥É•è‰½½°€ôQÉÕ”4(€€€Á±…•¡½±‘•ÈèÍÑÈð9½¹”€ô¥•±¡‘•™…Õ±Ðõ9½¹”°µ…á}±•¹Ñ ôÔÀÀ¤4(€€€½ÁÑ¥½¹Ìè±¥ÍÑmÍÑÉtð9½¹”€ô9½¹”4(4(4)±…ÍÌ¥¡…ÑI•ÅÕ•ÍÐ¡¥Q•ÍÑI•ÅÕ•ÍÐ¤è4(€€€Ñ¡¥¹­¥¹}•¹…‰±•è‰½½°€ô…±Í”4(€€€Ñ•µÁ•É…ÑÕÉ•}ÁÉ•Í•Ðè1¥Ñ•É…±l‰±½Üˆ°€‰µ•‘¥Õ´ˆ°€‰¡¥ ˆ°€‰•áÑÉ…}¡¥ ‰t€ô€‰µ•‘¥Õ´ˆ4(4(4)±…ÍÌ¥¡…Ñ¹ÍÝ•ÉI•ÍÁ½¹Í”¡	…Í•5½‘•°¤è4(€€€ÍÑ…ÑÕÌè1¥Ñ•É…±l‰…¹ÍÝ•È‰t4(€€€µ•ÍÍ…”èÍÑÈ4(4(4)±…ÍÌ¥¡…Ñ%¹Ù½¥•1¥ÍÑI•ÍÁ½¹Í”¡	…Í•5½‘•°¤è4(€€€ÍÑ…ÑÕÌè1¥Ñ•É…±l‰¥¹Ù½¥•}±¥ÍÐ‰t4(€€€µ•ÍÍ…”èÍÑÈ4(€€€¥¹Ù½¥•Ìè±¥ÍÑm%¹Ù½¥•1¥ÍÑ%Ñ•µt4(4(4)±…ÍÌ¥¡…Ñ5¥ÍÍ¥¹¥•±‘ÍI•ÍÁ½¹Í”¡	…Í•5½‘•°¤è4(€€€ÍÑ…ÑÕÌè1¥Ñ•É…±l‰µ¥ÍÍ¥¹}™¥•±‘Ì‰t4(€€€µ¥ÍÍ¥¹}™¥•±‘Ìè±¥ÍÑmÍÑÉt4(€€€‘É…™Ðè%¹Ù½¥•É…™Ð4(€€€™¥•±‘Í}Ñ½}Í¡½Üè±¥ÍÑmå¹…µ¥½Éµ¥•±‘t€ô¥•±¡‘•™…Õ±Ñ}™…Ñ½Éäõ±¥ÍÐ¤4(4(4)±…ÍÌ¥¡…ÑÉÉ½ÉI•ÍÁ½¹Í”¡	…Í•5½‘•°¤è4(€€€ÍÑ…ÑÕÌè1¥Ñ•É…±l‰±±µ}Õ¹…Ù…¥±…‰±”ˆ°€‰…¥}Á…ÉÍ•}•ÉÉ½È‰t4(€€€µ•ÍÍ…”èÍÑÈ4(4(4)±…ÍÌ%¹Ù½¥•É…™ÑÉ•…Ñ•‘I•ÍÁ½¹Í”¡	…Í•5½‘•°¤è4(€€€ÍÑ…ÑÕÌè1¥Ñ•É…±l‰É•…Ñ•‰t4(€€€¥¹Ù½¥•}¥è¥¹Ð4(€€€¥¹Ù½¥•}¹Õµ‰•ÈèÍÑÈ4(€€€ÍÕ‰Ñ½Ñ…°è•¥µ…°4(€€€Ñ½Ñ…°è•¥µ…°4(€€€ÕÉÉ•¹äèÍÑÈ4(€€€Á‘™}ÕÉ°èÍÑÈ4(4(€€€™¥•±‘}Í•É¥…±¥é•È ‰ÍÕ‰Ñ½Ñ…°ˆ°€‰Ñ½Ñ…°ˆ°Ý¡•¹}ÕÍ•ô‰©Í½¸ˆ¤4(€€€‘•˜Í•É¥…±¥é•}µ½¹•ä¡Í•±˜°Ù…±Õ”è•¥µ…°¤€´ø™±½…Ðè4(€€€€€€€É•ÑÕÉ¸™±½…Ð¡Ù…±Õ”¤4