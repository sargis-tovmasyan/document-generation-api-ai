from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.services.auth_security import CurrentUser, get_current_user
from app.services.chat_schema import ensure_chat_schema
from app.services.chat_store import (
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
    business_profile_id: str | None = Field(default=None, max_length=100)
    client_id: str | None = Field(default=None, max_length=100)
    title: str | None = Field(default=None, max_length=200)


class ChatThreadUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    archived: bool | None = None
    pinned: bool | None = None
    ui_order: int | None = None


class UserSettingsUpdateRequest(BaseModel):
    settings: dict[str, Any] = Field(default_factory=dict)


@router.post("/chat-threads")
def create_thread(
    payload: ChatThreadCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    ensure_chat_schema()
    return create_chat_thread(
        user_id=current_user.id,
        business_profile_id=payload.business_profile_id,
        client_id=payload.client_id,
        title=payload.title,
    )


@router.get("/chat-threads")
def list_threads(
    status: Literal["active", "archived", "deleted"] = "active",
    current_user: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    ensure_chat_schema()
    return list_chat_threads(user_id=current_user.id, status=status)


@router.get("/chat-threads/{chat_id}")
def get_thread(
    chat_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    ensure_chat_schema()
    thread = get_chat_thread(chat_id, user_id=current_user.id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Chat thread not found")
    thread["messages"] = list_chat_messages(chat_id)
    thread["session_memory"] = get_session_state(chat_id)
    return thread


@router.patch("/chat-threads/{chat_id}")
def patch_thread(
    chat_id: str,
    payload: ChatThreadUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    ensure_chat_schema()
    thread = update_chat_thread(
        chat_id,
        user_id=current_user.id,
        title=payload.title,
        archived=payload.archived,
        pinned=payload.pinned,
        ui_order=payload.ui_order,
    )
    if thread is None:
        raise HTTPException(status_code=404, detail="Chat thread not found")
    return thread


@router.delete("/chat-threads/{chat_id}")
def remove_thread(
    chat_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, str]:
    ensure_chat_schema()
    if get_chat_thread(chat_id, user_id=current_user.id) is None:
        raise HTTPException(status_code=404, detail="Chat thread not found")
    soft_delete_chat_thread(chat_id, user_id=current_user.id)
    return {"status": "deleted"}


@router.get("/chat-threads/{chat_id}/messages")
def get_thread_messages(
    chat_id: str,
    limit: int = 100,
    current_user: CurrentUser = Depends(get_current_user),
) -> list[dict[str, Any]]:
    ensure_chat_schema()
    if get_chat_thread(chat_id, user_id=current_user.id) is None:
        raise HTTPException(status_code=404, detail="Chat thread not found")
    return list_chat_messages(chat_id, limit=limit)


@router.get("/chat-threads/{chat_id}/session-memory")
def get_thread_session_memory(
    chat_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    ensure_chat_schema()
    if get_chat_thread(chat_id, user_id=current_user.id) is None:
        raise HTTPException(status_code=404, detail="Chat thread not found")
    return get_session_state(chat_id)


@router.delete("/chat-threads/{chat_id}/session-memory/document-scope")
def clear_thread_document_scope(
    chat_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, str]:
    ensure_chat_schema()
    if get_chat_thread(chat_id, user_id=current_user.id) is None:
        raise HTTPException(status_code=404, detail="Chat thread not found")
    clear_document_scope(chat_id)
    return {"status": "cleared"}


@router.get("/user-ui-settings")
def read_user_settings(current_user: CurrentUser = Depends(get_current_user)) -> dict[str, Any]:
    ensure_chat_schema()
    return get_user_ui_settings(current_user.id)


@router.patch("/user-ui-settings")
def patch_user_settings(
    payload: UserSettingsUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> dict[str, Any]:
    ensure_chat_schema()
    return update_user_ui_settings(current_user.id, payload.settings)
