from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.chat_schema import ensure_chat_schema
from app.services.chat_store import (
    DEFAULT_USER_ID,
    create_chat_thread,
    get_chat_thread,
    get_user_ui_settings,
    list_chat_messages,
    list_chat_threads,
    soft_delete_chat_thread,
    update_chat_thread,
    update_user_ui_settings,
)

router = APIRouter(tags=["chat-threads"])


class ChatThreadCreateRequest(BaseModel):
    user_id: str = Field(default=DEFAULT_USER_ID, min_length=1, max_length=100)
    business_profile_id: str | None = Field(default=None, max_length=100)
    client_id: str | None = Field(default=None, max_length=100)
    title: str | None = Field(default=None, max_length=200)


class ChatThreadUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    archived: bool | None = None
    pinned: bool | None = None
    ui_order: int | None = None


class UserSettingsUpdateRequest(BaseModel):
    user_id: str = Field(default=DEFAULT_USER_ID, min_length=1, max_length=100)
    settings: dict[str, Any] = Field(default_factory=dict)


@router.post("/chat-threads")
def create_thread(payload: ChatThreadCreateRequest) -> dict[str, Any]:
    ensure_chat_schema()
    return create_chat_thread(
        user_id=payload.user_id,
        business_profile_id=payload.business_profile_id,
        client_id=payload.client_id,
        title=payload.title,
    )


@router.get("/chat-threads")
def list_threads(
    user_id: str = DEFAULT_USER_ID,
    status: Literal["active", "archived", "deleted"] = "active",
) -> list[dict[str, Any]]:
    ensure_chat_schema()
    return list_chat_threads(user_id=user_id, status=status)


@router.get("/chat-threads/{chat_id}")
def get_thread(chat_id: str) -> dict[str, Any]:
    ensure_chat_schema()
    thread = get_chat_thread(chat_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Chat thread not found")
    thread["messages"] = list_chat_messages(chat_id)
    return thread


@router.patch("/chat-threads/{chat_id}")
def patch_thread(chat_id: str, payload: ChatThreadUpdateRequest) -> dict[str, Any]:
    ensure_chat_schema()
    thread = update_chat_thread(
        chat_id,
        title=payload.title,
        archived=payload.archived,
        pinned=payload.pinned,
        ui_order=payload.ui_order,
    )
    if thread is None:
        raise HTTPException(status_code=404, detail="Chat thread not found")
    return thread


@router.delete("/chat-threads/{chat_id}")
def remove_thread(chat_id: str) -> dict[str, str]:
    ensure_chat_schema()
    soft_delete_chat_thread(chat_id)
    return {"status": "deleted"}


@router.get("/chat-threads/{chat_id}/messages")
def get_thread_messages(chat_id: str, limit: int = 100) -> list[dict[str, Any]]:
    ensure_chat_schema()
    return list_chat_messages(chat_id, limit=limit)


@router.get("/user-ui-settings")
def read_user_settings(user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
    ensure_chat_schema()
    return get_user_ui_settings(user_id)


@router.patch("/user-ui-settings")
def patch_user_settings(payload: UserSettingsUpdateRequest) -> dict[str, Any]:
    ensure_chat_schema()
    return update_user_ui_settings(payload.user_id, payload.settings)
