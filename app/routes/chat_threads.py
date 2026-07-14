from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.chat_schema import ensure_chat_schema
from app.services.chat_store import (
    DEFAULT_USER_ID,
    append_chat_message,
    clear_document_scope,
    create_chat_thread,
    get_chat_thread,
    get_session_state,
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


class ChatErrorCreateRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    retryable: bool = True
    diagnostics: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None


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
    thread["session_memory"] = get_session_state(chat_id)
    return thread


@router.post("/chat-threads/{chat_id}/errors")
def create_chat_error(chat_id: str, payload: ChatErrorCreateRequest) -> dict[str, Any]:
    ensure_chat_schema()
    if get_chat_thread(chat_id) is None:
        raise HTTPException(status_code=404, detail="Chat thread not found")
    metadata = {
        "status": "error",
        "message": payload.message,
        "retryable": payload.retryable,
    }
    if payload.diagnostics is not None:
        metadata["diagnostics"] = payload.diagnostics
    if payload.raw is not None:
        metadata["raw"] = payload.raw
    return append_chat_message(
        chat_id=chat_id,
        role="assistant",
        content=payload.message,
        metadata=metadata,
    )


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


@router.get("/chat-threads/{chat_id}/session-memory")
def get_thread_session_memory(chat_id: str) -> dict[str, Any]:
    ensure_chat_schema()
    if get_chat_thread(chat_id) is None:
        raise HTTPException(status_code=404, detail="Chat thread not found")
    return get_session_state(chat_id)


@router.delete("/chat-threads/{chat_id}/session-memory/document-scope")
def clear_thread_document_scope(chat_id: str) -> dict[str, str]:
    ensure_chat_schema()
    if get_chat_thread(chat_id) is None:
        raise HTTPException(status_code=404, detail="Chat thread not found")
    clear_document_scope(chat_id)
    return {"status": "cleared"}


@router.get("/user-ui-settings")
def read_user_settings(user_id: str = DEFAULT_USER_ID) -> dict[str, Any]:
    ensure_chat_schema()
    return get_user_ui_settings(user_id)


@router.patch("/user-ui-settings")
def patch_user_settings(payload: UserSettingsUpdateRequest) -> dict[str, Any]:
    ensure_chat_schema()
    return update_user_ui_settings(payload.user_id, payload.settings)
