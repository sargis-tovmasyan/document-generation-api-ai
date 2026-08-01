from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.chat_store import DEFAULT_USER_ID
from app.services.knowledge_store import list_shared_memories, list_skill_memories
from app.database import database_connection

router = APIRouter(tags=["memories"])


class MemoryStatusUpdate(BaseModel):
    status: Literal["active", "needs_review", "disabled", "rejected"]


@router.get("/shared-memories")
def get_shared_memories(user_id: str = DEFAULT_USER_ID) -> list[dict[str, Any]]:
    return list_shared_memories(user_id=user_id, limit=100)


@router.patch("/shared-memories/{memory_id}")
def patch_shared_memory(memory_id: str, payload: MemoryStatusUpdate) -> dict[str, Any]:
    with database_connection() as connection:
        connection.execute(
            """
            UPDATE shared_memories
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (payload.status, memory_id),
        )
        row = connection.execute(
            "SELECT * FROM shared_memories WHERE id = ?",
            (memory_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Shared memory not found")
    return dict(row)


@router.get("/skill-memories")
def get_skill_memories(user_id: str = DEFAULT_USER_ID) -> list[dict[str, Any]]:
    return list_skill_memories(user_id=user_id, limit=100)


@router.patch("/skill-memories/{skill_id}")
def patch_skill_memory(skill_id: str, payload: MemoryStatusUpdate) -> dict[str, Any]:
    with database_connection() as connection:
        connection.execute(
            """
            UPDATE skill_memories
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (payload.status, skill_id),
        )
        row = connection.execute(
            "SELECT * FROM skill_memories WHERE id = ?",
            (skill_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Skill memory not found")
    return dict(row)
